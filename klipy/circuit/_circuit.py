"""Circuit — the canonical container.

A Circuit holds parts, nets (introduced by reference + optionally annotated),
initial conditions, and analyses.  Validation fires at .add() time so errors
surface immediately at the offending line, not down in conversion.

The Python object itself is the source of truth.  to_dict()/from_dict()
exist for interchange / archival / round-trip testing; .py is what gets
version-controlled.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ._part import Part, part_from_dict
from ._analyses import Analysis, Tran, analysis_from_dict
from ._model import ModelCard


class _SubcktRegistryUnavailable(Exception):
    """Raised when KliCAD's parse_subckt_lib binding can't be reached."""


def _fetch_subckt_registry(kicad, lib_paths: list[str]) -> dict[str, int]:
    """Call klicad_native_sim_advanced.parse_subckt_lib for each lib path.

    Returns {subckt_name: pin_count} merged across all libs (last wins on
    duplicate names, matching ngspice's late-binding semantics).

    Raises _SubcktRegistryUnavailable on any IPC / binding failure — the
    caller turns this into a "check skipped" notice rather than blowing up.
    """
    import ast
    import json

    paths_repr = json.dumps([str(p) for p in lib_paths])
    code = (
        "import klicad_native_sim_advanced as sa\n"
        f"_paths = {paths_repr}\n"
        "_out = {}\n"
        "for _p in _paths:\n"
        "    _r = sa.parse_subckt_lib(_p)\n"
        "    if _r.get('ok'):\n"
        "        for _n, _info in _r['models'].items():\n"
        "            _out[_n] = int(_info['pin_count'])\n"
        "_out\n"
    )
    try:
        r = kicad.run_python(code)
    except Exception as e:
        raise _SubcktRegistryUnavailable(
            f"KliCAD IPC unavailable ({e!r})"
        ) from e
    if not r.ok:
        raise _SubcktRegistryUnavailable(
            f"klicad_native_sim_advanced.parse_subckt_lib failed: "
            f"{r.exception_traceback or r.stderr or r.stdout}"
        )
    try:
        return ast.literal_eval(r.result_repr or "{}")
    except (ValueError, SyntaxError) as e:
        raise _SubcktRegistryUnavailable(
            f"could not parse subckt registry result ({e!r}): {r.result_repr!r}"
        ) from e


# Net kind: 'signal' is the default; 'power' / 'ground' are auto-detected by
# common name conventions and trigger power-symbol placement in to_klicad_sch.
NetKind = Literal["signal", "power", "ground"]


# Names that auto-detect as power/ground.  Override by calling c.net(name, kind=...)
# explicitly.  Case-insensitive comparison.
_POWER_NAMES  = {"vcc", "vdd", "v+", "vbat", "vbus", "+5v", "+3v3", "+3.3v", "+12v"}
_GROUND_NAMES = {"gnd", "vss", "v-", "agnd", "dgnd", "earth"}


@dataclass
class NetMeta:
    name: str
    kind: NetKind = "signal"
    # Suppresses the "referenced by only N part(s)" typo warning for this
    # net.  Use on nets that are deliberately driven from outside the
    # Circuit being built — sub-sheet boundary IOs, stimulus injection
    # points, placeholders for parts present only in a sibling Circuit.
    expect_external: bool = False

    def to_dict(self) -> dict:
        d = {"name": self.name, "kind": self.kind}
        if self.expect_external:
            d["expect_external"] = True
        return d


def _auto_net_kind(name: str) -> NetKind:
    lo = name.lower()
    if lo in _POWER_NAMES or lo.startswith("+") or lo.startswith("vcc") or lo.startswith("vdd"):
        return "power"
    if lo in _GROUND_NAMES:
        return "ground"
    return "signal"


@dataclass
class Circuit:
    """Top-level circuit description.

    Build with .add(Part(...)) / .net(name, kind=...) / .ic(NL=5, ...).
    Add analyses by appending to .analyses or via .analysis(...).

    Validation:
      - Each .add() checks ref uniqueness + that all the part's pins are
        connected to non-empty net names.
      - Auto-registers any new nets the part references, with auto-detected
        kind (power/ground/signal).
      - Net used only once → warn at .add() time (typo guard).
      - to_spice_deck() runs a final pre-emit pass (orphan nets, missing
        models, etc.).
    """

    name: str = ""
    desc: str = ""
    parts: list[Part] = field(default_factory=list)
    nets:  dict[str, NetMeta] = field(default_factory=dict)
    initial_conditions: dict[str, float] = field(default_factory=dict)
    analyses: list[Analysis] = field(default_factory=list)
    # Search path for SPICE model libraries (.lib files).  Earlier entries win.
    model_lib_paths: list[str] = field(default_factory=list)
    # Inline SPICE `.model` cards, emitted ahead of the element lines.
    models: list[ModelCard] = field(default_factory=list)
    # Port list — passing ports=[...] at construction makes this Circuit
    # instance-able as a Sub-Circuit (KiCad sub-sheet ↔ SPICE .SUBCKT).
    # See klipy/circuit/_bus.py for bus syntax.  None means root Circuit.
    ports: list[str] | None = None
    # Strictness knobs (mostly for testing or generated code).
    strict: bool = True            # warnings become errors when True
    _warnings: list[str] = field(default_factory=list, init=False)
    # Populated by __post_init__ when ports is non-None.
    _port_decl:       list[str] = field(default_factory=list, init=False)
    _ports_expanded:  list[str] = field(default_factory=list, init=False)
    # Source reference for the spec pane.  See klipy.circuit._srcref.
    # Captured at construction; used as a fallback Klicad.SpecSrc for
    # items synthesized by to_schematic (power symbols, port stubs,
    # autorouted labels) that have no direct user line of their own.
    _src: "tuple[str, int] | None" = field(default=None, init=False,
                                           repr=False, compare=False)

    def __post_init__(self) -> None:
        from ._srcref import capture_user_frame
        self._src = capture_user_frame()
        if self.ports is not None:
            from ._bus import expand_port_decl
            # validate + expand; validate_port_decl runs inside expand
            self._port_decl      = list(self.ports)
            self._ports_expanded = expand_port_decl(self._port_decl)

    # ---- Sub-Circuit instance-ability ----

    @property
    def is_subcircuit(self) -> bool:
        """True if this Circuit was constructed with ports= (it's a definition).

        Root Circuits are emitted as the top-level deck + schematic.
        Sub-Circuit definitions can be instantiated in a parent via
        .instance(ref, **port_map); they're emitted as .SUBCKT blocks
        and (in H2) as child .kicad_sch files.
        """
        return self.ports is not None

    def instance(self, ref: str, *, repeat: int = 1, **port_map: str) -> "Part":
        """Return a SubcircuitInstance binding this Sub-Circuit's ports to
        external nets in a parent Circuit.  Add via `parent.add(...)`:

            amp = Circuit("amp", ports=["IN", "OUT", "VCC", "GND"])
            amp.add(...)

            top = Circuit("top")
            top.add(amp.instance("U_amp1",
                                 IN="AUDIO_IN", OUT="AUDIO_OUT",
                                 VCC="+12V", GND="GND"))

        port_map keys may be scalar port names, base names of bus
        ports, or full bus-range forms.  See _bus.expand_port_map.

        repeat: multi-channel marker (R5.3).  ``repeat=1`` (default) emits
            a single instance, identical to pre-R5.3 behaviour.  ``repeat=N``
            (N > 1) emits ONE SubcircuitInstance with ``repeat_count=N``,
            representing N parallel slots of the same Sub-Circuit; downstream
            schematic/SPICE expansion (R5.4, R5.5) consume the marker.
            Every bus port on the definition must span exactly ``N`` members
            (so bit ``i`` of the bus binds to slot ``i``); scalar ports are
            shared across all slots.  Mismatch → ValueError (per R5.1's
            ``validate_port_widths_for_repeat``).
        """
        if not self.is_subcircuit:
            raise ValueError(
                f"Circuit({self.name!r}).instance(): this Circuit was not "
                f"declared with ports=; only Sub-Circuit definitions can "
                f"be instantiated.  To make it a Sub-Circuit, construct as "
                f"Circuit({self.name!r}, ports=[...])."
            )
        # When repeat > 1, validate the definition's port widths against
        # the requested repeat count BEFORE constructing the instance so
        # the error surfaces at the offending .instance() call.  repeat=1
        # is the trivial case and skips validation to keep error messages
        # for malformed port_decls anchored at Circuit() construction.
        if repeat != 1:
            from ._bus import validate_port_widths_for_repeat
            validate_port_widths_for_repeat(self._port_decl, repeat)
        from ._part import SubcircuitInstance
        return SubcircuitInstance(
            ref, self, port_map=port_map, repeat_count=repeat,
        )

    # ---- net management ----

    def net(self, name: str, kind: NetKind = "signal", *,
            expect_external: bool = False) -> "Circuit":
        """Explicitly declare a net (optionally with kind).  Idempotent if kind matches.

        expect_external: marks the net as deliberately driven from outside
            this Circuit (sub-sheet boundary IO, externally-injected stimulus,
            etc.).  Suppresses the "referenced by only N part(s)" typo
            warning in validate_all().
        """
        if not name:
            raise ValueError("net name is required")
        existing = self.nets.get(name)
        if existing and existing.kind != kind:
            raise ValueError(
                f"net {name!r} already declared as {existing.kind!r}; "
                f"can't redeclare as {kind!r}"
            )
        # Preserve a previously-set expect_external if caller didn't override.
        external = expect_external or (existing.expect_external if existing else False)
        self.nets[name] = NetMeta(name=name, kind=kind, expect_external=external)
        return self

    def _register_net_from_part(self, name: str) -> None:
        """Auto-register a net referenced by a part if it hasn't been declared."""
        if not name:
            raise ValueError("part has empty net name on one of its pins")
        if name not in self.nets:
            self.nets[name] = NetMeta(name=name, kind=_auto_net_kind(name))

    # ---- adding parts ----

    def add(self, part: Part) -> "Circuit":
        """Add a part.  Validates uniqueness + connections + registers nets."""
        # Ref uniqueness
        existing = next((p for p in self.parts if p.ref == part.ref), None)
        if existing:
            raise ValueError(
                f"duplicate ref {part.ref!r} (already used by {type(existing).__name__})"
            )
        # Part-level validation (pin coverage)
        part.validate()
        # Auto-register any nets the part references
        for net_name in part.nets_used():
            self._register_net_from_part(net_name)
        self.parts.append(part)
        return self

    def add_many(self, *parts: Part) -> "Circuit":
        for p in parts:
            self.add(p)
        return self

    # ---- initial conditions ----

    def ic(self, **kwargs: float) -> "Circuit":
        """Set initial node voltages.  c.ic(NL=5, NR=0)"""
        for net_name, v in kwargs.items():
            if net_name not in self.nets:
                # Note: SPICE accepts .ic on nets even if not yet declared
                # (the .ic is a hint to the solver), but our integrity check
                # is the friendlier behaviour.
                raise ValueError(
                    f"ic({net_name}={v}): no such net.  Add a part that "
                    f"references it first, or declare with c.net({net_name!r})."
                )
            self.initial_conditions[net_name] = float(v)
        return self

    # ---- analyses ----

    def analysis(self, a: Analysis) -> "Circuit":
        """Append an analysis."""
        self.analyses.append(a)
        return self

    # ---- ergonomic .tran shortcut ----
    #
    # Several downstream projects assign `c.tran = Tran(...)` expecting it
    # to register the analysis.  Make that work — and keep self.analyses as
    # the single source of truth: setting `c.tran` replaces any existing
    # Tran in self.analyses; reading returns the first Tran or None.

    @property
    def tran(self) -> Tran | None:
        """The first Tran analysis registered on this Circuit, or None."""
        for a in self.analyses:
            if isinstance(a, Tran):
                return a
        return None

    @tran.setter
    def tran(self, t: Tran | None) -> None:
        if t is not None and not isinstance(t, Tran):
            raise TypeError(f"c.tran = ...: expected Tran, got {type(t).__name__}")
        if t is not None and not t.step:
            # Tran inherits `name` from Analysis as its first field, so
            # `Tran('1u', '10m')` positionally writes to name+step, leaving
            # stop empty.  Catch that here with a helpful message rather
            # than letting the broken Tran sit on the Circuit until deck
            # emission.
            raise ValueError(
                "c.tran = Tran(...): the assigned Tran has empty .step.  "
                "Tran inherits a `name` field, so positional args set "
                "name+step.  Use keyword args: Tran(step='1u', stop='10m')."
            )
        # Strip any existing Tran(s), append the new one (or none).
        self.analyses = [a for a in self.analyses if not isinstance(a, Tran)]
        if t is not None:
            self.analyses.append(t)

    # ---- model lib search paths ----

    def add_model_lib(self, path: str | Path) -> "Circuit":
        """Add a SPICE .lib file to the search path.  Earlier entries win.

        The deck emitter `.include`s every configured lib.  Use this for
        files holding `.model` cards or `.SUBCKT` definitions (vendor
        TVS / Zener / op-amp models).  Instantiate a `.SUBCKT` with an
        XSubckt part.
        """
        self.model_lib_paths.append(str(path))
        return self

    def add_model(self, name: str, kind: str, **params: object) -> "Circuit":
        """Define an inline SPICE `.model` card.

        name:   model name; parts reference it via model=.
        kind:   SPICE device type — D, NPN, PNP, NJF, NMOS, RES, ...
        params: device parameters as keyword args, stringified on emit:
                c.add_model("DZ15", "D", BV=15, RS=2, IBV="5m", CJO="200p")

        Emitted by to_spice_deck() as a `.model` line between the
        `.include` directives and the element lines.  For `.SUBCKT`-based
        vendor models, use add_model_lib() + an XSubckt part instead.
        """
        if not name:
            raise ValueError("add_model: model name is required")
        if not kind:
            raise ValueError(f"add_model({name!r}): device kind is required")
        if any(m.name == name for m in self.models):
            raise ValueError(f"add_model: model {name!r} already defined")
        self.models.append(ModelCard(
            name=name,
            kind=kind,
            params={k: str(v) for k, v in params.items()},
        ))
        return self

    # ---- final pre-emit validation ----

    def validate_all(self, *, kicad=None) -> list[str]:
        """Run integrity checks for orphan nets, missing models, etc.

        Returns a list of warning strings; raises if strict=True and any
        ERROR-level issue is found (orphans are warnings, refs to undeclared
        models are errors).
        """
        issues: list[str] = []
        errors: list[str] = []

        # Orphan nets (used only by 1 part — likely typo).  Skip nets
        # explicitly marked expect_external (sub-sheet boundary IOs, etc.).
        ref_count: dict[str, int] = {}
        for p in self.parts:
            for n in p.nets_used():
                ref_count[n] = ref_count.get(n, 0) + 1
        for net_name, count in ref_count.items():
            if count >= 2:
                continue
            meta = self.nets.get(net_name)
            if meta and meta.expect_external:
                continue
            issues.append(
                f"net {net_name!r} is referenced by only {count} part(s); "
                f"probable typo or missing connection"
            )

        # Every IC net must be in the net set (already enforced by ic(); recheck)
        for net_name in self.initial_conditions:
            if net_name not in self.nets:
                errors.append(f"ic() references undeclared net {net_name!r}")

        # Models referenced but library not configured (we don't read .lib
        # files here; the SPICE deck just .includes them and lets ngspice
        # complain at simulation time if missing).  Just check the path
        # exists if any model lib paths were configured.
        lib_paths = list(self.model_lib_paths)
        lib_paths += [p.library for p in self.parts if p.library]
        for path in lib_paths:
            if not Path(path).exists():
                issues.append(f"model_lib path {path!r} does not exist on disk")

        # Duplicate inline model names would make ngspice pick one arbitrarily.
        seen_models: set[str] = set()
        for m in self.models:
            if m.name in seen_models:
                errors.append(f"duplicate inline .model name {m.name!r}")
            seen_models.add(m.name)

        # .SUBCKT name + arity check for XSubckt instances.  Delegates to
        # KliCAD's C++ SPICE_LIBRARY_PARSER + SIM_LIBRARY_SPICE via the
        # klicad_native_sim_advanced.parse_subckt_lib binding — the
        # authoritative source.  Skipped when no KliCAD client is supplied
        # (the only Python-side validation that requires KliCAD).
        x_parts = [p for p in self.parts if getattr(p, "kind", "") == "X"]
        if x_parts and kicad is not None and lib_paths:
            try:
                registry = _fetch_subckt_registry(kicad, lib_paths)
            except _SubcktRegistryUnavailable as e:
                issues.append(
                    f"XSubckt arity check skipped — {e}.  Mismatches will "
                    f"surface at ngspice runtime instead."
                )
                registry = None
            if registry is not None:
                for x in x_parts:
                    name = getattr(x, "subckt", "")
                    if not name:
                        continue
                    if name not in registry:
                        issues.append(
                            f"XSubckt {x.ref!r} references subckt {name!r}, but "
                            f"no .SUBCKT {name} was found in any configured "
                            f"model_lib (per KliCAD's SPICE_LIBRARY_PARSER)"
                        )
                        continue
                    expected = registry[name]
                    actual = len(x.pin_names)
                    if expected != actual:
                        errors.append(
                            f"XSubckt {x.ref!r}: .SUBCKT {name} has {expected} "
                            f"terminal(s) per KliCAD, but {actual} node(s) were "
                            f"provided"
                        )
        elif x_parts and kicad is None:
            # Note (not warn): the user simply didn't pass kicad=.
            # Don't pollute warnings list — emitting a SPICE deck
            # without a kicad client is a normal use case.
            pass

        self._warnings = issues
        if errors:
            raise ValueError("circuit validation failed:\n  - " + "\n  - ".join(errors))
        return issues

    # ---- serialization ----

    def to_dict(self) -> dict:
        return {
            "schema_version": "1.0",
            "name": self.name,
            "desc": self.desc,
            "parts": [p.to_dict() for p in self.parts],
            "nets":  {n.name: n.to_dict() for n in self.nets.values()},
            "initial_conditions": dict(self.initial_conditions),
            "analyses": [a.to_dict() for a in self.analyses],
            "model_lib_paths": list(self.model_lib_paths),
            "models": [m.to_dict() for m in self.models],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Circuit":
        version = d.get("schema_version", "1.0")
        if version != "1.0":
            raise ValueError(f"unknown schema_version {version!r}; this code understands '1.0'")

        c = cls(
            name=d.get("name", ""),
            desc=d.get("desc", ""),
        )
        c.model_lib_paths = list(d.get("model_lib_paths", []))
        for md in d.get("models", []):
            c.models.append(ModelCard.from_dict(md))
        # Nets first so add() doesn't trample explicit declarations
        for name, meta in d.get("nets", {}).items():
            c.net(
                name,
                meta.get("kind", "signal"),
                expect_external=bool(meta.get("expect_external", False)),
            )
        for pd in d.get("parts", []):
            c.parts.append(part_from_dict(pd))
        for net_name, v in d.get("initial_conditions", {}).items():
            c.initial_conditions[net_name] = float(v)
        for ad in d.get("analyses", []):
            c.analyses.append(analysis_from_dict(ad))
        return c

    # ---- structural equality (round-trip test) ----

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Circuit):
            return NotImplemented
        return self.to_dict() == other.to_dict()

    def __hash__(self):  # pragma: no cover — Circuits are mutable
        return id(self)

    # ---- convenience ----

    def __repr__(self) -> str:
        return (
            f"Circuit({self.name!r}, parts={len(self.parts)}, "
            f"nets={len(self.nets)}, analyses={len(self.analyses)})"
        )

    # ---- conversion stubs (delegated to other modules) ----

    def to_spice_deck(self, *, self_running: bool = True, kicad=None) -> str:
        from ._spice import to_spice_deck
        return to_spice_deck(self, self_running=self_running, kicad=kicad)

    def run_tran(self, step: str | None = None, stop: str | None = None,
                 *, uic: bool | None = None, ng=None) -> dict[str, list[float]]:
        """Run a transient simulation and return the result vectors.

        Wraps the PySpice / libngspice footguns (singleton lifecycle,
        spurious-stderr exception, plot-name discovery, vector extraction).
        See `_sim.py` for full docs and the precise error semantics.

        Returns a dict mapping vector name → list[float].  Always includes
        the time axis (typically under key "time").

        Requires PySpice + ngspice on the system; raises ImportError if
        PySpice is missing.
        """
        from ._sim import run_tran
        return run_tran(self, step=step, stop=stop, uic=uic, ng=ng)

    def to_schematic(self, path: str | Path, *, kicad=None,
                     layout: str = "sugiyama",
                     route: bool = False,
                     mode: str = "diff") -> dict:
        """Author this Circuit into a live KliCAD schematic.

        path:    .kicad_sch file path.  Sibling .kicad_pro / sym-lib-table /
                 models.lib get auto-created if absent.
        kicad:   optional klipy.KliCAD instance (a new one is created if None).
        layout:  placement engine.  "sugiyama" (default) lays parts out in
                 columns by signal-flow depth.  "clustered" reuses Sugiyama
                 but with partition() block-id as a secondary ordering key
                 so same-block parts end up adjacent.  "spring" runs
                 force-directed Fruchterman-Reingold (via networkx) with
                 phantom intra-block springs — best for circuits with
                 multiple weakly-connected functional sub-blocks.
        route:   if True, draw explicit A* wires between same-net pins
                 (opt-in; default is label-based connectivity).
        mode:    "diff" (default) keeps existing parts in place and only
                 emits new/changed ones — best for round-trip workflows
                 where the user has hand-tuned positions.  "wipe" deletes
                 every existing symbol/sheet before emit and re-runs the
                 layout engine for the whole circuit — best for testing
                 a layout-engine change end-to-end (the diff path skips
                 already-placed parts so layout edits never propagate).

        Returns {ok, parts_placed, labels_placed, wires_placed, sch_path,
                 models_lib_path, project_path}.
        """
        from ._klicad_sch import to_schematic
        return to_schematic(self, path, kicad=kicad, layout=layout,
                            route=route, mode=mode)
        """Generate a .kicad_sch file via the live KliCAD bindings.

        Requires a running KliCAD instance (creates / uses one via klipy.klicad.KliCAD).
        Returns a small status dict; the .kicad_sch is written to disk.
        """
        from ._klicad_sch import to_klicad_sch
        return to_klicad_sch(self, path, kicad=kicad, route=route)

    def to_netlist(self, schematic_dir: str | None = None) -> str:
        """Emit this circuit's KiCad netlist.

        Delegates to the existing kicad-cli wrapper in
        klipy.circuit._netlist.  For multi-channel SubcircuitInstances
        (repeat_count > 1), KliCAD's C++ netlist exporter handles the
        fan-out automatically: BuildSheetList materializes N
        SCH_SHEET_PATHs whose trailing SCH_SHEET_INSTANCEs carry
        distinct slot_kiids drawn from the template's
        m_repeatInstances, and the exporter walks each path producing
        one component instance per slot.
        """
        from ._netlist import to_netlist as _to_netlist
        return _to_netlist(self, schematic_dir=schematic_dir)

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


# Net kind: 'signal' is the default; 'power' / 'ground' are auto-detected by
# common name conventions and trigger power-symbol placement in to_kicad_sch.
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
    # Strictness knobs (mostly for testing or generated code).
    strict: bool = True            # warnings become errors when True
    _warnings: list[str] = field(default_factory=list, init=False)

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

    def validate_all(self) -> list[str]:
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

        # .SUBCKT registry — parse the configured .lib files and verify each
        # XSubckt's subckt= name + node count matches a real .SUBCKT
        # definition.  Catches the most common late-binding errors
        # ("subcircuit not found", "too few terminals") at validate-time
        # instead of at ngspice runtime.
        x_parts = [p for p in self.parts if getattr(p, "kind", "") == "X"]
        if x_parts:
            from ._subckt_lib import parse_subckt_lib
            registry: dict[str, int] = {}
            for path in lib_paths:
                # parse_subckt_lib() returns {} for missing files (already
                # warned above) so we can call it unconditionally.
                registry.update(parse_subckt_lib(path))
            for x in x_parts:
                name = getattr(x, "subckt", "")
                if not name:
                    continue
                if name not in registry:
                    # Warn (not error) — the subckt might come from a
                    # not-yet-parseable lib, a verilog-A model, or some
                    # other source we don't read.
                    issues.append(
                        f"XSubckt {x.ref!r} references subckt {name!r}, but "
                        f"no .SUBCKT {name} was found in any configured "
                        f"model_lib"
                    )
                    continue
                expected = registry[name]
                actual = len(x.pin_names)
                if expected != actual:
                    errors.append(
                        f"XSubckt {x.ref!r}: .SUBCKT {name} has {expected} "
                        f"terminal(s) but {actual} node(s) were provided"
                    )

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

    def to_spice_deck(self, *, self_running: bool = True) -> str:
        from ._spice import to_spice_deck
        return to_spice_deck(self, self_running=self_running)

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
                     route: bool = False) -> dict:
        """Author this Circuit into a live KiCad schematic.

        path:    .kicad_sch file path.  Sibling .kicad_pro / sym-lib-table /
                 models.lib get auto-created if absent.
        kicad:   optional kipy.KiCad instance (a new one is created if None).
        layout:  placement engine.  "sugiyama" (default) lays parts out in
                 columns by signal-flow depth.  "clustered" reuses Sugiyama
                 but with partition() block-id as a secondary ordering key
                 so same-block parts end up adjacent.  "spring" runs
                 force-directed Fruchterman-Reingold (via networkx) with
                 phantom intra-block springs — best for circuits with
                 multiple weakly-connected functional sub-blocks.
        route:   if True, draw explicit A* wires between same-net pins
                 (opt-in; default is label-based connectivity).

        Returns {ok, parts_placed, labels_placed, wires_placed, sch_path,
                 models_lib_path, project_path}.
        """
        from ._kicad_sch import to_schematic
        return to_schematic(self, path, kicad=kicad, layout=layout, route=route)
        """Generate a .kicad_sch file via the live KliCAD bindings.

        Requires a running KliCAD instance (creates / uses one via kipy.klicad.KliCAD).
        Returns a small status dict; the .kicad_sch is written to disk.
        """
        from ._kicad_sch import to_kicad_sch
        return to_kicad_sch(self, path, kicad=kicad, route=route)

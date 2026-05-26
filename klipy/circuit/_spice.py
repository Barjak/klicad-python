"""Render a Circuit to a SPICE netlist string suitable for ngspice.

Layout of the emitted deck:

    * <circuit.name>                        title line (ngspice convention)
    .include <model_lib_path>               one per configured lib + part lib
    .model <name> <kind> (params)           one per inline ModelCard
    <element lines: R/C/L/D/Q/V/I/X>        one per Part
    .ic V(net1)=v1 V(net2)=v2 ...           if any initial conditions
    .control                                wraps the runnable analyses
        tran 1us 200ms uic
        ...
    .endc
    .end

Decisions baked in:
  - Analyses run inside .control because the `.tran` directive form is
    parse-time-only in ngspice; `tran` inside .control is the run-time form
    that actually produces a plot.  This matches what KliCAD's GUI does.
  - Initial conditions are emitted as a single .ic line.
  - The 0 (ground) node is whatever any Part says it is — by convention
    'GND' or '0'.  We rewrite 'GND' -> '0' on emit so SPICE recognizes it
    as the ground reference.  All other net names pass through verbatim.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._circuit import Circuit


# Net names that SPICE knows as ground (node 0).  Substitution happens at
# emit time so the rest of the pipeline stays uniformly named.
_GROUND_ALIASES = {"GND", "VSS", "AGND", "DGND", "EARTH", "0"}


def _to_spice_net(name: str) -> str:
    """Map a friendly net name to its SPICE form.  Ground -> '0'."""
    if name.upper() in _GROUND_ALIASES:
        return "0"
    return name


def _rewrite_grounds_in_part(p) -> str:
    """Render a part's spice_line() with any ground-net references rewritten to 0.

    We can't ask the Part to do this because it doesn't know about ground
    aliasing — that's a deck-level convention.  Cheapest path: render then
    post-process the net tokens.
    """
    line = p.spice_line()
    # We need to substitute net names, which appear after the ref-designator
    # and before the trailing value/model.  The Part class guarantees the
    # form is "<letter><ref> <net1> <net2> [...] <value-or-model>".
    head, *rest = line.split()
    if not rest:
        return line
    # Find which middle tokens are net refs by consulting the part's
    # connections.  Order is determined by pin_names.
    net_count = len(p.pin_names)
    nets = rest[:net_count]
    tail = rest[net_count:]
    rewritten = [_to_spice_net(n) for n in nets]
    return " ".join([head, *rewritten, *tail])


def _reachable_subcircuit_defs(c: "Circuit") -> list["Circuit"]:
    """Walk c's parts (and transitively any Sub-Circuit bodies) and return
    each distinct definition Circuit, once, in first-seen order.

    Keyed by `id()` — the same definition object instantiated N times in
    a parent collapses to one `.SUBCKT` block.  Different Circuit
    objects sharing a name are distinct (SPICE may complain at
    netlist-load time; that's a name-collision check, not our job).
    """
    seen: dict[int, "Circuit"] = {}
    order: list["Circuit"] = []

    def walk(circuit: "Circuit") -> None:
        for p in circuit.parts:
            if getattr(p, "kind", "") == "SUBCIRCUIT":
                defn = p.definition  # type: ignore[attr-defined]
                if id(defn) not in seen:
                    seen[id(defn)] = defn
                    order.append(defn)
                    walk(defn)   # transitive — handles nested Sub-Circuits

    walk(c)
    return order


def _validate_spice_names(c: "Circuit") -> None:
    """Boundary check: every ref/net/model name emitted is SPICE-safe.

    Called once at the top of to_spice_deck, before any string assembly.
    Raises with a specific message on the first violation.
    """
    from ._bus import validate_spice_name

    def check_circuit(circuit: "Circuit") -> None:
        for p in circuit.parts:
            validate_spice_name(p.ref, kind="ref")
            if getattr(p, "model", ""):
                validate_spice_name(p.model, kind="model")
            for net in p.connections.values():
                validate_spice_name(net, kind="net")

    check_circuit(c)
    for defn in _reachable_subcircuit_defs(c):
        check_circuit(defn)


def _emit_subckt_block(defn: "Circuit") -> list[str]:
    """Emit `.SUBCKT name port1 port2 ... portN` + body + `.ENDS name`.

    Body Parts go through the same ground-rewrite as the root; ports are
    the expanded scalar list in declaration order (`.SUBCKT` signature
    order).  No .include / .control / .ic inside a .SUBCKT — those are
    deck-level.  Inline `.model` cards from the body ARE emitted inside
    the .SUBCKT (they're local to it in SPICE).
    """
    if not defn.is_subcircuit:
        raise ValueError(
            f"_emit_subckt_block: {defn.name!r} is not a Sub-Circuit "
            f"(no ports= declared)"
        )
    lines: list[str] = []
    ports = " ".join(defn._ports_expanded)
    lines.append(f".SUBCKT {defn.name} {ports}")
    # Inline .model cards (scoped to this .SUBCKT)
    for m in defn.models:
        lines.append("  " + m.spice_line())
    # Body element lines
    for p in defn.parts:
        lines.append("  " + _rewrite_grounds_in_part(p))
    lines.append(f".ENDS {defn.name}")
    return lines


def _collect_implicit_globals(c: "Circuit") -> list[str]:
    """Collect non-ground power-net names used anywhere in the hierarchy.

    Emitted as a single `.global` directive so power nets flow across
    .SUBCKT boundaries (matching KiCad's power-symbol semantics).
    `GND` / `0` is already SPICE-global; not included here.
    """
    from ._bus import is_power_name

    names: dict[str, None] = {}   # ordered set

    def collect(circuit: "Circuit") -> None:
        for p in circuit.parts:
            for net in p.connections.values():
                if not is_power_name(net):
                    continue
                # Ground aliases are already global; skip.
                if net.upper() in _GROUND_ALIASES:
                    continue
                names[net] = None

    collect(c)
    for defn in _reachable_subcircuit_defs(c):
        collect(defn)
    return list(names)


def to_spice_deck(c: "Circuit", *, self_running: bool = True, kicad=None) -> str:
    """Render Circuit `c` as a SPICE deck string.

    Calls c.validate_all() first; warnings go to c._warnings.  Raises on
    integrity errors (referenced undeclared models in ic(), etc.).

    self_running: when True (default), wrap analyses in a `.control / .endc`
        block so the deck auto-runs on `source()`.  When False, omit the
        `.control` block entirely — useful when a runner drives `tran`
        explicitly via `exec_command()` (sourcing a self-running deck and
        then running `tran` again causes a double-tran error).
        `Circuit.run_tran()` uses self_running=False.

    Refuses on Sub-Circuit definitions — pass the root Circuit; Sub-
    Circuits are emitted as `.SUBCKT` blocks ahead of the root's
    element lines.
    """
    if c.is_subcircuit:
        raise ValueError(
            f"to_spice_deck: {c.name!r} is a Sub-Circuit definition "
            f"(ports={c._port_decl}).  Pass the root Circuit; Sub-Circuits "
            f"are emitted as .SUBCKT blocks automatically."
        )
    c.validate_all(kicad=kicad)
    _validate_spice_names(c)

    lines: list[str] = []

    # Title line — ngspice ignores it, but a missing first line is treated
    # as the title and silently swallowed.
    title = c.name or "circuit"
    lines.append(f"* {title}")
    if c.desc:
        lines.append(f"* {c.desc}")

    # Model library includes — circuit-level libs first, then any per-part
    # .library files, deduped while preserving first-seen order.  Walk
    # the hierarchy so Sub-Circuit bodies' libs are visible too.
    includes: list[str] = list(c.model_lib_paths)
    def collect_libs(circuit: "Circuit") -> None:
        for path in circuit.model_lib_paths:
            if path not in includes:
                includes.append(path)
        for p in circuit.parts:
            if getattr(p, "library", "") and p.library not in includes:
                includes.append(p.library)
    collect_libs(c)
    for defn in _reachable_subcircuit_defs(c):
        collect_libs(defn)
    for path in includes:
        lines.append(f".include {path}")

    # Implicit-power globals — make VCC / +12V / etc. flow across .SUBCKT
    # boundaries.  GND is already SPICE-global; omitted.
    globals_ = _collect_implicit_globals(c)
    if globals_:
        lines.append(f".global {' '.join(globals_)}")

    # Root-level inline .model cards (Sub-Circuit bodies emit their own
    # scoped models inside their .SUBCKT blocks via _emit_subckt_block).
    for m in c.models:
        lines.append(m.spice_line())

    # .SUBCKT blocks for every reachable Sub-Circuit definition.
    for defn in _reachable_subcircuit_defs(c):
        lines.append("")
        lines.extend(_emit_subckt_block(defn))

    # Root element lines, in insertion order so the deck is human-diffable.
    if c.parts:
        lines.append("")
        for p in c.parts:
            lines.append(_rewrite_grounds_in_part(p))

    # Initial conditions
    if c.initial_conditions:
        lines.append("")
        ic_parts = " ".join(
            f"V({_to_spice_net(name)})={val}"
            for name, val in c.initial_conditions.items()
        )
        lines.append(f".ic {ic_parts}")

    # Analyses — wrapped in .control so they actually execute.  Caller can
    # opt out via self_running=False when an external runner will drive
    # the analyses itself.
    if self_running:
        runnable = [a for a in c.analyses if hasattr(a, "spice_lines")]
        if runnable:
            lines.append("")
            lines.append(".control")
            for a in runnable:
                for stmt in a.spice_lines():
                    lines.append(stmt)
            lines.append(".endc")

    lines.append(".end")
    lines.append("")  # trailing newline
    return "\n".join(lines)

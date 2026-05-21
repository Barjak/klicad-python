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
    that actually produces a plot.  This matches what KiCad's GUI does.
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


def to_spice_deck(c: "Circuit") -> str:
    """Render Circuit `c` as a SPICE deck string.

    Calls c.validate_all() first; warnings go to c._warnings.  Raises on
    integrity errors (referenced undeclared models in ic(), etc.).
    """
    c.validate_all()

    lines: list[str] = []

    # Title line — ngspice ignores it, but a missing first line is treated
    # as the title and silently swallowed.
    title = c.name or "circuit"
    lines.append(f"* {title}")
    if c.desc:
        lines.append(f"* {c.desc}")

    # Model library includes — circuit-level libs first, then any per-part
    # .library files, deduped while preserving first-seen order.
    includes: list[str] = list(c.model_lib_paths)
    for p in c.parts:
        if p.library and p.library not in includes:
            includes.append(p.library)
    for path in includes:
        lines.append(f".include {path}")

    # Inline .model cards
    for m in c.models:
        lines.append(m.spice_line())

    # Element lines, in insertion order so the deck is human-diffable
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

    # Analyses — wrapped in .control so they actually execute
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

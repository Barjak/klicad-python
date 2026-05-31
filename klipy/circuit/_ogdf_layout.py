"""Klicad-python entry point for the OGDF-fork auto-layout (Option C).

After a Circuit has been emitted to disk via the normal
`to_schematic`, this module re-runs the layout pass using the
port-aware OGDF fork wired into KliCAD's eeschema kiface (binding
`klicad_native_auto_layout`).  The result overwrites the .kicad_sch
on disk in-place.

Usage::

    c = Circuit(...)
    c.add( R("R1", "VCC", "GND", value="10k") )
    sch_path = c.to_schematic("/tmp/r.kicad_sch")
    report = relayout_via_ogdf( c, sch_path )
    # sch_path now has OGDF-routed orthogonal wires + port-preserving
    # symbol placements.

Design notes in:
  ~/projects/KliCAD_development/HANDOFF.md      (Option C overview)
  ~/projects/KliCAD_development/research/c5-adapter-design.md
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._circuit import Circuit
    from ..klicad import KliCAD


def _net_spec_for( circuit: "Circuit" ) -> list[tuple[str, list[tuple[str, str]]]]:
    """Build the (net_name, [(sym_ref, kicad_pin_num), ...]) spec the
    binding expects.

    Per the C.5 adapter design: every connected net goes in (including
    power/ground — the OGDF placer treats power symbols as regular
    nodes and routes wires to them).  Filtering is the C++ side's
    job if it ever needs to be.
    """
    net_to_pins: dict[str, list[tuple[str, str]]] = {}
    for part in circuit.parts:
        pin_map = getattr( part, "kicad_pin_map", None ) or {}
        for conn_key, net_name in part.connections.items():
            if not net_name:
                continue
            kicad_pin_num = pin_map.get( conn_key, conn_key )
            net_to_pins.setdefault( net_name, [] ).append(
                ( part.ref, kicad_pin_num )
            )
    return [ (name, pins) for name, pins in net_to_pins.items() ]


def relayout_via_ogdf(
    circuit: "Circuit",
    sch_path: Path | str,
    kicad: "KliCAD | None" = None,
) -> dict:
    """Call klicad_native_auto_layout.run via IPC and return the layout
    report dict.  Returns {ok: bool, symbols_placed, wires_emitted,
    crossings, bends, total_wirelength}.

    Requires KliCAD to be running (the kiface binding lives in
    eeschema_kiface and is only available via the IPC API)."""
    from ..klicad import KliCAD as _KliCAD  # local import to avoid cycle

    if kicad is None:
        kicad = _KliCAD()

    nets = _net_spec_for( circuit )
    sch_str = str( Path( sch_path ).expanduser().resolve() )

    code = (
        "import klicad_native_auto_layout as al\n"
        f"result = al.run({sch_str!r}, {nets!r})\n"
    )
    r = kicad.run_python( code )
    # run_python returns the last expression's repr in r.result_repr.
    # For a dict-returning call we expect e.g. "{'ok': True, ...}".
    return r

# Schematic layout iteration log

Strategy-A iteration cycle. Reference design: `~/projects/driver-board/8pin/build_schematic.py`.
Each entry: hypothesis -> change -> verdict, with before/after PNG paths.

## Baseline

Rendered current code (`klipy/circuit/_layout.py` + `_route.py` + `_partition.py`)
against the 8pin driver design. KliCAD launched on the `driver8.kicad_pro`
project; `build_schematic.py` invoked.

- Output: `docs/research/layout-screenshots/baseline/driver8.png` (top sheet)
- Output: `docs/research/layout-screenshots/baseline/driver8-CH1.png` (channel body)

Top-sheet observations:
- All 17 top-level parts (1 VLOAD source + 8 VPULSE gate stubs + 8 VDC iopin
  stubs + 8 RLOAD + 8 LLOAD + 8 sheets) cram into the left ~third of the
  A4 page; right two-thirds is empty.
- VPULSE PULSE-parameter strings overlap with RLOAD resistor symbols /
  values. Value text width >> SLOT_DY (15.24 mm), so adjacent column
  collision is inevitable.
- LLOAD `5m` value text overlaps with neighbouring RLOAD column.
- CH1..CH8 sheets land at the bottom-left, overflowing off the page; the
  layout did not account for sheet bbox size at all.
- A stray "GND/GND" label cluster sits mid-page (orphan power symbol).

Channel-sheet observations:
- Hier-label column at top-left (GATE, IOPIN, VLOAD, PIN, PD_OPT) is tight
  but legible.
- Individual passive parts (RGS, RGATE, RSER, RPD) all stacked vertically
  in one column with very small lateral separation, value labels touching
  the symbol body.


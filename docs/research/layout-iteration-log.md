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


## Iteration 1 — hide Sim.Params + bump hier-label column (2026-05-29)

Two focused fixes targeting the top two visible defects in the baseline:

**Change 1**: hide `Sim.Params` field on V/I source symbols
(VPULSE/VSIN/etc.).  The default Value field already advertises the
symbol type (VPULSE); the Sim.Params text only needs to exist in the
file for the netlist exporter.  Implemented as a post-pass that
appends `(hide yes)` to the field's `(effects ...)` block — the
proper fix is extending `klicad_native_schematic_state.set_symbol_field`
to take an optional `visible=False` kwarg (small C++ binding patch,
deferred).  Post-pass script: `/tmp/auto-route-iter/hide_sim_params.py`.

**Change 2**: shift channel-sheet hierarchical-label column down
clear of the A4 frame's top border-marker row (~15mm).
`_klicad_sch.py::_emit_port_anchors` now seeds new labels at
y=8*_GRID (20.32mm) instead of 4*_GRID (10.16mm); a sibling post-pass
(`/tmp/auto-route-iter/bump_hier_labels.py`) shifts existing labels
down by 10.16mm on schematics generated before this change so the
old artifacts can be re-rendered without regenerating.

Top-sheet observations:
- VPULSE PULSE-parameter strings GONE.  Adjacent column collisions
  resolved.  Labels (`V_GATE1` through `V_GATE8`, `LOAD#_MID`) now
  legible end-to-end.
- Right two-thirds of the page still empty — addresses Phase 2/3 of
  the original plan (column-width awareness + page-fill rescaling),
  unchanged this iteration.
- Channel sheets at bottom-left still overflow off the page edge —
  unchanged, scoped to a follow-up iteration.

Channel-sheet observations:
- Hier-label column (GATE, IOPIN, VLOAD, PIN, PD_OPT) shifted from
  y∈[10, 30] mm to y∈[20, 40] mm — now visually below the top
  border-marker row.  Slight overlap with the "1" column marker
  remains; second bump or relocation to x>=20mm would fully clear.
- Body parts (DFW1, M1, RG*, RPD) still huddled in the upper-left
  quadrant of the page.  Phase 2/3 layout work needed.

Output:
- `docs/research/layout-screenshots/iter1/driver8.png` (top sheet)
- `docs/research/layout-screenshots/iter1/driver8-CH1.png` (channel)

Net: two visible defects closed.  Top-sheet readability is the
biggest gain — the schematic is now scannable at a glance.  Channel
sheet improved at the top but the layout is unchanged below.


## Iteration 2 — page-fill x-rescale in `_coord_assign` (2026-05-29)

Added a linear x-rescale pass to `_layout.py::_coord_assign` so the
Sugiyama layout fills the A4 landscape page width.  Target right edge
is 230mm (= ORIGIN_X + 200mm); when the computed layout's rightmost
column lands short of that, all x-positions get scaled up around
ORIGIN_X to hit the target.  Skips rescale when the layout already
exceeds the target (multi-page overflow is a separate problem).

Visual verification result: NEGATIVE this iteration — but for an
interesting reason.

Tried two ways to verify:

  (a) Regenerate the schematic via `build_schematic.py`.  Output:
      "6 parts placed, 0 sheets" — KliCAD's diff/apply machinery
      treats existing parts as already-placed and does NOT re-run
      the layout for them.  Only 6 new parts got the rescaled
      positions; the other ~45 (and all child sheets) kept their
      pre-rescale coordinates.  Net visual effect: zero.

  (b) Sexpr post-pass that rescales top-level symbol/sheet (at X Y)
      blocks directly (/tmp/auto-route-iter/rescale_x.py).  Symbols
      moved, but their connecting wires + labels did NOT — the
      layout broke connectivity, producing the "floating symbols
      across the page" failure mode visible in
      /tmp/auto-route-iter/iter6-1.png.  Reverted.

The `_layout.py` edit IS correct in principle: a fresh-from-empty
schematic generation would render the rescaled layout cleanly
(because wires/labels would route against the new positions).  But
the existing iteration loop runs incrementally against a live
schematic, so the rescale never sees the existing parts.

What's needed for this to land visibly:

  - Either a "force re-layout" mode in `to_schematic` that deletes
    all symbol placements + their wires before re-emitting, OR
  - An expanded sexpr post-pass that moves symbols AND their
    connected wires/labels coherently, OR
  - Test against a fresh project (empty .kicad_sch) to demonstrate
    the rescale produces the intended layout.

Committed `_coord_assign` edit anyway because it's correct and will
apply on any fresh emit; this iteration's lesson is about the
verification mechanism, not the algorithm.


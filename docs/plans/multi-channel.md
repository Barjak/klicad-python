# Multi-channel design (Altium-style REPEAT) — implementation plan

Status: **DRAFT — pending audit**

This plan adds native N-into-1 sheet collapse to KliCAD's schematic
editor + klicad-python's DSL.  Today a 60-channel design produces 60
visually identical `SCH_SHEET` items on the parent canvas; with this
feature, **one** `SCH_SHEET` annotated `repeat=60` represents all 60
channels, with bus-syntax sheet pins distributing one bit of each bus
to each channel.

Goal: design feature parity with Altium's `REPEAT(SheetSymbol, 1, N)`
+ Cadence-style instance arrays.  Tracked upstream as KiCad GitLab
issues [#1998](https://gitlab.com/kicad/code/kicad/-/issues/1998) and
[#13814](https://gitlab.com/kicad/code/kicad/-/issues/13814); both
open with no milestone.  This plan is what we'd implement if upstream
hasn't acted by the time we need it for the 60-channel driver-board.

## Established context

1. **Thin-layer principle** (CONTRIBUTING.md).  klicad-python
   orchestrates; the data-model + connectivity + netlist logic lives
   in KliCAD's C++.
2. **`(sheet_path, ref)` diff identity** was the H3 plan's locked
   decision; nested hierarchy (H4) was deferred specifically because
   the diff-key refactor needed to land first.  This feature reopens
   that question — repeated sheets multiply the path space.
3. **Today's pattern** is N `SCH_SHEET` items sharing one
   `.kicad_sch` via complex hierarchy.  klicad-python already does
   the SCH_SCREEN-sharing optimization (`add_sheet` looks up an
   existing screen by filename).  The hierarchy walker emits N
   distinct `SCH_SHEET_PATH`s, one per instance.
4. **Bus syntax** at the sheet-pin level already produces a bus on
   the parent side, but pins inside the child sheet must use the
   expanded scalar names (`DATA[3]` etc.) and N instances of the
   body must exist for N channels.  We don't get free replication.
5. **PCB Multi-Channel tool** (upstream pcbnew feature) replicates
   layout across sheet *paths*.  Whatever schematic-side
   representation we land on must produce N sheet paths in the
   netlist, or the PCB tool breaks.

## Decisions to lock (proposed)

Pending the auditor's input.  Defaults shown.

- **Schematic data model**: `SCH_SHEET` gains a `m_repeat_count`
  integer, default 1.  Backward compatible — `.kicad_sch` v <
  20260520 round-trips unchanged.  See "Alternative B" below for
  the netlist-only variant.
- **Sheet path expansion**: a sheet with `repeat_count = N` yields
  N logical `SCH_SHEET_PATH`s during hierarchy walk.  This is the
  invasive change — every code path that iterates
  `Schematic().Hierarchy()` learns to handle it.
- **Bus-pin bit distribution**: sheet pin `FOO[0..N-1]` on a
  repeated sheet routes bit M to instance M's `FOO` net (inside
  the body, `FOO` is a scalar reference).  Scalar pins are shared
  across all instances (KiCad's existing global-power semantics for
  GND/VCC carry over automatically).
- **Refdes annotation**: per-instance refs follow Altium convention
  `<parent_ref>_<instance>` (e.g., `U1_CH1`..`U1_CH8`).  Internal
  body refs get path-distinguishing annotation as today.
- **DSL surface**: `sub.instance("U1", repeat=8, **port_map)`
  returns ONE `SubcircuitInstance` with `repeat_count=8`.  Port
  validation checks that every bus port has width equal to
  `repeat_count` (or is scalar = shared).

## Alternative B (netlist-time expansion)

Instead of teaching every KliCAD code path about repeated sheets,
keep the schematic representation single-instance and **expand at
netlist export time**: SPICE deck + KiCad-native netlist emit N
component instances per repeat block, but the schematic editor +
ERC + connectivity graph only ever see ONE child sheet.

**Pros**: localized change (only the netlist exporter); upstream PCB
Multi-Channel tool continues to see N sheet paths in the netlist.

**Cons**: ERC can't detect issues that only manifest at expansion
(e.g., a bus pin's width mismatch with `repeat_count`); the GUI
hierarchy navigator shows 1 entry but the netlist + PCB show N —
asymmetry violates the principle of least surprise; doesn't deliver
the user-visible "8 sheets collapse to 1 sheet in the navigator"
result they asked for.

## Alternative C (compile-time expansion in klicad-python)

Keep KliCAD untouched.  klicad-python's `to_schematic` expands a
`repeat=N` `SubcircuitInstance` into N regular instances at emit
time, producing today's "8 SCH_SHEETs sharing one file" pattern
visible in KliCAD.

**Pros**: zero C++ work; ships immediately.

**Cons**: doesn't deliver the visual collapse; KliCAD still shows
the N-fold repetition in the hierarchy navigator; this is what we
have today.  Not really a "feature".

## Recommended approach (pending audit)

**Alternative A** (native data model + path expansion), with these
priorities:

1. Get the SCH_SHEET data-model change in first; everything else
   builds on it.
2. Hide the implementation behind a feature flag during early phases
   so the existing schematic save/load doesn't surprise users.
3. PCB Multi-Channel tool integration: prefer to *teach* it (proper
   fix) but fall back to *export-time explosion* (a netlist-only
   shim) if that proves intractable.

---

# Phase R1 — SCH_SHEET data model + serialization

**Goal**: `SCH_SHEET` carries a `repeat_count` attribute; round-trips
through `.kicad_sch`.

## Files touched (KliCAD)

| File | Change |
|---|---|
| `eeschema/sch_sheet.h` / `sch_sheet.cpp` | Add `m_repeat_count` int; getter/setter; default 1; clone in copy ctor |
| `eeschema/sch_io/kicad_sexpr/sch_io_kicad_sexpr_parser.cpp` | Parse `(repeat_count <N>)` s-expr token |
| `eeschema/sch_io/kicad_sexpr/sch_io_kicad_sexpr.cpp` | Emit `(repeat_count <N>)` when `> 1`; skip when default |
| `eeschema/sch_sheet.cpp` (visual) | Render `×N` decoration when `repeat_count > 1` (top-right corner of sheet rect) |

## File-format compatibility

- `repeat_count == 1` → emit nothing extra.  Schematic byte-identical
  to today's output for non-repeated sheets.
- `repeat_count > 1` → emit `(repeat_count N)` token.  KiCad versions
  predating this feature would lex-warn-and-ignore (`(repeat_count
  …)` is unknown).  Verified upstream: KiCad's s-expr parser does
  ignore unknown tokens at sheet level rather than erroring.
- Format version bump optional — would let us reject older KiCad
  versions explicitly rather than letting them silently lose the
  attribute.  Default: bump.

## Tests

- Pure-C++: `qa/eeschema/test_sch_sheet_repeat.cpp` — construct
  `SCH_SHEET` with various `repeat_count`, serialize to s-expr,
  parse back, verify round-trip.
- Backward compat: load a current-day .kicad_sch with no repeat
  tokens, verify `repeat_count == 1`.

# Phase R2 — Hierarchy walker + SCH_SHEET_PATH expansion

**Goal**: `Schematic().Hierarchy()` enumerates N logical paths per
repeated `SCH_SHEET`.

## Files touched

| File | Change |
|---|---|
| `eeschema/sch_sheet_path.h` / `.cpp` | Each `SCH_SHEET_PATH` segment can carry an `instance_index` int (default 0).  `Hierarchy()` emits paths for `instance_index ∈ [0, repeat_count)` when a sheet repeats. |
| `eeschema/connection_graph.cpp` | Walks `m_hier_pins` per-sheet-path; needs to bus-bit-route bus-named pins when `instance_index > 0` (or generally: per path, pin's value = bit `instance_index` of the bus) |
| `eeschema/symbol_instance.h` / wherever per-path refdes lives | Annotation tags every instance's body parts; needs to handle the extra path discriminator |

## Risks

- Wherever code does `for( const SCH_SHEET_PATH& path : sch.Hierarchy() )` and then walks `path.LastScreen()->Items()`, the per-path scope now needs to know about the instance index for any bus-port content.  This is invasive — likely 30+ call sites across KliCAD.
- Annotation needs to assign per-(path, instance_index) refs.  The existing per-path machinery may handle this with a path encoding extension or may need an additional dimension.

## Tests

- `qa/eeschema/test_repeated_sheet_hierarchy.cpp` — schematic with
  one `repeat_count=8` sheet; `Hierarchy()` returns 8 entries with
  distinct `instance_index`; connection graph distributes bus bits;
  netlist emits 8 component instances per body part.

# Phase R3 — Bus-pin bit distribution at connection-graph build

**Goal**: a sheet-pin `DATA[0..7]` on a `repeat=8` sheet routes bit M
of the external bus to instance M's `DATA` net.

## Files touched

| File | Change |
|---|---|
| `eeschema/connection_graph.cpp:575-578` (existing `m_hier_pins` insertion) | When parent sheet is repeated AND pin is bus-named with width matching `repeat_count`, insert per-instance pin connections to bus members rather than the full bus |
| `eeschema/sch_connection.cpp` | Augment bus-resolution to recognize "this pin is a single bit of the parent's bus, selected by `instance_index`" |

## Validation

- Bus pin width must match `repeat_count` exactly.  Mismatch: ERC error.
- Scalar pins on a repeated sheet shared across all instances (existing
  semantics; no change).
- Bus pins on a *non*-repeated sheet behave as today (full bus passes
  through).

# Phase R4 — Netlist export

**Goal**: SPICE deck + KiCad netlist emit N component instances per
body part on a repeated sheet.

## Files touched

| File | Change |
|---|---|
| `eeschema/netlist_exporters/netlist_exporter_kicad.cpp` | Per-path iteration now sees `repeat_count > 1` paths; emit per-instance refs |
| `eeschema/netlist_exporters/netlist_exporter_spice.cpp` | Same — emit per-instance X-lines |
| `eeschema/sch_symbol.cpp:GetRef(SCH_SHEET_PATH*)` | When path has `instance_index > 0`, append the index to the base ref (`U1` → `U1_CH3`) |

## Backward compatibility

Existing netlists (non-repeated) byte-identical.  For repeated
sheets, the per-instance suffix follows Altium convention but is
configurable per-project (default: `_CH<N>`, alt: `[<N>]`, `<N>`).

# Phase R5 — klicad-python DSL

**Goal**: `sub.instance("U", repeat=8, **port_map)` returns one
`SubcircuitInstance` with `repeat_count=8`.  `to_schematic` lays it
down as ONE `SCH_SHEET` with `repeat_count=8`.

## Files touched (klicad-python)

| File | Change |
|---|---|
| `klipy/circuit/_part.py` | `SubcircuitInstance` gains `repeat_count: int = 1` field |
| `klipy/circuit/_circuit.py` | `Circuit.instance(ref, repeat=1, **port_map)`; validate every bus port has width == `repeat`, every scalar port is shared |
| `klipy/circuit/_klicad_sch.py:_place_sheet_instances` | Emit ONE `add_sheet` call with `repeat_count=N`; pin width validation |
| `klipy/circuit/_spice.py` | When emitting `.SUBCKT` body, repeated instances generate N X-lines from a single SubcircuitInstance |
| `klipy/circuit/_bus.py` | Bus-port-vs-repeat-count width validation |

## New C++ binding

`klicad_native_schematic_state.add_sheet(..., repeat_count=1)` —
extend existing binding to accept `repeat_count`.  No new binding
file.

## Tests

- Pure-Python: `sub.instance("U", repeat=8)` validates port widths,
  emits one SubcircuitInstance with `repeat_count=8`.
- Live KliCAD: place a `repeat=8` instance, save, reload, verify
  hierarchy walker enumerates 8 paths, SPICE deck has 8 X-lines.

# Phase R6 — Bidirectional GUI ↔ DSL round-trip

**Goal**: an existing `.kicad_sch` with a `repeat_count` sheet is
visible to klicad-python's diff machinery as a single
`SubcircuitInstance` with `repeat_count > 1`.

## Files touched

| File | Change |
|---|---|
| `klipy/circuit/_klicad_sch.py:_emit_one_sheet` | `list_sheets()` now returns per-row `repeat_count`; diff identity stays `(sheet_path, ref)` but a kept sheet's `repeat_count` is part of the demotion check (changed repeat count → remove+add, like file_name mismatch) |
| `klicad_native_hierarchy.list_sheets` | Include `repeat_count` in each row |
| `klicad_native_schematic_state.list_symbols` | When a sheet is repeated, the body parts appear under N paths; the diff needs to dedup by (file_name, body_ref) so we don't see "R1×8 to delete" on a fresh start |

# Phase R7 — PCB Multi-Channel tool integration

**Goal**: upstream's `pcbnew/tools/multichannel_tool.cpp` continues
to work with the new representation.

## Strategy choice

**Strategy A (teach upstream)**: modify `multichannel_tool.cpp` to
recognize `repeat_count > 1` sheets and treat them as N peer rule
areas.  Pros: cleanest.  Cons: touches more upstream code; potential
upstream-merge conflict if KiCad later adopts a different repeat
representation.

**Strategy B (netlist-time explosion shim)**: leave Multi-Channel
tool alone; when exporting the netlist for PCB, explode repeated
sheets into N synthetic sheet paths so PCB sees them as separate.
Pros: zero churn in Multi-Channel.  Cons: netlist asymmetry (sch
shows 1 sheet, netlist + PCB show N).

Default: **Strategy B** for the first release (minimize blast
radius), Strategy A as follow-up if user-visible asymmetry bites.

# Open questions (for auditor)

1. **Refdes encoding under repeated sheets**: Altium uses
   `U1_CH<N>`; Cadence uses `U1<N>` or `U1[<N>]`.  KiCad's existing
   convention for complex hierarchy is `U1` with the path
   distinguishing.  Should `U1[3]` (Cadence-style) be the default,
   or `U1_CH3` (Altium-style), or `U1/3/Q1` (extending the existing
   KiCad path-ref convention)?
2. **Bus pin width vs `repeat_count` mismatch**: error or
   broadcast?  Altium errors; Cadence allows partial connections.
   Erring is safer; broadcasting is more permissive.
3. **`SCH_SHEET_PATH` data structure**: extending each segment with
   an `instance_index` int adds 4-8 bytes per path segment globally
   — meaningful for designs with deep hierarchies.  Alternative: use
   a separate `SCH_SHEET_PATH_INSTANCE` wrapper class with the index.
4. **Hierarchy navigator UX**: a repeated sheet should probably show
   as one entry with "×N" annotation, expandable to N instances.
   Upstream's `eeschema/widgets/hierarchy_pane.cpp` would need
   updating.  Defer to R8 polish phase or include in R2?
5. **`SCH_SHEET_PIN.GetShape()` semantics under repeat**: input/
   output/bidirectional shape applies per-bit.  Any visual
   difference needed in the GUI?

# Out of scope

- Heterogeneous repeat (different parameters per channel — Altium's
  `REPEAT(Sym, 1, 8, ChNum)`).  R-phase reserved for homogeneous
  repeats only.
- Pre-existing SCH_SHEET items can't be auto-converted to repeated
  — user must opt in by setting `repeat_count` manually or via DSL.
- Per-instance net overrides (e.g., "channel 4 has a different
  flyback diode").  Heterogeneous body content requires either a
  separate sheet (today's pattern) or a parameterization extension
  beyond this plan.
- Hot-reload of `repeat_count` changes in an open schematic.
  Changing N requires schematic-close + reopen for the hierarchy
  walker to re-emit paths.

# Estimated scope

| Phase | Effort estimate |
|---|---|
| R1 (data model + serialize) | One substantial session |
| R2 (hierarchy walker) | One substantial session (heaviest) |
| R3 (connection graph) | One substantial session |
| R4 (netlist export) | Half-session |
| R5 (DSL) | Half-session |
| R6 (diff round-trip) | Half-session |
| R7 (PCB tool) | Half-session (Strategy B) or full (Strategy A) |

Total: ~5-7 sessions of focused work, plus audit + test passes at each
phase boundary.

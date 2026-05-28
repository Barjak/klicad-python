# Multi-channel design (REPEAT-style sheets) — implementation plan

Status: **R0–C.7 LANDED on `loop/integration-7`; implementation diverged
from this spec on the body's "scalar net" interpretation.**

> ⚠ **Read before implementing**:
> `~/projects/loop-state/audit-context/multi-channel-vectorization-discussion.md`
> records a shape-preservation finding against R5.6/R5.7 + R3.3: the
> spec calls (correctly) for a **vectorized** body with one scalar
> hier-port per declared bus, but the implementation lowered into a
> hand-unrolled body with N hier-labels and N body parts.  The fix is
> at the C++ `CONNECTION_GRAPH::propagateToNeighbors` matcher and is
> being audited for three candidate approaches.  Until that lands,
> treat the DSL section below as the *authored* shape and assume
> R5.6/R5.7's emit will collapse once the C++ accepts the
> scalar-body-port match.

This plan adds native N-into-1 sheet collapse to KliCAD's schematic
editor + klicad-python's DSL.  Today a 60-channel design produces 60
visually identical `SCH_SHEET` items on the parent canvas; with this
feature, **one** `SCH_SHEET` annotated `repeat_count=60` represents
all 60 channels, with bus-syntax sheet pins distributing one bit of
each bus to each channel.

Tracked upstream as KiCad GitLab [#1998](https://gitlab.com/kicad/code/kicad/-/issues/1998)
and [#13814](https://gitlab.com/kicad/code/kicad/-/issues/13814); both
open with no milestone.

## The chosen approach (post-audit): synthetic-instance complex hierarchy

The audit (`task ad0100eda1ff369a82`) rejected the original
"`SCH_SHEET_PATH` instance_index" design as gratuitously invasive
(touches ~165 path-handling call sites, breaks `KIID_PATH`
round-tripping that `FOOTPRINT::m_path` depends on, and forces refdes
mangling).  Better insight:

**KiCad already has the primitive we need — complex hierarchy.**  Today
multiple `SCH_SHEET` items can share one `SCH_SCREEN` via filename
match (klicad-python uses this in `add_sheet`).  The hierarchy walker
produces N distinct `SCH_SHEET_PATH`s whose only difference is the
last segment's KIID; annotation already hands out distinct refs per
path; PCB Multi-Channel detects peers via `(sheetname, sheetfile)`
strings derived from those paths.

What's actually missing for the user's "one block on canvas, N
channels in the netlist" goal is purely **the canvas-side collapse**.
Everything else (path enumeration, annotation, netlist, PCB sync,
ERC) already works for the N-distinct-sheets case.

The plan: a `SCH_SHEET` carrying `m_repeat_count = N` is **rendered as
one block** on the canvas, but **materialized as N synthetic SCH_SHEET
children** at `BuildSheetList` time, each sharing the on-canvas
sheet's `SCH_SCREEN` and carrying a pre-allocated stable KIID from the
parent's `repeat_instances` list.  Downstream consumers see what looks
like N normal complex-hierarchy peers.  Only two pieces of genuinely
new logic are needed:

1. **Bus-pin bit fan-out** at the connection-graph level — when a
   sheet pin is bus-named on a repeated sheet, the per-path
   `instance_index` (derivable from "which slot in the synthetic-list
   this path's last KIID occupies") selects which bit of the parent's
   bus connects to the body's scalar net.
2. **ERC marker dedup** — N identical body-faults must collapse to
   one marker on the report.

The rest of the work is data-model + serialization + DSL plumbing.

## Established context

1. **Thin-layer principle** (CONTRIBUTING.md).  klicad-python
   orchestrates; the data-model + connectivity + netlist logic lives
   in KliCAD's C++.
2. **`(sheet_path, ref)` diff identity** (locked in H3).  Synthetic
   clones produce N real paths; identity stays `(KIID_PATH, ref)` on
   both schematic and DSL sides.  No new key shape needed.
3. **Spice-idempotence**: the SPICE deck for a repeated sheet must
   depend only on the Circuit's `repeat=N` declaration, not on the
   history of how synthetic clones were allocated.  KIIDs are stable
   across save/load (serialized in `repeat_instances`).
4. **PCB Multi-Channel tool** (upstream pcbnew) keys on
   `FOOTPRINT::GetSheetname()/GetSheetfile()` strings produced by
   `SCH_SHEET_PATH::PathHumanReadable`.  Synthetic clones get
   distinct KIIDs → distinct human-readable paths → automatic peer
   detection.  Zero pcbnew code change required.

## Decisions locked

- **Refdes encoding**: existing KiCad per-path annotation.  Refs are
  `U1, U2, … UN` (or whatever annotation hands out), distinguished
  via `SCH_SHEET_PATH` exactly as today's complex hierarchy.  No
  `_CH<N>` suffix; no `[N]` bracket form.
- **SCH_SHEET_PATH structure**: **unchanged**.  No `instance_index`
  field, no new segment shape.
- **File format**: **mandatory version bump**.  Current version
  `20260326` → new `20260601` (or month of landing).  Older KiCad
  loading a v20260601 file will refuse rather than silently dropping
  the repeat info.
- **Bus pin width**: **strict-equal-or-error**.  Bus pin `FOO[0..K-1]`
  on a `repeat=N` sheet requires `K == N`.  No partial binding, no
  fan-in, no broadcast.  Altium's rule.
- **Scalar ports** are shared across all instances (consistent with
  implicit-power semantics: a scalar `EN` on a `repeat=8` sheet means
  one signal connects to all 8 channels).  Per-channel fan-out
  requires explicit bus syntax: `EN[0..7]`.
- **Annotation policy**: interleave freely with non-repeated
  symbols.  No reserved blocks.  Existing behavior; cheap default.
- **Shrink semantics**: when `repeat_count` decreases (N → M, M<N),
  KIIDs for dropped instances vanish; per-path `SCH_SYMBOL_INSTANCE`
  entries are silently garbage-collected.  KIID regeneration on
  re-grow is deterministic from a salt + index.
- **Canvas rendering**: same-size sheet with "×N" decoration in the
  corner.  No visual tile / grow — that would complicate selection
  bbox calculations and the diff/apply position contract.
- **Hierarchy navigator UX**: N entries (the natural output of
  synthetic expansion).  A "collapse repeated peers" view toggle is
  deferred to a polish phase.

## DSL surface (klicad-python)

```python
amp = Circuit("channel", ports=["GATE", "OUT", "DATA[0..N-1]"])
amp.add(...)

top = Circuit("driver")
top.add(amp.instance("U_CH",            # single ref; annotation expands
                     repeat=8,
                     GATE="GATE_BUS[0..7]",
                     OUT="OUT_BUS[0..7]",
                     DATA="DATA_BUS[0..7]"))
```

Internally:
- `SubcircuitInstance` gains `repeat_count: int = 1` field.
- `Circuit.instance(ref, repeat=N, **port_map)` validates: every bus
  port has width matching N exactly; scalar ports are shared.
- `to_schematic` emits ONE `add_sheet(...)` call with `repeat_count=N`.
- `to_spice_deck` emits N X-lines from the single SubcircuitInstance
  by walking the expanded port-map.

---

# Phase R0 — `SCH_SHEET_PATH.Last()` consumer audit

**Goal**: enumerate all KliCAD call sites that walk back from
`path.Last()` and operate on the user-placed SCH_SHEET (fields,
properties, geometry).  Synthetic clones must forward those calls
to the on-canvas template, or hold their own forwarded copies.

## Deliverable

A short report (in `docs/plans/multi-channel-r0.md`) listing each
call site with a verdict:

- **Trivial** — the field is always read from the template (e.g.,
  `Last()->GetName()` already returns the same string for any clone)
- **Needs forwarding** — synthetic clone should return the template's
  value via a delegating getter
- **Needs per-instance** — synthetic clone needs its own value
  (almost certainly only `m_Uuid`)

## Method

```bash
grep -rnE 'path\.Last\(\)|sheetPath\.Last\(\)|\.Last\(\) *->' \
    /home/jakob/projects/KliCAD/eeschema/ | sort -u
```

Then per-call audit, mostly mechanical.

# Phase R1 — `SCH_SHEET.repeat_count` + serialization

**Goal**: `SCH_SHEET` carries `repeat_count` + a `repeat_instances`
KIID list; round-trips through `.kicad_sch`.

## Files touched

| File | Change |
|---|---|
| `eeschema/sch_sheet.h` / `.cpp` | Add `m_repeat_count: int = 1` and `m_repeat_instances: std::vector<KIID>`; getters / setters; copy ctor clones both |
| `eeschema/sch_io/kicad_sexpr/sch_io_kicad_sexpr_parser.cpp` | Parse `(repeat_count N)` and `(repeat_instances (uuid ...) ...)`; default to `N=1, empty vector` when absent.  **Verify** the existing parser behavior on unknown tokens at sheet level (the audit flagged this as the unverified claim from the prior draft). |
| `eeschema/sch_io/kicad_sexpr/sch_io_kicad_sexpr.cpp` | Emit both tokens when `repeat_count > 1`; skip when default.  Mirror the existing `sheet_instances` emission pattern (line 1693-1707). |
| `eeschema/sch_file_versions.h` | Bump `SEXPR_SCHEMATIC_FILE_VERSION` to next month's date with comment "Sheet repeat instances" |
| `eeschema/sch_sheet.cpp` (paint) | Render "×N" decoration in top-right corner when `repeat_count > 1`; keep bbox unchanged |

## KIID allocation

When `repeat_count` is set or increased (DSL or GUI):
- Generate `repeat_count - 1` additional KIIDs (one is implicit — the
  on-canvas SCH_SHEET's own `m_Uuid` is instance 0).
- Store in `m_repeat_instances` in slot order; serialize with the
  sheet.
- Stable across save/load — synthetic-clone identities don't change.

When `repeat_count` decreases: pop tail KIIDs from `m_repeat_instances`.
Orphan symbol-instance entries on those vanished paths are GC'd by
the next `SCHEMATIC::CleanupOrphans` pass (or eagerly here — TBD).

## Tests

- `qa/eeschema/test_sch_sheet_repeat.cpp` — construct SCH_SHEET,
  set `repeat_count`, serialize to s-expr, parse back, verify
  identical state.
- Backward compat: load current-day `.kicad_sch` (no tokens) →
  `repeat_count == 1`, `m_repeat_instances.empty()`.

# Phase R2 — `BuildSheetList` synthetic expansion

**Goal**: `SCH_SHEET_LIST::BuildSheetList` materializes N synthetic
sibling `SCH_SHEET` pointers per repeated sheet.

## Files touched

| File | Change |
|---|---|
| `eeschema/sch_sheet_path.cpp:984-1059` (`BuildSheetList`) | When pushing a child SCH_SHEET, check `repeat_count`.  If `> 1`, push `repeat_count` synthetic siblings instead, each with a `m_Uuid` from the parent's `m_repeat_instances` slot (with the first slot being the on-canvas sheet's actual `m_Uuid`).  Synthetic clones share `m_screen` and all other fields with the template via a thin `SCH_SHEET_INSTANCE_VIEW` shim, OR via copy-on-the-stack siblings whose lifetime is bounded by the walk. |
| `eeschema/sch_sheet.h` / `.cpp` | Possibly add an `IsSynthetic() const` predicate (returns true if this is a transient clone rather than the on-canvas user-placed sheet).  Required only if some code paths need to distinguish them — verify in R0 audit. |

## Lifetime model

The synthetic clones live in the `m_currentSheetPath` vector during
the walk.  `SCH_SHEET_PATH` already holds raw `SCH_SHEET*`; we just
need those pointers to remain valid for the consumer.  Two designs:

- **(a) Stack-allocated siblings** — `BuildSheetList` constructs N
  `SCH_SHEET` copies on its own stack, pushes/pops them during the
  walk.  Lifetime ends at walk completion; downstream consumers must
  not store the path past then.  Risky — existing consumers DO store
  paths (`SCH_SHEET_LIST` itself is a vector of paths).
- **(b) Schematic-owned clone cache** — the schematic maintains a
  `std::vector<std::unique_ptr<SCH_SHEET>>` of synthetic clones,
  rebuilt by `BuildSheetList` and held until the next rebuild.
  Lifetime matches `SCH_SHEET_LIST`.  Cleaner; small memory cost
  (one full `SCH_SHEET` struct per synthetic clone).

Recommendation: **(b)**, with the clone cache invalidated on any
`repeat_count` change or schematic-level mutation that affects the
hierarchy.

## Tests

- `qa/eeschema/test_repeated_sheet_hierarchy.cpp` — schematic with
  one `repeat_count=8` sheet; `Schematic().Hierarchy()` returns
  9 entries (root + 8 instances); each instance has a distinct
  `Last()->m_Uuid`; all instances point to the same `LastScreen()`.

# Phase R3 — Bus-pin bit fan-out at connection-graph build

**Goal**: a sheet-pin `DATA[0..7]` on a `repeat=8` sheet routes bit M
of the external bus to instance M's `DATA` scalar net.

## Files touched

| File | Change |
|---|---|
| `eeschema/connection_graph.cpp:575-578, 2892` | When processing a hier-pin's connection during graph build, check if the *containing sheet path's parent* has `repeat_count > 1`.  If so, derive `instance_index` from the path's last KIID position in the parent's `m_repeat_instances`.  For bus-named pins of matching width, select bit `instance_index` as the body's scalar binding; for scalar pins, behavior unchanged (shared). |
| `eeschema/connection_graph.cpp:2895,453,2956` | **R2 audit (F')** flagged: the propagation logic appends `pin->GetParent()` to a path while iterating hier-pins.  `pin->GetParent()` is always the on-canvas template, so paths constructed this way end with the template even when the consumer is a clone slot.  The KIID match at `:2956` then fails for slots 1..N-1.  Fix: when the path's existing last segment is a synthetic clone with matching slot KIID, use the clone in place of `pin->GetParent()`. **Acceptance test**: drive a hier-pin on a `repeat=4` sheet, verify subgraph propagation reaches all 4 child subgraphs. |
| `eeschema/sch_connection.cpp` | Possibly extend `SCH_CONNECTION` to recognize "this connection is bit-K of a parent-sheet bus" — TBD; may not be needed if the index resolution happens at graph-build time only. |

## Validation

- Bus pin width != `repeat_count` → ERC error at graph build (not
  silent).
- Scalar pin on repeated sheet → connects to all N instances'
  identical-name net (existing semantics, no change).

## Tests

- `qa/eeschema/test_repeated_sheet_busfanout.cpp` — schematic with
  one `repeat=4` sheet whose pin is `DATA[0..3]`; verify each
  instance's body sees a distinct bit of the parent's
  `DATA_BUS[0..3]`.

# Phase R3.5 — ERC marker dedup

**Goal**: N identical body-faults emit one marker on the ERC report,
not N.

## Files touched

| File | Change |
|---|---|
| `eeschema/erc/erc_report.cpp` (and wherever markers are aggregated) | After ERC walks all paths, dedup the marker list keyed on `(SCH_ITEM->m_Uuid, rule_id)` — markers for the same underlying body item across different synthetic paths collapse to one entry.  The marker's display path is the *template* (on-canvas) sheet, not the first synthetic clone. |

## Subtlety

For markers that DO differ per instance (e.g., a bus-bit fan-out
violation specific to instance 3), the dedup key should preserve the
instance.  Most body-internal faults are template-shared; bus-pin
related faults are per-instance.  Distinguish at marker-creation
time by tagging the source.

## Tests

- `qa/eeschema/test_repeated_sheet_erc.cpp` — schematic with one
  `repeat=8` sheet whose body has an intentional ERC violation
  (e.g., floating pin); verify the marker count is 1, not 8.

# Phase R4 — Netlist export

**Goal**: SPICE deck + KiCad netlist emit N component instances per
body part on a repeated sheet — already true by virtue of R2's
synthetic-path expansion.  This phase is mostly verification +
SPICE-side handling.

## Files touched

| File | Change |
|---|---|
| `eeschema/netlist_exporters/netlist_exporter_kicad.cpp` | No expected changes.  Verify per-path iteration sees N synthetic paths and emits N component entries with distinct refs. |
| `eeschema/netlist_exporters/netlist_exporter_spice.cpp` | Same — verify N X-lines emit cleanly with the bus-bit-resolved nets per instance. |

## Tests

- `qa/eeschema/test_repeated_sheet_netlist.cpp` — emit netlist for a
  `repeat=4` sheet; verify 4 instances of each body symbol, each with
  the correct per-bit net binding on bus pins.

# Phase R5 — klicad-python DSL

**Goal**: `sub.instance("U", repeat=8, **port_map)` returns one
`SubcircuitInstance` with `repeat_count=8`.

## Files touched

| File | Change |
|---|---|
| `klipy/circuit/_part.py` | `SubcircuitInstance` gains `repeat_count: int = 1` |
| `klipy/circuit/_circuit.py` | `Circuit.instance(ref, repeat=1, **port_map)`; bus port width validation |
| `klipy/circuit/_bus.py` | New: `validate_port_widths_for_repeat(port_decl, repeat_count)` — each bus port must have width == repeat_count, scalars are shared |
| `klipy/circuit/_klicad_sch.py:_place_sheet_instances` | Emit ONE `add_sheet(..., repeat_count=N)` call per repeated SubcircuitInstance |
| `klipy/circuit/_spice.py` | Recursive `.SUBCKT` walker handles `repeat_count` by emitting N X-lines per SubcircuitInstance, with port_map indexed by instance |

## New C++ binding addition

`klicad_native_schematic_state.add_sheet(..., repeat_count=1)` —
extend the existing `add_sheet` binding to accept the new arg.  No
new module.

## Tests

- Pure-Python: `sub.instance("U", repeat=8)` validates port widths,
  emits one SubcircuitInstance with `repeat_count=8`.  Bus port
  width mismatch raises.
- Live KliCAD: place a `repeat=8` instance, verify ONE SCH_SHEET on
  the canvas + 8 synthetic clones from `list_sheets()`.
- SPICE: emit deck, confirm 8 X-lines with per-bit bus binding.

# Phase R6 — Diff/apply round-trip via the new representation

**Goal**: opening an existing schematic with a repeated sheet exposes
it to klicad-python's diff as a single `SubcircuitInstance` with
`repeat_count > 1`, NOT as 8 separate instances.

## Files touched

| File | Change |
|---|---|
| `klipy/circuit/_klicad_sch.py:_emit_one_sheet` | When grouping sheet rows by ref, recognize repeat-derived synthetic clones (they share `file_name` AND have UUIDs in a parent's `repeat_instances`); collapse to one virtual row with the discovered `repeat_count`. |
| `klicad_native_hierarchy.list_sheets` | Per-row include `is_synthetic: bool` and `template_uuid: str` (the on-canvas sheet's KIID) so the Python side can do the collapse. |
| `klicad_native_schematic_state.list_symbols` | When body parts appear under N synthetic paths, the Python diff dedups them by `(template_path, ref)` for the diff identity key. |

## Demotion rules

- `repeat_count` change (8 → 12) on a kept SubcircuitInstance →
  per-pin diff on the SCH_SHEET_PIN width (existing H3 mechanism;
  bus pins gain bits), KIID list extended.
- `child_filename` change on a kept ref → whole-sheet remove+add
  (existing H3 rule).
- Cross-kind change (sheet ↔ symbol with same ref) → remove+add.

# Phase R7 — PCB Multi-Channel verification

**Goal**: upstream's `pcbnew/tools/multichannel_tool.cpp` continues
to work with the new representation.

**~~No code change expected.~~** ❌  R2 audit (concern F) invalidated
this assumption.  `SCH_SHEET_PATH::PathHumanReadable`
(`sch_sheet_path.cpp:464-507`) concatenates each segment's
`SHEET_NAME` field, not its KIID.  All synthetic clones of a
template share the template's name field — so the human-readable
path is **identical** across all N slots, e.g. `/Channel/` for every
slot.  This propagates to `FOOTPRINT::Sheetname` /
`FOOTPRINT::Sheetfile` (`board_netlist_updater.cpp:662-670`); the
Multi-Channel tool keys uniqueness on the
`(Sheetname, Sheetfile)` pair at `multichannel_tool.cpp:461`,
collapsing all N channels to one rule area.

**Required code change.**  One of:

- **F1 (recommended)** — Modify `PathHumanReadable` to append a slot
  index suffix when a segment is a synthetic clone.  E.g.
  `/Channel:1/`, `/Channel:2/`, ...  Slot index derived from the
  clone's position in the parent's `m_repeatInstances` (slot 0 = no
  suffix; slots 1..N-1 = `:i`).  Touches only `sch_sheet_path.cpp`
  and the few netlist exporters that emit human-readable sheet
  paths.  Backward-compatible for non-repeated sheets.
- **F2** — Use `KIID_PATH::AsString()` (the KIID-based path) as the
  keying string in `multichannel_tool.cpp`.  Touches pcbnew; loses
  human readability in user-facing dialogs.
- **F3** — Add an explicit per-channel suffix in the sheet name
  itself when emitting the netlist.  Invasive; affects user-visible
  sheet names.

F1 is the lowest-impact and most consistent with the plan's
"existing per-path annotation" decision.  Promote F1 to a
prerequisite of R7.

## Tests

Open the 8pin design (existing archive at
`~/projects/driver-board/8pin-archive-2026-05-26/`), refactor it to
use a single `repeat=8` channel via the new DSL, regenerate the
schematic + netlist + PCB.  Run Multi-Channel tool, verify it
identifies 8 peer rule areas matching the originals.

If this fails, fall back to the netlist-explosion shim (the old
plan's Strategy B) — but the audit predicts it won't.

---

# Phasing summary

| Phase | Scope | Effort |
|---|---|---|
| R0 | `Last()` consumer audit | small (no code) |
| R1 | data model + serialization | one substantial session |
| R2 | BuildSheetList expansion | one substantial session |
| R3 | bus-pin fan-out | one substantial session (heaviest — algorithmic) |
| R3.5 | ERC dedup | half-session |
| R4 | netlist verification | half-session (mostly tests) |
| R5 | klicad-python DSL | half-session |
| R6 | diff round-trip | half-session |
| R7 | PCB tool verification | half-session (test only) |

Total: ~4-5 substantial sessions of focused work, plus audit + test
passes at each phase boundary.

## Risks remaining

1. **Synthetic clone lifetime** (R2 design choice).  Schematic-owned
   clone cache adds memory + invalidation complexity.  Mitigated by
   keeping the cache invalidation logic localized to
   `BuildSheetList`'s callers.
2. **Bus-pin fan-out** is the only place where `instance_index` has
   to be derived from the path.  If the connection-graph code
   doesn't have ready access to the path's parent sheet's
   `m_repeat_instances`, the indexing logic needs path-context
   threaded through deeper than the current code expects.
3. **ERC dedup** can over-collapse if the marker uniqueness key is
   too coarse, hiding real per-instance issues.  Conservative
   approach: dedup by `(item_kiid, rule_id, instance_distinguishing_flag)`
   where the flag is set only for instance-specific faults.
4. **Annotation re-pass** on `repeat_count` change.  Adding a 9th
   channel after annotation needs to assign a new ref for that
   path's body parts without renumbering the existing 8.  The
   existing complex-hierarchy annotation already does this; verify
   with a R5 integration test.

## Out of scope

- Heterogeneous repeat (different parameters per channel).
- Per-instance net overrides.
- Hot-reload of `repeat_count` in an open schematic — requires
  closing + reopening for the hierarchy walker to re-emit paths.
- Pre-existing N hand-placed sibling sheets auto-converting to a
  `repeat=N` block.  Requires a user-driven "collapse to repeated
  sheet" command — defer to polish.

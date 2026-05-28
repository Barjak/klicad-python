# Session-handoff status (klicad-python + KliCAD)

Last updated: end of session 2026-05-28.

This is the "where are we and why" document for picking up cleanly in
a future session.  It lists what's landed, what's paused mid-flight,
and what's queued — with the motivation for each.

## Repo state at handoff (2026-05-28)

- **klicad-python**: branch `loop/integration-7`, tip `d634f68`
  ("C.6: wire to_netlist -> ratsnest.set_spec after to_schematic").
- **KliCAD**: branch `loop/integration-7`, tip `73590277d4`
  ("C.5: klicad_native_ratsnest.set_spec binding").
- 289 klicad-python tests passing, 61 skipped, 0 failed.
- 22 C++ QA cases passing across SchSheet / SchSheetPath /
  RepeatedSheetHierarchy / RepeatedSheetBusFanout.
- No uncommitted changes in either repo.

## What landed since 2026-05-27 night

This session closed out the multi-channel arc (R3.3 finish + R5.6/R5.7
+ A.11), the netlist-decoupling arc (C.5/C.6), and three crash fixes
that surfaced during integration.  It also introduced a new auditor
gate after the multi-channel arc landed *and* the user opened the
artifact for the first time.

### Crash fixes

- **`35f1fa2a35`** (KliCAD) — null-check `m_SchematicSettings` in
  `SIMULATOR_FRAME::SaveSettings`; shutdown SIGSEGV when the project
  had been torn down by the time settings save fired.
- **`66b0239127`** (KliCAD) — re-fetch `sheetList` from
  `schematic->Hierarchy()` after `RecalculateConnections` in
  `EESCHEMA_HELPERS::LoadSchematic`; multi-channel netlist export
  was UAF'ing because `RefreshHierarchy` had cleared the synthetic-
  clone cache while the caller still held stale paths.
- **`e7ecca48ac`** (KliCAD) — `pm_load_project` now sends a new
  `MAIL_PROJECT_TEARDOWN` KIWAY mail to `FRAME_SCH` and
  `FRAME_PCB_EDITOR` before `UnloadProject`/`LoadProject`, so editor
  frames disconnect from the about-to-be-freed PROJECT.  Fixed
  the API-driven project-switch dangling pointer that the
  `docs/plans/known-issues.md` entry described.

### Multi-channel (R0 through C.7) — feature landed, shape under audit

R0 through C.7 are committed and exercised by A.11.  The netlist
exporter consolidation (R3.3 per-slot bit-member binding) is
load-bearing for every netlist downstream of multi-channel.

- **`f11fef9a89`** (KliCAD) — `describe_sheet_path` exposes
  `is_synthetic`; klicad-python's hierarchy diffs now skip
  BuildSheetList's synthetic clones when resolving the on-canvas
  template KIID.
- **`bf422c17bc`** (KliCAD) — R3.3 per-slot bit-member binding in
  `propagateToNeighbors`.  Two issues fixed: the reverse-direction
  same-type filter was rejecting scalar-body-port → bus-parent-pin
  matches (multi-channel needs this), and the final Clone() pass was
  cloning the whole-bus driver onto bit-bound subgraphs instead of
  the K-th bus member.  Added
  `CONNECTION_SUBGRAPH::m_repeat_bus_bit_index` set by the fan-out
  sites from `path.GetSlotIndex()`.
- **`f5e10b6`** (klicad-python) — R5.6 + R5.7: emit bus-shaped sheet
  pins (was N scalar pins) and bare bit-member body labels (was
  bracketed `GATE[0]`).  **Flagged for shape-preservation review:**
  the emit lowers a vectorized DSL input into a hand-unrolled body
  shape.  See
  `~/projects/loop-state/audit-context/multi-channel-vectorization-discussion.md`.
- **`4bc7548` / `961a619`** (klicad-python) — A.11 live test for the
  multi-channel netlist round-trip; transitioned from
  `xfail(strict=True)` to 5/5 strict pass once R3.3 landed.
- **`a7a9efa`** (klicad-python) — A.12 PCB peer test scaffold.  One
  smoke test passes; the full Multi-Channel peer-area assertion is a
  strict-skip with two infra gaps tracked: (1) `kicad-cli pcb update
  --netlist` doesn't exist on this build, (2) the Multi-Channel
  placement tool opens a modal dialog that hangs IPC.

### Netlist-decoupling (Track C C.5 + C.6)

The schematic no longer has to be 1:1 with the netlist.  This makes
the multi-channel shape-preservation finding fixable (see above).

- **`73590277d4`** (KliCAD) — `klicad_native_ratsnest.set_spec`
  binding.  Parses a KiCad-sexpr netlist string via the upstream
  `SEXPR::PARSER`, looks up pin coordinates from the current
  schematic, chains edges per-net into the existing M1
  `SCH_RATSNEST_ITEM` via its existing `ClearEdges`/`AddEdge` API.
  Returns `{ok, edges_placed, nets_resolved, missing_pins}`.
- **`d634f68`** (klicad-python) — `to_schematic` calls `set_spec`
  after `save_schematic` so the ratsnest layer carries spec-derived
  edges.  Soft-fails on missing binding (`ModuleNotFoundError`),
  failed netlist export, or IPC trouble — `to_schematic`'s primary
  job is the schematic; ratsnest is the polish layer.  Splits
  `to_netlist` to expose `netlist_from_sch(sch_path)` so the hook
  doesn't re-enter `to_schematic`.

### Audits this session

- **Track B (ratsnest M1) integration audit** — confirmed M1
  (a567efe824..a58ab05139) is already fully on `loop/integration-7`;
  no cherry-pick needed; C.5–C.7 unblocked.
- **Skips audit** — 60 skips reviewed; all legitimate platform /
  availability gates or tracked infra gaps (A.12 GAP 1+2).  No stale
  skips, no expected_crash markers, no whole-file skips.
- **Vectorization shape-preservation discussion** — user opened the
  multi-channel artifact, identified that R5.6/R5.7 lower the
  vectorized DSL into a hand-unrolled body.  Discussion preserved in
  `~/projects/loop-state/audit-context/multi-channel-vectorization-discussion.md`.
  Caused two upstream changes:
  (a) auditor spec gained a mandatory shape-preservation check (in
      `~/projects/loop-state/loop-chunks.md` and
      `~/.claude/plans/shiny-sleeping-karp.md`);
  (b) the multi-channel spec at `docs/plans/multi-channel.md` now
      carries a banner pointing at the audit-context file.

### Vectorization auditor — in flight

A dispatch is going out (or just went out) to find three candidate
"correct" fixes at L4 (`connection_graph.cpp`) that let the matcher
accept a scalar-body-port → bus-parent-pin binding via the path's
slot index.  Once one of those lands, R5.6/R5.7 collapse to emit a
single body part with scalar hier-ports, the body's .kicad_sch
shrinks ~6×, and the DSL's `repeat=N` parameter stops being silently
expanded.

---

## What landed this session (in order)

### Track 1 — Hierarchy + buses (H1, H2, H3) ✅ COMPLETE

The canonical-circuit DSL gained Sub-Circuit support with SPICE
`.SUBCKT` + KiCad sub-sheets + bus syntax.

- **H1** (`7c7a0dc`): Sub-Circuit DSL + recursive `.SUBCKT` emission.
  `Circuit(ports=[...])` makes a Circuit instance-able;
  `Circuit.instance(ref, **port_map)` returns a `SubcircuitInstance`.
  Bus syntax expanded at port-map time; `expand_bus_range("DATA[7..0]")`
  canonicalizes to ascending; `validate_spice_name` boundary check on
  emit.  46 pure-python + PySpice integration tests.

- **H2** (`0fe0ae1` + KliCAD `038b8517db`): hierarchical schematic
  emission.  New C++ bindings `add_sheet` + `add_sheet_pin`;
  `sheet_path` scoping on `list_symbols` + `clear_routing`.  Python
  refactor: `to_schematic` walks root then each Sub-Circuit kind,
  push/pop into the child sheet to emit the body.  Multi-instance
  Sub-Circuits share one SCH_SCREEN (KiCad complex-hierarchy).  15
  live-KliCAD tests.

- **H3** (`22b1052` + KliCAD `10bcb95f75`): per-pin sheet diff +
  delete-by-kiid handles SCH_SHEET_PIN children.  Per-pin add/delete
  preserves SCH_SHEET kiid + user-positioned pins.  7 new tests.

- **H3 per-sheet spice-idempotence tests** (`a27328c`): 4 cases
  proving the diff/apply contract extends across sheet boundaries
  (value, model, Sim.Params changes on body parts; lib_id mismatch
  inside a Sub-Circuit body).

### Track 2 — Audit-driven fixes for H1-H3 ✅ COMPLETE

External audit (`task ac53edbfa349c338f`) found three hard problems +
drift items.  All addressed.

- `_route.py` and `_label_power_pins_only` gained `SUBCIRCUIT` branches
  (would have `KeyError`'d on `route=True` + hierarchy).
- Port-anchor hier-labels survive diff iterations via new
  `clear_routing(keep_hier_labels=True)` + per-name diff in
  `_emit_port_anchors`.  Previously: wiped + re-emitted at fixed
  coords every pass, contradicting the aesthetic-preservation
  contract.
- `V+` / `V-` added to power regex.
- `_add_sheet_pins` respects pin side from `list_sheet_pins`.
- `TestBusPortWidthChange` tightened: now asserts kiid preservation
  + exact pin-name set (was `len(pins) >= 1`, which passed regardless
  of whether the diff worked).
- 2 new C++ bindings: `list_labels` + `close_topmost_dialog`.
- 3 new tests: route+hierarchy no-crash; hier-label identity-diff
  position survival; stale anchor cleanup on port drop.

Audit deferred **H-3** (diff identity keyed on `(sheet_path, ref)`
tuples vs bare `ref` + sheet_path filter).  Works for flat H2
hierarchy; will need refactor when nested hierarchy (H4) lands.

### Track 3 — Documentation ✅ COMPLETE

- `docs/dsl-reference.md` (`d7f1d02`): neutral catalogue of every
  Part class, Circuit method, schematic / SPICE emission, and
  `klicad_native_*` binding.  Map of the territory, not a recipe.
  Design-loop prompt references it from the tooling section.

### Track 4 — Design-loop agent run 2 ✅ SUCCEEDED

Re-dispatched the design-loop agent against the cleaned-up surface.
Workspace wiped + archived (`~/projects/driver-board/8pin-archive-2026-05-26/`).
Agent completed end-to-end:

- Topology: discrete low-side N-FET per channel (MDD05N40A 40V/5A
  SOT-23) + Schottky catch (SS34-TD) + unidirectional TVS (SMF28A);
  one MCP23S17 SPI I/O expander handles all eight gates + Mode-B
  drives + Mode-C inputs.
- Per-channel: $0.032 active components at volume.  Per-module: $2.68
  (incl. expander, caps, fuse, connectors, PCB, assy).
- Used hierarchy on its own: "channel sub-circuit screen-shared by
  8 instances" — Sub-Circuit + multi-instance sharing worked.
- ERC dispatched ok; SPICE smoke sim 60,725 samples over 600 ms,
  clean convergence.
- 5 KLICAD_GAPS surfaced (see below).

### Track 5 — Modal-dialog handling ✅ COMPLETE (for now)

While the agent was running, modal dialogs (ERC report) blocked the
IPC.  Fixed:

- `klipy.KliCAD.list_modal_buttons()` and `dismiss_modals()`
  helpers.
- `klicad_native_gui.close_topmost_dialog()` C++ binding: last-
  resort `EndModal(wxID_CANCEL)` for dialogs whose Close button uses
  non-standard wx IDs (e.g., ERC dialog Close id 5101).
- `dismiss_modals` tries preferred-button click first, polls to
  verify dismissal, falls back to force-close on persistence.
- Memory: `feedback_klicad_modal_dialogs.md` notes the
  "test-hangs-check-for-modal-first" heuristic.

### Track 6 — Multi-channel feature plan + R0 audit 🟡 IN FLIGHT

User asked: can KiCad collapse N hierarchical-sheet instances into
one block on the parent (Altium's `REPEAT(SheetSymbol, 1, N)`)?
Research agent (`task a5f6a667029bbc6f4`) confirmed: **No native
KiCad feature**.  Tracked upstream as GitLab #1998, #13814 — both
open with no milestone.  Decision: add it to the fork.

Plan committed at `docs/plans/multi-channel.md`.  Audit agent
(`task a0100eda1ff369a82`) rejected the original "add `instance_index`
to SCH_SHEET_PATH" design as gratuitously invasive (~165 call sites,
breaks `KIID_PATH` round-tripping, mangles refdes).  Proposed
**Alternative D**: synthetic-clone complex hierarchy — `SCH_SHEET`
carries `m_repeat_count` + a `m_repeat_instances` KIID list; at
`BuildSheetList` time, materialize N synthetic SCH_SHEET children
sharing the on-canvas sheet's screen.  Downstream sees N normal
complex-hierarchy peers.  Only two pieces of genuinely new logic:
bus-pin bit fan-out + ERC marker dedup.

Plan revised to Alternative D (`eda6b72`).  R0 audit landed
(`ecbf1ef`): of 36 `path.Last()->*` call sites, 30 are trivial reads,
3 are tree-widget display reads, 1 is per-instance identity (the
case clones must differ on, handled naturally), and 4 are renames in
the hierarchy navigator that need `IsSynthetic()` guard +
template-forwarding.  R2 can proceed with field-shallow-copy
synthetic clones.

**Locked decisions**:
- Refdes: existing per-path annotation, no `_CH<N>` suffix.
- `SCH_SHEET_PATH` structure: unchanged.
- File format: mandatory version bump.
- Bus pin width: strict-equal-or-error.
- Scalar ports shared; bus syntax required for fan-out.
- Canvas: "×N" decoration, same bbox.
- Hierarchy navigator: N entries.
- Shrink: silent GC of orphan instance entries.

## What's next (paused mid-feature)

### R1 — SCH_SHEET data model + serialization ✅ COMPLETE (KliCAD `145c33722c`)

Landed:
- `SCH_SHEET::m_repeatCount`, `m_repeatInstances`, getters/setters.
- `SCH_SHEET::IsSynthetic()` / `GetTemplate()` / `MarkSynthetic()`
  — the R0 audit's prerequisites for R2's hierarchy-navigator
  rename guard.
- Copy ctor + swapData + operator== updated; synthetic state
  (m_isSynthetic, m_template) is deliberately NOT copied / swapped
  / compared — it's identity, not data.
- Sexpr emit: `(repeat_count N)` + `(repeat_instances (uuid ...))`
  in `saveSheet`, gated on N > 1 (default schematics are byte-
  identical to pre-R1).
- Sexpr parse: new `case T_repeat_count` + `case T_repeat_instances`
  arms in `parseSheet`.  Verified: the parser's `default` arm
  throws via `Expecting()` — unknown tokens are NOT silently
  dropped, which is why the file format version bump is necessary.
- New keywords `repeat_count`, `repeat_instances` in
  `schematic.keywords`.
- File format `SEXPR_SCHEMATIC_FILE_VERSION` bumped 20260326 →
  20260526 ("Sheet repeat instances (multi-channel)").
- `SCH_PAINTER::draw` paints "×N" in the sheet's top-right corner
  when repeat_count > 1; same bbox.
- `qa/tests/eeschema/test_sch_sheet.cpp` gained 6 R1 test cases.
  All 11 SchSheet cases pass.  Related suites (SchSheetList,
  SchSheetPath, Issue23403SharedSubsheetScreen, SaveasCopySubsheets,
  FlatHierarchy) continue to pass (15 cases).

### R2 — `BuildSheetList` synthetic expansion ⏳ NEXT

Where to pick up.  Touch:
- `eeschema/sch_sheet_path.cpp:984-1059` (`BuildSheetList`) — when
  pushing a child SCH_SHEET, check `GetRepeatCount()`.  If > 1,
  push `repeat_count` synthetic siblings instead of just the one,
  each carrying a m_Uuid from the parent's m_repeatInstances slot
  (slot 0 is the on-canvas sheet's own m_Uuid; slots 1..N-1 come
  from m_repeatInstances).
- `eeschema/schematic.h` / `.cpp`: own a `std::vector<
  std::unique_ptr<SCH_SHEET>>` clone cache; rebuild on
  `BuildSheetList`, invalidate on any repeat_count change or
  hierarchy mutation.
- Synthetic clones use `MarkSynthetic(template)` so the navigator
  rename guard (`hierarchy_pane.cpp:727/750/975-978` per R0)
  forwards writes to the template.
- `qa/eeschema/test_repeated_sheet_hierarchy.cpp` — schematic with
  one `repeat_count=8` sheet; Schematic().Hierarchy() returns 9
  entries (root + 8); each clone has distinct Last()->m_Uuid; all
  point to the same LastScreen().

Effort: one substantial session.

R0 audit (`docs/plans/multi-channel-r0.md`) confirms 30 trivial
read-sites are safe, 1 per-instance identity site (the m_Uuid
read in connection_graph.cpp:2951) Just Works for clones, and
the 4 rename writes need the `IsSynthetic()` guard R1 already
shipped.

### R3 onward (sketched)

- **R3** — Connection-graph bus-pin bit fan-out.  Algorithmically
  the heaviest phase.
- **R3.5** — ERC marker dedup by `(item_kiid, rule_id,
  instance_flag)`.
- **R4** — Netlist export verification (no new code expected; tests
  only).
- **R5** — klicad-python DSL: `sub.instance("U", repeat=N, ...)`.
- **R6** — Diff round-trip: collapse synthetic clones back to one
  `SubcircuitInstance` with `repeat_count > 1` on read.
- **R7** — PCB Multi-Channel verification (no code change expected;
  refactor the 8pin design and confirm tool sees 8 peer rule
  areas).

Total R1-R7: ~4-5 substantial sessions.

## Other open tracks (not in flight)

### `.history` modal upstream-correct fix (task #97)

The agent's run popped a "can't open file '.../.history/' (error 21:
Is a directory)" modal.  `.history/` is an **upstream KiCad feature**
(commit `8257870ac3`, Seth Hillbrand, Sep 2025) — autosave + restore
via a project-local git repo.

Some KliCAD file enumerator is reading the project dir + trying to
open `.history/` as a file, missing the skip-list.  The
`local_history.cpp` itself skips `.history` at multiple points
(lines 735, 1533), so the bug is somewhere else — possibly a project
sheet-list scan or a file-watcher init.

Action: reproduce in a minimal case, identify the offending
`wxFile::Open` call site, fix at the right level, file upstream.
Carry a minimal local patch if upstream merge takes time.

### KLICAD_GAPS the design-loop agent surfaced (run 2)

From `~/projects/driver-board/8pin/KLICAD_GAPS.md`:

1. **`NMOS`/`PMOS` ref-prefix bug**.  KiCad convention puts `Q` on
   FET refs, but `Part.spice_line()` emits the ref verbatim.
   `NMOS("Q1", ...)` produces `Q1 ... NMOS_S` which ngspice parses
   as a BJT (`Error: incorrect model type`).  Workaround: use `M1`.
   Fix: when `spice_letter != ref[0]`, prepend it.  ~10 lines in
   `_part.py`.
2. **`Circuit.to_schematic()` doesn't forward `mode=`**.  Module-
   level `to_schematic` accepts `mode='diff'|'replace'|'strict'`;
   the convenience method on `Circuit` doesn't pass it through.
   Doc/code drift.  One-line fix.
3. **ERC marker enumeration** (long-standing gap #4 from earlier
   audit).  `klicad_native_erc.get_markers()` would close the gap.
   Session-sized C++ work.
4. **`Circuit.run_tran()` false-positive log**.  Prints "Command
   'tran...' failed" to stderr even when it returns clean data.
   Suppress the log when post-run vector fetch succeeds.
5. **`STANDARD_MODEL_LIB` missing Schottky / Zener / TVS**.  Add
   `MBR340` (generic Schottky), `1N5240B` (10 V zener), parametric
   `TVSxxV` stubs.

Priority order for a quick-wins pass: 1 (real bug), 2 (one-liner), 4
(easy log fix), 5 (data file additions).  Save 3 for the multi-
channel work to land first (would touch the same eeschema code).

### Modal-dismissal pytest fixture (from memory note)

Wrap `klipy.KliCAD.dismiss_modals()` in a session-scoped pytest
fixture that polls between tests and click-dismisses unexpected
dialogs.  Quality-of-life; would have saved manual rescue during
the agent run.

### Nested hierarchy + bbox-aware sheet layout (from hierarchy plan H4 + H5)

H4 needs the `(sheet_path, ref)` tuple refactor the H3 audit deferred.
H5 replaces the H2 placeholder sheet sizing with bbox-aware layout.
Both are sketched in `docs/plans/hierarchy-and-buses.md`.  Neither
is blocking real designs (the design-loop agent worked at single-
level hierarchy).

## Decisions worth carrying forward

- **Hierarchy is the right call for repeated channels** in KiCad
  today — confirmed by research agent + agent run 2.  N SCH_SHEETs
  sharing one .kicad_sch is canonical.  Multi-channel feature
  refines the canvas-side view without changing the underlying
  model.
- **The agent's prior runs showed the design loop works end-to-end**
  when KLICAD_GAPS are kept short.  Each iteration of fixing gaps
  → re-dispatching → checking the new gaps is steady forward
  progress.
- **External audits before code start are worth their cost.**  The
  hierarchy audit changed the design materially (kept us from a
  ~165-call-site refactor).  The multi-channel audit changed it
  even more dramatically (rejected the entire `instance_index`
  approach).  Default to dispatching an Opus auditor on any plan
  >300 lines.
- **Plan docs in `docs/plans/`** are durable session-handoff
  artifacts.  Worth re-reading at session-start before code edits.

## Where to look first when resuming

1. Read this STATUS.md.
2. Read `docs/plans/multi-channel.md` (revised plan, READY TO
   START R0 — R0 now complete).
3. Read `docs/plans/multi-channel-r0.md` (call-site audit; lists
   the `IsSynthetic()` + `GetTemplate()` surfaces R1 must add).
4. `git log --oneline -10` in both repos to see what's freshest.
5. Start R1 (SCH_SHEET data model + serialization).

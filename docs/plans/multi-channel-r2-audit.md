# R2 audit — `BuildSheetList` synthetic-clone expansion

Audit of the uncommitted R2 changes in `/home/jakob/projects/KliCAD`
(diff against `145c33722c`).  Read-only audit; no code or build runs.

## 1. Verdict

**Yellow.**  The core mechanic — N synthetic siblings produced by
`BuildSheetList`, sharing the template's `SCH_SCREEN`, distinguished
by KIID — works as designed and matches what R0 said the codebase
will tolerate.  The new tests cover the right invariants.

But there is **one clear bug** (D) and **one downstream
correctness issue scoped to R3 but worth flagging now** (F') that you
will want resolved before R3:

- **Clone-cache accumulation across non-`RefreshHierarchy`
  `BuildSheetList` callers** (concern D, real bug).  Every call to
  `Schematic::BuildSheetListSortedByPageNumbers`,
  `BuildUnorderedSheetList`, or any direct
  `SCH_SHEET_LIST::BuildSheetList` invocation appends N-1 clones to
  `m_repeatClones` without clearing.  There are ~15 such call sites
  (ERC, plotter, dialog_sheet_properties, sch_edit_tool, etc.).
  Each ERC run, each plot, each `BuildSheetList` recompute leaks
  one full clone set into the schematic-lifetime cache.
- **Connection-graph parent-uuid match assumes pin parent == path's
  last sheet** (concern F').  At
  `connection_graph.cpp:2956` and `:453` the propagation logic
  compares `pin->GetParent()->m_Uuid` against the path's `Last()->m_Uuid`
  and pushes `pin->GetParent()` onto a path.  For a clone path,
  `pin->GetParent()` is always the template (canvas) sheet, so
  propagation through repeated sheets only reaches slot 0.  This is
  R3's job to fix, but R2 implicitly exposes the bug because R2 is
  the first phase where path.Last() can differ in pointer from
  pin->GetParent() for matching KIIDs.  Document it now so R3 has
  a clear acceptance test.

Other concerns A, B, C, E, G are addressed correctly or are
non-issues — details below.

## 2. Per-concern findings

### A. Synthetic clones in `SCH_SCREEN`

**Refcount inflation is correct and balances.**  The copy ctor at
`eeschema/sch_sheet.cpp:259-260` calls `m_screen->IncRefCount()` for
each clone; the destructor at `:268-274` calls `DecRefCount()`.  As
long as `ClearRepeatCloneCache` drops the unique_ptrs (which calls
`~SCH_SHEET` on each clone), refcount returns to the pre-walk
value.  Confirmed via `git diff schematic.cpp` lines 473-476 plus
`RefreshHierarchy` clearing at line 441.

**`Append()` is never called on clones.**  Audited
`eeschema/sch_screen.cpp:164,276`; the only `SCH_SCREEN::Append` paths
take items from canvas placement, paste, parser load, or library
load.  None pass synthetic clones — they are minted in
`MintRepeatClone` and stored only in `m_repeatClones`
(`eeschema/schematic.cpp:466-468`).  Clones are never inserted into
any items list.

**`GetClientSheetPaths()` is unaffected.**  Rebuilt by
`SCH_SCREENS::BuildClientSheetPathList`
(`eeschema/sch_screen.cpp:2370-2402`) walking `sch->Hierarchy()`.
With R2, that hierarchy contains N paths whose `LastScreen()` all
match the same template `SCH_SCREEN*`.  The loop at `:2393` matches
on pointer identity, so the template screen ends up with N entries
in its client-sheet list — which is exactly what other code expects
("this screen is shared between N paths").  This matches existing
complex-hierarchy semantics; consumers like
`sch_screen.cpp:1185` (alternate-reference detection) and
`fields_data_model.cpp:680` (shared-instance check) treat repeated
sheets the same way they treat hand-placed complex hierarchy.

**Watch-out flagged by audit but not a bug**: `screen->GetRefCount()
> 1` becomes true for repeated-sheet screens even when the user has
placed only one on-canvas copy.  Hits at
`eeschema/schematic.cpp:1561` (`IsComplexHierarchy`) and
`fields_data_model.cpp:680` (`isSharedInstance`).  Semantically
correct (repeated sheets ARE complex hierarchy under the hood), and
matches the plan's "downstream consumers see N peers" goal.  No
change needed.

### B. Clone fields point at the clone (not the template)

**No mutation paths reach the clone.**  Mutating field reads on
`path.Last()->GetField(...)` exist only in
`hierarchy_pane.cpp:744,980` (both now patched to forward via
`GetTemplate()`) — see the R0 audit + the R2 diff.  All other
`Last()->GetField` / `Last()->GetFields` hits are read-only
(`sch_plotter.cpp:142,216`, `sch_design_block_utils.cpp:102,199`,
`netlist_exporter_xml.cpp:377`).

**Symbol-instance lookups are KIID-keyed and work.**
`SCH_SYMBOL::GetInstance` at `sch_symbol.cpp:790-810` matches on
`KIID_PATH`; `SCH_SHEET_PATH::Path()` at `sch_sheet_path.cpp:438-461`
builds the KIID_PATH from each segment's `m_Uuid`.  Clones have
distinct UUIDs, so clones get distinct symbol-instance entries.
Confirmed by the test in `test_repeated_sheet_hierarchy.cpp::ClonesHaveDistinctKIIDs`.

**Text-variable resolution works.**
`SCH_SHEET::ResolveTextVar` at `sch_sheet.cpp:380-441` walks the
clone's own fields (which are copies of the template's — same values)
and resolves `Schematic()` via parent chain.  Clones inherit the
template's `m_parent` (the SCH_SCREEN) via `EDA_ITEM`'s copy ctor at
`common/eda_item.cpp:68`, so `clone->Schematic()` succeeds via
screen→schematic.

**Property-editor flows can't reach clones** because clones are not
in any `SCH_SCREEN::Items()` list and the canvas selection layer
only finds items that exist in a visible screen.  `DIALOG_SHEET_PROPERTIES`
gets its `SCH_SHEET*` from canvas selection (`sch_edit_tool.cpp:2789`,
`sch_drawing_tools.cpp:3440`) which can only return a template.

### C. Lifetime — `m_repeatClones` invalidation

**Single-rebuild path is correct.**
`SCHEMATIC::RefreshHierarchy` clears the cache before rebuilding
(`schematic.cpp:441`).  Each clone is owned by `m_repeatClones` until
the next refresh.  `SCH_SHEET_LIST` is value-copyable (vector of
paths); copies hold the same raw clone pointers, which remain valid
across the schematic's lifetime as long as no `RefreshHierarchy`
happens.  Matches the existing pattern that `Hierarchy()` copies go
stale on structural mutation.

**`BuildUnorderedSheetList` mints clones into the schematic cache.**
At `schematic.cpp:2210-2230` this is a *separate* path that does not
clear, but it calls back through `SCH_SHEET_LIST::BuildSheetList`
which calls `MintRepeatClone`.  The clones it mints persist in the
schematic's cache until the next `RefreshHierarchy` or
`ClearRepeatCloneCache`.  This is the seed of the leak in concern D
below.

**`sheet->Schematic()` nullity.**  The R2 code at
`sch_sheet_path.cpp:1077-1085` defensively checks for null and logs
a warning.  In practice `sheet` here is a child sheet found via
`m_currentSheetPath.LastScreen()->GetSheets()`.  That child was
`Append`ed to its parent screen via `SCH_SCREEN::Append`, which sets
`aItem->SetParent(this)` (`sch_screen.cpp:169`), so `child->Schematic()`
resolves via the screen's `Schematic()` (`sch_screen.cpp:109`) so
long as the parent screen has its SCHEMATIC parent installed.  This
is true for all top-level sheets created through standard paths;
the only failure mode is a sheet that has never been bound to a
schematic (e.g., a freshly-constructed `SCH_SHEET` not yet inserted
into a screen's items list).  Defensive check is appropriate; will
not fire in normal operation.

### D. Multiple `BuildSheetList` calls without `RefreshHierarchy` **(real bug)**

`MintRepeatClone` unconditionally appends to `m_repeatClones`
(`schematic.cpp:468`).  The cache is cleared only by
`RefreshHierarchy` (`:441`).  But `BuildSheetList` /
`BuildSheetListSortedByPageNumbers` / `BuildUnorderedSheetList` are
called from many sites outside `RefreshHierarchy`:

| Call site | What it does |
|---|---|
| `eeschema_helpers.cpp:134` | Schematic load: `BuildSheetListSortedByPageNumbers` |
| `erc/erc.h:58` | ERC runner ctor pulls `BuildSheetListSortedByPageNumbers` |
| `erc/erc_report.cpp:76` | ERC report (separate from runner) |
| `erc/erc_settings.cpp:351` | ERC settings sweep: `BuildUnorderedSheetList` |
| `tools/sch_editor_control.cpp:669` | `BuildSheetListSortedByPageNumbers` |
| `sheet.cpp:404` | "validity-check repaired list" via direct `BuildSheetList` |
| `dialogs/dialog_sheet_properties.cpp:364` | same |
| `tools/sch_edit_tool.cpp:2799` | edit-sheet validity check via direct `BuildSheetList` |
| `sch_plotter.cpp:101,330,506,691` | each plot type calls direct `BuildSheetList` |
| `netlist_exporters/netlist_exporter_spice.cpp:801` | `SCH_SHEET_LIST(currentSheet.Last())` ctor → direct `BuildSheetList` |

Each of these mints N-1 new clones into the schematic's cache
without freeing the old ones.  The old paths in `m_hierarchy` still
point at the *first* set of clones (still alive in the cache, since
nothing freed them); the new path returned to the caller points at
fresh clones.  Two practical consequences:

1. **Memory leak proportional to (# of BuildSheetList calls) × (N -
   1) clones per repeated sheet.**  Each clone is a full `SCH_SHEET`
   plus its pin objects.  For a `repeat_count=60` board with 10
   plot+ERC+netlist invocations per session, that's ~600 zombie
   clones per repeated sheet.  Not catastrophic for one session;
   accumulates over a long edit.
2. **Stale clone pointers in `m_hierarchy`.**  After the first
   external `BuildSheetList` call, `m_hierarchy` still references
   the original clones (good — they're not freed).  But subsequent
   external callers receive paths pointing at *different* clone
   objects representing the same logical slots.  Pointer-identity
   comparisons across hierarchy snapshots will diverge unexpectedly.
   For example, code that does
   `if( itemData->m_SheetPath.Last() == path.Last() )`
   (`hierarchy_pane.cpp:918`, the "highlight identical sheets" path)
   will fail to detect identity between a navigator-cached path and
   a freshly-built one — even though the user-visible identity
   (template + slot KIID) is the same.

**Fix options ordered by simplicity:**

- **(D1, recommended)** Dedup `MintRepeatClone` by
  `(aTemplate, aSlotKIID)`.  Store in a
  `std::unordered_map<std::pair<SCH_SHEET*, KIID>, std::unique_ptr<SCH_SHEET>>`
  keyed on those.  Hits return the existing clone pointer.
  `ClearRepeatCloneCache` still drops everything.  Cost: one map
  lookup per slot; benefit: pointer-stable clones across multiple
  `BuildSheetList` invocations within a single hierarchy state.
- **(D2)** Have `BuildSheetListSortedByPageNumbers` and
  `BuildUnorderedSheetList` clear the cache before walking, then
  rebuild.  Cheap to write but breaks `m_hierarchy` if either is
  called while the cached `m_hierarchy` is still in use.  Worse
  than D1 in practice.
- **(D3)** Hoist clone ownership out of the schematic and into
  `SCH_SHEET_LIST` itself (one cache per SCH_SHEET_LIST instance).
  Cleaner ownership story but doubles the memory cost per
  Hierarchy() copy and requires SCH_SHEET_LIST to become non-copyable
  or to deep-copy clones on copy — invasive.  Don't do this.

D1 is the right answer.

### E. Recursion check correctness

**Unfounded — hoisting is safe.**  `TestForRecursion`
(`sch_sheet_path.cpp:651-700`) compares only filenames.  Clones
share the template's `GetFileName()` (the field-shallow-copy ensures
this).  The recursion result is invariant across slots, so checking
once before slot expansion produces identical behavior to checking
per slot.  Also re-verified that the `aCheckIntegrity == false`
branch's `wxCHECK2_MSG ... continue` behavior is preserved: the
macro's `continue` argument is executed when the condition is false,
so identical-filename children continue (recursion prevention), and
others fall through to the recursive `BuildSheetList` call — same
shape as the pre-R2 code.

### F. PCB Multi-Channel tool downstream

**Yellow — clone KIIDs are distinct in `PathAsString` but identical in
`PathHumanReadable`.**

- `PathAsString` (KIID-based, used in netlist `tstamps` attribute at
  `netlist_exporter_xml.cpp:559`) is unique per slot — good.
- `PathHumanReadable` (`sch_sheet_path.cpp:464-507`) concatenates
  `GetField(SHEET_NAME)->GetShownText()` per segment.  Since all
  clones share the template's name field, the human-readable path
  for slots 0..N-1 is **identical** — they all read e.g. `/Channel/`.

This propagates to `board_netlist_updater.cpp:662-670`, which sets
`FOOTPRINT::Sheetname` from the netlist's `humanSheetPath` and
`Sheetfile` from the `Sheetfile` property.  Both are identical
across all clone-derived footprints.

The Multi-Channel tool at
`pcbnew/tools/multichannel_tool.cpp:461` keys uniqueness on
`(GetSheetname, GetSheetfile)` — both strings.  With R2 alone,
**all N channels collapse to one rule area**, not N peer areas.

This contradicts the plan's R7 claim ("synthetic clones get distinct
KIIDs → distinct human-readable paths → automatic peer detection";
`docs/plans/multi-channel.md:68-71`).  The premise that
PathHumanReadable produces distinct strings is incorrect — it uses
sheet *names*, not KIIDs.

**Fix options** (R3-or-R7 scope, not blocking R2):

- **F1** Append the slot KIID (short form) to the sheet name segment
  in `PathHumanReadable` when the segment's sheet is a clone of a
  repeat-N template.  E.g. `/Channel:01/` for slot 1.  Requires a
  flag on `SCH_SHEET_PATH` or sniffing `IsSynthetic()` on each
  segment.
- **F2** Append a slot index `[K]` derived from the segment's KIID
  position in the parent's `m_repeatInstances`.  Stable across
  saves (since `m_repeatInstances` is serialized in slot order).
- **F3** Have `Multi-Channel tool` key on
  `FOOTPRINT::GetPath()` (the full KIID path) instead of the
  human-readable strings.  Cleanest, but touches pcbnew.

The plan section "Bus pin width: strict-equal-or-error" suggests the
plan's authors already accept changing some of these strings —
prefer F1 or F2 with the slot index for human readability.

### F'. Connection-graph propagation through clones (R3 surface)

This is not in the original concern list but surfaced during the
audit.  At `connection_graph.cpp:2895` and `:453,2956`, the code
constructs sub-paths by appending `pin->GetParent()` to a path:

```cpp
SCH_SHEET_PATH path = aParent->m_sheet;
path.push_back( pin->GetParent() );
```

The `pin` is from `m_hier_pins`, populated at
`connection_graph.cpp:576` while iterating `screen->Items()` — these
are canvas SCH_SHEET_PINs whose `GetParent()` is always the
template (canvas SCH_SHEET).  For a clone path, this push_back
re-injects the template instead of the clone, producing a path that
ends in `[..., clone, template]` — semantically wrong.

The KIID match at `:2956` (`pin->GetParent()->m_Uuid !=
last_parent_uuid`) is then false for all clone-slot paths (slots
1..N-1), because the pin's parent UUID is slot 0's UUID.  Effect:
propagation from a parent subgraph only finds child subgraphs that
sit under slot 0.  Slots 1..N-1 are unreachable.

This needs to be fixed as part of R3's bus-pin fan-out anyway —
flagging here because R2 is the first phase where it's *possible* to
exhibit the bug.  Acceptance test for R3: drive a hier-pin on a
repeat=4 sheet and confirm subgraph propagation reaches all 4 child
contexts.

### G. Other write sites missed by R0

**None found beyond R0's hierarchy_pane sites.**  The mechanical
sweep over all 36→57 `Last()` references in `eeschema/` finds:

- 4 mutating writes, all in `hierarchy_pane.cpp` (R0 found these;
  R2 patches them).
- 1 sheet-pin parent assignment in `sch_io_altium.cpp:477` —
  parser-only, runs during load before any clones exist.
- 1 sheet-screen append in `cadstar_sch_archive_loader.cpp:2499` —
  parser-only.
- The rest are reads or pointer-identity comparisons.

`DIALOG_SHEET_PROPERTIES` (concern G prompt) gets its `m_sheet`
pointer from canvas selection — synthetic clones are never on the
canvas (not in any screen's `Items()`), so the dialog cannot
receive a clone pointer.  Same for drag/resize/move tools, sheet
field edits via the properties dialog, and variant edits — all
operate on canvas-selected items, which are always templates.

**One subtle G-flavored watch-out:** `hierarchy_pane.cpp:918`'s
"highlight identical sheets" path compares `Last() == Last()` for
pointer identity.  Clones get distinct pointers per slot, so two
navigator entries pointing at slots 1 and 2 of the same template
will not highlight each other on hover.  This is cosmetic; defer.

## 3. Other issues found

### O1. `findSelf` walks `CurrentSheet()` looking for `this`

`sch_sheet.cpp:1349-1366`'s `findSelf` walks
`Schematic()->CurrentSheet()` popping back until `Last() == this`.
If the user navigates into a clone via the hierarchy navigator (so
`CurrentSheet().Last() == clone`) and then code on the *template*
calls `template->findSelf()`, the loop pops everything and falls
through to `Schematic()->CurrentSheet()` + `push_back(template)`
— constructing a path that ends in the template even though the
current sheet ends in the clone.  Effects:

- The path returned has the wrong KIID at the last segment (slot 0
  instead of slot K).
- Used to look up `GetPageNumber()` and similar — for repeated
  sheets, page numbering is a hierarchy-level property assigned per
  path; the wrong path yields the wrong number.

Severity: low.  Triggers only when the user is "inside" a clone and
something queries the template's `findSelf`.  Punt to R3-R5.

### O2. Test fixture uses heap-allocated SCH_SHEET / SCH_SCREEN

`test_repeated_sheet_hierarchy.cpp:55-65` does
`m_top = new SCH_SHEET(...)` and `new SCH_SCREEN(...)`, then hands
ownership to the SCHEMATIC via `SetTopLevelSheets({ m_top })`.  This
is the standard pattern, but the destructor chain depends on the
SCHEMATIC owning the top sheets.  Worth a one-line note in the test
that the SCHEMATIC takes ownership; otherwise the test reads as a
leak.  Not a code issue, just clarity.

### O3. The `KIID` slot allocation in the fixture defaults to fresh KIIDs

`test_repeated_sheet_hierarchy.cpp:88` does `slots.emplace_back()` to
get a default-constructed KIID per slot.  These are random UUIDs.
Fine for the in-memory tests, but the R1 plan says
"`m_repeatInstances` holds the N-1 stable KIIDs ... stable across
save/load".  The save-load round-trip is tested in R1 (per the
commit message), not here.  Worth adding to R2 tests: shrink + grow
+ Save + reload + verify slot KIIDs are preserved.  Low priority;
not blocking.

### O4. `MintRepeatClone` mutates a `const SCHEMATIC*`

The method is declared `const` but appends to `m_repeatClones`,
which is `mutable`.  Standard pattern, called from a `const`
`BuildSheetList` context.  Audit: the const-ness is honest because
the cache is logically transient (no user-visible state changes).
But it does mean *any* `const SCHEMATIC&` reference can trigger
clone allocation — which is exactly the leak vector in concern D.
Coupled with D1 (dedup), this becomes harmless; without it, the
const interface is hiding a side-effect.

### O5. `wxLogWarning` on invariant violations runs in the build loop

`sch_sheet_path.cpp:1069,1081` emit `wxLogWarning` for both the
"too few slot KIIDs" and "no schematic" cases.  These run during
`BuildSheetList`, which on a malformed schematic could fire dozens
of times.  No throttling.  Won't crash; will spam the log.
Consider `wxLogTrace` with a unique trace mask, or fire once per
template via a static set.

### O6. Clone pin copies are dead memory

`SCH_SHEET::SCH_SHEET(const SCH_SHEET&)` deep-copies `m_pins` via
`new SCH_SHEET_PIN(*pin)` (`sch_sheet.cpp:250-254`).  For clones,
these pin objects are never read by any code path the audit found —
sheet-pin iteration always goes through `screen->Items()`, which
finds only the template's pins.  Each clone carries a redundant
copy of every pin.  For `repeat_count=60` with 10 sheet pins per
clone, that's 600 dead SCH_SHEET_PIN objects per template.  Not a
correctness issue; consider whether `MintRepeatClone` should
`m_pins.clear()` on the clone to reclaim the memory.

## 4. Recommended changes

In order of severity:

1. **Dedup `MintRepeatClone` by `(template, slot KIID)`** — concern
   D.  Replace the `std::vector<std::unique_ptr<SCH_SHEET>>` with
   an `std::unordered_map<std::pair<const SCH_SHEET*, KIID>, std::unique_ptr<SCH_SHEET>>`
   (or similar keyed container; KIID has a `std::hash`-compatible
   value).  `MintRepeatClone` checks the map first and returns the
   existing pointer.  Stable across all `BuildSheetList` invocations
   between `RefreshHierarchy` calls.  Fixes the leak and the
   pointer-identity discontinuity.

2. **Document the `PathHumanReadable` collision** — concern F.
   The plan's R7 phase claim that synthetic clones produce distinct
   human-readable paths is incorrect; downstream
   `FOOTPRINT::Sheetname` will be identical across slots.  Either
   (a) change `PathHumanReadable` to append a slot index for
   synthetic-clone segments, (b) teach `multichannel_tool.cpp` to
   key on KIID path, or (c) explicitly downgrade R7's PCB Multi-Channel
   verification to "expected to break, fix in R7+".  This is not a
   R2 code change but it invalidates an R7 assumption.

3. **Pre-flag F' for R3** — connection-graph propagation through
   `pin->GetParent()` won't reach clone slots.  Add an acceptance
   test to R3 that verifies subgraph propagation reaches all N
   slot contexts on a `repeat>1` sheet.

4. **(Optional, O5)** Demote the `wxLogWarning` invariant-violation
   spam to `wxLogTrace` with a fire-once flag.  Won't bite in normal
   operation; mostly hygiene.

5. **(Optional, O6)** Clear `m_pins` on synthetic clones in
   `MintRepeatClone` to reclaim memory.  Pins are unused on clones;
   the audit found no code path that reads them.

6. **(Optional, O3)** Add a save-load round-trip test in
   `test_repeated_sheet_hierarchy.cpp` (or the R1 suite) that
   verifies slot KIIDs are stable across reload.  Strictly an R1
   test gap, surfaced by R2's reliance on `m_repeatInstances`
   stability.

No R2 changes are needed beyond (1).  The current implementation is
sound for a single `RefreshHierarchy` cycle; (1) is what makes it
sound across the many `BuildSheetList` callers that exist outside
that single path.

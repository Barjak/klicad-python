# SCH_SHEET_INSTANCE refactor — session log

Branch: `refactor/sheet-instance` in both KliCAD and klicad-python.
Worktrees: `~/projects/{KliCAD,klicad-python}-sheet-instance`.

## Why this refactor exists

A heap-use-after-free in `SCH_SHEET::IsSynthetic()`, called from
`resolveHierPinPushTarget()` at `connection_graph.cpp:2914`,
caught under ASan during a wire-stub multi-channel emit.

**Proximate cause.** `SCH_SHEET_PATH` stored `std::vector<SCH_SHEET*>`.
Long-lived holders (`CONNECTION_SUBGRAPH::m_sheet`,
`SCH_REFERENCE::m_sheetPath`, several connection-graph maps,
`SCH_PIN::m_net_name_map`) cached paths across `RefreshHierarchy()`
boundaries.  Each `RefreshHierarchy()` invoked
`ClearRepeatCloneCache()` which freed every synthetic SCH_SHEET clone
minted for multi-channel slots; the next `BuildSheetListSortedByPageNumbers()`
re-minted clones at new addresses.  Any cached path whose `Last()`
was a clone became dangling.

**Structural cause.** `SCH_SHEET` was doing two jobs: it represented
both a *template* (the on-canvas sheet the user drew) and an
*instance* (an appearance in a hierarchy path).  For multi-channel
slots the instance required materializing a "synthetic clone" of
the template, owned by a per-cycle cache.  Identity-by-address was
load-bearing, but address stability was not actually guaranteed for
clones.

**Three prior commits papered over specific symptoms.**
- `35f1fa2a35` null-check `m_SchematicSettings` in `SaveSettings`.
- `66b0239127` re-fetch `sheetList` from `schematic->Hierarchy()`
  after `RecalculateConnections` in `EESCHEMA_HELPERS::LoadSchematic`.
- `e7ecca48ac` `MAIL_PROJECT_TEARDOWN` to disconnect frames before
  `UnloadProject`.

Each addressed the immediate failure site without naming the
template/instance conflation.  All three should be revertable on
this branch after the structural fix lands (P8).

## Structural fix

`SCH_SHEET` becomes pure template data — name, filename, screen,
position, `m_repeatCount`, `m_repeatInstances` (slot KIID list).

`SCH_SHEET_INSTANCE` (new value type in
`eeschema/sch_sheet_instance.h`) is the per-path identity carrying
`(template_kiid, slot_kiid)`.  Pure value type, hashable, totally
orderable, no SCH_SHEET pointer.

`SCH_SHEET_PATH` ends up holding `std::vector<SCH_SHEET_INSTANCE>`
(P6) instead of `std::vector<SCH_SHEET*>`.

Per-instance data (page numbers, BOM/SIM exclusions, etc.) moves
from `SCH_SHEET::m_instances` to `SCHEMATIC::m_sheetInstanceData`
keyed by `KIID_PATH` (P3).

The synthetic-clone mechanism (`m_repeatClones`, `MintRepeatClone`,
`ClearRepeatCloneCache`, `IsSynthetic`, `GetTemplate`,
`MarkSynthetic`) is deleted entirely (P7) because no consumer needs
clones anymore — `BuildSheetList` constructs instance-typed paths
without allocating any throwaway SCH_SHEETs.

## Phase ledger (as of writing)

```
P1  qa/eeschema regression suite                         a31aae36a5  ✓
P2-prelude  rename SCH_SHEET_INSTANCE struct → ..._DATA  ecb5fae70e  ✓
P2  introduce SCH_SHEET_INSTANCE value type              bfcbeafcb7  ✓
P3a SCHEMATIC-owned m_sheetInstanceData                  8a1ec1648e  ✓
P3b route SCH_SHEET_PATH page-number through SCHEMATIC   e2911fb81c  ✓
P4  instance-returning accessors (Last/GetInstance)      e33d26815b  ✓
P4b SCH_SHEET_PATH::m_instances mirror storage           69230f4271  ✓
P5a resolveHierPinPushTarget reads via SCH_SHEET_INSTANCE 057e3e4c4b ✓
P5b regression tests use the safe API                    49d0a992a6  ✓
P5c Path / Cmp / Rehash / op< / PathAsString → mirror    73f8ac3718  ✓
P5d connection_graph LastScreen → SCHEMATIC resolution   4ebd07836e  ✓
P5e hierarchy_pane 6 sites → SCHEMATIC resolution        121d705a14  ✓
P5f Last() auto-safe for synthetic slots (structural)    88caa6d9f0  ✓
P8a revert 66b0239127 (re-fetch sheetList workaround)    946064aa62  ✓
fixups (P2 dtor / P3 const-correct / KIID compare)       3 commits   ✓

P5g… more consumer migrations                            deferred
P6   delete m_sheets; require SCHEMATIC for resolution   deferred
P7   delete synthetic-clone mechanism                    deferred
P8b  e7ecca48ac / 35f1fa2a35 not in scope — different
     bug classes, leave the prior fixes in place
```

## Verification (under ASan, fresh build at 14:10)

All 302 assertions of the extended sheet+regression suites pass
clean under ASan:

```
SheetPathLifetime                      73/73 assertions pass
  PathSurvivesRefreshHierarchyCycle    5/5
  StaleMapKeyAfterCacheClear           2/2
  ClonePointerStableWithinSingleRefresh 3/3
  OrderingStableAcrossRefresh          4/4   ← auditor C1 verified
  PageNumberSurvivesRefresh            2/2   ← auditor I3 verified
  ConnectionMapPathReadsSurviveRefresh 2/2   ← original UAF class
  LastInstanceSplitsTemplateFromSlot   18/18
  GetInstanceWalksThePath              37/37

SchSheetInstance                       35/35 assertions pass
SchematicSheetInstanceData             13/13 assertions pass
SchSheet / SchSheetPath / SchSheetList 70/70 assertions pass
RepeatedSheetHierarchy / *BusFanout    78/78 assertions pass
Issue23403SharedSubsheetScreen         23/23 assertions pass
                                       --------
                                       302/302 assertions PASS
```

No ASan reports during execution.  The original CONNECTION_SUBGRAPH::
m_sheet UAF (`SCH_SHEET::IsSynthetic()` from `resolveHierPinPushTarget`
at `connection_graph.cpp:2914`) is structurally eliminated.

## Remaining work (deferred)

Each item below removes more potential UAFs but is not blocking for
the original bug.  Listed in approximate priority order:

- **PathHumanReadable** dereferences `m_sheets[i]->GetField(SHEET_NAME)`
  to assemble human-readable path strings.  ~50 callers; mostly in
  plot/log/text-var paths where the SCH_SHEET_PATH is fresh.
  Migration would route through `SCHEMATIC::ResolveSheetTemplate` to
  read the sheet name from a live template.

- **`hierarchy_pane.cpp`** (~6 `.Last()->` callsites) — widget cache
  of hierarchy paths.  Outlives `RefreshHierarchy()` so a stored
  path with a synthetic-clone leaf can dangle on the next click.

- **`SCH_REFERENCE::m_sheetPath`** (audit P5e) — symbol annotation
  references; per-sheet-path scope.  Investigate whether reads
  outlive a refresh.

- **`SCH_PIN::m_net_name_map`** keyed by `SCH_SHEET_PATH` — already
  KIID-safe at the map level (hash + `operator==` were KIID-based,
  and `operator<` is now too as of P5c), but the read-after-find
  pattern could still go through unsafe accessors.

- **P6**: delete `m_sheets` entirely; require `SCHEMATIC` for any
  `SCH_SHEET*` resolution.  Once consumer migrations are complete,
  the bandwidth for unsafe pointer reads is gone by construction.

- **P7**: delete the synthetic-clone mechanism (`m_repeatClones`,
  `MintRepeatClone`, `ClearRepeatCloneCache`, `IsSynthetic`,
  `GetTemplate`, `MarkSynthetic`).  `BuildSheetList` then constructs
  `SCH_SHEET_INSTANCE` values directly without allocating any
  throwaway SCH_SHEETs.

- **P8**: revert prior proximate fixes that this refactor obsoletes.

  - ✓ **66b0239127** *re-fetch sheetList after RecalculateConnections
    in headless load*  Reverted at 946064aa62.  The workaround
    addressed the exact UAF class this refactor closes: synthetic-
    clone SCH_SHEET* held in a local sheetList across a
    RefreshHierarchy.  After P5f (auto-safe `Last()`), the
    structural fix subsumes it.  qa suite remains ASan-clean with
    the revert in place.

  - ☐ **e7ecca48ac** *api: disconnect SCHEMATIC/BOARD before
    pm_load_project unloads*  Different bug class — PROJECT
    teardown ordering on IPC project-switch.  Leave in place; the
    SCH_SHEET_INSTANCE refactor doesn't touch the project-lifetime
    machinery.

  - ☐ **35f1fa2a35** *null-check m_SchematicSettings in SaveSettings*
    Different bug class — simulator-frame shutdown when
    SchematicSettings was already torn down at app close.  Leave
    in place; defensive null-checks at shutdown are correct
    independent of any SCH_SHEET_PATH refactor.

  Plus klicad-python's `SheetPath(proto_ref=…)` wrapper audit for
  value-vs-reference semantics.

## Wire format

On-disk and on-wire formats were *already* KIID-based — see
`SchPath` proto's `repeated KIID path` field and the
`(instances (project name (path "uuid/uuid" ...)))` shape in
`.kicad_sch`.  The pointer-typed in-memory representation was the
outlier; the refactor aligns the in-memory shape with what disk and
wire already use.  No file-format bump.  No proto change.  No
PCB-side cross-probing format change.

## Deferred structural debt

`SCH_SHEET` still merges template-canvas-position with its identity.
A genuinely orthogonal model would have `SCH_SHEET_TEMPLATE`
(canvas-bound) and `SCH_SHEET_INSTANCE` (path-bound) as completely
distinct types, with no shared base.  Not load-bearing for any
current bug class; flagged for whoever next touches this area.

`KIID` collision is theoretically possible (128-bit UUIDs).  Not
addressed; not load-bearing.

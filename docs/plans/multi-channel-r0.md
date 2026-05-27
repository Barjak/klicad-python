# R0 — `SCH_SHEET_PATH.Last()` consumer audit

Verdict for the multi-channel synthetic-clone approach: **only the
hierarchy navigator's rename code needs special handling**.  All
other `path.Last()->*` calls are safe because synthetic clones are
field-shallow-copies of the template SCH_SHEET (only `m_Uuid` differs).

36 call sites total across `~/projects/KliCAD/eeschema/`.

## Read-only — trivial (30 sites)

These read attributes that are identical across template and clones
because synthetic clones copy them all except `m_Uuid`.  No code
change required.

| Method | Sites | Notes |
|---|---|---|
| `GetName()` | 13 | Returns the on-canvas sheet's name; clones inherit |
| `GetContextualTextVars()` | 5 | Reads field-derived text vars; clones inherit |
| `GetField(FIELD_T)` | 4 | Field array is shallow-copied at clone time |
| `GetFileName()` | 3 | Clones share the same file (point at template's `SCH_SCREEN`) |
| `GetFields()` | 3 | Same as `GetField` |
| `GetShownText()` | 2 | Field-derived; clones inherit |
| `ResolveTextVar()` | 1 | Field-derived |
| `GetScreen()` | 1 | Clones share `m_screen` with template (key property of the design) |
| `IsTopLevelSheet()` | 2 | Clones inherit (depth-based; same as template) |
| `IsVirtualRootSheet()` | 1 | Inherited |

Specific files: `sch_sheet.cpp`, `schematic.cpp`, `sch_edit_frame.cpp`,
`sch_sheet_path.cpp`, `sch_plotter.cpp` (×6), `files-io.cpp`,
`connection_graph.cpp` (×2), `sch_design_block_utils.cpp` (×3),
`sch_field.cpp`, `dialog_text_properties.cpp` (×2),
`sch_editor_control.cpp` (×2), `dialog_table_properties.cpp`,
`netlist_exporter_xml.cpp`, `dialog_tablecell_properties.cpp`.

## Per-instance identity (1 site)

| File:Line | Code | Verdict |
|---|---|---|
| `connection_graph.cpp:2951` | `const KIID& last_parent_uuid = aParent->m_sheet.Last()->m_Uuid;` | **Per-instance** — this is *exactly* the case clones must differ on.  The connection-graph code uses the last segment's KIID to distinguish path subgraphs.  Synthetic clones carry distinct `m_Uuid`s from the parent's `m_repeat_instances`, so this Just Works. |

## Tree-widget reads via `SetItemText` (3 sites)

| File:Line | Code | Verdict |
|---|---|---|
| `hierarchy_pane.cpp:701` | `m_tree->SetItemText( aItem, itemData->m_SheetPath.Last()->GetName() );` | **Trivial** — `SetItemText` operates on the wxTreeCtrl; the argument is a `GetName()` read.  Clones return the same name; navigator entry for clone reads "channel" (the template name) — exactly what we want. |
| `hierarchy_pane.cpp:765` | Same shape | Trivial |
| `hierarchy_pane.cpp:985` | Same shape | Trivial |

The hierarchy navigator showing N entries with the same name "channel"
is fine — the per-path numbering distinguishes them in the netlist
and on the PCB.  If we later want per-clone display names
("channel (×N: 3 of 8)" or similar), that's a render-time concern,
not data-model.

## Writes — needs UI guard (4 sites)

The two rename code paths in the hierarchy navigator mutate the
sheet via `SetName` + `Modify` for undo.  Both are user-initiated
edits triggered by renaming an item in the tree.

| File:Line | Code |
|---|---|
| `hierarchy_pane.cpp:727` | `if( data->m_SheetPath.Last()->GetName() != newName )` (read) |
| `hierarchy_pane.cpp:747` | `commit.Modify( data->m_SheetPath.Last()->GetField( FIELD_T::SHEET_NAME ), ... )` (undo) |
| `hierarchy_pane.cpp:750` | `data->m_SheetPath.Last()->SetName( newName );` (write) |
| `hierarchy_pane.cpp:975-978` | Same shape — second code path for a different rename trigger |

### Verdict — synthetic clones are not directly renameable

Two reasonable behaviors when the user double-clicks-and-renames a
synthetic-clone entry in the navigator:

**Behavior A — Reject** (silently or with a tooltip): "Cannot rename
an instance of a repeated sheet directly; rename the template sheet
instead."

**Behavior B — Forward to template**: silently apply the rename to
the on-canvas template, which renames all clones simultaneously.
Consistent semantics, but a user trying to rename "just channel 3"
gets surprised when all 8 channels change.

**Recommended**: **Behavior B with a notification**.  Detect the
synthetic case via the new `SCH_SHEET::IsSynthetic()` predicate
(introduced in R1).  Forward to the template SCH_SHEET pointer
(stored on the synthetic clone or derivable from the parent's
`m_repeat_instances` lookup).  Notify the user via a transient
status-bar message: "Renamed template sheet (affects all N
instances)."

Implementation: add a one-line guard in both `SetName` paths:

```cpp
SCH_SHEET* target = data->m_SheetPath.Last();
if( target->IsSynthetic() )
    target = target->GetTemplate();   // returns the on-canvas SCH_SHEET
target->SetName( newName );
```

`IsSynthetic()` returns true on clones; `GetTemplate()` returns the
real on-canvas SCH_SHEET (returns `this` for non-synthetic).  Both
land in R1 alongside `m_repeat_count`.

## Summary

| Category | Count | Action |
|---|---|---|
| Trivial reads (identical for clone vs template) | 30 | None |
| Per-instance identity (m_Uuid) | 1 | None (clones already differ here) |
| Tree-widget reads (text-only display) | 3 | None |
| Renames in hierarchy navigator | 2 paths × 2 calls = 4 | Add `IsSynthetic()` guard, forward to template |

**R1 prerequisites surfaced by R0**:
- `SCH_SHEET::IsSynthetic() const` predicate (defaults to `false`;
  set `true` on cache-owned synthetic clones)
- `SCH_SHEET::GetTemplate() const` accessor (returns `this` by
  default; clones return their template pointer)

Both are additive to `SCH_SHEET` and don't perturb any other call
site.  The synthetic-clone construction in R2 sets both fields when
materializing clones.

## What R0 did NOT find

- No place that depends on `path.Last()` being a *unique* pointer
  (some patterns store `Last()` in a map by pointer-identity, which
  would have been a problem — there are zero of these).
- No place that uses `Last()` for `operator==` against other paths
  (paths compare by their KIID chain, not by pointer-identity at
  the end).
- No place outside the hierarchy navigator that calls `SetName`,
  `Modify`, or anything that would write through `Last()`.

R2 can proceed with confidence that synthetic clones (field-shallow-
copies of the template, distinct `m_Uuid` only) are safe to insert
into any code path that walks the hierarchy.

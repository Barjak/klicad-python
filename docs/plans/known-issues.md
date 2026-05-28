# Known issues

## SCHEMATIC::SetProject dangling-PROJECT crash on API-driven project switch

**Symptom**: SIGSEGV in `SCHEMATIC::SetProject(PROJECT*) + 0x37` when:
1. KliCAD launches with `open_projects` (kicad.json) auto-restoring a prior project
2. The auto-restored project also auto-opens its eeschema (creating SCHEMATIC with `m_project = projA`)
3. API caller invokes `klicad_native_project_manager.load_project(projB)`
4. SETTINGS_MANAGER unloads `projA`, freeing the PROJECT object
5. Subsequent code path calls `Schematic().SetProject(...)`, dereferences the now-freed `m_project` vptr → segfault at the `m_project->GetProjectFile()` virtual call inside the `if (m_project)` guard

**Root cause**: `pm_load_project` in `common/api/bindings_project_manager.cpp` calls
`SETTINGS_MANAGER::LoadProject` directly. The clean wx path
(`SCH_EDIT_FRAME::OpenProjectFiles` etc., `eeschema/files-io.cpp:199,922`)
calls `Schematic().SetProject(nullptr)` BEFORE `UnloadProject` — the API
path bypasses this disconnect.

**Workarounds**:
- Clear `open_projects` in `~/.config/kicad/10.99/kicad.json` before
  launching for tests (current `conftest.py` does not do this; should
  either add it or document the requirement).
- Or: close any open editor frames before calling `load_project`.

**Proper fix** (sketches):
- Option A (binding-level): `pm_load_project` calls
  `Schematic().SetProject(nullptr)` / `Board().SetProject(nullptr)`
  (and the analog board call) before invoking `mgr.LoadProject`. Mirrors
  the wx-clean path. Smallest blast radius.
- Option B (architectural): SETTINGS_MANAGER maintains a PROJECT-observer
  list; broadcasts `OnProjectUnload(PROJECT*)` so SCHEMATIC, BOARD, etc.
  null out their pointers. Correct but bigger refactor.

Status: blocked on the main feature work; not user-visible in the
standard "launch fresh from manager" flow.

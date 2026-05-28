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

## Status: fixed

Fixed 2026-05-27 on KliCAD `loop/integration-7` in commit
`e7ecca48acd790998956e94daabf44bf364a22ad` via Option A
(binding-level disconnect).  `pm_load_project` now sends a new
`MAIL_PROJECT_TEARDOWN` KIWAY mail to any live `FRAME_SCH` /
`FRAME_PCB_EDITOR` before calling `SETTINGS_MANAGER::UnloadProject`,
mirroring the wx file-open flow at `eeschema/files-io.cpp:199` and
`pcbnew/files.cpp:602`.  Receivers null out `SCHEMATIC::m_project` /
`BOARD::m_project` so the subsequent `LoadProject` can't dereference
a freed PROJECT.

Mail routing goes through `KIWAY::ExpressMail`, which uses
`Player(..., doCreate=false)` internally --- so the disconnect is a
no-op when no editor frame is up (headless `kicad-cli api-server`
case), and we never accidentally instantiate an editor frame just to
tell it to release a pointer.

Files touched on the KliCAD side:

- `include/mail_type.h` --- new `MAIL_PROJECT_TEARDOWN` enumerator
- `common/api/bindings_project_manager.cpp` --- `pm_load_project`
  sends the mail and calls `mgr.UnloadProject(&mgr.Prj(), false)`
  before `mgr.LoadProject(...)`
- `eeschema/cross-probing.cpp` --- `SCH_EDIT_FRAME::KiwayMailIn`
  handles `MAIL_PROJECT_TEARDOWN` via `m_schematic->SetProject(nullptr)`
- `pcbnew/cross-probing.cpp` --- `PCB_EDIT_FRAME::KiwayMailIn` handles
  `MAIL_PROJECT_TEARDOWN` via `GetBoard()->ClearProject()`

Workarounds listed above are no longer required; `conftest.py` does
not need to scrub `open_projects` from kicad.json before launch.

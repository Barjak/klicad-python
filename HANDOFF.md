# klicad-python handoff — 2026-05-17

This repo is the Python side of KliCAD: forked `kipy` library + a `kipy.klicad`
convenience façade + the pytest suite that drives KiCad over IPC.

The main handoff doc lives in the C++ fork at
`Barjak/KliCAD:HANDOFF.md`. Read that first — it covers project shape,
architecture, open crashes, and bug priority. This doc is the
Python-side specifics.

## What's here

```
kipy/
  klicad/                 # NEW: convenience façade (~125 wrapped methods, 13 proxies)
    _facade.py            # KliCAD class — composes the sub-proxies
    _runner.py            # _PyRunner + KliCADError
    drc.py erc.py ...     # one file per subsystem
  kicad.py                # patched: adds KiCad.run_python(code)
  proto/                  # regenerated from patched .proto files (protoc 29 from Homebrew)
tests/
  conftest.py             # KiCad fixture, switch_project_copy, _crash_baseline
  fixtures/
    switch_project/       # bundled test project (was hardcoded to a Mac path)
    minimal.kicad_jobset
  test_klicad_safety.py   # 93 tests: per-binding import + Pattern B lifecycle
  test_klicad_bindings.py # 42 tests: per-binding positive cases
  test_gui_smoke.py       # 18 tests: visual GUI walkthrough w/ screenshots
  test_geometry.py        # 19 tests: kipy geometry (upstream-ish)
tools/
  generate_protos.py
```

## How the façade works

`KliCAD()` instantiates a `kipy.KiCad` connection (default
`/tmp/kicad/api.sock`, 300s timeout). Each proxy (`.drc`, `.board`, `.sim`,
etc.) is a thin wrapper that builds a Python snippet, calls
`run_python(snippet)`, and either:

- parses `result_repr` via `ast.literal_eval` and returns it, or
- raises `KliCADError(traceback)` if `not r.ok`.

Pattern: never expose raw `run_python` to users. Wrap idiomatically.

Look at `kipy/klicad/drc.py` for the simplest example. `board.py` is the
busiest (20 methods). `library.py` has nested proxies
(`k.library.symbols.*`, `k.library.footprints.*`).

## Test conventions

- **Session-scoped `kicad` fixture** (in conftest.py) skips the whole run
  if `/tmp/kicad/api.sock` isn't reachable. So tests require a running
  KliCAD.
- **`_crash_baseline`** snapshots `~/Library/Logs/DiagnosticReports/` (macOS
  crash dir) at session start; fails the suite if new crashes appear.
  On Linux you'll want to point this at `/var/lib/apport/coredump/` or
  whatever's appropriate, OR just no-op it for portability.
- `auto_dismiss_dialogs` fixture (function-scoped) calls
  `kicad_native_gui.dismiss_dialogs()` after each test that uses it.
- `loaded_switch_project` (session-scoped, in `test_gui_smoke.py`) loads
  the bundled switch project once. Tests that need a real board+schematic
  depend on this fixture.

Last full run before the schematic-switch crash hit: **172 passed, 0
xfailed.**

## Recent commits worth knowing

- `4e63eff` — bundled `tests/fixtures/switch_project/` so the suite is no
  longer pinned to a Mac filesystem path. `.gitignore` inside excludes the
  runtime cruft KiCad sprays into any opened project (`~*.lck`,
  `.history/`, `*.kicad_prl`, `*-backups/`).
- `a199b5c` — `tests/test_gui_smoke.py`: 18-test visual GUI walkthrough
  consolidating the ad-hoc smoke heredocs the previous dev was running
  by hand. Pytest-driven, writes a 3D-viewer snapshot to
  `$TMPDIR/klicad-gui-smoke/3d_viewer.png`.
- `a552379` — dropped 2 xfail markers (pcb_upgrade save-rename) that
  turned green once `pcb_state.open_board` started giving the editor a
  real filename.
- `2df7a84` — wave-5 coverage in test_klicad_safety.py (10 new modules:
  bitmap2component, diff, hierarchy, sim_advanced, 3d_viewer, netinfo,
  sync, cvpcb, design_blocks, local_history).
- `79d1172` — `kipy.klicad` façade (the 13-proxy convenience layer).
- `3560e50` — `KiCad.run_python` wrapper + initial pytest safety net.

`git log --oneline` to see the rest.

## Linux notes

- `origin` now points at GitHub (`Barjak/klicad-python`). `upstream` is
  gitlab. **Don't push to upstream.**
- `setup.py` exists but `pip install -e .` fails on the upstream protoc
  regen step (mypy plugin issue). Don't try; just use `PYTHONPATH`:
  ```
  PYTHONPATH=/path/to/klicad-python python3 your_script.py
  ```
- Python 3.13 is what the previous dev used (matches the embedded
  interpreter on the C++ side). 3.12 should work too.
- `protoc` regen requires protobuf 29 (from Homebrew on Mac); on Linux
  pin protobuf 29.x and the matching Python `protobuf~=5.x` runtime.
  Newer protoc (33+) emits gencode the Python runtime rejects.

## Push convention

Push to GitHub after every commit. See parent HANDOFF.md.

## Where to look first

If the C++ side compiles and KiCad runs but tests are weird, check:

1. `tests/conftest.py::kicad` fixture — timeout is 300s, socket path is
   `/tmp/kicad/api.sock`. If your KiCad uses a different socket, point it
   there.
2. `tests/conftest.py::_crash_baseline` — references a macOS-specific
   crash dir. Make this no-op or point at the right Linux dir.
3. Pin types: `kipy/proto/` is regenerated from the patched .proto files
   on the C++ side. If you change a .proto, regen via
   `tools/generate_protos.py` (and check the version trailer matches the
   runtime).

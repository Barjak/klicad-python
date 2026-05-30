"""Source-frame capture for spec-pane bidirectional sync.

Every Part, Circuit, label, wire, etc. that the klicad-python pipeline
emits into a schematic carries a `(file, line)` reference back to the
user's spec line.  KliCAD's eeschema spec pane (see
~/projects/klicad-python/docs/plans/spec-pane.md, and the C++
SCH_SPEC_PANE) reads the `Klicad.SpecSrc` field on each item to wire
hover/selection to caret position.

This module hosts the frame-walker that the various `__post_init__` /
emission helpers call.  Capture happens AT CONSTRUCTION TIME, not at
emission time, so a Part assembled inside a Sub-Circuit factory still
points at the *user's* call site, not at the factory function.

`Klicad.SpecSrc` storage format on the C++ side is "<path>:<lineno>" —
absolute when the project root isn't yet known, project-relative when
serialised through `to_relative_src()`.
"""
from __future__ import annotations

import inspect
import os
from pathlib import Path
from typing import Optional


# Cached so we don't keep re-resolving — klipy's install path is fixed for the
# life of a process.
_KLIPY_ROOT: Optional[str] = None


def _klipy_root() -> str:
    """Absolute path of the klipy package (`klipy/`), trailing slash.

    Any frame whose `__file__` starts with this prefix is a klipy internal
    and gets skipped by the user-frame walker.
    """
    global _KLIPY_ROOT
    if _KLIPY_ROOT is None:
        # klipy/circuit/_srcref.py → up to klipy/.
        _KLIPY_ROOT = str(Path(__file__).resolve().parent.parent) + os.sep
    return _KLIPY_ROOT


def capture_user_frame(extra_skip: int = 0) -> Optional[tuple[str, int]]:
    """Walk the call stack until we leave klipy and return (abs_path, lineno).

    extra_skip lets a caller hop additional frames when it's wrapped behind
    helper functions or factory layers.  Default is 0; the walker still
    skips this function's own frame plus everything inside `klipy/`.

    Returns None when the user frame is inside the REPL (`<stdin>`,
    `<string>`, etc.) or when the walker exhausts the stack without
    finding a real file — in either case there's no source line to
    point at.
    """
    klipy = _klipy_root()
    stack = inspect.stack()
    # Always skip this function's own frame (index 0).
    for frame_info in stack[1 + extra_skip:]:
        filename = frame_info.filename
        # REPL / exec'd strings / <frozen importlib> have no real path.
        if not filename or filename.startswith("<"):
            continue
        # klipy-internal frame — keep walking.
        if filename.startswith(klipy):
            continue
        # Resolve to an absolute path so the C++ side can open it without
        # caring about the user's cwd at construction time.
        try:
            abs_path = str(Path(filename).resolve())
        except OSError:
            # Path resolution failed (e.g. file was deleted between
            # interpretation and capture).  Fall back to the raw name.
            abs_path = filename
        return (abs_path, frame_info.lineno)
    return None


def to_relative_src(src: Optional[tuple[str, int]],
                    project_dir: Optional[Path]) -> Optional[str]:
    """Format `_src` for the `Klicad.SpecSrc` field.

    When project_dir is known, the spec path is rewritten relative to it
    using os.path.relpath (which CAN produce a `../`-prefixed path; the
    common repo layout is `<repo>/spec.py` + `<repo>/kicad/foo.kicad_pro`,
    so the spec sits one level above the project dir).  Falls back to
    the absolute path when relpath raises (different filesystem roots,
    typically Windows drives).

    Returns None when `src` is None, signaling "don't emit the field".
    """
    if src is None:
        return None
    path, lineno = src
    if project_dir is not None:
        try:
            rel = os.path.relpath(
                str(Path(path).resolve()),
                str(project_dir.resolve()),
            )
            return f"{rel}:{lineno}"
        except (ValueError, OSError):
            # Different drive roots on Windows; resolution failures.
            pass
    return f"{path}:{lineno}"

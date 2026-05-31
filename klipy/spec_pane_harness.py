"""Spec-pane harness: load a user spec module and emit its top-level
circuit to the schematic the eeschema spec pane is editing.

Invoked by `eeschema/widgets/sch_spec_pane.cpp` as

    python3 -m klipy.spec_pane_harness <spec.py>

with env vars:

    KLICAD_SCH_PATH         absolute path to the open .kicad_sch
    KLICAD_API_SOCKET       (optional) IPC socket; default lookup if unset
    KLICAD_DISCARD_UNSAVED  (set by spec pane) bypass save-changes modal

The spec module is expected to define one of:

    * a top-level ``circuit`` (or ``top``) variable holding a Circuit
    * a callable ``board`` (or ``main``) returning a Circuit
    * a ``@v01.root``-decorated function (handled by its own side effect)

This file IS the C++-side application code that previously lived in
each spec's ``@root`` decorator: project-path discovery, IPC connect,
sheet navigation, emit.  Specs become pure declarations.
"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


def _find_circuit(ns: dict):
    from klipy.circuit._circuit import Circuit
    for name in ('circuit', 'top'):
        v = ns.get(name)
        if isinstance(v, Circuit):
            return v
    for name in ('board', 'main', 'build'):
        f = ns.get(name)
        if callable(f):
            r = f()
            if isinstance(r, Circuit):
                return r
    return None


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print('usage: python3 -m klipy.spec_pane_harness <spec.py>',
              file=sys.stderr)
        return 2

    spec_path = Path(argv[1]).resolve()
    if not spec_path.is_file():
        print(f'spec_pane_harness: no such file: {spec_path}', file=sys.stderr)
        return 2

    sch_env = os.environ.get('KLICAD_SCH_PATH', '').strip()
    if not sch_env:
        print('spec_pane_harness: KLICAD_SCH_PATH not set (eeschema must '
              'launch this harness with the active schematic path)',
              file=sys.stderr)
        return 2
    sch_path = Path(sch_env)

    # Run the spec.  If it has its own @v01.root, that fires on the
    # __name__ == '__main__' check and emits directly — we then exit
    # without doing a second emit.  Otherwise we pick up a top-level
    # `circuit` / `top` / `board()` and emit it ourselves.
    sys.path.insert(0, str(spec_path.parent))
    ns = runpy.run_path(str(spec_path), run_name='__main__')

    cir = _find_circuit(ns)
    if cir is None:
        # @root path already emitted; nothing more to do.
        return 0

    from klipy import KliCAD
    k = KliCAD(timeout_ms=300_000)
    if not k.is_alive():
        print('spec_pane_harness: KliCAD IPC not reachable', file=sys.stderr)
        return 1

    k.run_python(
        'import klicad_native_hierarchy as h\n'
        'while True:\n'
        '    s = h.list_sheets()\n'
        '    if not s or s[0].get("depth", 0) == 0: break\n'
        '    h.pop_sheet()\n'
    )

    cir.to_schematic(sch_path, kicad=k, mode='replace')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))

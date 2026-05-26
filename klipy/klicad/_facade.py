"""Top-level :class:`KliCAD` façade — composes every sub-proxy.

Owns the underlying :class:`klipy.klicad.KliCAD` connection (which it
either receives or constructs with a 300 s timeout against the default
``/tmp/klicad/api.sock``).  Each sub-proxy is wired with a shared
:class:`._runner._PyRunner` so they all dispatch through the same
``run_python`` channel.
"""

from __future__ import annotations

from typing import Optional, TYPE_CHECKING

from ._runner import _PyRunner
from .board import Board
from .drc import DRC
from .erc import ERC
from .export import Export
from .gerber import Gerber
from .gui import GUI
from .jobset import Jobset
from .library import Library
from .project import Project
from .sch import Schematic
from .sim import Simulator

if TYPE_CHECKING:  # pragma: no cover
    from klipy.klicad import KliCAD


class KliCAD:
    """Pythonic convenience layer over KliCAD's ``kicad_native_*`` bindings.

    Construct with no arguments to auto-connect to the default
    ``ipc:///tmp/klicad/api.sock`` socket::

        from klipy.klicad import KliCAD

        k = KliCAD()
        violations = k.drc.run('/path/to/board.kicad_pcb')
        k.sch.open('/path/to/foo.kicad_sch')

    To reuse an existing :class:`klipy.klicad.KliCAD` connection, pass it as
    ``kicad=...``::

        from klipy import KliCAD
        from klipy.klicad import KliCAD
        kk = KliCAD()
        k = KliCAD(kicad=kk)

    Sub-proxies:

    - :attr:`drc`     — DRC on .kicad_pcb files.
    - :attr:`erc`     — ERC on .kicad_sch files.
    - :attr:`sch`     — schematic editor: CRUD, annotation, actions, upgrade.
    - :attr:`board`   — pcb editor: CRUD, layers, stackup, custom DRC rules, upgrade.
    - :attr:`sim`     — ngspice simulator (tran/ac/dc/op/noise + vector access).
    - :attr:`library` — symbol+footprint lib-table CRUD; ``.symbols`` /
                       ``.footprints`` sub-proxies for editor-backed CRUD.
    - :attr:`export`  — gerbers, drill, 3D, BOM, netlist, SVG, sch plot.
    - :attr:`gui`     — frame launching, dialog dismissal.
    - :attr:`project` — project lifecycle + COMMON_SETTINGS get/set.
    - :attr:`gerber`  — gerber diff/info/PNG + gerbview frame ops.
    - :attr:`jobset`  — load + run ``.kicad_jobset`` files.
    """

    DEFAULT_SOCKET = "ipc:///tmp/klicad/api.sock"
    DEFAULT_TIMEOUT_MS = 300_000

    def __init__(
        self,
        kicad: Optional["KliCAD"] = None,
        *,
        socket_path: Optional[str] = None,
        timeout_ms: Optional[int] = None,
    ) -> None:
        """Connect to a running KliCAD/KliCAD instance.

        :param kicad: an existing :class:`klipy.klicad.KliCAD` to reuse.  If
            given, ``socket_path``/``timeout_ms`` are ignored.
        :param socket_path: IPC socket override (default
            ``ipc:///tmp/klicad/api.sock``).
        :param timeout_ms: per-request timeout (default 300 000 ms / 5 min;
            generous enough for raytraced renders and STEP exports).
        """
        if kicad is None:
            # Imported lazily so importing klipy.klicad doesn't drag in
            # the whole proto-generated client module at import time.
            from klipy.klicad import KliCAD

            kicad = KliCAD(
                socket_path=socket_path or self.DEFAULT_SOCKET,
                timeout_ms=timeout_ms or self.DEFAULT_TIMEOUT_MS,
            )
        self._kicad = kicad
        runner = _PyRunner(kicad)
        self._runner = runner

        # Sub-proxies (composed once, shared runner).
        self.drc: DRC = DRC(runner)
        self.erc: ERC = ERC(runner)
        self.sch: Schematic = Schematic(runner)
        self.board: Board = Board(runner)
        self.sim: Simulator = Simulator(runner)
        self.library: Library = Library(runner)
        self.export: Export = Export(runner)
        self.gui: GUI = GUI(runner)
        self.project: Project = Project(runner)
        self.gerber: Gerber = Gerber(runner)
        self.jobset: Jobset = Jobset(runner)

    @property
    def kicad(self) -> "KliCAD":
        """The underlying :class:`klipy.klicad.KliCAD` connection.

        Use this for any low-level call not covered by the façade (e.g.
        ``self.kicad.run_python('arbitrary code')`` or proto-RPC methods).
        """
        return self._kicad

    def run_python(self, code: str):
        """Pass-through to :meth:`klipy.klicad.KliCAD.run_python`.

        Use for arbitrary code that doesn't fit any sub-proxy.  Returns
        the raw :class:`klipy.klicad.RunPythonResult`.
        """
        return self._kicad.run_python(code)

    def close(self) -> None:
        """Close the underlying KliCAD connection."""
        self._kicad.close()

    def __enter__(self) -> "KliCAD":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"<KliCAD kicad={self._kicad!r}>"

"""Pythonic convenience layer over KliCAD's ``kicad_native_*`` bindings.

KliCAD's external Python interface is exactly one method:
``KiCad.run_python(code) -> RunPythonResult``.  Power users can call it
directly and write embedded snippets by hand.  This package, by contrast,
wraps that primitive in a tree of small proxy classes so that day-to-day
calls look like ordinary Python::

    from kipy.klicad import KliCAD

    k = KliCAD()                                    # auto-connects
    violations = k.drc.run('/path/to/board.kicad_pcb')
    k.sch.add_wire(20.0, 20.0, 50.0, 20.0)
    nets = k.board.list_nets()
    k.sim.tran(schematic='/path/to/foo.kicad_sch', step='10us', stop='1ms')
    k.export.bom('/path/to/foo.kicad_sch', '/tmp/bom.csv')

Every method on every proxy is a thin wrapper that builds a Python
snippet, ships it to KiCad's embedded interpreter via ``run_python``,
parses the ``result_repr`` field, and either returns the resulting native
Python value or raises :class:`KliCADError` carrying the embedded
traceback.  No proto wrangling; no template strings in user code.

For anything not covered by the proxies, drop down to the underlying
``KiCad`` via :attr:`KliCAD.kicad` (or call :meth:`KliCAD.run_python`
directly).
"""

from ._facade import KliCAD
from ._runner import KliCADError

__all__ = ["KliCAD", "KliCADError"]

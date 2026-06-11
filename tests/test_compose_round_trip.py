"""F-S3 compose end-to-end smoke tests.

Replaces the legacy ``test_ogdf_round_trip.py`` (deleted in F-S3 Phase C
along with ``_klicad_sch.py`` and ``_ogdf_layout.py``).  Each test
builds a small Circuit, calls ``Circuit.compose()``, and asserts the
single-IPC compose path produced a usable schematic.

Run::

    .venv/bin/python -m pytest tests/test_compose_round_trip.py -s

Requires klicad to be running with the eeschema kiface loadable.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from klipy.circuit import Circuit, R
from klipy.klicad import KliCAD


@pytest.mark.skipif(
    not KliCAD().is_alive(),
    reason="klicad not running; this is the IPC end-to-end test",
)
def test_two_symbol_via_compose( tmp_path: Path ):
    """F-S3 Phase B — single-resistor round-trip through Circuit.compose().
    One IPC call, no legacy to_schematic or relayout_via_ogdf."""
    out = tmp_path / "twosym.kicad_sch"
    kicad = KliCAD( timeout_ms=180_000 )

    try:
        kicad.run_python( "import klicad_native_gui as g; g.dismiss_dialogs()" )
    except Exception as e:
        print( f"(pre-flight dismiss_dialogs raised, continuing: {e})" )

    c = Circuit( "twosym" )
    c.add( R( "R1", "VCC", "GND", value="10k" ) )

    # F-S3 Phase C: compose absorbs project-shell creation.  Caller
    # only passes the .kicad_sch path; .kicad_pro / sym-lib-table /
    # models.lib are written automatically inside compose().
    report = c.compose( str(out), kicad=kicad )
    print( f"\nCircuit.compose() report:\n  {report!r}" )

    assert report.get("ok") is True, f"compose failed: {report}"
    assert report.get("parts_placed") == 1
    assert isinstance(report.get("crossings"), int)
    assert report["crossings"] >= 0
    # F-S3 deferred-feature stub: ratsnest_spec is present but empty.
    assert report.get("ratsnest_spec") == ""
    print( f"F-S7 crossings count: {report['crossings']}" )


@pytest.mark.skipif(
    not KliCAD().is_alive(),
    reason="klicad not running; this is the IPC end-to-end test",
)
def test_voltage_divider_via_compose( tmp_path: Path ):
    """F-S3 Phase B — voltage divider through Circuit.compose().

    Asserts the canonical 2-resistor net (R1.MID ↔ R2.MID) routes
    correctly and that compose surfaces parts/wires/crossings counts."""
    out = tmp_path / "vdiv.kicad_sch"
    kicad = KliCAD( timeout_ms=180_000 )

    try:
        kicad.run_python( "import klicad_native_gui as g; g.dismiss_dialogs()" )
    except Exception as e:
        print( f"(pre-flight dismiss_dialogs raised, continuing: {e})" )

    c = Circuit( "vdiv" )
    c.add( R( "R1", "VCC", "MID", value="10k" ) )
    c.add( R( "R2", "MID", "GND", value="10k" ) )

    report = c.compose( str(out), kicad=kicad )
    print( f"\nCircuit.compose() report:\n  {report!r}" )

    assert report.get("ok") is True, f"compose failed: {report}"
    assert report.get("parts_placed") == 2
    assert report.get("wires_emitted") >= 1, "expected at least one wire"
    assert isinstance(report.get("crossings"), int)
    assert report["crossings"] >= 0
    print( f"F-S7 crossings count: {report['crossings']}" )


@pytest.mark.parametrize("mode", ["diff", "strict"])
def test_compose_deferred_modes_stub( mode ):
    """F-S3 deferred-feature stubs: mode='diff' and mode='strict' raise
    NotImplementedError per the 2026-06-07 GOAL.md decision.  The
    docstring on Circuit.compose / compose_schematic captures the
    intended implementation gist + an alternative formulation each."""
    c = Circuit( "stub" )
    c.add( R( "R1", "VCC", "GND", value="10k" ) )

    with pytest.raises( NotImplementedError ) as excinfo:
        c.compose( "/tmp/should_not_be_used.kicad_sch", mode=mode )

    msg = str( excinfo.value )
    assert "deferred-feature stub" in msg
    assert "GOAL.md" in msg
    assert mode in msg

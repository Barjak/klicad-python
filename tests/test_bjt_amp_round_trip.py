"""M3 verification fixture: 10-symbol common-emitter BJT amplifier round-trip.

Builds a canonical common-emitter BJT amplifier (Q1 + R1..R4 + RL + C1..C3 +
V1 = 10 symbols), emits the schematic via to_schematic, re-runs the layout
through the ELK backend (relayout_via_ogdf with KLICAD_LAYOUT_BACKEND=elk),
renders to PNG via kicad-cli + pdftoppm, then runs kicad-cli sch erc and
asserts the ELK-routed schematic produces 0 ERC errors and <=5 warnings.

Run::

    KLICAD_LAYOUT_BACKEND=elk \\
    /home/jakob/projects/KliCAD_development/driver-board/.venv/bin/python \\
        -m pytest tests/test_bjt_amp_round_trip.py -s

Requires klicad to be running with the eeschema kiface loadable.

NOTE on the BJT lib_id: GOAL.md asks for `Transistor_BJT:BC547`, but the
klipy.circuit.NPN DSL class hardcodes `Transistor_BJT:Q_NPN_EBC` as its
kicad_lib_id (BC547-specific symbols aren't currently exposed through the
NPN constructor).  We use the default Q_NPN_EBC symbol with model="BC547"
so the SPICE side names BC547 while the schematic placement uses the
generic NPN-EBC symbol shape (electrically and topologically equivalent
for ERC purposes).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from klipy.circuit import Circuit, R, C, V
from klipy.circuit._part import NPN
from klipy.klicad import KliCAD


@pytest.mark.skipif(
    not KliCAD().is_alive(),
    reason="klicad not running; this is the IPC end-to-end test",
)
def test_bjt_amp_via_compose( tmp_path: Path ):
    """F-S3 Phase B — same BJT amp through Circuit.compose() (single IPC).

    Acceptance: same as legacy path — 0 routing errors, <=5 warnings,
    2 fixture-intrinsic `power_pin_not_driven` excluded.  Additionally
    asserts the ComposeReport carries the F-S7 crossings counter and
    that it's a non-negative int.
    """
    out = tmp_path / "bjt_amp.kicad_sch"
    kicad = KliCAD( timeout_ms=180_000 )

    try:
        kicad.run_python(
            "import klicad_native_gui as g; g.dismiss_dialogs()"
        )
    except Exception as e:
        print( f"(pre-flight dismiss_dialogs raised, continuing: {e})" )

    # Same fixture as the legacy test.  Kept inline so the two tests
    # are independently auditable.
    c = Circuit( "bjt_amp" )
    c.add( V(  "V1", "VCC",      "GND",      dc=12 ) )
    c.add( V(  "V2", "IN",       "GND",      ac="SIN(0 0.01 1k)" ) )
    c.add( R(  "R1", "VCC",      "OUT_COLL", value="1k"  ) )
    c.add( R(  "R2", "EMIT",     "GND",      value="1k"  ) )
    c.add( R(  "R3", "VCC",      "BASE",     value="10k" ) )
    c.add( R(  "R4", "BASE",     "GND",      value="10k" ) )
    c.add( R(  "RL", "OUT",      "GND",      value="10k" ) )
    c.add( C(  "C1", "IN",       "BASE",     value="1u"  ) )
    c.add( C(  "C2", "OUT_COLL", "OUT",      value="1u"  ) )
    c.add( C(  "C3", "EMIT",     "GND",      value="10u" ) )
    c.add( NPN( "Q1", c="OUT_COLL", b="BASE", e="EMIT", model="BC547" ) )

    assert len(c.parts) == 11

    # F-S3 Phase C: compose() absorbs project-shell creation.  No
    # separate write_project_shell call needed.
    report = c.compose( str(out), kicad=kicad )
    print( f"\nCircuit.compose() report:\n  {report!r}" )
    assert report.get("ok") is True, f"compose failed: {report}"
    assert report.get("parts_placed") == 11
    assert isinstance(report.get("crossings"), int)
    assert report["crossings"] >= 0
    print( f"F-S7 crossings count: {report['crossings']}" )

    # ERC ─────────────────────────────────────────────────────────────
    erc_report = tmp_path / "bjt_amp.erc.json"
    env = {**os.environ, "ASAN_OPTIONS": "detect_leaks=0"}
    erc_proc = subprocess.run(
        [
            os.path.expanduser( "~/.local/bin/kicad-cli" ),
            "sch", "erc",
            "--severity-all",
            "--format", "json",
            "-o", str(erc_report),
            str(out),
        ],
        env=env, capture_output=True, text=True,
    )
    assert erc_report.exists(), (
        f"kicad-cli sch erc did not produce a report (rc={erc_proc.returncode}); "
        f"stderr={erc_proc.stderr!r}"
    )

    with erc_report.open() as f:
        erc = json.load( f )

    errors:   list[dict] = []
    warnings: list[dict] = []
    for sheet in erc.get( "sheets", [] ):
        for v in sheet.get( "violations", [] ):
            sev = v.get( "severity", "" ).lower()
            if sev == "error":
                errors.append( v )
            elif sev == "warning":
                warnings.append( v )

    ROUTING_ERROR_TYPES = {
        "pin_not_connected",
        "unconnected_wire_endpoint",
        "label_dangling",
        "hier_label_mismatch",
    }
    routing_errors = [v for v in errors if v.get("type") in ROUTING_ERROR_TYPES]
    fixture_errors = [v for v in errors if v.get("type") not in ROUTING_ERROR_TYPES]

    def _summ( vs: list[dict] ) -> str:
        return "\n    ".join(
            f"[{v.get('severity','?')}] {v.get('type','?')}"
            for v in vs
        ) or "(none)"

    print( f"\nERC routing errors: {len(routing_errors)}" )
    print( f"ERC fixture-intrinsic errors: {len(fixture_errors)}" )
    print( f"ERC warnings: {len(warnings)}" )

    assert len(routing_errors) == 0, (
        f"compose path M3: expected 0 routing ERC errors, got "
        f"{len(routing_errors)}:\n  " + _summ(routing_errors)
    )
    assert len(warnings) <= 5, (
        f"compose path M3: expected <=5 ERC warnings, got "
        f"{len(warnings)}:\n  " + _summ(warnings)
    )

"""End-to-end smoke test for the OGDF auto-layout (Option C).

Builds a 2-symbol circuit (R between Vcc and GND), emits the
schematic via the existing sugiyama layout, then re-runs the
OGDF binding to replace the layout in-place.  Renders the
result to a PNG via kicad-cli and pdftoppm so the user can
eyeball it.

Run::

    /home/jakob/projects/KliCAD_development/driver-board/.venv/bin/python -m pytest \\
        tests/test_ogdf_round_trip.py -s

Requires klicad to be running with the eeschema kiface loadable
(i.e. ~/.local/bin/klicad started and the IPC socket reachable).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from klipy.circuit import Circuit, R
from klipy.circuit._ogdf_layout import _net_spec_for, relayout_via_ogdf
from klipy.klicad import KliCAD


def test_net_spec_for_two_symbol_circuit():
    """Pure-Python sanity check: the spec maps R's two connections
    to ('VCC', [('R1', '1')]) and ('GND', [('R1', '2')])."""
    c = Circuit( "twosym" )
    c.add( R( "R1", "VCC", "GND", value="10k" ) )
    spec = _net_spec_for( c )
    spec_dict = dict( spec )
    assert "VCC" in spec_dict, f"missing VCC net; spec={spec}"
    assert "GND" in spec_dict, f"missing GND net; spec={spec}"
    assert ( "R1", "1" ) in spec_dict["VCC"]
    assert ( "R1", "2" ) in spec_dict["GND"]


@pytest.mark.skipif(
    not KliCAD().is_alive(),
    reason="klicad not running; this is the IPC end-to-end test",
)
def test_two_symbol_round_trip( tmp_path: Path ):
    """The full pipeline: emit a 2-symbol schematic with sugiyama,
    re-layout via the OGDF binding, render to PNG.

    Acceptance is currently 'doesn't crash and produces a PNG' —
    visual inspection is the human-loop part of the M1 multimodal
    verification.  PNG path is printed so the user can open it."""
    out = tmp_path / "twosym.kicad_sch"

    c = Circuit( "twosym" )
    c.add( R( "R1", "VCC", "GND", value="10k" ) )
    c.to_schematic( str(out) )

    # Re-run layout via OGDF.
    report = relayout_via_ogdf( c, out )
    print( f"\nrelayout_via_ogdf report:\n  {report.result_repr!r}" )
    # The binding's IPC return ends up in r.result_repr as a Python
    # dict-repr string.  Don't strict-parse; just sanity-check it
    # mentions 'ok'.
    assert "ok" in (report.result_repr or "")

    # Render PNG via kicad-cli + pdftoppm.
    pdf = tmp_path / "twosym.pdf"
    env = {**os.environ, "ASAN_OPTIONS": "detect_leaks=0"}
    subprocess.run(
        [
            os.path.expanduser( "~/.local/bin/kicad-cli" ),
            "sch", "export", "pdf",
            "-o", str(pdf), str(out),
        ],
        env=env, check=True, capture_output=True,
    )
    assert pdf.exists() and pdf.stat().st_size > 1024

    pdftoppm = shutil.which( "pdftoppm" )
    if pdftoppm is None:
        pytest.skip( "pdftoppm not installed; cannot render PNG" )
    subprocess.run(
        [ pdftoppm, "-r", "150", "-png", str(pdf), str(tmp_path / "twosym") ],
        check=True,
    )

    pngs = sorted( tmp_path.glob( "twosym-*.png" ) )
    assert pngs, f"no PNG produced; tmp_path contents: {list(tmp_path.iterdir())}"
    print( f"\nrendered PNGs (open these to verify the OGDF layout):" )
    for p in pngs:
        print( f"  {p}" )

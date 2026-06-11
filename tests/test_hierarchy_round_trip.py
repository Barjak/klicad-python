"""GOAL.md M4 acceptance fixture — two-level hierarchy through Circuit.compose().

Builds a parent sheet (2 resistors + 1 sub-sheet instance) referencing a
3-part "Amp" child Sub-Circuit (1 NPN + 2 resistors).  The cross-sheet
net STAGE_IN ↔ IN has 3 endpoints (1 parent + 2 child) so F-S1d's
endpoint-exact / junction-at-≥3-way invariants fire ACROSS the sheet
boundary — that is the M4-specific load the BJT amp fixture (single-
sheet) doesn't exercise.

Run::

    KLICAD_LAYOUT_BACKEND=elk \\
    .venv/bin/python -m pytest tests/test_hierarchy_round_trip.py -s

Requires klicad to be running with the eeschema kiface loadable.

xfail rationale (this file)
---------------------------
This test is CORRECT against the eventual-passing path but is marked
xfail (strict=False) because F-S5 Phase B has not landed yet:

  * ``ElkHierarchyBuilder`` (compound-node ELK graph + cross-sheet
    hyperedges, ``KliCAD/eeschema/auto_layout/elk_hierarchy_builder.{h,cpp}``)
    is not yet implemented.  Until then, compose materializes the
    parent's SCH_SHEET item via ``schematic_operations``'s sheet
    materialization loop, but ELK never sees a compound node for the
    child — so cross-sheet wires either don't route or route to stale
    placeholder coordinates.
  * ``HIERARCHY_HANDLING = INCLUDE_CHILDREN`` is not set on the ELK
    root by anything in the current adapter.
  * F-S4b's ``unmatched_hier_reference`` ERC error fires when
    ``m_matchedEndpoint`` is empty.  After F-S3 + F-S4a land, every
    sheet pin without a typed-reference write will trigger that — and
    we WANT that to count as a real failure, not a fixture quirk.

Once F-S5 Phase B + F-S6 ship, this test should pass; flip
``strict=False`` to ``strict=True`` to catch silent regressions.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from klipy.circuit import Circuit, R
from klipy.circuit._part import NPN, SubcircuitInstance
from klipy.klicad import KliCAD


@pytest.mark.xfail(
    reason=(
        "F-S5 Phase B ElkHierarchyBuilder not yet shipped; compose "
        "materializes sheet items but compound-node layout missing. "
        "Test is correct against eventual-passing path."
    ),
    strict=False,
)
@pytest.mark.skipif(
    not KliCAD().is_alive(),
    reason="klicad not running; IPC end-to-end test",
)
def test_hierarchy_round_trip( tmp_path: Path ):
    """GOAL.md M4 acceptance.  Two-level hierarchy via Circuit.compose().

    Exercises:
      - F-S5 Phase B compound-node layout (HIERARCHY_HANDLING=INCLUDE_CHILDREN)
      - F-S1d connectivity invariants across a sheet boundary (the
        cross-sheet net STAGE_IN ↔ IN has 3 endpoints — 1 parent
        (R_in.pin2) + 2 child (Q1.B and R_pullup.pin1) — so the
        writeback must emit a junction at the child-side fork)
      - F-S4b ``unmatched_hier_reference`` ERC fires when typed-reference
        ``m_matchedEndpoint`` lookup misses (counted as a routing error).
    """
    out = tmp_path / "hier_top.kicad_sch"
    kicad = KliCAD( timeout_ms=180_000 )

    try:
        kicad.run_python(
            "import klicad_native_gui as g; g.dismiss_dialogs()"
        )
    except Exception as e:
        print( f"(pre-flight dismiss_dialogs raised, continuing: {e})" )

    # ── Child Sub-Circuit "Amp" ────────────────────────────────────────
    # 3 parts.  The IN port connects to TWO child-side pins (Q1.B and
    # R_pullup.pin1).  Combined with R_in.pin2 on the parent side, the
    # STAGE_IN ↔ IN cross-sheet net has 3 endpoints total — that is
    # the F-S1d junction-at-≥3-way invariant exercised across the
    # hierarchy boundary, which is GOAL.md M4's specific requirement.
    # Power nets (VCC/GND) flow implicitly through the hierarchy — the
    # DSL refuses to declare them as ports (validate_port_decl rejects
    # power-named ports).  Only the signal-routing IN / OUT are typed
    # ports, which is exactly the case the typed sheet-pin reference
    # mechanism (F-S4a/b) is designed to handle.
    amp = Circuit( "Amp", ports=["IN", "OUT"] )
    amp.add( NPN( "Q1", c="OUT", b="IN",  e="GND", model="BC547" ) )
    amp.add( R(   "R_pullup", "IN",  "VCC", value="10k" ) )
    amp.add( R(   "R_load",   "OUT", "VCC", value="1k"  ) )

    # ── Parent ─────────────────────────────────────────────────────────
    top = Circuit( "hier_top" )
    top.add( R( "R_in",  "INPUT",     "STAGE_IN",  value="10k" ) )
    top.add( R( "R_out", "STAGE_OUT", "GND",       value="10k" ) )
    top.add(
        SubcircuitInstance(
            "U_amp1", amp,
            port_map={
                "IN":  "STAGE_IN",
                "OUT": "STAGE_OUT",
            },
        )
    )

    # F-S3 Phase C: compose absorbs project shell.  Single IPC call.
    report = top.compose( str(out), kicad=kicad )
    print( f"\nCircuit.compose() report:\n  {report!r}" )

    assert report.get("ok") is True, f"compose failed: {report}"
    assert report.get("parts_placed") == 2, (
        f"expected 2 parent-sheet parts (R_in, R_out), got "
        f"{report.get('parts_placed')!r}"
    )
    assert report.get("sheets_placed", 0) >= 1, (
        f"expected at least 1 sheet instance materialized, got "
        f"{report.get('sheets_placed')!r}"
    )
    assert isinstance(report.get("crossings"), int)
    assert report["crossings"] >= 0
    # F-S3 deferred-feature stub: ratsnest_spec is present but empty.
    assert report.get("ratsnest_spec") == ""
    print( f"F-S7 crossings count: {report['crossings']}" )

    # ── ERC ────────────────────────────────────────────────────────────
    erc_report = tmp_path / "hier_top.erc.json"
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

    errors: list[dict] = []
    for sheet in erc.get( "sheets", [] ):
        for v in sheet.get( "violations", [] ):
            if v.get( "severity", "" ).lower() == "error":
                errors.append( v )

    # M4 acceptance split — F-S5 Phase B + F-S1d both fire across the
    # hierarchy.  F-S4b's `unmatched_hier_reference` is in the routing
    # bucket because it indicates a missing typed sheet-pin↔hier-label
    # KIID reference, which is exactly the cross-sheet connectivity
    # failure we want to count as a real (not fixture-quirk) failure.
    ROUTING_ERROR_TYPES = {
        "pin_not_connected",
        "unconnected_wire_endpoint",
        "label_dangling",
        "hier_label_mismatch",
        "unmatched_hier_reference",  # F-S4b ERC error
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
    print( f"  routing detail:\n    {_summ(routing_errors)}" )
    print( f"  fixture detail:\n    {_summ(fixture_errors)}" )

    # M4: zero routing errors across the hierarchy boundary.
    assert len(routing_errors) == 0, (
        f"M4 acceptance: expected 0 routing errors, got "
        f"{len(routing_errors)}:\n  " + _summ(routing_errors)
    )

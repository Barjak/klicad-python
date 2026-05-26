"""KliCAD GUI smoke test — visually exercise every editor frame end-to-end.

Consolidates the ad-hoc heredocs we run by hand for runtime validation:

  1. Load a real project (switch) + board + schematic
  2. Open every editor frame (pcb, sch, sym, fp, gerbview, page_layout,
     cvpcb, calculator, simulator)
  3. Auto-spawn the 3D viewer and write a snapshot
  4. Run DRC / ERC / sync.dry_run / diff against the loaded content
  5. Sanity-check the fp editor 0-libs fix (≥100 libs visible)
  6. Confirm every Pattern B kiface (cvpcb, pagelayout, simulator,
     sim_advanced) is alive and its module surface is loadable
  7. Report screenshot directory at the end

Each test depends on the session-scoped `loaded_switch_project` fixture so
the project gets loaded exactly once.  Tests can run in any order after
that; pytest collects in file order by default which gives a nice
"walk through the GUI" feel.

Run together with the rest:
    pytest tests/

Run alone with verbose output (recommended for visual review):
    pytest tests/test_gui_smoke.py -v -s
"""
from __future__ import annotations

import ast
import pathlib
import tempfile

import pytest

from conftest import (
    SWITCH_PCB,
    SWITCH_SCH,
    SWITCH_PROJECT_DIR,
    assert_kicad_alive,
    assert_run_python_ok,
)


SMOKE_SCREENSHOT_DIR = pathlib.Path(tempfile.gettempdir()) / "klicad-gui-smoke"


# --- session-scoped fixture: load project + open the two main editors ----

@pytest.fixture(scope="session")
def loaded_switch_project(kicad):
    """Load the switch project, push board into pcb_editor, push schematic
    into eeschema.  Idempotent; safe to call multiple times if the test
    runner ever re-invokes the fixture.
    """
    SMOKE_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

    pro_path = SWITCH_PROJECT_DIR / "switch.kicad_pro"

    r = kicad.run_python(
        "import kicad_native_project_manager as pm\n"
        f"pm.load_project({str(pro_path)!r})"
    )
    assert_run_python_ok(r)

    r = kicad.run_python(
        "import kicad_native_gui as g; g.show_frame('pcb_editor')\n"
        "import kicad_native_pcb_state as ps\n"
        f"ps.open_board({str(SWITCH_PCB)!r})"
    )
    assert_run_python_ok(r)
    assert "switch.kicad_pcb" in r.result_repr, r.result_repr

    r = kicad.run_python(
        "import kicad_native_gui as g; g.show_frame('schematic')\n"
        "import kicad_native_schematic_state as ss\n"
        f"ss.open_schematic({str(SWITCH_SCH)!r})"
    )
    assert_run_python_ok(r)

    return kicad


# --- frame-by-frame walkthrough -------------------------------------------

def test_smoke_pcb_editor_loaded(loaded_switch_project):
    """PCB editor has the switch board loaded (filename + non-empty bbox)."""
    r = loaded_switch_project.run_python(
        "import kicad_native_pcb_state as ps; ps.get_board_info()"
    )
    assert_run_python_ok(r)
    info = ast.literal_eval(r.result_repr)
    assert info["filename"].endswith("switch.kicad_pcb"), info
    assert info["board_outline_bbox"]["width_mm"] > 0, info
    assert_kicad_alive(loaded_switch_project)


def test_smoke_sch_editor_loaded(loaded_switch_project):
    """Schematic editor has the switch schematic loaded (sheet_count>=1)."""
    r = loaded_switch_project.run_python(
        "import kicad_native_schematic_state as ss; ss.get_items_summary()"
    )
    assert_run_python_ok(r)
    summary = ast.literal_eval(r.result_repr)
    assert summary.get("sheet_count", 0) >= 1, summary


def test_smoke_fp_editor_sees_libraries(loaded_switch_project):
    """fp 0-libs bug fix sanity: footprint editor sees ≥100 of the 155 std libs."""
    r = loaded_switch_project.run_python(
        "import kicad_native_gui as g; g.show_frame('footprint_editor')\n"
        "import kicad_native_footprint_editor as fe\n"
        "len(fe.list_loaded_libraries())"
    )
    assert_run_python_ok(r)
    count = int(r.result_repr)
    assert count >= 100, (
        f"footprint editor sees only {count} libraries (expected ≥100). "
        f"The 0-libs adapter cache regression is back."
    )


def test_smoke_symbol_editor_kiface_alive(loaded_switch_project):
    """symbol_editor kiface spawns + binding module is loadable."""
    r = loaded_switch_project.run_python(
        "import kicad_native_gui as g; g.show_frame('symbol_editor')\n"
        "import kicad_native_symbol_editor as se\n"
        "sorted(x for x in dir(se) if not x.startswith('_'))[:5]"
    )
    assert_run_python_ok(r)


def test_smoke_gerbview_kiface_alive(loaded_switch_project):
    """gerbview kiface spawns + binding module is loadable."""
    r = loaded_switch_project.run_python(
        "import kicad_native_gui as g; g.show_frame('gerbview')\n"
        "import kicad_native_gerbview as gv\n"
        "sorted(x for x in dir(gv) if not x.startswith('_'))[:5]"
    )
    assert_run_python_ok(r)


def test_smoke_pagelayout_kiface_alive(loaded_switch_project):
    """page_layout kiface spawns + binding module is loadable."""
    r = loaded_switch_project.run_python(
        "import kicad_native_gui as g; g.show_frame('page_layout')\n"
        "import kicad_native_pagelayout as pl\n"
        "sorted(x for x in dir(pl) if not x.startswith('_'))[:5]"
    )
    assert_run_python_ok(r)


def test_smoke_simulator_kiface_alive(loaded_switch_project):
    """simulator kiface spawns + both sim binding modules are loadable."""
    r = loaded_switch_project.run_python(
        "import kicad_native_gui as g; g.show_frame('simulator')\n"
        "import kicad_native_simulator as sim\n"
        "import kicad_native_sim_advanced as sa\n"
        "[sorted(x for x in dir(sim) if not x.startswith('_'))[:3],\n"
        " sorted(x for x in dir(sa) if not x.startswith('_'))[:3]]"
    )
    assert_run_python_ok(r)


def test_smoke_cvpcb_kiface_alive(loaded_switch_project):
    """cvpcb kiface spawns + binding module is loadable.

    This is the wave-5 new kiface_register infrastructure — first time
    cvpcb had any KliCAD plumbing.
    """
    r = loaded_switch_project.run_python(
        "import kicad_native_gui as g; g.show_frame('cvpcb')\n"
        "import kicad_native_cvpcb as c\n"
        "sorted(x for x in dir(c) if not x.startswith('_'))"
    )
    assert_run_python_ok(r)
    surface = ast.literal_eval(r.result_repr)
    assert "load_netlist" in surface, surface
    assert "assign_footprint" in surface, surface


def test_smoke_calculator_kiface_alive(loaded_switch_project):
    """pcb_calculator kiface spawns; binding is Pattern A (always loadable)."""
    r = loaded_switch_project.run_python(
        "import kicad_native_gui as g; g.show_frame('calculator')\n"
        "import kicad_native_pcb_calculator as pc\n"
        "sorted(x for x in dir(pc) if not x.startswith('_'))[:5]"
    )
    assert_run_python_ok(r)


def test_smoke_all_frames_in_open_list(loaded_switch_project):
    """list_open_frames sees every kiface we spawned above."""
    r = loaded_switch_project.run_python(
        "import kicad_native_gui as g\n"
        "[f['title'] for f in g.list_open_frames()]"
    )
    assert_run_python_ok(r)
    titles = ast.literal_eval(r.result_repr)
    # We don't pin exact titles (some include filenames, "[no symbol loaded]",
    # etc.) — just verify at least the core editors are present by keyword.
    flat = " | ".join(titles).lower()
    for needle in ["schematic editor", "pcb editor", "symbol editor",
                   "footprint editor", "gerber viewer", "drawing sheet"]:
        assert needle in flat, f"missing {needle!r} in open frames: {titles}"


# --- wave-5 binding sanity against the loaded content --------------------

def test_smoke_netinfo_sees_real_nets(loaded_switch_project):
    """netinfo.list_nets() returns real nets from the loaded board."""
    r = loaded_switch_project.run_python(
        "import kicad_native_netinfo as n; [x['name'] for x in n.list_nets()]"
    )
    assert_run_python_ok(r)
    names = ast.literal_eval(r.result_repr)
    # Switch board has J1 pads → expect at least Net-(J1-Pad...) entries.
    assert any("J1-Pad" in n for n in names), \
        f"expected J1-pad nets, got: {names}"


def test_smoke_hierarchy_sees_real_sheets(loaded_switch_project):
    """hierarchy.list_sheets() returns at least one sheet."""
    r = loaded_switch_project.run_python(
        "import kicad_native_hierarchy as h; len(h.list_sheets())"
    )
    assert_run_python_ok(r)
    assert int(r.result_repr) >= 1


def test_smoke_3d_viewer_snapshot(loaded_switch_project):
    """3D viewer auto-spawns via PCB_BASE_FRAME::CreateAndShow3D_Frame and
    writes a non-empty PNG.  This is the wave-5 spawn-path fix in action.
    """
    snap = SMOKE_SCREENSHOT_DIR / "3d_viewer.png"
    snap.unlink(missing_ok=True)

    r = loaded_switch_project.run_python(
        "import kicad_native_3d_viewer as v\n"
        f"v.take_snapshot({str(snap)!r}, width=600, height=400)"
    )
    assert_run_python_ok(r)
    assert snap.exists(), f"snapshot not written to {snap}"
    # PNG header is 8 bytes; a real raster of any size is well over 1KB.
    assert snap.stat().st_size > 1024, \
        f"3D snapshot suspiciously small ({snap.stat().st_size} bytes)"


def test_smoke_drc_runs(loaded_switch_project):
    """DRC dispatches against the loaded board."""
    r = loaded_switch_project.run_python(
        "import kicad_native_drc as drc\n"
        f"drc.run({str(SWITCH_PCB)!r}, severity='error').get('ok', False)"
    )
    assert_run_python_ok(r)
    assert r.result_repr == "True", r.result_repr


def test_smoke_erc_runs(loaded_switch_project):
    """ERC dispatches against the loaded schematic."""
    r = loaded_switch_project.run_python(
        "import kicad_native_erc as erc\n"
        f"erc.run({str(SWITCH_SCH)!r}, severity='error').get('ok', False)"
    )
    assert_run_python_ok(r)
    assert r.result_repr == "True", r.result_repr


def test_smoke_sync_dry_run_structured(loaded_switch_project):
    """sync.dry_run_update returns a structured response.

    Doesn't require ok=True — without a SPICE-clean schematic open, the
    netlist fetch can legitimately fail.  We just want the binding to
    dispatch and return the documented dict shape.
    """
    r = loaded_switch_project.run_python(
        "import kicad_native_sync as s; s.dry_run_update()"
    )
    assert_run_python_ok(r)
    res = ast.literal_eval(r.result_repr)
    assert "changes" in res
    assert "warnings" in res
    assert "errors" in res


def test_smoke_diff_self_zero_changes(loaded_switch_project):
    """Diff the switch board against itself: zero changes, ≥1 item."""
    r = loaded_switch_project.run_python(
        "import kicad_native_diff as d\n"
        f"d.diff_summary_pcb({str(SWITCH_PCB)!r}, {str(SWITCH_PCB)!r})['summary']"
    )
    assert_run_python_ok(r)
    summary = ast.literal_eval(r.result_repr)
    assert summary["added_count"] == 0, summary
    assert summary["removed_count"] == 0, summary
    assert summary["modified_count"] == 0, summary
    assert summary["total_a"] >= 1, summary


# --- last test: surface artifacts to the user ----------------------------

def test_smoke_artifacts_summary(loaded_switch_project):
    """Print the screenshot directory + KliCAD version so a `pytest -s` run
    surfaces actionable info for visual review."""
    r = loaded_switch_project.run_python(
        "import kicad_native; kicad_native.version()"
    )
    assert_run_python_ok(r)

    print(f"\n>>> KliCAD GUI smoke artifacts:")
    print(f"    KliCAD version : {r.result_repr}")
    print(f"    screenshots   : {SMOKE_SCREENSHOT_DIR}")
    for p in sorted(SMOKE_SCREENSHOT_DIR.glob("*.png")):
        print(f"      - {p.name}  ({p.stat().st_size} bytes)")

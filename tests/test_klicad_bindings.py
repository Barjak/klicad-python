"""Per-binding positive tests for KliCAD subsystem bindings.

One module per subsystem.  Each test invokes the binding's `run()` and
asserts:
  * `result['ok']` is True (or that an expected error is raised)
  * Expected output files exist where the binding said they went
  * KiCad still responds afterwards (catches crashes immediately,
    before the session-end crash-log check)

Tests that we know crash KiCad today are marked ``@pytest.mark.xfail``
with a description of the upstream bug.  When upstream fixes it the
test will turn green (xpassed), prompting us to remove the mark.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from conftest import (
    INSTALL_DIR,
    SWITCH_PCB,
    SWITCH_SCH,
    assert_kicad_alive,
    assert_run_python_ok,
)


# ---- DRC / ERC ----

def test_drc_on_switch_board(kicad, tmp_path):
    """DRC on switch.kicad_pcb returns a structured report dict."""
    r = kicad.run_python(
        "import kicad_native_drc as d\n"
        f"d.run({str(SWITCH_PCB)!r}, units='mm', severity='warning')"
    )
    assert_run_python_ok(r)
    assert "'ok': True" in r.result_repr, r.result_repr
    assert "'report':" in r.result_repr, r.result_repr
    assert_kicad_alive(kicad)


def test_erc_on_switch_schematic(kicad):
    """ERC on switch.kicad_sch returns a structured report dict."""
    r = kicad.run_python(
        "import kicad_native_erc as e\n"
        f"e.run({str(SWITCH_SCH)!r}, units='mm', severity='warning')"
    )
    assert_run_python_ok(r)
    assert "'ok': True" in r.result_repr, r.result_repr
    assert "'report':" in r.result_repr, r.result_repr
    assert_kicad_alive(kicad)


# ---- PCB-side exports ----

def test_export_gerbers(kicad, tmp_path):
    """Gerbers export produces a directory full of .g* files."""
    out_dir = tmp_path / "gerbers"
    out_dir.mkdir()
    r = kicad.run_python(
        "import kicad_native_export_gerbers as g\n"
        f"g.run({str(SWITCH_PCB)!r}, output_dir={str(out_dir) + '/'!r})"
    )
    assert_run_python_ok(r)
    assert "'ok': True" in r.result_repr, r.result_repr
    produced = list(out_dir.iterdir())
    assert len(produced) >= 5, f"expected several gerber files, got {produced}"
    assert_kicad_alive(kicad)


def test_export_drill(kicad, tmp_path):
    """Drill export produces .drl + (optionally) map files."""
    out_dir = tmp_path / "drill"
    out_dir.mkdir()
    r = kicad.run_python(
        "import kicad_native_export_drill as d\n"
        f"d.run({str(SWITCH_PCB)!r}, output_dir={str(out_dir) + '/'!r},"
        f" format='excellon', generate_map=True, map_format='pdf')"
    )
    assert_run_python_ok(r)
    assert "'ok': True" in r.result_repr, r.result_repr
    # Drill output may be named '.drl' or '<base>.drl' depending on config;
    # check .name.endswith because Path('.drl').suffix == '' (treats as hidden).
    assert any(".drl" in p.name for p in out_dir.iterdir()), \
        f"no .drl-bearing file in {list(out_dir.iterdir())}"
    assert_kicad_alive(kicad)


def test_export_3d_step(kicad, tmp_path):
    """STEP export produces a .step file."""
    out = tmp_path / "switch.step"
    r = kicad.run_python(
        "import kicad_native_export_3d as t\n"
        f"t.run({str(SWITCH_PCB)!r}, output={str(out)!r}, format='step', overwrite=True)"
    )
    assert_run_python_ok(r)
    assert "'ok': True" in r.result_repr, r.result_repr
    assert out.is_file() and out.stat().st_size > 0
    assert_kicad_alive(kicad)


def test_render_png(kicad, tmp_path):
    """Render produces a PNG image."""
    out = tmp_path / "render.png"
    r = kicad.run_python(
        "import kicad_native_render as rn\n"
        f"rn.run({str(SWITCH_PCB)!r}, output={str(out)!r},"
        f" format='png', side='top', width=400, height=300, quality='basic')"
    )
    assert_run_python_ok(r)
    assert "'ok': True" in r.result_repr, r.result_repr
    assert out.is_file() and out.stat().st_size > 100
    assert_kicad_alive(kicad)


# ---- Schematic-side exports ----

def test_export_sch_pdf(kicad, tmp_path):
    """Schematic-plot to PDF produces a .pdf."""
    out = tmp_path / "sch.pdf"
    r = kicad.run_python(
        "import kicad_native_export_sch_plot as sp\n"
        f"sp.run({str(SWITCH_SCH)!r}, output={str(out)!r}, format='pdf')"
    )
    assert_run_python_ok(r)
    assert "'ok': True" in r.result_repr, r.result_repr
    assert out.is_file() and out.stat().st_size > 1024  # not an empty PDF
    assert_kicad_alive(kicad)


def test_export_sch_netlist(kicad, tmp_path):
    """Schematic netlist export (KiCad sexpr) produces a .net file."""
    out = tmp_path / "switch.net"
    r = kicad.run_python(
        "import kicad_native_export_sch_netlist as n\n"
        f"n.run({str(SWITCH_SCH)!r}, output={str(out)!r}, format='kicad')"
    )
    assert_run_python_ok(r)
    assert "'ok': True" in r.result_repr, r.result_repr
    assert out.is_file() and "export" in out.read_text()
    assert_kicad_alive(kicad)


def test_export_sch_bom(kicad, tmp_path):
    """BOM CSV export."""
    out = tmp_path / "bom.csv"
    r = kicad.run_python(
        "import kicad_native_export_sch_bom as b\n"
        f"b.run({str(SWITCH_SCH)!r}, output={str(out)!r},"
        " fields_ordered=['Reference','Value','Footprint','${QUANTITY}'],"
        " field_delimiter=',', string_delimiter='\"')"
    )
    assert_run_python_ok(r)
    assert "'ok': True" in r.result_repr, r.result_repr
    assert out.is_file()
    head = out.read_text().splitlines()[0]
    assert "Reference" in head and "QUANTITY" in head
    assert_kicad_alive(kicad)


# ---- Upgrades ----

def test_pcb_upgrade_on_copy(kicad, switch_project_copy):
    """PCB upgrade on a copy.  Currently expected to fail with the
    'cannot rename temp file' error when the live KiCad has the original
    switch project loaded — but the binding shouldn't crash.

    Marked xfail because the failure mode is a known upstream state issue
    (KiCad's PCB editor holds the original path; save-rename targets the
    copy and fails).  When upstream lets the in-process upgrade work on
    an unrelated path, this turns green."""
    target = switch_project_copy / "switch.kicad_pcb"
    r = kicad.run_python(
        "import kicad_native_pcb_upgrade as u\n"
        f"u.run({str(target)!r}, force=True)"
    )
    assert_run_python_ok(r)
    assert_kicad_alive(kicad)  # the IMPORTANT check — no crash
    if "'ok': True" not in r.result_repr:
        pytest.xfail(
            "known upstream state issue: PCB editor holds original path; "
            "save-rename of copy fails with 'Cannot rename temp file'"
        )


def test_sch_upgrade_on_copy(kicad, switch_project_copy):
    """Schematic upgrade rewrites the .kicad_sch in place on a copy."""
    target = switch_project_copy / "switch.kicad_sch"
    before_mtime = target.stat().st_mtime
    r = kicad.run_python(
        "import kicad_native_sch_upgrade as u\n"
        f"u.run({str(target)!r}, force=True)"
    )
    assert_run_python_ok(r)
    assert "'ok': True" in r.result_repr, r.result_repr
    assert target.stat().st_mtime > before_mtime, "file should have been rewritten"
    assert_kicad_alive(kicad)


def test_fp_upgrade_on_copy(kicad, demo_fp_lib, tmp_path):
    """Footprint library upgrade in place on a copy."""
    dst = tmp_path / demo_fp_lib.name
    shutil.copytree(demo_fp_lib, dst)
    r = kicad.run_python(
        "import kicad_native_fp_upgrade as u\n"
        f"u.run({str(dst)!r}, force=True)"
    )
    assert_run_python_ok(r)
    assert "'ok': True" in r.result_repr, r.result_repr
    assert_kicad_alive(kicad)


def test_sym_upgrade_on_copy(kicad, demo_sym_lib, tmp_path):
    """Symbol library upgrade in place on a copy."""
    dst = tmp_path / demo_sym_lib.name
    shutil.copy(demo_sym_lib, dst)
    r = kicad.run_python(
        "import kicad_native_sym_upgrade as u\n"
        f"u.run({str(dst)!r}, force=True)"
    )
    assert_run_python_ok(r)
    assert "'ok': True" in r.result_repr, r.result_repr
    assert_kicad_alive(kicad)


# ---- Library SVG exports ----

def test_sym_export_svg(kicad, demo_sym_lib, tmp_path):
    """Symbol-library SVG export produces per-symbol .svg files."""
    out_dir = tmp_path / "svgs"
    out_dir.mkdir()
    r = kicad.run_python(
        "import kicad_native_sym_export_svg as s\n"
        f"s.run({str(demo_sym_lib)!r}, output_dir={str(out_dir) + '/'!r})"
    )
    assert_run_python_ok(r)
    assert "'ok': True" in r.result_repr, r.result_repr
    svgs = [p for p in out_dir.iterdir() if p.suffix == ".svg"]
    assert len(svgs) > 0, f"expected SVGs in {out_dir}, got {list(out_dir.iterdir())}"
    assert_kicad_alive(kicad)


def test_fp_export_svg_crashes_today(kicad, demo_fp_lib, tmp_path, expected_crash):
    """fp_export_svg currently CRASHES KiCad.

    Upstream bug: ``PCBNEW_JOBS_HANDLER::doFpExportSvg`` dereferences
    ``Pgm().GetSettingsManager().GetProject("")`` which returns null in
    our GUI-mode embedded context (the CLI loads a project explicitly
    first; we don't).  Confirmed by the X86-64 crash dump on
    2026-05-16.

    This test:
      1. Marks the test as @expected_crash (so the session-end crash
         check tolerates one extra crash log)
      2. Skips itself if it ran — because anything after this test in
         the session has no KiCad to talk to (since the crash kills it)

    When upstream null-guards GetProject(""), flip this test to assert
    success and remove the expected_crash mark.
    """
    pytest.skip(
        "fp_export_svg crashes KiCad today (upstream null-deref in "
        "doFpExportSvg).  Tracked; enable when upstream fixes."
    )


# ---- Jobset ----

def test_jobset_module_loaded(kicad):
    """The jobset binding module loads and exposes load() + run()."""
    r = kicad.run_python(
        "import kicad_native_jobset as js\n"
        "sorted(a for a in dir(js) if not a.startswith('_'))"
    )
    assert_run_python_ok(r)
    assert "'load'" in r.result_repr and "'run'" in r.result_repr, r.result_repr


def test_jobset_load_demo_if_present(kicad):
    """If any demo ships a .kicad_jobset, load it and check the
    dict shape."""
    found = subprocess.run(
        ["find", str(INSTALL_DIR / "../demos"), "-name", "*.kicad_jobset"],
        capture_output=True, text=True,
    ).stdout.splitlines()
    if not found:
        pytest.skip("no demo .kicad_jobset to load")
    path = found[0]
    r = kicad.run_python(
        f"import kicad_native_jobset as js\n"
        f"info = js.load({path!r})\n"
        f"(info['ok'], len(info['jobs']), len(info['destinations']))"
    )
    assert_run_python_ok(r)
    assert "True" in r.result_repr, r.result_repr
    assert_kicad_alive(kicad)



# ---- Gerber-diff (smoke only; full diff needs two .gbr files) ----

def test_gerber_diff_module(kicad):
    """gerber-diff binding loads without instantiating a diff."""
    r = kicad.run_python(
        "import kicad_native_gerber_diff as gd; hasattr(gd, 'run')"
    )
    assert_run_python_ok(r)
    assert r.result_repr == "True"
    assert_kicad_alive(kicad)


# ---- batch 5: gerber_info / gerber_export_png / pcb_import ----

def test_gerber_info_on_exported_gerber(kicad, tmp_path):
    """gerber_info parses one of the gerbers we just exported from the switch
    board and returns a structured report dict."""
    # First export gerbers from switch board so we have files to inspect
    gerber_dir = tmp_path / "gerbers"
    gerber_dir.mkdir()
    r = kicad.run_python(
        "import kicad_native_export_gerbers as g\n"
        f"g.run({str(SWITCH_PCB)!r}, output_dir={str(gerber_dir) + '/'!r})"
    )
    assert_run_python_ok(r)
    # Pick the F.Cu gerber
    f_cu = next((p for p in gerber_dir.iterdir() if "F_Cu" in p.name), None)
    assert f_cu is not None, f"no F_Cu gerber in {list(gerber_dir.iterdir())}"

    r = kicad.run_python(
        "import kicad_native_gerber_info as gi\n"
        f"gi.run({str(f_cu)!r}, output_format='json', units='mm', calculate_area=True)"
    )
    assert_run_python_ok(r)
    assert "'ok': True" in r.result_repr, r.result_repr
    assert "'report':" in r.result_repr, r.result_repr
    assert_kicad_alive(kicad)


def test_gerber_export_png(kicad, tmp_path):
    """gerber_export_png rasterizes a gerber to PNG.

    The switch board is sparse — many of its per-layer gerbers contain
    "no draw items".  We pick the largest gerber in the export, which is
    the most likely to have content.  If even that is empty, accept the
    upstream "no draw items" error as proof the binding dispatched; the
    test still fails only if KiCad crashes.
    """
    gerber_dir = tmp_path / "gerbers"
    gerber_dir.mkdir()
    r = kicad.run_python(
        "import kicad_native_export_gerbers as g\n"
        f"g.run({str(SWITCH_PCB)!r}, output_dir={str(gerber_dir) + '/'!r})"
    )
    assert_run_python_ok(r)

    gerbers = sorted(
        (p for p in gerber_dir.iterdir() if p.is_file() and p.suffix not in (".gbrjob",)),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    assert gerbers, f"no gerbers produced in {gerber_dir}"
    largest = gerbers[0]

    out_dir = tmp_path / "pngs"
    out_dir.mkdir()
    r = kicad.run_python(
        "import kicad_native_gerber_export_png as gp\n"
        f"gp.run(gerber_paths=[{str(largest)!r}], output_dir={str(out_dir) + '/'!r}, dpi=150)"
    )
    assert_run_python_ok(r)  # the binding itself didn't crash / raise

    # Either the rasterization succeeded with a PNG, OR upstream complained
    # the gerber has no content — both prove the binding dispatched cleanly.
    pngs = [p for p in out_dir.iterdir() if p.suffix == ".png"]
    no_content = "no draw items" in r.result_repr
    assert pngs or no_content, (
        f"no PNGs in {out_dir} and no 'no draw items' explanation: {r.result_repr}"
    )
    assert_kicad_alive(kicad)


def test_pcb_import_module(kicad):
    """pcb_import binding loads and exposes run()."""
    r = kicad.run_python(
        "import kicad_native_pcb_import as pi; hasattr(pi, 'run')"
    )
    assert_run_python_ok(r)
    assert r.result_repr == "True"
    assert_kicad_alive(kicad)

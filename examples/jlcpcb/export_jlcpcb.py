"""JLCPCB Gerber + drill export — KliCAD reproduction of the official procedure.

Captures the export "algorithm" researched against JLCPCB's own help article
"How to generate Gerber and Drill files in KiCAD 9" (jlcpcb.com/help, updated
2025-10-30).  Drives a running KliCAD instance via the kipy IPC + the
kicad_native_* bindings.

What it does, in order:

  1. Load the target board into the PCB editor (open_board).  This is
     REQUIRED, not cosmetic: PCBNEW_JOBS_HANDLER::getBoard() ignores a
     job's m_filename in GUI mode and operates on whatever board the
     editor holds.  Loading it ourselves makes the export deterministic.
     (kicad_native_drc.run was fixed to do this internally; the
     export_gerbers / export_drill bindings still have the footgun, so
     this script compensates.)

  2. Run DRC.  Abort on any error-severity violation; print + continue on
     warnings.  JLCPCB's guide explicitly says "run DRC once more before
     generating the files."

  3. Export Gerbers with the JLCPCB-recommended settings:
       - Protel filename extensions      (binding default: no_protel_extension=False)
       - Extended X2 format              (binding default: no_x2=False)
       - Include netlist attributes      (binding default: no_netlist=False)
       - Board edge on every layer       (common_layers='Edge.Cuts')
       - Check zone fills before plot    (check_zones=True)
     Layer set is built from the board's copper-layer count: the 8 standard
     non-copper layers + F.Cu/B.Cu/Edge.Cuts + In1..InN.Cu for multilayer.

  4. Export drill data with the JLCPCB-recommended settings — all of which
     are already the kicad_native_export_drill binding defaults:
       - Excellon format, absolute origin, millimetres, decimal zeros,
         alternate oval-hole mode, PTH+NPTH merged into one file.

  5. Zip the output folder for upload.

  6. Print JLCPCB's verification checklist (outline watertight, drill
     alignment, silkscreen, etc.) — those checks need human eyes in a
     Gerber viewer.

Design-rule note: examples/jlcpcb/JLCPCB_2layer.kicad_dru in this directory
is a corrected JLCPCB rule set (cross-checked against jlcpcb.com/capabilities;
fixes the 0.075mm annular-ring and 0.127mm SMD-pad-to-pad errors found in the
community .kicad_dru lineage).  Copy it into your project as
<project>.kicad_dru before running DRC.

Usage:
    # KliCAD must already be running with the project's PCB editor reachable.
    python examples/jlcpcb/export_jlcpcb.py /path/to/board.kicad_pcb [output_dir]

    # output_dir defaults to <board-stem>-jlcpcb-export/ next to the board.
"""
from __future__ import annotations

import os
import shutil
import sys
import zipfile
from pathlib import Path

from kipy import KiCad


# The 8 non-copper layers every JLCPCB order needs, in the order JLCPCB's
# guide lists them.  Copper layers are inserted by build_layer_list().
_NONCOPPER_LAYERS = [
    "F.Paste", "B.Paste",
    "F.Silkscreen", "B.Silkscreen",
    "F.Mask", "B.Mask",
    "Edge.Cuts",
]

# Protel extensions JLCPCB expects, used to pick which files go in the zip.
_FAB_EXTENSIONS = (
    ".gtl", ".gbl", ".g1", ".g2", ".g3", ".g4",   # copper (top/bottom/inner)
    ".gtp", ".gbp",                                # paste
    ".gto", ".gbo",                                # silkscreen
    ".gts", ".gbs",                                # mask
    ".gm1",                                        # edge cuts
    ".drl",                                        # drill
)


def build_layer_list(copper_layer_count: int) -> str:
    """Comma-separated KiCad layer names for a board with N copper layers."""
    layers = ["F.Cu"]
    # Inner copper layers In1.Cu .. In(N-2).Cu
    for i in range(1, max(0, copper_layer_count - 2) + 1):
        layers.append(f"In{i}.Cu")
    layers.append("B.Cu")
    layers.extend(_NONCOPPER_LAYERS)
    return ",".join(layers)


def run(kicad: KiCad, board_path: str) -> dict:
    """Load board_path into the editor.  Returns its get_board_info() dict."""
    snippet = (
        "import kicad_native_gui as g; g.show_frame('pcb_editor')\n"
        "import kicad_native_pcb_state as ps\n"
        f"ps.open_board({board_path!r})\n"
        "ps.get_board_info()"
    )
    r = kicad.run_python(snippet)
    if not r.ok:
        raise RuntimeError(f"failed to load board: {r.exception_traceback}")
    import ast
    return ast.literal_eval(r.result_repr)


def run_drc(kicad: KiCad, board_path: str) -> list[dict]:
    """Run DRC; return the violation list.  Raises on dispatch failure."""
    r = kicad.run_python(
        "import kicad_native_drc as drc\n"
        f"result = drc.run({board_path!r}, severity='warning')\n"
        "result.get('report', {}).get('violations', [])"
    )
    if not r.ok:
        raise RuntimeError(f"DRC dispatch failed: {r.exception_traceback}")
    import ast
    return ast.literal_eval(r.result_repr)


def export_gerbers(kicad: KiCad, board_path: str, out_dir: str, layers: str) -> None:
    r = kicad.run_python(
        "import kicad_native_export_gerbers as gb\n"
        f"res = gb.run({board_path!r}, {out_dir!r},\n"
        f"             layers={layers!r},\n"
        "             common_layers='Edge.Cuts',\n"
        "             check_zones=True)\n"
        "res.get('ok', False)"
    )
    if not r.ok or r.result_repr != "True":
        raise RuntimeError(
            f"gerber export failed: {r.exception_traceback or r.result_repr}"
        )


def export_drill(kicad: KiCad, board_path: str, out_dir: str) -> None:
    # Every JLCPCB-recommended drill setting is already a binding default:
    # excellon / absolute origin / mm / decimal zeros / alternate oval /
    # PTH+NPTH merged.  No overrides needed.
    r = kicad.run_python(
        "import kicad_native_export_drill as dr\n"
        f"res = dr.run({board_path!r}, {out_dir!r})\n"
        "res.get('ok', False)"
    )
    if not r.ok or r.result_repr != "True":
        raise RuntimeError(
            f"drill export failed: {r.exception_traceback or r.result_repr}"
        )


def zip_output(out_dir: Path) -> Path:
    """Zip the fab files (flat, no directory entries) for upload."""
    zip_path = out_dir / f"{out_dir.name}.zip"
    fab_files = sorted(
        p for p in out_dir.iterdir()
        if p.suffix.lower() in _FAB_EXTENSIONS
    )
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in fab_files:
            zf.write(p, arcname=p.name)
    return zip_path


def export_jlcpcb(board_path: str, output_dir: str | None = None) -> Path:
    """Full JLCPCB export pipeline.  Returns the path to the upload zip.

    Raises RuntimeError if KliCAD is unreachable or DRC reports an
    error-severity violation.
    """
    board = Path(board_path).resolve()
    if not board.is_file():
        raise FileNotFoundError(board)

    out_dir = Path(output_dir) if output_dir else \
        board.parent / f"{board.stem}-jlcpcb-export"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)

    kicad = KiCad(timeout_ms=300_000)
    try:
        kicad.get_version()
    except Exception as e:
        raise RuntimeError(f"KliCAD not reachable: {e}") from None

    # 1. Load the board (deterministic — see module docstring).
    info = run(kicad, str(board))
    copper = info.get("copper_layer_count", 2)
    print(f"[1] board loaded: {board.name}  ({copper} copper layers)")

    # 2. DRC — abort on errors, continue on warnings.
    viols = run_drc(kicad, str(board))
    errors = [v for v in viols if v.get("severity") == "error"]
    warnings = [v for v in viols if v.get("severity") != "error"]
    print(f"[2] DRC: {len(errors)} error(s), {len(warnings)} warning(s)")
    for v in warnings:
        print(f"      warning: {v.get('type')}: {v.get('description','')}")
    if errors:
        for v in errors:
            print(f"      ERROR:   {v.get('type')}: {v.get('description','')}")
        raise RuntimeError(
            f"{len(errors)} DRC error(s) — fix before fabrication"
        )

    # 3. Gerbers.
    layers = build_layer_list(copper)
    export_gerbers(kicad, str(board), str(out_dir), layers)
    print(f"[3] Gerbers exported  (layers: {layers})")

    # 4. Drill.
    export_drill(kicad, str(board), str(out_dir))
    print(f"[4] drill exported")

    # 5. Zip.
    zip_path = zip_output(out_dir)
    n = len(zipfile.ZipFile(zip_path).namelist())
    print(f"[5] zipped: {zip_path}  ({n} files)")

    # 6. Verification reminder.
    print()
    print("Before uploading, open the Gerbers in a viewer and confirm:")
    print("  - board outline exists and is watertight (no gaps)")
    print("  - inner cutouts / slots / V-cuts present on the GM1 layer")
    print("  - drill holes aligned with pads across layers")
    print("  - vias covered/exposed as intended; silkscreen legible")

    return zip_path


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        print("ERROR: board path required", file=sys.stderr)
        return 2
    board_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None
    try:
        zip_path = export_jlcpcb(board_path, output_dir)
    except Exception as e:
        print(f"\nexport failed: {e}", file=sys.stderr)
        return 1
    print(f"\nready to upload: {zip_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

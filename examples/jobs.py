#!/usr/bin/env python3

# Copyright The KliCAD Developers
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the “Software”), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import argparse
from pathlib import Path

from klipy import KliCAD
from klipy.board import (
    Board3DFormat,
    BoardJobPaginationMode,
    DrillFormat,
    Ipc2581Version,
    OdbCompression,
    PositionFormat,
    PositionSide,
    RenderBackgroundStyle,
    RenderFormat,
    RenderQuality,
    RenderSide,
    StatsOutputFormat,
    Units,
)
from klipy.board_jobs import (
    Export3DSettings,
    Ipc2581ExportSettings,
    PlotSettings,
    PositionExportSettings,
    RenderSettings,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Board export jobs demo")
    parser.add_argument(
        "destination",
        help="Destination directory for exported files",
    )
    args = parser.parse_args()
    out = Path(args.destination).resolve()
    out.mkdir(parents=True, exist_ok=True)

    # Export jobs can take a while on larger boards.
    kicad = KliCAD(timeout_ms=30000)
    board = kicad.get_board()
    board_name = Path(board.name).stem or "board"

    enabled_layers = list(board.get_enabled_layers())

    plot = PlotSettings()
    plot.layers = enabled_layers
    plot.plot_drawing_sheet = True

    s3d = Export3DSettings()
    s3d.format = Board3DFormat.B3D_STEP
    s3d.export_board_body = True
    s3d.export_components = True

    render = RenderSettings()
    render.format = RenderFormat.RF_PNG
    render.quality = RenderQuality.RQ_BASIC
    render.background_style = RenderBackgroundStyle.RBS_OPAQUE
    render.side = RenderSide.RS_TOP
    render.width = 1600
    render.height = 1200

    position = PositionExportSettings()
    position.side = PositionSide.PS_BOTH
    position.units = Units.U_MM
    position.format = PositionFormat.PF_CSV
    position.single_file = True

    ipc2581 = Ipc2581ExportSettings()
    ipc2581.units = Units.U_MM
    ipc2581.version = Ipc2581Version.IPC2581V_C
    ipc2581.precision = 6

    jobs = [
        (
            "export_3d",
            lambda: board.export_3d(str(out / f"{board_name}.step"), settings=s3d),
        ),
        (
            "export_render",
            lambda: board.export_render(str(out / f"{board_name}-render.png"), settings=render),
        ),
        (
            "export_svg",
            lambda: board.export_svg(
                str(out / f"{board_name}.svg"),
                plot_settings=plot,
                page_mode=BoardJobPaginationMode.BJPM_ALL_LAYERS_ONE_PAGE,
            ),
        ),
        (
            "export_dxf",
            lambda: board.export_dxf(
                str(out / f"{board_name}.dxf"),
                plot_settings=plot,
                units=Units.U_MM,
                page_mode=BoardJobPaginationMode.BJPM_ALL_LAYERS_ONE_PAGE,
            ),
        ),
        (
            "export_pdf",
            lambda: board.export_pdf(
                str(out / f"{board_name}.pdf"),
                plot_settings=plot,
                include_metadata=True,
                single_document=True,
                page_mode=BoardJobPaginationMode.BJPM_ALL_LAYERS_ONE_PAGE,
            ),
        ),
        (
            "export_ps",
            lambda: board.export_ps(
                str(out / f"{board_name}.ps"),
                plot_settings=plot,
                page_mode=BoardJobPaginationMode.BJPM_ALL_LAYERS_ONE_PAGE,
            ),
        ),
        (
            "export_gerbers",
            lambda: board.export_gerbers(str(out / f"{board_name}-gerbers"), layers=enabled_layers),
        ),
        (
            "export_drill",
            lambda: board.export_drill(str(out / f"{board_name}-drill"), format=DrillFormat.DF_EXCELLON),
        ),
        (
            "export_position",
            lambda: board.export_position(str(out / f"{board_name}-position.csv"), settings=position),
        ),
        ("export_gencad", lambda: board.export_gencad(str(out / f"{board_name}.cad"))),
        (
            "export_ipc2581",
            lambda: board.export_ipc2581(str(out / f"{board_name}.xml"), settings=ipc2581),
        ),
        (
            "export_ipc_d356",
            lambda: board.export_ipc_d356(str(out / f"{board_name}.d356")),
        ),
        (
            "export_odb",
            lambda: board.export_odb(
                str(out / f"{board_name}.odb.zip"),
                units=Units.U_MM,
                precision=6,
                compression=OdbCompression.ODBC_ZIP,
            ),
        ),
        (
            "export_stats",
            lambda: board.export_stats(
                str(out / f"{board_name}-stats.txt"),
                format=StatsOutputFormat.SOF_REPORT,
                units=Units.U_MM,
            ),
        ),
    ]

    for name, fn in jobs:
        try:
            result = fn()
        except Exception as e:
            print(f"{name} failed: {e}")
            continue

        if not result.succeeded:
            print(f"{name} failed: {result.message!r}")
            continue

        for path in result.output_paths:
            print(Path(path).name)


if __name__ == "__main__":
    main()

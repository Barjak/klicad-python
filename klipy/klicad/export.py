"""Export proxy — gerbers, drill, 3D, BOM, netlist, SVG, schematic plot.

All exports are libkicommon Pattern A bindings backed by ``JOB_*`` classes
(the same code paths exercised by ``kicad-cli``).  Each method returns
the standard JOB result dict:
``{ok, exit_code, messages, output_paths}``.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ._runner import _PyRunner


class Export:
    """Export PCB / schematic / library artifacts via KliCAD's JOB layer.

    Methods accept the **most common** of each binding's many kwargs.
    Power users who need an obscure flag can drop down to
    ``k.run_python("import kicad_native_export_*; ...")`` directly.
    """

    def __init__(self, runner: _PyRunner) -> None:
        self._run = runner

    # ---- PCB exports ----------------------------------------------------

    def gerbers(self, board_path: str, output_dir: str, *,
                layers: Optional[List[str]] = None,
                use_board_plot_params: bool = False,
                include_border_title: bool = False,
                precision: int = 6,
                create_jobs_file: bool = False,
                **extra: Any) -> Dict[str, Any]:
        """Export Gerber files (``JOB_EXPORT_PCB_GERBERS``).

        :param output_dir: directory to write into (must exist).  Use a
            trailing slash if you want the binding to treat it as a dir.
        :param layers: layer canonical names; ``None`` = use plot params /
            sensible defaults.
        :param create_jobs_file: also emit a ``.gbrjob`` manifest.
        :param extra: any other gerber kwarg accepted by the binding
            (``exclude_value``, ``no_x2``, ``variant``, etc.).
        """
        return self._run.call(
            "kicad_native_export_gerbers", "run",
            board_path, output_dir,
            layers=layers,
            use_board_plot_params=use_board_plot_params,
            include_border_title=include_border_title,
            precision=precision,
            create_jobs_file=create_jobs_file,
            **extra,
        )

    def drill(self, board_path: str, output_dir: str, *,
              format: str = "excellon", drill_origin: str = "absolute",
              units: str = "mm", generate_map: bool = False,
              map_format: str = "pdf",
              **extra: Any) -> Dict[str, Any]:
        """Export drill files (``JOB_EXPORT_PCB_DRILL``).

        :param format: ``'excellon'`` (default) | ``'gerber'``.
        :param drill_origin: ``'absolute'`` | ``'plot'``.
        :param generate_map: also emit a drill map (in ``map_format``).
        """
        return self._run.call(
            "kicad_native_export_drill", "run",
            board_path, output_dir,
            format=format, drill_origin=drill_origin, units=units,
            generate_map=generate_map, map_format=map_format,
            **extra,
        )

    def step(self, board_path: str, output: str, *,
             overwrite: bool = True, include_dnp: bool = True,
             include_unspecified: bool = True,
             **extra: Any) -> Dict[str, Any]:
        """Convenience for STEP export.  Wraps :meth:`three_d` with
        ``format='step'``."""
        return self.three_d(
            board_path, output, format="step",
            overwrite=overwrite, include_dnp=include_dnp,
            include_unspecified=include_unspecified, **extra,
        )

    def three_d(self, board_path: str, output: str, *,
                format: str = "step", overwrite: bool = True,
                **extra: Any) -> Dict[str, Any]:
        """Export the board to a 3D model (``JOB_EXPORT_PCB_3D``).

        :param format: ``'step'`` (default) | ``'glb'`` | ``'brep'`` |
            ``'stl'`` | ``'vrml'`` | ``'ply'`` | ``'u3d'`` | ``'pdf'``.
        """
        return self._run.call(
            "kicad_native_export_3d", "run",
            board_path, output,
            format=format, overwrite=overwrite, **extra,
        )

    def render(self, board_path: str, output: str, *,
               format: str = "png", side: str = "top",
               width: int = 1024, height: int = 768,
               quality: str = "basic",
               **extra: Any) -> Dict[str, Any]:
        """Raytraced board render (``JOB_PCB_RENDER``).

        :param format: ``'png'`` (default) | ``'jpg'``.
        :param side: ``'top'`` | ``'bottom'`` | etc.
        :param quality: ``'basic'`` | ``'high'`` | ``'user'``.
        """
        return self._run.call(
            "kicad_native_render", "run",
            board_path, output,
            format=format, side=side, width=width, height=height,
            quality=quality, **extra,
        )

    # ---- Schematic exports ---------------------------------------------

    def sch_plot(self, schematic_path: str, output: str, *,
                 format: str = "pdf", black_and_white: bool = False,
                 pages: str = "",
                 **extra: Any) -> Dict[str, Any]:
        """Plot a schematic (``JOB_EXPORT_SCH_PLOT``).

        :param format: ``'pdf'`` (default) | ``'svg'`` | ``'dxf'`` | ``'ps'``.
        :param pages: page filter (empty = all).
        """
        return self._run.call(
            "kicad_native_export_sch_plot", "run",
            schematic_path, output,
            format=format, black_and_white=black_and_white, pages=pages,
            **extra,
        )

    def bom(self, schematic_path: str, output: str, *,
            bom_preset_name: str = "", bom_format_preset_name: str = "",
            fields_ordered: Optional[List[str]] = None,
            field_delimiter: str = ",",
            string_delimiter: str = '"',
            **extra: Any) -> Dict[str, Any]:
        """Export a BOM CSV (``JOB_EXPORT_SCH_BOM``).

        :param fields_ordered: explicit column list, e.g.
            ``['Reference','Value','Footprint','${QUANTITY}']``.  ``None``
            = let the preset decide.
        """
        return self._run.call(
            "kicad_native_export_sch_bom", "run",
            schematic_path, output,
            bom_preset_name=bom_preset_name,
            bom_format_preset_name=bom_format_preset_name,
            fields_ordered=fields_ordered,
            field_delimiter=field_delimiter,
            string_delimiter=string_delimiter,
            **extra,
        )

    def netlist(self, schematic_path: str, output: str, *,
                format: str = "kicad",
                **extra: Any) -> Dict[str, Any]:
        """Export a schematic netlist (``JOB_EXPORT_SCH_NETLIST``).

        :param format: ``'kicad'`` (default) | ``'xml'`` | ``'orcad'`` |
            ``'cadstar'`` | ``'pads'`` | ``'spice'`` | ``'spice-model'`` |
            ``'allegro'``.
        """
        return self._run.call(
            "kicad_native_export_sch_netlist", "run",
            schematic_path, output, format=format, **extra,
        )

    # ---- Library SVG ---------------------------------------------------

    def symbol_svg(self, library_path: str, output_dir: str, *,
                   symbol: str = "", black_and_white: bool = False,
                   include_hidden_pins: bool = False,
                   include_hidden_fields: bool = False,
                   color_theme: str = "") -> Dict[str, Any]:
        """Per-symbol SVG export (``JOB_SYM_EXPORT_SVG``).

        Empty ``symbol`` = export every symbol in the library.
        """
        return self._run.call(
            "kicad_native_sym_export_svg", "run",
            library_path, output_dir,
            symbol=symbol, black_and_white=black_and_white,
            include_hidden_pins=include_hidden_pins,
            include_hidden_fields=include_hidden_fields,
            color_theme=color_theme,
        )

    def footprint_svg(self, library_path: str, output_dir: str, *,
                      footprint: Optional[str] = None,
                      layers: Optional[List[str]] = None,
                      color_theme: str = "",
                      black_and_white: bool = False,
                      **extra: Any) -> Dict[str, Any]:
        """Per-footprint SVG export (``JOB_FP_EXPORT_SVG``).

        ``None`` ``footprint`` = export every footprint in the library.
        """
        return self._run.call(
            "kicad_native_fp_export_svg", "run",
            library_path, output_dir,
            footprint=footprint, layers=layers, color_theme=color_theme,
            black_and_white=black_and_white, **extra,
        )

    # ---- footprint / symbol library upgrades ---------------------------

    def upgrade_footprint_lib(self, library_path: str, *,
                              output_library_path: str = "",
                              force: bool = False) -> Dict[str, Any]:
        """In-place footprint-library format upgrade (``JOB_FP_UPGRADE``)."""
        return self._run.call(
            "kicad_native_fp_upgrade", "run", library_path,
            output_library_path=output_library_path, force=force,
        )

    def upgrade_symbol_lib(self, library_path: str, *,
                           output_library_path: str = "",
                           force: bool = False) -> Dict[str, Any]:
        """In-place ``.kicad_sym`` format upgrade (``JOB_SYM_UPGRADE``)."""
        return self._run.call(
            "kicad_native_sym_upgrade", "run", library_path,
            output_library_path=output_library_path, force=force,
        )

    def __repr__(self) -> str:
        return "<klicad.Export>"

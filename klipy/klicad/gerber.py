"""Gerber proxy — gerber-side diff / info / PNG raster + gerbview frame.

Composes:
  - ``kicad_native_gerber_diff`` — image diff between two gerbers.
  - ``kicad_native_gerber_info`` — metadata extraction.
  - ``kicad_native_gerber_export_png`` — gerber → PNG.
  - ``kicad_native_gerbview`` (Pattern B) — load / manage layers in the
    Gerbview frame.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ._runner import _PyRunner


class Gerber:
    """Gerber-file utilities + gerbview-frame controller.

    The static-file utilities (:meth:`diff`, :meth:`info`, :meth:`export_png`)
    are libkicommon Pattern A and don't require any frame.  The frame
    methods (:meth:`load`, :meth:`set_active_layer`, etc.) require gerbview
    to be open, which they ensure on first use.
    """

    def __init__(self, runner: _PyRunner) -> None:
        self._run = runner

    # ---- static file utilities ----------------------------------------

    def diff(self, reference_gerber: str, comparison_gerber: str, *,
             output_path: str = "", output_format: str = "png",
             dpi: int = 300, antialias: bool = True,
             transparent_background: bool = True,
             exit_code_only: bool = False, tolerance: int = 0,
             strict: bool = False, no_align: bool = False) -> Dict[str, Any]:
        """Compare two Gerber/Excellon files.

        :param output_format: ``'png'`` (default) | ``'text'`` | ``'json'``.
        :param output_path: where to write the diff output; empty = default
            (``<reference>-diff.png`` for PNG, stdout otherwise).
        """
        return self._run.call(
            "kicad_native_gerber_diff", "run",
            reference_gerber, comparison_gerber,
            output_path=output_path, output_format=output_format,
            dpi=dpi, antialias=antialias,
            transparent_background=transparent_background,
            exit_code_only=exit_code_only, tolerance=tolerance,
            strict=strict, no_align=no_align,
        )

    def info(self, gerber_path: str, *, output_format: str = "json",
             units: str = "mm", calculate_area: bool = False,
             strict: bool = False) -> Dict[str, Any]:
        """Inspect a Gerber/Excellon file's metadata.

        :param output_format: ``'json'`` (default) | ``'text'``.
        :param units: ``'mm'`` | ``'in'`` | ``'mils'``.
        :param calculate_area: include estimated copper area.
        """
        return self._run.call(
            "kicad_native_gerber_info", "run", gerber_path,
            output_format=output_format, units=units,
            calculate_area=calculate_area, strict=strict,
        )

    def export_png(self, gerber_paths: List[str], output_dir: str, *,
                   dpi: int = 300, width: int = 0, height: int = 0,
                   antialias: bool = True,
                   transparent_background: bool = True,
                   strict: bool = False, units: str = "mm",
                   foreground_color: str = "",
                   background_color: str = "",
                   **extra: Any) -> Dict[str, Any]:
        """Rasterize one or more Gerber/Excellon files to PNG (one per input).

        ``width``/``height`` > 0 override DPI.
        """
        return self._run.call(
            "kicad_native_gerber_export_png", "run",
            gerber_paths=gerber_paths, output_dir=output_dir,
            dpi=dpi, width=width, height=height, antialias=antialias,
            transparent_background=transparent_background,
            strict=strict, units=units,
            foreground_color=foreground_color,
            background_color=background_color,
            **extra,
        )

    # ---- gerbview frame ops --------------------------------------------

    def _ensure_frame(self) -> None:
        self._run.call("kicad_native_gui", "show_frame", "gerbview")

    def load(self, paths: List[str]) -> Dict[str, Any]:
        """Load a list of files into Gerbview, autodetecting each one's
        type (Gerber / drill / zip / job)."""
        self._ensure_frame()
        return self._run.call("kicad_native_gerbview", "load_autodetect",
                              paths=paths)

    def load_gerber_files(self, paths: List[str]) -> Dict[str, Any]:
        """Force-load each path as a Gerber file (one per draw layer)."""
        self._ensure_frame()
        return self._run.call("kicad_native_gerbview", "load_gerber_files",
                              paths=paths)

    def load_excellon_files(self, paths: List[str]) -> Dict[str, Any]:
        """Force-load each path as an Excellon (NC drill) file."""
        self._ensure_frame()
        return self._run.call("kicad_native_gerbview", "load_excellon_files",
                              paths=paths)

    def load_zip_archive(self, path: str) -> Dict[str, Any]:
        """Load a zip archive containing Gerber and/or drill files."""
        self._ensure_frame()
        return self._run.call("kicad_native_gerbview", "load_zip_archive", path)

    def load_gerber_job(self, path: str) -> Dict[str, Any]:
        """Load a Gerber job file (``.gbrjob``) plus the files it references."""
        self._ensure_frame()
        return self._run.call("kicad_native_gerbview", "load_gerber_job", path)

    def clear_all_layers(self) -> Dict[str, Any]:
        """Erase every loaded gerber/drill draw layer."""
        self._ensure_frame()
        return self._run.call("kicad_native_gerbview", "clear_all_layers")

    def clear_current_layer(self) -> Dict[str, Any]:
        """Erase only the currently-active draw layer."""
        self._ensure_frame()
        return self._run.call("kicad_native_gerbview", "clear_current_layer")

    def get_active_layer(self) -> int:
        """0-based index of the active layer."""
        self._ensure_frame()
        return int(self._run.call("kicad_native_gerbview", "get_active_layer"))

    def set_active_layer(self, layer: int) -> Dict[str, Any]:
        """Set the active layer (0-based index)."""
        self._ensure_frame()
        return self._run.call("kicad_native_gerbview", "set_active_layer", layer)

    def get_layer_count(self) -> int:
        """Number of layers with a loaded GERBER_FILE_IMAGE."""
        self._ensure_frame()
        return int(self._run.call("kicad_native_gerbview", "get_layer_count"))

    def list_loaded_files(self) -> List[Dict[str, Any]]:
        """Per-layer info for every loaded image."""
        self._ensure_frame()
        return list(self._run.call("kicad_native_gerbview", "list_loaded_files"))

    def __repr__(self) -> str:
        return "<klicad.Gerber>"

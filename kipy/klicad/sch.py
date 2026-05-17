"""Schematic (eeschema) proxy.

Composes three KliCAD bindings:
  - ``kicad_native_gui`` — to spawn the schematic frame on demand.
  - ``kicad_native_schematic_state`` — live SCHEMATIC CRUD (Pattern B).
  - ``kicad_native_sch_actions`` — TOOL_ACTION runner for the SCH frame
    (Pattern B).
  - ``kicad_native_annotation`` — annotate / clear / back-annotate.

Because Pattern B modules only become importable after the schematic
kiface has loaded once, :meth:`Schematic.open` and most CRUD methods
implicitly ensure the schematic frame is up before dispatching.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ._runner import _PyRunner


class Schematic:
    """High-level wrapper for the schematic editor (eeschema) subsystem.

    Methods that operate on schematic content (``add_wire``, ``annotate``,
    etc.) require a live schematic frame; this class spawns it on demand
    via ``kicad_native_gui.show_frame('schematic')``.

    File-format upgrades use ``kicad_native_sch_upgrade``, exposed via
    :meth:`upgrade`.
    """

    def __init__(self, runner: _PyRunner) -> None:
        self._run = runner

    # ---- frame lifecycle ------------------------------------------------

    def _ensure_frame(self) -> None:
        self._run.call("kicad_native_gui", "show_frame", "schematic")

    def open(self, schematic_path: Optional[str] = None) -> Dict[str, Any]:
        """Open the schematic editor, optionally pointing at a specific file.

        When ``schematic_path`` is omitted the editor opens with whatever
        project is currently active.  Passing a path runs the eeschema
        ``open`` tool action on it after spawning the frame.

        :returns: the ``show_frame`` result dict (with optional follow-up
            action status when ``schematic_path`` is given).
        """
        result = self._run.call("kicad_native_gui", "show_frame", "schematic")
        if schematic_path:
            # Best-effort: dispatch the file-open action.  Not all KiCad
            # builds expose a stable action name for "open file"; this is
            # the canonical eeschema action used by the menu item.
            self._run.call("kicad_native_sch_actions", "run_action",
                           "eeschema.EditorControl.openFile",
                           args={"filename": schematic_path})
        return result

    # ---- CRUD ----------------------------------------------------------

    def add_wire(self, start_x_mm: float, start_y_mm: float,
                 end_x_mm: float, end_y_mm: float) -> Dict[str, Any]:
        """Add a wire (SCH_LINE on LAYER_WIRE) between two points.

        Coordinates are in millimetres.  Returns ``{ok, kiid}``.
        """
        self._ensure_frame()
        return self._run.call("kicad_native_schematic_state", "add_wire",
                              start_x_mm, start_y_mm, end_x_mm, end_y_mm)

    def add_junction(self, x_mm: float, y_mm: float) -> Dict[str, Any]:
        """Add a SCH_JUNCTION at (x_mm, y_mm).  Returns ``{ok, kiid}``."""
        self._ensure_frame()
        return self._run.call("kicad_native_schematic_state", "add_junction",
                              x_mm, y_mm)

    def add_label(self, x_mm: float, y_mm: float, text: str,
                  *, kind: str = "local") -> Dict[str, Any]:
        """Add a label at (x_mm, y_mm).

        :param kind: ``'local'`` (SCH_LABEL, default) | ``'global'``
            (SCH_GLOBALLABEL) | ``'hierarchical'`` (SCH_HIERLABEL).
        :returns: ``{ok, kiid, kind}``.
        """
        self._ensure_frame()
        return self._run.call("kicad_native_schematic_state", "add_label",
                              x_mm, y_mm, text, kind=kind)

    def add_symbol(self, lib_id: str, ref_des: str,
                   x_mm: float, y_mm: float, *, unit: int = 1) -> Dict[str, Any]:
        """Add a SCH_SYMBOL by lib id.

        :param lib_id: ``'LibName:SymbolName'``.
        :param ref_des: reference designator (e.g. ``'R1'``); pass ``''`` to skip.
        :param unit: 1-based unit index for multi-unit packages.
        :returns: ``{ok, kiid, lib_id, ref_des, error?}``.
        """
        self._ensure_frame()
        return self._run.call("kicad_native_schematic_state", "add_symbol",
                              lib_id, ref_des, x_mm, y_mm, unit=unit)

    # ---- inspection ----------------------------------------------------

    def get_sheet_count(self) -> int:
        """Return the number of sheets in the schematic hierarchy."""
        self._ensure_frame()
        return int(self._run.call("kicad_native_schematic_state", "get_sheet_count"))

    def get_items_summary(self) -> Dict[str, int]:
        """Per-type item counts across every sheet (wires, symbols, junctions, ...)."""
        self._ensure_frame()
        return self._run.call("kicad_native_schematic_state", "get_items_summary")

    # ---- annotation ----------------------------------------------------

    def annotate(self, *, scope: str = "all", order: str = "x_then_y",
                 start_number: int = 0,
                 sort_by_first_letter: bool = False) -> Dict[str, Any]:
        """Annotate (assign reference designators to) all symbols.

        :param scope: ``'all'`` | ``'current_sheet'``.
        :param order: ``'x_then_y'`` (SORT_BY_X_POSITION) |
            ``'y_then_x'`` (SORT_BY_Y_POSITION).
        :param start_number: first refdes index to use; 0 = next available.
        :param sort_by_first_letter: group by ref-prefix first letter.
        """
        self._ensure_frame()
        return self._run.call(
            "kicad_native_annotation", "annotate",
            scope=scope, order=order, start_number=start_number,
            sort_by_first_letter=sort_by_first_letter,
        )

    def clear_annotation(self, *, scope: str = "all") -> Dict[str, Any]:
        """Clear reference designators from symbols.

        :param scope: ``'all'`` | ``'current_sheet'``.
        """
        self._ensure_frame()
        return self._run.call("kicad_native_annotation", "clear_annotation",
                              scope=scope)

    def annotation_summary(self) -> Dict[str, Any]:
        """Return ``{total_symbols, annotated, unannotated, next_refs_by_prefix}``."""
        self._ensure_frame()
        return self._run.call("kicad_native_annotation", "get_annotation_summary")

    def back_annotate_from_netlist(self, netlist_path: str) -> Dict[str, Any]:
        """Apply PCB->SCH back-annotation from a pcbnew-exported netlist."""
        self._ensure_frame()
        return self._run.call(
            "kicad_native_annotation", "back_annotate_from_netlist", netlist_path,
        )

    # ---- actions -------------------------------------------------------

    def list_actions(self) -> List[str]:
        """Return every TOOL_ACTION registered process-wide.

        Filter for ``a.startswith('eeschema.')`` to see schematic-side actions.
        """
        self._ensure_frame()
        return list(self._run.call("kicad_native_sch_actions", "list_actions"))

    def run_action(self, action_name: str,
                   *, args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Dispatch a schematic-side TOOL_ACTION by name.

        :param action_name: e.g. ``'eeschema.InteractiveSelection.selectAll'``.
        :param args: optional kwarg dict forwarded as the action's parameters.
        """
        self._ensure_frame()
        kwargs: Dict[str, Any] = {}
        if args is not None:
            kwargs["args"] = args
        return self._run.call("kicad_native_sch_actions", "run_action",
                              action_name, **kwargs)

    # ---- format upgrade (libkicommon, no frame needed) ------------------

    def upgrade(self, schematic_path: str, *, force: bool = False) -> Dict[str, Any]:
        """In-place upgrade of a ``.kicad_sch`` file to the current format.

        Wraps ``JOB_SCH_UPGRADE`` (``kicad-cli sch upgrade``).
        """
        return self._run.call("kicad_native_sch_upgrade", "run",
                              schematic_path, force=force)

    def __repr__(self) -> str:
        return "<klicad.Schematic>"

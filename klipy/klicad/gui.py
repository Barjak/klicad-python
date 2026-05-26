"""GUI control proxy.

Wraps ``kicad_native_gui`` (libkicommon Pattern A): spawn arbitrary
KliCAD frames by string name, list open frames, dismiss stray dialogs.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ._runner import _PyRunner


class GUI:
    """Programmatic GUI control: launch frames, dismiss dialogs.

    Frame names this binding accepts (full list via :meth:`list_frame_names`):

    - ``'schematic'`` / ``'schematic_editor'`` / ``'eeschema'``
    - ``'pcb_editor'`` / ``'pcbnew'``
    - ``'footprint_editor'``
    - ``'symbol_editor'``
    - ``'gerbview'``
    - ``'page_layout'``
    - ``'calculator'``
    - ``'3d_viewer'``           (needs an open PCB first)
    - ``'simulator'``           (needs an open schematic first)
    - ``'symbol_viewer'`` / ``'symbol_chooser'`` / etc.
    """

    def __init__(self, runner: _PyRunner) -> None:
        self._run = runner

    def show_frame(self, name: str, *, raise_to_front: bool = True) -> Dict[str, Any]:
        """Spawn (if needed) and raise a KliCAD frame by name.

        :returns: ``{ok, name, frame_id, raised, title, error?}``.
        """
        return self._run.call("kicad_native_gui", "show_frame", name,
                              raise_to_front=raise_to_front)

    def list_frame_names(self) -> List[str]:
        """Return every string accepted by :meth:`show_frame`."""
        return list(self._run.call("kicad_native_gui", "list_frame_names"))

    def list_open_frames(self) -> List[Dict[str, Any]]:
        """Describe every currently-open top-level window in the KliCAD
        process (class, title, is_shown, is_kiway_*)."""
        return list(self._run.call("kicad_native_gui", "list_open_frames"))

    def dismiss_dialogs(self, *, title_substr: str = "") -> Dict[str, Any]:
        """Programmatically dismiss every wxDialog currently open.

        :param title_substr: if non-empty, only dismiss dialogs whose title
            contains this substring (case-sensitive).
        :returns: ``{count, titles}``.
        """
        return self._run.call("kicad_native_gui", "dismiss_dialogs",
                              title_substr=title_substr)

    def __repr__(self) -> str:
        return "<klicad.GUI>"

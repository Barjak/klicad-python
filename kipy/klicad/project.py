"""Project manager + COMMON_SETTINGS proxy.

Composes:
  - ``kicad_native_project_manager`` (Pattern A): load/save/archive
    projects via SETTINGS_MANAGER + PROJECT_ARCHIVER.
  - ``kicad_native_settings`` (Pattern A): typed get/set on
    COMMON_SETTINGS via dotted JSON paths.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ._runner import _PyRunner


class Project:
    """Project lifecycle and KiCad-wide settings.

    Settings methods (:meth:`get_setting` etc.) target ``kicad_common.json``
    — global preferences, not per-project ones.
    """

    def __init__(self, runner: _PyRunner) -> None:
        self._run = runner

    # ---- project lifecycle ---------------------------------------------

    def current(self) -> Optional[Dict[str, Any]]:
        """The currently-active project, or ``None`` when none is loaded.

        Returns ``{full_name, path, name, is_readonly, is_null}``.
        """
        return self._run.call("kicad_native_project_manager",
                              "get_current_project")

    def list_open(self) -> List[Dict[str, Any]]:
        """All projects SETTINGS_MANAGER currently tracks."""
        return list(self._run.call("kicad_native_project_manager",
                                   "list_open_projects"))

    def load(self, path: str, *, set_active: bool = True) -> Dict[str, Any]:
        """Load a ``.kicad_pro`` file via SETTINGS_MANAGER::LoadProject.

        :param set_active: also make the loaded project the active one.
        """
        return self._run.call("kicad_native_project_manager", "load_project",
                              path, set_active=set_active)

    def unload(self, path: str = "") -> Dict[str, Any]:
        """Save and unload a project (empty ``path`` = the active one)."""
        return self._run.call("kicad_native_project_manager", "unload_project",
                              path=path)

    def save(self, path: str = "") -> Dict[str, Any]:
        """Save a loaded project (empty ``path`` = active project)."""
        return self._run.call("kicad_native_project_manager", "save_project",
                              path=path)

    def save_as(self, new_path: str, *, source_path: str = "") -> Dict[str, Any]:
        """Rename + save the active (or named) project to ``new_path``."""
        return self._run.call("kicad_native_project_manager", "save_project_as",
                              new_path, source_path=source_path)

    def save_copy(self, new_path: str) -> Dict[str, Any]:
        """Save a copy of the active project to ``new_path`` without
        changing its identity."""
        return self._run.call("kicad_native_project_manager", "save_project_copy",
                              new_path)

    def archive(self, archive_path: str, *, source_dir: str = "") -> Dict[str, Any]:
        """Zip a project directory via PROJECT_ARCHIVER::Archive.

        Empty ``source_dir`` = the active project's directory.
        """
        return self._run.call("kicad_native_project_manager", "archive",
                              archive_path, source_dir=source_dir)

    def unarchive(self, archive_path: str, dest_dir: str) -> Dict[str, Any]:
        """Extract a project zip via PROJECT_ARCHIVER::Unarchive.

        WARNING: overwrites files in ``dest_dir``.
        """
        return self._run.call("kicad_native_project_manager", "unarchive",
                              archive_path, dest_dir)

    def is_open(self) -> bool:
        """True if any project is loaded (including the dummy)."""
        return bool(self._run.call("kicad_native_project_manager",
                                   "is_project_open"))

    def is_open_real(self) -> bool:
        """True if a real (non-dummy / non-null) project is active."""
        return bool(self._run.call("kicad_native_project_manager",
                                   "is_project_open_not_dummy"))

    # ---- settings ------------------------------------------------------

    def get_setting(self, setting_path: str) -> Any:
        """Read a value from COMMON_SETTINGS at a dotted JSON path.

        Example::

            k.project.get_setting('system.local_history_debounce')  # -> 5
            k.project.get_setting('api.enable_server')              # -> True
        """
        return self._run.call("kicad_native_settings", "get", setting_path)

    def set_setting(self, setting_path: str, value: Any) -> Any:
        """Write a value into COMMON_SETTINGS and persist to disk.

        Example::

            k.project.set_setting('system.local_history_debounce', 0)
        """
        return self._run.call("kicad_native_settings", "set",
                              setting_path, value)

    def dump_settings(self, prefix: str = "") -> Dict[str, Any]:
        """Dump COMMON_SETTINGS as a nested dict.

        Empty ``prefix`` returns the whole document; otherwise that subtree.
        """
        return self._run.call("kicad_native_settings", "dump", prefix=prefix)

    def save_settings(self) -> Dict[str, Any]:
        """Flush all registered SETTINGS_MANAGER settings to disk."""
        return self._run.call("kicad_native_settings", "save")

    def __repr__(self) -> str:
        return "<klicad.Project>"

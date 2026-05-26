"""Library proxy.

Composes three KliCAD library-related bindings:
  - ``kicad_native_library_tables`` (Pattern A): sym/fp lib-table CRUD,
    env-var helpers.
  - ``kicad_native_symbol_editor`` (Pattern B): live LIB_SYMBOL CRUD
    via SYMBOL_EDIT_FRAME.
  - ``kicad_native_footprint_editor`` (Pattern B): FOOTPRINT CRUD
    via FOOTPRINT_EDIT_FRAME.

The :class:`Library` façade exposes the Pattern A surface directly
and sub-objects :attr:`symbols` / :attr:`footprints` for the editor-
backed surface.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ._runner import _PyRunner


class SymbolEditor:
    """Live editor-backed view of the global symbol libraries.

    Spawns the symbol editor frame (``show_frame('symbol_editor')``) on
    first use; required because this binding is kiface-resident.
    """

    def __init__(self, runner: _PyRunner) -> None:
        self._run = runner

    def _ensure_frame(self) -> None:
        self._run.call("kicad_native_gui", "show_frame", "symbol_editor")

    def list_loaded_libraries(self) -> List[str]:
        """Names of libraries currently visible to the symbol editor."""
        self._ensure_frame()
        return list(self._run.call("kicad_native_symbol_editor",
                                   "list_loaded_libraries"))

    def list_symbols_in_library(self, library_name: str) -> List[str]:
        """Symbol names within a given library."""
        self._ensure_frame()
        return list(self._run.call(
            "kicad_native_symbol_editor", "list_symbols_in_library",
            library_name))

    def get_symbol_info(self, library_name: str, symbol_name: str) -> Dict[str, Any]:
        """Metadata about a single library symbol."""
        self._ensure_frame()
        return self._run.call(
            "kicad_native_symbol_editor", "get_symbol_info",
            library_name, symbol_name)

    def list_pins(self, library_name: str, symbol_name: str) -> List[Dict[str, Any]]:
        """Per-pin metadata for a symbol."""
        self._ensure_frame()
        return list(self._run.call(
            "kicad_native_symbol_editor", "list_pins",
            library_name, symbol_name))

    def load_symbol(self, library_name: str, symbol_name: str) -> Dict[str, Any]:
        """Open a library symbol for editing (becomes the current symbol)."""
        self._ensure_frame()
        return self._run.call(
            "kicad_native_symbol_editor", "load_symbol",
            library_name, symbol_name)

    def create_symbol(self, library_name: str, symbol_name: str,
                      **kwargs: Any) -> Dict[str, Any]:
        """Create a new symbol in the given library.

        Forwards any extra kwargs (e.g. ``reference``, ``description``,
        ``keywords``, ``inherit_from``) verbatim to the binding.
        """
        self._ensure_frame()
        return self._run.call(
            "kicad_native_symbol_editor", "create_symbol",
            library_name, symbol_name, **kwargs)

    def save_current(self) -> Dict[str, Any]:
        """Save the currently-loaded symbol."""
        self._ensure_frame()
        return self._run.call("kicad_native_symbol_editor", "save_current")

    def save_all(self) -> Dict[str, Any]:
        """Save every modified symbol library."""
        self._ensure_frame()
        return self._run.call("kicad_native_symbol_editor", "save_all")

    def delete_symbol(self, library_name: str, symbol_name: str) -> Dict[str, Any]:
        """Delete a symbol from a library."""
        self._ensure_frame()
        return self._run.call(
            "kicad_native_symbol_editor", "delete_symbol",
            library_name, symbol_name)

    def __repr__(self) -> str:
        return "<klicad.SymbolEditor>"


class FootprintEditor:
    """Live editor-backed view of the global footprint libraries."""

    def __init__(self, runner: _PyRunner) -> None:
        self._run = runner

    def _ensure_frame(self) -> None:
        self._run.call("kicad_native_gui", "show_frame", "footprint_editor")

    def list_loaded_libraries(self) -> List[str]:
        """Names of footprint libraries currently visible."""
        self._ensure_frame()
        return list(self._run.call("kicad_native_footprint_editor",
                                   "list_loaded_libraries"))

    def list_footprints_in_library(self, library_name: str) -> List[str]:
        """Footprint names within a library."""
        self._ensure_frame()
        return list(self._run.call(
            "kicad_native_footprint_editor", "list_footprints_in_library",
            library_name))

    def get_footprint_info(self, library_name: str, footprint_name: str) -> Dict[str, Any]:
        """Metadata for a single library footprint."""
        self._ensure_frame()
        return self._run.call(
            "kicad_native_footprint_editor", "get_footprint_info",
            library_name, footprint_name)

    def list_pads(self, library_name: str, footprint_name: str) -> List[Dict[str, Any]]:
        """Per-pad metadata for a footprint."""
        self._ensure_frame()
        return list(self._run.call(
            "kicad_native_footprint_editor", "list_pads",
            library_name, footprint_name))

    def load_footprint(self, library_name: str, footprint_name: str) -> Dict[str, Any]:
        """Open a footprint for editing."""
        self._ensure_frame()
        return self._run.call(
            "kicad_native_footprint_editor", "load_footprint",
            library_name, footprint_name)

    def save_current(self) -> Dict[str, Any]:
        """Save the currently-loaded footprint."""
        self._ensure_frame()
        return self._run.call("kicad_native_footprint_editor", "save_current")

    def save_all(self) -> Dict[str, Any]:
        """Save every modified footprint library."""
        self._ensure_frame()
        return self._run.call("kicad_native_footprint_editor", "save_all")

    def delete_footprint(self, library_name: str, footprint_name: str) -> Dict[str, Any]:
        """Delete a footprint from a library."""
        self._ensure_frame()
        return self._run.call(
            "kicad_native_footprint_editor", "delete_footprint",
            library_name, footprint_name)

    def __repr__(self) -> str:
        return "<klicad.FootprintEditor>"


class Library:
    """Library-table CRUD façade.

    Direct methods (``list_symbol_libs`` etc.) wrap the Pattern A
    ``kicad_native_library_tables`` binding — no editor frame required.
    The :attr:`symbols` and :attr:`footprints` attributes expose the
    editor-backed sub-proxies for deeper CRUD on individual symbols /
    footprints.
    """

    def __init__(self, runner: _PyRunner) -> None:
        self._run = runner
        self.symbols = SymbolEditor(runner)
        self.footprints = FootprintEditor(runner)

    # ---- listing --------------------------------------------------------

    def list_symbol_libs(self, *, scope: str = "all") -> List[Dict[str, Any]]:
        """List symbol-library rows.

        :param scope: ``'global'`` | ``'project'`` | ``'all'``.
            Project scope requires an open project.
        """
        return list(self._run.call("kicad_native_library_tables",
                                   "list_symbol_libs", scope=scope))

    def list_footprint_libs(self, *, scope: str = "all") -> List[Dict[str, Any]]:
        """List footprint-library rows.  Same scope rules as
        :meth:`list_symbol_libs`."""
        return list(self._run.call("kicad_native_library_tables",
                                   "list_footprint_libs", scope=scope))

    # ---- mutation -------------------------------------------------------

    def add_symbol_lib(self, name: str, uri: str, type: str,
                       *, description: str = "", options: str = "",
                       enabled: bool = True, visible: bool = True,
                       scope: str = "global") -> Dict[str, Any]:
        """Append a symbol-library row.

        :param type: plugin name, e.g. ``'KliCAD'`` / ``'Legacy'``.
        :param scope: ``'global'`` (default) or ``'project'``.
        """
        return self._run.call(
            "kicad_native_library_tables", "add_symbol_lib",
            name, uri, type,
            description=description, options=options,
            enabled=enabled, visible=visible, scope=scope,
        )

    def add_footprint_lib(self, name: str, uri: str, type: str,
                          *, description: str = "", options: str = "",
                          enabled: bool = True, visible: bool = True,
                          scope: str = "global") -> Dict[str, Any]:
        """Append a footprint-library row.  Args mirror :meth:`add_symbol_lib`."""
        return self._run.call(
            "kicad_native_library_tables", "add_footprint_lib",
            name, uri, type,
            description=description, options=options,
            enabled=enabled, visible=visible, scope=scope,
        )

    def remove_symbol_lib(self, name: str, *, scope: str = "global") -> Dict[str, Any]:
        """Remove a symbol-library row by name."""
        return self._run.call(
            "kicad_native_library_tables", "remove_symbol_lib",
            name, scope=scope)

    def remove_footprint_lib(self, name: str, *, scope: str = "global") -> Dict[str, Any]:
        """Remove a footprint-library row by name."""
        return self._run.call(
            "kicad_native_library_tables", "remove_footprint_lib",
            name, scope=scope)

    # ---- env vars / paths ----------------------------------------------

    def resolve_path(self, path_with_vars: str) -> str:
        """Expand ``${VAR}`` substitutions in a path (project + system vars)."""
        return str(self._run.call(
            "kicad_native_library_tables", "resolve_path", path_with_vars))

    def list_env_vars(self) -> Dict[str, str]:
        """KliCAD-known environment variables (defined + predefined)."""
        return self._run.call("kicad_native_library_tables", "list_env_vars")

    def save(self) -> Dict[str, Any]:
        """Persist every live library table (symbol + footprint, all scopes)."""
        return self._run.call("kicad_native_library_tables", "save")

    def __repr__(self) -> str:
        return "<klicad.Library>"

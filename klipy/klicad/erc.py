"""ERC (Electrical Rule Check) proxy."""

from __future__ import annotations

from typing import Any, Dict

from ._runner import _PyRunner


class ERC:
    """Run KliCAD's schematic Electrical Rule Check on a .kicad_sch file.

    Wraps ``kicad_native_erc`` (libkicommon Pattern A).  Dispatches through
    ``JOB_SCH_ERC`` — the same job invoked by ``kicad-cli sch erc``.
    """

    def __init__(self, runner: _PyRunner) -> None:
        self._run = runner

    def run(
        self,
        schematic_path: str,
        *,
        units: str = "mm",
        severity: str = "warning",
    ) -> Dict[str, Any]:
        """Run ERC on a schematic and return the structured report dict.

        :param schematic_path: absolute path to the ``.kicad_sch`` file.
        :param units: ``'mm'`` | ``'in'`` | ``'mils'``.
        :param severity: minimum severity to include
            (``'error'`` | ``'warning'`` | ``'exclusion'``).
        :returns: ``{ok, exit_code, messages, report_path, json_text, report}``.
            Shape matches :meth:`klicad.DRC.run`.
        """
        return self._run.call(
            "kicad_native_erc", "run", schematic_path,
            units=units, severity=severity,
        )

    def violations(self, schematic_path: str, **kwargs: Any) -> list:
        """Convenience: run ERC and return just the violations list."""
        result = self.run(schematic_path, **kwargs)
        return result.get("report", {}).get("violations", []) or []

    def __repr__(self) -> str:
        return "<klicad.ERC>"

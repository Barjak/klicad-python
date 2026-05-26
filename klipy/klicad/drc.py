"""DRC (Design Rule Check) proxy."""

from __future__ import annotations

from typing import Any, Dict

from ._runner import _PyRunner


class DRC:
    """Run KliCAD's PCB Design Rule Check on a .kicad_pcb file.

    Wraps ``kicad_native_drc`` (libkicommon Pattern A).  All checks dispatch
    through ``JOB_PCB_DRC`` — the same job invoked by ``kicad-cli pcb drc``.

    Typical usage::

        violations = k.drc.run('/path/to/board.kicad_pcb')
        for v in violations['report']['violations']:
            print(v['severity'], v['type'], v['description'])
    """

    def __init__(self, runner: _PyRunner) -> None:
        self._run = runner

    def run(
        self,
        board_path: str,
        *,
        units: str = "mm",
        severity: str = "warning",
        all_track_errors: bool = False,
        schematic_parity: bool = False,
        refill_zones: bool = False,
    ) -> Dict[str, Any]:
        """Run DRC on a board and return the structured report dict.

        :param board_path: absolute path to the ``.kicad_pcb`` file.
        :param units: ``'mm'`` | ``'in'`` | ``'mils'``.
        :param severity: minimum severity to include
            (``'error'`` | ``'warning'`` | ``'exclusion'``).
        :param all_track_errors: report every track-vs-X violation, not just
            the first per pair.
        :param schematic_parity: also check that the board matches the
            schematic (requires the schematic to be present).
        :param refill_zones: pour zones before checking (slower).
        :returns: ``{ok, exit_code, messages, report_path, json_text, report}``.
            ``report`` is the parsed JSON violation list.
        """
        return self._run.call(
            "kicad_native_drc", "run", board_path,
            units=units, severity=severity,
            all_track_errors=all_track_errors,
            schematic_parity=schematic_parity,
            refill_zones=refill_zones,
        )

    def violations(self, board_path: str, **kwargs: Any) -> list:
        """Convenience: run DRC and return just the list of violations.

        Equivalent to ``self.run(board_path, **kwargs)['report']['violations']``.
        Forwards all kwargs to :meth:`run`.
        """
        result = self.run(board_path, **kwargs)
        return result.get("report", {}).get("violations", []) or []

    def __repr__(self) -> str:
        return "<klicad.DRC>"

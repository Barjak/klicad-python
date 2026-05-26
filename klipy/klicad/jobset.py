"""Jobset proxy — parse + run ``.kicad_jobset`` files.

Wraps ``kicad_native_jobset`` (libkicommon Pattern A): the umbrella
runner for JOBSET + JOB_REGISTRY.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ._runner import _PyRunner


class Jobset:
    """Load and execute KliCAD ``.kicad_jobset`` files.

    A jobset bundles one or more jobs (gerbers, drill, BOM, ...) with
    one or more destination configurations.  :meth:`load` returns the
    parsed structure without executing; :meth:`run` dispatches the
    runner.
    """

    def __init__(self, runner: _PyRunner) -> None:
        self._run = runner

    def load(self, jobset_path: str) -> Dict[str, Any]:
        """Parse a ``.kicad_jobset`` file (no execution).

        :returns: ``{ok, file, jobs, destinations}`` — ``jobs`` and
            ``destinations`` are per-entry dicts.  See the binding's
            docstring for full key descriptions.
        """
        return self._run.call("kicad_native_jobset", "load", jobset_path)

    def run(self, jobset_path: str, *,
            destinations: Optional[List[str]] = None,
            stop_on_error: bool = False) -> Dict[str, Any]:
        """Execute a ``.kicad_jobset`` via JOBS_RUNNER.

        :param destinations: subset of destination uuids/descriptions to
            run; ``None`` (default) = all destinations.
        :param stop_on_error: abort the runner after the first failed job.
        """
        return self._run.call(
            "kicad_native_jobset", "run", jobset_path,
            destinations=destinations, stop_on_error=stop_on_error,
        )

    def __repr__(self) -> str:
        return "<klicad.Jobset>"

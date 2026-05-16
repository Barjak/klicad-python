"""
KliCAD test fixtures.

Assumes a fresh-build KliCAD is running externally — does NOT launch or
kill KiCad.  Tests share one client connection per session for speed.

Key fixture: ``no_kicad_crashes`` is session-scoped and snapshots the
macOS crash-report directory on entry, then on session teardown asserts
that no new crash report appeared.  Run pytest with ``-x`` and you'll
catch the first binding that crashes KiCad.

To skip the crash check (e.g. while iterating on a binding known to
crash today): mark the test ``@pytest.mark.expected_crash``.  That
test's crash will be recorded but won't fail the session-end check.
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

import pytest

# Ensure the local kicad-python fork takes precedence over any pip install.
_FORK_ROOT = Path(__file__).resolve().parents[1]
if str(_FORK_ROOT) not in sys.path:
    sys.path.insert(0, str(_FORK_ROOT))

from kipy import KiCad
from kipy.errors import ConnectionError as KipyConnectionError


SWITCH_PROJECT_DIR = Path("/Users/shopnew/switchfiles/switch")
SWITCH_PCB = SWITCH_PROJECT_DIR / "switch.kicad_pcb"
SWITCH_SCH = SWITCH_PROJECT_DIR / "switch.kicad_sch"
INSTALL_DIR = Path("/Users/shopnew/kicad-build/install/KiCad.app")
CRASH_DIR = Path.home() / "Library/Logs/DiagnosticReports"


def _snapshot_crashes() -> set[str]:
    """List of crash-report filenames mentioning 'kicad' at this moment."""
    if not CRASH_DIR.is_dir():
        return set()
    return {f.name for f in CRASH_DIR.iterdir() if "kicad" in f.name.lower()}


# --- session-scoped fixtures ----------------------------------------------

@pytest.fixture(scope="session")
def kicad() -> KiCad:
    """Single shared KiCad client for the whole session.

    Skips the entire test run if KiCad isn't running (no socket, or socket
    refuses connection).  Uses a 60-second timeout because some operations
    (3D export, rendering) genuinely take that long.
    """
    try:
        # 5 minutes — 3D STEP export and full-quality renders can genuinely
        # take >60s on first invocation (OCC + raytracer warm-up).  Cost of
        # the higher ceiling is paid only when something actually hangs.
        k = KiCad(timeout_ms=300_000)
        # Smoke probe so we fail before any test runs if the connection is bad.
        k.get_version()
    except (KipyConnectionError, OSError) as exc:
        pytest.skip(
            f"KiCad isn't reachable on /tmp/kicad/api.sock: {exc}.  "
            "Launch KiCad and rerun."
        )
        raise  # appease type checkers
    return k


@pytest.fixture(scope="session", autouse=True)
def _crash_baseline(request) -> None:
    """Snapshot crash-report dir at session start; at teardown, fail if any
    new crash report appeared that wasn't marked expected_crash."""
    baseline = _snapshot_crashes()
    expected_crash_count = {"value": 0}

    # Stash on session config so tests can register expected crashes.
    request.session.klicad_baseline_crashes = baseline
    request.session.klicad_expected_crashes = expected_crash_count

    yield

    current = _snapshot_crashes()
    new = sorted(current - baseline)
    if not new:
        return

    expected = expected_crash_count["value"]
    if len(new) <= expected:
        # Each expected_crash test bumps the counter; tolerate up to that.
        return

    msg = [
        f"{len(new)} new KiCad crash report(s) since session start "
        f"({expected} expected from @expected_crash tests):",
    ]
    for name in new:
        msg.append(f"  - {CRASH_DIR / name}")
    pytest.fail("\n".join(msg))


# --- function-scoped fixtures ---------------------------------------------

@pytest.fixture
def expected_crash(request):
    """Mark a test as expected to crash KiCad.  Crash will be counted in
    the session-end check but won't fail the suite."""
    request.session.klicad_expected_crashes["value"] += 1
    yield


@pytest.fixture
def switch_project_copy(tmp_path: Path) -> Path:
    """Function-scoped temp copy of the switch project directory.

    Excludes ``~*.lck`` files and ``.history/`` so we don't conflict
    with the live KiCad's open project.
    Returns the path to the copied directory.
    """
    dst = tmp_path / "switch"
    shutil.copytree(
        SWITCH_PROJECT_DIR,
        dst,
        ignore=lambda d, names: [n for n in names if n.startswith("~") or n == ".history"],
    )
    return dst


@pytest.fixture
def demo_sym_lib() -> Path:
    """A demo .kicad_sym shipped with our install."""
    p = INSTALL_DIR / "../demos/kit-dev-coldfire-xilinx_5213/kit-dev-coldfire-xilinx_5213.kicad_sym"
    p = p.resolve()
    if not p.is_file():
        pytest.skip(f"missing demo sym lib: {p}")
    return p


@pytest.fixture
def demo_fp_lib() -> Path:
    """A demo .pretty footprint library shipped with our install."""
    p = INSTALL_DIR / "../demos/cm5_minima/CM5IO.pretty"
    p = p.resolve()
    if not p.is_dir():
        pytest.skip(f"missing demo fp lib: {p}")
    return p


# --- helpers tests can import directly ------------------------------------

def assert_run_python_ok(result, *, allow_stderr: bool = False) -> None:
    """Common assertion: run_python result was clean."""
    assert result.ok, (
        f"run_python raised:\n{result.exception_traceback}\n"
        f"stdout: {result.stdout!r}\n"
        f"stderr: {result.stderr!r}"
    )
    if not allow_stderr:
        assert not result.stderr, f"unexpected stderr: {result.stderr!r}"


def assert_kicad_alive(kicad: KiCad) -> None:
    """Verify KiCad still responds.  Use this after any test that might
    have crashed it — fast follow-up assertion catches death immediately
    instead of waiting for session-end."""
    try:
        kicad.get_version()
    except (KipyConnectionError, OSError) as exc:
        pytest.fail(f"KiCad appears dead after operation: {exc}")

"""End-to-end smoke test for the kicad-cli netlist wrapper."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from klipy.circuit import Circuit, R
from klipy.circuit._netlist import to_netlist
from klipy.errors import ConnectionError as KipyConnectionError


_BUILD_CLI = Path.home() / "projects" / "KliCAD" / "build" / "kicad" / "kicad-cli"


def _kicad_cli_available() -> bool:
    return shutil.which("kicad-cli") is not None or _BUILD_CLI.exists()


@pytest.mark.skipif(
    not _kicad_cli_available(),
    reason="kicad-cli not available",
)
def test_to_netlist_trivial_circuit():
    c = Circuit(name="netlist_smoke")
    c.add(R("R1", "A", "B", value="1k"))

    try:
        text = to_netlist(c)
    except KipyConnectionError as e:
        pytest.skip(f"KliCAD IPC unreachable: {e}")
    except RuntimeError as e:
        # to_schematic() needs live KliCAD for placement; skip if unreachable.
        if "KliCAD" in str(e) or "IPC" in str(e):
            pytest.skip(f"KliCAD IPC unreachable: {e}")
        raise

    assert text, "expected non-empty netlist text"
    assert "(export" in text, f"missing (export header:\n{text[:500]}"
    assert "(comp" in text, f"missing (comp record:\n{text[:500]}"

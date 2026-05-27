"""End-to-end smoke test for the kicad-cli netlist wrapper."""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from klipy.circuit import Circuit, R, C, V
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


@pytest.mark.skipif(
    not _kicad_cli_available(),
    reason="kicad-cli not available",
)
def test_to_netlist_three_parts():
    """Build R+C+V circuit; assert each ref appears in (comp ...) and a net."""
    c = Circuit(name="three_parts", strict=False)
    c.add(V("V1", "IN", "GND", dc=5))
    c.add(R("R1", "IN", "OUT", value="1k"))
    c.add(C("C1", "OUT", "GND", value="1u"))

    try:
        netlist = to_netlist(c)
    except KipyConnectionError as e:
        pytest.skip(f"KliCAD IPC unreachable: {e}")
    except RuntimeError as e:
        if "KliCAD" in str(e) or "IPC" in str(e):
            pytest.skip(f"KliCAD IPC unreachable: {e}")
        raise

    # Substring asserts only — DO NOT parse.  kicad-cli quotes attribute
    # values, so accept either quoted or bare ref form.
    for ref in ("V1", "R1", "C1"):
        assert (f'(comp (ref "{ref}")' in netlist
                or f"(comp (ref {ref})" in netlist), \
            f"missing (comp (ref {ref}) in netlist:\n{netlist[:500]}"
        # Each ref must appear at least twice: once in (comp ...) and once
        # in a (net ...) membership entry.
        assert netlist.count(ref) >= 2, \
            f"ref {ref} appears <2 times (expected comp + net):\n{netlist[:500]}"


@pytest.mark.skipif(
    not _kicad_cli_available(),
    reason="kicad-cli not available",
)
def test_circuit_to_netlist_method_matches_free_function():
    """Circuit.to_netlist() is a thin wrapper — must match the free function."""
    c = Circuit(name="netlist_method_smoke")
    c.add(R("R1", "A", "B", value="1k"))

    try:
        via_method = c.to_netlist()
        via_func = to_netlist(c)
    except KipyConnectionError as e:
        pytest.skip(f"KliCAD IPC unreachable: {e}")
    except RuntimeError as e:
        if "KliCAD" in str(e) or "IPC" in str(e):
            pytest.skip(f"KliCAD IPC unreachable: {e}")
        raise

    assert via_method, "expected non-empty netlist from method"
    assert via_func, "expected non-empty netlist from free function"
    # The netlist body (component refs, nets) must be present in both.
    # kicad-cli may stamp a timestamp / source-path header that varies
    # between calls, so check that the core structural markers + the
    # part ref appear in both rather than demanding byte-for-byte equality.
    for marker in ("(export", "(comp", "R1"):
        assert marker in via_method, f"method missing {marker!r}:\n{via_method[:500]}"
        assert marker in via_func, f"free fn missing {marker!r}:\n{via_func[:500]}"

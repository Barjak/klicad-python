"""C.6: to_schematic feeds the netlist into klicad_native_ratsnest.set_spec.

These tests cover the Python-side wiring only; the actual C++ ratsnest
behaviour is C.7's responsibility.

  * test_ratsnest_spec_key_present_on_live_emit — full-stack smoke test;
    skips if KliCAD IPC or kicad-cli is unavailable.  Just asserts the
    return dict carries a ``ratsnest_spec`` key (value may be None when
    the C.5 binding isn't built yet, that's the soft-fail path).

  * test_module_not_found_is_soft_failure — pure-python unit test with a
    fake kicad client.  Simulates the ``ModuleNotFoundError`` that the
    C.5-less KliCAD will throw and asserts to_schematic still returns
    cleanly with ``ratsnest_spec is None`` and no exception escaping.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

from klipy.circuit import Circuit, R
from klipy.circuit import _klicad_sch as ksch


_BUILD_CLI = Path.home() / "projects" / "KliCAD" / "build" / "kicad" / "kicad-cli"


def _kicad_cli_available() -> bool:
    return shutil.which("kicad-cli") is not None or _BUILD_CLI.exists()


# ──────────────────────────────────────────────────────────────────────────
# Live emit test (skips if env unavailable)
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.skipif(
    not _kicad_cli_available(),
    reason="kicad-cli not available",
)
def test_ratsnest_spec_key_present_on_live_emit(tmp_path: Path):
    """A successful to_schematic must surface ratsnest_spec in its dict.

    Doesn't care whether the value is a dict (C.5 binding present) or
    None (binding missing / soft-fail) — just that the key exists so
    callers can inspect it.
    """
    from klipy import KliCAD
    from klipy.errors import ConnectionError as KipyConnectionError

    try:
        kicad = KliCAD(timeout_ms=5_000)
        kicad.get_version()
    except (KipyConnectionError, OSError) as e:
        pytest.skip(f"KliCAD IPC unreachable: {e}")

    c = Circuit(name="ratsnest_spec_smoke")
    c.add(R("R1", "A", "B", value="1k"))

    sch = tmp_path / "ratsnest_spec_smoke.kicad_sch"
    try:
        result = c.to_schematic(str(sch), kicad=kicad)
    except RuntimeError as e:
        # IPC-bound placement may still fail e.g. on a stale GUI state;
        # skip rather than fail because this isn't what we're testing.
        if "KliCAD" in str(e) or "IPC" in str(e) or "open_schematic" in str(e):
            pytest.skip(f"to_schematic IPC failure: {e}")
        raise

    assert "ratsnest_spec" in result, (
        f"to_schematic result missing 'ratsnest_spec' key; got keys: "
        f"{sorted(result)}"
    )
    spec = result["ratsnest_spec"]
    # Either the binding ran and returned the contract dict, or it
    # wasn't built yet and we soft-failed to None.  Both are acceptable
    # here; C.7 will tighten the schema check.
    if spec is not None:
        assert isinstance(spec, dict), f"expected dict or None, got {type(spec)}"
        for key in ("ok", "edges_placed", "nets_resolved", "missing_pins"):
            assert key in spec, f"contract key {key!r} missing from {spec!r}"


# ──────────────────────────────────────────────────────────────────────────
# ModuleNotFoundError soft-fail (pure unit test)
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class _FakeRunPythonResult:
    ok: bool
    result_repr: str = ""
    exception_traceback: str = ""
    stdout: str = ""
    stderr: str = ""


def test_module_not_found_is_soft_failure(tmp_path, monkeypatch, capsys):
    """If klicad_native_ratsnest doesn't exist, _push_ratsnest_spec must
    return None and emit a single stderr warning — no exception."""

    class _FakeKicad:
        pass

    # Bypass the netlist export — pretend it returned text.
    monkeypatch.setattr(
        "klipy.circuit._klicad_sch._push_ratsnest_spec",
        ksch._push_ratsnest_spec,  # use the real function
    )
    # The function imports netlist_from_sch lazily; patch it on the
    # _netlist module.
    monkeypatch.setattr(
        "klipy.circuit._netlist.netlist_from_sch",
        lambda _p: "(export (version E))\n",
    )

    calls = {"n": 0}

    def fake_run_python(self_or_code, code=None):
        # _FakeKicad's bound run_python is called like `kicad.run_python(snippet)`;
        # monkeypatching as a function on the instance means self isn't
        # passed.  Be defensive in case it is.
        calls["n"] += 1
        return _FakeRunPythonResult(
            ok=False,
            exception_traceback=(
                'Traceback (most recent call last):\n'
                '  File "<string>", line 1, in <module>\n'
                "ModuleNotFoundError: No module named 'klicad_native_ratsnest'\n"
            ),
        )

    fake = _FakeKicad()
    fake.run_python = fake_run_python

    # Need a real schematic file so netlist_from_sch's existence check
    # would pass if reached — but we monkeypatched that out anyway.
    sch = tmp_path / "fake.kicad_sch"
    sch.write_text("(kicad_sch)\n")

    c = Circuit(name="fake_for_ratsnest")
    c.add(R("R1", "A", "B", value="1k"))

    # Call the hook directly — exercises the soft-fail branch without
    # needing live IPC for the placement step.
    spec = ksch._push_ratsnest_spec(c, fake, sch)
    assert spec is None, f"expected None on ModuleNotFoundError, got {spec!r}"
    assert calls["n"] == 1, f"expected exactly one run_python call, saw {calls['n']}"

    captured = capsys.readouterr()
    assert "ratsnest" in captured.err.lower(), (
        f"expected stderr warning about ratsnest; got: {captured.err!r}"
    )
    # Single-line warning contract — newline count tells us it's one log.
    assert captured.err.count("\n") == 1, (
        f"expected one-line warning, got:\n{captured.err}"
    )


def test_netlist_export_failure_is_soft_failure(tmp_path, monkeypatch, capsys):
    """If kicad-cli (or any other piece of netlist generation) fails,
    we must also soft-fail — to_schematic's primary product is the
    schematic file, not the ratsnest spec."""

    def boom(_p):
        raise RuntimeError("kicad-cli not found")

    monkeypatch.setattr("klipy.circuit._netlist.netlist_from_sch", boom)

    class _FakeKicad:
        def run_python(self, code):
            raise AssertionError(
                "run_python should not be called when netlist export fails"
            )

    sch = tmp_path / "fake.kicad_sch"
    sch.write_text("(kicad_sch)\n")
    c = Circuit(name="fake_for_ratsnest_netfail")
    c.add(R("R1", "A", "B", value="1k"))

    spec = ksch._push_ratsnest_spec(c, _FakeKicad(), sch)
    assert spec is None

    captured = capsys.readouterr()
    assert "ratsnest" in captured.err.lower()
    assert "netlist" in captured.err.lower()

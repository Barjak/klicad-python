"""Safety net for KliCAD.

Two kinds of tests:

1. **Boot/protocol smoke** — the embedded-Python pipe itself is healthy:
   run_python round-trips, state persists across calls, exceptions are
   captured cleanly (not swallowed or crashing).

2. **Import-each-binding** — every kicad_native_* module imports inside
   the embedded interpreter without crashing.  This catches stale
   bindings whose underlying C++ deps moved.  Does NOT call any
   subsystem operation (those live in test_klicad_bindings.py).
"""

import pytest

from conftest import assert_kicad_alive, assert_run_python_ok


# Every binding TU defines one PYBIND11_EMBEDDED_MODULE; this is the list.
ALL_BINDING_MODULES = [
    "kicad_native",
    "kicad_native_drc",
    "kicad_native_erc",
    "kicad_native_export_3d",
    "kicad_native_export_drill",
    "kicad_native_export_gerbers",
    "kicad_native_export_sch_bom",
    "kicad_native_export_sch_netlist",
    "kicad_native_export_sch_plot",
    "kicad_native_fp_export_svg",
    "kicad_native_fp_upgrade",
    "kicad_native_gerber_diff",
    "kicad_native_jobset",
    "kicad_native_pcb_upgrade",
    "kicad_native_render",
    "kicad_native_sch_upgrade",
    "kicad_native_sym_export_svg",
    "kicad_native_sym_upgrade",
]


# ---- boot / protocol ----

def test_get_version(kicad):
    """KiCad responds to the typed-RPC get_version command."""
    v = kicad.get_version()
    assert v is not None
    assert str(v).startswith("10."), f"unexpected version: {v}"
    assert_kicad_alive(kicad)


def test_run_python_trivial(kicad):
    """RunPython with a trivial expression returns ok + result_repr."""
    r = kicad.run_python("1 + 1")
    assert_run_python_ok(r)
    assert r.result_repr == "2", r
    assert_kicad_alive(kicad)


def test_run_python_stdout(kicad):
    """print() output is captured in stdout."""
    r = kicad.run_python("print('hello')")
    assert_run_python_ok(r)
    assert r.stdout == "hello\n", r


def test_run_python_state_persists(kicad):
    """Variables defined in one call survive to the next (shared __main__)."""
    kicad.run_python("__klicad_test_marker = 'persisted-' + str(7 * 6)")
    r = kicad.run_python("__klicad_test_marker")
    assert_run_python_ok(r)
    assert r.result_repr == "'persisted-42'", r
    # cleanup so this test doesn't leak state into other tests
    kicad.run_python("del __klicad_test_marker")


def test_run_python_exception_captured(kicad):
    """Python exceptions don't crash KiCad — they come back as ok=False + traceback."""
    r = kicad.run_python("1 / 0")
    assert not r.ok
    assert "ZeroDivisionError" in r.exception_traceback
    assert_kicad_alive(kicad)  # critical: no crash from an exception


def test_run_python_syntax_error(kicad):
    """Even syntactically broken code is captured, not crashed."""
    r = kicad.run_python("def broken(:\n")
    assert not r.ok
    assert "SyntaxError" in r.exception_traceback
    assert_kicad_alive(kicad)


def test_kicad_native_echo(kicad):
    """The smoke-probe binding in kicad_native still works (canary for
    embedded interp + pybind11 lifecycle)."""
    r = kicad.run_python(
        "import kicad_native; kicad_native.echo('round-trip')"
    )
    assert_run_python_ok(r)
    assert r.result_repr == "'round-trip'"


def test_kicad_native_version_binding(kicad):
    """kicad_native.version() calls real C++ code (GetMajorMinorPatchVersion)."""
    r = kicad.run_python("import kicad_native; kicad_native.version()")
    assert_run_python_ok(r)
    # Should be a quoted M.N.P string
    assert r.result_repr.startswith("'10."), r.result_repr


# ---- per-binding import-and-list-dir smoke ----

@pytest.mark.parametrize("module_name", ALL_BINDING_MODULES)
def test_binding_importable(kicad, module_name):
    """Each binding module imports cleanly inside the embedded interpreter.

    Doesn't invoke its operations — just verifies the module is registered
    via PYBIND11_EMBEDDED_MODULE and loads without ImportError.  Catches
    stale bindings whose underlying headers changed.
    """
    code = f"import {module_name}; {module_name!r} in repr({module_name})"
    r = kicad.run_python(code)
    assert_run_python_ok(r)
    assert r.result_repr == "True", r
    assert_kicad_alive(kicad)


@pytest.mark.parametrize("module_name", ALL_BINDING_MODULES)
def test_binding_has_callable(kicad, module_name):
    """Every binding module exposes at least one callable (its `run`
    function, or `load`+`run` for jobset)."""
    code = (
        f"import {module_name}\n"
        f"[name for name in dir({module_name}) "
        f"if not name.startswith('_') and callable(getattr({module_name}, name))]"
    )
    r = kicad.run_python(code)
    assert_run_python_ok(r)
    # result_repr is the repr of a list — should contain at least one entry
    assert r.result_repr != "[]", f"{module_name} has no callable surface"

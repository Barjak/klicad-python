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
    "kicad_native_design_blocks",
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
    "kicad_native_gerber_export_png",
    "kicad_native_gerber_info",
    "kicad_native_gui",
    "kicad_native_io_discovery",
    "kicad_native_jobset",
    "kicad_native_kiway_events",
    "kicad_native_library_tables",
    "kicad_native_local_history",
    "kicad_native_pcb_calculator",
    "kicad_native_project_manager",
    "kicad_native_pcb_import",
    "kicad_native_pcb_upgrade",
    "kicad_native_pcm",
    "kicad_native_render",
    "kicad_native_sch_upgrade",
    "kicad_native_settings",
    "kicad_native_sym_export_svg",
    "kicad_native_sym_upgrade",
]

# Kiface-resident modules (Pattern B in BINDING_PATTERN.md).  These only
# become importable after the relevant editor kiface has been loaded.
KIFACE_RESIDENT_MODULES = [
    ("kicad_native_annotation",       "schematic"),
    ("kicad_native_sch_actions",      "schematic"),
    ("kicad_native_schematic_state",  "schematic"),
    ("kicad_native_symbol_editor",    "symbol_editor"),
    ("kicad_native_simulator",        "simulator"),
    ("kicad_native_3d_resolver",      "pcb_editor"),
    ("kicad_native_drc_rules",        "pcb_editor"),
    ("kicad_native_pcb_state",        "pcb_editor"),
    ("kicad_native_pcb_actions",      "pcb_editor"),
    ("kicad_native_stackup",          "pcb_editor"),
    ("kicad_native_footprint_editor", "footprint_editor"),
    ("kicad_native_gerbview",         "gerbview"),
    ("kicad_native_pagelayout",       "page_layout"),
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


# ---- kiface-resident binding lifecycle (Pattern B) ----

@pytest.mark.parametrize("module_name,kiface_frame", KIFACE_RESIDENT_MODULES)
def test_kiface_binding_appears_after_load(kicad, module_name, kiface_frame):
    """Kiface-resident bindings register lazily on kiface load.

    Pattern B contract:
      1. Module isn't importable before the kiface is loaded.
      2. show_frame(<editor>) triggers kiface load → register_on_load hook
         creates the module and inserts into sys.modules.
      3. Module is importable after.

    Doesn't assert (1) strictly — if an earlier test spawned the editor
    the module is already there, which is fine. The load step is
    idempotent.
    """
    # Trigger the kiface (idempotent — Player(true) is a no-op if already up)
    r = kicad.run_python(
        f"import kicad_native_gui as g; g.show_frame({kiface_frame!r})"
    )
    assert_run_python_ok(r)
    assert "'ok': True" in r.result_repr, r.result_repr

    # Module is now importable
    r = kicad.run_python(
        f"import {module_name}; "
        f"[name for name in dir({module_name}) if not name.startswith('_')]"
    )
    assert_run_python_ok(r)
    assert r.result_repr != "[]", f"{module_name} has no surface after kiface load"
    assert_kicad_alive(kicad)

"""Phase A acceptance test for klipy.circuit.

Verifies:
  1. The Python DSL builds a Circuit object without errors.
  2. Validation rejects malformed circuits (duplicate ref, dangling pin,
     ic on undeclared net).
  3. to_dict()/from_dict() round-trips losslessly.
  4. to_spice_deck() produces a deck that ngspice accepts via the live
     KliCAD simulator binding.
  5. The simulated LED oscillator actually oscillates.

Requires a running KliCAD instance for (4) and (5); the other tests are pure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from klipy.circuit import (
    Circuit,
    R, C, NPN, LED, V, D, L, XSubckt,
    ModelCard,
    Tran,
    STANDARD_MODEL_LIB,
)


# ---- shared fixture: the LED oscillator we'll build everywhere ---------

def build_led_oscillator() -> Circuit:
    """Canonical 2-transistor astable, parameterised by nothing."""
    c = Circuit(
        name="LED Oscillator",
        desc="2-transistor astable multivibrator driving an LED",
    )
    c.add_model_lib(STANDARD_MODEL_LIB)

    c.add(V("V1", "VCC", "GND", dc=5))
    c.add(R("R1", "VCC", "NL",    value="1k"))
    c.add(R("R2", "VCC", "BL",    value="47k"))
    c.add(R("R3", "VCC", "BR",    value="47k"))
    c.add(R("R4", "VCC", "LED_A", value="1k"))
    c.add(C("C1", "NL", "BR",     value="10u"))
    c.add(C("C2", "NR", "BL",     value="10u"))
    c.add(NPN("Q1", c="NL", b="BL", e="GND", model="2N3904"))
    c.add(NPN("Q2", c="NR", b="BR", e="GND", model="2N3904"))
    c.add(LED("D1", a="LED_A", k="NR"))

    c.ic(NL=5, NR=0, BL=0.7, BR=0)
    c.analysis(Tran(step="1ms", stop="3s", uic=True))
    return c


# ---- pure tests (no KliCAD needed) --------------------------------------

def test_build_circuit_no_errors():
    c = build_led_oscillator()
    # 10 parts, 7 distinct nets (VCC, GND, NL, NR, BL, BR, LED_A), 1 analysis
    assert len(c.parts) == 10
    assert len(c.nets) == 7
    assert set(c.nets) == {"VCC", "GND", "NL", "NR", "BL", "BR", "LED_A"}
    assert c.nets["VCC"].kind == "power"
    assert c.nets["GND"].kind == "ground"
    assert c.nets["NL"].kind == "signal"


def test_validation_rejects_duplicate_ref():
    c = Circuit("t")
    c.add(R("R1", "A", "B", "1k"))
    with pytest.raises(ValueError, match="duplicate ref"):
        c.add(R("R1", "C", "D", "2k"))


def test_validation_rejects_dangling_pin():
    with pytest.raises(ValueError, match="unconnected"):
        R("R1", "", "B", "1k").validate()


def test_validation_rejects_ic_on_undeclared_net():
    c = Circuit("t")
    c.add(R("R1", "A", "B", "1k"))
    with pytest.raises(ValueError, match="no such net"):
        c.ic(ZZZ=5)


def test_warns_on_orphan_net():
    c = Circuit("t")
    c.add(R("R1", "A", "B", "1k"))  # B used only once after this
    c.add(R("R2", "A", "C", "1k"))  # A used twice, B and C each used once
    warnings = c.validate_all()
    # Both B and C are referenced only once each — both should warn
    assert any("B" in w for w in warnings), warnings
    assert any("C" in w for w in warnings), warnings


def test_roundtrip_to_dict_from_dict():
    c1 = build_led_oscillator()
    d = c1.to_dict()
    c2 = Circuit.from_dict(d)
    assert c1 == c2
    assert c2.to_dict() == d  # idempotent


def test_spice_deck_well_formed():
    c = build_led_oscillator()
    deck = c.to_spice_deck()
    # Title line
    assert deck.splitlines()[0].startswith("* LED Oscillator")
    # Has the .include for the model lib
    assert ".include " in deck and "standard.lib" in deck
    # All parts present
    for ref in ("V1", "R1", "R2", "R3", "R4", "C1", "C2", "Q1", "Q2", "D1"):
        # Element line starts with the ref (which by KliCAD convention
        # already begins with the SPICE element letter, e.g. "R1 ...")
        assert any(line.startswith(f"{ref} ") for line in deck.splitlines()), \
            f"missing {ref}: {deck}"
    # IC line
    assert ".ic " in deck
    # .control / tran / .endc / .end
    assert ".control" in deck and "tran 1ms 3s uic" in deck
    assert ".endc" in deck and ".end" in deck
    # GND was rewritten to 0
    assert " 0 " in deck or " 0\n" in deck
    # No stray "GND" tokens left
    for line in deck.splitlines():
        if line.startswith(("*", ".include", ".control", ".endc", ".end")):
            continue
        if not line:
            continue
        assert "GND" not in line, f"unrewritten GND in element line: {line!r}"


# ---- inline .model cards + .SUBCKT instantiation -----------------------

def _flyback_circuit() -> Circuit:
    """Solenoid + freewheel diode using an inline .model card."""
    c = Circuit(name="Flyback", desc="solenoid with inline-modelled clamp")
    c.add_model("DFW", "D", IS="1e-9", N=1.7, RS=0.02, BV=600)
    c.add(V("V1", "RAIL", "GND", dc=24))
    c.add(L("L1", "RAIL", "SW", value="50m"))
    c.add(R("R1", "SW", "GND", value="5"))            # crude switch stand-in
    c.add(D("D1", a="SW", k="RAIL", model="DFW"))
    c.analysis(Tran(step="1us", stop="5ms"))
    return c


def test_inline_model_card_emitted():
    c = _flyback_circuit()
    deck = c.to_spice_deck()
    lines = deck.splitlines()
    # .model line present, well-formed, ahead of the element lines.
    model_idx = next(i for i, ln in enumerate(lines)
                     if ln.startswith(".model DFW D ("))
    assert "BV=600" in lines[model_idx] and "RS=0.02" in lines[model_idx]
    first_elem = next(i for i, ln in enumerate(lines) if ln.startswith("D1 "))
    assert model_idx < first_elem, "the .model card must precede the element lines"
    # The diode element references the model by name.
    assert any(ln.startswith("D1 ") and ln.endswith("DFW") for ln in lines)


def test_add_model_rejects_duplicate():
    c = Circuit(name="dup")
    c.add_model("DZ", "D", BV=15)
    with pytest.raises(ValueError, match="already defined"):
        c.add_model("DZ", "D", BV=20)


def test_xsubckt_emitted_as_x_element():
    c = Circuit(name="TVS clamp")
    c.add_model_lib(STANDARD_MODEL_LIB)        # any existing file, just for the .include
    c.add(V("V1", "RAIL", "GND", dc=24))
    c.add(L("L1", "RAIL", "SW", value="50m"))
    c.add(R("R1", "SW", "GND", value="5"))
    # KliCAD-style ref 'D1' -> SPICE subckt call 'XD1'.
    c.add(XSubckt("D1", ["SW", "RAIL"], subckt="SMAJ24CA"))
    deck = c.to_spice_deck()
    lines = deck.splitlines()
    assert any(ln == "XD1 SW RAIL SMAJ24CA" for ln in lines), deck
    # No bare 'D1 ...' diode line — the subckt must not be emitted as a diode.
    assert not any(ln.startswith("D1 ") for ln in lines)


def test_xsubckt_ground_rewrite():
    """Ground nets inside an X line are still rewritten to 0."""
    c = Circuit(name="g")
    c.add(V("V1", "A", "GND", dc=5))
    c.add(XSubckt("U1", ["A", "GND"], subckt="SOMECKT"))
    deck = c.to_spice_deck()
    assert "XU1 A 0 SOMECKT" in deck


def test_part_library_field_included():
    """A per-part .library file gets its own .include line."""
    c = Circuit(name="L")
    c.add(V("V1", "A", "GND", dc=5))
    d = D("D1", a="A", k="GND", model="CUSTOM")
    d.library = STANDARD_MODEL_LIB
    c.add(d)
    deck = c.to_spice_deck()
    assert f".include {STANDARD_MODEL_LIB}" in deck


def test_roundtrip_with_models_and_xsubckt():
    c1 = _flyback_circuit()
    c1.add_model_lib("/tmp/some_vendor.lib")
    c1.add(XSubckt("D2", ["SW", "RAIL"], subckt="SMAJ30CA"))
    c2 = Circuit.from_dict(c1.to_dict())
    assert c1 == c2
    assert c2.to_dict() == c1.to_dict()           # idempotent
    # The reconstructed XSubckt still emits a correct X line.
    assert "XD2 SW RAIL SMAJ30CA" in c2.to_spice_deck()


def test_modelcard_roundtrip():
    m = ModelCard("DZ", "D", {"BV": "15", "RS": "2"})
    assert ModelCard.from_dict(m.to_dict()) == m
    assert m.spice_line() == ".model DZ D (BV=15 RS=2)"


# ---- XSubckt KliCAD-symbol binding (per-instance) -----------------------

def test_xsubckt_schematic_binding_optional():
    """XSubckt still works without a KliCAD-symbol binding (SPICE-only mode)."""
    x = XSubckt("D1", ["A", "K"], subckt="SMF54A")
    assert x.kicad_lib_id == ""
    assert not hasattr(x, "kicad_pin_map") or not x.kicad_pin_map
    assert x.spice_line() == "XD1 A K SMF54A"


def test_xsubckt_schematic_binding_accepted():
    """XSubckt with kicad_lib_id + kicad_pin_map stores the binding."""
    x = XSubckt(
        "Q1", ["D", "G", "S"], subckt="DO5T10BA",
        kicad_lib_id="Device:Q_NMOS",
        kicad_pin_map={"1": "D", "2": "G", "3": "S"},
    )
    assert x.kicad_lib_id == "Device:Q_NMOS"
    assert x.kicad_pin_map == {"1": "D", "2": "G", "3": "S"}
    assert x.spice_line() == "XQ1 D G S DO5T10BA"


def test_xsubckt_pinmap_without_libid_rejected():
    with pytest.raises(ValueError, match="kicad_pin_map without kicad_lib_id"):
        XSubckt("D1", ["A", "K"], subckt="SMF54A",
                kicad_pin_map={"1": "2", "2": "1"})


def test_xsubckt_libid_without_pinmap_rejected():
    with pytest.raises(ValueError, match="requires kicad_pin_map"):
        XSubckt("Q1", ["D", "G", "S"], subckt="DO5T10BA",
                kicad_lib_id="Device:Q_NMOS")


def test_xsubckt_pinmap_incomplete_rejected():
    with pytest.raises(ValueError, match="missing entries for"):
        XSubckt("Q1", ["D", "G", "S"], subckt="DO5T10BA",
                kicad_lib_id="Device:Q_NMOS",
                kicad_pin_map={"1": "D", "2": "G"})  # missing "3"


def test_xsubckt_schematic_binding_roundtrip():
    """to_dict/from_dict preserves kicad_lib_id + kicad_pin_map."""
    c1 = Circuit(name="t", desc="")
    c1.add(XSubckt(
        "Q1", ["D", "G", "S"], subckt="DO5T10BA",
        kicad_lib_id="Device:Q_NMOS",
        kicad_pin_map={"1": "D", "2": "G", "3": "S"},
    ))
    c2 = Circuit.from_dict(c1.to_dict())
    q = next(p for p in c2.parts if p.ref == "Q1")
    assert q.kicad_lib_id == "Device:Q_NMOS"
    assert q.kicad_pin_map == {"1": "D", "2": "G", "3": "S"}
    assert q.spice_line() == "XQ1 D G S DO5T10BA"


def test_to_schematic_rejects_bare_xsubckt():
    """Without kicad_lib_id, schematic placement raises NotImplementedError
    (the runtime path that prior code already exercised, plus a clearer
    message)."""
    from klipy.circuit._klicad_sch import _place_parts
    c = Circuit(name="t", desc="")
    c.add(XSubckt("U1", ["A", "B"], subckt="UNDEF"))
    # Don't need a live KliCAD to hit the early raise.
    with pytest.raises(NotImplementedError, match="no KliCAD symbol binding"):
        _place_parts(c, kicad=None, models_lib_path=Path("/tmp/x.lib"))


def test_to_schematic_snippet_for_xsubckt(monkeypatch, tmp_path):
    """With a kicad_lib_id + kicad_pin_map, _place_parts generates the
    expected schematic-authoring snippet for the XSubckt.

    Uses a FakeKiCad that captures every run_python call instead of
    sending it to a live KliCAD; that lets us inspect the generated
    code without requiring a running session.
    """
    from klipy.circuit._klicad_sch import _place_parts

    c = Circuit(name="t", desc="")
    c.add(XSubckt(
        "Q1", ["DRAIN", "GATE", "SOURCE"], subckt="DO5T10BA",
        kicad_lib_id="Device:Q_NMOS",
        kicad_pin_map={"1": "D", "2": "G", "3": "S"},
    ))
    c.add(XSubckt(
        "D1", ["GND", "DRAIN"], subckt="SMF54A",
        kicad_lib_id="Device:D",
        kicad_pin_map={"1": "2", "2": "1"},
    ))

    class FakeKiCad:
        def __init__(self):
            self.calls = []
            self.next_kiid = iter(range(1, 100))

        def run_python(self, snippet: str):
            self.calls.append(snippet)
            kiid = next(self.next_kiid)
            class R:
                ok = True
                result_repr = repr(f"kiid_{kiid}")
            return R()

    fake = FakeKiCad()
    models_lib = tmp_path / "models.lib"
    models_lib.write_text("")

    placed = _place_parts(c, kicad=fake, models_lib_path=models_lib)

    assert set(placed) == {"Q1", "D1"}
    # Each part snippet contains the right add_symbol + Sim.Name + Sim.Type
    snippets = "\n".join(fake.calls)
    assert "'Device:Q_NMOS'" in snippets
    assert "'Q1'" in snippets
    assert "'DO5T10BA'" in snippets        # Sim.Name = subckt name
    assert "'SUBCKT'" in snippets           # Sim.Type
    assert "'Device:D'" in snippets
    assert "'SMF54A'" in snippets
    # Sim.Pins encodes the kicad_pin → spice_position mapping
    assert "D=1" in snippets and "G=2" in snippets and "S=3" in snippets
    assert "2=1" in snippets and "1=2" in snippets  # D1 reversed-pin TVS


# ---- c.tran property + .tran emitted from to_spice_deck() --------------

def test_c_tran_setter_appends_to_analyses():
    c = Circuit("t")
    c.add(R("R1", "A", "B", "1k"))
    c.add(R("R2", "B", "0", "1k"))
    assert c.tran is None
    c.tran = Tran(step="1u", stop="10m")
    assert c.tran is not None
    assert c.tran.step == "1u"
    assert c.tran.stop == "10m"
    # And the analysis must end up in the deck
    deck = c.to_spice_deck()
    assert ".control" in deck
    assert "tran 1u 10m" in deck.lower()


def test_c_tran_setter_replaces_prior_tran():
    """Assigning c.tran a second time strips the first one."""
    c = Circuit("t")
    c.add(R("R1", "A", "0", "1k"))
    c.tran = Tran(step="1u", stop="10m")
    c.tran = Tran(step="5u", stop="20m")
    trans = [a for a in c.analyses if isinstance(a, Tran)]
    assert len(trans) == 1
    assert trans[0].stop == "20m"


def test_c_tran_set_to_none_clears():
    c = Circuit("t")
    c.add(R("R1", "A", "0", "1k"))
    c.tran = Tran(step="1u", stop="10m")
    c.tran = None
    assert c.tran is None
    assert not any(isinstance(a, Tran) for a in c.analyses)


def test_c_tran_type_check():
    c = Circuit("t")
    with pytest.raises(TypeError):
        c.tran = "not a Tran"


# ---- expect_external suppresses orphan-net warning ---------------------

def test_expect_external_suppresses_warning():
    c = Circuit("t", strict=False)
    c.net("BOUNDARY_OUT", expect_external=True)
    c.add(R("R1", "BOUNDARY_OUT", "INTERNAL", "1k"))
    c.add(R("R2", "INTERNAL", "GND", "1k"))
    warnings = c.validate_all()
    # INTERNAL is used twice (R1, R2) — no warn. GND is used once but it's
    # auto-detected as ground; the typo guard treats it the same as other
    # signals.  BOUNDARY_OUT is used once but is marked external.
    assert not any("BOUNDARY_OUT" in w for w in warnings), warnings


def test_expect_external_does_not_mask_unrelated_orphans():
    c = Circuit("t", strict=False)
    c.net("BOUNDARY_OUT", expect_external=True)
    c.add(R("R1", "BOUNDARY_OUT", "INTERNAL", "1k"))
    c.add(R("R2", "INTERNAL", "ORPHAN", "1k"))  # ORPHAN truly orphan
    warnings = c.validate_all()
    assert any("ORPHAN" in w for w in warnings), warnings


def test_expect_external_strict_mode():
    """In strict=True, an expect_external net should not error out."""
    c = Circuit("t", strict=True)
    c.net("STIMULUS", expect_external=True)
    c.add(R("R1", "STIMULUS", "B", "1k"))
    c.add(R("R2", "B", "GND", "1k"))
    # validate_all() raises only on errors, not warnings; with the
    # expect_external set we expect no warning for STIMULUS at all.
    warnings = c.validate_all()
    assert not any("STIMULUS" in w for w in warnings), warnings


def test_expect_external_roundtrips():
    c1 = Circuit("t")
    c1.net("X", expect_external=True)
    c1.add(R("R1", "X", "0", "1k"))
    d = c1.to_dict()
    c2 = Circuit.from_dict(d)
    assert c2.nets["X"].expect_external is True


def test_expect_external_default_not_in_dict():
    """expect_external=False is the default; don't bloat the JSON."""
    c = Circuit("t")
    c.net("Y")
    d = c.to_dict()
    assert "expect_external" not in d["nets"]["Y"]


# ---- run_tran helper (requires PySpice + ngspice) ----------------------

# ---- to_spice_deck(self_running=False) (#2 follow-up) -------------------
#
# NOTE: an earlier draft of this file added Python-side .SUBCKT arity
# checks via a local parser.  That parser was removed and the validate_all()
# check pruned — see CONTRIBUTING.md ("thin-layer principle").  The check
# will return as a RunPython call against KliCAD's SPICE_LIBRARY_PARSER
# once the SIM_LIBRARY_SPICE bindings are exposed on the C++ side.

def test_self_running_default_emits_control():
    c = Circuit("t")
    c.add(R("R1", "A", "0", "1k"))
    c.add(R("R2", "A", "B", "1k"))
    c.tran = Tran(step="1u", stop="10m")
    deck = c.to_spice_deck()
    assert ".control" in deck
    assert "tran 1u 10m" in deck
    assert ".endc" in deck


def test_self_running_false_omits_control():
    c = Circuit("t")
    c.add(R("R1", "A", "0", "1k"))
    c.add(R("R2", "A", "B", "1k"))
    c.tran = Tran(step="1u", stop="10m")
    deck = c.to_spice_deck(self_running=False)
    assert ".control" not in deck
    assert ".endc" not in deck
    # The tran analysis itself shouldn't bleed in as a stray directive either
    assert "tran 1u 10m" not in deck


# ---- write_project_shell (offline half of to_schematic) ----------------

def test_write_project_shell_writes_files(tmp_path):
    from klipy.circuit._klicad_sch import write_project_shell
    c = build_led_oscillator()
    sch = tmp_path / "led_osc.kicad_sch"
    out = write_project_shell(c, sch)
    assert out["ok"] is True
    assert Path(out["project_path"]).exists()
    assert Path(out["models_lib_path"]).exists()
    assert Path(out["sym_lib_table_path"]).exists()
    assert Path(out["sch_path"]).exists()


def test_write_project_shell_no_kicad_needed(tmp_path):
    """Offline call must not attempt any IPC."""
    from klipy.circuit._klicad_sch import write_project_shell
    c = Circuit("tiny")
    c.add(R("R1", "A", "0", "1k"))
    c.add(R("R2", "A", "B", "1k"))
    out = write_project_shell(c, tmp_path / "tiny.kicad_sch")
    assert out["ok"] is True


# ---- validate_all(kicad=...) — IPC arity check delegate ----------------

class _FakeRunPython:
    """Tiny mock that mimics klipy.KliCAD.run_python returning a dict-repr."""

    def __init__(self, registry: dict[str, int] | None, *, raise_exc: Exception | None = None):
        self._registry = registry
        self._raise = raise_exc

    def run_python(self, code: str):
        if self._raise is not None:
            raise self._raise

        class _R:
            ok = True
            stdout = ""
            stderr = ""
            exception_traceback = ""

            def __init__(self, repr_str):
                self.result_repr = repr_str

        if self._registry is None:
            r = _R("{}")
            r.ok = False
            r.exception_traceback = "simulated failure"
            return r
        return _R(repr(self._registry))


def test_validate_all_with_kicad_arity_passes(tmp_path):
    """When the mock registry agrees with the XSubckt, no warnings/errors."""
    c = Circuit("t", strict=False)
    c.add_model_lib(str(tmp_path / "fake.lib"))
    (tmp_path / "fake.lib").write_text(".SUBCKT SMAJ24CA a k\n.ENDS\n")
    c.add(V("V1", "RAIL", "0", dc=24))
    c.add(R("R1", "RAIL", "SW", "5"))
    c.add(XSubckt("D1", ["SW", "RAIL"], subckt="SMAJ24CA"))
    fk = _FakeRunPython({"SMAJ24CA": 2})
    warnings = c.validate_all(kicad=fk)
    assert not any("SMAJ24CA" in w for w in warnings), warnings


def test_validate_all_with_kicad_arity_mismatch_raises(tmp_path):
    c = Circuit("t", strict=False)
    c.add_model_lib(str(tmp_path / "fake.lib"))
    (tmp_path / "fake.lib").write_text(".SUBCKT SMAJ24CA a k\n.ENDS\n")
    c.add(V("V1", "RAIL", "0", dc=24))
    c.add(XSubckt("D1", ["SW", "RAIL", "EXTRA"], subckt="SMAJ24CA"))
    fk = _FakeRunPython({"SMAJ24CA": 2})
    with pytest.raises(ValueError, match=r"SMAJ24CA.*2 terminal.*3 node"):
        c.validate_all(kicad=fk)


def test_validate_all_with_kicad_unknown_subckt_warns(tmp_path):
    c = Circuit("t", strict=False)
    c.add_model_lib(str(tmp_path / "fake.lib"))
    (tmp_path / "fake.lib").write_text(".SUBCKT SOMETHING_ELSE a b\n.ENDS\n")
    c.add(V("V1", "A", "0", dc=24))
    c.add(XSubckt("D1", ["A", "0"], subckt="UNKNOWN_PART"))
    fk = _FakeRunPython({"SOMETHING_ELSE": 2})
    warnings = c.validate_all(kicad=fk)
    assert any("UNKNOWN_PART" in w for w in warnings), warnings


def test_validate_all_with_kicad_ipc_failure_skips_check(tmp_path):
    """If the IPC call fails, the check skips with a notice — never raises."""
    c = Circuit("t", strict=False)
    c.add_model_lib(str(tmp_path / "fake.lib"))
    (tmp_path / "fake.lib").write_text(".SUBCKT X a k\n.ENDS\n")
    c.add(V("V1", "A", "0", dc=5))
    c.add(XSubckt("D1", ["A", "0"], subckt="X"))
    fk = _FakeRunPython(None, raise_exc=RuntimeError("boom"))
    warnings = c.validate_all(kicad=fk)
    assert any("skipped" in w.lower() for w in warnings), warnings


def test_validate_all_without_kicad_doesnt_warn(tmp_path):
    """No kicad= → check silently skipped, no warnings about it."""
    c = Circuit("t", strict=False)
    c.add_model_lib(str(tmp_path / "fake.lib"))
    (tmp_path / "fake.lib").write_text(".SUBCKT X a k\n.ENDS\n")
    c.add(V("V1", "A", "0", dc=5))
    c.add(XSubckt("D1", ["A", "0"], subckt="X"))
    warnings = c.validate_all()
    assert not any("subckt" in w.lower() or "arity" in w.lower() or "skipped" in w.lower()
                   for w in warnings), warnings


# ---- live ngspice ------------------------------------------------------

@pytest.fixture
def _pyspice_available():
    pyspice = pytest.importorskip("PySpice.Spice.NgSpice.Shared")
    return pyspice


def test_run_tran_rc_steady_state(_pyspice_available):
    """RC circuit charges to its driving voltage within the time horizon."""
    c = Circuit("rc")
    c.add(V("V1", "VIN", "0", dc=5))
    c.add(R("R1", "VIN", "OUT", "10k"))
    c.add(C("C1", "OUT", "0", "10n"))  # tau = 100us
    c.ic(OUT=0)
    c.tran = Tran(step="1u", stop="2m", uic=True)

    result = c.run_tran()
    assert "time" in result
    # The OUT node should be reported as v(out) or 'out'.
    out_key = next((k for k in result if k.lower() in ("v(out)", "out")), None)
    assert out_key is not None, list(result.keys())
    assert result[out_key][-1] == pytest.approx(5.0, abs=0.05)


def test_run_tran_overrides_step_stop(_pyspice_available):
    """Explicit step/stop wins over c.tran."""
    c = Circuit("rc")
    c.add(V("V1", "VIN", "0", dc=5))
    c.add(R("R1", "VIN", "OUT", "10k"))
    c.add(C("C1", "OUT", "0", "10n"))
    c.tran = Tran(step="1u", stop="100u", uic=True)
    # The c.tran horizon is too short to reach 5V; override.
    result = c.run_tran(step="1u", stop="2m", uic=True)
    out_key = next((k for k in result if k.lower() in ("v(out)", "out")), None)
    assert result[out_key][-1] == pytest.approx(5.0, abs=0.05)


def test_run_tran_requires_tran(_pyspice_available):
    c = Circuit("rc")
    c.add(R("R1", "A", "0", "1k"))
    c.add(R("R2", "A", "B", "1k"))
    with pytest.raises(ValueError):
        c.run_tran()


def test_run_tran_surfaces_ngspice_stderr_on_parse_error(_pyspice_available):
    """Bad SPICE syntax should surface ngspice's actual error lines."""
    c = Circuit("bad", strict=False)
    # Add a B-source with intentionally broken syntax to provoke a parse
    # error.  klicad-python doesn't have a B element so we add it via
    # add_model + a custom XSubckt that references it — but the cleanest
    # provocation is just an undefined model on a D part.
    c.add(V("V1", "A", "0", dc=5))
    c.add(D("D1", a="A", k="0", model="NONEXISTENT_MODEL_XYZ"))
    c.tran = Tran(step="1u", stop="100u")
    with pytest.raises(RuntimeError) as exc:
        c.run_tran()
    # The error should mention either the model name OR include a stderr
    # excerpt — not just "no tran plot produced" with no context.
    msg = str(exc.value)
    assert (
        "ngspice stderr" in msg
        or "NONEXISTENT_MODEL_XYZ" in msg.upper()
        or "MOSFET" in msg.upper()  # ngspice's actual error wording varies
        or "model" in msg.lower()
    ), f"expected ngspice context in error, got: {msg!r}"


# ---- live ngspice run (requires KliCAD instance) -----------------------

def test_oscillator_actually_oscillates(kicad):
    """Push the generated deck to ngspice, verify the circuit oscillates."""
    c = build_led_oscillator()
    deck = c.to_spice_deck()

    r = kicad.run_python(f"""
import klicad_native_simulator as sim
sim.load_netlist({deck!r})
sim.command('run')
# Use the most-recent tran plot — previous test runs in the same KliCAD
# session leave tran1/tran2/... around; ours is the last one.
plots = sim.list_plots()
tran_plots = [p for p in plots if p.startswith('tran')]
assert tran_plots, f'no tran plot present, only: {{plots}}'
sim.command(f'setplot {{tran_plots[-1]}}')
nl = sim.get_vector('v(nl)')['data']
nr = sim.get_vector('v(nr)')['data']
diff = [a - b for a, b in zip(nl, nr)]
zero_crossings = sum(1 for i in range(1, len(diff))
                     if (diff[i-1] >= 0) != (diff[i] >= 0))
{{
    'plot':       tran_plots[-1],
    'samples':    len(nl),
    'nl_swing':   round(max(nl) - min(nl), 3),
    'nr_swing':   round(max(nr) - min(nr), 3),
    'zero_xings': zero_crossings,
}}
""")
    assert r.ok, r.exception_traceback
    import ast
    result = ast.literal_eval(r.result_repr)
    # Expect a real swing, not a dead DC operating point
    assert result["nl_swing"] > 3.0, result
    assert result["nr_swing"] > 1.5, result
    # 3s of sim at ~1.3 Hz = ~4 full periods = ~8 zero crossings (we set
    # IC to start asymmetric so it kicks immediately)
    assert result["zero_xings"] >= 3, result

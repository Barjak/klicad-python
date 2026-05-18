"""Phase A acceptance test for kipy.klicad.circuit.

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

import pytest

from kipy.klicad.circuit import (
    Circuit,
    R, C, NPN, LED, V,
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


# ---- pure tests (no KiCad needed) --------------------------------------

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
        # Element line starts with the ref (which by KiCad convention
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


# ---- live ngspice run (requires KliCAD instance) -----------------------

def test_oscillator_actually_oscillates(kicad):
    """Push the generated deck to ngspice, verify the circuit oscillates."""
    c = build_led_oscillator()
    deck = c.to_spice_deck()

    r = kicad.run_python(f"""
import kicad_native_simulator as sim
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

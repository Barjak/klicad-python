"""H1 tests — Sub-Circuit DSL + recursive .SUBCKT emission.

All pure-Python; no IPC; no live KliCAD required.  ngspice integration
tests use PySpice's NgSpiceShared via the existing Circuit.run_tran
helper — those are gated by a try/import on PySpice.
"""
from __future__ import annotations

import pytest

from klipy.circuit import (
    Circuit, R, C, L, D, V, NPN, NMOS, Tran, Op, STANDARD_MODEL_LIB,
)
from klipy.circuit._bus import (
    is_bus_member, is_bus_range, is_bus_ref, is_power_name,
    parse_bus_member, parse_bus_range,
    expand_bus_range, expand_one,
    expand_port_decl, expand_port_map,
    validate_port_decl, validate_port_widths_for_repeat, validate_spice_name,
)
from klipy.circuit._part import SubcircuitInstance


# ──────────────────────────────────────────────────────────────────────────
# Bus syntax — pure functions
# ──────────────────────────────────────────────────────────────────────────

class TestBusSyntax:
    def test_recognizers(self):
        assert is_bus_member("DATA[3]")
        assert not is_bus_member("DATA[0..3]")
        assert is_bus_range("DATA[0..3]")
        assert not is_bus_range("DATA[3]")
        assert is_bus_ref("DATA[3]")
        assert is_bus_ref("DATA[0..3]")
        assert not is_bus_ref("DATA")

    def test_parse(self):
        assert parse_bus_member("DATA[3]") == ("DATA", 3)
        assert parse_bus_member("DATA[0..3]") is None
        # parse_bus_range canonicalizes endianness
        assert parse_bus_range("DATA[0..7]") == ("DATA", 0, 7)
        assert parse_bus_range("DATA[7..0]") == ("DATA", 0, 7)
        assert parse_bus_range("DATA[3]") is None

    def test_expand_bus_range(self):
        assert expand_bus_range("DATA[0..3]") == [
            "DATA[0]", "DATA[1]", "DATA[2]", "DATA[3]",
        ]
        # Endian-canonical: descending input → ascending output
        assert expand_bus_range("DATA[3..0]") == expand_bus_range("DATA[0..3]")
        with pytest.raises(ValueError):
            expand_bus_range("DATA")

    def test_expand_one_passthrough(self):
        assert expand_one("IN") == ["IN"]
        assert expand_one("DATA[3]") == ["DATA[3]"]
        assert expand_one("DATA[0..3]") == ["DATA[0]", "DATA[1]", "DATA[2]", "DATA[3]"]

    def test_power_recognition(self):
        for name in ("GND", "VCC", "VDD", "VSS", "+12V", "+3V3", "VBAT", "AGND"):
            assert is_power_name(name), name
        for name in ("IN", "OUT", "DATA", "audio_in", "AUDIO_OUT"):
            assert not is_power_name(name), name


class TestPortDeclValidation:
    def test_well_formed(self):
        validate_port_decl(["IN", "OUT"])
        validate_port_decl(["CLK", "DATA[0..7]", "Q"])

    def test_reject_power_scalar(self):
        with pytest.raises(ValueError, match="power"):
            validate_port_decl(["IN", "GND"])
        with pytest.raises(ValueError, match="power"):
            validate_port_decl(["+12V"])

    def test_reject_power_bus(self):
        with pytest.raises(ValueError, match="power"):
            validate_port_decl(["GND[0..3]"])

    def test_reject_duplicates(self):
        with pytest.raises(ValueError, match="duplicate"):
            validate_port_decl(["IN", "IN"])
        with pytest.raises(ValueError, match="(duplicate|used twice)"):
            validate_port_decl(["DATA[0..3]", "DATA[2..4]"])

    def test_expand_preserves_order(self):
        result = expand_port_decl(["IN", "DATA[0..3]", "OUT"])
        assert result == ["IN", "DATA[0]", "DATA[1]", "DATA[2]", "DATA[3]", "OUT"]


class TestPortMapExpansion:
    def test_scalar(self):
        m = expand_port_map(["IN", "OUT"], {"IN": "A", "OUT": "B"})
        assert m == {"IN": "A", "OUT": "B"}

    def test_bus_by_base_name(self):
        m = expand_port_map(["DATA[0..3]"], {"DATA": "BUS[0..3]"})
        assert m == {
            "DATA[0]": "BUS[0]", "DATA[1]": "BUS[1]",
            "DATA[2]": "BUS[2]", "DATA[3]": "BUS[3]",
        }

    def test_bus_by_full_range(self):
        m = expand_port_map(["DATA[0..3]"], {"DATA[0..3]": "BUS[0..3]"})
        assert m == {
            "DATA[0]": "BUS[0]", "DATA[1]": "BUS[1]",
            "DATA[2]": "BUS[2]", "DATA[3]": "BUS[3]",
        }

    def test_bus_descending_net_canonicalizes(self):
        # DATA[0..3] ↔ BUS[3..0] — both canonicalize to ascending,
        # so DATA[0]↔BUS[0], DATA[1]↔BUS[1], etc.  No bit reversal.
        m = expand_port_map(["DATA[0..3]"], {"DATA": "BUS[3..0]"})
        assert m == {
            "DATA[0]": "BUS[0]", "DATA[1]": "BUS[1]",
            "DATA[2]": "BUS[2]", "DATA[3]": "BUS[3]",
        }

    def test_reject_width_mismatch(self):
        with pytest.raises(ValueError, match="width"):
            expand_port_map(["DATA[0..7]"], {"DATA": "BUS[0..3]"})

    def test_reject_missing_port(self):
        with pytest.raises(ValueError, match="missing"):
            expand_port_map(["IN", "OUT"], {"IN": "A"})

    def test_reject_unknown_port(self):
        with pytest.raises(ValueError, match="doesn't match"):
            expand_port_map(["IN"], {"IM": "A"})

    def test_reject_scalar_to_bus_binding(self):
        with pytest.raises(ValueError, match="scalar but bound to bus"):
            expand_port_map(["IN"], {"IN": "BUS[0..3]"})

    def test_reject_bus_to_scalar_binding(self):
        with pytest.raises(ValueError, match="bus width"):
            expand_port_map(["DATA[0..3]"], {"DATA": "SCALAR"})


class TestValidatePortWidthsForRepeat:
    """R5.1: bus-port width must equal repeat_count; scalars are unrestricted."""

    def test_all_scalar_any_repeat(self):
        # Scalars are shared across slots — any repeat_count is valid.
        validate_port_widths_for_repeat(["IN", "OUT", "EN"], repeat_count=1)
        validate_port_widths_for_repeat(["IN", "OUT", "EN"], repeat_count=8)
        validate_port_widths_for_repeat(["IN", "OUT", "EN"], repeat_count=60)

    def test_bus_width_matches(self):
        # DATA[0..7] has width 8; repeat=8 is valid.
        validate_port_widths_for_repeat(["DATA[0..7]"], repeat_count=8)
        # Descending range also has width 8.
        validate_port_widths_for_repeat(["DATA[7..0]"], repeat_count=8)
        # Width-1 bus (a single member span) with repeat=1.
        validate_port_widths_for_repeat(["X[0..0]"], repeat_count=1)

    def test_mixed_scalar_and_bus(self):
        # Mixed ports — bus widths match, scalars unrestricted → OK.
        validate_port_widths_for_repeat(
            ["GATE", "OUT", "DATA[0..7]", "EN"],
            repeat_count=8,
        )

    def test_multiple_buses_all_match(self):
        validate_port_widths_for_repeat(
            ["A[0..3]", "B[0..3]", "C[3..0]", "SHARED"],
            repeat_count=4,
        )

    def test_bus_width_mismatch_raises(self):
        # Bus too narrow.
        with pytest.raises(ValueError, match="bus width 4 doesn't match repeat_count 8"):
            validate_port_widths_for_repeat(["DATA[0..3]"], repeat_count=8)
        # Bus too wide.
        with pytest.raises(ValueError, match="bus width 16 doesn't match repeat_count 8"):
            validate_port_widths_for_repeat(["DATA[0..15]"], repeat_count=8)

    def test_one_bus_matches_one_mismatches(self):
        # First bus matches, second doesn't — must still raise.
        with pytest.raises(ValueError, match=r"'B\[0\.\.3\]'.*bus width 4"):
            validate_port_widths_for_repeat(
                ["A[0..7]", "B[0..3]"],
                repeat_count=8,
            )

    def test_reject_non_positive_repeat(self):
        with pytest.raises(ValueError, match="repeat_count must be >= 1"):
            validate_port_widths_for_repeat(["IN"], repeat_count=0)
        with pytest.raises(ValueError, match="repeat_count must be >= 1"):
            validate_port_widths_for_repeat(["IN"], repeat_count=-1)

    def test_reject_non_int_repeat(self):
        with pytest.raises(ValueError, match="repeat_count must be an int"):
            validate_port_widths_for_repeat(["IN"], repeat_count=8.0)  # type: ignore[arg-type]
        # bool is technically an int subclass — explicitly rejected.
        with pytest.raises(ValueError, match="repeat_count must be an int"):
            validate_port_widths_for_repeat(["IN"], repeat_count=True)  # type: ignore[arg-type]

    def test_propagates_port_decl_errors(self):
        # Malformed port_decl is caught by the underlying validate_port_decl.
        with pytest.raises(ValueError, match="power"):
            validate_port_widths_for_repeat(["IN", "GND"], repeat_count=1)
        with pytest.raises(ValueError, match="(duplicate|used twice)"):
            validate_port_widths_for_repeat(["IN", "IN"], repeat_count=1)

    def test_isolated_bus_member_is_scalar_like(self):
        # An isolated DATA[3] (not a range) is treated as a scalar port —
        # it doesn't span a width, so any repeat_count is OK.
        validate_port_widths_for_repeat(["DATA[3]", "IN"], repeat_count=8)


class TestSpiceNameValidation:
    def test_simple_refs(self):
        validate_spice_name("R1", kind="ref")
        validate_spice_name("U_amp1", kind="ref")

    def test_simple_nets(self):
        validate_spice_name("VCC", kind="net")
        validate_spice_name("AUDIO_IN", kind="net")
        validate_spice_name("DATA[3]", kind="net")    # bus carve-out
        validate_spice_name("DATA[0..7]", kind="net")

    def test_model_names_with_leading_digits_ok(self):
        # SPICE accepts model names starting with digits — 2N3904, 1N4148, etc.
        validate_spice_name("2N3904", kind="model")
        validate_spice_name("1N4148", kind="model")

    def test_reject_leading_digit_in_ref(self):
        with pytest.raises(ValueError, match="letter"):
            validate_spice_name("1R", kind="ref")

    def test_reject_meaningful_chars(self):
        for bad in ("foo bar", "foo=bar", "foo;bar", "foo(bar)"):
            with pytest.raises(ValueError, match="SPICE-meaningful"):
                validate_spice_name(bad, kind="net")


# ──────────────────────────────────────────────────────────────────────────
# Circuit ports= + .instance()
# ──────────────────────────────────────────────────────────────────────────

class TestSubcircuitDSL:
    def test_root_circuit_is_not_subcircuit(self):
        c = Circuit("top")
        assert not c.is_subcircuit
        assert c.ports is None

    def test_subcircuit_construction(self):
        amp = Circuit("amp", ports=["IN", "OUT"])
        assert amp.is_subcircuit
        assert amp.ports == ["IN", "OUT"]
        assert amp._port_decl == ["IN", "OUT"]
        assert amp._ports_expanded == ["IN", "OUT"]

    def test_subcircuit_bus_port_expansion(self):
        sc = Circuit("sc", ports=["CLK", "DATA[0..3]"])
        assert sc._port_decl == ["CLK", "DATA[0..3]"]
        assert sc._ports_expanded == [
            "CLK", "DATA[0]", "DATA[1]", "DATA[2]", "DATA[3]",
        ]

    def test_subcircuit_rejects_power_port(self):
        with pytest.raises(ValueError, match="power"):
            Circuit("bad", ports=["IN", "VCC"])

    def test_root_cant_be_instanced(self):
        root = Circuit("root")
        with pytest.raises(ValueError, match="not declared with ports"):
            root.instance("U1")

    def test_instance_returns_subcircuit_part(self):
        amp = Circuit("amp", ports=["IN", "OUT"])
        inst = amp.instance("U_amp1", IN="A", OUT="B")
        assert isinstance(inst, SubcircuitInstance)
        assert inst.kind == "SUBCIRCUIT"
        assert inst.ref == "U_amp1"
        assert inst.subckt == "amp"
        assert inst.definition is amp
        assert inst.pin_names == ("IN", "OUT")
        assert inst.connections == {"IN": "A", "OUT": "B"}

    def test_instance_spice_line(self):
        amp = Circuit("amp", ports=["IN", "OUT"])
        inst = amp.instance("U_amp1", IN="A", OUT="B")
        # SPICE X-line: X<ref> <nets...> <subckt_name>
        assert inst.spice_line() == "XU_amp1 A B amp"

    def test_instance_x_prefix_idempotent(self):
        amp = Circuit("amp", ports=["IN", "OUT"])
        inst = amp.instance("X3", IN="A", OUT="B")
        assert inst.spice_line() == "X3 A B amp"

    def test_bus_instance(self):
        sc = Circuit("sc", ports=["CLK", "DATA[0..3]"])
        inst = sc.instance("U1", CLK="K", DATA="BUS[0..3]")
        assert inst.pin_names == (
            "CLK", "DATA[0]", "DATA[1]", "DATA[2]", "DATA[3]",
        )
        assert inst.connections["DATA[2]"] == "BUS[2]"
        assert inst.spice_line() == "XU1 K BUS[0] BUS[1] BUS[2] BUS[3] sc"

    def test_repeat_count_field(self):
        """R5.2: SubcircuitInstance carries a repeat_count int (default 1)."""
        amp = Circuit("amp", ports=["IN", "OUT"])

        # Default — no kwarg, repeat_count is 1.
        inst_default = SubcircuitInstance(
            "U_amp1", amp, port_map={"IN": "A", "OUT": "B"},
        )
        assert inst_default.repeat_count == 1

        # Explicit — kwarg sets the field.
        inst_multi = SubcircuitInstance(
            "U_amp2", amp, port_map={"IN": "A", "OUT": "B"},
            repeat_count=4,
        )
        assert inst_multi.repeat_count == 4


# ──────────────────────────────────────────────────────────────────────────
# Recursive .SUBCKT emission
# ──────────────────────────────────────────────────────────────────────────

class TestSubcktEmission:
    def _amp(self) -> Circuit:
        amp = Circuit("amp", ports=["IN", "OUT"])
        amp.add(R("R1", "IN", "n1", value="10k"))
        amp.add(R("R2", "VCC", "OUT", value="1k"))
        amp.add(NPN("Q1", c="OUT", b="n1", e="GND"))
        return amp

    def _top(self) -> Circuit:
        amp = self._amp()
        top = Circuit("top")
        top.add(V("VCC_SRC", "VCC", "GND", dc=12))
        top.add(V("V_IN", "IN0", "GND", dc=0.7))
        top.add(amp.instance("U_amp1", IN="IN0", OUT="OUT0"))
        top.add(R("R_LOAD", "OUT0", "GND", value="1k"))
        return top

    def test_to_spice_deck_on_subcircuit_raises(self):
        amp = self._amp()
        with pytest.raises(ValueError, match="Sub-Circuit definition"):
            amp.to_spice_deck()

    def test_deck_has_subckt_block(self):
        deck = self._top().to_spice_deck()
        assert ".SUBCKT amp IN OUT" in deck
        assert ".ENDS amp" in deck

    def test_deck_has_x_line(self):
        deck = self._top().to_spice_deck()
        assert "XU_amp1 IN0 OUT0 amp" in deck

    def test_deck_globalizes_power_nets(self):
        deck = self._top().to_spice_deck()
        # VCC is used inside amp's body; gets a .global so it crosses
        # the .SUBCKT boundary.  GND is omitted (already SPICE-global).
        assert ".global VCC" in deck
        assert "GND" not in deck.split(".global")[1].splitlines()[0]

    def test_subckt_body_rewrites_ground(self):
        deck = self._top().to_spice_deck()
        # Inside amp's .SUBCKT, Q1's emitter (GND) becomes 0
        subckt_block = deck[deck.index(".SUBCKT"):deck.index(".ENDS")]
        assert "Q1 OUT n1 0 2N3904" in subckt_block

    def test_subckt_block_appears_before_root_parts(self):
        deck = self._top().to_spice_deck()
        assert deck.index(".SUBCKT amp") < deck.index("XU_amp1")

    def test_multiple_instances_one_subckt(self):
        amp = self._amp()
        top = Circuit("top")
        top.add(V("VCC_SRC", "VCC", "GND", dc=12))
        top.add(V("V1", "IN1", "GND", dc=0.5))
        top.add(V("V2", "IN2", "GND", dc=0.5))
        top.add(amp.instance("U1", IN="IN1", OUT="OUT1"))
        top.add(amp.instance("U2", IN="IN2", OUT="OUT2"))
        top.add(R("RL1", "OUT1", "GND", value="1k"))
        top.add(R("RL2", "OUT2", "GND", value="1k"))
        deck = top.to_spice_deck()
        # One .SUBCKT block, two X-lines
        assert deck.count(".SUBCKT amp") == 1
        assert deck.count(".ENDS amp") == 1
        assert "XU1 IN1 OUT1 amp" in deck
        assert "XU2 IN2 OUT2 amp" in deck

    def test_nested_subcircuits_emit_all_kinds(self):
        # Sub-Circuit B is used inside Sub-Circuit A's body.
        inner = Circuit("inner", ports=["A", "B"])
        inner.add(R("R1", "A", "B", value="100"))

        outer = Circuit("outer", ports=["IN", "OUT"])
        outer.add(R("Rin", "IN", "mid", value="1k"))
        outer.add(inner.instance("Ui", A="mid", B="OUT"))

        top = Circuit("top")
        top.add(V("V1", "AIN", "GND", dc=1))
        top.add(outer.instance("Uo", IN="AIN", OUT="AOUT"))
        top.add(R("RL", "AOUT", "GND", value="1k"))

        deck = top.to_spice_deck()
        assert ".SUBCKT inner A B" in deck
        assert ".SUBCKT outer IN OUT" in deck
        # outer's body contains an X-line for inner
        outer_block = deck[deck.index(".SUBCKT outer"):deck.index(".ENDS outer")]
        assert "XUi mid OUT inner" in outer_block

    def test_includes_aggregated_from_hierarchy(self):
        amp = self._amp()
        amp.add_model_lib("/tmp/vendor_amp.lib")

        top = Circuit("top")
        top.add_model_lib("/tmp/main.lib")
        top.add(V("V1", "IN0", "GND", dc=1))
        top.add(amp.instance("U1", IN="IN0", OUT="OUT0"))
        top.add(R("RL", "OUT0", "GND", value="1k"))

        deck = top.to_spice_deck()
        assert ".include /tmp/main.lib" in deck
        assert ".include /tmp/vendor_amp.lib" in deck


class TestSpiceNameBoundaryCheck:
    def test_rejects_bad_ref_at_emit(self):
        c = Circuit("c")
        # Bypass Circuit.add (which doesn't validate this case)
        c.parts.append(R.__new__(R))
        c.parts[0].__init__("1bad_ref", "A", "B", value="1k")
        with pytest.raises(ValueError, match="letter"):
            c.to_spice_deck()

    def test_rejects_meaningful_chars_in_net(self):
        c = Circuit("c")
        c.add(R("R1", "good_net", "bad net", value="1k"))
        with pytest.raises(ValueError, match="SPICE-meaningful"):
            c.to_spice_deck()


# ──────────────────────────────────────────────────────────────────────────
# Integration — actually run via ngspice through Circuit.run_tran
# ──────────────────────────────────────────────────────────────────────────

@pytest.fixture
def _pyspice_available():
    pytest.importorskip("PySpice.Spice.NgSpice.Shared")


@pytest.mark.usefixtures("_pyspice_available")
class TestSubcircuitNgspice:
    def test_hierarchical_tran_runs(self):
        """Verify the recursive .SUBCKT deck actually parses and runs."""
        amp = Circuit("amp", ports=["IN", "OUT"])
        amp.add_model_lib(STANDARD_MODEL_LIB)
        amp.add(R("R1", "IN", "n1", value="10k"))
        amp.add(R("R2", "VCC", "OUT", value="1k"))
        amp.add(NPN("Q1", c="OUT", b="n1", e="GND"))

        top = Circuit("top")
        top.add_model_lib(STANDARD_MODEL_LIB)
        top.add(V("VCC_SRC", "VCC", "GND", dc=12))
        top.add(V("V_IN", "IN0", "GND", ac="PULSE(0 0.5 0 10n 10n 1u 2u)"))
        top.add(amp.instance("U_amp1", IN="IN0", OUT="OUT0"))
        top.add(R("R_LOAD", "OUT0", "GND", value="1k"))
        top.tran = Tran(step="100n", stop="10u")

        result = top.run_tran()
        assert "time" in result
        assert "out0" in result
        assert "vcc" in result
        # Internal node should be namespaced by instance ref
        assert "xu_amp1.n1" in result
        # VCC settled at 12V
        assert abs(result["vcc"][-1] - 12.0) < 0.01

    def test_multi_instance_namespaces_internal_nodes(self):
        """Two instances of the same Sub-Circuit have distinct internal nodes."""
        amp = Circuit("amp", ports=["IN", "OUT"])
        amp.add_model_lib(STANDARD_MODEL_LIB)
        amp.add(R("R1", "IN", "n1", value="10k"))
        amp.add(R("R2", "n1", "OUT", value="1k"))

        top = Circuit("top")
        top.add_model_lib(STANDARD_MODEL_LIB)
        top.add(V("V1", "IN1", "GND", dc=1.0))
        top.add(V("V2", "IN2", "GND", dc=2.0))
        top.add(amp.instance("Ua", IN="IN1", OUT="OUT1"))
        top.add(amp.instance("Ub", IN="IN2", OUT="OUT2"))
        top.add(R("RL1", "OUT1", "GND", value="1k"))
        top.add(R("RL2", "OUT2", "GND", value="1k"))
        top.tran = Tran(step="100n", stop="1u")

        result = top.run_tran()
        assert "xua.n1" in result
        assert "xub.n1" in result
        # Different stimulus → different internal node values
        assert result["xua.n1"][-1] != result["xub.n1"][-1]

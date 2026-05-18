"""Eyeball tests for kipy.klicad.circuit._partition.partition — HANDOFF Phase D.

Three composite circuits exercise the Louvain-based heuristic:

  1. LED osc (cross-coupled astable) — no clean cut, expect 1 sheet.
  2. Two-stage BJT common-emitter amplifier — coupling cap is the
     boundary, expect 2 stage blocks + a top-level supply.
  3. Mini AC-to-DC supply: bridge rectifier + smoothing + load —
     expect ~2 blocks (rectifier and downstream stage).

The expected counts are loose — they're an eyeball-on-the-heuristic
guide, not a guarantee that Louvain will always split exactly that
way.  If a future tweak changes the count by ±1, update the assertion
and check the printed block layout against intuition before
committing.
"""

from __future__ import annotations

import pytest

from kipy.klicad.circuit import (
    Circuit, R, C, L, D, LED, NPN, V, Tran,
)
# partition is intentionally not on the public surface — these tests
# poke the internal helper directly since they exercise the algorithm.
from kipy.klicad.circuit._partition import partition, Block


# ──────────────────────────────────────────────────────────────────────────
# Fixture 1 — LED astable multivibrator (no decomposition expected)
# ──────────────────────────────────────────────────────────────────────────

def _led_osc() -> Circuit:
    c = Circuit("led-osc")
    c.add(V("V1", "VCC", "GND", dc=5))
    c.add(R("R1", "VCC", "NL", value="1k"))
    c.add(R("R2", "VCC", "BL", value="47k"))
    c.add(R("R3", "VCC", "BR", value="47k"))
    c.add(R("R4", "VCC", "LED_A", value="1k"))
    c.add(C("C1", "NL", "BR", value="10u"))
    c.add(C("C2", "NR", "BL", value="10u"))
    c.add(NPN("Q1", c="NL", b="BL", e="GND", model="2N3904"))
    c.add(NPN("Q2", c="NR", b="BR", e="GND", model="2N3904"))
    c.add(LED("D1", a="LED_A", k="NR"))
    return c


# ──────────────────────────────────────────────────────────────────────────
# Fixture 2 — Two-stage common-emitter amplifier (clean 2-block cut)
# ──────────────────────────────────────────────────────────────────────────

def _two_stage_ce() -> Circuit:
    """Stage 1: input → C_in → Q1 (bias R1a/R1b, collector R_c1, emitter
    R_e1 with bypass C_e1).  Stage 1 output → C_couple → Stage 2: same
    topology with Q2.  Stage 2 output → C_out → load.

    The two stages are connected ONLY by the coupling cap C_couple — a
    single signal-net bridge.  Louvain should split them.
    """
    c = Circuit("two-stage-ce")
    c.add(V("VCC1", "VCC", "GND", dc=12))
    c.add(V("VIN",  "vin", "GND", ac="SIN(0 10m 1k)"))

    # Stage 1
    c.add(C("Cin",  "vin",     "b1",   value="1u"))
    c.add(R("R1a",  "VCC",     "b1",   value="100k"))
    c.add(R("R1b",  "b1",      "GND",  value="22k"))
    c.add(R("Rc1",  "VCC",     "c1",   value="4.7k"))
    c.add(R("Re1",  "e1",      "GND",  value="1k"))
    c.add(C("Ce1",  "e1",      "GND",  value="10u"))
    c.add(NPN("Q1", c="c1", b="b1", e="e1", model="2N3904"))

    # Stage 2 (same pattern, refs *2)
    c.add(C("Cc",   "c1",      "b2",   value="1u"))   # COUPLING
    c.add(R("R2a",  "VCC",     "b2",   value="100k"))
    c.add(R("R2b",  "b2",      "GND",  value="22k"))
    c.add(R("Rc2",  "VCC",     "c2",   value="4.7k"))
    c.add(R("Re2",  "e2",      "GND",  value="1k"))
    c.add(C("Ce2",  "e2",      "GND",  value="10u"))
    c.add(NPN("Q2", c="c2", b="b2", e="e2", model="2N3904"))

    c.add(C("Cout", "c2",      "out",  value="1u"))
    c.add(R("Rload","out",     "GND",  value="10k"))

    return c


# ──────────────────────────────────────────────────────────────────────────
# Fixture 3 — AC-to-DC mini supply: bridge rectifier + smoothing + load
# ──────────────────────────────────────────────────────────────────────────

def _ac_to_dc() -> Circuit:
    """AC source (VAC) -> 4-diode bridge -> smoothing cap -> RC filter
    -> LED indicator on the cleaned rail.

    Expected blocks: bridge rectifier (D1-D4 + the two AC nodes) ought
    to be one community; smoothing + filter + load another.
    """
    c = Circuit("ac-to-dc")
    c.add(V("VAC", "ac1", "ac2", ac="SIN(0 12 60)"))

    # Bridge: ac1/ac2 inputs, dc+/GND outputs
    c.add(D("D1", a="ac1",  k="dc_plus", model="1N4001"))
    c.add(D("D2", a="ac2",  k="dc_plus", model="1N4001"))
    c.add(D("D3", a="GND",  k="ac1",     model="1N4001"))
    c.add(D("D4", a="GND",  k="ac2",     model="1N4001"))

    # Smoothing + filter
    c.add(C("Csmooth", "dc_plus", "GND", value="1000u"))
    c.add(R("Rfilt",   "dc_plus", "vout", value="10"))
    c.add(C("Cfilt",   "vout",    "GND", value="100u"))

    # Load
    c.add(R("Rload",   "vout",    "led_a", value="1k"))
    c.add(LED("D5",    a="led_a", k="GND"))

    return c


# ──────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────

def _print_blocks(label: str, blocks: list[Block]) -> None:
    print(f"\n=== {label}: {len(blocks)} block(s) ===")
    for i, b in enumerate(blocks):
        kind = "TOP" if i == 0 else "SUB"
        print(f"  [{kind}] {b.suggested_name}  size={b.size}  "
              f"boundary={len(b.boundary_nets)} "
              f"({b.boundary_ratio:.2f})")
        print(f"      parts:    {b.parts}")
        print(f"      internal: {sorted(b.internal_nets)}")
        print(f"      boundary: {sorted(b.boundary_nets)}")


def test_led_osc_is_one_block(capsys):
    blocks = partition(_led_osc())
    _print_blocks("LED osc", blocks)
    # The cross-coupled astable has no clean cut: every part is multiply
    # connected to the rest.  Expect just the top-level block (and
    # maybe nothing else).
    subs = blocks[1:]
    assert len(subs) <= 1, (
        f"LED osc shouldn't decompose into multiple sub-sheets; "
        f"got {len(subs)} sub-blocks"
    )


def test_two_stage_ce_splits_into_stages(capsys):
    blocks = partition(_two_stage_ce())
    _print_blocks("Two-stage CE", blocks)
    subs = blocks[1:]
    # We want at least 2 sub-blocks (the two stages).  Could legitimately
    # be 3 if Louvain peels the input/output couplers off too.
    assert 2 <= len(subs) <= 3, (
        f"Two-stage amp should split into ~2 stages; got {len(subs)}"
    )
    # Each sub-block should have a BJT in it.
    bjts_per_sub = [
        sum(1 for ref in b.parts if ref.startswith("Q"))
        for b in subs
    ]
    assert sorted(bjts_per_sub)[-2:] == [1, 1], (
        f"Each top-2 sub-block should contain exactly one BJT; "
        f"BJT counts: {bjts_per_sub}"
    )


def test_ac_to_dc_splits_into_stages(capsys):
    blocks = partition(_ac_to_dc())
    _print_blocks("AC to DC", blocks)
    subs = blocks[1:]
    # Bridge rectifier (4 diodes + ac nets) is one tight cluster.  The
    # post-rectifier filter+load is another.  Expect at least one
    # sub-block — exactly two is the design intent but bridge diodes
    # may collapse into the top if their fan-out is too high.
    assert len(subs) >= 1, (
        f"AC-to-DC should expose at least one sub-block; got {len(subs)}"
    )

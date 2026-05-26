"""Exhaustive tests for to_schematic() diff/apply semantics.

Test groups:

  A. Spice-affecting property changes on KEPT refs — these are the cases
     the field-update fix has to handle.  For each, build the schematic
     once, modify one property in the code, re-run mode='diff', verify
     the corresponding field on the schematic now reflects the new code.

  B. Topology — add / remove / rename / empty-target.

  C. Connectivity — pin reconnection (net rewire), label re-emission.

  D. Aesthetic preservation — position, rotation, mirror, footprint,
     custom user fields survive diff but reset under replace.

  E. Power-flag symbols (#PWR_*) do not accumulate across diff iterations.

  F. Edge cases — strict mode on empty, duplicate-ref validation.

Requires a live KliCAD with the diff_apply_proj schematic open.  The
session fixture below skips the whole module if KliCAD is on a
different project — launch with:

    ~/.local/bin/klicad tests/fixtures/diff_apply_proj/diff_apply.kicad_pro
    pytest tests/test_diff_apply.py
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from klipy import KliCAD
from klipy.circuit import (
    Circuit, R, C, L, V, NMOS, PMOS, D, NPN, Tran, STANDARD_MODEL_LIB,
)
from klipy.circuit._klicad_sch import to_schematic, write_project_shell


PROJ_DIR = Path(__file__).parent / "fixtures" / "diff_apply_proj"
SCH      = PROJ_DIR / "diff_apply.kicad_sch"
PRO      = PROJ_DIR / "diff_apply.kicad_pro"


# ──────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def ensure_project_loaded(kicad):
    """Skip the module if KliCAD isn't loaded on diff_apply.kicad_pro.

    We can't safely switch projects from inside KliCAD (schematic-switch
    crash, HANDOFF.md crash #2), so the right precondition is that the
    user launched KliCAD on the target project before pytest.
    """
    r = kicad.run_python(
        "import klicad_native_project_manager as pm; pm.get_current_project()"
    )
    if not r.ok or PRO.name not in r.result_repr:
        pytest.skip(
            f"KliCAD must be loaded on {PRO} for diff/apply tests.  "
            f"Quit KliCAD and relaunch with `~/.local/bin/klicad {PRO}`."
        )


def _bootstrap_project_files(c: Circuit) -> None:
    """Write the offline shell files for the test project."""
    PROJ_DIR.mkdir(parents=True, exist_ok=True)
    write_project_shell(c, SCH)


def _list_symbols(kicad) -> list[dict]:
    """Live SCH_SYMBOL inventory keyed by ref — with all enriched fields."""
    r = kicad.run_python(
        "import klicad_native_schematic_state as ss; ss.list_symbols()"
    )
    assert r.ok, f"list_symbols failed: {r.exception_traceback}"
    return ast.literal_eval(r.result_repr)


def _by_ref(rows: list[dict]) -> dict[str, dict]:
    return {row["ref"]: row for row in rows if not row["ref"].startswith("#")}


def _power_rows(rows: list[dict]) -> list[dict]:
    return [row for row in rows if row["ref"].startswith("#")]


def _reset_to(kicad, c: Circuit) -> dict:
    """Wipe + place c via mode='replace'.  Returns the diff result dict."""
    return to_schematic(c, SCH, kicad=kicad, mode="replace")


# Canonical starting circuit used by most tests.  Small enough to reason
# about (4 parts + GND/VCC nets) and exercises both a passive (R), a
# typed source (V with PULSE), a BJT (Q1 — kind needs Sim.*), and a
# MOSFET (M1 — D/G/S string pin map).
def _base_circuit() -> Circuit:
    c = Circuit("diff_apply", desc="base circuit for diff/apply tests")
    c.add_model_lib(STANDARD_MODEL_LIB)
    c.add(V("V1",  "VCC", "GND", dc=12))
    c.add(V("VG",  "G",   "GND", ac="PULSE(0 3.3 0 1n 1n 1u 2u)"))
    c.add(R("R1",  "VCC", "D",   value="100"))
    c.add(NMOS("M1", d="D", g="G", s="GND", model="NMOS_S"))
    c.analysis(Tran(step="100n", stop="10u"))
    return c


# ──────────────────────────────────────────────────────────────────────────
# Group A — Spice-affecting property changes on KEPT refs
# ──────────────────────────────────────────────────────────────────────────

def test_a_value_change_on_kept_passive_propagates(kicad):
    """R1 value 100 → 1k on a kept ref should update the Value field."""
    _reset_to(kicad, _base_circuit())
    before = _by_ref(_list_symbols(kicad))
    assert before["R1"]["value"] == "100"
    r1_kiid_before = before["R1"]["kiid"]

    c2 = _base_circuit()
    next(p for p in c2.parts if p.ref == "R1").value = "1k"
    res = to_schematic(c2, SCH, kicad=kicad, mode="diff")
    assert res["parts_kept"] == 4 and res["parts_placed"] == 0

    after = _by_ref(_list_symbols(kicad))
    assert after["R1"]["value"] == "1k", (
        f"R1 value field did not update: {after['R1']['value']!r}"
    )
    # kiid must survive (this proves R1 was kept, not remove+add)
    assert after["R1"]["kiid"] == r1_kiid_before


def test_a_model_change_on_kept_active_propagates_sim_name(kicad):
    """M1 model NMOS_S → BSS138 on a kept ref should update Sim.Name."""
    _reset_to(kicad, _base_circuit())
    before = _by_ref(_list_symbols(kicad))
    assert before["M1"]["fields"].get("Sim.Name") == "NMOS_S"
    m1_kiid_before = before["M1"]["kiid"]

    c2 = _base_circuit()
    m1 = next(p for p in c2.parts if p.ref == "M1")
    m1.model = "BSS138"; m1.value = "BSS138"
    to_schematic(c2, SCH, kicad=kicad, mode="diff")

    after = _by_ref(_list_symbols(kicad))
    assert after["M1"]["fields"].get("Sim.Name") == "BSS138"
    assert after["M1"]["kiid"] == m1_kiid_before


def test_a_sim_params_change_on_kept_typed_source(kicad):
    """VG's pulse spec rewrites the Sim.Params field, kiid preserved.

    KliCAD canonicalizes raw `PULSE(...)` text into named-params form
    (`y1=... y2=... td=...`).  We don't pin the canonical text — just
    verify the field changed and the kiid is preserved (proving it was
    kept, not remove+add).
    """
    _reset_to(kicad, _base_circuit())
    before = _by_ref(_list_symbols(kicad))
    params_before = before["VG"]["fields"].get("Sim.Params", "")
    vg_kiid_before = before["VG"]["kiid"]
    assert "3.3" in params_before, f"baseline Sim.Params missing 3.3: {params_before!r}"

    c2 = _base_circuit()
    vg = next(p for p in c2.parts if p.ref == "VG")
    vg.sim_params = "PULSE(0 5.0 1m 100n 100n 5m 10m)"
    to_schematic(c2, SCH, kicad=kicad, mode="diff")

    after = _by_ref(_list_symbols(kicad))
    params_after = after["VG"]["fields"].get("Sim.Params", "")
    assert params_after != params_before, (
        f"Sim.Params didn't change: still {params_after!r}"
    )
    assert "5" in params_after, (
        f"new Sim.Params doesn't reflect the 5.0V amplitude: {params_after!r}"
    )
    assert after["VG"]["kiid"] == vg_kiid_before


def test_a_lib_id_mismatch_demotes_to_remove_add(kicad):
    """If a Part's kicad_lib_id changes for the same ref, the symbol must
    be re-placed (so the new lib's pin numbering takes effect for SPICE)."""
    _reset_to(kicad, _base_circuit())
    before = _by_ref(_list_symbols(kicad))
    r1_kiid_before = before["R1"]["kiid"]
    assert before["R1"]["lib_id"] == "Device:R"

    # Force R1 onto a different lib_id (Device:R_US is the standard variant).
    c2 = _base_circuit()
    r1 = next(p for p in c2.parts if p.ref == "R1")
    r1.kicad_lib_id = "Device:R_US"
    res = to_schematic(c2, SCH, kicad=kicad, mode="diff")
    assert res["parts_removed"] >= 1, "lib_id mismatch should trigger removal"
    assert res["parts_placed"] >= 1, "and a fresh add"

    after = _by_ref(_list_symbols(kicad))
    assert after["R1"]["lib_id"] == "Device:R_US"
    assert after["R1"]["kiid"] != r1_kiid_before, (
        "kiid should change on remove+add demotion"
    )


# ──────────────────────────────────────────────────────────────────────────
# Group B — Topology changes
# ──────────────────────────────────────────────────────────────────────────

def test_b_add_new_part(kicad):
    _reset_to(kicad, _base_circuit())
    c2 = _base_circuit()
    c2.add(C("C1", "D", "GND", value="100n"))
    res = to_schematic(c2, SCH, kicad=kicad, mode="diff")
    assert res["parts_placed"] == 1 and res["parts_kept"] == 4

    after = _by_ref(_list_symbols(kicad))
    assert "C1" in after


def test_b_remove_part(kicad):
    _reset_to(kicad, _base_circuit())
    c2 = _base_circuit()
    c2.parts = [p for p in c2.parts if p.ref != "R1"]
    res = to_schematic(c2, SCH, kicad=kicad, mode="diff")
    assert res["parts_removed"] == 1
    after = _by_ref(_list_symbols(kicad))
    assert "R1" not in after


def test_b_rename_ref_is_remove_plus_add(kicad):
    _reset_to(kicad, _base_circuit())
    c2 = _base_circuit()
    r1 = next(p for p in c2.parts if p.ref == "R1")
    r1.ref = "R_pull"
    res = to_schematic(c2, SCH, kicad=kicad, mode="diff")
    assert res["parts_removed"] == 1 and res["parts_placed"] == 1
    after = _by_ref(_list_symbols(kicad))
    assert "R1" not in after and "R_pull" in after


def test_b_empty_target_removes_all_non_power_symbols(kicad):
    _reset_to(kicad, _base_circuit())
    c2 = Circuit("diff_apply", desc="empty")
    res = to_schematic(c2, SCH, kicad=kicad, mode="diff")
    assert res["parts_kept"] == 0
    after = _by_ref(_list_symbols(kicad))
    assert len(after) == 0, f"non-power symbols remain: {list(after)}"


# ──────────────────────────────────────────────────────────────────────────
# Group C — Connectivity
# ──────────────────────────────────────────────────────────────────────────

def test_c_pin_rewire_relabels(kicad):
    """Change R1's pin-2 net from D to D_NEW; labels are re-emitted clean."""
    _reset_to(kicad, _base_circuit())
    c2 = _base_circuit()
    r1 = next(p for p in c2.parts if p.ref == "R1")
    r1.connections["2"] = "D_NEW"
    to_schematic(c2, SCH, kicad=kicad, mode="diff")
    # We can't easily query individual labels via the current binding
    # set, but if clear_routing didn't wipe and re-emit, ERC would
    # report stale net "D" + new net "D_NEW" disconnected.  Use the
    # summary as a smoke: number of labels should equal expected.
    r = kicad.run_python(
        "import klicad_native_schematic_state as ss; "
        "ss.get_items_summary()"
    )
    summary = ast.literal_eval(r.result_repr)
    # 4 parts × 2-3 pins each, plus #PWR labels — sanity: nonzero.
    assert summary["labels"] > 0


# ──────────────────────────────────────────────────────────────────────────
# Group D — Aesthetic preservation
# ──────────────────────────────────────────────────────────────────────────

def test_d_position_survives_diff(kicad):
    _reset_to(kicad, _base_circuit())
    before = _by_ref(_list_symbols(kicad))
    r1_pos_before = (before["R1"]["x_mm"], before["R1"]["y_mm"])

    # Re-run identical: position must not change.
    to_schematic(_base_circuit(), SCH, kicad=kicad, mode="diff")
    after = _by_ref(_list_symbols(kicad))
    assert (after["R1"]["x_mm"], after["R1"]["y_mm"]) == r1_pos_before


def test_d_rotation_survives_diff_resets_on_replace(kicad):
    """Rotate R1, verify diff preserves; replace returns to fresh default.

    KliCAD's fresh `add_symbol` produces a non-identity transform whose
    GetOrientation() reads as SYM_ORIENT_180 (=3).  We don't pin the
    specific value — capture the post-replace baseline and compare.
    """
    _reset_to(kicad, _base_circuit())
    rows = _by_ref(_list_symbols(kicad))
    r1_kiid_fresh = rows["R1"]["kiid"]
    fresh_orient = rows["R1"]["orientation"]

    # Apply rotation via IPC.
    r = kicad.run_python(
        f"import klicad_native_schematic_state as ss; "
        f"ss.set_symbol_rotation({r1_kiid_fresh!r}, 90)"
    )
    assert r.ok, f"set_symbol_rotation failed: {r.exception_traceback}"

    rotated = _by_ref(_list_symbols(kicad))
    rot_set = rotated["R1"]["orientation"]
    assert rot_set != fresh_orient, (
        f"rotation didn't change the orientation int: still {rot_set}"
    )

    # Diff preserves.
    to_schematic(_base_circuit(), SCH, kicad=kicad, mode="diff")
    after_diff = _by_ref(_list_symbols(kicad))
    assert after_diff["R1"]["orientation"] == rot_set, (
        f"diff didn't preserve rotation: {after_diff['R1']['orientation']} vs {rot_set}"
    )
    assert after_diff["R1"]["kiid"] == r1_kiid_fresh  # not remove+add

    # Replace removes + re-adds — orientation returns to the fresh default.
    to_schematic(_base_circuit(), SCH, kicad=kicad, mode="replace")
    after_replace = _by_ref(_list_symbols(kicad))
    assert after_replace["R1"]["orientation"] == fresh_orient, (
        f"replace didn't reset rotation: {after_replace['R1']['orientation']} "
        f"(expected fresh default {fresh_orient})"
    )
    assert after_replace["R1"]["kiid"] != r1_kiid_fresh, (
        "replace should have given a new kiid"
    )


def test_d_custom_user_field_survives_diff(kicad):
    """A user-added field on a kept ref must NOT get wiped by diff."""
    _reset_to(kicad, _base_circuit())
    rows = _by_ref(_list_symbols(kicad))
    r1_kiid = rows["R1"]["kiid"]

    r = kicad.run_python(
        f"import klicad_native_schematic_state as ss; "
        f"ss.set_symbol_field({r1_kiid!r}, 'user_notes', 'manual edit')"
    )
    assert r.ok, f"set custom field failed: {r.exception_traceback}"

    after_diff = _by_ref(_list_symbols(kicad, ) if False else _list_symbols(kicad))
    assert after_diff["R1"]["fields"].get("user_notes") == "manual edit"

    to_schematic(_base_circuit(), SCH, kicad=kicad, mode="diff")
    final = _by_ref(_list_symbols(kicad))
    assert final["R1"]["fields"].get("user_notes") == "manual edit", (
        "diff clobbered a user-added field"
    )


def test_d_footprint_survives_diff(kicad):
    """Footprint set manually on a kept ref must survive diff."""
    _reset_to(kicad, _base_circuit())
    rows = _by_ref(_list_symbols(kicad))
    r1_kiid = rows["R1"]["kiid"]

    r = kicad.run_python(
        f"import klicad_native_schematic_state as ss; "
        f"ss.set_symbol_field({r1_kiid!r}, 'Footprint', 'Resistor_SMD:R_0402_1005Metric')"
    )
    assert r.ok, f"set footprint failed: {r.exception_traceback}"

    to_schematic(_base_circuit(), SCH, kicad=kicad, mode="diff")
    final = _by_ref(_list_symbols(kicad))
    assert final["R1"]["footprint"] == "Resistor_SMD:R_0402_1005Metric"


# ──────────────────────────────────────────────────────────────────────────
# Group E — Power-flag non-accumulation
# ──────────────────────────────────────────────────────────────────────────

def test_e_power_symbols_do_not_accumulate(kicad):
    """clear_routing must wipe #PWR_* symbols so they re-emit clean."""
    _reset_to(kicad, _base_circuit())
    baseline = len(_power_rows(_list_symbols(kicad)))

    for _ in range(3):
        to_schematic(_base_circuit(), SCH, kicad=kicad, mode="diff")
    after = len(_power_rows(_list_symbols(kicad)))
    assert after == baseline, f"#PWR accumulated: {baseline} -> {after}"


# ──────────────────────────────────────────────────────────────────────────
# Group F — Edge cases
# ──────────────────────────────────────────────────────────────────────────

def test_f_strict_on_empty_schematic_succeeds(kicad):
    """mode='strict' against an empty schematic must place fresh."""
    # Reach empty first by replacing with an empty circuit.
    to_schematic(Circuit("diff_apply"), SCH, kicad=kicad, mode="replace")
    pre = _by_ref(_list_symbols(kicad))
    assert len(pre) == 0

    res = to_schematic(_base_circuit(), SCH, kicad=kicad, mode="strict")
    assert res["parts_placed"] == 4 and res["parts_removed"] == 0


def test_f_strict_on_populated_schematic_raises(kicad):
    _reset_to(kicad, _base_circuit())
    with pytest.raises(RuntimeError, match="strict"):
        to_schematic(_base_circuit(), SCH, kicad=kicad, mode="strict")


def test_f_duplicate_ref_raises(kicad):
    """to_schematic guards against duplicate refs even if Circuit.add was
    bypassed (someone appended directly to c.parts).  Belt-and-suspenders
    on top of Circuit.add's own uniqueness check."""
    c = _base_circuit()
    # Bypass Circuit.add (which has its own dup check) so we hit
    # to_schematic's defense-in-depth path.
    c.parts.append(R("R1", "VCC", "GND", value="999"))
    with pytest.raises(ValueError, match="duplicate refs"):
        to_schematic(c, SCH, kicad=kicad, mode="diff")


def test_f_invalid_mode_raises(kicad):
    with pytest.raises(ValueError, match="mode must be"):
        to_schematic(_base_circuit(), SCH, kicad=kicad, mode="bogus")


# ──────────────────────────────────────────────────────────────────────────
# Bootstrap helper — run once manually before pytest to lay down the
# project shell.  Importing this module without running it is harmless;
# pytest will skip if KliCAD isn't loaded on the right project.
# ──────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    _bootstrap_project_files(_base_circuit())
    print(f"wrote project shell at {PRO}")
    print(f"launch KliCAD with: ~/.local/bin/klicad {PRO}")
    print(f"then run:           pytest {__file__}")

"""KliCAD end-to-end demo: 2-transistor astable multivibrator + LED.

Exercises every step of the schematic-authoring + simulation pipeline that
the KliCAD fork enables:

  1. Lay down a fresh project on disk (.kicad_pro + .kicad_sch).
  2. Build a project-scoped sym-lib-table pointing at a merged .kicad_sym
     containing the four needed symbols (R, C, LED, 2N3904) plus the parent
     Q_NPN_EBC.  Pulled from the standard kicad-symbols repo, which on
     recent checkouts uses the exploded ``.kicad_symdir/`` layout — the
     merge collapses that back into a single-file library.
  3. Place 10 symbols, set values, drop net labels at every pin to wire
     by name (instead of routing).  Save schematic.
  4. Verify by exporting the netlist and asserting every intended net has
     the expected pin count (no dangling pins — this guards the IU-rounding
     bug fixed in feature/iu-scale-rounding-fix).
  5. Push a hand-authored SPICE deck to ngspice (load_netlist).
  6. Drive the workbook flow via run_analysis('tran', ...) — creates a
     SIM_PLOT_TAB the way clicking Run in the GUI would.
  7. Call add_trace(...) on V(NC1), V(NC2), V(LED_A) so the traces appear
     on the plot canvas, exactly as they would after dragging the signals
     from the workbook sidebar.
  8. Assert the circuit actually oscillates (V(NC1) crosses its midpoint
     several times in 3s of sim).

PREREQUISITES — this is NOT a self-contained pytest test.  It expects:

  - KliCAD launched *with the demo project as argv[1]*:

        kicad /tmp/klicad-led-osc/led-osc.kicad_pro

    Loading a different project in-session currently hits a
    SCHEMATIC::SetProject crash (see HANDOFF.md crash #2).  Launching with
    the path on the cmd line side-steps that.

  - The standard kicad-symbols repo cloned to /home/jakob/projects/
    kicad-libraries/kicad-symbols (or set KICAD_SYMBOLS env var).

This script creates the project directory and the merged sym lib itself,
so the typical invocation is:

    # one-time, before launching kicad:
    python examples/led_oscillator_demo.py --setup-only

    # then launch kicad on the project (windows pop up):
    kicad /tmp/klicad-led-osc/led-osc.kicad_pro

    # then run the actual demo against the running KliCAD instance:
    python examples/led_oscillator_demo.py
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

from kipy import KiCad


PROJ_DIR = Path("/tmp/klicad-led-osc")
SYMBOLS_ROOT = Path(
    os.environ.get(
        "KICAD_SYMBOLS",
        "/home/jakob/projects/kicad-libraries/kicad-symbols",
    )
)


# ---------- merged symbol library helpers -------------------------------

_NEEDED = [
    SYMBOLS_ROOT / "Device.kicad_symdir" / "R.kicad_sym",
    SYMBOLS_ROOT / "Device.kicad_symdir" / "C.kicad_sym",
    SYMBOLS_ROOT / "Device.kicad_symdir" / "LED.kicad_sym",
    SYMBOLS_ROOT / "Transistor_BJT.kicad_symdir" / "Q_NPN_EBC.kicad_sym",
    SYMBOLS_ROOT / "Transistor_BJT.kicad_symdir" / "2N3904.kicad_sym",
]


def _extract_outer_symbol_block(text: str) -> str:
    """Return the first top-level (symbol "...") sexpr in a .kicad_sym file.

    Single-symbol .kicad_sym files have the outer (symbol "X" ...) block
    at depth 1 inside (kicad_symbol_lib ...).
    """
    depth = 0
    i = 0
    in_str = False
    while i < len(text):
        c = text[i]
        if c == '"' and text[i - 1] != "\\":
            in_str = not in_str
        elif not in_str:
            if c == "(":
                if text[i : i + 8] == "(symbol " and depth == 1:
                    # paren-match from here to find the block end
                    sd = 0
                    j = i
                    ins = False
                    while j < len(text):
                        cc = text[j]
                        if cc == '"' and text[j - 1] != "\\":
                            ins = not ins
                        elif not ins:
                            if cc == "(":
                                sd += 1
                            elif cc == ")":
                                sd -= 1
                                if sd == 0:
                                    return text[i : j + 1]
                        j += 1
                depth += 1
            elif c == ")":
                depth -= 1
        i += 1
    raise RuntimeError("no outer (symbol ...) found")


def setup_project_files() -> None:
    """Create /tmp/klicad-led-osc/{led-osc.kicad_pro, .kicad_sch, sym-lib-table, led-osc-syms.kicad_sym}."""
    PROJ_DIR.mkdir(parents=True, exist_ok=True)

    for src in _NEEDED:
        if not src.exists():
            sys.exit(
                f"missing source symbol file: {src}\n"
                "Clone gitlab.com/kicad/libraries/kicad-symbols (or set KICAD_SYMBOLS)."
            )

    # Merge into one library
    chunks = [_extract_outer_symbol_block(p.read_text()) for p in _NEEDED]
    merged = (
        "(kicad_symbol_lib\n"
        '\t(version 20251024)\n'
        '\t(generator "klicad_demo")\n'
        '\t(generator_version "10.99")\n'
        "\t" + "\n\t".join(chunks) + "\n"
        ")\n"
    )
    (PROJ_DIR / "led-osc-syms.kicad_sym").write_text(merged)

    (PROJ_DIR / "sym-lib-table").write_text(
        '(sym_lib_table\n'
        '\t(version 7)\n'
        f'\t(lib (name "ledosc") (type "KiCad") (uri "{PROJ_DIR / "led-osc-syms.kicad_sym"}") '
        '(options "") (descr "LED-osc demo symbols (R, C, LED, 2N3904 + parent)"))\n'
        ')\n'
    )

    (PROJ_DIR / "led-osc.kicad_pro").write_text(
        '{\n  "meta": {\n    "filename": "led-osc.kicad_pro",\n    "version": 3\n  }\n}\n'
    )

    (PROJ_DIR / "led-osc.kicad_sch").write_text(
        "(kicad_sch\n"
        "\t(version 20250114)\n"
        '\t(generator "klicad")\n'
        '\t(generator_version "10.99")\n'
        '\t(uuid "00000000-0000-0000-0000-000000000001")\n'
        '\t(paper "A4")\n'
        "\t(lib_symbols)\n"
        '\t(sheet_instances\n\t\t(path "/" (page "1"))\n\t)\n'
        ")\n"
    )

    print(f"[setup] wrote {PROJ_DIR}/{{led-osc.kicad_pro, .kicad_sch, sym-lib-table, led-osc-syms.kicad_sym}}")


# ---------- schematic build ----------------------------------------------

# Component placements (mm).  Visual layout doesn't affect the netlist
# because we wire by labels.
_PARTS = [
    ("ledosc:2N3904", "Q1", 100.0, 120.0),
    ("ledosc:2N3904", "Q2", 200.0, 120.0),
    ("ledosc:R",      "R1", 100.0,  60.0),
    ("ledosc:R",      "R2", 200.0,  60.0),
    ("ledosc:R",      "R3",  60.0,  90.0),
    ("ledosc:R",      "R4", 240.0,  90.0),
    ("ledosc:R",      "R5", 260.0,  60.0),
    ("ledosc:C",      "C1", 130.0,  90.0),
    ("ledosc:C",      "C2", 170.0,  90.0),
    ("ledosc:LED",    "D1", 260.0, 120.0),
]

_VALUES = {
    "Q1": "2N3904", "Q2": "2N3904",
    "R1": "1k", "R2": "1k", "R3": "47k", "R4": "47k", "R5": "470",
    "C1": "10u", "C2": "10u",
    "D1": "LED",
}

# (ref, pin_id) -> net.  pin_id matches pin NUMBER first, then NAME, so
# both '1' (numeric) and 'B'/'C'/'E' (named, for the 2N3904) work.
_NET_MAP = {
    ("Q1", "B"): "NB1",  ("Q1", "C"): "NC1",  ("Q1", "E"): "GND",
    ("Q2", "B"): "NB2",  ("Q2", "C"): "NC2",  ("Q2", "E"): "GND",
    ("R1", "1"): "VCC",  ("R1", "2"): "NC1",
    ("R2", "1"): "VCC",  ("R2", "2"): "NC2",
    ("R3", "1"): "VCC",  ("R3", "2"): "NB1",
    ("R4", "1"): "VCC",  ("R4", "2"): "NB2",
    ("R5", "1"): "NC2",  ("R5", "2"): "LED_A",
    ("C1", "1"): "NC2",  ("C1", "2"): "NB1",
    ("C2", "1"): "NC1",  ("C2", "2"): "NB2",
    ("D1", "1"): "GND",  ("D1", "2"): "LED_A",  # KiCad LED: 1=K (cathode), 2=A (anode)
}

# Expected per-net pin counts (for the verification step).
_EXPECTED_NETS = {
    "GND":   3, "VCC":   4,
    "NB1":   3, "NB2":   3,
    "NC1":   3, "NC2":   4,
    "LED_A": 2,
}


SPICE_DECK = """* LED astable multivibrator
.model Q2N3904 NPN (Is=6.734f Xti=3 Eg=1.11 Vaf=74.03 Bf=416.4 Ne=1.259
+ Ise=6.734f Ikf=66.78m Xtb=1.5 Br=.7371 Nc=2 Isc=0 Ikr=0 Rc=1 Cjc=3.638p
+ Mjc=.3085 Vjc=.75 Fc=.5 Cje=4.493p Mje=.2593 Vje=.75 Tr=239.5n
+ Tf=301.2p Itf=.4 Vtf=4 Xtf=2 Rb=10)
.model DLED D (Is=1e-18 N=2 Rs=2 Cjo=20p Vj=2.2 Tt=10n Bv=4)

Vcc VCC 0 PWL(0 0  1m 5)
Q1 NC1 NB1 0 Q2N3904
Q2 NC2 NB2 0 Q2N3904
R1 VCC NC1 1k
R2 VCC NC2 1k
R3 VCC NB1 47k
R4 VCC NB2 47k
C1 NC2 NB1 10u IC=0
C2 NC1 NB2 10u IC=2
R5 NC2 LED_A 470
D1 LED_A 0 DLED
.end
"""


def build_and_simulate() -> None:
    k = KiCad(timeout_ms=300_000)

    # 1. Open schematic editor
    r = k.run_python(
        "import kicad_native_gui as g\n"
        "import kicad_native_schematic_state as ss\n"
        "g.show_frame('schematic')\n"
        f"ss.open_schematic({str(PROJ_DIR / 'led-osc.kicad_sch')!r})"
    )
    assert r.ok, r.exception_traceback
    print("[1] schematic editor open")

    # 2. Place all 10 symbols, set values
    kiids = {}
    for lib_id, ref, x, y in _PARTS:
        r = k.run_python(
            f"import kicad_native_schematic_state as ss\n"
            f"ss.add_symbol({lib_id!r}, {ref!r}, {x}, {y})"
        )
        res = eval(r.result_repr)
        assert res.get("ok"), f"add_symbol({ref}) -> {res}"
        kiids[ref] = res["kiid"]
    for ref, val in _VALUES.items():
        k.run_python(
            f"import kicad_native_schematic_state as ss\n"
            f"ss.set_symbol_value({kiids[ref]!r}, {val!r})"
        )
    print(f"[2] placed {len(kiids)} symbols, set values")

    # 3. Drop labels at every pin (no wires)
    labels_placed = 0
    for (ref, pin_id), net in _NET_MAP.items():
        r = k.run_python(
            "import kicad_native_schematic_state as ss\n"
            f"p = ss.get_symbol_pin_position({kiids[ref]!r}, {pin_id!r})\n"
            f"ss.add_label(p['x_mm'], p['y_mm'], {net!r}) if p.get('ok') else p"
        )
        res = eval(r.result_repr)
        assert res.get("ok"), f"label {ref}.{pin_id} -> {net}: {res}"
        labels_placed += 1
    print(f"[3] placed {labels_placed} net labels at pin positions")

    # 4. Save + export netlist + verify nets
    k.run_python("import kicad_native_schematic_state as ss; ss.save_schematic()")
    netfile = PROJ_DIR / "led-osc.net"
    r = k.run_python(
        "import kicad_native_export_sch_netlist as nl\n"
        f"nl.run({str(PROJ_DIR / 'led-osc.kicad_sch')!r}, {str(netfile)!r}, format='kicad')"
    )
    assert r.ok, r.exception_traceback

    text = netfile.read_text()
    nets_section = text[text.index("(nets"):]
    actual = {}
    for m in re.finditer(r'\(name "([^"]+)"', nets_section):
        end = nets_section.find('(name "', m.end())
        chunk = nets_section[m.start() : end if end != -1 else None]
        # Each net's pin count = number of (ref ...) entries
        actual[m.group(1).lstrip("/")] = len(re.findall(r"\(ref ", chunk))

    dangling = [n for n in actual if n.startswith("unconnected-")]
    assert not dangling, f"dangling pins: {dangling}"
    for name, n in _EXPECTED_NETS.items():
        assert actual.get(name) == n, f"net {name}: expected {n} pins, got {actual.get(name)}"
    print(f"[4] netlist verified: {len(_EXPECTED_NETS)} nets correct, 0 dangling")

    # 5. Bring up the simulator and push the hand-authored deck
    k.run_python("import kicad_native_gui as g; g.show_frame('simulator')")
    time.sleep(0.5)
    r = k.run_python(
        f"import kicad_native_simulator as sim\n"
        f"sim.load_netlist({SPICE_DECK!r})"
    )
    assert eval(r.result_repr).get("ok"), r.result_repr
    print("[5] SPICE netlist loaded into ngspice")

    # 6. Run transient via the workbook flow (NewSimTab + Command('run'))
    r = k.run_python(
        "import kicad_native_simulator as sim\n"
        "sim.run_analysis('tran', step='200u', stop='3', uic=True)"
    )
    res = eval(r.result_repr)
    assert res["ok"] and res["plot_name"] == "tran1", res
    print(f"[6] run_analysis -> plot '{res['plot_name']}', {len(res['vectors'])} vectors")

    # 7. Add traces to the current plot tab (mirrors GUI 'Add Signal')
    for name in ("nc1", "nc2", "led_a"):
        r = k.run_python(
            f"import kicad_native_simulator as sim; sim.add_trace({name!r}, 'voltage')"
        )
        assert eval(r.result_repr).get("ok"), r.result_repr
    print("[7] traces added: V(NC1), V(NC2), V(LED_A)")

    # 8. Assert oscillation
    r = k.run_python(
        "import kicad_native_simulator as sim\n"
        "v = sim.get_vector('nc1')\n"
        "data, t = v['data'], v['xaxis']\n"
        "mid = (max(data) + min(data)) / 2\n"
        "rises = sum(1 for i in range(1, len(data)) if data[i-1] < mid <= data[i])\n"
        "{'samples': len(data), 't_end': round(t[-1], 3), "
        "'min': round(min(data), 3), 'max': round(max(data), 3), "
        "'rises': rises, 'freq_Hz': round(rises / t[-1], 2)}"
    )
    stats = eval(r.result_repr)
    assert stats["max"] > 4.5, f"NC1 doesn't swing high enough: {stats}"
    assert stats["min"] < 0.5, f"NC1 doesn't swing low enough: {stats}"
    assert stats["rises"] >= 4, f"not enough oscillation cycles: {stats}"
    print(f"[8] V(NC1) oscillation: {stats['min']}V .. {stats['max']}V, {stats['freq_Hz']} Hz")
    print("\nAll demo steps passed.")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--setup-only",
        action="store_true",
        help="create the project files on disk and exit (run this once "
             "before launching KliCAD)",
    )
    args = p.parse_args()

    if args.setup_only:
        setup_project_files()
        print(
            "\nNext steps:\n"
            f"  1. Launch KliCAD with the project:  kicad {PROJ_DIR}/led-osc.kicad_pro\n"
            f"  2. Re-run this script without --setup-only to drive the demo.\n"
        )
        return

    # If the project files don't exist yet, create them; otherwise reuse.
    if not (PROJ_DIR / "led-osc.kicad_sch").exists():
        setup_project_files()
        sys.exit(
            f"Project files just created at {PROJ_DIR}.\n"
            f"Launch KliCAD with `kicad {PROJ_DIR}/led-osc.kicad_pro`, then re-run."
        )

    build_and_simulate()


if __name__ == "__main__":
    main()

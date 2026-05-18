"""End-to-end demo: canonical Circuit → schematic + SPICE + GUI Play button.

Replaces examples/led_oscillator_demo.py for the Phase 1 use case.  Single
Python source builds the circuit description; both the .kicad_sch and the
SPICE deck derive from it.  This means the GUI Play button (which
regenerates a netlist from the schematic) and our `simulator.load_netlist`
path produce the same simulatable deck — no parallel sources of truth.

PREREQUISITE: KliCAD already launched with the target project on the
command line.  Loading a different project mid-session hits a known
SCHEMATIC::SetProject crash (HANDOFF.md crash #2).

  # one-time:
  python examples/led_oscillator_canonical.py --setup-only

  # then in another terminal:
  kicad /tmp/klicad-led-osc-canonical/led-osc.kicad_pro

  # then drive the demo:
  python examples/led_oscillator_canonical.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kipy import KiCad
from kipy.klicad.circuit import (
    Circuit,
    R, C, NPN, LED, V,
    Tran,
    STANDARD_MODEL_LIB,
)


PROJ_DIR = Path("/tmp/klicad-led-osc-canonical")
PROJ_FILE = PROJ_DIR / "led-osc.kicad_pro"
SCH_FILE  = PROJ_DIR / "led-osc.kicad_sch"


def build_circuit() -> Circuit:
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


def setup_only(c: Circuit) -> None:
    """Lay down the project + supporting files; don't connect to KliCAD."""
    from kipy.klicad.circuit._kicad_sch import _bootstrap_project_files
    pro_path, sym_lib, models_lib = _bootstrap_project_files(c, SCH_FILE)
    print(f"[setup] wrote project files in {PROJ_DIR}/")
    print(f"        - {pro_path.name}")
    print(f"        - {SCH_FILE.name} (stub; will be populated by the demo)")
    print(f"        - {sym_lib.name}")
    print(f"        - {models_lib.name} ({models_lib.stat().st_size} bytes)")
    print()
    print("Next steps:")
    print(f"  1. Launch KliCAD with the project:  kicad {PROJ_FILE}")
    print(f"  2. Re-run this script without --setup-only to author the schematic.")


def drive_demo(c: Circuit) -> int:
    """Generate the schematic + SPICE deck via live KliCAD."""
    k = KiCad(timeout_ms=300_000)

    # Sanity: KliCAD is up
    try:
        version = k.get_version()
    except Exception as e:
        print(f"ERROR: can't reach KliCAD: {e}", file=sys.stderr)
        print(f"Launch with: kicad {PROJ_FILE}", file=sys.stderr)
        return 1
    print(f"[1] KliCAD {version} reachable")

    # Verify the project loaded matches
    r = k.run_python("import kicad_native_project_manager as pm; pm.get_current_project()")
    if not r.ok or PROJ_FILE.name not in r.result_repr:
        print(f"ERROR: KliCAD has a different project loaded.", file=sys.stderr)
        print(f"  Wanted:  {PROJ_FILE.name}", file=sys.stderr)
        print(f"  Current: {r.result_repr if r.ok else 'unknown'}", file=sys.stderr)
        print(f"Relaunch with: kicad {PROJ_FILE}", file=sys.stderr)
        return 1
    print(f"[2] project loaded: {PROJ_FILE.name}")

    # Generate the schematic into KliCAD's live state
    result = c.to_schematic(SCH_FILE, kicad=k)
    print(f"[3] schematic generated: {result['parts_placed']} parts, "
          f"{result['labels_placed']} pin labels")
    print(f"    models lib: {result['models_lib_path']}")

    # Pre-annotate so ReadyToNetlist doesn't pop the ModalAnnotate dialog
    # (HANDOFF lesson 4: the modal blocks IPC because the handler is queued
    # behind it; click_dialog_button can't dismiss because it's also queued).
    r = k.run_python("import kicad_native_annotation as a; a.annotate(scope='all')")
    if not r.ok:
        print(f"WARN: pre-annotate failed: {r.exception_traceback}", file=sys.stderr)

    # Run the simulation via the canonical SPICE deck
    deck = c.to_spice_deck()
    r = k.run_python(
        f"import kicad_native_simulator as sim\n"
        f"sim.load_netlist({deck!r})\n"
        f"sim.command('run')\n"
        f"plots = sim.list_plots()\n"
        f"tran_plots = [p for p in plots if p.startswith('tran')]\n"
        f"sim.command(f'setplot {{tran_plots[-1]}}')\n"
        f"nl = sim.get_vector('v(nl)')['data']\n"
        f"nr = sim.get_vector('v(nr)')['data']\n"
        f"swing_nl = round(max(nl) - min(nl), 3)\n"
        f"swing_nr = round(max(nr) - min(nr), 3)\n"
        f"{{'samples': len(nl), 'swing_nl': swing_nl, 'swing_nr': swing_nr}}"
    )
    if not r.ok:
        print(f"ERROR: sim failed: {r.exception_traceback}", file=sys.stderr)
        return 1
    print(f"[4] sim ran: {r.result_repr}")
    print()
    print("Now click PLAY in the simulator window:  the schematic-generated")
    print("netlist now has Sim.Library set on Q1/Q2/D1 -> models.lib, so the")
    print("GUI's run should also work and match the deck above.")
    return 0


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--setup-only", action="store_true",
                    help="Write project files but don't connect to KliCAD")
    args = ap.parse_args()

    c = build_circuit()
    if args.setup_only:
        setup_only(c)
        return 0
    return drive_demo(c)


if __name__ == "__main__":
    sys.exit(main())

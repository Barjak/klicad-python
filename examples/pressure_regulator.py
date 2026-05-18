"""Pressure regulator analog front-end — canonical-circuit DSL example.

Source spec: ~/projects/arduino/netlist/pressure_regulator/NETLIST_Rev3.2.txt.
That circuit drives an Arduino Nano 33 IoT controlling a 12V PWM fan and
a 12V solenoid with reed-switch feedback.  We model the discrete-analog
parts the canonical-circuit DSL can express; the Nano, the Kavlico
sensor IC, the BS170 MOSFET, the fan motor, the reed switch, and the
solenoid coil get substituted by V sources / loads / open nodes:

  - Arduino digital outputs           -> V sources (PULSE for the fan
                                         PWM, step for the solenoid
                                         enable).
  - Arduino +5V / +3V3 rails          -> V sources.
  - Pressure sensor U3 (P1K-5)        -> V source modelling the analog
                                         output (3.5" H2O ~ 2.875V).
  - BS170 MOSFET Q1                   -> omitted (the DSL has no NMOS
                                         primitive yet); the solenoid
                                         driver chain stops at the
                                         BS170 GATE node.
  - Reed switch SW1                   -> the "valve closed" state
                                         (switch open: the RC network
                                         settles to +3V3 via R4).
  - Fan motor M1, solenoid coil L1    -> modelled as an inductor with
                                         the spec'd 60Ω DC resistance
                                         for the solenoid.

One transient analysis: the fan PWM at 25kHz, ~50% duty, fed into
Q2's base via R10.  We watch the inverted-collector waveform on
PWM_FAN to verify it falls below the fan's VIL spec (0.4V) during
the on-half of the cycle, and at the same time read the sensor's
DC operating point at SENSOR_A0 to confirm the voltage divider is
on-spec (2.875V × 10k/(2.7k+10k) ≈ 2.264V).

(An AC sweep on the sensor LPF would be the natural second analysis,
but the V class doesn't carry an AC stimulus magnitude yet — left
for a future DSL extension.)

Run with --spice-only to skip the live-KliCAD round-trip and just
print the SPICE deck + partition() summary.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running directly from a checkout.
HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent.parent))

from kipy.klicad.circuit import (
    Circuit, R, C, L, D, NPN, V, Tran, STANDARD_MODEL_LIB,
)


def build_circuit() -> Circuit:
    c = Circuit("pressure-regulator-fe",
                desc="Arduino Nano 33 IoT pressure regulator — analog front-end "
                     "(Rev 3.2, components the DSL can express).")
    c.add_model_lib(STANDARD_MODEL_LIB)

    # ── Power rails (modelled as ideal V sources) ──────────────────────────
    c.add(V("V12",  "+12V", "GND", dc=12.0))
    c.add(V("V5",   "+5V",  "GND", dc=5.0))
    c.add(V("V3V3", "+3V3", "GND", dc=3.3))
    c.add(C("C1",   "+12V", "GND", value="470u"))   # bulk

    # ── Sensor (Kavlico P1K-5) modelled as a 2.875V source ────────────────
    # Real sensor: 0.25-4.0V over 0-5" H2O range.  3.5" H2O ≈ 2.875V.
    c.add(V("VSENS",  "SENSOR_RAW", "GND", dc=2.875))
    c.add(C("C5",     "+5V", "GND", value="100n"))  # sensor decoupling

    # ── Sensor signal conditioning: divider + LPF ──────────────────────────
    # Ratio = 10k / (2.7k + 10k) = 0.787.  Corner ≈ 1/(2π·2.7k·1µF) = 59Hz.
    c.add(R("R1a",  "SENSOR_RAW", "SENSOR_A0", value="2.7k"))
    c.add(R("R1b",  "SENSOR_A0",  "GND",       value="10k"))
    c.add(C("C6",   "SENSOR_A0",  "GND",       value="1u"))

    # ── Fan PWM level shifter (D2 -> Q2 -> PWM_FAN) ───────────────────────
    # PULSE: 25kHz, 50% duty, 0/3.3V swing (Nano TTL on D2).
    c.add(V("VD2",  "PWM_OUT_D2", "GND",
             ac="PULSE(0 3.3 0 20n 20n 20u 40u)"))
    c.add(R("R10",  "PWM_OUT_D2", "Q2_BASE",  value="1k"))
    c.add(NPN("Q2", c="PWM_FAN",  b="Q2_BASE", e="GND", model="2N3904"))
    c.add(R("R6",   "+5V",        "PWM_FAN",  value="10k"))   # pull-up

    # ── Solenoid driver: D3 -> Q3 -> SOL_GATE (stops at BS170 gate) ───────
    c.add(V("VD3",  "SOL_OUT_D3", "GND", dc=0.0))    # commanded ON
    c.add(R("R7",   "SOL_OUT_D3", "Q3_BASE",   value="1k"))
    c.add(R("R9",   "+3V3",       "SOL_OUT_D3", value="10k"))  # boot safety
    c.add(NPN("Q3", c="SOL_GATE", b="Q3_BASE",  e="GND", model="2N3904"))
    c.add(R("R8",   "+12V",       "SOL_GATE",  value="10k"))   # gate pull-up

    # ── Solenoid coil + flyback clamp (cathode-to-cathode) ────────────────
    # The MOSFET is omitted; we model the solenoid as if its low-side
    # node (SOL_DRAIN) sits at +12V (MOSFET off, valve idle).  The
    # flyback diode/zener pair still hangs off SOL_DRAIN to show the
    # topology.
    c.add(L("L1",   "+12V",      "SOL_DRAIN", value="50m"))   # solenoid ~50mH
    c.add(D("D1",   k="D1D2_K",  a="SOL_DRAIN", model="1N4148"))
    c.add(D("D2",   k="D1D2_K",  a="+12V",      model="1N4148"))  # 4.7V zener stand-in

    # ── Reed switch RC filter (valve-closed state: SW1 open) ──────────────
    c.add(R("R4",   "+3V3",     "REED_RAW", value="10k"))
    c.add(C("C11",  "REED_RAW", "GND",      value="100n"))
    c.add(R("R5",   "REED_RAW", "REED_D15", value="1k"))

    # ── Analyses ──────────────────────────────────────────────────────────
    # Transient: 5 PWM cycles (40us period × 5 = 200us) is enough to see
    # PWM_FAN ripple settle.  The fan datasheet's VIL is 0.4V — we want
    # the BJT to pull PWM_FAN safely below that during the "on" half.
    c.analysis(Tran(step="200n", stop="200u", uic=False))

    return c


# ──────────────────────────────────────────────────────────────────────────
# Drivers
# ──────────────────────────────────────────────────────────────────────────

def spice_only(c: Circuit) -> int:
    """Print the SPICE deck.  No KliCAD needed."""
    print(f"=== SPICE deck ({c.name}) ===")
    print(c.to_spice_deck())
    return 0


def drive_demo(c: Circuit, proj_dir: Path) -> int:
    """Author the schematic into KliCAD + run a transient through ngspice."""
    from kipy.kicad import KiCad
    k = KiCad(timeout_ms=300_000)
    try:
        ver = k.get_version()
    except Exception as e:
        print(f"ERROR: cannot reach KliCAD: {e}", file=sys.stderr)
        return 1
    print(f"[1] KliCAD {ver} reachable")

    sch_path = proj_dir / f"{proj_dir.name}.kicad_sch"
    result = c.to_schematic(sch_path, kicad=k, layout="clustered", route=True)
    print(f"[2] schematic generated: {result['parts_placed']} parts, "
          f"{result['labels_placed']} labels, "
          f"{result.get('wires_placed','?')} wires")

    # Pre-annotate so ReadyToNetlist doesn't pop the ModalAnnotate dialog.
    r = k.run_python("import kicad_native_annotation as a; a.annotate(scope='all')")
    if not r.ok:
        print(f"WARN: pre-annotate failed: {r.exception_traceback}",
              file=sys.stderr)

    deck = c.to_spice_deck()
    r = k.run_python(
        f"import kicad_native_simulator as sim\n"
        f"sim.load_netlist({deck!r})\n"
        f"sim.command('run')\n"
        f"plots = sim.list_plots()\n"
        f"tran = [p for p in plots if p.startswith('tran')]\n"
        f"sim.command(f'setplot {{tran[-1]}}')\n"
        f"vfan = sim.get_vector('v(pwm_fan)')['data']\n"
        f"vsens_a0 = sim.get_vector('v(sensor_a0)')['data']\n"
        f"{{'tran_samples': len(vfan), 'pwm_fan_min': min(vfan),\n"
        f"  'pwm_fan_max': max(vfan), 'sensor_a0_dc': vsens_a0[-1]}}"
    )
    if not r.ok:
        print(f"sim failed: {r.exception_traceback}", file=sys.stderr)
        return 1
    print(f"[3] sim ran: {r.result_repr}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spice-only", action="store_true",
                    help="Print the SPICE deck + partition summary; "
                         "skip KliCAD round-trip.")
    ap.add_argument("--setup-only", action="store_true",
                    help="Lay down project files only.")
    ap.add_argument("--proj-dir", type=Path,
                    default=Path("/tmp/klicad-pressure-fe"),
                    help="Project directory for the live KliCAD run.")
    args = ap.parse_args()

    c = build_circuit()

    if args.spice_only:
        return spice_only(c)

    args.proj_dir.mkdir(parents=True, exist_ok=True)
    sch_path = args.proj_dir / f"{args.proj_dir.name}.kicad_sch"
    if args.setup_only:
        from kipy.klicad.circuit._kicad_sch import _bootstrap_project_files
        pro, syml, mods = _bootstrap_project_files(c, sch_path)
        print(f"[setup] {pro}")
        print(f"[setup] {syml}")
        print(f"[setup] {mods}")
        print(f"Launch: kicad {pro}")
        return 0

    return drive_demo(c, args.proj_dir)


if __name__ == "__main__":
    sys.exit(main())

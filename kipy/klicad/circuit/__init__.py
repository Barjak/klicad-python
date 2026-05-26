"""kipy.klicad.circuit — canonical circuit descriptions.

A Circuit is a Python object that fully describes an electrical circuit:
parts, named nets connecting them, initial conditions, and analyses to run.
The Python object itself is the source of truth.  Two derived artifacts get
generated from it:

  * a SPICE netlist (.to_spice_deck()) for direct simulation via ngspice
  * a KiCad schematic (.to_schematic()) for visual review + GUI Play button

Both views are derived from the same source, so there is no schematic/SPICE
divergence by construction.

Example:

    from kipy.klicad.circuit import Circuit, R, C, NPN, LED, V, Tran
    from kipy.klicad.circuit import STANDARD_MODEL_LIB

    c = Circuit("LED Oscillator", desc="2-transistor astable multivibrator")
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

    # Run the sim:
    deck = c.to_spice_deck()

    # Or generate the visual schematic for the GUI:
    c.to_schematic("/tmp/led-osc.kicad_sch")
"""

from pathlib import Path

from ._circuit import Circuit, NetMeta, NetKind
from ._part import (
    Part,
    R, C, L,
    D, LED,
    NPN, PNP,
    V, I,
    XSubckt,
    ALL_PARTS,
)
from ._model import ModelCard
from ._analyses import (
    Analysis,
    Tran, Ac, Dc, Op, Noise,
    Control,
    ALL_ANALYSES,
)
from ._kicad_sch import write_project_shell

# Absolute path to the bundled SPICE model starter library.  Pass to
# Circuit.add_model_lib(STANDARD_MODEL_LIB).
STANDARD_MODEL_LIB: str = str(Path(__file__).parent / "models" / "standard.lib")

__all__ = [
    "Circuit", "NetMeta", "NetKind",
    "Part", "R", "C", "L", "D", "LED", "NPN", "PNP", "V", "I", "XSubckt",
    "ModelCard",
    "Analysis", "Tran", "Ac", "Dc", "Op", "Noise", "Control",
    "ALL_PARTS", "ALL_ANALYSES",
    "STANDARD_MODEL_LIB",
    "write_project_shell",
]

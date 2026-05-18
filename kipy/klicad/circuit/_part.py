"""Part base + concrete part subclasses.

Pin names follow SPICE conventions (c/b/e for BJT, a/k for diode, +/- for
caps, d/g/s for MOSFET).  Each subclass declares:

  * spice_letter      first letter of the SPICE element line (R/C/L/D/Q/M/V/I)
  * pin_names         tuple of SPICE-semantic pin names
  * kicad_lib_id      'LibName:SymbolName' of the KiCad library symbol that
                      hosts the same shape with matching Sim.Pins
  * kicad_pin_map     dict mapping spice_pin -> kicad_pin_number
  * connections       dict mapping spice_pin -> net_name (filled per-instance)

The kicad_pin_map exists because KiCad pin NUMBERS aren't fixed across symbol
libraries (Q_NPN_EBC has 1=E 2=B 3=C; Q_NPN_BCE would have 1=B 2=C 3=E).
The map is anchored to the specific lib_id we pick.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar


@dataclass
class Part:
    """Base part.  Subclasses override class vars + __init__."""

    ref: str
    value: str = ""
    model: str = ""
    library: str = ""              # path to .lib with .model definition; "" = none
    footprint: str = ""            # PCB footprint hint (Library:Name); "" = none

    # Class-level metadata; subclasses override.
    kind:          ClassVar[str] = ""
    spice_letter:  ClassVar[str] = ""
    pin_names:     ClassVar[tuple[str, ...]] = ()
    kicad_lib_id:  ClassVar[str] = ""           # eg 'Device:R'
    kicad_pin_map: ClassVar[dict[str, str]] = {}  # spice_pin -> kicad_pin_num

    # Per-instance: pin name -> net name.  Filled by subclass __init__.
    connections: dict[str, str] = field(default_factory=dict, init=False)

    # ---- introspection ----

    def nets_used(self) -> list[str]:
        return list(self.connections.values())

    def validate(self) -> None:
        """Raise if pin set is wrong / any pin unconnected."""
        cls = type(self)
        if not self.ref:
            raise ValueError(f"{cls.__name__}: missing ref designator")
        missing = [p for p in cls.pin_names if not self.connections.get(p)]
        if missing:
            raise ValueError(
                f"{cls.__name__} {self.ref}: pins {missing} unconnected; "
                f"expected pin names {cls.pin_names}"
            )
        extra = [p for p in self.connections if p not in cls.pin_names]
        if extra:
            raise ValueError(
                f"{cls.__name__} {self.ref}: unexpected pins {extra}; "
                f"this part's pin names are {cls.pin_names}"
            )

    # ---- SPICE rendering ----

    def spice_line(self) -> str:
        """Default: <ref> <pin1> <pin2> ... <value-or-model>.

        KiCad reference designators (R1, C2, Q3) already start with the
        SPICE element letter, so we use ref verbatim — prepending
        spice_letter would give us 'RR1'.

        Subclasses with non-trivial SPICE syntax (V/I sources) override.
        """
        nets = " ".join(self.connections[p] for p in self.pin_names)
        suffix = self.model or self.value
        return f"{self.ref} {nets} {suffix}"

    # ---- serialization ----

    def to_dict(self) -> dict:
        d = {
            "kind":     self.kind,
            "ref":      self.ref,
            "value":    self.value,
            "model":    self.model,
            "library":  self.library,
            "footprint": self.footprint,
            "pins":     dict(self.connections),
        }
        return {k: v for k, v in d.items() if v != "" and v != {}}


# ──────────────────────────────────────────────────────────────────────────
# Passives: R, C, L
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class R(Part):
    """Resistor.  R(ref, n1, n2, value='1k')"""
    kind          = "R"
    spice_letter  = "R"
    pin_names     = ("1", "2")
    kicad_lib_id  = "Device:R"
    kicad_pin_map = {"1": "1", "2": "2"}

    def __init__(self, ref: str, n1: str, n2: str, value: str = "1k",
                 footprint: str = ""):
        super().__init__(ref=ref, value=value, footprint=footprint)
        self.connections = {"1": n1, "2": n2}


@dataclass
class C(Part):
    """Capacitor.  C(ref, plus, minus, value='1u')"""
    kind          = "C"
    spice_letter  = "C"
    pin_names     = ("1", "2")
    kicad_lib_id  = "Device:C"
    kicad_pin_map = {"1": "1", "2": "2"}

    def __init__(self, ref: str, plus: str, minus: str, value: str = "1u",
                 footprint: str = ""):
        super().__init__(ref=ref, value=value, footprint=footprint)
        self.connections = {"1": plus, "2": minus}


@dataclass
class L(Part):
    """Inductor.  L(ref, n1, n2, value='1m')"""
    kind          = "L"
    spice_letter  = "L"
    pin_names     = ("1", "2")
    kicad_lib_id  = "Device:L"
    kicad_pin_map = {"1": "1", "2": "2"}

    def __init__(self, ref: str, n1: str, n2: str, value: str = "1m",
                 footprint: str = ""):
        super().__init__(ref=ref, value=value, footprint=footprint)
        self.connections = {"1": n1, "2": n2}


# ──────────────────────────────────────────────────────────────────────────
# Diodes
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class D(Part):
    """Generic diode.  D(ref, a, k, model='1N4148')"""
    kind          = "D"
    spice_letter  = "D"
    pin_names     = ("a", "k")
    kicad_lib_id  = "Device:D"
    kicad_pin_map = {"k": "1", "a": "2"}  # KiCad Device:D has 1=K, 2=A

    def __init__(self, ref: str, a: str, k: str, model: str = "DEFAULT_D",
                 footprint: str = ""):
        super().__init__(ref=ref, value=model, model=model, footprint=footprint)
        self.connections = {"a": a, "k": k}


@dataclass
class LED(Part):
    """LED.  Same pins as D but maps to KiCad's LED symbol."""
    kind          = "LED"
    spice_letter  = "D"
    pin_names     = ("a", "k")
    kicad_lib_id  = "Device:LED"
    kicad_pin_map = {"k": "1", "a": "2"}

    def __init__(self, ref: str, a: str, k: str, model: str = "LED",
                 footprint: str = ""):
        super().__init__(ref=ref, value=model, model=model, footprint=footprint)
        self.connections = {"a": a, "k": k}


# ──────────────────────────────────────────────────────────────────────────
# BJTs
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class NPN(Part):
    """NPN BJT.  NPN(ref, *, c, b, e, model='2N3904')

    Maps to KiCad's Transistor_BJT:Q_NPN_EBC by default — that symbol's
    Sim.Pins is "1=E 2=B 3=C", so kicad pin 1 is the SPICE 'e' pin etc.
    """
    kind          = "NPN"
    spice_letter  = "Q"
    pin_names     = ("c", "b", "e")
    kicad_lib_id  = "Transistor_BJT:Q_NPN_EBC"
    kicad_pin_map = {"e": "1", "b": "2", "c": "3"}

    def __init__(self, ref: str, *, c: str, b: str, e: str,
                 model: str = "2N3904", footprint: str = ""):
        super().__init__(ref=ref, value=model, model=model, footprint=footprint)
        self.connections = {"c": c, "b": b, "e": e}


@dataclass
class PNP(Part):
    """PNP BJT.  PNP(ref, *, c, b, e, model='2N3906')"""
    kind          = "PNP"
    spice_letter  = "Q"
    pin_names     = ("c", "b", "e")
    kicad_lib_id  = "Transistor_BJT:Q_PNP_EBC"
    kicad_pin_map = {"e": "1", "b": "2", "c": "3"}

    def __init__(self, ref: str, *, c: str, b: str, e: str,
                 model: str = "2N3906", footprint: str = ""):
        super().__init__(ref=ref, value=model, model=model, footprint=footprint)
        self.connections = {"c": c, "b": b, "e": e}


# ──────────────────────────────────────────────────────────────────────────
# Independent sources: V, I
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class V(Part):
    """Independent voltage source.  V(ref, plus, minus, dc=5)

    For richer source types (sine, pulse, pwl) pass dc=None and set ac_spec.
    """
    kind          = "V"
    spice_letter  = "V"
    pin_names     = ("+", "-")
    kicad_lib_id  = "Simulation_SPICE:VDC"
    kicad_pin_map = {"+": "1", "-": "2"}

    dc: float | None = None
    ac:  str | None = None       # raw SPICE source spec, e.g. "SIN(0 1 1k)"

    def __init__(self, ref: str, plus: str, minus: str, dc: float | None = None,
                 ac: str | None = None, footprint: str = ""):
        if dc is None and ac is None:
            raise ValueError(f"V {ref}: supply dc=... or ac='SIN(...)'/etc.")
        value = f"{dc}V" if dc is not None else (ac or "")
        super().__init__(ref=ref, value=value, footprint=footprint)
        self.connections = {"+": plus, "-": minus}
        self.dc = dc
        self.ac = ac

    def spice_line(self) -> str:
        p, m = self.connections["+"], self.connections["-"]
        if self.dc is not None:
            return f"{self.ref} {p} {m} DC {self.dc}"
        return f"{self.ref} {p} {m} {self.ac}"


@dataclass
class I(Part):                              # noqa: E742 — yes, I is the name
    """Independent current source.  I(ref, plus, minus, dc=1m)"""
    kind          = "I"
    spice_letter  = "I"
    pin_names     = ("+", "-")
    kicad_lib_id  = "Simulation_SPICE:IDC"
    kicad_pin_map = {"+": "1", "-": "2"}

    dc: float | None = None
    ac: str | None = None

    def __init__(self, ref: str, plus: str, minus: str, dc: float | None = None,
                 ac: str | None = None, footprint: str = ""):
        if dc is None and ac is None:
            raise ValueError(f"I {ref}: supply dc=... or ac='SIN(...)'/etc.")
        value = f"{dc}A" if dc is not None else (ac or "")
        super().__init__(ref=ref, value=value, footprint=footprint)
        self.connections = {"+": plus, "-": minus}
        self.dc = dc
        self.ac = ac

    def spice_line(self) -> str:
        p, m = self.connections["+"], self.connections["-"]
        if self.dc is not None:
            return f"{self.ref} {p} {m} DC {self.dc}"
        return f"{self.ref} {p} {m} {self.ac}"


# Public registry for from_dict reconstruction + extensibility checks.
ALL_PARTS: dict[str, type[Part]] = {
    cls.kind: cls
    for cls in (R, C, L, D, LED, NPN, PNP, V, I)
}


def part_from_dict(d: dict) -> Part:
    """Reconstruct a Part from its to_dict() form (round-trip support).

    Bypasses __init__ because each subclass has its own positional signature.
    """
    cls = ALL_PARTS.get(d["kind"])
    if cls is None:
        raise ValueError(f"unknown part kind {d['kind']!r}; known: {sorted(ALL_PARTS)}")
    p = cls.__new__(cls)
    p.ref       = d["ref"]
    p.value     = d.get("value", "")
    p.model     = d.get("model", "")
    p.library   = d.get("library", "")
    p.footprint = d.get("footprint", "")
    p.connections = dict(d.get("pins", {}))
    if cls is V or cls is I:
        # Recover dc/ac from the value field
        v = p.value
        if v.endswith("V") or v.endswith("A"):
            try:
                p.dc = float(v[:-1])
                p.ac = None
            except ValueError:
                p.dc = None
                p.ac = v
        else:
            p.dc = None
            p.ac = v
    return p

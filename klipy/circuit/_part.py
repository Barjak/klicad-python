"""Part base + concrete part subclasses.

Pin names follow SPICE conventions (c/b/e for BJT, a/k for diode, +/- for
caps, d/g/s for MOSFET).  Each subclass declares:

  * spice_letter      first letter of the SPICE element line (R/C/L/D/Q/M/V/I)
  * pin_names         tuple of SPICE-semantic pin names
  * kicad_lib_id      'LibName:SymbolName' of the KliCAD library symbol that
                      hosts the same shape with matching Sim.Pins
  * kicad_pin_map     dict mapping spice_pin -> kicad_pin_number
  * connections       dict mapping spice_pin -> net_name (filled per-instance)

The kicad_pin_map exists because KliCAD pin NUMBERS aren't fixed across symbol
libraries (Q_NPN_EBC has 1=E 2=B 3=C; Q_NPN_BCE would have 1=B 2=C 3=E).
The map is anchored to the specific lib_id we pick.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import ClassVar


# ──────────────────────────────────────────────────────────────────────────
# SPICE-source-spec → KliCAD Simulation_SPICE symbol mapping
# ──────────────────────────────────────────────────────────────────────────
#
# KliCAD's Simulation_SPICE library has one symbol per source type (VDC,
# VPULSE, VSIN, VPWL, VEXP, ISIN, etc.) and KliCAD's schematic-to-SPICE
# exporter uses the symbol's Sim.Type / Sim.Params fields to emit the
# source line.  If we place a VDC symbol but its Value field contains
# 'PULSE(...)', the exporter emits a malformed source line and the
# resulting netlist either fails to parse or produces a singular matrix
# at solve time (KliCAD's current-probe wrapper turns the bad source
# into a near-short).
#
# Translation tables.  SPICE arg order vs KliCAD Sim.Params parameter
# name list:
#
#   PULSE(v1 v2 td tr tf pw  per [phase])    KliCAD: y1 y2 td tr tf tw  per [phase]
#   SIN  (vo va fr  td theta phase)          KliCAD: dc ampl f  td theta phase
#   EXP  (v1 v2 td1 tau1 td2 tau2)           KliCAD: y1 y2 td1 tau1 td2 tau2
#   PWL  (t1 v1 t2 v2 ...)                   KliCAD: pwl="t1 v1 t2 v2 ..."
#
# (KliCAD uses 'tw' for what SPICE calls 'pw'; same value.)

_SOURCE_SPEC: dict[tuple[str, str], tuple[str, list[str] | None]] = {
    ("PULSE", "V"): ("Simulation_SPICE:VPULSE",
                     ["y1", "y2", "td", "tr", "tf", "tw", "per", "phase"]),
    ("PULSE", "I"): ("Simulation_SPICE:IPULSE",
                     ["y1", "y2", "td", "tr", "tf", "tw", "per", "phase"]),
    ("SIN",   "V"): ("Simulation_SPICE:VSIN",
                     ["dc", "ampl", "f", "td", "theta", "phase"]),
    ("SIN",   "I"): ("Simulation_SPICE:ISIN",
                     ["dc", "ampl", "f", "td", "theta", "phase"]),
    ("EXP",   "V"): ("Simulation_SPICE:VEXP",
                     ["y1", "y2", "td1", "tau1", "td2", "tau2"]),
    ("EXP",   "I"): ("Simulation_SPICE:IEXP",
                     ["y1", "y2", "td1", "tau1", "td2", "tau2"]),
    ("PWL",   "V"): ("Simulation_SPICE:VPWL", None),  # special-cased
    ("PWL",   "I"): ("Simulation_SPICE:IPWL", None),
}

_SPEC_RE = re.compile(r"^\s*([A-Za-z]+)\s*\((.*)\)\s*$", re.DOTALL)


def _parse_source_spec(ac: str, device: str) -> tuple[str, str, str]:
    """Parse a SPICE source spec like 'PULSE(0 12 0 10n 10n 4u 10u)'.

    Returns (kicad_lib_id, sim_type, sim_params_string).  Raises ValueError
    if the spec can't be parsed or the source type isn't a recognized
    KliCAD symbol.  For unrecognized SPICE types, the caller is expected
    to fall back to VDC and put the raw string in Value (lossy, but the
    SPICE side still works through to_spice_deck()).
    """
    m = _SPEC_RE.match(ac)
    if not m:
        raise ValueError(f"unrecognized source spec {ac!r} (expected TYPE(args))")
    stype = m.group(1).upper()
    args_str = m.group(2).strip()
    key = (stype, device)
    if key not in _SOURCE_SPEC:
        raise ValueError(
            f"source type {stype!r} is not mapped to a KliCAD Simulation_SPICE "
            f"symbol; supported: PULSE, SIN, PWL, EXP"
        )
    lib_id, names = _SOURCE_SPEC[key]
    args = re.split(r"[\s,]+", args_str)
    if stype == "PWL":
        # Pairs of (t v); keep them as the inner string verbatim.
        sim_params = f'pwl="{" ".join(args)}"'
    else:
        assert names is not None
        if len(args) > len(names):
            raise ValueError(
                f"{stype}: got {len(args)} args, KliCAD only models {len(names)} "
                f"({names})"
            )
        sim_params = " ".join(f"{n}={v}" for n, v in zip(names, args))
    return lib_id, stype, sim_params


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

        KliCAD reference designators (R1, C2, Q3) already start with the
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
    kicad_pin_map = {"k": "1", "a": "2"}  # KliCAD Device:D has 1=K, 2=A

    def __init__(self, ref: str, a: str, k: str, model: str = "DEFAULT_D",
                 footprint: str = ""):
        super().__init__(ref=ref, value=model, model=model, footprint=footprint)
        self.connections = {"a": a, "k": k}


@dataclass
class LED(Part):
    """LED.  Same pins as D but maps to KliCAD's LED symbol."""
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

    Maps to KliCAD's Transistor_BJT:Q_NPN_EBC by default — that symbol's
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
# MOSFETs / JFETs
#
# Device:Q_{N,P}MOS uses non-numeric pin "numbers" D/G/S — the KiCad symbol
# library's convention for active devices.  The default integer pin map
# would silently bind nothing, so we ship the string-pin map by default.
# Device:Q_{N,P}JFET_GDS uses 1/2/3 with positional G/D/S meaning per the
# suffix (1=G, 2=D, 3=S).
#
# SPICE: M-element takes four nodes (d g s b).  Bulk defaults to source.
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class NMOS(Part):
    """N-channel MOSFET.  NMOS(ref, *, d, g, s, model='NMOS_S', bulk=None)

    Default model NMOS_S is a Level-1 toy NMOS in STANDARD_MODEL_LIB.
    Use a vendor `.SUBCKT` via XSubckt for quantitative accuracy.
    """
    kind          = "NMOS"
    spice_letter  = "M"
    pin_names     = ("d", "g", "s")
    kicad_lib_id  = "Device:Q_NMOS"
    kicad_pin_map = {"g": "G", "d": "D", "s": "S"}

    def __init__(self, ref: str, *, d: str, g: str, s: str,
                 model: str = "NMOS_S", bulk: str | None = None,
                 footprint: str = ""):
        super().__init__(ref=ref, value=model, model=model, footprint=footprint)
        self.connections = {"d": d, "g": g, "s": s}
        self._bulk = bulk if bulk is not None else s

    def spice_line(self) -> str:
        from ._spice import _to_spice_net
        d, g, s = (self.connections[k] for k in ("d", "g", "s"))
        return f"{self.ref} {d} {g} {s} {_to_spice_net(self._bulk)} {self.model}"


@dataclass
class PMOS(Part):
    """P-channel MOSFET.  PMOS(ref, *, d, g, s, model='PMOS_S', bulk=None)"""
    kind          = "PMOS"
    spice_letter  = "M"
    pin_names     = ("d", "g", "s")
    kicad_lib_id  = "Device:Q_PMOS"
    kicad_pin_map = {"g": "G", "d": "D", "s": "S"}

    def __init__(self, ref: str, *, d: str, g: str, s: str,
                 model: str = "PMOS_S", bulk: str | None = None,
                 footprint: str = ""):
        super().__init__(ref=ref, value=model, model=model, footprint=footprint)
        self.connections = {"d": d, "g": g, "s": s}
        self._bulk = bulk if bulk is not None else s

    def spice_line(self) -> str:
        from ._spice import _to_spice_net
        d, g, s = (self.connections[k] for k in ("d", "g", "s"))
        return f"{self.ref} {d} {g} {s} {_to_spice_net(self._bulk)} {self.model}"


@dataclass
class NJFET(Part):
    """N-channel JFET.  NJFET(ref, *, d, g, s, model=...).

    Pass an explicit model — STANDARD_MODEL_LIB does not ship a default JFET.
    """
    kind          = "NJFET"
    spice_letter  = "J"
    pin_names     = ("d", "g", "s")
    kicad_lib_id  = "Device:Q_NJFET_GDS"
    kicad_pin_map = {"g": "1", "d": "2", "s": "3"}

    def __init__(self, ref: str, *, d: str, g: str, s: str,
                 model: str, footprint: str = ""):
        super().__init__(ref=ref, value=model, model=model, footprint=footprint)
        self.connections = {"d": d, "g": g, "s": s}

    def spice_line(self) -> str:
        d, g, s = (self.connections[k] for k in ("d", "g", "s"))
        return f"{self.ref} {d} {g} {s} {self.model}"


@dataclass
class PJFET(Part):
    """P-channel JFET.  PJFET(ref, *, d, g, s, model=...)."""
    kind          = "PJFET"
    spice_letter  = "J"
    pin_names     = ("d", "g", "s")
    kicad_lib_id  = "Device:Q_PJFET_GDS"
    kicad_pin_map = {"g": "1", "d": "2", "s": "3"}

    def __init__(self, ref: str, *, d: str, g: str, s: str,
                 model: str, footprint: str = ""):
        super().__init__(ref=ref, value=model, model=model, footprint=footprint)
        self.connections = {"d": d, "g": g, "s": s}

    def spice_line(self) -> str:
        d, g, s = (self.connections[k] for k in ("d", "g", "s"))
        return f"{self.ref} {d} {g} {s} {self.model}"


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

    # Set when ac= is supplied: KliCAD-side selection for to_klicad_sch().
    # sim_type=DC + sim_params=None means "use the VDC symbol, no Sim.Params
    # needed" (KliCAD reads Value as the DC voltage).
    sim_type:   str | None = None
    sim_params: str | None = None

    def __init__(self, ref: str, plus: str, minus: str, dc: float | None = None,
                 ac: str | None = None, footprint: str = ""):
        if dc is None and ac is None:
            raise ValueError(f"V {ref}: supply dc=... or ac='SIN(...)'/etc.")
        value = f"{dc}V" if dc is not None else (ac or "")
        super().__init__(ref=ref, value=value, footprint=footprint)
        self.connections = {"+": plus, "-": minus}
        self.dc = dc
        self.ac = ac
        if ac is not None:
            lib_id, stype, params = _parse_source_spec(ac, "V")
            # Override the class-level VDC default for this instance.
            self.kicad_lib_id = lib_id
            self.sim_type = stype
            self.sim_params = params
        else:
            self.sim_type = "DC"
            self.sim_params = None

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

    sim_type:   str | None = None
    sim_params: str | None = None

    def __init__(self, ref: str, plus: str, minus: str, dc: float | None = None,
                 ac: str | None = None, footprint: str = ""):
        if dc is None and ac is None:
            raise ValueError(f"I {ref}: supply dc=... or ac='SIN(...)'/etc.")
        value = f"{dc}A" if dc is not None else (ac or "")
        super().__init__(ref=ref, value=value, footprint=footprint)
        self.connections = {"+": plus, "-": minus}
        self.dc = dc
        self.ac = ac
        if ac is not None:
            lib_id, stype, params = _parse_source_spec(ac, "I")
            self.kicad_lib_id = lib_id
            self.sim_type = stype
            self.sim_params = params
        else:
            self.sim_type = "DC"
            self.sim_params = None

    def spice_line(self) -> str:
        p, m = self.connections["+"], self.connections["-"]
        if self.dc is not None:
            return f"{self.ref} {p} {m} DC {self.dc}"
        return f"{self.ref} {p} {m} {self.ac}"


# ──────────────────────────────────────────────────────────────────────────
# SPICE subcircuit instances: X
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class XSubckt(Part):
    """SPICE subcircuit instance (an `X` element).

    Instantiates a `.SUBCKT` defined in an included .lib — the form most
    vendor TVS / Zener / op-amp / regulator models ship as.  Subcircuits
    have an arbitrary, model-specific pin count and order, so unlike the
    fixed-shape parts XSubckt takes its nodes as a positional list whose
    order must match the .SUBCKT's terminal order.

        c.add_model_lib("vendor_models/littelfuse_smaj_ca_tvs.lib")
        c.add(XSubckt("D1", ["RAIL", "SW"], subckt="SMAJ24CA"))
        # emits:  XD1 RAIL SW SMAJ24CA

    SPICE requires a subcircuit instance line to start with 'X'; an 'X'
    is prepended to the ref if it doesn't already have one (KliCAD-style
    ref 'D1' -> 'XD1', which SPICE accepts).

    NOTE: schematic placement (to_schematic) is not yet supported for
    XSubckt — it has no fixed KliCAD symbol.  SPICE-deck only for now.
    """
    kind          = "X"
    spice_letter  = "X"
    # NB: per-instance kicad_lib_id / kicad_pin_map override the class-level
    # empty defaults — XSubckts can adopt any KliCAD symbol whose pin set
    # the caller maps to the subckt's positional terminals.
    kicad_lib_id  = ""

    subckt: str = ""

    def __init__(self, ref: str, nodes: list[str], subckt: str,
                 footprint: str = "",
                 kicad_lib_id: str = "",
                 kicad_pin_map: dict[str, str] | None = None):
        """SPICE subcircuit instance.

        ref:           designator (e.g. 'Q1', 'U3'); 'X' is prepended in SPICE.
        nodes:         positional list of nets, in .SUBCKT terminal order.
        subckt:        name of the .SUBCKT defined in an included .lib.
        footprint:     optional KliCAD footprint hint.
        kicad_lib_id:  KliCAD library symbol id (e.g. 'Device:Q_NMOS_GDS').
                       If provided, to_schematic() will place this symbol;
                       otherwise schematic placement raises.
        kicad_pin_map: maps SPICE-positional pin names ('1'..'N') to KliCAD
                       symbol pin numbers (e.g. {'1':'2','2':'1','3':'3'}
                       for a DGS-order subckt going onto a GDS-labeled
                       KliCAD symbol).  Required when kicad_lib_id is set.
        """
        if not ref:
            raise ValueError("XSubckt: missing ref designator")
        if not nodes:
            raise ValueError(f"XSubckt {ref}: nodes list is empty")
        if not subckt:
            raise ValueError(f"XSubckt {ref}: subckt name is required")
        super().__init__(ref=ref, value=subckt, footprint=footprint)
        self.subckt = subckt
        # Positional pins named "1".."N"; order == .SUBCKT terminal order.
        self.pin_names = tuple(str(i + 1) for i in range(len(nodes)))
        self.connections = {name: net for name, net in zip(self.pin_names, nodes)}

        if kicad_lib_id:
            if not kicad_pin_map:
                raise ValueError(
                    f"XSubckt {ref}: kicad_lib_id={kicad_lib_id!r} requires "
                    f"kicad_pin_map (SPICE position '1'..'{len(nodes)}' → "
                    f"KliCAD pin number); pass kicad_pin_map={{'1':'1',...}}"
                )
            unmapped = [p for p in self.pin_names if p not in kicad_pin_map]
            if unmapped:
                raise ValueError(
                    f"XSubckt {ref}: kicad_pin_map missing entries for "
                    f"SPICE positions {unmapped}"
                )
            self.kicad_lib_id = kicad_lib_id
            self.kicad_pin_map = dict(kicad_pin_map)
        elif kicad_pin_map:
            raise ValueError(
                f"XSubckt {ref}: kicad_pin_map without kicad_lib_id — pass "
                f"both or neither (schematic placement requires both)"
            )

    def validate(self) -> None:
        if not self.ref:
            raise ValueError("XSubckt: missing ref designator")
        if not self.subckt:
            raise ValueError(f"XSubckt {self.ref}: missing subckt name")
        missing = [p for p in self.pin_names if not self.connections.get(p)]
        if missing:
            raise ValueError(f"XSubckt {self.ref}: pins {missing} unconnected")
        # If a KliCAD symbol was declared, every SPICE pin needs a kicad pin.
        if self.kicad_lib_id:
            unmapped = [p for p in self.pin_names
                        if p not in getattr(self, "kicad_pin_map", {})]
            if unmapped:
                raise ValueError(
                    f"XSubckt {self.ref}: kicad_pin_map missing entries "
                    f"for SPICE positions {unmapped}"
                )

    def _x_ref(self) -> str:
        """SPICE subckt-call ref — guaranteed to start with 'X'."""
        return self.ref if self.ref[:1].upper() == "X" else f"X{self.ref}"

    def spice_line(self) -> str:
        nets = " ".join(self.connections[p] for p in self.pin_names)
        return f"{self._x_ref()} {nets} {self.subckt}"

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["subckt"] = self.subckt
        if self.kicad_lib_id:
            d["kicad_lib_id"] = self.kicad_lib_id
            d["kicad_pin_map"] = dict(getattr(self, "kicad_pin_map", {}))
        return d


# ──────────────────────────────────────────────────────────────────────────
# SubcircuitInstance — a sub-Circuit instantiated in a parent.
#
# Returned by Circuit.instance(ref, **port_map).  Lives in the parent's
# c.parts list with kind="SUBCIRCUIT".  Emits as an X-line in SPICE
# (`X<ref> <expanded_nets...> <subckt_name>`) and as a SCH_SHEET on
# the parent canvas in to_schematic (Phase H2).
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class SubcircuitInstance(Part):
    """A Sub-Circuit instantiated in a parent Circuit.

    Not constructed directly — use `Circuit.instance(ref, **port_map)`
    so the bus expansion + validation runs against the definition.

    Per-instance pin_names + connections (not class-level — different
    instances can have different expansions if the definition is
    mutated between instantiations).  kicad_lib_id / kicad_pin_map are
    unused (sheet instances are placed as SCH_SHEET via add_sheet,
    not as SCH_SYMBOL via add_symbol).
    """
    kind          = "SUBCIRCUIT"
    spice_letter  = "X"
    kicad_lib_id  = ""

    # Set during __init__; per-instance, not class-level.
    pin_names: tuple[str, ...] = ()             # type: ignore[assignment]
    kicad_pin_map: dict[str, str] = field(      # type: ignore[assignment]
        default_factory=dict, init=False,
    )

    # Reference to the definition Circuit — held weakly in spirit
    # (the dataclass doesn't enforce, but downstream emitters walk
    # via this reference for recursive .SUBCKT generation).
    definition: object = None                   # actually "Circuit"
    subckt: str = ""                            # definition's name

    # Multi-channel marker.  1 (default) is a plain single instance.
    # Values > 1 indicate this instance represents `repeat_count`
    # parallel channels of the same sub-circuit; R5.3+ consumes this
    # (Circuit.instance(repeat=N), bus port width validation, downstream
    # schematic/SPICE expansion).
    repeat_count: int = 1

    def __init__(self, ref: str, definition, *,
                 port_map: dict[str, str],
                 footprint: str = "",
                 repeat_count: int = 1):
        if not ref:
            raise ValueError("SubcircuitInstance: missing ref designator")
        if definition is None or not getattr(definition, "is_subcircuit", False):
            raise ValueError(
                f"SubcircuitInstance {ref}: definition must be a Circuit "
                f"constructed with ports=..."
            )
        super().__init__(ref=ref, value=definition.name, footprint=footprint)
        self.definition = definition
        self.subckt = definition.name
        self.repeat_count = repeat_count
        # Expand port_map against the definition's port_decl.
        from ._bus import expand_port_map, expand_port_decl
        expanded = expand_port_map(definition._port_decl, port_map)
        # pin_names is the definition's expanded port list, in declaration
        # order — that's the .SUBCKT signature order and the X-line
        # argument order.
        self.pin_names = tuple(expand_port_decl(definition._port_decl))
        self.connections = {p: expanded[p] for p in self.pin_names}

    def _x_ref(self) -> str:
        return self.ref if self.ref[:1].upper() == "X" else f"X{self.ref}"

    def spice_line(self) -> str:
        # X<ref> <net1> <net2> ... <subckt_name>
        # Nets are in pin_names order = .SUBCKT signature order.
        nets = " ".join(self.connections[p] for p in self.pin_names)
        return f"{self._x_ref()} {nets} {self.subckt}"

    def validate(self) -> None:
        if not self.ref:
            raise ValueError("SubcircuitInstance: missing ref")
        if not self.subckt:
            raise ValueError(f"SubcircuitInstance {self.ref}: missing subckt name")
        missing = [p for p in self.pin_names if not self.connections.get(p)]
        if missing:
            raise ValueError(
                f"SubcircuitInstance {self.ref}: pins {missing} unconnected"
            )

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["subckt"] = self.subckt
        return d


# Public registry for from_dict reconstruction + extensibility checks.
# NOTE: SubcircuitInstance is intentionally NOT registered here — it
# requires a definition Circuit object to reconstruct, which doesn't
# round-trip through to_dict/from_dict alone.  Save the definition
# separately and rebuild via Circuit.instance().
ALL_PARTS: dict[str, type[Part]] = {
    cls.kind: cls
    for cls in (R, C, L, D, LED, NPN, PNP, NMOS, PMOS, NJFET, PJFET, V, I, XSubckt)
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
    if cls is XSubckt:
        p.subckt = d.get("subckt", d.get("value", ""))
        # pin_names follow the saved pin dict's insertion order ("1".."N").
        p.pin_names = tuple(d.get("pins", {}).keys())
        # Optional KliCAD-symbol binding survives the round-trip.
        if d.get("kicad_lib_id"):
            p.kicad_lib_id = d["kicad_lib_id"]
            p.kicad_pin_map = dict(d.get("kicad_pin_map", {}))
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

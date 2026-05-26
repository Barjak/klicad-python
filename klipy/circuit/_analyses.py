"""SPICE analyses as typed dataclasses.

Schema is a heterogeneous list of these on Circuit.analyses; v1 covers the
basic flow (tran/ac/dc/op/noise + .ic + raw .control escape hatch), v2+ adds
Meas / Sweep / MonteCarlo / Sensitivity / etc. without breaking v1.

Each analysis renders to one or more ngspice statements via .spice_lines().
Most fire from inside a .control block so ngspice runs them at deck-load
rather than silently treating them as netlist directives.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar


@dataclass
class Analysis:
    """Base; subclasses override kind + .spice_lines()."""

    name: str = ""
    kind: ClassVar[str] = ""

    def spice_lines(self) -> list[str]:
        raise NotImplementedError

    def to_dict(self) -> dict:
        from dataclasses import asdict
        d = {"kind": self.kind}
        d.update({k: v for k, v in asdict(self).items() if v not in ("", None, [])})
        return d


# ──────────────────────────────────────────────────────────────────────────
# Core analyses
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class Tran(Analysis):
    """Transient analysis: tran <step> <stop> [<start>] [uic]"""
    kind = "tran"
    step:  str = ""              # e.g. "1us" or "1m"
    stop:  str = ""              # e.g. "200ms"
    start: str = ""              # optional
    uic:   bool = False          # use initial conditions

    def spice_lines(self) -> list[str]:
        if not self.step or not self.stop:
            raise ValueError("Tran requires step= and stop=")
        parts = ["tran", self.step, self.stop]
        if self.start:
            parts.append(self.start)
        if self.uic:
            parts.append("uic")
        return [" ".join(parts)]


@dataclass
class Ac(Analysis):
    """AC analysis: ac <dec|oct|lin> <npoints> <fstart> <fstop>"""
    kind = "ac"
    sweep_type: str = "dec"       # 'dec' | 'oct' | 'lin'
    npoints:    int = 10
    fstart:     str = ""
    fstop:      str = ""

    def spice_lines(self) -> list[str]:
        if not self.fstart or not self.fstop:
            raise ValueError("Ac requires fstart= and fstop=")
        return [f"ac {self.sweep_type} {self.npoints} {self.fstart} {self.fstop}"]


@dataclass
class Dc(Analysis):
    """DC sweep: dc <src> <vstart> <vstop> <vstep>"""
    kind = "dc"
    source: str = ""
    vstart: str = ""
    vstop:  str = ""
    vstep:  str = ""

    def spice_lines(self) -> list[str]:
        if not all([self.source, self.vstart, self.vstop, self.vstep]):
            raise ValueError("Dc requires source=, vstart=, vstop=, vstep=")
        return [f"dc {self.source} {self.vstart} {self.vstop} {self.vstep}"]


@dataclass
class Op(Analysis):
    """Operating point: op"""
    kind = "op"

    def spice_lines(self) -> list[str]:
        return ["op"]


@dataclass
class Noise(Analysis):
    """Noise analysis: noise V(out [, ref]) <inputsrc> <sweep> <pts> <fstart> <fstop>"""
    kind = "noise"
    output:     str = ""          # e.g. "V(out)"
    inputsrc:   str = ""          # e.g. "V1"
    sweep_type: str = "dec"
    npoints:    int = 10
    fstart:     str = ""
    fstop:      str = ""

    def spice_lines(self) -> list[str]:
        if not all([self.output, self.inputsrc, self.fstart, self.fstop]):
            raise ValueError("Noise requires output=, inputsrc=, fstart=, fstop=")
        return [f"noise {self.output} {self.inputsrc} {self.sweep_type} "
                f"{self.npoints} {self.fstart} {self.fstop}"]


# ──────────────────────────────────────────────────────────────────────────
# Escape hatch: raw .control body
# ──────────────────────────────────────────────────────────────────────────

@dataclass
class Control(Analysis):
    """Raw ngspice .control block content.

    Use when the typed analyses don't cover what you need (.meas with
    complex find expressions, .let-driven derived vectors, FFT post-
    processing, scripted MC iteration, etc.).  Body is passed through
    verbatim — no escaping, no validation.
    """
    kind = "control"
    body: str = ""

    def spice_lines(self) -> list[str]:
        # The whole body is its own block contents; Circuit.to_spice_deck
        # handles wrapping.  We return one line per logical statement.
        return [line for line in (l.strip() for l in self.body.splitlines()) if line]


# Registry for from_dict reconstruction.
ALL_ANALYSES: dict[str, type[Analysis]] = {
    cls.kind: cls
    for cls in (Tran, Ac, Dc, Op, Noise, Control)
}


def analysis_from_dict(d: dict) -> Analysis:
    cls = ALL_ANALYSES.get(d["kind"])
    if cls is None:
        raise ValueError(f"unknown analysis kind {d['kind']!r}; known: {sorted(ALL_ANALYSES)}")
    a = cls.__new__(cls)
    for k, v in d.items():
        if k == "kind":
            continue
        setattr(a, k, v)
    # Fill in defaults for fields not in dict
    for f in cls.__dataclass_fields__.values():
        if not hasattr(a, f.name):
            if f.default is not field(default=None).default:  # noqa
                setattr(a, f.name, f.default)
    return a

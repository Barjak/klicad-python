"""SPICE `.model` cards — inline device model definitions.

A ModelCard is an inline `.model NAME KIND (param=value ...)` line.  It
lets a Circuit carry primitive device models in the Python source
instead of requiring a separate .lib file pulled in via
Circuit.add_model_lib().

Use Circuit.add_model(name, kind, **params); the deck emitter renders
the cards between the .include directives and the element lines.

A `.model` card only covers the primitive device types (D, NPN, PNP,
NJF, NMOS, RES, ...).  Models distributed as `.SUBCKT` — most vendor
TVS / Zener / op-amp / regulator models — are pulled in with
Circuit.add_model_lib() and instantiated with an XSubckt part.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ModelCard:
    """One inline SPICE `.model` card.

    name:   model name parts reference (e.g. a D's model='DZENER15').
    kind:   SPICE device type token — D, NPN, PNP, NJF, PJF, NMOS,
            PMOS, RES, CAP, IND, SW, ...
    params: ordered device parameters; values are stringified on emit.
    """
    name: str
    kind: str
    params: dict[str, str] = field(default_factory=dict)

    def spice_line(self) -> str:
        body = " ".join(f"{k}={v}" for k, v in self.params.items())
        return f".model {self.name} {self.kind} ({body})"

    def to_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind, "params": dict(self.params)}

    @classmethod
    def from_dict(cls, d: dict) -> "ModelCard":
        return cls(
            name=d["name"],
            kind=d["kind"],
            params={k: str(v) for k, v in d.get("params", {}).items()},
        )

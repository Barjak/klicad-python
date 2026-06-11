"""klipy.circuit v0.1 grammar.

A thin Python-native circuit-spec layer on top of the existing legacy
Part/Circuit infrastructure.  Provides:

    @subcircuit               # decorator: function = sheet template
    Net()                     # auto-mint or explicit-name nets
    Pin (M1.d, R.a, etc.)     # attribute access on Part instances
    >> / connect()            # series-chain operator + variadic unify
    .pulldown / .pullup / etc # pin-method std-helper shortcuts
    instance_array(...)       # call template N times -> N SCH_SHEET_INSTANCEs

Lowers to the existing legacy Part dataclass + Circuit.add() pipeline
so to_schematic() emits the same way it does for the .add()-style
spec.  This is the front-of-grammar work only; backend emit reuses
what's already shipping.

# How `>>` chains resolve

The graph is built deferred.  `pin >> R('100') >> pin2` does NOT
allocate a net at parse time; it records (pin, R.a) and (R.b, pin2)
in a connection list.  At end of subcircuit body the union-find pass
collapses connected components into one net each, names them
(LHS-variable preferred where Net()-on-RHS-of-assign was used; else
auto-minted), and writes back into each legacy Part's connections
dict.  Resolution is one pass, total: O(edges + parts).
"""
from __future__ import annotations

import contextvars
import functools
import inspect
from dataclasses import dataclass
from typing import Callable, Optional, Any

from ._circuit import Circuit as LegacyCircuit
from . import _part as _legacy


# ────────────────────────────────────────────────────────────────────
# Context: current sub-circuit being built
# ────────────────────────────────────────────────────────────────────


@dataclass
class _BuildContext:
    """One per @subcircuit invocation.  Accumulates parts + connections,
    flushes to a legacy Circuit at the end of the body."""
    name: str
    parts: list["PartV01"]
    # Connection list: each entry is a set/frozenset of pin handles +
    # Net objects that are all the same net.  Union-find collapses them.
    connections: list[set]
    # Per-instance net counter for auto-mint.
    net_counter: int = 0
    # Ports (function parameters that came in as Nets).
    port_nets: dict[str, "Net"] = None  # type: ignore

    def __post_init__(self):
        if self.port_nets is None:
            self.port_nets = {}

    def mint_net_name(self, hint: str | None = None) -> str:
        if hint:
            return hint
        self.net_counter += 1
        return f"_n{self.net_counter}"


_current: contextvars.ContextVar[Optional[_BuildContext]] = contextvars.ContextVar(
    "klipy_v01_current", default=None
)


def _ctx() -> _BuildContext:
    cur = _current.get()
    if cur is None:
        raise RuntimeError(
            "klipy.circuit.v01: Part / connect() called outside a @subcircuit body. "
            "Wrap your spec in `@subcircuit def board(...): ...` or call it from another "
            "@subcircuit."
        )
    return cur


# ────────────────────────────────────────────────────────────────────
# Net
# ────────────────────────────────────────────────────────────────────


class Net:
    """A wire / net.  May be named (`Net('GATE')`) or auto-minted
    (`Net()`).  Auto-minted nets get a stable name at sub-circuit
    close based on whichever LHS the Net() was assigned to, if any."""

    __slots__ = ("name", "_hint")

    def __init__(self, name: str | None = None):
        self.name = name      # None until resolved
        self._hint = name

    def __rshift__(self, other):
        return _chain(self, other)

    def __rrshift__(self, other):
        return _chain(other, self)

    def __repr__(self) -> str:
        return f"Net({self.name or '?'})"


# ────────────────────────────────────────────────────────────────────
# Pin proxy
# ────────────────────────────────────────────────────────────────────


class Pin:
    """One pin of a constructed PartV01.  Identified by the part it
    belongs to + the logical pin name (e.g. 'd', 'g', 's' for NMOS)."""

    __slots__ = ("part", "name")

    def __init__(self, part: "PartV01", name: str):
        self.part = part
        self.name = name

    # ---- connection operators ----

    def __rshift__(self, other):
        return _chain(self, other)

    def __rrshift__(self, other):
        return _chain(other, self)

    # ---- std-helper shortcuts ----

    def pulldown(self, value: str, to: Optional["Net"] = None) -> "Pin":
        """Connect this pin to ground (or `to=`) through a resistor."""
        if to is None:
            to = _project_gnd()
        r = R(value)
        connect(self, r.a)
        connect(r.b, to)
        return self

    def pullup(self, value: str, to: Optional["Net"] = None) -> "Pin":
        """Connect this pin to a rail (default project +rail) via R."""
        if to is None:
            to = _project_vcc()
        r = R(value)
        connect(self, r.a)
        connect(r.b, to)
        return self

    def bypass(self, value: str, to: Optional["Net"] = None) -> "Pin":
        """Decoupling cap from this pin to ground."""
        if to is None:
            to = _project_gnd()
        c = C(value)
        connect(self, c.a)
        connect(c.b, to)
        return self

    def __repr__(self) -> str:
        return f"Pin({self.part.kind}#{id(self.part):x}.{self.name})"


# ────────────────────────────────────────────────────────────────────
# Part wrappers — wrap legacy Part subclasses, expose Pin handles
# ────────────────────────────────────────────────────────────────────


class PartV01:
    """V0.1 part wrapper.  Subclasses declare:
        kind:           class var ("R", "C", "D", "NMOS", ...)
        pin_names:      tuple of logical pin names exposed as attrs
        legacy_factory: Callable[[ref, **port_nets], legacy.Part]
    """
    kind: str = ""
    pin_names: tuple[str, ...] = ()
    legacy_factory: Callable[..., _legacy.Part] | None = None
    value: str = ""

    def __init__(self, value: str | None = None, *, label: str | None = None):
        # Register with the active build context.
        if value is not None:
            self.value = value
        self.label = label  # explicit refdes override; None -> auto-assign
        self.pins: dict[str, Pin] = {n: Pin(self, n) for n in self.pin_names}
        _ctx().parts.append(self)

    def __getattr__(self, attr: str) -> Pin:
        # Only called for missing attrs.  Map logical pin names to Pin.
        pins = self.__dict__.get("pins")
        if pins is not None and attr in pins:
            return pins[attr]
        raise AttributeError(
            f"{type(self).__name__!r} has no pin or attribute {attr!r}; "
            f"valid pins: {list(pins) if pins else []}"
        )

    # Chain hooks: a Part on RHS / LHS of `>>` is treated as
    # "enter at the first pin, exit at the last."  For 2-pin parts
    # this is a/b or 1/2.  For 3-pin parts, this is a guard rail —
    # use explicit `M1.d` / `M1.g` / `M1.s` instead.
    @property
    def _flow_in(self) -> Pin:
        if len(self.pin_names) != 2:
            raise TypeError(
                f"{self.kind} has {len(self.pin_names)} pins; "
                f"use explicit pin attributes (e.g. M1.d) instead of `>>` on the part itself."
            )
        return self.pins[self.pin_names[0]]

    @property
    def _flow_out(self) -> Pin:
        if len(self.pin_names) != 2:
            raise TypeError(
                f"{self.kind} has {len(self.pin_names)} pins; "
                f"use explicit pin attributes instead of `>>` on the part itself."
            )
        return self.pins[self.pin_names[1]]

    def __rshift__(self, other):
        return _chain(self, other)

    def __rrshift__(self, other):
        return _chain(other, self)


# ─── concrete parts ────────────────────────────────────────────────


class R(PartV01):
    kind = "R"
    pin_names = ("a", "b")
    legacy_factory = staticmethod(_legacy.R)


class C(PartV01):
    kind = "C"
    pin_names = ("a", "b")
    legacy_factory = staticmethod(_legacy.C)


class L(PartV01):
    kind = "L"
    pin_names = ("a", "b")
    legacy_factory = staticmethod(_legacy.L)


class D(PartV01):
    kind = "D"
    pin_names = ("a", "k")
    legacy_factory = staticmethod(_legacy.D)


class NMOS(PartV01):
    kind = "NMOS"
    pin_names = ("d", "g", "s")
    legacy_factory = staticmethod(_legacy.NMOS)


class V(PartV01):
    kind = "V"
    pin_names = ("plus", "minus")
    legacy_factory = staticmethod(_legacy.V)


# Convenience aliases for the driver-board spec.  Real implementation
# will derive these from the KliCAD symbol library; for v0.1 we hand-
# alias the few parts the driver-board uses.
class NMOS_2N7002(NMOS):
    def __init__(self, *, label: str | None = None):
        super().__init__(label=label)
        self.value = "2N7002"


class D_1N4148(D):
    def __init__(self, *, label: str | None = None):
        super().__init__(label=label)
        self.value = "1N4148"


# ────────────────────────────────────────────────────────────────────
# `>>` chain implementation
# ────────────────────────────────────────────────────────────────────


def _to_pin_or_net(x):
    """Coerce x to either a Pin or a Net.  Pin if it's a PartV01 with
    2 pins (treated as flow_in/out)."""
    if isinstance(x, (Pin, Net)):
        return x
    if isinstance(x, PartV01):
        return x  # caller handles flow_in / flow_out direction
    raise TypeError(f"klipy.circuit.v01: cannot chain through {type(x).__name__}")


def _chain(left, right):
    """Implement `left >> right`.  Returns the rightmost endpoint so
    chains compose."""
    l = _to_pin_or_net(left)
    r = _to_pin_or_net(right)

    # The connect points: for Parts, in = flow_in, out = flow_out.
    # For Pin/Net, the value IS the endpoint.
    l_out = l._flow_out if isinstance(l, PartV01) else l
    r_in  = r._flow_in  if isinstance(r, PartV01) else r

    connect(l_out, r_in)

    # Return what the next `>>` should chain *from*.
    return r._flow_out if isinstance(r, PartV01) else r


# ────────────────────────────────────────────────────────────────────
# connect() — variadic unify
# ────────────────────────────────────────────────────────────────────


def connect(*items):
    """Unify all items into a single net.  Items can be Pin or Net.
    Variadic: every item is on the same net after this call."""
    if len(items) < 2:
        return
    members = set()
    for it in items:
        if isinstance(it, (Pin, Net)):
            members.add(it)
        else:
            raise TypeError(f"connect(): expected Pin or Net, got {type(it).__name__}")
    _ctx().connections.append(members)


# ────────────────────────────────────────────────────────────────────
# Project-level rails (placeholder — real impl reads from project module)
# ────────────────────────────────────────────────────────────────────

_PROJECT_RAILS: dict[str, Net] = {}


def _project_gnd() -> Net:
    if "GND" not in _PROJECT_RAILS:
        _PROJECT_RAILS["GND"] = Net("GND")
    return _PROJECT_RAILS["GND"]


def _project_vcc() -> Net:
    if "VCC" not in _PROJECT_RAILS:
        _PROJECT_RAILS["VCC"] = Net("VCC")
    return _PROJECT_RAILS["VCC"]


def set_rails(**rails: Net) -> None:
    """Project setup hook: declare project-level rails so `.pulldown()`
    etc. can resolve them.  Call this at the top of a spec file."""
    for name, net in rails.items():
        _PROJECT_RAILS[name] = net


# ────────────────────────────────────────────────────────────────────
# @subcircuit decorator
# ────────────────────────────────────────────────────────────────────


@dataclass
class SubcircuitDef:
    """A reusable circuit template.  Carries the user's function +
    metadata; instantiated by being called."""
    func: Callable
    name: str
    port_names: tuple[str, ...]

    def __call__(self, **port_nets: Net) -> LegacyCircuit:
        """Instantiate.  Pass net handles for every port.  Returns a
        legacy Circuit holding the lowered parts."""
        # Validate ports
        missing = set(self.port_names) - set(port_nets.keys())
        if missing:
            raise TypeError(
                f"{self.name}: missing port bindings {sorted(missing)}"
            )
        extra = set(port_nets.keys()) - set(self.port_names)
        if extra:
            raise TypeError(
                f"{self.name}: unexpected port bindings {sorted(extra)}"
            )

        # Build context, execute body, then flush to a legacy Circuit.
        ctx = _BuildContext(name=self.name, parts=[], connections=[],
                            port_nets=dict(port_nets))
        token = _current.set(ctx)
        try:
            # Call the user's function with port nets as kwargs.
            self.func(**port_nets)
        finally:
            _current.reset(token)

        # Resolve nets, build the legacy Circuit, return it.
        return _flush(ctx)

    def multi(self, ref: str, n: int, **port_map):
        """Instantiate as an N-channel sheet (one SCH_SHEET, N peer
        SCH_SHEET_INSTANCEs).  Port mappings whose RHS contains a
        ``[1..n]`` bus literal are treated as bus ports of width n;
        scalar mappings are shared across all N slots.

        Returns the SubcircuitInstance Part — add it to a Circuit::

            top.add(channel.multi('CH', 8,
                                  gate='GATE[1..8]', vload='VLOAD'))

        .. warning::

           **v0.1 stopgap — slated for replacement in v0.2.**  The
           ``'GATE[1..n]'`` string-literal syntax for indicating bus
           ports is detected by checking ``'[' in rhs`` (see body).
           This is a string-typing escape hatch standing in for a
           proper ``Bus(n)``/``Vector`` net handle.  Do NOT build new
           call sites that depend on this string syntax — when v0.2
           lands a typed bus handle, this entire ``multi()`` method
           will be replaced by passing bus handles directly, and
           string-shaped port maps will stop working.

           Cross-ref task #147 (mark stopgap), #142 (was Atopile —
           deleted; replacement is the unbuilt v0.2 bus-typing work).
        """
        from ._bus import expand_port_decl

        port_decl = []
        for pn in self.port_names:
            if pn not in port_map:
                raise TypeError(
                    f"{self.name}.multi: missing port binding for {pn!r}"
                )
            rhs = str(port_map[pn])
            port_decl.append(f"{pn.upper()}[1..{n}]" if '[' in rhs else pn.upper())

        # Build the underlying SubcircuitDef once with scalar Net handles
        # so the body executes against single-slot ports; the multi-channel
        # repeat=n expansion happens at sheet-instance placement time.
        scalar_nets = {pn: Net(pn.upper()) for pn in self.port_names}
        cir = self(**scalar_nets)
        cir.name = self.name
        cir.ports = port_decl
        cir._port_decl = list(port_decl)
        cir._ports_expanded = expand_port_decl(port_decl)
        return cir.instance(ref, repeat=n,
                            **{pn.upper(): port_map[pn] for pn in self.port_names})


def subcircuit(func: Callable) -> SubcircuitDef:
    """Decorator: turn a function into a re-instantiable sub-circuit
    template.  The function's parameter names become its port names."""
    sig = inspect.signature(func)
    port_names = tuple(p.name for p in sig.parameters.values())
    return SubcircuitDef(func=func, name=func.__name__, port_names=port_names)


# ────────────────────────────────────────────────────────────────────
# Flush: union-find + legacy Circuit population
# ────────────────────────────────────────────────────────────────────


def _flush(ctx: _BuildContext) -> LegacyCircuit:
    """Lower the build context into a legacy Circuit.
       1. Union-find pass: collapse connections into net components.
       2. Name each net (port nets keep their port name; explicit Net('X')
          keeps 'X'; anonymous nets get auto-minted '_nK').
       3. For each PartV01, look up its pins' net names and instantiate
          the corresponding legacy Part with the right port_map.
       4. Assign refdes via auto-counter (stable-hash is a follow-up).
    """
    # Union-find over Pin/Net members in ctx.connections.
    parent: dict[Any, Any] = {}

    def find(x):
        while parent.get(x, x) is not x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra is not rb:
            parent[rb] = ra

    # Seed parent map with all referenced Pin/Net objects.
    for component in ctx.connections:
        anchor = next(iter(component))
        for m in component:
            parent.setdefault(m, m)
            union(anchor, m)

    # Also ensure all ports are in the parent map even if unwired.
    for net in ctx.port_nets.values():
        parent.setdefault(net, net)

    # Group by root.
    components: dict[Any, set] = {}
    for member in parent:
        root = find(member)
        components.setdefault(root, set()).add(member)

    # Name each component.  Priority: any port Net in the component
    # gives the component its port name; otherwise explicit Net('X')
    # name; otherwise auto-mint.
    net_name: dict[Any, str] = {}
    port_net_set = set(ctx.port_nets.values())
    used_names: set[str] = set()

    for root, members in components.items():
        # Pick a name.
        chosen = None
        for m in members:
            if isinstance(m, Net) and m in port_net_set:
                # Find which port name this Net was bound to.
                for port_name, port_net in ctx.port_nets.items():
                    if port_net is m:
                        chosen = port_net._hint or port_name
                        break
                break
        if chosen is None:
            for m in members:
                if isinstance(m, Net) and m._hint:
                    chosen = m._hint
                    break
        if chosen is None:
            chosen = ctx.mint_net_name()
        # Dedupe if name collision.
        base = chosen
        i = 1
        while chosen in used_names:
            chosen = f"{base}_{i}"
            i += 1
        used_names.add(chosen)
        net_name[root] = chosen

    # For every Pin, compute which net it belongs to.
    pin_to_net: dict[Pin, str] = {}
    for member in parent:
        if isinstance(member, Pin):
            pin_to_net[member] = net_name[find(member)]

    # Refdes assignment — for v0.1 use a simple per-kind counter.
    # Stability-via-hash is a follow-up (task #141 item 6).
    refdes_counter: dict[str, int] = {}

    cir = LegacyCircuit(ctx.name)

    for p in ctx.parts:
        # Build the port_map for the legacy ctor.  Each PartV01
        # subclass declares pin_names; legacy ctor expects them as
        # positional or kwarg with the SAME logical names (R: n1, n2;
        # NMOS: d, g, s; D: a, k; V: plus, minus).
        port_map = {}
        for pn in p.pin_names:
            pin = p.pins[pn]
            if pin not in pin_to_net:
                # Unwired pin — leave it dangling; legacy ctor will
                # complain via .validate().
                continue
            port_map[pn] = pin_to_net[pin]

        # Refdes.
        if p.label:
            ref = p.label
        else:
            n = refdes_counter.get(p.kind, 0) + 1
            refdes_counter[p.kind] = n
            ref = f"{p.kind}{n}"

        # Call the legacy factory.  Signatures differ per kind; do
        # the dispatch explicitly.
        try:
            if p.kind == "R":
                lp = p.legacy_factory(ref, port_map.get("a", ""),
                                       port_map.get("b", ""), value=p.value or "1k")
            elif p.kind == "C":
                lp = p.legacy_factory(ref, port_map.get("a", ""),
                                       port_map.get("b", ""), value=p.value or "1u")
            elif p.kind == "L":
                lp = p.legacy_factory(ref, port_map.get("a", ""),
                                       port_map.get("b", ""), value=p.value or "1m")
            elif p.kind == "D":
                lp = p.legacy_factory(ref, a=port_map.get("a", ""),
                                       k=port_map.get("k", ""), model=p.value or "DEFAULT_D")
            elif p.kind == "NMOS":
                lp = p.legacy_factory(ref, d=port_map.get("d", ""),
                                       g=port_map.get("g", ""),
                                       s=port_map.get("s", ""),
                                       model=p.value or "NMOS_S")
            elif p.kind == "V":
                lp = p.legacy_factory(ref, plus=port_map.get("plus", ""),
                                       minus=port_map.get("minus", ""))
            else:
                raise NotImplementedError(
                    f"v0.1 backend doesn't yet handle kind={p.kind!r}"
                )
        except Exception as e:
            raise RuntimeError(
                f"klipy.circuit.v01 flush: failed to instantiate {p.kind}({ref}) "
                f"with port_map={port_map}: {e}"
            ) from e

        cir.add(lp)

    return cir


# ────────────────────────────────────────────────────────────────────
# instance_array — lower template + N port-bindings to N peer instances
# ────────────────────────────────────────────────────────────────────


def instance_array(
    template: SubcircuitDef,
    n: int,
    bind: Callable[[int], dict[str, Net]],
) -> list[LegacyCircuit]:
    """Instantiate `template` n times.  `bind(i)` returns the port
    bindings for instance i.  Each call produces one peer instance;
    backend lowering to SCH_SHEET_INSTANCE is handled by to_schematic."""
    return [template(**bind(i)) for i in range(n)]


# ────────────────────────────────────────────────────────────────────
# Public surface
# ────────────────────────────────────────────────────────────────────

__all__ = [
    "subcircuit",
    "root",
    "Bundle",
    "Power",
    "Net",
    "Pin",
    "connect",
    "instance_array",
    "set_rails",
    # parts
    "R", "C", "L", "D", "NMOS", "V",
    "NMOS_2N7002", "D_1N4148",
]


# ────────────────────────────────────────────────────────────────────
# @root — pure-declaration entry point.  Removes main()/IPC/paths from
# the spec file; the decorator does the work the user's main() used to.
# ────────────────────────────────────────────────────────────────────


def root(func: Callable) -> Callable:
    """Mark the function as the spec entry point.

    Side-effect-on-decoration semantics: when the module is exec'd
    *as `__main__`* (by the spec pane subprocess, by `python3 spec.py`,
    by Jupyter's top-level), this decorator:

      1. Discovers the project's `.kicad_pro` / `.kicad_sch` from
         env vars set by the spec pane (KLICAD_SCH_PATH) or by
         scanning sibling directories.
      2. Builds a fresh circuit by calling `func()`.
      3. Connects to KliCAD via the default IPC socket (or
         KLICAD_API_SOCKET) and emits via `to_schematic(mode='replace')`.

    When the module is `import`ed (not run), the decorator is a no-op
    so the spec is reusable as a library / fixture.

    The user's spec file becomes pure: imports + @subcircuit + @root
    + circuit body.  Everything else lives here.
    """
    import os
    import inspect as _inspect
    import sys as _sys
    from pathlib import Path as _Path

    # No-op when imported as a library.  Fire only when the spec
    # module is __main__ (spec pane subprocess invocation, direct
    # `python3 spec.py`, Jupyter top-level cell).
    caller_module = _inspect.getmodule(_inspect.stack()[1].frame)
    is_main = caller_module is None or caller_module.__name__ == '__main__' \
              or os.environ.get('KLIPY_FORCE_ROOT') == '1'
    if not is_main:
        return func

    # Discover the schematic path.
    sch_env = os.environ.get('KLICAD_SCH_PATH')
    if sch_env:
        sch_path = _Path(sch_env)
    else:
        spec_path = _Path(_inspect.getfile(func)).resolve()
        spec_dir = spec_path.parent
        candidates = list(spec_dir.glob('*.kicad_sch')) + \
                     list((spec_dir / 'kicad').glob('*.kicad_sch'))
        if not candidates:
            raise RuntimeError(
                f"@root: no .kicad_sch found near {spec_path}; "
                f"set KLICAD_SCH_PATH or place a project sibling to the spec."
            )
        sch_path = candidates[0]

    # Build the circuit by running the function in a build context.
    ctx = _BuildContext(name=func.__name__, parts=[], connections=[],
                        port_nets={})
    token = _current.set(ctx)
    try:
        result = func()
    finally:
        _current.reset(token)

    # If the function returned a legacy Circuit (built via top-level
    # `Circuit(...)` calls), use that.  Otherwise lower the ctx.
    if isinstance(result, LegacyCircuit):
        cir = result
    elif ctx.parts:
        cir = _flush(ctx)
        cir.name = func.__name__
    else:
        raise RuntimeError(
            f"@root: {func.__name__}() didn't build any parts or return a Circuit."
        )

    # Emit.
    from klipy import KliCAD
    k = KliCAD(timeout_ms=300_000)
    if not k.is_alive():
        raise RuntimeError("@root: KliCAD IPC not reachable")

    # Pop the current sheet stack back to the root before emit.  KliCAD's
    # in-memory frame may have been left pushed into a child sheet by a
    # previous interactive session; to_schematic needs to start at root.
    k.run_python(
        'import klicad_native_hierarchy as h\n'
        'while True:\n'
        '    sheets = h.list_sheets()\n'
        '    if not sheets or sheets[0].get("depth", 0) == 0:\n'
        '        break\n'
        '    h.pop_sheet()\n'
    )

    cir.compose(sch_path, kicad=k, mode='replace')

    return func


# ────────────────────────────────────────────────────────────────────
# Bundles — multi-net types that wire-by-bundle, with bridge inference
# ────────────────────────────────────────────────────────────────────


class Bundle:
    """Base class for multi-net interface types.

    Subclasses declare fields via class annotations:

        class Power(Bundle):
            vcc: Net
            gnd: Net

        class USB(Bundle):
            dp: Net
            dn: Net
            vbus: Net
            gnd: Net

    A bundle holds one Net per declared field.  Wiring `a >> b` where
    both are the same Bundle type unifies field-by-field.  Bridge
    inference (`bundle >> part >> bundle`) finds the matching bundle
    field on `part` whose type equals the LHS bundle's type, wires it,
    and returns the matching *output* bundle on `part` (the one named
    with `_out` suffix, or the other declared bundle of the same type).
    """

    # Subclasses populate this in __init_subclass__ with declared
    # field names → field types.
    _fields: dict[str, type] = {}

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Collect class annotations into _fields.
        cls._fields = {}
        for klass in reversed(cls.__mro__):
            anns = getattr(klass, '__annotations__', {})
            for name, typ in anns.items():
                if name.startswith('_'):
                    continue
                cls._fields[name] = typ

    def __init__(self, **field_values):
        # Each declared field becomes a Net on this instance.  If the
        # caller provides a Net, use it; otherwise mint a fresh one.
        for fname, ftype in self._fields.items():
            if fname in field_values:
                val = field_values[fname]
                if not isinstance(val, Net):
                    raise TypeError(
                        f"{type(self).__name__}({fname}=…): expected Net, "
                        f"got {type(val).__name__}"
                    )
                setattr(self, fname, val)
            else:
                setattr(self, fname, Net())

    def __rshift__(self, other):
        return _bundle_chain(self, other)

    def __rrshift__(self, other):
        return _bundle_chain(other, self)


class Power(Bundle):
    """Power rail: vcc + gnd."""
    vcc: Net
    gnd: Net


def _bundle_chain(left, right):
    """Implement `>>` for Bundle operands.

    Cases:
      bundle  >> bundle  (same type) → unify field-by-field
      bundle  >> part                → bridge inference: find the part's
                                       bundle field of the LHS's type and
                                       declared as an input; connect; return
                                       the matching output bundle.
      bundle  >> (Pin | Net)         → not currently supported; raise
    """
    if isinstance(left, Bundle) and isinstance(right, Bundle):
        if type(left) is not type(right):
            raise TypeError(
                f"can't bridge {type(left).__name__} -> {type(right).__name__}; "
                f"bundle types must match for direct connect."
            )
        for fname in left._fields:
            connect(getattr(left, fname), getattr(right, fname))
        return right

    if isinstance(left, Bundle) and isinstance(right, PartV01):
        # Bridge inference: find an "input"-side bundle on `right` of
        # matching type; wire LHS into it; return the matching output
        # bundle.  Convention: any bundle attribute whose name ends in
        # `_in` is input; `_out` is output; bare bundle name is bare.
        bundle_type = type(left)
        in_field = None
        out_field = None
        for attr in dir(right):
            try:
                val = getattr(right, attr)
            except AttributeError:
                continue
            if isinstance(val, Bundle) and type(val) is bundle_type:
                if attr.endswith('_in'):
                    in_field = (attr, val)
                elif attr.endswith('_out'):
                    out_field = (attr, val)
        if in_field is None:
            raise TypeError(
                f"bridge `bundle >> {type(right).__name__}` failed: no "
                f"{bundle_type.__name__} _in field on {type(right).__name__}"
            )
        # Connect LHS bundle into the part's in-bundle.
        for fname in bundle_type._fields:
            connect(getattr(left, fname), getattr(in_field[1], fname))
        # Return the matching out-bundle if present, else just the part
        # for additional chaining.
        return out_field[1] if out_field else right

    raise TypeError(
        f"can't chain {type(left).__name__} >> {type(right).__name__}"
    )

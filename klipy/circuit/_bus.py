"""Bus-syntax parsing for the canonical circuit DSL.

KiCad-native bus syntax — `FOO[3]` for a member, `FOO[0..7]` for a
range.  We carry the same syntax through the DSL so net names round-
trip cleanly into KiCad labels (KliCAD's net resolver recognizes the
syntax verbatim).

Pure functions; no IPC; safe to import anywhere.

Semantics:
  - Bus ranges expand to ascending members regardless of how the user
    wrote them.  `FOO[7..0]` and `FOO[0..7]` both expand to
    `[FOO[0], FOO[1], ..., FOO[7]]`.  The user's literal form is
    preserved for KiCad label emission (so the schematic reads
    `DATA[7..0]` if they wrote that), but the SPICE-side port order
    and the index-by-index port-map binding are always ascending.
  - Power-named nets (GND, VCC, +12V, etc.) are scalar globals — bus
    syntax on them is rejected.
"""
from __future__ import annotations

import re
from typing import Mapping, Sequence


# `name` is `\w+` — alphanumerics + underscore.  Excludes the `[`
# character so the boundary is unambiguous.
_BUS_MEMBER_RE = re.compile(r"^(\w+)\[(\d+)\]$")
_BUS_RANGE_RE  = re.compile(r"^(\w+)\[(\d+)\.\.(\d+)\]$")

# Implicit-power regex — anything matching these is a global power
# net, never a bus member or a port.  Mirrors _power_positions() in
# _klicad_sch.py.
_POWER_RE = re.compile(
    r"^("
    r"GND|VCC|VDD|VSS|VBAT|VBUS"
    r"|[+-]\d.*"
    r"|AGND|DGND|EARTH"
    r")$"
)


def is_power_name(name: str) -> bool:
    return bool(_POWER_RE.match(name))


def is_bus_member(name: str) -> bool:
    return bool(_BUS_MEMBER_RE.match(name))


def is_bus_range(name: str) -> bool:
    return bool(_BUS_RANGE_RE.match(name))


def is_bus_ref(name: str) -> bool:
    return is_bus_member(name) or is_bus_range(name)


def parse_bus_member(name: str) -> tuple[str, int] | None:
    m = _BUS_MEMBER_RE.match(name)
    return (m.group(1), int(m.group(2))) if m else None


def parse_bus_range(name: str) -> tuple[str, int, int] | None:
    """Return (base, low, high) with low <= high regardless of declaration order."""
    m = _BUS_RANGE_RE.match(name)
    if not m:
        return None
    a, b = int(m.group(2)), int(m.group(3))
    return (m.group(1), min(a, b), max(a, b))


def expand_bus_range(name: str) -> list[str]:
    """`DATA[0..7]` or `DATA[7..0]` → `['DATA[0]', 'DATA[1]', ..., 'DATA[7]']`."""
    parsed = parse_bus_range(name)
    if parsed is None:
        raise ValueError(f"not a bus range: {name!r}")
    base, low, high = parsed
    return [f"{base}[{i}]" for i in range(low, high + 1)]


def expand_one(name: str) -> list[str]:
    """Expand any port/net name to its scalar member list.

    Scalars and individual members return [self]; bus ranges expand.
    """
    if is_bus_range(name):
        return expand_bus_range(name)
    return [name]


# ──────────────────────────────────────────────────────────────────────────
# Port-list and port-map expansion
# ──────────────────────────────────────────────────────────────────────────

def validate_port_decl(ports: Sequence[str]) -> None:
    """Raise on power-named ports, duplicate (post-expansion) port names,
    or malformed bus syntax.  Pure validation; returns nothing."""
    seen: set[str] = set()
    bases_seen: set[str] = set()
    for p in ports:
        if not isinstance(p, str) or not p:
            raise ValueError(f"port names must be non-empty strings; got {p!r}")

        # Bus range: validate via parse, then check base hasn't been used.
        if is_bus_range(p):
            base, _, _ = parse_bus_range(p)  # type: ignore[misc]
            if is_power_name(base):
                raise ValueError(
                    f"port {p!r}: power-named buses are not allowed "
                    f"(power nets are implicit globals)"
                )
            if base in bases_seen:
                raise ValueError(f"port {p!r}: base name {base!r} used twice")
            bases_seen.add(base)
            members = expand_bus_range(p)
            for m in members:
                if m in seen:
                    raise ValueError(f"port {p!r}: member {m!r} duplicate")
                seen.add(m)
        elif is_bus_member(p):
            # Allowing isolated members (e.g., port=["DATA[3]"]) is legal
            # but unusual.  No special validation beyond uniqueness.
            base, _ = parse_bus_member(p)  # type: ignore[misc]
            if is_power_name(base):
                raise ValueError(
                    f"port {p!r}: power-named buses are not allowed"
                )
            if p in seen:
                raise ValueError(f"port {p!r}: duplicate")
            seen.add(p)
        else:
            if is_power_name(p):
                raise ValueError(
                    f"port {p!r}: power-named ports are not allowed "
                    f"(power nets flow through the hierarchy implicitly)"
                )
            if p in seen:
                raise ValueError(f"port {p!r}: duplicate")
            seen.add(p)


def expand_port_decl(ports: Sequence[str]) -> list[str]:
    """Expand a port-decl list to its scalar form.

    Calls validate_port_decl first; emits scalar entries in
    declaration order, with bus ranges expanded ascending in place.
    """
    validate_port_decl(ports)
    out: list[str] = []
    for p in ports:
        out.extend(expand_one(p))
    return out


def expand_port_map(
    port_decl: Sequence[str],
    user_map: Mapping[str, str],
) -> dict[str, str]:
    """Resolve a user port_map against a port_decl, returning a scalar map.

    The user may key the map by:
      - exact scalar port name (e.g., 'IN' when port_decl has 'IN')
      - base name of a bus port (e.g., 'DATA' when port_decl has 'DATA[7..0]')
      - explicit member of a bus port (e.g., 'DATA[3]' — requires every
        member of that bus to be bound explicitly)

    User-map values may be:
      - a scalar net name, bound to a scalar port
      - a bus range, bound to a bus port of matching width (index-by-
        index, ascending on both sides)
      - a single member of a bus, bound to one explicit bus-port member

    Width mismatch, missing ports, extra keys → ValueError.
    """
    validate_port_decl(port_decl)
    expanded_decl = expand_port_decl(port_decl)
    expanded_set = set(expanded_decl)

    # Build base→members map for bus ports
    bus_members: dict[str, list[str]] = {}
    for p in port_decl:
        if is_bus_range(p):
            base, _, _ = parse_bus_range(p)  # type: ignore[misc]
            bus_members[base] = expand_bus_range(p)

    out: dict[str, str] = {}
    bound: set[str] = set()

    for user_key, ext_net in user_map.items():
        if not isinstance(ext_net, str) or not ext_net:
            raise ValueError(
                f"port_map[{user_key!r}]: net must be a non-empty string; "
                f"got {ext_net!r}"
            )

        # Case 1: exact scalar port match.
        if user_key in expanded_set and user_key not in bus_members:
            # Could be a scalar port or an explicit bus-member port.
            if is_bus_range(ext_net):
                raise ValueError(
                    f"port {user_key!r} is scalar but bound to bus range "
                    f"{ext_net!r} — use explicit member like "
                    f"{user_key}={ext_net.split('[')[0]}[0]"
                )
            if user_key in bound:
                raise ValueError(f"port_map: duplicate binding for {user_key!r}")
            out[user_key] = ext_net
            bound.add(user_key)
            continue

        # Case 2: base-name reference to a bus port.
        if user_key in bus_members:
            members = bus_members[user_key]
            net_members = expand_one(ext_net)
            if len(net_members) != len(members):
                raise ValueError(
                    f"port {user_key!r}: bus width {len(members)} doesn't "
                    f"match net {ext_net!r} width {len(net_members)}"
                )
            for pm, nm in zip(members, net_members):
                if pm in bound:
                    raise ValueError(
                        f"port_map: duplicate binding for {pm!r}"
                    )
                out[pm] = nm
                bound.add(pm)
            continue

        # Case 3: full-range reference (e.g., 'DATA[7..0]') to a bus port.
        if is_bus_range(user_key):
            base, _, _ = parse_bus_range(user_key)  # type: ignore[misc]
            if base in bus_members:
                # Same handling as base-name reference, but verify width.
                members = bus_members[base]
                user_members = expand_bus_range(user_key)
                if len(user_members) != len(members):
                    raise ValueError(
                        f"port_map: range {user_key!r} width "
                        f"{len(user_members)} doesn't match port "
                        f"{base!r} width {len(members)}"
                    )
                net_members = expand_one(ext_net)
                if len(net_members) != len(members):
                    raise ValueError(
                        f"port {base!r}: bus width {len(members)} doesn't "
                        f"match net {ext_net!r} width {len(net_members)}"
                    )
                for pm, nm in zip(members, net_members):
                    if pm in bound:
                        raise ValueError(
                            f"port_map: duplicate binding for {pm!r}"
                        )
                    out[pm] = nm
                    bound.add(pm)
                continue

        raise ValueError(
            f"port_map key {user_key!r} doesn't match any declared port "
            f"(declared: {list(port_decl)})"
        )

    missing = set(expanded_decl) - bound
    if missing:
        raise ValueError(
            f"port_map missing bindings for ports: {sorted(missing)}"
        )

    return out


# ──────────────────────────────────────────────────────────────────────────
# SPICE-name validation (deck-emit boundary check)
# ──────────────────────────────────────────────────────────────────────────

# Characters that have meaning to the SPICE parser inside a name.
_SPICE_FORBIDDEN_CHARS = set(" \t\n=()[]{};,'\"")

def validate_spice_name(name: str, *, kind: str) -> None:
    """Raise if name contains SPICE-meaningful characters.

    Called at deck-emit time, NOT during DSL construction — SPICE rules
    are a SPICE-side constraint, and the schematic side may legitimately
    use names this would reject (e.g., bus members `DATA[3]` contain `[`
    and `]`).  Bus syntax is allowed via the carve-out below.

    kind ∈ {'ref', 'net', 'model'} for error-message context.

    Ref designators additionally must start with a letter (SPICE parses
    the first character as the element-type letter — `1R` would be read
    as element type "1", which doesn't exist).  Model names like
    `2N3904` are valid in SPICE and pass.
    """
    if not name:
        raise ValueError(f"empty {kind} name")
    # Carve-out: bus members are emitted to SPICE as e.g. DATA_3 or DATA[3]
    # depending on convention.  We accept the bracketed form here; the
    # deck emitter is responsible for any further translation.
    if is_bus_ref(name):
        # Strip the bracket portion and validate the base.
        if is_bus_range(name):
            base = parse_bus_range(name)[0]  # type: ignore[index]
        else:
            base = parse_bus_member(name)[0]  # type: ignore[index]
        return validate_spice_name(base, kind=kind)
    bad = _SPICE_FORBIDDEN_CHARS.intersection(name)
    if bad:
        raise ValueError(
            f"{kind} name {name!r} contains SPICE-meaningful characters: "
            f"{sorted(bad)}"
        )
    if kind == "ref" and not name[0].isalpha():
        raise ValueError(
            f"ref designator {name!r} must start with a letter (SPICE "
            f"reads the first character as the element-type letter)"
        )

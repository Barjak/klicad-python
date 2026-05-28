"""A.11: end-to-end verification that a multi-channel (repeat=N) Circuit
round-trips through ``to_schematic`` + ``kicad-cli sch export netlist``
without crashing and lands the expected on-disk shape.

Two distinct outcomes are pinned:

1. **Hard regression guard** (always asserted):
   - The netlist emit succeeds (no kicad-cli segfault, the historical
     symptom of the pre-66b0239 stale-sheetList UAF and the original
     fan-out crash chain).
   - The schematic carries one bus-shaped sheet pin per declared bus
     port on the parent side (R5.6).
   - The body sheet carries N bare bit-member hier-labels per bus
     port (R5.7), one per slot index, with no bracketed forms.

2. **Soft gap marker** (xfail until the C++ netlist exporter consumes
   R3.3's per-slot bit binding):
   - Per-slot bus-bit join: each slot K's body subgraph ends up on
     parent net ``GBUS[K]`` rather than on a slot-local
     ``/U_CH:K/GATEK`` net.  R3.3 wires up the connection_graph
     reverse-direction binding (`bbea8ea1b5`, `9275ff4cd6`) but the
     `NETLIST_EXPORTER_XML::makeListOfNets` path still keys nets on
     `GetNetMap()` entries that aren't merged after fan-out, so the
     netlist text still shows slot-local nets for slots 1..N-1.
     Tracked as a follow-up audit item.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from klipy.circuit import Circuit, R


_BUILD_CLI = Path.home() / "projects" / "KliCAD" / "build" / "kicad" / "kicad-cli"


def _kicad_cli() -> str | None:
    cli = shutil.which("kicad-cli")
    if cli:
        return cli
    if _BUILD_CLI.exists():
        return str(_BUILD_CLI)
    return None


def _build_multi_channel_circuit(repeat: int = 4) -> Circuit:
    """One bus-port child sheet, one body resistor wired to bit 0."""
    ch = Circuit("ch", ports=[f"GATE[0..{repeat - 1}]",
                              f"OUT[0..{repeat - 1}]"])
    ch.add(R("R1", "GATE[0]", "OUT[0]", value="10k"))
    top = Circuit("top_mc")
    top.add(ch.instance("U_CH",
                        repeat=repeat,
                        GATE=f"GBUS[0..{repeat - 1}]",
                        OUT=f"OBUS[0..{repeat - 1}]"))
    return top


@pytest.fixture
def multi_channel_emit(tmp_path: Path):
    """Emit a repeat=4 schematic and run kicad-cli netlist.

    Returns the netlist text + the parent/child .kicad_sch paths.
    Skips the whole test if kicad-cli or a running KliCAD aren't
    available (the underlying ``to_schematic`` step needs both).
    """
    cli = _kicad_cli()
    if cli is None:
        pytest.skip("kicad-cli not available")

    from klipy.errors import ConnectionError as KipyConnectionError

    top = _build_multi_channel_circuit(repeat=4)
    sch_path = tmp_path / "top.kicad_sch"
    try:
        top.to_schematic(str(sch_path))
    except KipyConnectionError as exc:
        pytest.skip(f"KliCAD IPC unreachable: {exc}")
    except RuntimeError as exc:
        if "KliCAD" in str(exc) or "IPC" in str(exc):
            pytest.skip(f"KliCAD IPC unreachable: {exc}")
        raise

    net_path = tmp_path / "top.net"
    proc = subprocess.run(
        [cli, "sch", "export", "netlist",
         "--format", "kicadsexpr",
         "-o", str(net_path), str(sch_path)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, (
        f"kicad-cli failed (rc={proc.returncode}):\n"
        f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
    )
    assert net_path.exists(), "netlist file not written"
    return {
        "tmp_path":   tmp_path,
        "sch_text":   sch_path.read_text(),
        "child_text": (tmp_path / "ch.kicad_sch").read_text(),
        "net_text":   net_path.read_text(),
    }


def test_multi_channel_netlist_does_not_crash(multi_channel_emit):
    """Hard guard: ``kicad-cli sch export netlist`` on a repeat=4
    schematic completes with rc=0 and a non-empty .net file."""
    net = multi_channel_emit["net_text"]
    assert net, "netlist output is empty"
    assert "(export" in net
    # All four annotated resistor instances must appear.
    refs = sorted(set(re.findall(r'\(comp\s+\(ref\s+"(R\d+)"', net)))
    assert refs == ["R1", "R2", "R3", "R4"], (
        f"expected R1..R4 across the four slots, got: {refs}"
    )


def test_parent_sheet_pin_is_bus_shaped(multi_channel_emit):
    """R5.6: one bus-shaped sheet pin per declared bus port."""
    sch = multi_channel_emit["sch_text"]
    # Parent has bus pins GATE[0..3] + OUT[0..3], no scalar GATE[K] pins.
    assert '(pin "GATE[0..3]"' in sch, (
        "expected bus-shaped parent sheet pin 'GATE[0..3]'\n" + sch[:1200]
    )
    assert '(pin "OUT[0..3]"' in sch
    # Negative: no per-bit scalar sheet pins.
    for k in range(4):
        assert f'(pin "GATE[{k}]"' not in sch, (
            f"unexpected scalar sheet pin GATE[{k}] in parent schematic"
        )
        assert f'(pin "OUT[{k}]"' not in sch


def test_body_emits_bare_bit_hier_labels(multi_channel_emit):
    """R5.7: body has N bare bit-member hier-labels per bus port (GATE0,
    GATE1, ...) so R3.3's repeatBusPinBitName can match by exact name.
    The body must NOT carry bracketed forms like GATE[0]."""
    child = multi_channel_emit["child_text"]
    for k in range(4):
        assert f'(hierarchical_label "GATE{k}"' in child, (
            f"expected bare body hier-label GATE{k}"
        )
        assert f'(hierarchical_label "OUT{k}"' in child
    # Negative: no bracketed hier-label forms.
    for k in range(4):
        assert f'(hierarchical_label "GATE[{k}]"' not in child
        assert f'(hierarchical_label "OUT[{k}]"' not in child


def test_parent_drives_bus_pins_with_bus_label(multi_channel_emit):
    """The parent label that drives the bus sheet pin must itself be
    bus-shaped (GBUS[0..3]) — anything else would prevent KliCAD's
    connection_graph from seeing the bus on the parent side."""
    sch = multi_channel_emit["sch_text"]
    assert '(label "GBUS[0..3]"' in sch
    assert '(label "OBUS[0..3]"' in sch


@pytest.mark.xfail(
    reason=("R3.3 connection_graph fan-out + NETLIST_EXPORTER_XML "
            "GetNetMap consolidation is not yet end-to-end for "
            "synthetic-clone slot bodies — slots 1..N-1 still resolve "
            "to slot-local nets in the netlist output.  Tracked "
            "separately; A.11 records the gap."),
    strict=True,
)
def test_per_slot_bit_bus_join(multi_channel_emit):
    """Each slot K's body GATE0/OUT0 net should merge with parent's
    bit K (so all four resistors land on the four bits of GBUS / OBUS).
    Currently only slot 0 merges; slots 1..3 stay slot-local."""
    net = multi_channel_emit["net_text"]
    # Per-slot resistor pin -> expected parent bit net membership.
    expectations = {
        "R1": ("GBUS[0]", "OBUS[0]"),  # slot 0
        "R2": ("GBUS[1]", "OBUS[1]"),  # slot 1
        "R3": ("GBUS[2]", "OBUS[2]"),  # slot 2
        "R4": ("GBUS[3]", "OBUS[3]"),  # slot 3
    }
    for ref, (expected_g, expected_o) in expectations.items():
        # Locate the (comp ref=ref) → pin → net by walking the net
        # blocks for nodes referring to this resistor.
        gate_net = _find_net_for_node(net, ref, pin="1")
        out_net  = _find_net_for_node(net, ref, pin="2")
        assert gate_net and expected_g in gate_net, (
            f"{ref}.1 expected on net mentioning {expected_g}, got {gate_net!r}"
        )
        assert out_net and expected_o in out_net, (
            f"{ref}.2 expected on net mentioning {expected_o}, got {out_net!r}"
        )


# ------- helpers ----------------------------------------------------------


def _find_net_for_node(netlist_text: str, ref: str, pin: str) -> str | None:
    """Walk the (nets ...) section and return the (name ...) of the
    first (net) block containing a (node (ref REF) (pin PIN) ...) line."""
    # Lightweight forward scan; the kicad-cli netlist is well-formed
    # enough that ``(net`` blocks are flat one-deep.
    in_nets = False
    cur_name: str | None = None
    cur_nodes: list[tuple[str, str]] = []
    pending: list[tuple[str, list[tuple[str, str]]]] = []
    for line in netlist_text.splitlines():
        s = line.strip()
        if s == "(nets":
            in_nets = True
            continue
        if not in_nets:
            continue
        m = re.match(r'\(name "([^"]+)"\)', s)
        if m and cur_name is None:
            cur_name = m.group(1)
            continue
        if s.startswith("(net"):
            if cur_name is not None:
                pending.append((cur_name, cur_nodes))
            cur_name = None
            cur_nodes = []
            continue
        m = re.match(r'\(ref "([^"]+)"\)', s)
        if m:
            cur_nodes.append((m.group(1), ""))
            continue
        m = re.match(r'\(pin "([^"]+)"\)', s)
        if m and cur_nodes:
            r_ref, _ = cur_nodes[-1]
            cur_nodes[-1] = (r_ref, m.group(1))
            continue
    if cur_name is not None:
        pending.append((cur_name, cur_nodes))
    for name, nodes in pending:
        for r, p in nodes:
            if r == ref and p == pin:
                return name
    return None

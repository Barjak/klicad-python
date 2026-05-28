"""A.11 (post-Candidate-A): end-to-end verification that a multi-channel
(repeat=N) Circuit round-trips through ``to_schematic`` + ``kicad-cli sch
export netlist`` without crashing and lands the **vectorized** on-disk
shape.

After the R5.7-collapse, the body sheet declares ONE component (one R
wired to scalar nets ``IN``/``OUT``) and ONE scalar hier-label per
declared port (``GATE``, ``OUT``).  The parent still emits a bus-shaped
sheet pin per declared bus port (R5.6, unchanged).  The matcher in
``connection_graph.cpp`` accepts the bus base-name on the body side via
Candidate A's base-name fallback, so each slot K's single body subgraph
binds to bit K of the parent's bus.

Outcomes asserted:

1. ``kicad-cli sch export netlist`` succeeds (no crash).
2. The child sheet carries scalar hier-labels matching the port base
   names (``GATE``, ``OUT``) — NOT N bare bit-member forms (that was
   the hand-unrolled R5.7 shape Candidate A replaces) and NOT bracketed
   forms.
3. With repeat=4 and one body resistor, the netlist contains exactly
   4 component instances (R1..R4) — down from 16 in the hand-unrolled
   shape.
4. Each slot K's resistor binds to parent bus bits ``GBUS<K>`` and
   ``OBUS<K>`` — the per-slot bit join (R3.3) still holds end-to-end.
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
    """Vectorized bus-port child sheet with ONE body resistor.

    Candidate A canonical shape: the body declares its bus ports
    (``GATE[0..N-1]``, ``OUT[0..N-1]``) but the component inside wires
    to the scalar net names ``GATE``/``OUT``.  The C++ matcher's
    base-name fallback binds each slot K's scalar ``GATE`` subgraph to
    bit K of the parent's ``GBUS[0..N-1]`` bus (and likewise for OUT).
    Body authoring expresses "the per-channel function" once.
    """
    ch = Circuit("ch", ports=[f"GATE[0..{repeat - 1}]",
                              f"OUT[0..{repeat - 1}]"])
    ch.add(R("R1", "GATE", "OUT", value="10k"))
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
    schematic completes with rc=0 and a non-empty .net file.  Candidate
    A's vectorized body has ONE resistor; four slots produce 4
    annotated component instances total (R1..R4)."""
    net = multi_channel_emit["net_text"]
    assert net, "netlist output is empty"
    assert "(export" in net
    refs = sorted(set(re.findall(r'\(comp\s+\(ref\s+"(R\d+)"', net)),
                  key=lambda r: int(r[1:]))
    expected = [f"R{i + 1}" for i in range(4)]
    assert refs == expected, (
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


def test_body_emits_scalar_hier_labels(multi_channel_emit):
    """R5.7 collapse (Candidate A): the body emits exactly ONE scalar
    hier-label per declared port, using the port's base name (``GATE``,
    ``OUT``).  The C++ matcher's base-name fallback binds each slot K's
    scalar subgraph to bit K of the parent's bus.  Bare per-bit forms
    (``GATE0``..``GATE3``) and bracketed forms (``GATE[0]``..) must NOT
    appear in the body."""
    child = multi_channel_emit["child_text"]
    # Positive: exactly one scalar hier-label per port.
    gate_count = child.count('(hierarchical_label "GATE"')
    out_count  = child.count('(hierarchical_label "OUT"')
    assert gate_count == 1, (
        f"expected exactly one '(hierarchical_label \"GATE\"', got "
        f"{gate_count}; child sheet head:\n{child[:1500]}"
    )
    assert out_count == 1, (
        f"expected exactly one '(hierarchical_label \"OUT\"', got "
        f"{out_count}"
    )
    # Negative: no bare bit-member forms and no bracketed forms.
    for k in range(4):
        assert f'(hierarchical_label "GATE{k}"' not in child, (
            f"unexpected bare body hier-label GATE{k} — body should "
            f"be vectorized after R5.7 collapse"
        )
        assert f'(hierarchical_label "OUT{k}"' not in child
        assert f'(hierarchical_label "GATE[{k}]"' not in child
        assert f'(hierarchical_label "OUT[{k}]"' not in child


def test_parent_drives_bus_pins_with_bus_label(multi_channel_emit):
    """The parent label that drives the bus sheet pin must itself be
    bus-shaped (GBUS[0..3]) — anything else would prevent KliCAD's
    connection_graph from seeing the bus on the parent side."""
    sch = multi_channel_emit["sch_text"]
    assert '(label "GBUS[0..3]"' in sch
    assert '(label "OBUS[0..3]"' in sch


def test_per_slot_bit_bus_join(multi_channel_emit):
    """For each slot K, the body part wired to GATE[K]/OUT[K] is
    annotated as a per-slot resistor whose pins land on the parent
    bus bits ``GBUSK`` / ``OBUSK``.  Body parts on bits != K stay on
    slot-local nets — that's the R3.3 fan-out invariant exercised
    end-to-end.

    Annotation lays out the resistors by sheet-instance ordering;
    we don't pin the exact ref-per-bit mapping (auditors observe
    R1, R6, R11, R16 in one ordering) — we only assert that the
    four /GBUS<K> nets each contain exactly one resistor and that
    its pin 1 / pin 2 net memberships agree.
    """
    net = multi_channel_emit["net_text"]
    nets_by_name = _collect_nets(net)
    for k in range(4):
        gname = f"/GBUS{k}"
        oname = f"/OBUS{k}"
        assert gname in nets_by_name, (
            f"expected parent bus-bit net {gname} in netlist; "
            f"nets seen: {sorted(nets_by_name)}"
        )
        assert oname in nets_by_name
        gate_nodes = nets_by_name[gname]
        out_nodes  = nets_by_name[oname]
        assert len(gate_nodes) == 1, (
            f"{gname}: expected exactly one node, got {gate_nodes}"
        )
        assert len(out_nodes) == 1
        # Pin 1 on GBUS<K>, pin 2 on OBUS<K> for the same ref.
        g_ref, g_pin = gate_nodes[0]
        o_ref, o_pin = out_nodes[0]
        assert g_pin == "1"
        assert o_pin == "2"
        assert g_ref == o_ref, (
            f"bit {k}: GATE/OUT bound to different refs ({g_ref} vs {o_ref})"
        )


# ------- helpers ----------------------------------------------------------


def _collect_nets(netlist_text: str) -> dict[str, list[tuple[str, str]]]:
    """Parse the (nets ...) section into ``{net_name: [(ref, pin), ...]}``."""
    out: dict[str, list[tuple[str, str]]] = {}
    in_nets = False
    cur_name: str | None = None
    cur_nodes: list[tuple[str, str]] = []
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
                out[cur_name] = cur_nodes
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
        out[cur_name] = cur_nodes
    return out

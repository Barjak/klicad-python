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
    """Bus-port child sheet with one body resistor per bus bit.

    Mirrors the canonical multi-channel pattern: the body declares
    parts for every bit of its bus ports, and each slot K's annotated
    instance of the bit-K resistor binds to the parent's bit-K bus
    member.  Slot K's other body parts (wired to bits != K) stay on
    slot-local nets (the R3.3 fan-out semantics: only the slot's own
    bit-K body net joins parent's bit K).
    """
    ch = Circuit("ch", ports=[f"GATE[0..{repeat - 1}]",
                              f"OUT[0..{repeat - 1}]"])
    for k in range(repeat):
        ch.add(R(f"R{k + 1}", f"GATE[{k}]", f"OUT[{k}]", value="10k"))
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
    schematic completes with rc=0 and a non-empty .net file.  With one
    resistor per body bit, four slots produce 16 annotated component
    instances total (R1..R16)."""
    net = multi_channel_emit["net_text"]
    assert net, "netlist output is empty"
    assert "(export" in net
    refs = sorted(set(re.findall(r'\(comp\s+\(ref\s+"(R\d+)"', net)),
                  key=lambda r: int(r[1:]))
    expected = [f"R{i + 1}" for i in range(16)]
    assert refs == expected, (
        f"expected R1..R16 across the four slots, got: {refs}"
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

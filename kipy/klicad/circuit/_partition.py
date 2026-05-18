"""Hierarchical block decomposition for Circuit objects (HANDOFF Phase D).

Goal: take a flat Circuit, find natural subcircuit boundaries, and return
a list of Blocks that a future schematic generator can emit as separate
hierarchical sheets.

Algorithm: Louvain community detection on a netgraph where
  - nodes are parts (by ref)
  - edges connect two parts that share a non-power/ground net
  - edge weight = #shared signal nets (so multi-net connections pull
    parts harder into the same community than single-wire taps)

Power and ground nets are excluded from the graph because they touch
nearly every part and would collapse the whole circuit into one
community.  They're still electrically present in every block — the
emitter will just route them via labels rather than hierarchical pins.

Filtering: a Louvain community becomes a Block iff
  - size >= 3 parts, AND
  - |external_nets| / |parts| < 0.3
The first guard prevents tiny "blocks" of two coupled parts.  The
second rejects communities whose boundary fan-out is so large that
hierarchical-sheet encapsulation would not actually reduce sheet-level
complexity.  Communities that fail either guard get merged into a
"top-level" block.

Hypergraph upgrade path: real circuit nets are hyperedges, not pairs.
Louvain on the binary projection is a known approximation that's good
enough for hierarchical-block discovery on the designs we care about.
A future swap to KaHyPar/hMETIS/PaToH keeps the same threshold rule
and Block return shape — only `_build_netgraph` changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._circuit import Circuit


# Communities smaller than this stay in the top-level sheet.
MIN_BLOCK_SIZE = 3

# Boundary-fan-out cutoff.  Lower = stricter (fewer / tighter blocks);
# higher = more / looser blocks.  HANDOFF suggests ~0.3.
MAX_BOUNDARY_RATIO = 0.30


@dataclass
class Block:
    """A candidate hierarchical sub-sheet.

    - parts: refs grouped into this block, in stable iteration order.
    - internal_nets: nets used only by parts inside this block.
      (Power/ground excluded — they're always implicitly global.)
    - boundary_nets: nets that cross the block boundary.  These become
      hierarchical pins on the future sheet.
    - suggested_name: a label suitable for the sheet name.  Currently
      derived from the highest-fan-in part inside; the schematic
      generator is free to override.
    """
    parts: list[str]
    internal_nets: set[str] = field(default_factory=set)
    boundary_nets: set[str] = field(default_factory=set)
    suggested_name: str = ""

    @property
    def size(self) -> int:
        return len(self.parts)

    @property
    def boundary_ratio(self) -> float:
        return len(self.boundary_nets) / self.size if self.size else 0.0

    def __repr__(self) -> str:
        return (
            f"Block(name={self.suggested_name!r}, parts={self.parts}, "
            f"boundary={sorted(self.boundary_nets)}, "
            f"internal={sorted(self.internal_nets)})"
        )


def partition(c: "Circuit") -> list[Block]:
    """Decompose c into Blocks via Louvain on the signal-net graph.

    The first Block in the returned list is always the top-level
    "leftover" block containing parts that didn't end up in a worthy
    community (small or boundary-heavy).  Subsequent Blocks are the
    accepted hierarchical sub-sheets, ordered by size (descending).

    For a circuit with no internal structure (e.g. an LED osc with
    cross-coupled symmetry), every part ends up in the top-level
    block and the returned list has length 1.
    """
    import networkx as nx
    from networkx.algorithms.community import louvain_communities

    G = _build_netgraph(c)

    if G.number_of_nodes() == 0:
        return [Block(parts=[])]

    # Louvain is randomized; pin the seed for reproducibility.  resolution
    # >1 biases toward smaller communities; HANDOFF's threshold rule does
    # the actual block-size policing, so leave resolution at the default.
    communities = louvain_communities(G, weight="weight", seed=42)

    leftover: list[str] = []
    accepted: list[Block] = []
    part_nets = _part_nets_map(c)
    signal_nets_by_part = _signal_nets_by_part(c)

    for community in communities:
        refs = sorted(community)
        block = _make_block(refs, part_nets, signal_nets_by_part)
        if block.size >= MIN_BLOCK_SIZE and block.boundary_ratio < MAX_BOUNDARY_RATIO:
            block.suggested_name = _suggest_name(refs, c)
            accepted.append(block)
        else:
            leftover.extend(refs)

    # Parts the netgraph didn't include (isolated, e.g. a sole voltage
    # source with only power/ground pins) become leftover too.
    in_communities = {ref for comm in communities for ref in comm}
    for p in c.parts:
        if p.ref not in in_communities:
            leftover.append(p.ref)

    leftover_block = _make_block(sorted(set(leftover)), part_nets, signal_nets_by_part)
    leftover_block.suggested_name = "top"

    accepted.sort(key=lambda b: b.size, reverse=True)
    return [leftover_block, *accepted]


# ──────────────────────────────────────────────────────────────────────────
# Internals
# ──────────────────────────────────────────────────────────────────────────

def _signal_nets_by_part(c: "Circuit") -> dict[str, set[str]]:
    """ref -> set of signal nets it touches (power/ground excluded)."""
    out: dict[str, set[str]] = {}
    for p in c.parts:
        nets = set()
        for net_name in p.connections.values():
            meta = c.nets.get(net_name)
            if meta and meta.kind in ("power", "ground"):
                continue
            nets.add(net_name)
        out[p.ref] = nets
    return out


def _part_nets_map(c: "Circuit") -> dict[str, set[str]]:
    """ref -> set of ALL nets it touches (incl. power/ground)."""
    return {p.ref: set(p.connections.values()) for p in c.parts}


def _build_netgraph(c: "Circuit"):
    """Project the hypergraph (nets-as-hyperedges) onto a weighted simple
    graph: an edge between p1 and p2 weighted by the number of signal
    nets they share."""
    import networkx as nx

    G = nx.Graph()
    signal_nets = _signal_nets_by_part(c)
    for ref in signal_nets:
        G.add_node(ref)

    # For each signal net, connect every pair of parts on it.
    net_to_parts: dict[str, list[str]] = {}
    for ref, nets in signal_nets.items():
        for n in nets:
            net_to_parts.setdefault(n, []).append(ref)

    for net, refs in net_to_parts.items():
        for i in range(len(refs)):
            for j in range(i + 1, len(refs)):
                a, b = refs[i], refs[j]
                if G.has_edge(a, b):
                    G[a][b]["weight"] += 1
                else:
                    G.add_edge(a, b, weight=1)

    return G


def _make_block(refs: list[str], part_nets: dict[str, set[str]],
                signal_nets: dict[str, set[str]]) -> Block:
    """Compute internal/boundary nets for a set of parts.

    A net is internal iff every part touching it is in `refs` AND it's a
    signal net (power/ground are implicitly global, never internal).
    """
    member = set(refs)
    inside_nets: dict[str, set[str]] = {}  # net -> set of refs touching it (whole circuit)
    for ref, nets in part_nets.items():
        for n in nets:
            inside_nets.setdefault(n, set()).add(ref)

    internal: set[str] = set()
    boundary: set[str] = set()
    for ref in refs:
        for n in signal_nets[ref]:
            touchers = inside_nets.get(n, set())
            if touchers <= member:
                internal.add(n)
            else:
                boundary.add(n)

    return Block(parts=refs, internal_nets=internal, boundary_nets=boundary)


def _suggest_name(refs: list[str], c: "Circuit") -> str:
    """Best-effort sheet name.  Today: 'blk_<first-ref>'.

    Tomorrow this should consider the role of the highest-degree part
    (e.g. a sheet containing one BJT + biasing → 'stage_Q1') and let the
    user override via a Circuit.tag('block-name', [refs]) API.
    """
    return f"blk_{refs[0].lower()}" if refs else "blk_empty"

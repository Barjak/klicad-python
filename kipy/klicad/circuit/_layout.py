"""Sugiyama-style schematic placement (HANDOFF Phase E).

Replaces the old "everything in one horizontal row at y=ROW_Y" stub
that lived in _kicad_sch.py.  Goal: parts that drive flow left-to-right
into parts that receive; crossings minimized; power rails above, ground
below; symmetric circuits land in a reasonable arrangement instead of
an arbitrary one.

The four classical Sugiyama passes:

  1. Cycle removal.  Our connection graph between parts is undirected
     to begin with — we treat it as the underlying skeleton.  We turn
     it into a DAG by picking sources (V/I parts have an obvious
     drive role) and doing a BFS from them.  Edges that don't fit
     the BFS layering get directed by the layering itself, so there
     are no real "cycles" to remove — symmetric / feedback circuits
     just produce a wider single layer.
  2. Layer assignment.  BFS distance from the nearest source becomes
     the layer index (= column).  Parts unreachable from any source
     (rare; happens for circuits that have no V/I element) land in
     a single trailing layer.
  3. Crossing minimization.  Barycentric ordering applied to each
     layer in alternating sweep directions, for a small fixed number
     of passes.  Good enough for designs of the size we author
     today (≲50 parts).  When designs grow we should switch to
     median ordering or Sander's two-phase heuristic.
  4. Coord assignment.  Map (layer, slot) → (x_mm, y_mm) on a
     uniform grid, centred vertically around y0.

Power/ground nets are *included* in the layout graph here (unlike
partition.py which excludes them) because for placement we want the
power-pin connection of every part to bias it toward the rail.  The
sheet-level power-port symbols are placed separately by
_kicad_sch._place_power_symbols using independent y-offsets above /
below the part row.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._circuit import Circuit


# Grid spacing.  The 2.54 mm "perfboard pitch" is the KiCad-conventional
# alignment grid; placements should land on multiples of it.
_GRID = 2.54

# Column width (horizontal distance between adjacent layers).  Wide
# enough that two-pin parts placed orthogonally don't overlap labels.
LAYER_DX = 8 * _GRID            # 20.32 mm

# Row height within a layer.
SLOT_DY  = 6 * _GRID            # 15.24 mm

# Origin of the placement grid.
ORIGIN_X = 12 * _GRID
ORIGIN_Y = 12 * _GRID

# Crossing-minimization sweep count.  3 alternating passes is the
# textbook recommendation for the barycentric heuristic — diminishing
# returns past that.
CROSSING_PASSES = 3


def sugiyama_positions(c: "Circuit") -> dict[str, tuple[float, float]]:
    """Assign (x_mm, y_mm) to each part ref.

    Layout shape: parts arranged in columns by signal-flow depth from
    voltage/current sources, centered vertically within each column.

    Single-sheet only; per-block sheet emission comes later.
    """
    if not c.parts:
        return {}

    adj = _build_adjacency(c)
    sources = _pick_sources(c)
    layer_of = _layer_assign(c, adj, sources)
    layered = _group_by_layer(layer_of)
    order   = _minimize_crossings(layered, adj)
    return _coord_assign(order)


# ──────────────────────────────────────────────────────────────────────────
# Pass 1: adjacency from nets
# ──────────────────────────────────────────────────────────────────────────

def _build_adjacency(c: "Circuit") -> dict[str, set[str]]:
    """ref -> set of refs connected by *any* shared net (power/ground
    included; they pull a part toward the rail).

    Multiple shared nets collapse to one undirected adjacency — for
    layout, "connected at all" is what matters.
    """
    net_to_refs: dict[str, list[str]] = {}
    for p in c.parts:
        for net in p.connections.values():
            net_to_refs.setdefault(net, []).append(p.ref)

    adj: dict[str, set[str]] = {p.ref: set() for p in c.parts}
    for refs in net_to_refs.values():
        for i in range(len(refs)):
            for j in range(i + 1, len(refs)):
                a, b = refs[i], refs[j]
                adj[a].add(b)
                adj[b].add(a)
    return adj


# ──────────────────────────────────────────────────────────────────────────
# Pass 2: layer assignment
# ──────────────────────────────────────────────────────────────────────────

def _pick_sources(c: "Circuit") -> list[str]:
    """Parts that act as the BFS roots.  V and I sources are the
    natural choice — they drive the circuit.  Fall back to the first
    part if none exist (e.g. a passive-only filter)."""
    sources = [p.ref for p in c.parts if p.kind in ("V", "I")]
    if sources:
        return sources
    return [c.parts[0].ref] if c.parts else []


def _layer_assign(c: "Circuit", adj: dict[str, set[str]],
                  sources: list[str]) -> dict[str, int]:
    """BFS distance from the *closest* source becomes the layer index.

    Unreachable parts get assigned to a single trailing layer one past
    the deepest BFS depth.  This keeps disconnected sub-circuits visible
    on the same sheet instead of silently dropped.
    """
    layer: dict[str, int] = {}
    q = deque()
    for s in sources:
        layer[s] = 0
        q.append(s)

    while q:
        ref = q.popleft()
        d = layer[ref]
        for n in adj[ref]:
            if n not in layer:
                layer[n] = d + 1
                q.append(n)

    if layer:
        trailing = max(layer.values()) + 1
    else:
        trailing = 0
    for p in c.parts:
        if p.ref not in layer:
            layer[p.ref] = trailing
    return layer


def _group_by_layer(layer_of: dict[str, int]) -> list[list[str]]:
    """layer_of -> list-of-lists indexed by layer, refs alphabetic
    within each layer as a stable initial ordering."""
    if not layer_of:
        return []
    n_layers = max(layer_of.values()) + 1
    out: list[list[str]] = [[] for _ in range(n_layers)]
    for ref, lyr in layer_of.items():
        out[lyr].append(ref)
    for layer in out:
        layer.sort()
    return out


# ──────────────────────────────────────────────────────────────────────────
# Pass 3: crossing minimization via barycentric ordering
# ──────────────────────────────────────────────────────────────────────────

def _minimize_crossings(layered: list[list[str]],
                        adj: dict[str, set[str]]) -> list[list[str]]:
    """Iterative barycentric reordering.

    On each pass, walk the layers in alternating direction.  For each
    layer L, reorder its nodes so each node's slot is close to the
    average slot of its neighbours in the *fixed* adjacent layer
    (whichever direction we're sweeping from).  Repeat for a small
    fixed number of passes — barycentric converges fast on the small
    graphs we're laying out.
    """
    if not layered:
        return layered

    layered = [list(L) for L in layered]   # mutate-safe copy
    for sweep in range(CROSSING_PASSES):
        if sweep % 2 == 0:
            # Top-down: layer L is reordered using layer L-1 as the anchor.
            for L in range(1, len(layered)):
                layered[L] = _reorder_by_neighbour_slot(
                    layered[L], layered[L - 1], adj
                )
        else:
            # Bottom-up: layer L reordered against layer L+1.
            for L in range(len(layered) - 2, -1, -1):
                layered[L] = _reorder_by_neighbour_slot(
                    layered[L], layered[L + 1], adj
                )
    return layered


def _reorder_by_neighbour_slot(nodes: list[str],
                               anchor: list[str],
                               adj: dict[str, set[str]]) -> list[str]:
    """Reorder `nodes` by the mean slot index of each node's neighbours
    in `anchor`.  Nodes with no neighbour in `anchor` keep their
    current relative position (assigned a barycenter equal to their
    current index, which preserves the stable initial alphabetic
    order)."""
    anchor_slot = {ref: i for i, ref in enumerate(anchor)}

    def barycenter(ref: str, fallback: float) -> float:
        slots = [anchor_slot[n] for n in adj[ref] if n in anchor_slot]
        if not slots:
            return fallback
        return sum(slots) / len(slots)

    indexed = [(barycenter(r, float(i)), i, r) for i, r in enumerate(nodes)]
    indexed.sort()                   # (bary, original_idx, ref) — stable
    return [r for _, _, r in indexed]


# ──────────────────────────────────────────────────────────────────────────
# Pass 4: coord assignment
# ──────────────────────────────────────────────────────────────────────────

def _coord_assign(layered: list[list[str]]) -> dict[str, tuple[float, float]]:
    """Map (layer index, slot index) -> (x_mm, y_mm).  Each layer is
    vertically centred around ORIGIN_Y, so heights of adjacent layers
    don't visually bias the rail placement."""
    out: dict[str, tuple[float, float]] = {}
    if not layered:
        return out
    max_height = max(len(L) for L in layered)
    y_center = ORIGIN_Y + (max_height - 1) * SLOT_DY / 2

    for li, layer in enumerate(layered):
        n = len(layer)
        y_top = y_center - (n - 1) * SLOT_DY / 2
        for si, ref in enumerate(layer):
            out[ref] = (
                ORIGIN_X + li * LAYER_DX,
                y_top    + si * SLOT_DY,
            )
    return out

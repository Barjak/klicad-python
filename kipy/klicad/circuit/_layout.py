"""Schematic placement engines (HANDOFF Phases E + force-directed).

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


def sugiyama_positions(c: "Circuit",
                       cluster_key: dict[str, int] | None = None,
                       ) -> dict[str, tuple[float, float]]:
    """Assign (x_mm, y_mm) to each part ref.

    Layout shape: parts arranged in columns by signal-flow depth from
    voltage/current sources, centered vertically within each column.

    cluster_key: optional ref -> int mapping (typically the partition
    block index).  When supplied, the within-layer barycentric ordering
    breaks ties by cluster id, so same-cluster parts end up adjacent on
    the sheet — gives a "block-aware" layout on a single page.

    Single-sheet only.
    """
    if not c.parts:
        return {}

    adj = _build_adjacency(c)
    sources = _pick_sources(c)
    layer_of = _layer_assign(c, adj, sources)
    layered = _group_by_layer(layer_of, cluster_key=cluster_key)
    order   = _minimize_crossings(layered, adj, cluster_key=cluster_key)
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


def _group_by_layer(layer_of: dict[str, int],
                    cluster_key: dict[str, int] | None = None,
                    ) -> list[list[str]]:
    """layer_of -> list-of-lists indexed by layer.

    Initial within-layer ordering is (cluster_id, ref) so same-cluster
    parts start out adjacent before the crossing-minimization passes
    rearrange them.
    """
    if not layer_of:
        return []
    n_layers = max(layer_of.values()) + 1
    out: list[list[str]] = [[] for _ in range(n_layers)]
    for ref, lyr in layer_of.items():
        out[lyr].append(ref)
    if cluster_key is None:
        for layer in out:
            layer.sort()
    else:
        for layer in out:
            layer.sort(key=lambda r: (cluster_key.get(r, -1), r))
    return out


# ──────────────────────────────────────────────────────────────────────────
# Pass 3: crossing minimization via barycentric ordering
# ──────────────────────────────────────────────────────────────────────────

def _minimize_crossings(layered: list[list[str]],
                        adj: dict[str, set[str]],
                        cluster_key: dict[str, int] | None = None,
                        ) -> list[list[str]]:
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
            for L in range(1, len(layered)):
                layered[L] = _reorder_by_neighbour_slot(
                    layered[L], layered[L - 1], adj, cluster_key
                )
        else:
            for L in range(len(layered) - 2, -1, -1):
                layered[L] = _reorder_by_neighbour_slot(
                    layered[L], layered[L + 1], adj, cluster_key
                )
    return layered


def _reorder_by_neighbour_slot(nodes: list[str],
                               anchor: list[str],
                               adj: dict[str, set[str]],
                               cluster_key: dict[str, int] | None = None,
                               ) -> list[str]:
    """Reorder `nodes` by the mean slot index of each node's neighbours
    in `anchor`, with an optional cluster id as a secondary sort key
    so same-cluster parts stay adjacent across passes."""
    anchor_slot = {ref: i for i, ref in enumerate(anchor)}

    def barycenter(ref: str, fallback: float) -> float:
        slots = [anchor_slot[n] for n in adj[ref] if n in anchor_slot]
        if not slots:
            return fallback
        return sum(slots) / len(slots)

    def sort_key(idx_ref):
        i, r = idx_ref
        bary = barycenter(r, float(i))
        cid  = cluster_key.get(r, -1) if cluster_key else 0
        # Primary sort: cluster id (keeps blocks contiguous).
        # Secondary:    barycenter (places block within layer correctly).
        # Tertiary:     original index for stability.
        return (cid, bary, i)

    indexed = sorted(enumerate(nodes), key=sort_key)
    return [r for _, r in indexed]


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


# ──────────────────────────────────────────────────────────────────────────
# Spring / force-directed placement (Fruchterman-Reingold via networkx)
# ──────────────────────────────────────────────────────────────────────────
#
# Treats nets as springs pulling connected parts together, all part
# pairs as Coulomb-repulsive, and partition() blocks as extra "cluster
# gravity" — same-block parts get bonus springs between them so the
# block stays clumped on the sheet.
#
# Hooke + Coulomb energy minimization is well-understood for graph
# drawing; networkx.spring_layout is the off-the-shelf
# Fruchterman-Reingold implementation we lean on.

# Preferred separation between adjacent cluster centroids.  ≈3× the
# Sugiyama intra-cluster spacing — clusters are visually distinct
# without making the sheet enormous.
CLUSTER_SEPARATION_MM = 50.0

# How much extra "spring weight" we add between every pair of parts
# in the same partition block (over and above their net-shared edges).
# 1.0 = same strength as a single shared net.  Set high enough that
# intra-cluster pull dominates inter-cluster signal-net pulls — so
# clusters tighten and Coulomb repulsion shoves them apart cleanly.
INTRA_CLUSTER_BONUS = 2.0

# Optimal inter-node distance in mm (passed to networkx as the `k` arg
# after normalization).  Larger -> more breathing room between parts.
INTRA_CLUSTER_SPACING_MM = 15.0

# Initial jitter around the cluster centroid, in mm.  Keeps the FR
# solver from starting parts on exactly the same point (degenerate
# zero-distance Coulomb term).
INIT_JITTER_MM = 5.0

# FR iterations.  100 converges fine for ≤50 nodes.
SPRING_ITERATIONS = 100

# Final coordinates snapped to this grid step.
SNAP_GRID_MM = 2.54


def spring_positions(c: "Circuit",
                     cluster_key: dict[str, int] | None = None,
                     ) -> dict[str, tuple[float, float]]:
    """Force-directed placement with optional cluster-gravity.

    cluster_key: ref -> int (typically the partition() block index).
    When supplied, intra-block phantom springs pull same-block parts
    together AND initial positions are seeded so blocks start on a
    grid roughly CLUSTER_SEPARATION_MM apart.
    """
    import math
    import random
    import networkx as nx

    if not c.parts:
        return {}

    G = _build_spring_graph(c, cluster_key)

    # Isolated nodes (no signal-net edges) — typically power-supply V
    # sources, bulk caps on power rails, etc. — have no spring force
    # and would otherwise float wherever the initial seed puts them
    # and pollute the bounding box.  Pin them along the bottom edge
    # so they don't deform the layout of the actually-connected parts.
    isolated = [n for n in G.nodes if G.degree(n) == 0]
    connected_nodes = [n for n in G.nodes if G.degree(n) > 0]

    # Initial positions: place each cluster's parts in a small disk
    # around its centroid on a coarse grid.
    init_pos = _seed_positions(c, cluster_key)

    # `scale` and `k` are tuned for the CONNECTED subgraph.  We then
    # bolt on the isolated nodes at fixed peripheral positions.
    n_clusters_connected = len({
        cluster_key.get(n, -1) for n in connected_nodes
    }) if cluster_key else 1
    scale = CLUSTER_SEPARATION_MM * max(1.5, math.sqrt(n_clusters_connected))
    k = INTRA_CLUSTER_SPACING_MM

    if connected_nodes:
        # Pin the isolated nodes during the run so the layout area
        # doesn't get hijacked by their initial scatter positions.
        # Pre-place them on a horizontal row well below the spring box.
        peripheral_y = -scale - 2 * CLUSTER_SEPARATION_MM
        for i, ref in enumerate(sorted(isolated)):
            init_pos[ref] = (i * INTRA_CLUSTER_SPACING_MM, peripheral_y)
        pos = nx.spring_layout(
            G,
            pos=init_pos,
            fixed=isolated if isolated else None,
            weight="weight",
            iterations=SPRING_ITERATIONS,
            scale=scale,
            k=k,
            seed=42,
        )
    else:
        pos = init_pos

    # Shift to positive quadrant + snap to grid.
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    dx = ORIGIN_X - min(xs)
    dy = ORIGIN_Y - min(ys)
    out: dict[str, tuple[float, float]] = {}
    for ref, (x, y) in pos.items():
        out[ref] = (
            _snap(x + dx, SNAP_GRID_MM),
            _snap(y + dy, SNAP_GRID_MM),
        )

    # If snapping collapsed two parts onto the same point, nudge one.
    return _resolve_collisions(out)


def _build_spring_graph(c: "Circuit",
                        cluster_key: dict[str, int] | None):
    """Net-adjacency graph with edge weights:
      - 1 per signal net shared between two parts
      - +INTRA_CLUSTER_BONUS if both parts are in the same cluster
    Power/ground nets are excluded; they'd over-couple every part."""
    import networkx as nx
    G = nx.Graph()
    for p in c.parts:
        G.add_node(p.ref)

    net_to_refs: dict[str, list[str]] = {}
    for p in c.parts:
        for net in p.connections.values():
            meta = c.nets.get(net)
            if meta and meta.kind in ("power", "ground"):
                continue
            net_to_refs.setdefault(net, []).append(p.ref)

    for refs in net_to_refs.values():
        for i in range(len(refs)):
            for j in range(i + 1, len(refs)):
                a, b = refs[i], refs[j]
                if G.has_edge(a, b):
                    G[a][b]["weight"] += 1
                else:
                    G.add_edge(a, b, weight=1)

    if cluster_key:
        from collections import defaultdict
        by_cluster: dict[int, list[str]] = defaultdict(list)
        for ref, cid in cluster_key.items():
            by_cluster[cid].append(ref)
        for refs in by_cluster.values():
            for i in range(len(refs)):
                for j in range(i + 1, len(refs)):
                    a, b = refs[i], refs[j]
                    if G.has_edge(a, b):
                        G[a][b]["weight"] += INTRA_CLUSTER_BONUS
                    else:
                        G.add_edge(a, b, weight=INTRA_CLUSTER_BONUS)
    return G


def _seed_positions(c: "Circuit",
                    cluster_key: dict[str, int] | None,
                    ) -> dict[str, tuple[float, float]]:
    """Per-part starting position.  Each cluster centroid sits on a
    coarse grid at CLUSTER_SEPARATION_MM spacing; parts get jittered
    in a small disk around their centroid."""
    import math
    import random
    if cluster_key is None:
        # Random scatter in the working area.
        rng = random.Random(42)
        size = CLUSTER_SEPARATION_MM * max(1, math.isqrt(len(c.parts)))
        return {p.ref: (rng.uniform(0, size), rng.uniform(0, size))
                for p in c.parts}

    from collections import defaultdict
    by_cluster: dict[int, list[str]] = defaultdict(list)
    for p in c.parts:
        by_cluster[cluster_key.get(p.ref, -1)].append(p.ref)

    n_clusters = len(by_cluster)
    cols = max(1, math.ceil(math.sqrt(n_clusters)))
    centroids: dict[int, tuple[float, float]] = {}
    for i, cid in enumerate(sorted(by_cluster)):
        centroids[cid] = (
            (i %  cols) * CLUSTER_SEPARATION_MM,
            (i // cols) * CLUSTER_SEPARATION_MM,
        )

    rng = random.Random(42)
    out: dict[str, tuple[float, float]] = {}
    for cid, refs in by_cluster.items():
        cx, cy = centroids[cid]
        for ref in refs:
            out[ref] = (cx + rng.uniform(-INIT_JITTER_MM, INIT_JITTER_MM),
                        cy + rng.uniform(-INIT_JITTER_MM, INIT_JITTER_MM))
    return out


def _snap(v: float, step: float) -> float:
    return round(v / step) * step


def _resolve_collisions(pos: dict[str, tuple[float, float]],
                        ) -> dict[str, tuple[float, float]]:
    """If grid-snapping landed two parts on the same point, walk one
    of them outward by SNAP_GRID_MM until the point is free."""
    occupied: dict[tuple[float, float], str] = {}
    out: dict[str, tuple[float, float]] = {}
    for ref, (x, y) in pos.items():
        while (x, y) in occupied:
            x += SNAP_GRID_MM   # arbitrary direction; small bias
        occupied[(x, y)] = ref
        out[ref] = (x, y)
    return out

"""Schematic placement engines (HANDOFF Phases E + force-directed).

Replaces the old "everything in one horizontal row at y=ROW_Y" stub
that lived in _klicad_sch.py.  Goal: parts that drive flow left-to-right
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
_klicad_sch._place_power_symbols using independent y-offsets above /
below the part row.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._circuit import Circuit


# Grid spacing.  The 2.54 mm "perfboard pitch" is the KliCAD-conventional
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

# Maximum number of parts stacked vertically in a single Sugiyama layer
# before that layer wraps into multiple sub-columns.  A4 landscape is
# ~210mm tall; at SLOT_DY=15.24mm we fit roughly 13 parts comfortably
# (leaving margins).  Above that we end up with parts running off the
# bottom of the page and labels overlapping into neighbouring layers.
# Wrapping at 8 leaves room for value-text overhang.
MAX_LAYER_HEIGHT = 8

# Horizontal spacing between sub-columns within a wrapped layer.  Smaller
# than LAYER_DX so wrapped layer still reads as "one column-group" while
# leaving room for value labels.
SUBCOL_DX = 4 * _GRID            # 10.16 mm — half LAYER_DX


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
    """Map (layer index, slot index) -> (x_mm, y_mm).

    Layers taller than MAX_LAYER_HEIGHT wrap into multiple sub-columns
    so a single Sugiyama layer doesn't run off an A4 sheet.  A 17-part
    "all V sources" layer (common when a circuit has many independent
    stub sources) without wrapping would stretch 250+ mm vertically;
    wrapping at 8 keeps it inside the page and reduces label overlap
    between adjacent layers.

    Sub-columns within a wrapped layer get SUBCOL_DX horizontal
    separation.  The next Sugiyama layer's x offset accumulates from
    the wrapped layer's full width, so adjacent layers don't collide.

    Layers are vertically centred around a common axis so adjacent
    layer heights don't visually bias rail placement.
    """
    out: dict[str, tuple[float, float]] = {}
    if not layered:
        return out

    # Per-layer effective height (post-wrap).
    layer_heights = [
        min(len(L), MAX_LAYER_HEIGHT) if L else 0
        for L in layered
    ]
    max_height = max(layer_heights) if layer_heights else 0
    y_center = ORIGIN_Y + (max_height - 1) * SLOT_DY / 2

    # Walk layers left-to-right, tracking the cumulative x cursor so
    # wrapped (multi-sub-column) layers don't overlap the next layer.
    x_cursor = ORIGIN_X
    for li, layer in enumerate(layered):
        n = len(layer)
        if n == 0:
            x_cursor += LAYER_DX
            continue
        sub_cols = (n + MAX_LAYER_HEIGHT - 1) // MAX_LAYER_HEIGHT
        rows_per_sub = (n + sub_cols - 1) // sub_cols
        y_top = y_center - (rows_per_sub - 1) * SLOT_DY / 2
        for si, ref in enumerate(layer):
            sub = si // rows_per_sub
            row = si % rows_per_sub
            out[ref] = (
                x_cursor + sub * SUBCOL_DX,
                y_top    + row * SLOT_DY,
            )
        # Advance the cursor past this layer's full width.  A non-wrapped
        # (sub_cols=1) layer advances by LAYER_DX; a wrapped layer
        # advances by the sub-column span plus the inter-layer gap.
        if sub_cols > 1:
            x_cursor += (sub_cols - 1) * SUBCOL_DX + LAYER_DX
        else:
            x_cursor += LAYER_DX
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

# Preferred chord between adjacent cluster centroids on the seeding
# circle.  Has to be at least ~2 * CLUSTER_MAX_RADIUS_MM or clusters
# overlap.  Tuned so the full layout fits inside A4 landscape.
CLUSTER_SEPARATION_MM = 55.0

# Weight of the phantom edges added between EVERY pair of cluster
# centroids.  Connected sub-blocks of a real circuit are often
# electrically isolated on the signal side (they only share power/
# ground), so without these phantom edges the spring graph has many
# disconnected components and Coulomb repulsion drifts them apart
# until they leave the sheet.  Keep this small so it nudges clusters
# closer without breaking the cluster-as-tight-group geometry.
INTER_CLUSTER_PHANTOM_WEIGHT = 0.2

# How much extra "spring weight" we add between every pair of parts
# in the same partition block (over and above their net-shared edges).
# 1.0 = same strength as a single shared net.  Set high enough that
# intra-cluster pull dominates inter-cluster signal-net pulls — so
# clusters tighten and Coulomb repulsion shoves them apart cleanly.
INTRA_CLUSTER_BONUS = 2.0

# Optimal inter-node distance in mm (passed to networkx as the `k` arg).
INTRA_CLUSTER_SPACING_MM = 12.0

# After FR converges, compress each cluster's parts toward its own
# centroid so the cluster's radius is at most this value.  Decouples
# intra-cluster compactness from the inter-cluster spread the spring
# simulation produces.  ~25mm gives a 5-part cluster ~12mm between
# centers, comfortable above the ~7mm body+pin footprint of a Device:R
# at default orientation.
CLUSTER_MAX_RADIUS_MM = 25.0

# Target page-fit box for the centroids of clusters (not parts).  After
# FR converges the inter-cluster spread can exceed an A4 sheet; we
# scale the centroid offsets to fit this box, preserving each
# cluster's intra-cluster geometry (so part spacing within a cluster
# is NOT crushed by the page-fit).
PAGE_FIT_W_MM = 230.0
PAGE_FIT_H_MM = 150.0

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
        # Pin isolated nodes along the TOP edge of the cluster region.
        # Putting them far outside the region would inflate the final
        # bounding box (FR doesn't rescale when there are pinned
        # nodes), and the connected parts would land off-sheet.
        connected_xs = [init_pos[r][0] for r in connected_nodes if r in init_pos]
        connected_ys = [init_pos[r][1] for r in connected_nodes if r in init_pos]
        if connected_xs and connected_ys:
            row_x0 = min(connected_xs)
            row_y  = min(connected_ys) - CLUSTER_SEPARATION_MM
        else:
            row_x0, row_y = 0.0, 0.0
        for i, ref in enumerate(sorted(isolated)):
            init_pos[ref] = (row_x0 + i * INTRA_CLUSTER_SPACING_MM, row_y)
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

    # Compress each cluster's parts toward its own centroid so the
    # cluster is visually tight even though Coulomb repulsion pushed
    # its members outward during the spring simulation.
    pos = _shrink_clusters(pos, cluster_key, CLUSTER_MAX_RADIUS_MM)

    # Squeeze the *centroid layout* to fit a target page box without
    # touching intra-cluster geometry: translate each part by its
    # cluster's centroid delta.
    pos = _fit_centroids_to_page(pos, cluster_key,
                                 PAGE_FIT_W_MM, PAGE_FIT_H_MM)

    # Translate to positive quadrant + snap to KliCAD's grid.
    xs = [p[0] for p in pos.values()]
    ys = [p[1] for p in pos.values()]
    dx = ORIGIN_X - min(xs)
    dy = ORIGIN_Y - min(ys)
    out: dict[str, tuple[float, float]] = {}
    for ref, (x, y) in pos.items():
        out[ref] = (_snap(x + dx, SNAP_GRID_MM),
                    _snap(y + dy, SNAP_GRID_MM))

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

        # Intra-cluster cohesion springs.
        for refs in by_cluster.values():
            for i in range(len(refs)):
                for j in range(i + 1, len(refs)):
                    a, b = refs[i], refs[j]
                    if G.has_edge(a, b):
                        G[a][b]["weight"] += INTRA_CLUSTER_BONUS
                    else:
                        G.add_edge(a, b, weight=INTRA_CLUSTER_BONUS)

        # Inter-cluster phantom edges between one representative of each
        # cluster.  Prevents disconnected-on-signal sub-blocks from
        # drifting apart under Coulomb repulsion.
        reps = [refs[0] for refs in by_cluster.values() if refs]
        for i in range(len(reps)):
            for j in range(i + 1, len(reps)):
                a, b = reps[i], reps[j]
                if G.has_edge(a, b):
                    G[a][b]["weight"] += INTER_CLUSTER_PHANTOM_WEIGHT
                else:
                    G.add_edge(a, b, weight=INTER_CLUSTER_PHANTOM_WEIGHT)
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

    # Place cluster centroids on a circle.  Chord length between
    # adjacent centroids ≈ CLUSTER_SEPARATION_MM, so radius is
    # CLUSTER_SEPARATION_MM / (2 sin(π/n)).  Circle layout gives
    # a 2-dimensional spread instead of the grid's "rows feel like
    # columns" look when the grid has many disjoint clusters.
    n_clusters = len(by_cluster)
    if n_clusters <= 1:
        centroids = {cid: (0.0, 0.0) for cid in by_cluster}
    else:
        radius = CLUSTER_SEPARATION_MM / (2 * math.sin(math.pi / n_clusters))
        centroids = {}
        for i, cid in enumerate(sorted(by_cluster)):
            theta = 2 * math.pi * i / n_clusters
            centroids[cid] = (radius * math.cos(theta),
                              radius * math.sin(theta))

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


def _fit_centroids_to_page(pos: dict[str, tuple[float, float]],
                           cluster_key: dict[str, int] | None,
                           page_w_mm: float, page_h_mm: float,
                           ) -> dict[str, tuple[float, float]]:
    """Rescale ONLY the inter-cluster centroid layout.  Each cluster's
    parts get translated together (rigid body), so the intra-cluster
    spacing produced by _shrink_clusters is preserved exactly."""
    if cluster_key is None or not pos:
        return pos
    from collections import defaultdict
    by_cluster: dict[int, list[str]] = defaultdict(list)
    for ref, cid in cluster_key.items():
        if ref in pos:
            by_cluster[cid].append(ref)

    # Cluster centroids.
    centroids: dict[int, tuple[float, float]] = {}
    for cid, refs in by_cluster.items():
        cx = sum(pos[r][0] for r in refs) / len(refs)
        cy = sum(pos[r][1] for r in refs) / len(refs)
        centroids[cid] = (cx, cy)

    # No clusters have parts in `pos`?  Nothing to rescale.
    if not centroids:
        return pos

    cxs = [c[0] for c in centroids.values()]
    cys = [c[1] for c in centroids.values()]
    cx_span = max(cxs) - min(cxs)
    cy_span = max(cys) - min(cys)
    sx = page_w_mm / cx_span if cx_span > 0 else 1.0
    sy = page_h_mm / cy_span if cy_span > 0 else 1.0
    s = min(1.0, sx, sy)             # only scale DOWN; never inflate

    if s >= 1.0:
        return pos

    # Compute each cluster's translation delta = (s-1) * (centroid - mean_centroid)
    mean_cx = sum(cxs) / len(cxs)
    mean_cy = sum(cys) / len(cys)
    delta: dict[int, tuple[float, float]] = {}
    for cid, (cx, cy) in centroids.items():
        dx = (s - 1.0) * (cx - mean_cx)
        dy = (s - 1.0) * (cy - mean_cy)
        delta[cid] = (dx, dy)

    out = {}
    for ref, (x, y) in pos.items():
        cid = cluster_key.get(ref, -1)
        dx, dy = delta.get(cid, (0.0, 0.0))
        out[ref] = (x + dx, y + dy)
    return out


def _shrink_clusters(pos: dict[str, tuple[float, float]],
                     cluster_key: dict[str, int] | None,
                     max_radius_mm: float,
                     ) -> dict[str, tuple[float, float]]:
    """Per-cluster: if any part is more than max_radius_mm from the
    cluster's centroid, scale that cluster's positions inward so the
    farthest part lands exactly at max_radius_mm.  Decouples
    intra-cluster compactness from inter-cluster spread."""
    if cluster_key is None:
        return pos
    from collections import defaultdict
    by_cluster: dict[int, list[str]] = defaultdict(list)
    for ref, cid in cluster_key.items():
        if ref in pos:
            by_cluster[cid].append(ref)

    out = dict(pos)
    for cid, refs in by_cluster.items():
        if len(refs) <= 1:
            continue
        cx = sum(out[r][0] for r in refs) / len(refs)
        cy = sum(out[r][1] for r in refs) / len(refs)
        max_r = 0.0
        for r in refs:
            dx, dy = out[r][0] - cx, out[r][1] - cy
            d = (dx * dx + dy * dy) ** 0.5
            if d > max_r:
                max_r = d
        if max_r <= max_radius_mm or max_r == 0:
            continue
        s = max_radius_mm / max_r
        for r in refs:
            x, y = out[r]
            out[r] = (cx + (x - cx) * s, cy + (y - cy) * s)
    return out


def _resolve_collisions(pos: dict[str, tuple[float, float]],
                        ) -> dict[str, tuple[float, float]]:
    """If grid-snapping landed two parts on the same point, walk one
    of them outward by SNAP_GRID_MM steps in a square spiral until the
    point is free.

    Spiral (not pure +x) so multiple isolated nodes pinned to the same
    horizontal row don't all chain east in a long line — they fan out
    into a 2-D cluster around the contention point.
    """
    occupied: dict[tuple[float, float], str] = {}
    out: dict[str, tuple[float, float]] = {}
    # Spiral offsets in (dx, dy) units of SNAP_GRID_MM: outward rings,
    # 4*r points per ring of radius r.
    def _spiral():
        for r in range(1, 100):
            for i in range(-r, r):     yield ( r,  i)
            for i in range(-r, r):     yield (-i,  r)
            for i in range(r, -r, -1): yield (-r,  i)
            for i in range(r, -r, -1): yield ( i, -r)
    for ref, (x, y) in pos.items():
        if (x, y) not in occupied:
            occupied[(x, y)] = ref
            out[ref] = (x, y)
            continue
        for dx, dy in _spiral():
            nx, ny = x + dx * SNAP_GRID_MM, y + dy * SNAP_GRID_MM
            if (nx, ny) not in occupied:
                occupied[(nx, ny)] = ref
                out[ref] = (nx, ny)
                break
        else:
            # Spiral exhausted (shouldn't happen for any sane circuit);
            # fall back to the original point and accept the overlap.
            out[ref] = (x, y)
    return out

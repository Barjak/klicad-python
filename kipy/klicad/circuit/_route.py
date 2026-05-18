"""A* schematic wire router with orientation + footprint avoidance.

HANDOFF Phase F.  Opt-in: passing route=True to to_kicad_sch() makes
the emitter draw explicit wires between same-net pins instead of
relying on coincident text labels.

Algorithm

    1. Gather placed-symbol bboxes (via the get_symbol_bbox binding,
       which respects each symbol's current rotation).  Inflate each
       bbox by KEEPOUT_MM so wires don't graze a body edge.
    2. Build a Hanan grid: vertical lines through every pin x; horizontal
       lines through every pin y.  The Hanan grid is provably sufficient
       for optimum Manhattan-routed Steiner trees, and dramatically
       smaller than a uniform grid.
    3. For each signal net (skipping power and ground — those still go
       through power-port symbols and shared labels):
         a. Collect pin coords.
         b. Build a Steiner tree by sequentially adding pins.  The first
            two pins are connected by A*; each subsequent pin is
            connected via A* to its closest point on the existing tree.
            This is the classical "successive shortest path" heuristic;
            simple, ≤2x optimal for small terminal counts.
    4. Submit wire segments via the add_wire binding.

Power and ground stay label/power-port-driven because routing them
would clutter the sheet beyond utility — they touch nearly every part.

Orientation: bbox queries respect rotation today, so a rotated symbol
correctly exposes a rotated obstacle rectangle.  When Phase E starts
rotating parts (e.g. to align pin-out direction with signal flow),
this router will respect the new orientation without code changes.
"""

from __future__ import annotations

import heapq
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._circuit import Circuit


# Inflation around each part body that wires must not enter.  2.54 mm
# (one KiCad grid step) keeps wires visibly off the body.
KEEPOUT_MM = 2.54

# Cost added to a path step when it crosses an existing wire perpendicular.
# Encourages routes that share segments instead of stacking.
CROSSING_PENALTY = 1.0

# Per-bend cost.  Bends are visually noisy, so prefer straight runs.
BEND_PENALTY = 0.25

# A* iteration cap per source/target pair.  A Hanan grid for ≲50 pins
# has ≲2500 nodes; this is a safety net for pathologically obstructed
# routes.
MAX_NODES_EXPANDED = 50_000


def route_signal_nets(c: "Circuit", kicad, placed: dict[str, str]) -> int:
    """Route every signal net by drawing wires between its pin coords.

    Returns the number of wire segments submitted.  Skips power/ground
    nets — those are still connected via power-port symbols.
    """
    obstacles, pin_positions = _gather_geometry(c, kicad, placed)
    nets = _signal_net_terminals(c, pin_positions)

    if not nets:
        return 0

    grid_xs, grid_ys = _hanan_grid(pin_positions)
    if not grid_xs or not grid_ys:
        return 0

    existing_wires: list[tuple[tuple[float, float], tuple[float, float]]] = []
    # Grid points that lie on or between any prior net's wires.  New
    # routes must avoid these to prevent accidental connection between
    # different nets (KiCad merges collinear/coincident wires at save).
    forbidden_points: set[tuple[float, float]] = set()
    segments_emitted = 0

    # Each terminal's own pin coord is always allowed for the net it
    # belongs to.  Build a per-net allow-list of terminal coords so the
    # router can revisit any grid point that happens to be a pin OF
    # this net even if a prior net's wire passed through there (rare).
    fallback_label_count = 0
    for net_name, terminals in nets.items():
        if len(terminals) < 2:
            continue
        own_terms = {(tx, ty) for (_r, tx, ty) in terminals}
        net_forbidden = forbidden_points - own_terms
        tree_segments, all_ok = _route_one_net(
            terminals, obstacles, grid_xs, grid_ys,
            existing_wires, net_forbidden,
        )
        if not all_ok:
            # Fall back to label-based connectivity for this net.
            # Drop partial wires — they could either leave a pin
            # floating or, worse, half-connect a net in a misleading
            # way.  A label at every terminal coord re-establishes
            # connectivity by KiCad's "same-name" rule.
            fallback_label_count += _drop_labels_at_terminals(
                kicad, terminals, net_name
            )
            continue
        for (x1, y1), (x2, y2) in tree_segments:
            r = kicad.run_python(
                f"import kicad_native_schematic_state as ss\n"
                f"ss.add_wire({x1}, {y1}, {x2}, {y2})\n"
                f"True"
            )
            if not r.ok:
                continue
            existing_wires.append(((x1, y1), (x2, y2)))
            segments_emitted += 1
        # Mark this net's wires as forbidden for future nets.
        for (a, b) in tree_segments:
            _record_segment_points(a, b, grid_xs, grid_ys, forbidden_points)
        # Junctions at every wire endpoint AND every terminal pin.
        # KiCad's connection-graph requires an explicit junction at any
        # point where 3+ wire endpoints meet, or where a wire crosses
        # a pin mid-segment.  We add liberally; KiCad drops junctions
        # that turn out unnecessary (its save-time cleanup pass).
        junction_pts: set[tuple[float, float]] = set()
        for (a, b) in tree_segments:
            junction_pts.add(a)
            junction_pts.add(b)
        for _ref, x, y in terminals:
            junction_pts.add((x, y))
        for (x, y) in junction_pts:
            kicad.run_python(
                f"import kicad_native_schematic_state as ss\n"
                f"ss.add_junction({x}, {y})\nTrue"
            )
    if fallback_label_count:
        print(f"[route] {fallback_label_count} pins fell back to label-mode "
              f"after routing failure")
    return segments_emitted


def _drop_labels_at_terminals(
    kicad, terminals: list[tuple[str, float, float]], net_name: str
) -> int:
    """Place a text label at each terminal coord using `net_name`.
    Used as a per-net fallback when A* can't connect every pin."""
    n = 0
    for _ref, x, y in terminals:
        r = kicad.run_python(
            f"import kicad_native_schematic_state as ss\n"
            f"ss.add_label({x}, {y}, {net_name!r})\nTrue"
        )
        if r.ok:
            n += 1
    return n


def _record_segment_points(
    a: tuple[float, float], b: tuple[float, float],
    grid_xs: list[float], grid_ys: list[float],
    forbidden: set[tuple[float, float]],
) -> None:
    """Add every Hanan grid point lying on segment a-b (interior + endpoints)
    to `forbidden`."""
    if a[0] == b[0]:
        x = a[0]
        lo, hi = sorted((a[1], b[1]))
        for y in grid_ys:
            if lo <= y <= hi:
                forbidden.add((x, y))
    elif a[1] == b[1]:
        y = a[1]
        lo, hi = sorted((a[0], b[0]))
        for x in grid_xs:
            if lo <= x <= hi:
                forbidden.add((x, y))


# ──────────────────────────────────────────────────────────────────────────
# Geometry harvest
# ──────────────────────────────────────────────────────────────────────────

def _gather_geometry(
    c: "Circuit", kicad, placed: dict[str, str]
) -> tuple[list[tuple[str, float, float, float, float]],
           dict[tuple[str, str], tuple[float, float]]]:
    """Return (obstacles, pin_positions).

    obstacles:    list of (ref, x_min, y_min, x_max, y_max) in mm.
    pin_positions: (ref, spice_pin_name) -> (x_mm, y_mm).
    """
    obstacles: list[tuple[str, float, float, float, float]] = []
    pin_positions: dict[tuple[str, str], tuple[float, float]] = {}

    for p in c.parts:
        kiid = placed[p.ref]

        r = kicad.run_python(
            f"import kicad_native_schematic_state as ss\n"
            f"ss.get_symbol_bbox({kiid!r})"
        )
        if r.ok:
            import ast
            bb = ast.literal_eval(r.result_repr)
            if bb.get("ok"):
                x = bb["x_mm"] - KEEPOUT_MM
                y = bb["y_mm"] - KEEPOUT_MM
                w = bb["w_mm"] + 2 * KEEPOUT_MM
                h = bb["h_mm"] + 2 * KEEPOUT_MM
                obstacles.append((p.ref, x, y, x + w, y + h))

        for spice_pin in p.connections:
            kicad_pin_num = p.kicad_pin_map[spice_pin]
            r = kicad.run_python(
                f"import kicad_native_schematic_state as ss\n"
                f"ss.get_symbol_pin_position({kiid!r}, {kicad_pin_num!r})"
            )
            if not r.ok:
                continue
            import ast
            pos = ast.literal_eval(r.result_repr)
            if pos.get("ok"):
                pin_positions[(p.ref, spice_pin)] = (pos["x_mm"], pos["y_mm"])

    return obstacles, pin_positions


def _signal_net_terminals(
    c: "Circuit",
    pin_positions: dict[tuple[str, str], tuple[float, float]],
) -> dict[str, list[tuple[str, float, float]]]:
    """net -> list of (part_ref, x, y), signal nets only.

    Carrying the part_ref lets the router skip a terminal's own
    keepout-inflated obstacle, which otherwise covers the pin
    coordinate itself and makes A* refuse to enter the goal cell.
    """
    out: dict[str, list[tuple[str, float, float]]] = {}
    for p in c.parts:
        for spice_pin, net_name in p.connections.items():
            meta = c.nets.get(net_name)
            if meta and meta.kind in ("power", "ground"):
                continue
            pos = pin_positions.get((p.ref, spice_pin))
            if pos is None:
                continue
            out.setdefault(net_name, []).append((p.ref, pos[0], pos[1]))
    return out


def _hanan_grid(
    pin_positions: dict[tuple[str, str], tuple[float, float]],
) -> tuple[list[float], list[float]]:
    """Unique sorted x's and y's across all pins, plus lateral
    "channel" columns/rows offset by GRID_STEP_MM on each side of every
    pin coord.  Channels give the hub-and-spoke router room to place a
    hub that doesn't sit on any pin — necessary because a hub coincident
    with a pin causes KiCad's save-time wire merge to collapse two
    spokes into one straight wire (hiding the pin connection)."""
    base_xs = sorted({round(p[0], 4) for p in pin_positions.values()})
    base_ys = sorted({round(p[1], 4) for p in pin_positions.values()})
    xs_set = set(base_xs)
    ys_set = set(base_ys)
    # Add CHANNELS_PER_PIN lateral columns/rows on each side of every
    # pin so A* has enough room to route around an inflated keepout
    # bbox.  For a part chain laid out tightly along a single column,
    # one or two channels isn't enough — the bbox of a middle part
    # blocks every lateral track its neighbours' channels reach.
    for x in base_xs:
        for k in range(1, CHANNELS_PER_PIN + 1):
            xs_set.add(round(x - k * GRID_STEP_MM, 4))
            xs_set.add(round(x + k * GRID_STEP_MM, 4))
    for y in base_ys:
        for k in range(1, CHANNELS_PER_PIN + 1):
            ys_set.add(round(y - k * GRID_STEP_MM, 4))
            ys_set.add(round(y + k * GRID_STEP_MM, 4))
    return sorted(xs_set), sorted(ys_set)


# Lateral spacing of channel grid lines around each pin.
GRID_STEP_MM = 2.54
# Number of channels per pin per direction.  3 = enough room to route
# around a typical body+keepout (~5 mm).
CHANNELS_PER_PIN = 3


# ──────────────────────────────────────────────────────────────────────────
# Steiner tree by successive A* additions
# ──────────────────────────────────────────────────────────────────────────

def _route_one_net(
    terminals: list[tuple[str, float, float]],
    obstacles: list[tuple[str, float, float, float, float]],
    grid_xs: list[float], grid_ys: list[float],
    existing_wires: list[tuple[tuple[float, float], tuple[float, float]]],
    forbidden_points: set[tuple[float, float]],
) -> tuple[list[tuple[tuple[float, float], tuple[float, float]]], bool]:
    """Connect all `terminals` via a tree of axis-aligned wires.

    Strategy: hub-and-spoke.  Pick a hub coordinate (the median of all
    terminal coords, snapped to the Hanan grid); route each terminal as
    a separate A* path to the hub.  This makes every terminal a wire
    endpoint, which is necessary for KiCad's save-time wire-merge logic
    to preserve a junction at the hub — collinear segments meeting at
    a midpoint get silently merged and lose the connection.

    For two-terminal nets, the hub falls between the two terminals and
    the result is a simple L-shape with no junction needed.
    """
    if len(terminals) < 2:
        return [], True

    snapped = [(ref, _nearest(grid_xs, x), _nearest(grid_ys, y))
               for (ref, x, y) in terminals]

    # Hub = median of terminal coords.  Crucially, the hub must NOT
    # coincide with any terminal — otherwise two spokes converging on
    # that point from opposite directions form one straight wire that
    # KiCad merges, hiding the pin behind a continuous wire and
    # erasing the junction we need for connectivity.  Nudge to the
    # nearest "free" Hanan grid point.
    median_x = sorted(t[1] for t in snapped)[len(snapped) // 2]
    median_y = sorted(t[2] for t in snapped)[len(snapped) // 2]
    terminal_xys = {(t[1], t[2]) for t in snapped}
    hub = _pick_hub(median_x, median_y, grid_xs, grid_ys, terminal_xys, obstacles)

    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    all_ok = True
    for ref, tx, ty in snapped:
        if (tx, ty) == hub:
            continue
        exempt = {ref}
        path = _astar((tx, ty), hub, obstacles, exempt,
                      grid_xs, grid_ys, existing_wires, forbidden_points)
        if not path:
            all_ok = False
            break
        segments.extend(_path_to_segments(path))

    return segments, all_ok


def _pick_hub(
    median_x: float, median_y: float,
    grid_xs: list[float], grid_ys: list[float],
    terminal_xys: set[tuple[float, float]],
    obstacles: list[tuple[str, float, float, float, float]],
) -> tuple[float, float]:
    """Pick a Hanan grid point near (median_x, median_y) that isn't
    a terminal and isn't inside any obstacle interior."""
    # Spiral out from the median over grid neighbours.
    cx = _nearest(grid_xs, median_x)
    cy = _nearest(grid_ys, median_y)
    ix0 = grid_xs.index(cx)
    iy0 = grid_ys.index(cy)
    max_r = max(len(grid_xs), len(grid_ys))

    def is_free(px: float, py: float) -> bool:
        if (px, py) in terminal_xys:
            return False
        for _ref, x0, y0, x1, y1 in obstacles:
            if x0 < px < x1 and y0 < py < y1:
                return False
        return True

    for r in range(0, max_r):
        for dx in range(-r, r + 1):
            for dy in (-r, r) if r > 0 else (0,):
                ix, iy = ix0 + dx, iy0 + dy
                if 0 <= ix < len(grid_xs) and 0 <= iy < len(grid_ys):
                    if is_free(grid_xs[ix], grid_ys[iy]):
                        return (grid_xs[ix], grid_ys[iy])
        for dy in range(-r + 1, r):
            for dx in (-r, r):
                ix, iy = ix0 + dx, iy0 + dy
                if 0 <= ix < len(grid_xs) and 0 <= iy < len(grid_ys):
                    if is_free(grid_xs[ix], grid_ys[iy]):
                        return (grid_xs[ix], grid_ys[iy])

    # Fallback: return median even if occupied.
    return (cx, cy)


def _split_at_terminals(
    segments: list[tuple[tuple[float, float], tuple[float, float]]],
    terminals: list[tuple[float, float]],
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """For each segment, if any terminal lies strictly between its
    endpoints (axis-aligned only), break the segment at that point."""
    out = []
    for (a, b) in segments:
        breakpoints: list[tuple[float, float]] = []
        for t in terminals:
            if _strictly_between(a, b, t):
                breakpoints.append(t)
        if not breakpoints:
            out.append((a, b))
            continue
        # Order breakpoints along the segment (a -> b).
        if a[0] == b[0]:
            breakpoints.sort(key=lambda p: (p[1] - a[1]) * (1 if b[1] > a[1] else -1))
        else:
            breakpoints.sort(key=lambda p: (p[0] - a[0]) * (1 if b[0] > a[0] else -1))
        prev = a
        for bp in breakpoints:
            out.append((prev, bp))
            prev = bp
        out.append((prev, b))
    return out


def _strictly_between(a: tuple[float, float], b: tuple[float, float],
                      t: tuple[float, float]) -> bool:
    """True iff axis-aligned segment a-b contains t strictly in its
    interior (not at either endpoint)."""
    if a[0] == b[0]:           # vertical
        if t[0] != a[0]:
            return False
        lo, hi = sorted((a[1], b[1]))
        return lo < t[1] < hi
    elif a[1] == b[1]:         # horizontal
        if t[1] != a[1]:
            return False
        lo, hi = sorted((a[0], b[0]))
        return lo < t[0] < hi
    return False


def _path_to_segments(
    path: list[tuple[float, float]]
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Collapse a polyline of grid points into the minimum number of
    axis-aligned segments by joining colinear consecutive points."""
    if len(path) < 2:
        return []
    segments = []
    seg_start = path[0]
    for i in range(1, len(path) - 1):
        a, b, c = path[i - 1], path[i], path[i + 1]
        # Bend detected when the axis changes between a->b and b->c.
        bx_axis = "h" if a[1] == b[1] else "v"
        cx_axis = "h" if b[1] == c[1] else "v"
        if bx_axis != cx_axis:
            segments.append((seg_start, b))
            seg_start = b
    segments.append((seg_start, path[-1]))
    return segments


def _astar(
    start: tuple[float, float], goal: tuple[float, float],
    obstacles: list[tuple[str, float, float, float, float]],
    exempt_refs: set[str],
    grid_xs: list[float], grid_ys: list[float],
    existing_wires: list[tuple[tuple[float, float], tuple[float, float]]],
    forbidden_points: set[tuple[float, float]] | None = None,
) -> list[tuple[float, float]] | None:
    """A* on a 4-connected Hanan grid.  Returns a list of (x, y) points
    from start to goal (inclusive), or None if no path within the node
    budget."""
    if start == goal:
        return [start]

    xs, ys = grid_xs, grid_ys
    x_index = {x: i for i, x in enumerate(xs)}
    y_index = {y: i for i, y in enumerate(ys)}

    # Lookup table: which obstacles to skip for the start/goal endpoints
    # (we must be allowed to enter the obstacle of our own symbol).
    def in_obstacle(x: float, y: float) -> bool:
        for ref, x0, y0, x1, y1 in obstacles:
            if ref in exempt_refs:
                continue
            if x0 < x < x1 and y0 < y < y1:
                return True
        return False

    def crosses_existing(a: tuple[float, float], b: tuple[float, float]) -> bool:
        # Cheap check: does the segment a-b cross an existing wire at
        # a non-zero angle?  Used only to inflate cost — not to forbid.
        for (p, q) in existing_wires:
            if _segments_cross(a, b, p, q):
                return True
        return False

    h = lambda x, y: abs(x - goal[0]) + abs(y - goal[1])

    open_heap: list[tuple[float, float, float, tuple[float, float], tuple[float, float] | None]] = []
    counter = 0
    heapq.heappush(open_heap, (h(*start), 0.0, counter, start, None))
    came_from: dict[tuple[float, float], tuple[float, float] | None] = {start: None}
    g_score: dict[tuple[float, float], float] = {start: 0.0}
    expanded = 0

    while open_heap and expanded < MAX_NODES_EXPANDED:
        _, gcur, _, cur, prev = heapq.heappop(open_heap)
        if cur == goal:
            return _reconstruct(came_from, cur)
        expanded += 1

        ix = x_index.get(cur[0])
        iy = y_index.get(cur[1])
        if ix is None or iy is None:
            continue

        for dxi, dyi in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nxi, nyi = ix + dxi, iy + dyi
            if not (0 <= nxi < len(xs) and 0 <= nyi < len(ys)):
                continue
            nx, ny = xs[nxi], ys[nyi]
            if (nx, ny) != goal and in_obstacle((nx + cur[0]) / 2, (ny + cur[1]) / 2):
                continue
            if forbidden_points and (nx, ny) in forbidden_points and (nx, ny) != goal:
                continue
            step_cost = abs(nx - cur[0]) + abs(ny - cur[1])
            if crosses_existing(cur, (nx, ny)):
                step_cost += CROSSING_PENALTY
            # Bend penalty: did we change axis?
            if prev is not None:
                prev_axis = "h" if prev[1] == cur[1] else "v"
                next_axis = "h" if cur[1] == ny       else "v"
                if prev_axis != next_axis:
                    step_cost += BEND_PENALTY

            gnext = gcur + step_cost
            if gnext < g_score.get((nx, ny), math.inf):
                g_score[(nx, ny)] = gnext
                came_from[(nx, ny)] = cur
                counter += 1
                heapq.heappush(open_heap,
                               (gnext + h(nx, ny), gnext, counter, (nx, ny), cur))

    return None


def _reconstruct(
    came_from: dict[tuple[float, float], tuple[float, float] | None],
    end: tuple[float, float],
) -> list[tuple[float, float]]:
    path = [end]
    cur: tuple[float, float] | None = end
    while True:
        cur = came_from.get(cur)
        if cur is None:
            break
        path.append(cur)
    path.reverse()
    return path


# ──────────────────────────────────────────────────────────────────────────
# Geometry helpers
# ──────────────────────────────────────────────────────────────────────────

def _nearest(grid: list[float], v: float) -> float:
    """Snap v to the nearest value in `grid`."""
    if not grid:
        return v
    return min(grid, key=lambda g: abs(g - v))


def _segments_cross(
    a: tuple[float, float], b: tuple[float, float],
    p: tuple[float, float], q: tuple[float, float],
) -> bool:
    """True iff the axis-aligned segments a-b and p-q intersect at a
    single interior point (T-junctions don't count; pure overlap
    doesn't count)."""
    ah = a[1] == b[1]
    ph = p[1] == q[1]
    if ah == ph:
        return False  # parallel
    if ah:
        h, v = (a, b), (p, q)
    else:
        h, v = (p, q), (a, b)
    hy = h[0][1]
    hx0, hx1 = sorted((h[0][0], h[1][0]))
    vx = v[0][0]
    vy0, vy1 = sorted((v[0][1], v[1][1]))
    return hx0 < vx < hx1 and vy0 < hy < vy1

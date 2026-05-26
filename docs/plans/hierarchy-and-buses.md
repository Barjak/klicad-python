# Hierarchical schematics + bus support — implementation plan

Status: **READY TO START H1 — audit applied, open questions resolved**

This plan adds hierarchical schematics (Sub-Circuits as KiCad sub-sheets +
SPICE `.SUBCKT` blocks) and parallel-bus connections to the canonical
circuit DSL and the klicad-python emitter.  Five phases: **H1–H3 land
together** (the working set); H4–H5 are deferred sketches.

## Established context

Before reading the phased plan, internalize:

1. **Thin-layer principle** ([CONTRIBUTING.md](../../CONTRIBUTING.md)).
   `klicad-python` orchestrates; the heavy lifting (parsing, validation,
   netlist generation, ERC) lives in KliCAD's C++.  Hierarchy is no
   exception: KliCAD already knows how to walk a `SCH_SHEET` tree and
   produce a flat netlist; we are not reimplementing that.

2. **Code-as-source-of-truth diff semantics** (per `to_schematic` mode=`diff`).
   For every Part in `c.parts`, KliCAD's symbol receives Sim.*/value
   overwrites unconditionally; aesthetic state (position, rotation,
   custom user fields) survives because we never touch it.  The same
   partition must hold for sheet instances: code owns sheet identity
   + pin names + child-file contents; user owns sheet position + size
   + pin position on the sheet boundary.

3. **The existing IPC surface**.  Today's bindings (verified by
   reading `KliCAD/eeschema/api/bindings_*.cpp`):
   - `klicad_native_schematic_state`: `add_symbol`, `add_wire`,
     `add_junction`, `add_label(kind=...)` — kind ∈ {`local`, `global`,
     `hierarchical`}, `set_symbol_value`, `set_symbol_field`,
     `set_symbol_rotation`, `get_symbol_pin_position`, `get_symbol_bbox`,
     `open_schematic`, `save_schematic`, `get_sheet_count`,
     `get_items_summary`, `list_symbols`, `delete_by_kiid`,
     `clear_routing`.
   - **`klicad_native_hierarchy`** (this module exists and was missed
     in the first draft): `list_sheets`, `get_current_sheet`,
     `set_current_sheet`, `push_sheet`, `pop_sheet`,
     `get_sheet_path_string`, `walk_hierarchy`, `count_instances`,
     `list_sheet_pins`, `update_page_numbers`.  All the sheet
     *navigation* primitives we need already exist.
   - `list_symbols`, `get_items_summary`, and `clear_routing` walk
     `sch.Hierarchy()` (every `SCH_SHEET_PATH`) by default.  Per-sheet
     scoping requires a new optional `sheet_path` argument on the
     first two (the third is fine to leave whole-hierarchy and let
     Python target it via `set_current_sheet` + the new arg).

4. **What the agent's design-loop expects from the DSL**.  See
   `docs/agent-prompts/design-loop.md`.  Phase 5 says "construct the
   schematic via klicad-python's `Circuit` DSL plus `to_schematic()`."
   Sub-circuits need to feel native to that flow — not a side road.

## DSL surface (Option B, locked in)

`Circuit` becomes the universal container.  `ports=` makes it
instance-able as a sub-circuit; without it, the Circuit is the root
sheet of a flat design (existing behavior, unchanged).

```python
# Subcircuit definition
amp = Circuit("amplifier_v1",
              ports=["IN", "OUT", "VCC", "GND"])
amp.add(R("R1", "IN", "n1", value="10k"))
amp.add(NPN("Q1", c="OUT", b="n1", e="GND"))

# Root circuit
top = Circuit("top")
top.add(amp.instance("U_amp1",
                     IN="AUDIO_IN", OUT="AUDIO_OUT",
                     VCC="+12V", GND="GND"))
```

Bus syntax (KiCad-native, `name[a..b]`):

```python
shift_reg = Circuit("shift_reg_x8",
                    ports=["CLK", "DATA_IN", "DATA_OUT[0..7]"])

top.add(shift_reg.instance("U1",
                           CLK="SPI_CLK",
                           DATA_IN="SPI_MOSI",
                           DATA_OUT="DRV_G[0..7]"))    # vector→vector
```

Internally, `Circuit` tracks port list as scalar + vector entries.  The
expansion `DATA_OUT[0..7] → 8 scalar ports DATA_OUT[0]..DATA_OUT[7]`
happens at validation time; SPICE emission and SCH_SHEET pin emission
both consume the expanded form.

Implicit power: nets matching `GND|VCC|VDD|VSS|\+\d|-\d|VBAT|VBUS` (the
existing `_power_positions` regex) flow through the hierarchy without
ever needing to appear in `ports=`.  KiCad's "global power" net
behavior matches: a `power:GND` symbol on any sheet ties to every other
`power:GND` regardless of hierarchy.

### Decisions locked

- **Bus literal**: string `"DRV_G[0..7]"`.  `Bus(name, width)` is
  optional sugar that returns the string.
- **Validation scope**: the DSL validates **port-map-level** invariants
  only — port name exists in the definition, port_map covers every
  port, vector→vector widths align.  Body-level bus-reference
  validation (does `DATA[8]` make sense given port `DATA[0..7]`?) is
  delegated to KliCAD's ERC.  This is corollary 1 in action: KliCAD
  already parses `NET_SETTINGS::ParseBusVector` and resolves
  `SCH_CONNECTION` member sets; we don't reimplement it.
- **Endian**: accept both `DATA[0..7]` and `DATA[7..0]`; canonicalize to
  ascending at storage time; preserve the user's original form in the
  emitter so the schematic label reads the way they wrote it.
- **Port names are not power names**: `Circuit(ports=...)` rejects any
  port whose name matches the implicit-power regex (`GND|VCC|VDD|VSS|
  \+\d|-\d|VBAT|VBUS`).  Power nets are global and never appear in
  port lists — closing the ambiguity case where a port and an
  implicit-power net share a name.
- **Bus syntax forbidden on power nets**: `c.bus("GND", 4)` raises.
  Power nets are scalar.
- **No heterogeneous bundles** (`{A, B, C[0..3]}` style).  Slice-only.

---

# Phase H1 — Sub-Circuit DSL + recursive `.SUBCKT` emission (no GUI)

**Goal**: the SPICE-only path works end-to-end.  Define sub-circuits,
instantiate them with bus port maps, get a recursive deck that
ngspice runs.  No schematic emission, no IPC.  All offline, unit-
testable.

## Files touched

| File | Change |
|---|---|
| `klipy/circuit/_circuit.py` | `Circuit.__init__(ports=None)`; `instance()` method; bus expansion in port maps; `is_subcircuit` property |
| `klipy/circuit/_part.py` | New `SubcircuitInstance(Part)` class — what `Circuit.instance()` returns; lives in the parent's `parts` list |
| `klipy/circuit/_spice.py` | Recursive deck walker; `.SUBCKT` block emission; instance → X-line |
| `klipy/circuit/_bus.py` | New: bus syntax parser, range expansion, validation; pure functions |
| `klipy/circuit/__init__.py` | Re-exports |
| `tests/test_circuit.py` | New cases under `class TestHierarchy` and `class TestBusSyntax` |
| `tests/test_subcircuit.py` | New file — integration tests against PySpice |

## DSL additions

```python
class Circuit:
    def __init__(self, name: str, *,
                 desc: str = "",
                 ports: Sequence[str] | None = None) -> None:
        ...
        self._is_subcircuit = ports is not None
        self._ports = _parse_ports(ports or [])    # expanded scalar list
        self._port_decl = list(ports or [])         # original form for emission

    @property
    def is_subcircuit(self) -> bool: return self._is_subcircuit

    def instance(self, ref: str, **port_map: str) -> "SubcircuitInstance":
        """Bind external nets to this subcircuit's ports.
        Returns a Part-shaped object that the parent .add()s."""
        ...

class SubcircuitInstance(Part):
    """A reference to a sub-Circuit, instantiated in a parent.

    kind = "SUBCIRCUIT".  Emits as `X<ref> <nets...> <subckt_name>`.
    Holds a reference to its definition Circuit (subckt body) for
    recursive emission.  kicad_lib_id is unused (we use SCH_SHEET,
    not SCH_SYMBOL); a placeholder string for diff-key consistency.
    """
    kind = "SUBCIRCUIT"
    spice_letter = "X"
    # pin_names is the port list of the subcircuit (after bus expansion)
    # connections is the parent-side net map (after bus expansion)
```

## Bus parsing (pure)

```python
# klipy/circuit/_bus.py

_BUS_MEMBER_RE = re.compile(r"^(\w+)\[(\d+)\]$")
_BUS_RANGE_RE  = re.compile(r"^(\w+)\[(\d+)\.\.(\d+)\]$")

def is_bus_ref(name: str) -> bool: ...
def expand_bus_range(name: str) -> list[str]:
    """'DATA[0..7]' -> ['DATA[0]', 'DATA[1]', ..., 'DATA[7]']"""

def expand_port_decl(ports: Sequence[str]) -> list[str]:
    """Expand a port declaration list, validating no dups."""

def expand_port_map(decl: Sequence[str],
                    user_map: dict[str, str]) -> dict[str, str]:
    """user_map values may be scalar or bus-range; expand to a
    scalar-only port→net map matching the expanded port decl."""
```

## SPICE emitter changes

```python
# klipy/circuit/_spice.py

def to_spice_deck(c, *, self_running=True, kicad=None) -> str:
    if c.is_subcircuit:
        raise ValueError(
            "to_spice_deck: c is a subcircuit definition, not a root. "
            "Subcircuits are emitted as part of their parent's deck."
        )
    lines = [...preamble...]
    for sc_def in _reachable_subcircuit_kinds(c):
        lines += _emit_subckt_block(sc_def)
    lines += _emit_parts(c)
    lines += _emit_analyses(c, self_running=self_running)
    return "\n".join(lines)

def _reachable_subcircuit_kinds(c: Circuit) -> list[Circuit]:
    """Walk c's parts (including transitive SubcircuitInstance children)
    and return each distinct definition Circuit, once.  Definitions are
    keyed by id() — same Circuit object instantiated N times is one .SUBCKT."""

def _emit_subckt_block(sc: Circuit) -> list[str]:
    """Emit `.SUBCKT <name> <port1> <port2> ...` + body + `.ENDS <name>`.
    Ports are the expanded scalar list (DATA[0..7] -> DATA[0] ... DATA[7])."""
```

## Validation (port-map level only)

`Circuit.validate_all()` must check:

1. **Definition well-formedness**: port list has no duplicate names;
   bus-syntax port names parse as string-grammar; no port name matches
   the implicit-power regex.
2. **Instance port-map coverage**: `port_map` keys cover every port in
   the definition (no missing ports, no extra ports).
3. **Width alignment**: if a port is a vector (`DATA[0..7]`) and the
   bound external net is also a vector, the widths must match
   (`DATA[0..7]` ↔ `BUS[0..3]` raises).
4. **Recursive validation**: when a parent contains an instance,
   validate the definition's port list too (idempotent — cache by id()).
5. **SPICE-name boundary check at deck-emit time**: reject ref/net/model
   names containing SPICE-meaningful characters (whitespace, `=`, `(`,
   `)`, `;`) or starting with a digit (reserved for numeric literals).
   This is the only name-content check we perform; no case translation,
   no munging.  Inputs that pass go to the deck verbatim.

What we **don't** validate (delegated to ERC):

- Whether body-level bus references like `DATA[3]` are within the
  declared port's range.  KliCAD's bus-vector parser already does this.
- Whether net names inside a Sub-Circuit body resolve correctly across
  all sheet boundaries.  `SCH_CONNECTION` resolves this at ERC time.
- Bus-group / unicode / step-by-N range syntax.  Out of scope for the
  DSL; KliCAD's parser owns it.

## Tests (pure-Python, fast)

- Subcircuit definition with all-scalar ports
- Subcircuit with bus port → scalar port_map (8 scalar entries)
- Subcircuit with bus port → bus port_map shorthand
- Nested subcircuits (depth 2)
- Same definition instantiated multiple times — one `.SUBCKT` block, N
  X-lines
- Implicit power: GND/VCC flow through without being in ports
- Validation: missing port in port_map → ValueError
- Validation: bus width mismatch → ValueError
- Validation: typo in body net name → ValueError
- Validation: power net in bus syntax → ValueError
- `to_spice_deck` round-trip through ngspice (PySpice load + simple op)

---

# Phase H2 — Single-level hierarchical schematic emission

**Goal**: `to_schematic(top)` writes `top.kicad_sch` (with `SCH_SHEET`
items + sheet pins) and one child `.kicad_sch` per Sub-Circuit *kind*
(with hier-labels matching the parent sheet pins).  Flat hierarchy
only — Sub-Circuits cannot contain Sub-Circuits.  KliCAD's ERC and
SPICE netlist export "just work" because they walk the hierarchy.

## C++ delta (smaller than originally planned)

Sheet navigation already exists in `klicad_native_hierarchy`.  Only
sheet *creation* and per-sheet *scoping* are new.

**New bindings in `klicad_native_schematic_state`:**

```cpp
add_sheet(name, filename, x_mm, y_mm, w_mm, h_mm) -> {ok, kiid}
    // Places a SCH_SHEET on the *current* sheet (root or otherwise).
    // filename is relative to the project dir.  The child .kicad_sch
    // itself is created by Python via write_project_shell-equivalent;
    // this binding only places the SCH_SHEET item that points at it.

add_sheet_pin(sheet_kiid, name, side, position_mm, shape="bidirectional")
    -> {ok, kiid}
    // Adds a SCH_SHEET_PIN to an SCH_SHEET.  side ∈ {"left","right","top","bottom"}.
    // shape ∈ {"input","output","bidirectional","tri_state","passive"}.
    // name may carry bus syntax — KliCAD's name parser detects automatically.
```

**Existing bindings get optional scope args (not new bindings):**

```cpp
list_symbols(sheet_path="") -> [...]
    // "" = whole hierarchy (today's behavior); "/U1" = only that sheet.
    // Each returned row also gains a "sheet_path" field so the Python
    // diff can group by sheet without a second round-trip.

clear_routing(sheet_path="") -> {ok, removed}
    // Same scoping treatment.

delete_by_kiid(kiid) -> {ok, kiid}
    // UUIDs are globally unique; no scoping needed.  Unchanged.
```

**Existing bindings consumed unchanged:**

- `klicad_native_schematic_state.add_label(x, y, name, kind="local")` —
  default kind is local; pass `kind="hierarchical"` for hier-labels.
- `klicad_native_hierarchy.set_current_sheet(path)` — sets the active
  sheet so subsequent `add_symbol` / `add_label` target it.
- `klicad_native_hierarchy.push_sheet(uuid)` / `pop_sheet()` — scoped
  navigation, returns to previous on pop.
- `klicad_native_hierarchy.list_sheet_pins(sheet_uuid)` — used by the
  label-position resolver for sheet instances.
- `klicad_native_hierarchy.walk_hierarchy()` — gives the Python diff
  the list of (path, screen_kiid) pairs to iterate over.

## Sheet-file ergonomics

Each Sub-Circuit *kind* → one child `.kicad_sch` file in the project
dir, named after the Circuit's `name` sanitized with
`re.sub(r"[^a-zA-Z0-9_]", "_", name)`.  After sanitization, raise if
two distinct definitions collapse to the same filename (e.g.,
`amp/v1` and `amp_v1` both → `amp_v1`).  Multiple instances of the
same Sub-Circuit share the file (KiCad's "complex hierarchy"); each
`SCH_SHEET` on the parent points at it with its own `ref` and
instance-bookkeeping.

`write_project_shell` extends to:

1. Write parent `.kicad_pro` (the existing near-empty form is enough
   at shell-write time — KliCAD writes the `sheets` array on
   `save_schematic` after the hierarchy is populated, per corollary 3:
   the project-file sheet list is KliCAD-side state, not Python's).
2. Write parent `<top>.kicad_sch` stub (existing form).
3. For each Sub-Circuit kind: write `<sanitized_name>.kicad_sch` stub.
   **Verify** that the existing stub's `(sheet_instances (path "/" ...))`
   line is correct for a child (it's currently written for the root —
   may need to be empty or different for a child).  Inspect a freshly-
   created KiCad sheet to confirm.

## `to_schematic` recursion

The shape of the recursion: emit child sheets first (so the SCH_SHEETs
on the parent have something to point at), then emit the parent
canvas (which places SCH_SHEETs alongside scalar parts).

```python
def to_schematic(c, sch_path, *, kicad=None, layout="sugiyama",
                 route=False, mode="diff"):
    if c.is_subcircuit:
        raise ValueError(
            "to_schematic: c is a subcircuit definition.  Pass the root "
            "Circuit; subcircuit sheets are emitted automatically."
        )
    # ... existing project-shell + IPC bootstrap ...

    # 1. Per Sub-Circuit kind: emit body into its child .kicad_sch
    for sc_def in _reachable_subcircuit_kinds(c):
        child_sch_path = _child_sch_path(sc_def, project_dir)
        _emit_sheet(sc_def, child_sch_path, kicad, mode=mode)
        # set_current_sheet to the child, _place_parts (body),
        # _label_pins (existing — all local), _emit_port_anchors
        # (one hier-label per port, see below), _place_power_symbols
        # for power nets used in the body.  Then pop back to root.

    # 2. Back on root: place SCH_SHEETs + scalar parts together.
    #    _place_parts now branches on p.kind == "SUBCIRCUIT" to call
    #    add_sheet + add_sheet_pin instead of add_symbol.
    _emit_sheet(c, sch_path, kicad, mode=mode)

    # 3. kicad_native_annotation.annotate(scope="all") runs once at
    #    the end, after the full hierarchy is populated.  KiCad's
    #    per-path symbol-instance bookkeeping requires this.
    kicad.run_python("import klicad_native_annotation as a; a.annotate(scope='all')")

    # 4. save_schematic — single call walks every screen + updates
    #    .kicad_pro's sheets array.
    kicad.run_python("import klicad_native_schematic_state as ss; ss.save_schematic()")
```

### Per-pin emission: kind-branches at three call sites

`SubcircuitInstance` lives in the parent's `c.parts` list with
`kind == "SUBCIRCUIT"`.  Three emitter functions branch on it:

| Function | `p.kind in symbol_kinds` | `p.kind == "SUBCIRCUIT"` |
|---|---|---|
| `_place_parts` | `add_symbol(lib_id, ref, x, y)` + set value + set Sim.* fields | `add_sheet(name, child_filename, x, y, w, h)` + `add_sheet_pin(kiid, name, side, pos)` per port |
| `_label_pins` (pin position) | `get_symbol_pin_position(kiid, kicad_pin_num)` | `list_sheet_pins(sheet_kiid)` → `{name: (x, y)}` |
| `_route` (pin position) | same as above | same as above |

`SubcircuitInstance` is a `Part` subclass with **per-instance**
`pin_names` and `connections` (not class-level — each instance has
its own expanded port set after bus expansion).  `kicad_lib_id` and
`kicad_pin_map` are symbol-shaped fields and stay unused for sheet
instances.

## Sheet sizing + pin layout

The SCH_SHEET on the parent is a rectangle with pins on its edges.
Initial sizing rule:

- Width: `max(8 * GRID, len(longest_pin_name) * 1.27 + 4*GRID)`
- Height: `4 * GRID * max(left_pin_count, right_pin_count) + 8 * GRID`
- Inputs → left edge, outputs → right edge, bidirectional → split
  (first half left, second half right).  Per the locked decision, no
  power ports appear here (rejected at `Circuit(ports=...)` time).
- Pin position: evenly spaced, 2*GRID from top edge, 2*GRID spacing.

### Layout interaction (H2 hack, H5 proper fix)

`_layout_positions` and the Sugiyama placer (`_layout.py:_PART_DX`,
`_PART_DY`) assume per-part stride; a 60×40 mm SCH_SHEET dropped on
the same grid overlaps neighboring parts.  **H2 hack**: when a Part's
`kind == "SUBCIRCUIT"`, the layout engine multiplies its grid cell
size by a sheet-stride factor (e.g., 4× the normal `_PART_DX`/`_DY`).
The placement still uses `_layout_positions` — sheets just consume
more grid cells than scalar parts.  Visually crude; correct
topology.  **H5** replaces with bbox-aware layout that respects each
part's actual rendered footprint.

## Sheet-pin emission (parent side)

For each port in `sc_def._port_decl`:

- `add_sheet_pin(sheet_kiid, name, side, position_mm, shape)`
- `shape` defaults to `"bidirectional"` for now (KliCAD treats all
  shapes equivalently in netlist; differs only in rendering glyph).
  We can later infer from port-usage analysis (drives-only → output,
  read-only → input) — defer to H3.

### Binding external nets to sheet pins (parent side)

A sheet pin's external connection is established by a regular **local**
label at the pin's external position naming the external net.  This
mirrors how regular symbol pins work: `_label_pins` drops a local
label at each pin position naming the connecting net; KliCAD's net
resolver merges by name.

Concretely: when `_label_pins` encounters `p.kind == "SUBCIRCUIT"`, it
calls `list_sheet_pins(sheet_kiid)` to get pin coordinates by name,
then drops a local label at each named position with the
corresponding external net.  Same control-flow shape as for symbol
pins — just a different pin-position resolver.

## Hier-label emission (child side): one anchor per port

Inside each child sheet, port nets must use **hierarchical** labels
(not local) — that's the KiCad mechanism by which a net is "exposed"
as a sheet pin.  The cleanest emission strategy: one hier-label per
port, placed once at a canonical anchor position along the canvas
edge.  KliCAD's net resolver merges any **local** label naming the
same net (which `_label_pins` already drops at every part pin) into
the hier-label by name-coincidence.

```python
def _emit_port_anchors(sc_def, kicad):
    """Place one hier-label per port at a canonical anchor position."""
    for port_name, side in _classify_ports(sc_def):
        x, y = _anchor_position(side, port_name)
        kicad.run_python(
            f"import klicad_native_schematic_state as ss; "
            f"ss.add_label({x}, {y}, {port_name!r}, kind='hierarchical')"
        )
```

This means:

- `_label_pins` stays exactly as it is today (always emits local
  labels).
- Inside a Sub-Circuit body, port-bound nets get local labels at
  every pin position (from `_label_pins`) **and** one hier-label at
  the port anchor (from `_emit_port_anchors`).  KliCAD merges them by
  name.
- No new code path inside `_label_pins`.  The hier-label work is one
  small step in the per-sheet emission.

## Diff/apply per sheet

The diff operates one sheet at a time.  Today's flow:

```
list_symbols (whole hierarchy)
delete remove_refs (anywhere in hierarchy)
clear_routing (whole hierarchy)
_place_parts (current sheet only)
```

becomes:

```
for sheet_path in walk_hierarchy(c):       # uses existing klicad_native_hierarchy
    set_current_sheet(sheet_path)
    existing = list_symbols(sheet_path)    # NEW scope arg
    delete (remove_refs ∩ this sheet)
    clear_routing(sheet_path)              # NEW scope arg
    _place_parts (now branches on p.kind)
    _label_pins (now uses pin-position resolver branched on p.kind)
    _emit_port_anchors (if this sheet is a Sub-Circuit body)
    _place_power_symbols
```

The per-sheet `_place_power_symbols` already works: `_place_power_symbols`
walks `c.nets` for the *current* Circuit (parent or sub).  Each sub's
`c.nets` was populated by `_register_net_from_part` during construction.
Power nets used inside a Sub-Circuit get power symbols on that sheet.

## Sheet-instance diff

**Diff identity is `(sheet_path, ref)` tuples** — matching KiCad's own
annotation model (a `Q3` at `/U1/U_amp1` is distinct from `Q3` at
`/U2/U_amp1`).  `Circuit.add`'s uniqueness check stays per-Circuit
(unchanged); the diff just keys by the tuple instead of the bare ref.

For sheet instances specifically, the demotion check uses sheet-shaped
fields, not `lib_id`:

```python
for (sheet_path, ref) in keep_candidates:
    existing = existing_by_key[(sheet_path, ref)]
    target_part = target_by_key[(sheet_path, ref)]

    if existing["kind"] != target_part.kind:
        # Cross-kind: sheet ↔ symbol with same ref.  Identity changed.
        demote_to_remove_add(sheet_path, ref)

    elif target_part.kind == "SUBCIRCUIT":
        if existing["file_name"] != _child_sch_path(target_part.definition).name:
            # Sub-Circuit kind changed under this ref.  Full remove+add.
            demote_to_remove_add(sheet_path, ref)
        # Otherwise: kept.  Per-pin diff happens in _update_sheet_pins
        # (see H3 — H2 placeholder is whole-sheet remove+add on any
        # port-set difference; H3 refines to per-pin add/delete).

    else:
        if existing["lib_id"] != target_part.kicad_lib_id:
            demote_to_remove_add(sheet_path, ref)
```

`list_symbols` is extended so its rows carry the discriminator and the
sheet-specific extras:

- `kind`: `"SUBCIRCUIT"` for SCH_SHEET items; for SCH_SYMBOL items the
  field is omitted and `lib_id` carries the discrimination as today.
- For SCH_SHEET rows: `file_name=<child .kicad_sch path>` +
  `pins=[{name, side, x_mm, y_mm, kiid}, ...]`.
- `sheet_path`: which sheet the item lives on (every row).

This makes the demotion logic clean: branch on `kind`, two
shape-specific equality checks.

## Tests (live KliCAD, parallel to test_diff_apply.py)

A new `tests/test_hierarchy.py` mirroring the diff/apply suite:

- **H2-0** (smoke before topology tests): `set_current_sheet` round-trip
  across all child sheets does not crash KliCAD.  The schematic-switch
  crash (HANDOFF.md crash #2) is about `pm.load_project`, not
  `SetCurrentSheet`, but no test confirms this yet.
- **H2-A**: place a top circuit with one sub-circuit instance; verify
  child .kicad_sch exists, SCH_SHEET present on root, sheet pins
  match ports.
- **H2-B**: ERC the result via the existing ERC binding — should be
  clean (no dangling sheet pins, no orphan hier-labels).
- **H2-C**: SPICE netlist export — verify `.SUBCKT` block + `X`
  instance line appear.
- **H2-D**: Diff add a new instance of an existing Sub-Circuit kind
  — existing instance survives, new one added.
- **H2-E**: Diff remove an instance — sheet removed, child file
  preserved (other instances may still reference it).
- **H2-F**: Diff change a Sub-Circuit's port list (add a port) —
  every instance forces remove+add; child file's hier-labels updated.
- **H2-G**: Diff change a body Part inside a Sub-Circuit (value 100
  → 1k) — instance count unchanged; child sheet's Value field
  updated (spice-idempotence per-sheet).
- **H2-H**: Bus port width change (`DATA[0..7]` → `DATA[0..15]`) —
  port-list-mismatch demotion, sheet rebuilt with new pin set.
- **H2-I**: Multiple instances of same Sub-Circuit (U1, U2, U3) —
  one child file, three SCH_SHEETs.  Each instance's net binding
  independent.

---

# Phase H3 — Diff/apply across the hierarchy

**Goal**: H2 ships the recursive emit path; H3 hardens it.  Diff is
correct under all the cases H2 tests, plus:

## Robustness items

- **Per-pin diff within kept sheet instances** (the headline H3 work).
  H2 placeholder: any port-set difference forces whole-sheet
  remove+add.  H3 replaces with: keep the `SCH_SHEET` kiid, preserve
  position and user-positioned pins for ports that still exist, only
  add/delete the pins that changed.  This extends the aesthetic-
  preservation contract from "user-edited symbol position survives
  diff" to "user-positioned sheet pin survives diff as long as the
  port name didn't change."
  - Demotion to whole-sheet remove+add only on `child_filename`
    mismatch (i.e., the Sub-Circuit kind changed) or kind cross-over
    (sheet ↔ symbol).
  - Verify `delete_by_kiid` works on `SCH_SHEET_PIN` items.  UUIDs
    are universal in KiCad; should work without modification.
  - The H2 diff code keys sheet instances on `(ref, child_filename,
    port_set)` already, so H3's refinement is local — no rework of
    the diff-set arithmetic.
- **Stale child files**: when a Sub-Circuit kind is removed from the
  Circuit (no more instances anywhere), its child .kicad_sch is
  garbage.  Decision: leave the file on disk (cheap, harmless for git
  history; KiCad ignores unreferenced sheets) BUT remove the parent's
  reference + sheet entry from `.kicad_pro`.
- **Per-sheet `_place_power_symbols`**: each child sheet places its own
  power symbols for its locally-used power nets.  Power symbols are
  already filtered out of the diff (#PWR_* refs).  The fix from
  `clear_routing` (sweep #PWR_*) extends to child sheets via the
  per-sheet scoping.
- **Per-sheet routing**: `_label_pins` and `route_signal_nets` now
  branch on `p.kind == "SUBCIRCUIT"` for the pin-position resolver
  (calling `list_sheet_pins` instead of `get_symbol_pin_position`).
  In hierarchical mode they run per-sheet, scoped to that sheet's
  parts.  Port nets exit each child sheet through the single
  hier-label anchor per port; local labels at body pins merge in by
  name-coincidence.
- **`lib_id_mismatch` guard against sheet rows**: the current diff
  computes `existing_by_ref[ref]["lib_id"]`.  After H2 introduces
  SCH_SHEET rows that don't carry `lib_id`, the check must branch on
  `kind` first (per the demotion-key logic above) to avoid `KeyError`.
- **Save**: `save_schematic` saves the whole hierarchy in one call
  (KliCAD-side it walks all screens) and updates `.kicad_pro`'s
  `sheets` array.  Confirm via test that re-loading the project
  reproduces the placed state.

## Spice-idempotence under hierarchy

`Group A` of test_diff_apply.py runs again, but **per sub-sheet**:

- Value change on a body part inside a sub-circuit → that body part's
  Value field updates, sheet instance kiid unchanged, parent SCH_SHEET
  kiid unchanged, other instances of same Sub-Circuit unaffected.
- Model change on a body part — same.
- Sim.Params change on a body part — same.
- lib_id mismatch on a body part → that part remove+add inside the
  child sheet, instance kiid unchanged.

## Tests

`tests/test_hierarchy_diff.py` — adds the H3 robustness cases on top
of H2.

---

# Phase H4 (sketch) — Nested hierarchy

Sub-Circuits containing Sub-Circuits.  Conceptually a small change:
`_reachable_subcircuit_kinds` already does a transitive walk; child
sheet emission recurses; per-sheet diff scoping is parameterized by
sheet path which is already arbitrary depth.

Main work:

- **Sheet-path naming**: `set_current_sheet("/U1/U_inner")` — make sure
  the path encoding survives multi-segment depths.  KliCAD's
  `SCH_SHEET_PATH` is already segmented, just need the binding to
  accept "/" separated strings.
- **Recursive `to_schematic`**: each Sub-Circuit calls into the same
  emission code, no special-casing depth.
- **Instance bookkeeping**: KiCad records per-path reference annotations
  for instances (so U1/Q3 vs U2/Q3 are distinct in the netlist).  The
  existing `pre-annotate` step has to run per-sheet-path; verify the
  annotation binding handles it.

## Risks

- Deep hierarchies may stress the existing `list_symbols` /
  `clear_routing` walkers — confirm linear time scaling.
- ERC marker enumeration (gap #4, deferred) becomes more important
  here, since hierarchy makes more error classes possible (e.g.
  hier-label / sheet-pin name mismatch).

---

# Phase H5 (sketch) — Auto-layout for sheets

The H2 sheet-sizing rule is crude.  H5 replaces it with:

- **Sub-Circuit body layout**: existing `_layout_positions` (Sugiyama
  + spring) runs per-sheet.  Already partition-aware.
- **Sheet-pin placement**: order pins along edges to minimize wire
  crossings on both sides (parent canvas + child canvas).  Probably a
  simple greedy heuristic: group pins by which net cluster on the
  child side they connect to, place those groups together.
- **Sheet body sizing**: shrink-to-fit based on actual child contents
  rather than the crude pin-count rule.
- **Parent canvas layout**: SCH_SHEETs are larger than parts; the
  existing `_PART_DX`/`_PART_DY` spacing has to scale to per-instance
  bbox.  `_layout_positions` already returns x,y; the change is that
  parts carry a `(w_mm, h_mm)` and the layout engine respects it.

## Open questions

- Should sheet-pin order on parent and child auto-mirror, or do we
  always place pins by side (input-left, output-right) regardless of
  child's internal layout?
- Is there value in laying out sheet contents to minimize hier-label
  position relative to parent's sheet-pin layout (cross-sheet
  optimization)?  Probably overkill.

---

# Risks (post-audit, post-resolution)

1. **Sheet-pin shape from port-usage analysis** — H2 punts to
   "bidirectional" for all.  Cleaner would be: walk the body, classify
   each port as input (read only) / output (driven only) / bidirectional
   (both).  Defer to H3 — easy to add once H2's flow is stable.

2. **Bus port + scalar net binding** — `top.add(amp.instance("U1",
   DATA="SCALAR_NET"))` where `DATA` is a bus port and `SCALAR_NET` is
   not a bus — raise.  Bus-to-scalar needs explicit member access
   (`DATA[0]="SCALAR_NET"`).

3. **SPICE name validation at deck-emit time** — reject ref/net/model
   names containing SPICE-meaningful characters (whitespace, `=`, `(`,
   `)`, `;`, leading digit on ref).  Cheap input validation at the
   system boundary.  No case translation, no name munging — emit
   verbatim otherwise.  SPICE is case-insensitive per spec; ngspice
   honors it.  If ngspice ever surfaces a real bug, fix it upstream
   rather than adding a Python-side translation layer.

4. **Sheet-instance ref designators** — KiCad convention is `U<n>` for
   sheet instances (matching IC convention).  Our DSL is agnostic
   (`amp.instance("U_amp1", ...)`).  No prefix enforcement.

5. **The agent's current 8pin design** uses 8 shift-register-driven
   channels — perfect H2 test target.  Once H1+H2 land we re-dispatch
   the design-loop agent and see if it picks up the new DSL naturally.

# Resolved (no longer open questions)

- **Diff identity under hierarchy**: `(sheet_path, ref)` tuples.
  Circuit.add's per-Circuit uniqueness check stays unchanged.  Ref
  reuse across sheets is allowed and matches KliCAD's annotation
  semantics.
- **Sheet-instance port-rename granularity**: H3 ships per-pin diff
  within kept sheet instances; whole-sheet remove+add only on
  `child_filename` mismatch.  H2 placeholder is whole-sheet
  remove+add on any port set difference — H3 replaces.
- **PySpice / ngspice case sensitivity**: dissolved.  Emit verbatim.
  No translation layer.

---

# Dependencies

- H1 depends on nothing — purely additive to the DSL.
- H2 depends on H1 (the DSL must be settled before SCH_SHEET emission
  has anything to emit).
- H2 requires new C++ bindings + KliCAD rebuild.
- H3 depends on H2 + the existing diff/apply test infrastructure.
- H4 and H5 depend on H2 (and H3 for diff support).

# Out of scope (explicitly)

- **Hierarchical net inspection via IPC** — there's no `get_net(name)`
  binding today; we don't need one for H1–H3.  Tests rely on the
  exported SPICE netlist + ERC dispatch result.
- **Sheet renaming** — if a user renames a Sub-Circuit, the child
  file's name changes.  Treat as remove+add of the kind.  Don't
  attempt to rename in-place.
- **Pin reordering on a sheet** — once H2 places pins by the port
  order, manual rearrangement in KliCAD survives via the position-
  preservation diff property.  We don't try to "track" reorder vs.
  add+remove.
- **Multi-page sheets** — KiCad supports sheets with multiple
  `.kicad_sch` files representing pages of one logical sheet.  Defer.
- **External `.kicad_sch` references** — pulling in someone else's
  schematic as a sub-circuit.  Defer.

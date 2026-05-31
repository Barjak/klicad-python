# klicad-python DSL — reference catalogue

A neutral inventory of what the `klipy` / `klipy.circuit` layer exposes.
This is a map of the territory, not a recipe.  The agent picks the
right tools for the problem; this doc just lists what's in the
toolbox.

For binding-level (run_python) APIs into KliCAD's embedded interpreter,
see the `klicad_native_*` module sections at the end.

---

## Top-level imports

```python
from klipy import KliCAD                              # IPC client
from klipy.circuit import (
    Circuit, NetMeta, NetKind,
    Part,
    R, C, L, D, LED,
    NPN, PNP,
    NMOS, PMOS, NJFET, PJFET,
    V, I,
    XSubckt,
    ModelCard,
    Analysis, Tran, Ac, Dc, Op, Noise, Control,
    ALL_PARTS, ALL_ANALYSES,
    STANDARD_MODEL_LIB,
    write_project_shell,
)
```

`STANDARD_MODEL_LIB` — absolute path to a bundled `.lib` that ships
the basic primitive models (`2N3904`, `2N3906`, `1N4148`, `NMOS_S`,
`PMOS_S`, etc.).  Add via `c.add_model_lib(STANDARD_MODEL_LIB)`.

## klipy.KliCAD — the IPC client

```python
k = KliCAD(timeout_ms=300_000)              # connect (defaults to /tmp/klicad/api.sock)
k.is_alive()                                # bool — cheap probe
k.get_version()                             # KliCADVersion
k.run_python(code: str)                     # execute Python inside KliCAD's
                                            #   embedded interpreter; returns
                                            #   PythonRunResult with .ok,
                                            #   .result_repr, .stdout, .stderr,
                                            #   .exception_traceback
```

The `run_python` bundle is the primary way to call into KliCAD's
`klicad_native_*` modules.  One IPC round-trip per call.

## Circuit — the canonical container

```python
c = Circuit(name: str, desc: str = "", ports: list[str] | None = None)
```

Root vs Sub-Circuit:
- **`ports=None` (default)** — root circuit.  Pass to `to_schematic()`,
  `to_spice_deck()`, `run_tran()`.
- **`ports=[...]`** — Sub-Circuit definition.  Cannot be a root; must
  be instantiated in a parent via `parent.add(sub.instance(ref, **port_map))`.
  Port names with bus syntax (`DATA[0..7]`) expand to scalars.
  Power-named ports (`GND`/`VCC`/`+12V`/`V+`/etc.) are rejected at
  construction — those flow implicitly across the hierarchy.

Building:

```python
c.add(part: Part)                           # validates ref uniqueness, pin coverage,
                                            #   registers nets; raises on conflict
c.add_many(*parts)
c.add_model_lib(path: str | Path)           # path to a .lib that gets
                                            #   .include'd in the deck
c.add_model(name, kind, **params)           # inline .model card
c.net(name, kind="signal", expect_external=False)
                                            # explicit net declaration
c.ic(NET=value, ...)                        # initial conditions
c.analysis(Analysis(...))                   # add an analysis
```

`Circuit.tran` property: shortcut for `c.analysis(Tran(...))` /
`c.run_tran()`.

Instance-ability (Sub-Circuit only):

```python
sub.is_subcircuit          # True iff ports= was passed
sub.instance(ref, *, repeat: int = 1, **port_map) -> SubcircuitInstance
                           # port_map keys may be scalar port names, base
                           #   names of bus ports, or full-range
                           #   ('DATA[0..7]') forms.  Values may be
                           #   scalar nets, bus ranges of matching width,
                           #   or individual bus members.
                           # repeat=N produces ONE SCH_SHEET with N peer
                           #   SCH_SHEET_INSTANCEs (see below).
```

Validation runs at instance-time: missing ports, extra port-map
keys, vector-width mismatches, scalar-to-bus and bus-to-scalar
binding errors all raise.

### Multi-channel: `repeat=N` with bus ports

A SubcircuitInstance with `repeat=N` lowers to a *single* SCH_SHEET on
the parent canvas plus N peer SCH_SHEET_INSTANCEs at depth 1 — the
"complex hierarchy" idiom in KiCad terms.  All instances share one
backing `<filename>.kicad_sch` (one screen, one library lookup, one
edit affects all).  Per-instance identity lives in the SCH_SHEET_PATH
slot KIID set on the parent.

**The canonical shape: bus-shaped ports + base-name body refs.**  The
sub-circuit declares each per-channel port as a bus of width N.  The
body wires components to the *base* (un-indexed) name — KliCAD's C++
matcher binds slot K's scalar subgraph for `GATE` to bit K of the
parent's `GBUS[0..N-1]` bus at hier-pin connection time.  You write
"the per-channel function" once.

```python
N = 8
ch = Circuit("channel", ports=[f"GATE[0..{N-1}]",
                               f"OUT[0..{N-1}]"])
ch.add(R("R1", "GATE", "OUT", value="10k"))   # body refs base names
# ↑ slot K sees GATE = GBUS[K], OUT = OBUS[K]

top.add(ch.instance("U_CH",
                    repeat=N,
                    GATE=f"GBUS[0..{N-1}]",   # bus on both sides,
                    OUT =f"OBUS[0..{N-1}]"))  # widths must match
```

**What's NOT supported.**  Scalar port + bus-net binding is rejected
by the validator — the following raises
`ValueError: port 'GATE' is scalar but bound to bus range 'GBUS[0..7]'`:

```python
ch = Circuit("channel", ports=["GATE", "OUT"])     # scalar ports
top.add(ch.instance("U_CH", repeat=8,
                    GATE="GBUS[0..7]", OUT="OBUS[0..7]"))   # ← rejected
```

If you want one rail shared across every slot, declare it scalar on
both sides — those are the shared globals (power, common controls):

```python
# Mixed: bus ports for per-slot signals, scalar ports for shared rails.
ch = Circuit("channel", ports=[f"GATE[0..{N-1}]",
                               f"OUT[0..{N-1}]",
                               "VCC", "GND"])
top.add(ch.instance("U_CH", repeat=N,
                    GATE=f"GBUS[0..{N-1}]",
                    OUT =f"OBUS[0..{N-1}]",
                    VCC ="+5V",       # scalar both sides → shared
                    GND ="GND"))      # implicit-global also works
```

In practice the cleanest pattern is to keep power as implicit globals
(GND, +5V, +VLOAD) and only port-list the per-channel signals — the
implicit-global rule flows them through every slot without appearing
in any port list (see "Implicit power nets" below).

What this emits:
- One `channel.kicad_sch` (template + screen)
- One SCH_SHEET symbol on the parent
- N SCH_SHEET_INSTANCE entries (slot 0..N-1) on the parent sheet
- N per-slot SPICE X-lines via `to_spice_deck` (one per slot,
  bus-bound port maps indexed by slot)
- N component-instance refs per body part in the netlist

Verify after emit:

```python
import klicad_native_hierarchy as h
sheets = h.list_sheets()
templates = {(s['file_name'], s['depth']) for s in sheets if s['depth'] > 0}
# len(templates) == 1; len(sheets at depth 1) == N
```

ERC sees N peer hier-paths, not one.  SPICE sees N X-lines, not one.
Footprint refs are per-slot (`U1:1`, `U1:2`, ..., `U1:N`).

When *not* to use multi-channel: when slots differ structurally (not
just per-slot net binding).  Then write N distinct
`ch.instance("CH<k>", ...)` calls.  The `repeat=` form assumes
slot-identical bodies — only the port nets vary.

The canonical test is `tests/test_multi_channel_netlist.py`
(`_build_multi_channel_circuit`); consult it if the validator pushes
back on a pattern you expect to work.

Validation (called automatically by emitters):

```python
c.validate_all(kicad=None)
```

## Part classes

Every Part has class-level `kind` + `pin_names` + `kicad_lib_id` +
`kicad_pin_map`, plus per-instance `ref`, `value`, `model`, `library`,
`footprint`, and a `connections: {spice_pin: net_name}` dict filled by
`__init__`.  `validate()` checks pin coverage.

| Class | Constructor | Default model | KiCad lib_id | SPICE element |
|---|---|---|---|---|
| `R(ref, n1, n2, value='1k', footprint='')` | passive | — | `Device:R` | `R` |
| `C(ref, plus, minus, value='1u', footprint='')` | passive | — | `Device:C` | `C` |
| `L(ref, n1, n2, value='1m', footprint='')` | passive | — | `Device:L` | `L` |
| `D(ref, *, a, k, model='1N4148')` | diode | `1N4148` | `Device:D` | `D` |
| `LED(ref, *, a, k, model='LED')` | LED | `LED` | `Device:LED` | `D` |
| `NPN(ref, *, c, b, e, model='2N3904')` | NPN BJT | `2N3904` | `Transistor_BJT:Q_NPN_EBC` | `Q` |
| `PNP(ref, *, c, b, e, model='2N3906')` | PNP BJT | `2N3906` | `Transistor_BJT:Q_PNP_EBC` | `Q` |
| `NMOS(ref, *, d, g, s, model='NMOS_S', bulk=None)` | N-MOSFET | `NMOS_S` (toy Level-1) | `Device:Q_NMOS` (pin numbers `D`/`G`/`S`) | `M` |
| `PMOS(ref, *, d, g, s, model='PMOS_S', bulk=None)` | P-MOSFET | `PMOS_S` (toy Level-1) | `Device:Q_PMOS` | `M` |
| `NJFET(ref, *, d, g, s, model)` | N-JFET | required | `Device:Q_NJFET_GDS` | `J` |
| `PJFET(ref, *, d, g, s, model)` | P-JFET | required | `Device:Q_PJFET_GDS` | `J` |
| `V(ref, plus, minus, dc=5)` | DC voltage source | — | `Simulation_SPICE:VDC` | `V` |
| `V(ref, plus, minus, ac='PULSE(...)')` | typed source (PULSE/SIN/PWL/...) | — | `Simulation_SPICE:VPULSE` etc. | `V` |
| `I(ref, plus, minus, dc=0.001)` | current source | — | analog | `I` |
| `XSubckt(ref, nodes: list, subckt: str, kicad_lib_id='', kicad_pin_map=None)` | external .SUBCKT instance | — | optional | `X` |
| `SubcircuitInstance` | returned by `Circuit.instance(...)`; not constructed directly | — | sheet | `X` |

Notes:
- MOSFETs default to the toy Level-1 models in `STANDARD_MODEL_LIB`
  (good for topology validation; under-predicts real device current
  ~40×).  For accurate sizing, supply a vendor `.SUBCKT` via
  `XSubckt` instead.
- `NMOS`/`PMOS` accept `bulk=` to override the default (bulk = source).
  Bulk net is GND-remapped at deck-emit time.
- `XSubckt` requires a positional node list; the `subckt` name must
  match a `.SUBCKT` defined in an `.include`'d `.lib`.

## Bus syntax

KiCad-native: `FOO[3]` = member, `FOO[0..7]` = range.  Both directions
accepted (`[7..0]` and `[0..7]` are equivalent semantically — they
canonicalize to ascending at storage time).

```python
# Ports:
sub = Circuit("shifter", ports=["CLK", "DATA[0..7]"])
# → expanded ports: CLK, DATA[0], DATA[1], ..., DATA[7]  (9 scalar SPICE ports)

# Connections (inside the body):
sub.add(R("R1", "DATA[3]", "GND", value="10k"))

# Instance port maps — full-bus or per-member:
top.add(sub.instance("U1",
    CLK="MY_CLK",
    DATA="MY_BUS[0..7]"))           # vector → vector
top.add(sub.instance("U2",
    CLK="MY_CLK",
    **{"DATA[0]": "X0", "DATA[1]": "X1", ...}))   # explicit per-member
```

Bus-port width must equal bus-net width.  Mixing scalar with bus
raises.

## Implicit power nets

Net names matching `GND|VCC|VDD|VSS|VBAT|VBUS|V+|V-|+\d.*|-\d.*|AGND|DGND|EARTH`
are implicit globals: visible across all sheet boundaries without
appearing in any port list.  `Circuit(ports=["VCC"])` is rejected for
this reason.

## Analyses

```python
Op()
Tran(step="1u", stop="10m", uic=False)
Ac(type, n_steps, fstart, fstop)
Dc(src, start, stop, step)
Noise(output, src, type, n_steps, fstart, fstop)
Control(commands="meas tran ...")
```

`c.analysis(...)` adds; `c.run_tran(step, stop)` is a convenience
that runs through PySpice's NgSpiceShared and returns
`{time, <net1>, <net2>, ...}` dicts of lists.

## SPICE deck

```python
deck: str = c.to_spice_deck(self_running=True, kicad=None)
```

- Title + desc → comments
- `.include` from `model_lib_paths` (aggregated across hierarchy)
- `.global` directive for implicit-power nets used in the hierarchy
- One `.SUBCKT name ports... / .ENDS name` block per reachable
  Sub-Circuit definition, in dependency order
- Root element lines
- `.ic` if `initial_conditions` set
- `.control / .endc` block when `self_running=True`
- `.end`

Refuses to run on a Sub-Circuit definition (`is_subcircuit=True`).

Deck-emit name validation: refs must start with a letter; net/model
names must not contain SPICE-meaningful chars (whitespace, `=`, `(`,
`)`, `;`).  Bus-syntax names like `DATA[3]` are accepted (the brackets
get a carve-out).

## Schematic emission

```python
result = to_schematic(c, sch_path, *,
                     kicad=None,
                     layout: str = "sugiyama",
                     route: bool = False,
                     mode: str = "diff")
```

Modes:
- `"diff"` (default) — preserve symbols whose ref still exists; per-
  pin diff inside SCH_SHEETs (port set add/remove); position +
  rotation + custom user fields + footprint survive on kept refs;
  re-issue value + Sim.* fields every pass.
- `"replace"` — clear all symbols + sheets + routing, place fresh.
- `"strict"` — refuse if anything is already on the sheet.

`layout` ∈ {`"sugiyama"`, `"clustered"`, `"spring"`}.
`route=True` enables A* wire routing for signal nets (power stays
label-driven).

For hierarchical circuits, child `.kicad_sch` files are emitted
alongside the root; one per reachable Sub-Circuit definition.

Returns a dict with `parts_placed`, `parts_kept`, `parts_removed`,
`sheets_placed`, `sheets_kept`, `sheets_removed`, `labels_placed`,
`wires_placed`, `mode`, `sch_path`, `models_lib_path`, `project_path`.

Offline-only variant (no IPC needed):

```python
write_project_shell(c, sch_path) -> dict
```

Writes `.kicad_pro`, `sym-lib-table`, `models.lib`, and a `.kicad_sch`
stub.  Useful for laying down the project tree before launching
KliCAD on it.

## klicad_native_* modules (via run_python)

The bindings live inside KliCAD's embedded interpreter.  Reach them
via `k.run_python("import klicad_native_X as X; X.fn(...)")`.

### `klicad_native_project_manager`
- `get_current_project()` — `{name, full_name, path, is_readonly, is_null}`
- `load_project(path)` — switch active project (subject to schematic-
  switch issues if a different project is already open)

### `klicad_native_gui`
- `show_frame(name)` — `'schematic'`, `'pcb'`, `'simulator'`, ...
- `list_dialog_buttons()` — enumerates buttons on the topmost modal
- `click_dialog_button(label)` — programmatic dismissal

### `klicad_native_schematic_state`  (the bulk of schematic mutation)
- `add_symbol(lib_id, ref, x_mm, y_mm)` → `{ok, kiid}`
- `add_wire(x1, y1, x2, y2)`, `add_junction(x, y)`,
  `add_label(x, y, name, kind='local'|'global'|'hierarchical')`
- `add_sheet(name, filename, x_mm, y_mm, w_mm, h_mm)` → `{ok, kiid}`
  (screen-shared if another sheet points at the same filename)
- `add_sheet_pin(sheet_kiid, name, side, x_mm, y_mm, shape='bidi')`
- `set_symbol_value(kiid, value)`, `set_symbol_field(kiid, name, value)`,
  `set_symbol_rotation(kiid, degrees)`
- `get_symbol_pin_position(kiid, pin_number)` → `{ok, x_mm, y_mm}`
- `get_symbol_bbox(kiid)` → `{ok, x_mm, y_mm, w_mm, h_mm}`
- `open_schematic(path)`, `save_schematic()`
- `get_sheet_count()`, `get_items_summary()`
- `list_symbols(sheet_path="")` — rows with kiid, ref, lib_id,
  sheet_path, x_mm, y_mm, orientation, mirror_x/y, value,
  footprint, fields (dict of all Sim.* + custom user fields)
- `list_labels(sheet_path="")` — rows with kiid, name, kind, x_mm, y_mm
- `delete_by_kiid(kiid)` — works on symbols, wires, labels, junctions,
  sheets, sheet pins
- `clear_routing(sheet_path="", keep_hier_labels=False)` —
  bulk-deletes wires/labels/junctions/#PWR symbols; keep_hier_labels
  spares SCH_HIER_LABEL items

### `klicad_native_hierarchy`
- `list_sheets()` — every SCH_SHEET_PATH in the hierarchy
- `get_current_sheet()`, `set_current_sheet(path)`,
  `push_sheet(child_uuid)`, `pop_sheet()`
- `walk_hierarchy(max_depth=-1)`
- `list_sheet_pins(sheet_uuid)` — pins on a SCH_SHEET item
- `count_instances(symbol_uuid)`, `update_page_numbers()`

### `klicad_native_simulator`
- `load_netlist(deck_str)`
- `command(cmd_str)` — pass-through to ngspice (`run`, `op`,
  `tran 1u 1m`, `setplot tran1`, ...)
- `list_plots()`, `get_vector(name, plot=None)` →
  `{data: list[float], length, name}`

### `klicad_native_sch_actions`
- `list_actions()`
- `run_action(action_name)` — e.g.,
  `'eeschema.InspectionTool.runERC'`,
  `'eeschema.EditorControl.saveAs'`

### `klicad_native_annotation`
- `annotate(scope='all'|'current_sheet', order='by_x_then_y', ...)`
- `clear_annotation(scope)`

### `klicad_native_sim_advanced`
- `parse_subckt_lib(path)` — list subcircuits + pin counts in a `.lib`

### Others (less commonly used in the design loop)
- `klicad_native_drc`, `klicad_native_erc`,
  `klicad_native_export_3d`, `klicad_native_export_drill`,
  `klicad_native_export_gerbers`, `klicad_native_export_sch_bom`,
  `klicad_native_export_sch_netlist`, `klicad_native_export_sch_plot`,
  `klicad_native_jobset`, `klicad_native_library_tables`,
  `klicad_native_settings`, `klicad_native_diff`,
  `klicad_native_local_history`, `klicad_native_io_discovery`,
  `klicad_native_design_blocks`, `klicad_native_symbol_editor`,
  `klicad_native_sym_upgrade`, `klicad_native_sch_upgrade`,
  `klicad_native_pcb_upgrade`, `klicad_native_pcb_actions`,
  `klicad_native_fp_export_svg`, `klicad_native_gerber_export_png`,
  `klicad_native_gerber_diff`, `klicad_native_gerber_info`,
  `klicad_native_bitmap2component`, `klicad_native_kiway_events`

## Gotchas / known limitations

- ERC dispatch (`run_action('eeschema.InspectionTool.runERC')`)
  returns only `{ok: True}` — there's no programmatic marker
  enumeration yet.  Visual inspection of the GUI's ERC panel is the
  current path.
- `to_schematic()` requires a live KliCAD session; `KliCAD()` will
  raise `ConnectionError` if no socket.
- Switching projects mid-session (`pm.load_project`) is fraught;
  prefer launching KliCAD on the target project.
- The bundled Level-1 MOSFET models (`NMOS_S`, `PMOS_S`) under-
  predict real device current by ~40× at typical gate drives.  Use
  vendor `.SUBCKT` for sizing-relevant simulation.
- A modal dialog in the KliCAD GUI blocks all IPC.  `list_dialog_buttons`
  + `click_dialog_button` is the dismissal path.

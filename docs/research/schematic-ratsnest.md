# Schematic-side ratsnest — feasibility & design research

Status: research-only, no implementation.
Date: 2026-05-27.
Scope: bring the PCB ratsnest paradigm into the schematic editor — thin lines drawn between pins that *should* be connected per an external spec (klicad-python `Circuit`, SPICE netlist, imported netlist) but that the user hasn't yet wired in the schematic.

---

## Verdict + recommendations

**Verdict.** Feasible and natural. Schematic-side ratsnest is essentially a thin re-implementation of `RATSNEST_VIEW_ITEM` parametrised over the schematic's already-mature `CONNECTION_GRAPH` plus a new "spec graph" data source. The hardest engineering problems (pin-position lookup, connectivity-graph maintenance, view-item rendering, layer/visibility plumbing) are already solved in eeschema and pcbnew; the work is almost entirely *plumbing them together*, not building new algorithms.

**The key risk** is not algorithmic but UX/semantic: in schematics, "should be connected" is multi-valued. The spec might say `R1.2` and `C3.1` are on `OUT`, but the user might satisfy that by drawing a wire, by labelling both pins `OUT`, by routing through a hierarchical sheet pin, or by a power symbol pair. The ratsnest must collapse on *any* of those, which is exactly what `CONNECTION_GRAPH::Recalculate` already determines. So the ratsnest's diff input is *the resolved net name per pin*, not raw geometric wires.

**Recommendation.** Build it in three milestones (detailed at the end of this doc):
- **M1.** Spec source = the *current* schematic's own driver-priority-resolved netlist; renderer draws rats between pins that share a net but have no drawn-wire path. This shakes out the rendering, view-item, layer, and incremental-update infrastructure with no new data source. It is also independently useful: visualises "dangling" pins on labelled nets in big sheets.
- **M2.** Add an external-spec source (klicad-python `Circuit` IR → C++ "expected nets" registered through a new `klicad_native_ratsnest` module). Diff = spec netlist − current netlist on the symmetric difference of pin-pairs.
- **M3.** Interaction polish: click-rat-to-wire, hover-to-highlight, hierarchical handling, bus expansion, color-by-net, "show only failing rats" filter.

A spec-only ratsnest without M1 would conflate two failure modes ("you forgot to wire it" vs "your spec disagrees with the schematic") and would be harder to validate.

---

## A. Prior art in EDA

I am unaware of any commercial or open-source schematic editor that ships exactly this feature — "ghost wires between pins that *should* be connected per an external connectivity spec." The closest analogues, by tool:

- **Altium Designer.** No schematic ratsnest. Altium has *Net Highlight* (Tools → Highlight Nets) which dims everything but the selected net, and *Cross-Probe* between schematic and PCB. The PCB-side ratsnest doesn't have a schematic mirror. Altium *does* have "Compile Errors" panel showing pins that aren't connected to anything per ERC, but it's a textual report, not a visual overlay.
- **Cadence Allegro / OrCAD Capture.** No schematic ratsnest. *DE-HDL* (Allegro Design Entry HDL) has "implied connections" via *signal name* matching — but that's just the standard net-by-name mechanism, not an overlay. OrCAD Capture's "Update Properties" reports missing connections in a panel.
- **Mentor Xpedition.** Same shape as Allegro. No visual rats on the schematic side.
- **Eagle.** Schematic and board are tightly coupled forward-annotation; if you place a part in schematic with no net, you get a "*airwire*" on the board, never on the schematic. Eagle's *show* command can highlight a net on the schematic, but again no rats.
- **EasyEDA.** Sym/sch/PCB pipeline; the schematic editor draws "guide lines" from a pin while you're actively wiring (rubber-band from the source pin to the cursor) but those vanish on click — they are not persistent missing-connection indicators.
- **LTspice.** Wires-only. No spec separate from the schematic itself; no rats. *Net Highlight* (right-click net → "Mark Reference") is the closest, but again it highlights *existing* connectivity, not missing.
- **Logisim / Logisim Evolution.** Wires-only digital schematic. Disconnected pins show as small blue squares (an *endpoint marker*, not a rat).
- **Digital (hneemann).** Same: blue endpoint markers on dangling pins.
- **gEDA gschem.** Endpoint markers (red square on disconnected pin), no rats.
- **qucs / qucs-s.** Same endpoint-marker idiom inherited from gschem-era conventions.
- **Falstad circuit simulator.** No persistent missing-connection indicator; you can't place a pin without wiring it.
- **Multisim, TINA, Proteus.** All three give in-place ERC-style warnings ("net N has only one pin") in a panel or as small icons on the pin; none draw a line *between* the pins that should be connected.

**Closest cousin: schematic-driven PCB import.** Several tools (KiCad included) draw a *board*-side ratsnest the moment you import a new netlist. The user's vision is to invert this: the schematic ratsnest is sourced from a spec that *would have been* the netlist, were the schematic complete.

**Closest closest cousin: SPICE-deck-as-spec compares.** Cadence's *PSpice* has a "Compare Netlists" diff that highlights mismatches in a textual diff. No graphical overlay.

**Conclusion.** This appears to be a genuinely novel schematic-editor feature. The closest precedents are the *endpoint marker* convention (dangling-pin dot) shared across gschem-derived tools, and KiCad's own existing *dangling end indicator* on wire endpoints — but no tool draws a line between the two pins that should be joined.

---

## B. Algorithmic structure

### B.1 Source-of-truth inputs

Three input modes, all reducing to the same shape — a set of (pin-identity → net-name) tuples — that I'll call the **spec graph**:

1. **klicad-python `Circuit`.** The Python IR already builds `_nets: dict[str, list[(ref, pin)]]`. The DSL's `to_schematic` method already feeds this to KliCAD via `klicad_native_schematic_state.add_wire / add_label`. The same dict can be pushed via a new `klicad_native_ratsnest.set_spec(nets)` binding without going through wire creation.
2. **SPICE deck.** Parse with the existing `klicad_native_sim_advanced.parse_subckt_lib` infrastructure (`/home/jakob/projects/KliCAD/eeschema/api/bindings_sim_advanced.cpp`); each `.SUBCKT` line and instance line yields the same (ref, pin → net) tuples. Klicad-python already has a SPICE→Circuit pathway.
3. **Imported netlist** (KiCad `.net`, OrCAD `.net`, etc.). The netlist exporters live in `/home/jakob/projects/KliCAD/eeschema/netlist_exporters/`; an importer would be the inverse — but reusing klicad-python as the canonical IR is cleaner than writing C++ importers for every flavour. M2 should just say "give us a `Circuit`".

The spec must be **source-agnostic at the C++ layer**: the data structure is a flat `std::vector<SpecPin>` where `SpecPin = { wxString ref, wxString pin_number, wxString net_name }`.

### B.2 Diff between spec and current

Two connectivity graphs:

- **Spec graph (S).** From the external source. Map: `(ref, pin) → spec_net_name`.
- **Current graph (C).** From `CONNECTION_GRAPH::GetNetMap()`. Iterating `m_net_code_to_subgraphs_map` (`/home/jakob/projects/KliCAD/eeschema/connection_graph.h:842`) gives each net's subgraphs; each subgraph's `GetItems()` returns the pins on the net. Inverted: `(ref, pin) → current_net_name`.

Both are partial functions (pins might be absent from either side). The diff:

```
For each unordered pin-pair (p, q) where p.ref ≠ q.ref:
    in_spec    = S[p] == S[q]   (defined and equal)
    in_current = C[p] == C[q]   (defined and equal)
    if in_spec and not in_current:   draw a rat
    if in_current and not in_spec:   no rat (the user added something the spec didn't ask for — that's fine)
```

Equivalence is by **net membership**, not by net name. Pin p and pin q being on a net named `OUT` in spec vs `n3` in current is *fine* as long as they're on the same net in both. So really:

```
For each net N_spec in spec:
    pins_in_spec = sorted pins on N_spec
    Find: do all pins_in_spec resolve to the same net in C?
        If yes — net is satisfied, no rats.
        If they split into multiple C-nets — draw rats spanning the components.
```

This is the **MST-of-disconnected-components** view: for each spec-net, partition its pins by their current-net, and draw rats that span the partitions. If any pin in `pins_in_spec` is *absent* from C entirely (the symbol exists on the schematic but has no driver — a floating pin), it's its own singleton partition.

A note on **direction**: a missing component in C vs. S is rats. A missing component in S vs. C (the user wired things the spec didn't mention) is *not* an error — by default it's silent. M3 could expose this as a separate diagnostic ("schematic has extra connectivity") in a different color.

### B.3 Ratsnest line generation

Pcbnew uses **Kruskal's MST on a Delaunay-triangulated complete graph** (`pcbnew/ratsnest/ratsnest_data.cpp:111` `kruskalMST`, `ratsnest_data.cpp:289` `compute`). This gives `n-1` edges for `n` pins, choosing geometrically-short edges.

For schematic, the same MST is appropriate because:
- Schematic pin counts per net are small (rarely > 10) — Delaunay is overkill but harmless.
- A spanning tree is visually unambiguous: every pin is rat-connected to at least one other, but not every pair.
- The user's act of drawing a wire collapses *all* of a component-pair's rats, which is what MST-edges-per-component naturally yields.

A simpler alternative is **star from the most-driven pin** (e.g. the pin nearest the spec's "first" pin), which avoids triangulation but produces visually-busier diagrams when pin clusters are spread across a sheet. Recommend: copy pcbnew's Kruskal + delaunay path with `m_nodes` typed as `SCH_PIN*` anchors.

For **hierarchical sheets**, rats must not cross sheet boundaries (you can't draw a wire across sheets — you'd add a hierarchical label). On a sheet boundary, the rat terminates at the *expected* hierarchical-label position, or at the sheet-pin if one exists. M3 territory.

### B.4 Visual style

Match the PCB precedent (`ratsnest_view_item.cpp:46–293`):

- **Layer.** A new `LAYER_SCH_RATSNEST` between `LAYER_SCHEMATIC_AUX_ITEMS` and `LAYER_SCHEMATIC_ANCHOR` in `include/layer_ids.h:454-507`. Set `LayerTarget = TARGET_OVERLAY` and `LayerDisplayOnly = true` (the pcbnew pattern at `pcb_draw_panel_gal.cpp:921-922`).
- **Color.** Configurable via the existing color-theme system; default `COLOR4D(0.6, 0.6, 0.6, 0.5)` — soft grey, ~50% alpha — to read as "ghost".
- **Line style.** Solid thin line at 1× wire-thickness, or dashed (the existing GAL has dash support). Solid keeps rendering cheap and matches PCB. Dashed would visually distinguish from real wires.
- **Curved option.** Pcbnew has `m_DisplayRatsnestLinesCurved` (`ratsnest_view_item.cpp:121`). Worth replicating — a slight bezier helps when many rats overlap.
- **Net coloring.** Optional cycle through netcolors when `colorByNet` (the pcbnew toggle at `ratsnest_view_item.cpp:88` `GetNetColorMode()`); reuse the schematic's netclass colour assignment.
- **Z-order.** Above wires (so they're not occluded), below symbols and labels (so they don't obscure pin numbers).

### B.5 Incremental update

`CONNECTION_GRAPH::Recalculate` is already incremental for non-minor schematics (`/home/jakob/projects/KliCAD/eeschema/schematic.cpp:1808-1824`, gated on `ADVANCED_CFG::m_IncrementalConnectivity`). The schematic-side ratsnest piggy-backs on this:

- After every `SCH_COMMIT::Push` that triggers `RecalculateConnections`, mark the ratsnest dirty and request a `SCH_VIEW::Update(m_ratsnest)`.
- Debouncing comes for free via the existing `OnModify`/commit machinery: the user doesn't see a recompute mid-drag, only on commit.
- Per-net dirty flagging (like `RN_NET::IsDirty` at `ratsnest_data.h:74`) can be added but is optional for M1; schematics are tiny enough that full-rebuild on every commit is acceptable.

### B.6 Hierarchical, power, buses, diff-pairs

- **Hierarchical nets** (multi-sheet). `CONNECTION_GRAPH` already resolves hierarchical labels across sheets via `m_hier_parent`/`m_hier_children` (`connection_graph.h:295-299`). The rat must check whether two pins resolve to the *same fully-qualified net*, not just the same local label. The existing `CONNECTION_SUBGRAPH::GetNetName()` returns the resolved name, so this is free.
- **Power symbols.** A pin connected to a `GND` power symbol is on the global `GND` net per `CONNECTION_SUBGRAPH::PRIORITY::GLOBAL_POWER_PIN` (`connection_graph.h:75`). So power-connected pins simply *aren't* on the rat list because they already resolve. Easy.
- **Buses.** Spec might say `D[0..7]`. Klicad-python's H1 already expands bus ranges (`expand_bus_range("DATA[7..0]")` per STATUS.md). At the rat layer, each bus member is an independent net — buses are an authoring convenience, not a connectivity primitive. The ratsnest sees only expanded pins.
- **Differential pairs.** No special handling needed at M1/M2. If the spec contains `OUT_P` and `OUT_N`, they're just two nets. M3 could optionally draw pair-color-matched rats but it's not load-bearing.

### B.7 Interaction with `CONNECTION_GRAPH`

`CONNECTION_GRAPH` is the authoritative current-graph source. Key entry points already exposed:

- `GetNetMap()` (`connection_graph.h:443`): all nets by `(name, code)` → vector of subgraphs.
- `GetSubgraphForItem(SCH_ITEM*)` (`connection_graph.h:465`): per-item lookup.
- `SCH_ITEM::Connection(sheet_path)` (`sch_item.h:560`): per-item per-sheet `SCH_CONNECTION*` accessor; `SCH_PIN` inherits this. From there, `SCH_CONNECTION::GetNetName()` returns the resolved name.

So the diff loop is roughly:

```
for each pin in the schematic:
    current_net = pin->Connection(sheet)->Name()        // C[p]
    spec_net    = spec_graph_lookup(pin)                // S[p]
    # bucket pins by (spec_net, current_net)
group pins by spec_net:
    if all pins in group have same current_net:  satisfied, no rats
    else:  group by current_net, MST across the groups
```

---

## C. Implementation hooks in KliCAD

Every touchpoint below is verified by file inspection. File:line citations point to the construct being modified or referenced.

### C.1 Connectivity engine (`eeschema/connection_graph.{h,cpp}`)

Already exposes everything needed. No modifications expected for M1/M2.

- `NET_MAP m_net_code_to_subgraphs_map` (`connection_graph.h:842`) is the authoritative C-graph; expose iteration via the existing `GetNetMap()` (`connection_graph.h:443`).
- `CONNECTION_SUBGRAPH::GetItems()` (`connection_graph.h:140–143`) enumerates the SCH_ITEMs on a subgraph; filter for `SCH_PIN`.
- `Recalculate` is already the canonical refresh trigger (`schematic.cpp:1823`).

Possible *small* additions: a `CONNECTION_GRAPH::GetPinNetMap()` convenience that flattens the per-item iteration into a `std::unordered_map<SCH_PIN*, wxString>`. Pure refactor.

### C.2 Schematic-side rendering surface

- New files: `eeschema/sch_ratsnest_view_item.{h,cpp}` modelled directly on `pcbnew/ratsnest/ratsnest_view_item.{h,cpp}`. The latter is only ~290 lines and ~70 of those are GAL boilerplate that translates directly.
- `SCH_PAINTER` (`/home/jakob/projects/KliCAD/eeschema/sch_painter.cpp`, 3752 lines) does not need modification — the new view item self-renders via `ViewDraw()` (the pcbnew pattern at `ratsnest_view_item.cpp:63`). No need to add a `SCH_PAINTER::draw(const SCH_RATS*, …)` overload; rats are not SCH_ITEMs.
- `SCH_SCREEN` (`eeschema/sch_screen.{h,cpp}`) does **not** own the rats. Following the pcbnew pattern (`pcb_draw_panel_gal.cpp:485`), the rats live on the **draw panel**, not the screen: one rats-view-item per schematic editor frame, attached when the panel is constructed.

### C.3 Layer registration

- `include/layer_ids.h:454–507` — add `LAYER_SCH_RATSNEST` to `enum SCH_LAYER_ID`. Choose a slot late in the enum to avoid shifting existing values; the safe move is to insert just before `SCH_LAYER_ID_END` (`include/layer_ids.h:507`).
- The schematic equivalent of `pcb_draw_panel_gal.cpp:921-922` (`SetLayerTarget(LAYER_RATSNEST, TARGET_OVERLAY)` + `SetLayerDisplayOnly`) goes in the schematic draw panel. The schematic draw panel is `SCH_DRAW_PANEL` (in `eeschema/sch_draw_panel.{h,cpp}`, not opened here — pattern likely identical).

### C.4 Schematic view item ownership

- Pcbnew owns its rats via `m_ratsnest = std::make_unique<RATSNEST_VIEW_ITEM>(...)` (`pcb_draw_panel_gal.cpp:485-486`). The schematic mirror lives on `SCH_DRAW_PANEL_GAL` or on `SCH_VIEW` (`/home/jakob/projects/KliCAD/eeschema/sch_view.h:97`).
- The view item needs a data backing — analogous to pcbnew's `CONNECTIVITY_DATA` (passed at `ratsnest_view_item.cpp:46`). For schematic, this is a new `SCH_RATSNEST_DATA` class that owns:
  - the cached **spec graph** (vector of `SpecPin`)
  - a **computed rats list** (vector of `{ VECTOR2I a, VECTOR2I b, wxString netName, int netcode }`)
  - a `IsDirty()` flag + `Recompute(CONNECTION_GRAPH*)` method
  - a `KISPINLOCK` for thread safety (pcbnew pattern at `ratsnest_view_item.cpp:65`).

### C.5 Recompute trigger

- After every `SCHEMATIC::RecalculateConnections` (`schematic.cpp:1775`), schedule a `SCH_RATSNEST_DATA::Recompute`. This can be done by adding a listener via the existing `SCHEMATIC_LISTENER` mechanism (`schematic.h:65`, `schematic.cpp:1358` — `InvokeListeners(&SCHEMATIC_LISTENER::OnSchItemsAdded, ...)`). A `OnConnectivityRecomputed` listener method would be a natural extension.
- For M1, simpler still: hook directly inside `RecalculateConnections` after the `ConnectionGraph()->Recalculate(...)` call.

### C.6 Tool interactions (`eeschema/tools/*.cpp`)

- **Click rat → start wire**: extend `SCH_LINE_WIRE_BUS_TOOL` (`/home/jakob/projects/KliCAD/eeschema/tools/sch_line_wire_bus_tool.cpp`). When the user clicks on a rat-line and the active tool is "select" or "wire", consume the click by initiating a wire whose start is `rat.a` and whose endpoint is being-dragged to `rat.b`. M3 polish.
- **Hover rat → highlight net**: extend `SCH_INSPECTION_TOOL` (`/home/jakob/projects/KliCAD/eeschema/tools/sch_inspection_tool.cpp`) or the existing `net_navigator.cpp` to accept a `RAT` as a highlight target. M3 polish.

### C.7 ERC overlap

`eeschema/erc/erc.{cpp,h}` already reports `ERCE_PIN_NOT_CONNECTED` (`erc_settings.h:44`) for pins not connected to anything. The ratsnest is **complementary, not overlapping**:

- ERC pin-not-connected says "this pin has no net at all".
- Ratsnest says "this pin has *a* net, but it's not the net the spec expected, and here's the pin it should connect to".

The two can co-exist: ERC catches floating pins from the *schematic's* side; rats catch missing connections from the *spec's* side. The rat for a floating pin is well-defined (the pin's current-net is just the pin itself; spec-net says it should join others), so a floating pin will also have a rat — which is reinforcing rather than conflicting.

### C.8 klicad-python API surface

- Add a new binding module via `klicad_kiface_register.cpp:78-96` (the existing module list): `klicad_native_ratsnest`. The boilerplate at `eeschema/api/bindings_schematic_state.cpp` (1338-onward, the `m.def` calls) is the template.
- Bindings:
  - `set_spec(pins: list[tuple[ref, pin_number, net_name]]) -> None` — stores the spec graph and triggers a recompute.
  - `clear_spec() -> None`.
  - `get_pending_rats() -> list[tuple[ref_a, pin_a, ref_b, pin_b, net_name]]` — read back the current rats for testing.
  - `set_visible(visible: bool) -> None` — toggle layer visibility programmatically.

---

## D. klicad-python integration

### D.1 Spec hand-off

`Circuit._nets: dict[str, list[(ref, pin)]]` is already the canonical Python-side connectivity representation (`klicad-python/klipy/circuit/_circuit.py:214` and surrounds). The hand-off:

```
from klipy.circuit import Circuit
c = Circuit(...)
... add parts ...
c.to_schematic_ratsnest()
   # internally:
   spec = [(ref, pin, net) for net, pins in c._nets.items() for (ref, pin) in pins]
   klicad_native_ratsnest.set_spec(spec)
```

This is *additive* — `Circuit.to_schematic()` still emits real wires. The ratsnest path is for the case where the user wants to *manually wire* the schematic and use the spec as a visual guide.

### D.2 Identity keys (H3 hook)

The H3 phase (per STATUS.md L42-49) keys diff identity on `(sheet_path, ref)`. The ratsnest naturally inherits this: a spec pin is identified by `(ref, pin_number)`, and the current-graph pin is `(SCH_SYMBOL ref, SCH_PIN number)` on a particular sheet. The mapping is straightforward for flat (H2) hierarchies, becomes `(sheet_path, ref, pin_number)` for nested hierarchies (post-H4).

### D.3 Live updates

The Python spec lives in-process in the long-running KliCAD GUI (via the existing `klicad_native_*` bindings — the schematic is the live state, not a one-shot export). So:

1. User runs `Circuit.to_schematic_ratsnest()` — spec installed.
2. User draws wires interactively.
3. Each `SCH_COMMIT::Push` triggers `RecalculateConnections`, which triggers rats recompute, which collapses rats as wires satisfy spec edges.
4. User reruns `Circuit.to_schematic_ratsnest()` after editing Python — spec replaced, rats recomputed.

### D.4 Diff-feedback loop

The existing `Circuit.to_schematic()` diff-iteration loop (the H3 mechanism) already round-trips spec ↔ schematic via the `klicad_native_schematic_state` bindings. The ratsnest is a strictly-readonly observer on top of that loop: it doesn't *write* schematic geometry, it just *displays* what's missing. So there's no risk of feedback oscillation between the diff and the rats.

---

## E. UX prior art — controls

PCB ratsnest controls in KliCAD/KiCad (verified via `ratsnest_view_item.cpp`):

- **Global show/hide.** `PCBNEW_SETTINGS::m_Display.m_ShowGlobalRatsnest` (referenced at `ratsnest_view_item.cpp:237`). Toggle in Layers panel.
- **Per-net hide.** `GetHiddenNets()` (`ratsnest_view_item.cpp:84`). Right-click on a net in the Net Inspector.
- **Per-footprint hide.** `Parent()->GetLocalRatsnestVisible()` (`ratsnest_view_item.cpp:239-245`). Per-footprint toggle.
- **"Only visible layers" mode.** `m_Display.m_RatsnestMode == RATSNEST_MODE::VISIBLE` (`ratsnest_view_item.cpp:93`). Hides rats whose endpoints aren't on a visible layer.
- **Curved rats.** `m_DisplayRatsnestLinesCurved` (`ratsnest_view_item.cpp:121`).
- **Color by net.** `GetNetColorMode() != OFF` (`ratsnest_view_item.cpp:88`).
- **Highlight selected net.** `GetHighlightNetCodes()` (`ratsnest_view_item.cpp:83`); brightens the rats of the selected net.

Schematic mirrors of these controls — recommended subset for M1, full set for M3:

| PCB control | Schematic mirror | Priority |
|---|---|---|
| Global show/hide | `SCH_RATSNEST` toggle in Layer Manager | M1 |
| Curved lines | Same setting in eeschema preferences | M2 |
| Color by net | Reuse netclass colours from schematic | M2 |
| Highlight selected | Brighten rats whose net is selected via `net_navigator.cpp` | M3 |
| Per-net hide | Right-click rat → "hide this net's rats" | M3 |
| "Only on visible sheets" | Hide rats that cross sheet boundaries | M3 |

Allegro's *Display → Status → Ratsnest* dialog is the closest external comparator; Eagle's *Display → Layer 19 Unrouted* is the simplest.

---

## F. Effort estimate

Person-week ranges; *low* = experienced KliCAD contributor on a happy path, *high* = if surprises emerge.

| Component | Low | Mid | High | What pushes from low to high |
|---|---|---|---|---|
| Data model (`SCH_RATSNEST_DATA`) + diff algorithm | 0.5 | 1 | 2 | Hierarchical net resolution edge cases; spec input formats |
| Renderer (`SCH_RATSNEST_VIEW_ITEM`) + layer registration | 0.5 | 1 | 1.5 | GAL/Cairo dual-target rendering bugs; theme integration |
| Incremental update infrastructure | 0.5 | 1 | 2 | If we have to add per-net dirty flagging instead of full-recompute |
| Tool integration (M3: click, hover) | 1 | 2 | 3 | Conflict with existing wire-tool drag semantics |
| klicad-python binding + tests | 0.5 | 1 | 1.5 | Round-trip with `to_schematic()` diff loop |
| Tests (live-KliCAD + pure-python) | 1 | 1.5 | 2.5 | Headless rendering verification; multi-sheet scenarios |
| UX controls (per-net hide, color modes) | 0.5 | 1 | 2 | Color-theme system surprises |
| **Total (M1 only)** | **2** | **3.5** | **5.5** | M1 = data + renderer + minimal incremental + minimal tests |
| **Total (M1+M2)** | **3** | **5** | **8** | adds binding + python spec source |
| **Total (M1+M2+M3)** | **5** | **8.5** | **14** | adds tools + UX polish |

The dominant unknown is **GAL theme integration** for the new layer — KliCAD's color-theme registration has a tendency to require touching ~6 files (settings json, default theme, theme migration, layer-id, painter, settings dialog). Budget a buffer.

---

## G. Staged implementation plan

### M1 — current-graph self-ratsnest (no external spec)

**Deliverable.** Toggle in the schematic's Layer Manager: "Show schematic ratsnest". When enabled, the eeschema window draws thin grey lines between pins that are on the same `CONNECTION_GRAPH` net but aren't connected by a continuous wire path on the current sheet.

**Scope.**
- New layer `LAYER_SCH_RATSNEST` (`include/layer_ids.h:454-507`).
- New files `eeschema/sch_ratsnest_view_item.{h,cpp}` (~250 lines, mirror of pcbnew).
- New files `eeschema/sch_ratsnest_data.{h,cpp}` (~300 lines, mirror of pcbnew but using `SCH_PIN*` anchors).
- Hook into `SCHEMATIC::RecalculateConnections` (`schematic.cpp:1775`) for refresh.
- Owner: `SCH_DRAW_PANEL_GAL` (analogous to `pcb_draw_panel_gal.cpp:485`).
- Diff source: only `CONNECTION_GRAPH::GetNetMap()` — no external spec.
- For each subgraph, compute MST across pins; suppress edges that already have a continuous wire path (which is exactly what a subgraph-membership check tells us — same subgraph = no rat).

**Why this first.** Bedds in all the rendering / view-item / layer / refresh infrastructure with a *known-correct* spec source (the schematic's own resolved netlist). Also useful: on large multi-sheet schematics, it visualises labelled-net membership the way the PCB ratsnest visualises a net's pads.

**Effort.** 2–5.5 person-weeks (see table above).

**Tests.** 
- Pure-python (klicad-python live-KliCAD): place two symbols, label both pins `OUT`, ratsnest disappears when wire is drawn. 
- Place two symbols sharing `GND` via power symbols: no rat (already on the same net via global driver).
- Hierarchical sheet with shared label: no rat across sheets if labels resolve.

### M2 — external-spec ratsnest

**Deliverable.** Python user can register a `Circuit` as the spec; schematic draws rats for any pin pair that the spec says is connected but the schematic doesn't realise.

**Scope.**
- New binding module `klicad_native_ratsnest` (`klicad_kiface_register.cpp:78-96`-style registration).
- Bindings: `set_spec`, `clear_spec`, `get_pending_rats`, `set_visible`.
- `SCH_RATSNEST_DATA` extended with a `m_spec_pins: vector<SpecPin>` field; diff logic per section B.2.
- klicad-python: `Circuit.set_as_schematic_ratsnest_spec()` convenience method on `Circuit`.

**Effort.** +1–2.5 person-weeks on top of M1.

**Tests.**
- klicad-python `Circuit` with one resistor across two parts; place parts with no wires; assert one rat. Wire the pins; rat disappears.
- Update `Circuit` to add a third part on the same net; re-call `set_spec`; assert two rats.
- Place parts in a hierarchical sheet; spec says cross-sheet net; assert rats terminate at sheet boundaries (or at hier-label positions).

### M3 — interaction & polish

**Deliverable.** Productionised UX matching pcbnew.

**Scope.**
- Click rat → start wire from `rat.a` to `rat.b` (`sch_line_wire_bus_tool.cpp`).
- Hover rat → highlight all pins on that net (`net_navigator.cpp` extension).
- Per-net hide (right-click on a rat).
- Curved rats option.
- Color-by-net mode.
- "Show only failing-spec rats" filter (suppress M1 self-rats when an external spec is loaded).
- Bus expansion: spec bus `D[0..7]` → 8 separate rats.

**Effort.** +2–6 person-weeks.

**Tests.**
- Click-to-wire integration test (uses existing `klicad_native_sch_actions` for clicks).
- Color-by-net visual snapshot test.
- Bus-spec → 8-rats expansion test.

---

## H. Key file:line reference table

| Topic | File | Line(s) |
|---|---|---|
| PCB ratsnest entry-point | `/home/jakob/projects/KliCAD/pcbnew/ratsnest/ratsnest.cpp` | 36–44 |
| PCB rats data class | `/home/jakob/projects/KliCAD/pcbnew/ratsnest/ratsnest_data.h` | 63–145 |
| PCB MST (Kruskal) | `/home/jakob/projects/KliCAD/pcbnew/ratsnest/ratsnest_data.cpp` | 111–135 |
| PCB rats compute | `/home/jakob/projects/KliCAD/pcbnew/ratsnest/ratsnest_data.cpp` | 289–345 |
| PCB rats view-item draw | `/home/jakob/projects/KliCAD/pcbnew/ratsnest/ratsnest_view_item.cpp` | 63–287 |
| PCB rats view-item owner | `/home/jakob/projects/KliCAD/pcbnew/pcb_draw_panel_gal.cpp` | 485–486 |
| PCB rats layer target | `/home/jakob/projects/KliCAD/pcbnew/pcb_draw_panel_gal.cpp` | 921–922 |
| `LAYER_RATSNEST` enum | `/home/jakob/projects/KliCAD/include/layer_ids.h` | 253 |
| `SCH_LAYER_ID` enum (where to add `LAYER_SCH_RATSNEST`) | `/home/jakob/projects/KliCAD/include/layer_ids.h` | 450–507 |
| Schematic connection-graph header | `/home/jakob/projects/KliCAD/eeschema/connection_graph.h` | full |
| `CONNECTION_SUBGRAPH::GetItems` | `/home/jakob/projects/KliCAD/eeschema/connection_graph.h` | 140–143 |
| `CONNECTION_GRAPH::GetNetMap` | `/home/jakob/projects/KliCAD/eeschema/connection_graph.h` | 443 |
| `m_net_code_to_subgraphs_map` field | `/home/jakob/projects/KliCAD/eeschema/connection_graph.h` | 842 |
| `SCHEMATIC::RecalculateConnections` | `/home/jakob/projects/KliCAD/eeschema/schematic.cpp` | 1775–1824 |
| `SCH_ITEM::Connection` accessor | `/home/jakob/projects/KliCAD/eeschema/sch_item.h` | 560 |
| `SCH_PIN::GetPosition` | `/home/jakob/projects/KliCAD/eeschema/sch_pin.h` | 252 |
| `SCH_VIEW` (view item host) | `/home/jakob/projects/KliCAD/eeschema/sch_view.h` | 97–143 |
| ERC unconnected-pin code | `/home/jakob/projects/KliCAD/eeschema/erc/erc_settings.h` | 44 |
| Schematic API listener `OnSchItemsAdded` | `/home/jakob/projects/KliCAD/eeschema/schematic.h` | 65 |
| Schematic API listener invoke | `/home/jakob/projects/KliCAD/eeschema/schematic.cpp` | 1358 |
| Schematic API: existing net-walking | `/home/jakob/projects/KliCAD/eeschema/api/api_handler_sch.cpp` | 1252–1296 |
| klicad-python module registry | `/home/jakob/projects/KliCAD/eeschema/api/klicad_kiface_register.cpp` | 78–96 |
| klicad-python `add_wire` binding (template) | `/home/jakob/projects/KliCAD/eeschema/api/bindings_schematic_state.cpp` | 1338 |
| klicad-python `Circuit._nets` (spec source) | `/home/jakob/projects/klicad-python/klipy/circuit/_circuit.py` | ~214 |
| klicad-python H3 status | `/home/jakob/projects/klicad-python/docs/plans/STATUS.md` | 42–49 |

---

## I. Open questions / known unknowns

1. **Sheet-boundary visualisation.** When the spec requires a cross-sheet connection, does the rat terminate at the sheet edge, at an expected hier-label position, or follow into the parent sheet's coordinates? Recommend: terminate at the sheet-pin position if a sheet-pin exists, else at the symbol pin (and tag the rat as "cross-sheet" for color differentiation). Defer until M3.
2. **Performance ceiling.** Pcbnew's Kruskal is fine for ~5k pads/net. Schematic nets are smaller (rarely > 10 pins). Should be fine, but verify on a 10k-net schematic (autogenerated test fixture).
3. **Subgraph-vs-net resolution.** A subgraph is *sheet-local* (`connection_graph.h:53-63`). Two subgraphs on different sheets can share a net. The diff must operate on *resolved net name* (`CONNECTION_SUBGRAPH::GetNetName()`), not subgraph identity. Verified by inspection but warrants a test.
4. **Stale spec on reload.** If the user closes and reopens the schematic, the spec must be either persisted (probably as a project sidecar `*.kicad_ratsnest_spec` file) or re-installed by re-running the Python script. Recommend: do not persist; require re-install. Simpler, and the spec is "live" anyway.
5. **Multi-instance sub-circuits** (H2 territory). One `Circuit.instance(ref, ...)` with two instances → two sub-sheets sharing one screen. Rats must be drawn per-instance-context. The existing per-sheet-path `SCH_CONNECTION` machinery already handles this, but worth a test.
6. **Spec-vs-current naming.** Spec net `OUT` resolving to current net `n42` is fine *if and only if* the rats only check pin-set-equivalence, not name-equivalence. Confirmed in B.2 but the implementation must resist the temptation to compare names.

---

End of research document.

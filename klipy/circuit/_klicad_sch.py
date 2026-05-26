"""Render a Circuit to a KliCAD .kicad_sch file via the live KliCAD bindings.

Strategy:
  1. Lay down the project + bootstrap an empty .kicad_sch on disk.
  2. Write the project's sym-lib-table (so the KliCAD library adapter can
     resolve part lib_ids without needing a global table edit).
  3. Write a project-local models.lib gathering every distinct model the
     parts reference.
  4. Connect to KliCAD over IPC, load the project, open the schematic.
  5. For each Part:
       a. add_symbol(lib_id, ref, x, y)
       b. set_symbol_value(kiid, value)
       c. For each pin: get_symbol_pin_position + add_label naming the net.
       d. For parts that need an external model: set_symbol_field("Sim.Library", path).
  6. Save schematic.

After this completes:
  * The .kicad_sch represents the same circuit as the Circuit object.
  * The GUI's Play button works because the schematic is fully SPICE-
    annotated (Sim.Device + Sim.Pins inherited from the lib; Sim.Library
    set per-instance to the models.lib).

Layout is a simple horizontal row of parts at the top with power symbols
above and ground below — the netlist generator only cares about pin
positions and net-label coincidence, not aesthetics.  Real auto-layout
is a Phase C concern (graph drawing + force-directed placement).

Note: the schematic-switch crash (HANDOFF.md crash #2) means you can't
call this against a KliCAD session that already has a different project
open.  Either launch KliCAD with the target project on the command line,
or restart between projects.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._circuit import Circuit
    from ._part import Part


# Grid spacing (mm).  100mil = 2.54mm is the KliCAD schematic grid unit.
_GRID = 2.54
# Stride between adjacent symbols on the row.
_PART_DX = 8 * _GRID            # 20.32 mm
# Vertical drop from row to ground rail.
_PART_DY = 6 * _GRID            # 15.24 mm

# Where the row starts, in mm — far enough from origin that labels fit.
_ROW_X0 = 12 * _GRID
_ROW_Y  = 12 * _GRID

# Power/ground symbol positions: above/below the row.
_PWR_Y  = _ROW_Y - 4 * _GRID
_GND_Y  = _ROW_Y + _PART_DY + 4 * _GRID


# ──────────────────────────────────────────────────────────────────────────
# Project bootstrap (files on disk)
# ──────────────────────────────────────────────────────────────────────────

def _bootstrap_project_files(c: "Circuit", sch_path: Path) -> tuple[Path, Path, Path]:
    """Write supporting files (.kicad_pro, sym-lib-table, models.lib).

    Critically does NOT touch the .kicad_sch — that would clobber KliCAD's
    in-memory state if it has the schematic open, popping an
    UnsavedChangesDialog that blocks the IPC.  The schematic gets
    authored via the bindings and saved with ss.save_schematic().

    For a fresh project where no .kicad_sch exists yet, write a minimal
    stub so KliCAD's load_project + open_schematic has something to read.

    Returns (pro_path, sym_lib_table_path, models_lib_path).
    """
    proj_dir = sch_path.parent
    proj_dir.mkdir(parents=True, exist_ok=True)
    stem = sch_path.stem
    pro_path = proj_dir / f"{stem}.kicad_pro"
    sym_lib_table_path = proj_dir / "sym-lib-table"
    models_lib_path = proj_dir / "models.lib"

    if not pro_path.exists():
        pro_path.write_text(
            '{\n  "meta": {"filename": "' + pro_path.name + '", "version": 3}\n}\n'
        )

    # Only write a stub if .kicad_sch doesn't already exist — never
    # overwrite a live schematic file out from under KliCAD.
    if not sch_path.exists():
        sch_path.write_text(
            f"(kicad_sch (version 20250114) (generator \"klicad-circuit\")\n"
            f" (generator_version \"10.99\") (uuid \"00000000-0000-0000-0000-000000000001\")\n"
            f" (paper \"A4\") (lib_symbols)\n"
            f" (sheet_instances (path \"/\" (page \"1\"))))\n"
        )

    # Project sym-lib-table: by default, DON'T write one.  The global
    # symbol library table (configured at KliCAD install / Preferences ->
    # Manage Symbol Libraries) already provides Device, Transistor_BJT,
    # Simulation_SPICE, power, etc.  Writing a project-scoped one with
    # hardcoded ${KICAD_SYMBOL_DIR} paths invariably gets the env-var
    # name wrong (KliCAD 10 uses ${KICAD10_SYMBOL_DIR}) and / or assumes a
    # single-file lib layout when upstream now ships exploded
    # `.kicad_symdir/` directories.  Cleaner to leave the project table
    # empty and let KliCAD resolve via the global table.
    #
    # Future: when we need a project-local merged lib (e.g. for CI without
    # a global table), write it via a separate Circuit.add_local_lib() API
    # so the user opts in.
    if not sym_lib_table_path.exists():
        sym_lib_table_path.write_text("(sym_lib_table)\n")

    # Gather every distinct model the parts reference + concatenate the
    # corresponding .model lines from the configured model_lib_paths.
    # models.lib is project-local and KliCAD doesn't keep it open, so
    # rewriting it here is safe.
    needed_models: set[str] = set()
    for p in c.parts:
        if p.model:
            needed_models.add(p.model)

    deck_lines: list[str] = [f"* Auto-generated from {c.name} — models needed: " +
                              ", ".join(sorted(needed_models))]
    for src_path in c.model_lib_paths:
        if Path(src_path).exists():
            deck_lines.append(f"* --- from {src_path} ---")
            deck_lines.append(Path(src_path).read_text().rstrip())
    models_lib_path.write_text("\n".join(deck_lines) + "\n")

    return pro_path, sym_lib_table_path, models_lib_path


# ──────────────────────────────────────────────────────────────────────────
# Placement layout — phase B is a simple horizontal row
# ──────────────────────────────────────────────────────────────────────────

def _layout_positions(c: "Circuit", engine: str = "sugiyama"
                      ) -> dict[str, tuple[float, float]]:
    """Assign (x_mm, y_mm) to each part ref.

    engine:
      "sugiyama"  (default) — pure signal-flow layering.
      "clustered"           — Sugiyama with partition() block-id as a
                              secondary ordering key.
      "spring"              — force-directed (Fruchterman-Reingold with
                              cluster gravity); blocks settle apart by
                              ~50 mm with intra-block springs holding
                              same-block parts tight.
    """
    from ._partition import partition

    if engine in ("clustered", "spring"):
        blocks = partition(c)
        block_of: dict[str, int] = {}
        for i, b in enumerate(blocks):
            for ref in b.parts:
                block_of[ref] = i
    else:
        block_of = None

    if engine == "spring":
        from ._layout import spring_positions
        return spring_positions(c, cluster_key=block_of)

    from ._layout import sugiyama_positions
    return sugiyama_positions(c, cluster_key=block_of)


# Position power/ground stub symbols off the row.  We need ONE +5V symbol
# per power net (typically just "VCC") and ONE GND per ground net.
def _power_positions(c: "Circuit") -> list[tuple[str, str, float, float]]:
    """Return (net_name, sym_lib_id, x, y) for power/ground symbols.

    One symbol per power/ground net.  Placed at x left of the row.
    """
    out: list[tuple[str, str, float, float]] = []
    pwr_x = _ROW_X0 - 4 * _GRID
    gnd_x = _ROW_X0 - 4 * _GRID
    pwr_count = 0
    gnd_count = 0
    for name, meta in c.nets.items():
        if meta.kind == "power":
            # Pick KliCAD power symbol by common name; default to +5V.
            lib_id = _power_lib_id_for(name)
            out.append((name, lib_id, pwr_x, _PWR_Y - pwr_count * 2 * _GRID))
            pwr_count += 1
        elif meta.kind == "ground":
            out.append((name, "power:GND", gnd_x, _GND_Y + gnd_count * 2 * _GRID))
            gnd_count += 1
    return out


def _power_lib_id_for(name: str) -> str:
    """Map a net name to a power-symbol lib_id."""
    table = {
        "VCC":   "power:VCC",
        "VDD":   "power:VDD",
        "+5V":   "power:+5V",
        "+3V3":  "power:+3V3",
        "+3.3V": "power:+3V3",
        "+12V":  "power:+12V",
        "VBAT":  "power:VBAT",
        "VBUS":  "power:VBUS",
    }
    return table.get(name.upper(), f"power:+5V")


# ──────────────────────────────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────────────────────────────

def write_project_shell(c: "Circuit", sch_path) -> dict:
    """Write the offline-safe project shell for a Circuit.

    Generates the .kicad_pro, sym-lib-table, and models.lib files
    alongside `sch_path`, plus a minimal .kicad_sch stub if absent.
    Does NOT require KliCAD running; never raises on missing IPC.

    This is the always-succeeds half of `to_schematic()`.  The
    place-parts half (`place_parts()`) requires KliCAD.  Callers who
    want the full flow should keep using `to_schematic()`; this split
    is for tools that want to produce the project tree offline and
    place parts later, or want to inspect the shell separately from
    the placement.

    Returns {ok, sch_path, project_path, models_lib_path,
              sym_lib_table_path}.  `ok` is always True; failure here
    would be filesystem-level and propagates as the usual OSError.
    """
    sch_path = Path(sch_path).resolve()
    pro_path, sym_lib_table_path, models_lib_path = _bootstrap_project_files(c, sch_path)
    return {
        "ok": True,
        "sch_path": str(sch_path),
        "project_path": str(pro_path),
        "models_lib_path": str(models_lib_path),
        "sym_lib_table_path": str(sym_lib_table_path),
    }


def to_schematic(
    c: "Circuit",
    sch_path,
    *,
    kicad=None,
    layout: str = "sugiyama",
    route: bool = False,
    mode: str = "diff",
) -> dict:
    """Generate a complete .kicad_sch (+ project files + models.lib) for circuit c.

    Returns {ok, parts_placed, labels_placed, wires_placed, sch_path,
             models_lib_path, project_path, mode, kept, added, removed}.

    Internally splits into two phases:
      1. `write_project_shell()` — offline, always-succeeds, writes
         .kicad_pro / sym-lib-table / models.lib / .kicad_sch stub.
      2. KliCAD-side part placement — requires the IPC API.

    If you want the offline phase only (e.g. KliCAD isn't running and
    you'll place parts later), call `write_project_shell()` directly.

    mode controls idempotency on re-runs against the same schematic:
      * "diff" (default) — preserve symbols whose ref still exists in
        the Circuit (their positions survive); delete refs no longer
        present; place fresh refs.  Wires/labels/junctions are wiped
        and re-emitted each call (cheap routing, expensive layout).
      * "replace" — clear every symbol + wire + label, then place from
        scratch.  Use when you want layout to be fully recomputed.
      * "strict" — refuse to run on a non-empty schematic.  The pre-
        cleanup behavior; kept for tests that want a hard fail-fast.
    """
    if mode not in ("diff", "replace", "strict"):
        raise ValueError(
            f"to_schematic: mode must be 'diff' | 'replace' | 'strict', "
            f"got {mode!r}"
        )
    from klipy import KliCAD
    from klipy.errors import ConnectionError as KipyConnectionError

    # Phase 1 — offline shell.  Always-succeeds; ensures the project tree
    # exists on disk before the IPC step that may fail.
    shell = write_project_shell(c, sch_path)
    sch_path = Path(shell["sch_path"])
    pro_path = Path(shell["project_path"])
    sym_lib_table_path = Path(shell["sym_lib_table_path"])
    models_lib_path = Path(shell["models_lib_path"])

    # Phase 2 — IPC-bound placement.
    if kicad is None:
        try:
            kicad = KliCAD()
        except KipyConnectionError as e:
            raise RuntimeError(
                "to_schematic(): cannot reach KliCAD's IPC API.\n"
                "  • Start KliCAD (the GUI must be running).\n"
                "  • In Preferences → Plugins, ensure the IPC API is enabled.\n"
                "  • If you're trying to do a headless build, that's not "
                "supported — to_schematic() needs a live KliCAD process.\n"
                "  • The offline shell files (.kicad_pro, sym-lib-table, "
                "models.lib, .kicad_sch stub) WERE written alongside "
                f"{sch_path}.  You can call write_project_shell() directly "
                "to skip the placement step entirely.\n"
                f"underlying error: {e}"
            ) from e

    # The KliCAD instance must already be loaded on this project — load
    # otherwise (subject to schematic-switch crash if a different project
    # is already open).
    pre = kicad.run_python(
        "import klicad_native_project_manager as pm; pm.get_current_project()"
    )
    if pre.ok and pro_path.name in pre.result_repr:
        # Already loaded.
        pass
    else:
        r = kicad.run_python(
            f"import klicad_native_project_manager as pm; "
            f"pm.load_project({str(pro_path)!r})"
        )
        if not r.ok:
            raise RuntimeError(
                f"failed to load_project({pro_path}): {r.exception_traceback}"
            )

    # Open the schematic.
    r = kicad.run_python(
        "import klicad_native_gui as g; g.show_frame('schematic')\n"
        f"import klicad_native_schematic_state as ss\n"
        f"ss.open_schematic({str(sch_path)!r})"
    )
    if not r.ok:
        raise RuntimeError(f"open_schematic failed: {r.exception_traceback}")

    # Enumerate existing symbols on the schematic.  list_symbols returns
    # one dict per placed SCH_SYMBOL with kiid + reference + lib_id +
    # position; we key by ref because that's the user-facing identity
    # that survives across re-runs of the build script.
    r = kicad.run_python(
        "import klicad_native_schematic_state as ss; ss.list_symbols()"
    )
    if not r.ok:
        raise RuntimeError(
            f"to_schematic: list_symbols failed: {r.exception_traceback}"
        )
    import ast
    existing = ast.literal_eval(r.result_repr)
    # Filter out power-flag symbols (KiCad convention: ref prefixed with '#').
    # _place_power_symbols emits them every pass; they're not in c.parts and
    # would otherwise read as "needs removal" on every diff iteration.
    existing = [row for row in existing if not row["ref"].startswith("#")]
    existing_by_ref = {row["ref"]: row for row in existing}

    # Validate: a circuit with duplicate refs has ambiguous identity under
    # diff/apply (which symbol does "R1" point at when the build script
    # declares it twice?).  Catch this early with a clear error.
    seen = set()
    dups = []
    for p in c.parts:
        if p.ref in seen:
            dups.append(p.ref)
        seen.add(p.ref)
    if dups:
        raise ValueError(
            f"to_schematic: c.parts contains duplicate refs {sorted(set(dups))}.  "
            f"Reference designators must be unique."
        )

    target_refs = {p.ref for p in c.parts}
    target_by_ref = {p.ref: p for p in c.parts}

    # Spice-idempotence requires lib_id agreement on kept refs.  If a ref
    # exists with a different lib_id than the current Part wants, "keeping"
    # the old symbol would mean the SPICE netlist uses the old pin map.
    # Demote these from keep -> remove+add so the new lib_id takes effect.
    lib_id_mismatch = {
        ref for ref in (set(existing_by_ref) & target_refs)
        if existing_by_ref[ref]["lib_id"] != target_by_ref[ref].kicad_lib_id
    }

    keep_refs    = (set(existing_by_ref) & target_refs) - lib_id_mismatch
    remove_refs  = (set(existing_by_ref) - target_refs) | lib_id_mismatch
    add_refs     = (target_refs - set(existing_by_ref)) | lib_id_mismatch

    if mode == "strict" and existing:
        raise RuntimeError(
            f"to_schematic(mode='strict'): refusing to run on a schematic "
            f"with {len(existing)} existing symbols.  Either pass "
            f"mode='diff' / mode='replace' or clear the schematic first."
        )

    if mode == "replace":
        # Delete every existing symbol — clear_routing handles wires/labels.
        for row in existing:
            r = kicad.run_python(
                f"import klicad_native_schematic_state as ss\n"
                f"ss.delete_by_kiid({row['kiid']!r})"
            )
            if not r.ok:
                raise RuntimeError(
                    f"delete_by_kiid({row['kiid']}) failed: "
                    f"{r.exception_traceback}"
                )
        keep_refs, remove_refs = set(), set()
        add_refs = target_refs

    elif mode == "diff":
        # Delete refs no longer in the Circuit (plus lib_id-mismatch demotes).
        for ref in remove_refs:
            row = existing_by_ref[ref]
            r = kicad.run_python(
                f"import klicad_native_schematic_state as ss\n"
                f"ss.delete_by_kiid({row['kiid']!r})"
            )
            if not r.ok:
                raise RuntimeError(
                    f"delete_by_kiid({row['kiid']}) for ref={ref!r} "
                    f"failed: {r.exception_traceback}"
                )

    # Always wipe routing — wires/labels/junctions get re-emitted below.
    # Cheap relative to layout, and the alternative (diffing wire sets)
    # is its own session.
    r = kicad.run_python(
        "import klicad_native_schematic_state as ss; ss.clear_routing()"
    )
    if not r.ok:
        raise RuntimeError(
            f"clear_routing failed: {r.exception_traceback}"
        )

    # Place only the new refs; reuse existing kiids for kept refs.
    skip = {ref: existing_by_ref[ref]["kiid"] for ref in keep_refs}
    placed = _place_parts(c, kicad, models_lib_path, layout=layout,
                          skip_refs=skip)
    if route:
        # Phase F router: explicit wires instead of label-coincidence.
        # Power/ground stay label-driven through _place_power_symbols
        # because routing them would dominate the sheet visually.
        from ._route import route_signal_nets
        labeled = _label_power_pins_only(c, kicad, placed)
        wires = route_signal_nets(c, kicad, placed)
    else:
        labeled = _label_pins(c, kicad, placed)
        wires = 0
    _place_power_symbols(c, kicad)

    # Save
    r = kicad.run_python(
        "import klicad_native_schematic_state as ss; ss.save_schematic()"
    )
    if not r.ok:
        raise RuntimeError(f"save_schematic failed: {r.exception_traceback}")

    return {
        "ok":               True,
        "parts_placed":     len(placed) - len(keep_refs),  # newly added
        "parts_kept":       len(keep_refs),
        "parts_removed":    len(remove_refs),
        "labels_placed":    labeled,
        "wires_placed":     wires,
        "mode":             mode,
        "sch_path":         str(sch_path),
        "models_lib_path":  str(models_lib_path),
        "project_path":     str(pro_path),
    }


def _place_parts(c: "Circuit", kicad, models_lib_path: Path,
                 layout: str = "sugiyama",
                 skip_refs: dict[str, str] | None = None) -> dict[str, str]:
    """Place every Part as a SCH_SYMBOL.  Returns ref -> kiid mapping.

    skip_refs maps ref -> existing kiid; those refs are NOT re-placed
    (their on-screen positions and field settings survive untouched).
    The returned mapping still includes them so the labeler / router
    can address every Part.
    """
    skip_refs = skip_refs or {}
    positions = _layout_positions(c, engine=layout)
    placed: dict[str, str] = dict(skip_refs)
    # Part kinds whose model name + library path go into Sim.Name / Sim.Library
    # so KliCAD's SPICE netlist exporter can find the .MODEL card or .SUBCKT.
    # XSubckts are included whenever they carry a per-instance kicad_lib_id.
    needs_lib = {"NPN", "PNP", "NMOS", "PMOS", "NJFET", "PJFET", "D", "LED", "X"}

    # XSubckt parts without an explicit kicad_lib_id can't be placed —
    # the lookup table has nothing to map them to.  Fail early and clearly.
    bare_subckts = [p.ref for p in c.parts
                    if p.kind == "X" and not p.kicad_lib_id]
    if bare_subckts:
        raise NotImplementedError(
            f"to_schematic(): XSubckt parts {bare_subckts} have no KliCAD "
            f"symbol binding.  Pass kicad_lib_id= and kicad_pin_map= when "
            f"constructing each XSubckt, e.g.:\n"
            f"    XSubckt('Q1', ['DRAIN', 'GATE', 'SOURCE'], subckt='DO5T10BA',\n"
            f"            kicad_lib_id='Device:Q_NMOS_GDS',\n"
            f"            kicad_pin_map={{'1':'2','2':'1','3':'3'}})\n"
            f"or use to_spice_deck() if you only need the SPICE side."
        )

    import ast
    for p in c.parts:
        # Spice-idempotence: every Part's fields are reapplied every pass,
        # whether or not the symbol was placed this pass or in a previous
        # one.  Position, rotation, custom user fields, footprint — those
        # all live OUTSIDE this update block and survive because we never
        # touch them when the ref is in skip_refs.
        is_typed_source = (
            p.kind in ("V", "I")
            and getattr(p, "sim_params", None) is not None
        )
        if p.ref in skip_refs:
            kiid_literal = repr(skip_refs[p.ref])
            snippet = (
                f"import klicad_native_schematic_state as ss\n"
                f"kiid = {kiid_literal}\n"
            )
        else:
            x, y = positions[p.ref]
            snippet = (
                f"import klicad_native_schematic_state as ss\n"
                f"sym = ss.add_symbol({p.kicad_lib_id!r}, {p.ref!r}, {x}, {y})\n"
                f"if not sym.get('ok'): raise RuntimeError(f'add_symbol failed for {p.ref}: ' + str(sym))\n"
                f"kiid = sym['kiid']\n"
            )

        # For non-DC V/I sources, the Value field is a label only — the
        # actual SPICE stimulus lives in Sim.Params.  Everything else gets
        # value = p.value (passives) or p.model (actives with no Value).
        if not is_typed_source:
            snippet += (
                f"value = {(p.value or p.model)!r}\n"
                f"if value: ss.set_symbol_value(kiid, value)\n"
            )
        else:
            snippet += (
                f"ss.set_symbol_field(kiid, 'Sim.Params', {p.sim_params!r})\n"
            )
        if p.kind in needs_lib:
            snippet += (
                f"ss.set_symbol_field(kiid, 'Sim.Library', {str(models_lib_path)!r})\n"
            )
            # KliCAD's SPICE pipeline (eeschema/sim/) looks for Sim.Name
            # as the model identifier — NOT Sim.Model (which was an earlier
            # KliCAD 7-era convention).  Without Sim.Name the netlist
            # generator emits '<ref>.unknown' as the model name and the
            # deck fails to simulate.  Set Sim.Name to the model name.
            #
            # XSubckt: model identity lives in p.subckt (the .SUBCKT name);
            # additionally tag Sim.Type='SUBCKT' so KliCAD treats the symbol
            # as a subcircuit call (X-element) rather than trying to
            # interpret it as a built-in primitive.
            if p.kind == "X":
                # KliCAD's Sim.Pins format is "kicad_pin=spice_terminal_idx"
                # — tells the netlist exporter which KliCAD symbol pin
                # corresponds to which positional .SUBCKT terminal, so the
                # generated X-line argument order matches the .SUBCKT
                # definition.  We have kicad_pin_map as spice_pos → kicad_pin,
                # so invert it for the Sim.Pins field.
                sim_pins = ",".join(
                    f"{kn}={sp}" for sp, kn in sorted(
                        p.kicad_pin_map.items(),
                        key=lambda kv: int(kv[1]) if kv[1].isdigit() else 0,
                    )
                )
                snippet += (
                    f"ss.set_symbol_field(kiid, 'Sim.Name', {p.subckt!r})\n"
                    f"ss.set_symbol_field(kiid, 'Sim.Type', 'SUBCKT')\n"
                    f"ss.set_symbol_field(kiid, 'Sim.Pins', {sim_pins!r})\n"
                )
            elif p.model:
                snippet += (
                    f"ss.set_symbol_field(kiid, 'Sim.Name', {p.model!r})\n"
                )
        snippet += "kiid"

        r = kicad.run_python(snippet)
        if not r.ok:
            raise RuntimeError(f"failed placing {p.ref}: {r.exception_traceback}")
        placed[p.ref] = ast.literal_eval(r.result_repr)

    return placed


def _label_power_pins_only(c: "Circuit", kicad, placed: dict[str, str]) -> int:
    """Like _label_pins but only emits labels on power/ground pins.

    Used when route=True: signal nets get explicit wires from _route;
    power/ground stay label-driven (routing them would dominate the
    sheet — they touch nearly every part).
    """
    powerlike = {name for name, meta in c.nets.items()
                 if meta.kind in ("power", "ground")}
    n = 0
    for p in c.parts:
        kiid = placed[p.ref]
        for spice_pin, net_name in p.connections.items():
            if net_name not in powerlike:
                continue
            kicad_pin_num = p.kicad_pin_map[spice_pin]
            r = kicad.run_python(
                f"import klicad_native_schematic_state as ss\n"
                f"pos = ss.get_symbol_pin_position({kiid!r}, {kicad_pin_num!r})\n"
                f"if not pos.get('ok'): raise RuntimeError('pin pos failed: ' + str(pos))\n"
                f"ss.add_label(pos['x_mm'], pos['y_mm'], {net_name!r})\n"
                f"True"
            )
            if r.ok:
                n += 1
    return n


def _label_pins(c: "Circuit", kicad, placed: dict[str, str]) -> int:
    """Drop a text label at every pin coordinate.  Returns count placed."""
    n = 0
    for p in c.parts:
        kiid = placed[p.ref]
        for spice_pin, net_name in p.connections.items():
            kicad_pin_num = p.kicad_pin_map[spice_pin]
            r = kicad.run_python(
                f"import klicad_native_schematic_state as ss\n"
                f"pos = ss.get_symbol_pin_position({kiid!r}, {kicad_pin_num!r})\n"
                f"if not pos.get('ok'): raise RuntimeError(f'pin pos failed: ' + str(pos))\n"
                f"ss.add_label(pos['x_mm'], pos['y_mm'], {net_name!r})\n"
                f"True"
            )
            if not r.ok:
                raise RuntimeError(
                    f"failed labelling {p.ref}.{spice_pin} as {net_name}: "
                    f"{r.exception_traceback}"
                )
            n += 1
    return n


def _place_power_symbols(c: "Circuit", kicad) -> None:
    """Place +5V/GND/etc. symbols and label them with the net name."""
    for net_name, lib_id, x, y in _power_positions(c):
        # Power symbols don't need values/fields set, just a label at the pin
        # so KliCAD sees them as connected to net_name.
        r = kicad.run_python(
            f"import klicad_native_schematic_state as ss\n"
            f"sym = ss.add_symbol({lib_id!r}, '#PWR_{net_name}', {x}, {y})\n"
            f"if not sym.get('ok'): raise RuntimeError('power sym failed: ' + str(sym))\n"
            f"kiid = sym['kiid']\n"
            f"# pin '1' on power symbols is the output pin\n"
            f"pos = ss.get_symbol_pin_position(kiid, '1')\n"
            f"if pos.get('ok'):\n"
            f"    ss.add_label(pos['x_mm'], pos['y_mm'], {net_name!r})\n"
            f"True"
        )
        if not r.ok:
            # Power symbols are best-effort — schematic can still be valid
            # without them if every component uses an explicit label.
            # Log and continue.
            print(f"warning: power symbol {net_name} placement failed: "
                  f"{r.exception_traceback.splitlines()[-1] if r.exception_traceback else '?'}")

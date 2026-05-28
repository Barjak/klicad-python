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

def _sanitize_subckt_filename(name: str) -> str:
    """Sub-Circuit name → safe .kicad_sch base.

    Raises ValueError on empty results or if the user name was all-bad
    characters (the regex collapses to '').
    """
    import re
    out = re.sub(r"[^a-zA-Z0-9_]", "_", name).strip("_")
    if not out:
        raise ValueError(
            f"Sub-Circuit name {name!r} sanitizes to empty — choose a name "
            f"containing at least one alphanumeric character"
        )
    return out


def _child_sch_filename(sc_def: "Circuit") -> str:
    """Stable .kicad_sch filename for a Sub-Circuit definition."""
    return _sanitize_subckt_filename(sc_def.name) + ".kicad_sch"


def _check_sanitization_collisions(sub_defs: list["Circuit"]) -> None:
    """Two distinct Sub-Circuits whose names sanitize to the same filename
    would race for the same child .kicad_sch on disk.  Catch early."""
    seen: dict[str, str] = {}    # sanitized -> original name
    for sc in sub_defs:
        sanitized = _sanitize_subckt_filename(sc.name)
        if sanitized in seen and seen[sanitized] != sc.name:
            raise ValueError(
                f"Sub-Circuit name collision after filename sanitization: "
                f"{seen[sanitized]!r} and {sc.name!r} both produce "
                f"{sanitized}.kicad_sch.  Rename one of them."
            )
        seen[sanitized] = sc.name


def _bootstrap_child_sheet_stubs(sub_defs: list["Circuit"],
                                  proj_dir: Path) -> dict[int, Path]:
    """Write a .kicad_sch stub for each Sub-Circuit definition (if missing).

    Each stub gets a fresh random UUID so the project can hold multiple
    children without uuid collisions.  Returns {id(sc_def): Path} so the
    caller can look up each definition's file.

    Never overwrites an existing child file — that would clobber live
    in-memory KliCAD state if the schematic is loaded.  KliCAD's
    save_schematic() writes its in-memory SCH_SCREEN content back when
    we're done emitting.
    """
    import uuid as _uuid

    _check_sanitization_collisions(sub_defs)
    out: dict[int, Path] = {}
    for sc in sub_defs:
        path = proj_dir / _child_sch_filename(sc)
        if not path.exists():
            path.write_text(
                f"(kicad_sch (version 20250114) (generator \"klicad-circuit\")\n"
                f" (generator_version \"10.99\") (uuid \"{_uuid.uuid4()}\")\n"
                f" (paper \"A4\") (lib_symbols))\n"
            )
        out[id(sc)] = path
    return out


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
    if c.is_subcircuit:
        raise ValueError(
            f"to_schematic: {c.name!r} is a Sub-Circuit definition "
            f"(ports={c._port_decl}).  Pass the root Circuit; Sub-Circuits "
            f"are emitted automatically as child .kicad_sch files."
        )

    from klipy import KliCAD
    from klipy.errors import ConnectionError as KipyConnectionError
    from ._spice import _reachable_subcircuit_defs

    # Phase 1 — offline shell.  Always-succeeds; ensures the project tree
    # + every child .kicad_sch stub exists on disk before the IPC step.
    shell = write_project_shell(c, sch_path)
    sch_path = Path(shell["sch_path"])
    pro_path = Path(shell["project_path"])
    sym_lib_table_path = Path(shell["sym_lib_table_path"])
    models_lib_path = Path(shell["models_lib_path"])
    proj_dir = sch_path.parent
    sub_defs = _reachable_subcircuit_defs(c)
    sub_to_filename = _bootstrap_child_sheet_stubs(sub_defs, proj_dir)

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

    pre = kicad.run_python(
        "import klicad_native_project_manager as pm; pm.get_current_project()"
    )
    if pre.ok and pro_path.name in pre.result_repr:
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

    r = kicad.run_python(
        "import klicad_native_gui as g; g.show_frame('schematic')\n"
        f"import klicad_native_schematic_state as ss\n"
        f"ss.open_schematic({str(sch_path)!r})"
    )
    if not r.ok:
        raise RuntimeError(f"open_schematic failed: {r.exception_traceback}")

    # Make sure we start emission on the root sheet.  pop_sheet repeatedly
    # until depth==0; defensive against stale GUI navigation state.
    _navigate_to_root(kicad)

    # Validate target uniqueness in c.parts before anything mutates state.
    _check_dup_refs(c)

    # Emit the root sheet.
    root_result = _emit_one_sheet(c, kicad, models_lib_path, mode=mode,
                                   layout=layout, route=route,
                                   sub_to_filename=sub_to_filename,
                                   is_root=True)

    # Emit each Sub-Circuit body into its child sheet.  Each definition
    # gets visited once; multi-instance Sub-Circuits share a SCH_SCREEN
    # via add_sheet's screen-sharing logic (KiCad complex-hierarchy).
    sub_results: list[dict] = []
    for sc_def in sub_defs:
        # Find any SCH_SHEET on root pointing at this Sub-Circuit's file,
        # push into it.  (Screen-sharing means there's exactly one
        # SCH_SCREEN per file regardless of instance count.)
        child_filename = sub_to_filename[id(sc_def)].name
        sheet_kiid = _find_root_sheet_with_filename(kicad, child_filename)
        if sheet_kiid is None:
            raise RuntimeError(
                f"to_schematic: no root SCH_SHEET found for child "
                f"{child_filename!r}; expected one was placed in the root "
                f"emit pass.  This is an internal sequencing bug."
            )
        r = kicad.run_python(
            f"import klicad_native_hierarchy as h; "
            f"h.push_sheet({sheet_kiid!r})"
        )
        if not r.ok:
            raise RuntimeError(
                f"push_sheet({sheet_kiid}) failed: {r.exception_traceback}"
            )
        try:
            sub_result = _emit_one_sheet(sc_def, kicad, models_lib_path,
                                          mode=mode, layout=layout,
                                          route=route,
                                          sub_to_filename=sub_to_filename,
                                          is_root=False)
            sub_results.append(sub_result)
        finally:
            kicad.run_python(
                "import klicad_native_hierarchy as h; h.pop_sheet()"
            )

    # Annotation: only run when hierarchy is present.  KiCad's per-path
    # symbol-instance bookkeeping (so U1/Q1 vs U2/Q1 are distinct in the
    # netlist) depends on annotate running across the hierarchy.  For
    # flat schematics, the user-supplied ref designators are already
    # complete — running annotate(aResetAnnotation=true) would
    # renumber them and break the contract that code owns ref names.
    if sub_defs:
        r = kicad.run_python(
            "import klicad_native_annotation as a; a.annotate(scope='all')"
        )
        if not r.ok:
            # Annotation failure shouldn't abort — the schematic is
            # still well-formed structurally.  Warn but continue.
            import sys
            sys.stderr.write(
                f"[to_schematic] annotation failed (continuing): "
                f"{r.exception_traceback}\n"
            )

    # Save once for the whole hierarchy.
    r = kicad.run_python(
        "import klicad_native_schematic_state as ss; ss.save_schematic()"
    )
    if not r.ok:
        raise RuntimeError(f"save_schematic failed: {r.exception_traceback}")

    # C.6: feed the spec-derived netlist into KliCAD's ratsnest layer.
    # Schematic is fully populated + saved on disk; pin coordinates are
    # stable so the C.5 binding can resolve net names to pin positions.
    # Soft-fails on all paths: missing kicad-cli, missing C.5 binding, or
    # missing pins are reported but do NOT abort to_schematic — the
    # schematic file is the primary product; the ratsnest layer is polish.
    ratsnest_spec = _push_ratsnest_spec(c, kicad, sch_path)

    # Aggregate result: roll up child-sheet counts into the top-level dict
    # so downstream callers see the totals across the hierarchy.
    def _sum(key: str) -> int:
        return root_result.get(key, 0) + sum(s.get(key, 0) for s in sub_results)

    return {
        "ok":              True,
        "parts_placed":    _sum("parts_placed"),
        "parts_kept":      _sum("parts_kept"),
        "parts_removed":   _sum("parts_removed"),
        "sheets_placed":   root_result.get("sheets_placed", 0),
        "sheets_kept":     root_result.get("sheets_kept", 0),
        "sheets_removed":  root_result.get("sheets_removed", 0),
        "labels_placed":   _sum("labels_placed"),
        "wires_placed":    _sum("wires_placed"),
        "mode":            mode,
        "sch_path":        str(sch_path),
        "models_lib_path": str(models_lib_path),
        "project_path":    str(pro_path),
        "ratsnest_spec":   ratsnest_spec,
    }


def _push_ratsnest_spec(c: "Circuit", kicad, sch_path: Path) -> dict | None:
    """Generate the netlist for ``c`` and hand it to the C.5 ratsnest binding.

    Returns the dict echoed by ``klicad_native_ratsnest.set_spec`` (with
    keys ``ok``, ``edges_placed``, ``nets_resolved``, ``missing_pins``)
    on success, or ``None`` when the spec couldn't be pushed (kicad-cli
    missing, the C.5 binding not yet built into the running KliCAD, etc.).

    Never raises: every failure path warns to stderr and returns None so
    ``to_schematic`` can complete.
    """
    import ast
    import sys

    # Step 1: build the netlist from the just-written schematic.  Use the
    # narrow netlist_from_sch helper rather than to_netlist(circuit, ...)
    # to avoid re-emitting the schematic (which would recurse into IPC).
    try:
        from ._netlist import netlist_from_sch
        netlist_text = netlist_from_sch(sch_path)
    except Exception as e:
        sys.stderr.write(
            f"[to_schematic] ratsnest spec skipped — netlist export failed: {e}\n"
        )
        return None

    # Step 2: send the netlist to klicad_native_ratsnest.set_spec via IPC.
    # repr() handles multi-line / quoted content safely (same pattern the
    # rest of this module uses for snippet construction).
    snippet = (
        f"import klicad_native_ratsnest as r\n"
        f"r.set_spec({netlist_text!r})"
    )
    try:
        result = kicad.run_python(snippet)
    except Exception as e:
        sys.stderr.write(
            f"[to_schematic] ratsnest spec skipped — run_python raised: {e}\n"
        )
        return None

    if not result.ok:
        tb = result.exception_traceback or ""
        if "ModuleNotFoundError" in tb or "No module named" in tb:
            sys.stderr.write(
                "[to_schematic] ratsnest spec skipped — "
                "klicad_native_ratsnest binding not present (rebuild KliCAD "
                "to pick up C.5).\n"
            )
        else:
            # Some other binding-side failure — surface a single line so the
            # user has a hint without flooding the log.
            last = tb.strip().splitlines()[-1] if tb.strip() else "(no traceback)"
            sys.stderr.write(
                f"[to_schematic] ratsnest spec skipped — set_spec failed: {last}\n"
            )
        return None

    try:
        return ast.literal_eval(result.result_repr or "None")
    except (ValueError, SyntaxError) as e:
        sys.stderr.write(
            f"[to_schematic] ratsnest spec result unparseable ({e!r}): "
            f"{result.result_repr!r}\n"
        )
        return None


# ──────────────────────────────────────────────────────────────────────────
# Per-sheet emit (used for root and each child)
# ──────────────────────────────────────────────────────────────────────────

def _navigate_to_root(kicad) -> None:
    """Pop sheets until depth == 0.  No-op if already on root."""
    import ast
    for _ in range(64):                    # depth-bounded safety
        r = kicad.run_python(
            "import klicad_native_hierarchy as h; h.get_current_sheet()"
        )
        if not r.ok:
            raise RuntimeError(
                f"_navigate_to_root: get_current_sheet failed: "
                f"{r.exception_traceback}"
            )
        cur = ast.literal_eval(r.result_repr)
        if int(cur.get("depth", 0)) == 0:
            return
        kicad.run_python(
            "import klicad_native_hierarchy as h; h.pop_sheet()"
        )
    raise RuntimeError("_navigate_to_root: depth > 64; possible loop")


def _current_sheet_path(kicad) -> str:
    """Return the current sheet's KIID-based path_string."""
    import ast
    r = kicad.run_python(
        "import klicad_native_hierarchy as h; h.get_current_sheet()"
    )
    if not r.ok:
        raise RuntimeError(
            f"_current_sheet_path: {r.exception_traceback}"
        )
    return ast.literal_eval(r.result_repr)["path_string"]


def _check_dup_refs(c: "Circuit") -> None:
    seen = set()
    dups = []
    for p in c.parts:
        if p.ref in seen:
            dups.append(p.ref)
        seen.add(p.ref)
    if dups:
        raise ValueError(
            f"to_schematic: c.parts contains duplicate refs "
            f"{sorted(set(dups))}.  Reference designators must be unique."
        )


def _find_root_sheet_with_filename(kicad, filename: str) -> str | None:
    """Return the KIID of the first root-level SCH_SHEET whose file_name
    matches `filename`, or None if none exists."""
    import ast
    r = kicad.run_python(
        "import klicad_native_hierarchy as h; h.list_sheets()"
    )
    if not r.ok:
        raise RuntimeError(
            f"_find_root_sheet_with_filename: {r.exception_traceback}"
        )
    for row in ast.literal_eval(r.result_repr):
        if int(row.get("depth", 0)) == 1 and row.get("file_name") == filename:
            return row["uuid"]
    return None


def _emit_one_sheet(c: "Circuit",
                     kicad,
                     models_lib_path: Path,
                     *,
                     mode: str,
                     layout: str,
                     route: bool,
                     sub_to_filename: dict[int, Path],
                     is_root: bool) -> dict:
    """Author one sheet's content under the current KliCAD sheet path.

    Returns a per-sheet diff result dict.  Caller is responsible for
    set_current_sheet navigation; this function operates on whatever
    sheet is currently active.

    For root sheets, sheet instances in c.parts get placed via
    _place_sheet_instances + _add_sheet_pins.  For child sheets, c
    is a Sub-Circuit definition and we additionally emit hier-label
    port anchors.
    """
    import ast

    sheet_path = _current_sheet_path(kicad)

    # Existing scalar symbols on THIS sheet only.
    r = kicad.run_python(
        f"import klicad_native_schematic_state as ss\n"
        f"ss.list_symbols({sheet_path!r})"
    )
    if not r.ok:
        raise RuntimeError(
            f"_emit_one_sheet: list_symbols failed: {r.exception_traceback}"
        )
    existing = ast.literal_eval(r.result_repr)
    # Filter out power-flag symbols (KiCad #PWR_*).
    existing = [row for row in existing if not row["ref"].startswith("#")]
    existing_by_ref = {row["ref"]: row for row in existing}

    # Existing root-level SCH_SHEET instances (only on root; child sheets
    # don't contain sub-sheets in H2 — nesting is H4).
    existing_sheets_by_ref: dict[str, dict] = {}
    if is_root:
        r = kicad.run_python(
            "import klicad_native_hierarchy as h; h.list_sheets()"
        )
        if r.ok:
            for row in ast.literal_eval(r.result_repr):
                if int(row.get("depth", 0)) != 1:
                    continue
                # R5.6: skip BuildSheetList's synthetic clones — multi-channel
                # sheets expand into N hierarchy paths sharing the same name,
                # but only the on-canvas template's KIID is addressable for
                # downstream binding calls (add_sheet_pin, delete_by_kiid).
                if row.get("is_synthetic"):
                    continue
                existing_sheets_by_ref[row["name"]] = row

    # Target sets — scalar parts vs sheet instances.
    scalar_parts = [p for p in c.parts if p.kind != "SUBCIRCUIT"]
    sheet_parts  = [p for p in c.parts if p.kind == "SUBCIRCUIT"]
    target_refs        = {p.ref for p in scalar_parts}
    target_by_ref      = {p.ref: p for p in scalar_parts}
    target_sheet_refs  = {p.ref for p in sheet_parts}
    target_sheet_by_ref = {p.ref: p for p in sheet_parts}

    # ── Scalar-symbol diff (existing semantics) ──────────────────────────
    lib_id_mismatch = {
        ref for ref in (set(existing_by_ref) & target_refs)
        if existing_by_ref[ref].get("lib_id") != target_by_ref[ref].kicad_lib_id
    }
    keep_refs    = (set(existing_by_ref) & target_refs) - lib_id_mismatch
    remove_refs  = (set(existing_by_ref) - target_refs) | lib_id_mismatch

    # ── Sheet-instance diff (root only) ──────────────────────────────────
    keep_sheet_refs:   set[str] = set()
    remove_sheet_refs: set[str] = set()
    if is_root:
        # Demotion key: ref must match AND child file_name must match.
        # H3 will refine to per-pin diff; H2 placeholder is whole-sheet
        # remove+add on any port-set difference.
        for ref in (set(existing_sheets_by_ref) & target_sheet_refs):
            tgt = target_sheet_by_ref[ref]
            tgt_file = sub_to_filename[id(tgt.definition)].name
            if existing_sheets_by_ref[ref].get("file_name") == tgt_file:
                keep_sheet_refs.add(ref)
            else:
                remove_sheet_refs.add(ref)
        # Refs in existing but not in target → remove.
        remove_sheet_refs |= (set(existing_sheets_by_ref) - target_sheet_refs)

    if mode == "strict" and (existing or existing_sheets_by_ref):
        raise RuntimeError(
            f"to_schematic(mode='strict'): refusing to run on a sheet "
            f"with {len(existing)} existing symbols and "
            f"{len(existing_sheets_by_ref)} existing sheet instances.  "
            f"Pass mode='diff' / mode='replace' or clear the sheet first."
        )

    if mode == "replace":
        # Wipe both symbols and root sheets on this sheet.
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
        for row in existing_sheets_by_ref.values():
            r = kicad.run_python(
                f"import klicad_native_schematic_state as ss\n"
                f"ss.delete_by_kiid({row['uuid']!r})"
            )
            if not r.ok:
                raise RuntimeError(
                    f"delete_by_kiid({row['uuid']}) failed: "
                    f"{r.exception_traceback}"
                )
        keep_refs, remove_refs = set(), set(existing_by_ref)
        keep_sheet_refs, remove_sheet_refs = set(), set(existing_sheets_by_ref)
        existing_by_ref = {}
        existing_sheets_by_ref = {}

    elif mode == "diff":
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
        for ref in remove_sheet_refs:
            row = existing_sheets_by_ref[ref]
            r = kicad.run_python(
                f"import klicad_native_schematic_state as ss\n"
                f"ss.delete_by_kiid({row['uuid']!r})"
            )
            if not r.ok:
                raise RuntimeError(
                    f"delete_by_kiid({row['uuid']}) for sheet ref={ref!r} "
                    f"failed: {r.exception_traceback}"
                )

    # Wipe routing on THIS sheet.  Scoped via the sheet_path arg.
    # Child sheets spare hier-labels (port anchors) — _emit_port_anchors
    # does a per-name diff that preserves their (possibly user-
    # positioned) coordinates.  Root has no port anchors to preserve.
    keep_hier = "True" if (not is_root and c.is_subcircuit) else "False"
    r = kicad.run_python(
        f"import klicad_native_schematic_state as ss\n"
        f"ss.clear_routing({sheet_path!r}, keep_hier_labels={keep_hier})"
    )
    if not r.ok:
        raise RuntimeError(
            f"clear_routing failed: {r.exception_traceback}"
        )

    # Place scalar parts on this sheet (skipping kept refs).
    skip = {ref: existing_by_ref[ref]["kiid"]
            for ref in keep_refs if ref in existing_by_ref}
    placed = _place_parts(c, kicad, models_lib_path, layout=layout,
                          skip_refs=skip)

    # Place sheet instances (root only) — H2 single-level.
    sheet_kiids: dict[str, str] = {}
    if is_root:
        sheet_skip = {ref: existing_sheets_by_ref[ref]["uuid"]
                      for ref in keep_sheet_refs}
        positions = _layout_positions(c, engine=layout)
        sheet_kiids = _place_sheet_instances(c, kicad, sub_to_filename,
                                              positions, sheet_skip)
        # Merge sheet kiids into `placed` so _label_pins's SUBCIRCUIT
        # branch can find them.
        placed.update(sheet_kiids)
        # _add_sheet_pins needs the (x, y) origin of each placed sheet
        # to compute pin positions on the sheet's perimeter.
        sheet_positions = {
            p.ref: positions[p.ref]
            for p in c.parts if p.kind == "SUBCIRCUIT"
        }
        _add_sheet_pins(c, kicad, sheet_kiids, sheet_positions)

    # Emit hier-label anchors for ports on a child sheet (one per port).
    if not is_root and c.is_subcircuit:
        _emit_port_anchors(c, kicad, models_lib_path)

    # Labels / routing.
    if route:
        from ._route import route_signal_nets
        labeled = _label_power_pins_only(c, kicad, placed)
        wires = route_signal_nets(c, kicad, placed)
    else:
        labeled = _label_pins(c, kicad, placed)
        wires = 0

    _place_power_symbols(c, kicad)

    return {
        "ok":              True,
        "sheet_path":      sheet_path,
        "parts_placed":    sum(
            1 for p in c.parts
            if p.kind != "SUBCIRCUIT" and p.ref not in keep_refs
        ),
        "parts_kept":      len(keep_refs),
        "parts_removed":   len(remove_refs),
        "sheets_placed":   sum(
            1 for ref in target_sheet_refs if ref not in keep_sheet_refs
        ) if is_root else 0,
        "sheets_kept":     len(keep_sheet_refs),
        "sheets_removed":  len(remove_sheet_refs),
        "labels_placed":   labeled,
        "wires_placed":    wires,
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
        # Sheet instances follow a different placement path — see
        # _place_sheet_instances.  Don't try to add_symbol them.
        if p.kind == "SUBCIRCUIT":
            continue
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

    Same shape as _label_pins re: kind-branching — sheet instances
    (SUBCIRCUIT) get pin positions from `list_sheet_pins` since their
    SCH_SHEET_PINs aren't reachable via get_symbol_pin_position.
    """
    import ast
    powerlike = {name for name, meta in c.nets.items()
                 if meta.kind in ("power", "ground")}
    n = 0
    for p in c.parts:
        kiid = placed[p.ref]

        if p.kind == "SUBCIRCUIT":
            # Power-named external nets bound to sheet pins get labels
            # at the pin's external position; everything else stays
            # routed.  Position lookup via list_sheet_pins.
            r = kicad.run_python(
                f"import klicad_native_hierarchy as h\n"
                f"h.list_sheet_pins({kiid!r})"
            )
            if not r.ok:
                continue
            by_name = {pin["name"]: (pin["x_mm"], pin["y_mm"])
                       for pin in ast.literal_eval(r.result_repr)}
            for port_name, net_name in p.connections.items():
                if net_name not in powerlike:
                    continue
                if port_name not in by_name:
                    continue
                x, y = by_name[port_name]
                r = kicad.run_python(
                    f"import klicad_native_schematic_state as ss\n"
                    f"ss.add_label({x}, {y}, {net_name!r})\n"
                    f"True"
                )
                if r.ok:
                    n += 1
            continue

        # Scalar symbol path (existing).
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
    """Drop a text label at every pin coordinate.  Returns count placed.

    Symbols and sheet instances both flow through this, with different
    pin-position resolvers.  For symbols: `get_symbol_pin_position(kiid,
    kicad_pin_num)`.  For sheet instances (kind == "SUBCIRCUIT"): the
    SCH_SHEET_PIN positions come from `klicad_native_hierarchy.list_sheet_pins`.
    """
    import ast
    from ._bus import to_body_local_net
    # R5.7: when emitting a multi-channel body's labels, translate body
    # bus-member references (e.g., ``"GATE[0]"``) to KliCAD's bare bit
    # form (``"GATE0"``) so the connection_graph fan-out at R3.3 can
    # match the slot-K-specific bit name produced by repeatBusPinBitName.
    # is_subcircuit + a declared bus port is the trigger; root Circuits
    # and bus-free sub-circuits are unaffected.
    body_port_decl = (
        list(c._port_decl) if c.is_subcircuit and any(
            "[" in p and ".." in p for p in c._port_decl
        ) else []
    )
    n = 0
    for p in c.parts:
        kiid = placed[p.ref]

        if p.kind == "SUBCIRCUIT":
            # Sheet instance — pin positions come from the SCH_SHEET_PIN
            # items on this sheet.  list_sheet_pins gives name -> (x, y).
            r = kicad.run_python(
                f"import klicad_native_hierarchy as h\n"
                f"h.list_sheet_pins({kiid!r})"
            )
            if not r.ok:
                raise RuntimeError(
                    f"list_sheet_pins({kiid}) failed: {r.exception_traceback}"
                )
            sheet_pins = ast.literal_eval(r.result_repr)
            pin_pos = {row["name"]: (row["x_mm"], row["y_mm"])
                       for row in sheet_pins}
            # R5.6: multi-channel — sheet pins are bus-shaped (one pin per
            # declared port, e.g., "GATE[0..3]") and the parent label at
            # the pin position must carry the bus net so the connection
            # graph sees a bus driver into the pin.  Build (pin_name,
            # net_label) by walking the definition's port_decl: for each
            # declared entry the matching binding from port_map (keyed by
            # base name for bus ports, exact name for scalars) is the
            # parent net.
            if getattr(p, "repeat_count", 1) > 1:
                from ._bus import parse_bus_range
                port_iter = []
                for decl_port in p.definition._port_decl:
                    parsed = parse_bus_range(decl_port)
                    key = parsed[0] if parsed else decl_port
                    if key in p.port_map:
                        port_iter.append((decl_port, p.port_map[key]))
            else:
                # For SubcircuitInstance, p.pin_names == p.connections.keys() ==
                # expanded port list; the net is the external binding.
                port_iter = list(p.connections.items())
            for port_name, net_name in port_iter:
                if port_name not in pin_pos:
                    # Port hasn't been pinned yet — happens during placement
                    # before _add_sheet_pins runs.  Skip this iteration; the
                    # next pass will see it.
                    continue
                x, y = pin_pos[port_name]
                r = kicad.run_python(
                    f"import klicad_native_schematic_state as ss\n"
                    f"ss.add_label({x}, {y}, {net_name!r})\n"
                    f"True"
                )
                if not r.ok:
                    raise RuntimeError(
                        f"failed labelling sheet {p.ref} pin {port_name!r} "
                        f"as {net_name}: {r.exception_traceback}"
                    )
                n += 1
            continue

        # Symbol path (existing).
        for spice_pin, net_name in p.connections.items():
            kicad_pin_num = p.kicad_pin_map[spice_pin]
            local_net = (to_body_local_net(net_name, body_port_decl)
                         if body_port_decl else net_name)
            r = kicad.run_python(
                f"import klicad_native_schematic_state as ss\n"
                f"pos = ss.get_symbol_pin_position({kiid!r}, {kicad_pin_num!r})\n"
                f"if not pos.get('ok'): raise RuntimeError(f'pin pos failed: ' + str(pos))\n"
                f"ss.add_label(pos['x_mm'], pos['y_mm'], {local_net!r})\n"
                f"True"
            )
            if not r.ok:
                raise RuntimeError(
                    f"failed labelling {p.ref}.{spice_pin} as {local_net}: "
                    f"{r.exception_traceback}"
                )
            n += 1
    return n


# ──────────────────────────────────────────────────────────────────────────
# Sheet placement (H2 — hierarchical schematics)
# ──────────────────────────────────────────────────────────────────────────

# H2 placeholder sheet dimensions.  H5 will replace with bbox-aware layout.
_SHEET_W = 12 * _GRID    # 30.48 mm
_SHEET_PIN_DY = 2 * _GRID  # 5.08 mm per pin
_SHEET_PIN_INSET = 2 * _GRID  # offset from top edge


def _sheet_size_for(sc_def: "Circuit") -> tuple[float, float]:
    """Compute the (w, h) bbox for a sheet instance of `sc_def`.

    Width is fixed for H2 (no name-length-aware sizing yet).  Height
    scales with port count so all pins fit on one edge.
    """
    n_pins = len(sc_def._ports_expanded)
    h = max(4 * _GRID, _SHEET_PIN_INSET * 2 + n_pins * _SHEET_PIN_DY)
    return (_SHEET_W, h)


def _sheet_pin_layout(sc_def: "Circuit", sheet_x: float, sheet_y: float
                       ) -> list[tuple[str, str, float, float]]:
    """Compute (name, side, x_mm, y_mm) for each port on a sheet instance.

    H2 placeholder: all pins on the LEFT edge, evenly spaced from top.
    Positions are absolute on the parent canvas (sheet_x + 0, sheet_y +
    inset + i * dy).  H5 will replace with smarter placement that
    considers child sheet topology.
    """
    out: list[tuple[str, str, float, float]] = []
    for i, port_name in enumerate(sc_def._ports_expanded):
        x = sheet_x
        y = sheet_y + _SHEET_PIN_INSET + i * _SHEET_PIN_DY
        out.append((port_name, "left", x, y))
    return out


def _place_sheet_instances(c: "Circuit", kicad,
                            sub_to_filename: dict[int, Path],
                            positions: dict[str, tuple[float, float]],
                            skip_refs: dict[str, str]) -> dict[str, str]:
    """Add SCH_SHEET items for every SubcircuitInstance in c.parts.

    Returns ref -> sheet kiid mapping (including skipped/kept refs so
    downstream labelers/routers can address every instance).  Sheet
    pins are emitted by _add_sheet_pins after placement.
    """
    import ast
    import uuid
    placed: dict[str, str] = dict(skip_refs)
    for p in c.parts:
        if p.kind != "SUBCIRCUIT":
            continue
        if p.ref in skip_refs:
            continue
        sc_def = p.definition
        filename = sub_to_filename[id(sc_def)].name   # bare name, not full path
        w, h = _sheet_size_for(sc_def)
        x, y = positions[p.ref]
        # R5.4: multi-channel sheet — pass repeat_count + stable instance
        # KIIDs to the C++ binding.  Lazy-populate p.repeat_instances on
        # first emit so subsequent re-emits reuse the same KIIDs.
        repeat_count = getattr(p, "repeat_count", 1)
        if repeat_count > 1:
            if not getattr(p, "repeat_instances", None):
                p.repeat_instances = [
                    str(uuid.uuid4()) for _ in range(repeat_count - 1)
                ]
            extra_kwargs = (
                f", repeat_count={repeat_count}"
                f", repeat_instances={p.repeat_instances!r}"
            )
        else:
            extra_kwargs = ""
        snippet = (
            f"import klicad_native_schematic_state as ss\n"
            f"r = ss.add_sheet({p.ref!r}, {filename!r}, {x}, {y}, {w}, {h}{extra_kwargs})\n"
            f"if not r.get('ok'): raise RuntimeError(f'add_sheet failed for {p.ref}: ' + str(r))\n"
            f"r['kiid']"
        )
        r = kicad.run_python(snippet)
        if not r.ok:
            raise RuntimeError(
                f"failed placing sheet instance {p.ref}: {r.exception_traceback}"
            )
        placed[p.ref] = ast.literal_eval(r.result_repr)
    return placed


def _add_sheet_pins(c: "Circuit", kicad,
                     sheet_kiids: dict[str, str],
                     sheet_positions: dict[str, tuple[float, float]]
                     ) -> tuple[int, int]:
    """Per-pin diff: add target pins missing on the sheet, delete pins
    no longer in the target port list.  Returns (added, removed).

    For a fresh sheet, all pins are added at evenly-spaced positions
    along the left edge starting from the sheet's origin.

    For a kept sheet with existing pins, the matching pins are left
    alone (preserves any user-applied repositioning).  New pins land
    below the bottommost existing pin, on the same edge.

    H3 makes this the spice-idempotence path for port-set changes —
    SCH_SHEET kiid + position + user-positioned pins all survive
    unless `child_filename` changes (whole-sheet remove+add).
    """
    import ast
    added = 0
    removed = 0
    for p in c.parts:
        if p.kind != "SUBCIRCUIT":
            continue
        sheet_kiid = sheet_kiids[p.ref]

        # Existing pins on this sheet.
        r = kicad.run_python(
            f"import klicad_native_hierarchy as h\n"
            f"h.list_sheet_pins({sheet_kiid!r})"
        )
        existing_pins: list[dict] = ast.literal_eval(r.result_repr) if r.ok else []
        existing_by_name = {pin["name"]: pin for pin in existing_pins}

        # R5.6: multi-channel — emit ONE sheet pin per declared port (bus
        # syntax preserved as `GATE[0..N-1]`) so KliCAD's connection_graph
        # bit-fan-out at slot K can map the K-th bus member to the body's
        # scalar hier-label `GATE<K>`.  For non-multi-channel sheets keep
        # the legacy fully-expanded behaviour so a body with a bus port
        # at repeat=1 still names each bit explicitly.
        if getattr(p, "repeat_count", 1) > 1:
            target_names = list(p.definition._port_decl)
        else:
            target_names = list(p.pin_names)
        target_set = set(target_names)
        existing_set = set(existing_by_name)

        # Delete pins not in target.
        for name, pin in existing_by_name.items():
            if name in target_set:
                continue
            r = kicad.run_python(
                f"import klicad_native_schematic_state as ss\n"
                f"ss.delete_by_kiid({pin['uuid']!r})"
            )
            if not r.ok:
                raise RuntimeError(
                    f"delete_by_kiid({pin['uuid']}) for sheet {p.ref} "
                    f"pin {name!r}: {r.exception_traceback}"
                )
            removed += 1

        # Add pins in target but not existing.
        to_add = [n for n in target_names if n not in existing_set]
        if not to_add:
            continue

        # Origin + side for new pins.  Strategy:
        #   - If kept pins exist, group them by side and anchor new
        #     pins on whichever side has the most kept pins (so the
        #     visual layout follows what the user/we last chose).
        #     Y advances below the bottommost kept pin on that side.
        #   - Fresh sheet: fall back to sheet_positions layout +
        #     default "left" side.
        kept_pins = [pin for pin in existing_pins if pin["name"] in target_set]
        if kept_pins:
            by_side: dict[str, list[dict]] = {}
            for pin in kept_pins:
                by_side.setdefault(pin.get("side", "left"), []).append(pin)
            # Pick the most-populated side; ties broken by sort order.
            anchor_side = max(by_side, key=lambda s: (len(by_side[s]), s))
            side_pins = by_side[anchor_side]
            x_anchor = side_pins[0]["x_mm"]
            y_anchor = max(pin["y_mm"] for pin in side_pins) + _SHEET_PIN_DY
        else:
            x_origin, y_origin = sheet_positions.get(p.ref, (0.0, 0.0))
            x_anchor = x_origin
            y_anchor = y_origin + _SHEET_PIN_INSET
            anchor_side = "left"

        for i, name in enumerate(to_add):
            x_mm = x_anchor
            y_mm = y_anchor + i * _SHEET_PIN_DY
            r = kicad.run_python(
                f"import klicad_native_schematic_state as ss\n"
                f"r = ss.add_sheet_pin({sheet_kiid!r}, {name!r}, "
                f"{anchor_side!r}, {x_mm}, {y_mm})\n"
                f"if not r.get('ok'): raise RuntimeError('add_sheet_pin failed: ' + str(r))\n"
                f"True"
            )
            if not r.ok:
                raise RuntimeError(
                    f"failed pinning sheet {p.ref} port {name!r}: "
                    f"{r.exception_traceback}"
                )
            added += 1

    return added, removed


def _emit_port_anchors(sc_def: "Circuit", kicad, models_lib_path: Path) -> tuple[int, int]:
    """Inside a child sheet, ensure one SCH_HIER_LABEL exists per port.

    Per-port-name diff against the existing hier-labels on the current
    sheet:
      * Existing hier-label with matching name → leave alone (preserves
        position; user-positioned anchors survive iterations).
      * Existing hier-label with a name not in the port set → delete
        (port removed; stale anchor).
      * Port name not in existing hier-labels → place new one at the
        default left-edge anchor position.

    Returns (added, removed).  Caller must have set_current_sheet to
    the child before invoking; clear_routing on the child must have
    been called with keep_hier_labels=True so the existing anchors
    survive long enough for this diff to see them.

    KliCAD's net resolver merges any local label inside the body that
    names the same port-net into the hier-label by name-coincidence;
    position has no semantic effect on the netlist.
    """
    import ast
    sheet_path = _current_sheet_path(kicad)
    r = kicad.run_python(
        f"import klicad_native_schematic_state as ss\n"
        f"ss.list_labels({sheet_path!r})"
    )
    if not r.ok:
        raise RuntimeError(
            f"_emit_port_anchors: list_labels failed: {r.exception_traceback}"
        )
    existing = [row for row in ast.literal_eval(r.result_repr)
                if row["kind"] == "hierarchical"]
    existing_by_name: dict[str, dict] = {}
    for row in existing:
        # If duplicate names exist, keep the first; delete the rest below.
        existing_by_name.setdefault(row["name"], row)

    # R5.7: for body bus ports declared as ``GATE[0..N-1]`` emit N bare
    # bit-member hier-labels (``GATE0``..``GATE<N-1>``) so R3.3's
    # ``repeatBusPinBitName`` (which emits bare ``GATE<K>`` for slot K)
    # finds an exact name match in the body and binds parent bit K to
    # slot K's body subgraph.  Scalar ports + bus-free bodies keep the
    # pre-R5.7 ``_ports_expanded`` behaviour, which spells each member
    # with brackets (``GATE[0]``).
    from ._bus import bus_bit_member_name, parse_bus_range
    has_bus_port = any(parse_bus_range(p) is not None for p in sc_def._port_decl)
    if has_bus_port:
        target_names: list[str] = []
        for p in sc_def._port_decl:
            parsed = parse_bus_range(p)
            if parsed is None:
                target_names.append(p)
                continue
            base, low, high = parsed
            target_names.extend(bus_bit_member_name(base, k)
                                for k in range(low, high + 1))
    else:
        target_names = list(sc_def._ports_expanded)
    target_set = set(target_names)

    # Delete stale anchors (name not in port list) + duplicates.
    removed = 0
    seen_in_target: set[str] = set()
    for row in existing:
        name = row["name"]
        if name not in target_set or name in seen_in_target:
            r = kicad.run_python(
                f"import klicad_native_schematic_state as ss\n"
                f"ss.delete_by_kiid({row['kiid']!r})"
            )
            if r.ok:
                removed += 1
        else:
            seen_in_target.add(name)

    # Place missing anchors.
    added = 0
    for i, port_name in enumerate(target_names):
        if port_name in existing_by_name and port_name in seen_in_target:
            # Already present at user-positioned coords — leave alone.
            continue
        x = 4 * _GRID
        y = 4 * _GRID + i * _SHEET_PIN_DY
        r = kicad.run_python(
            f"import klicad_native_schematic_state as ss\n"
            f"r = ss.add_label({x}, {y}, {port_name!r}, kind='hierarchical')\n"
            f"if not r.get('ok'): raise RuntimeError('hier label failed: ' + str(r))\n"
            f"True"
        )
        if not r.ok:
            raise RuntimeError(
                f"failed emitting port anchor for {sc_def.name}:{port_name!r}: "
                f"{r.exception_traceback}"
            )
        added += 1
    return added, removed


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

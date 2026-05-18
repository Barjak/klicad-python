"""Render a Circuit to a KiCad .kicad_sch file via the live KliCAD bindings.

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


# Grid spacing (mm).  100mil = 2.54mm is the KiCad schematic grid unit.
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

    Critically does NOT touch the .kicad_sch — that would clobber KiCad's
    in-memory state if it has the schematic open, popping an
    UnsavedChangesDialog that blocks the IPC.  The schematic gets
    authored via the bindings and saved with ss.save_schematic().

    For a fresh project where no .kicad_sch exists yet, write a minimal
    stub so KiCad's load_project + open_schematic has something to read.

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
    # overwrite a live schematic file out from under KiCad.
    if not sch_path.exists():
        sch_path.write_text(
            f"(kicad_sch (version 20250114) (generator \"klicad-circuit\")\n"
            f" (generator_version \"10.99\") (uuid \"00000000-0000-0000-0000-000000000001\")\n"
            f" (paper \"A4\") (lib_symbols)\n"
            f" (sheet_instances (path \"/\" (page \"1\"))))\n"
        )

    # Project sym-lib-table: by default, DON'T write one.  The global
    # symbol library table (configured at KiCad install / Preferences ->
    # Manage Symbol Libraries) already provides Device, Transistor_BJT,
    # Simulation_SPICE, power, etc.  Writing a project-scoped one with
    # hardcoded ${KICAD_SYMBOL_DIR} paths invariably gets the env-var
    # name wrong (KiCad 10 uses ${KICAD10_SYMBOL_DIR}) and / or assumes a
    # single-file lib layout when upstream now ships exploded
    # `.kicad_symdir/` directories.  Cleaner to leave the project table
    # empty and let KiCad resolve via the global table.
    #
    # Future: when we need a project-local merged lib (e.g. for CI without
    # a global table), write it via a separate Circuit.add_local_lib() API
    # so the user opts in.
    if not sym_lib_table_path.exists():
        sym_lib_table_path.write_text("(sym_lib_table)\n")

    # Gather every distinct model the parts reference + concatenate the
    # corresponding .model lines from the configured model_lib_paths.
    # models.lib is project-local and KiCad doesn't keep it open, so
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

def _layout_positions(c: "Circuit") -> dict[str, tuple[float, float]]:
    """Assign (x_mm, y_mm) to each part ref.  Returns ref -> (x, y).

    Simple row layout for v1.  Phase C will replace with a graph-aware
    placement algorithm.
    """
    positions: dict[str, tuple[float, float]] = {}
    for i, p in enumerate(c.parts):
        positions[p.ref] = (_ROW_X0 + i * _PART_DX, _ROW_Y + _PART_DY / 2)
    return positions


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
            # Pick KiCad power symbol by common name; default to +5V.
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

def to_kicad_sch(
    c: "Circuit",
    sch_path,
    *,
    kicad=None,
) -> dict:
    """Generate a complete .kicad_sch (+ project files + models.lib) for circuit c.

    Returns {ok, parts_placed, labels_placed, sch_path, models_lib_path,
              project_path}.
    """
    from kipy import KiCad

    sch_path = Path(sch_path).resolve()
    pro_path, sym_lib_table_path, models_lib_path = _bootstrap_project_files(c, sch_path)

    if kicad is None:
        kicad = KiCad()

    # The KliCAD instance must already be loaded on this project — load
    # otherwise (subject to schematic-switch crash if a different project
    # is already open).
    pre = kicad.run_python(
        "import kicad_native_project_manager as pm; pm.get_current_project()"
    )
    if pre.ok and pro_path.name in pre.result_repr:
        # Already loaded.
        pass
    else:
        r = kicad.run_python(
            f"import kicad_native_project_manager as pm; "
            f"pm.load_project({str(pro_path)!r})"
        )
        if not r.ok:
            raise RuntimeError(
                f"failed to load_project({pro_path}): {r.exception_traceback}"
            )

    # Open the schematic.
    r = kicad.run_python(
        "import kicad_native_gui as g; g.show_frame('schematic')\n"
        f"import kicad_native_schematic_state as ss\n"
        f"ss.open_schematic({str(sch_path)!r})"
    )
    if not r.ok:
        raise RuntimeError(f"open_schematic failed: {r.exception_traceback}")

    # Idempotency guard: full-rebuild path is not idempotent — running
    # twice on the same schematic doubles the placements (each add_symbol
    # appends; we don't delete pre-existing content yet).  Phase C will
    # add incremental update via diff/apply.  For now, refuse to proceed
    # if the schematic already has symbols on it — the user must clear
    # them manually (Edit → Select All → Delete) in the schematic editor,
    # or delete the .kicad_sch file before launching KiCad.
    r = kicad.run_python(
        "import kicad_native_schematic_state as ss; "
        "ss.get_items_summary().get('symbols', 0)"
    )
    if r.ok and int(r.result_repr) > 0:
        raise RuntimeError(
            f"to_kicad_sch() refuses to run on a schematic that already "
            f"has {r.result_repr} symbols; running would double-place them. "
            f"Either clear the schematic (Edit → Select All → Delete in "
            f"the editor) or delete {sch_path} + launch KiCad on the "
            f"project from scratch.  Phase C will add incremental update."
        )

    placed = _place_parts(c, kicad, models_lib_path)
    labeled = _label_pins(c, kicad, placed)
    _place_power_symbols(c, kicad)

    # Save
    r = kicad.run_python(
        "import kicad_native_schematic_state as ss; ss.save_schematic()"
    )
    if not r.ok:
        raise RuntimeError(f"save_schematic failed: {r.exception_traceback}")

    return {
        "ok":               True,
        "parts_placed":     len(placed),
        "labels_placed":    labeled,
        "sch_path":         str(sch_path),
        "models_lib_path":  str(models_lib_path),
        "project_path":     str(pro_path),
    }


def _place_parts(c: "Circuit", kicad, models_lib_path: Path) -> dict[str, str]:
    """Place every Part as a SCH_SYMBOL.  Returns ref -> kiid mapping."""
    positions = _layout_positions(c)
    placed: dict[str, str] = {}
    needs_lib = {"NPN", "PNP", "D", "LED"}  # part kinds whose models live in the .lib

    for p in c.parts:
        x, y = positions[p.ref]
        # For non-DC V/I sources we placed a typed source symbol
        # (VPULSE/VSIN/...) whose Value field is just a label — don't
        # overwrite it with the raw SPICE spec string, set Sim.Params
        # instead (which is what KiCad's netlist exporter reads).
        is_typed_source = (
            p.kind in ("V", "I")
            and getattr(p, "sim_params", None) is not None
        )
        snippet = (
            f"import kicad_native_schematic_state as ss\n"
            f"sym = ss.add_symbol({p.kicad_lib_id!r}, {p.ref!r}, {x}, {y})\n"
            f"if not sym.get('ok'): raise RuntimeError(f'add_symbol failed for {p.ref}: ' + str(sym))\n"
            f"kiid = sym['kiid']\n"
        )
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
            # KiCad's SPICE pipeline (eeschema/sim/) looks for Sim.Name
            # as the model identifier — NOT Sim.Model (which was an earlier
            # KiCad 7-era convention).  Without Sim.Name the netlist
            # generator emits '<ref>.unknown' as the model name and the
            # deck fails to simulate.  Set Sim.Name to the model name.
            if p.model:
                snippet += (
                    f"ss.set_symbol_field(kiid, 'Sim.Name', {p.model!r})\n"
                )
        snippet += "kiid"

        r = kicad.run_python(snippet)
        if not r.ok:
            raise RuntimeError(f"failed placing {p.ref}: {r.exception_traceback}")
        import ast
        placed[p.ref] = ast.literal_eval(r.result_repr)

    return placed


def _label_pins(c: "Circuit", kicad, placed: dict[str, str]) -> int:
    """Drop a net-label at every pin coordinate.  Returns count placed."""
    n = 0
    for p in c.parts:
        kiid = placed[p.ref]
        for spice_pin, net_name in p.connections.items():
            kicad_pin_num = p.kicad_pin_map[spice_pin]
            r = kicad.run_python(
                f"import kicad_native_schematic_state as ss\n"
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
        # so KiCad sees them as connected to net_name.
        r = kicad.run_python(
            f"import kicad_native_schematic_state as ss\n"
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

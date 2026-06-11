"""GOAL.md F-S3 — single-IPC compose entry.

Replaces the multi-call legacy flow (``to_schematic`` + ``relayout_via_ogdf``)
with one round trip:

    Circuit.compose(sch_path)
       -> klicad_native_schematic_compose.compose(sch_path, program_dict)

Program dict carries parts / sheets / nets and **no coordinates** — F-S6
forbids any (x, y) literal from leaving Python.  The C++ side opens one
``SchLayoutTransaction``, materializes everything, runs ELK + F-S1d
writeback, commits, and returns a ``ComposeReport``-shaped dict.

Fork 1 resolved as (b): the program travels as a pybind11 dict.  Schema is
the encoder/decoder pair (this module + ``bindings_schematic_compose.cpp``);
drift is caught by ``test_compose_program_schema_round_trip``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from ._part import Part


# ──────────────────────────────────────────────────────────────────────────
# Project-shell creation (F-S3 Phase C: compose absorbs this from the
# soon-to-be-deleted _klicad_sch.write_project_shell)
# ──────────────────────────────────────────────────────────────────────────

def _bootstrap_project_files(c: Any, sch_path: Path) -> tuple[Path, Path, Path]:
    """Write supporting files (.kicad_pro, sym-lib-table, models.lib).

    Does NOT touch .kicad_sch if it already exists — KliCAD may have it
    open, and clobbering it would pop an UnsavedChangesDialog that blocks
    IPC.  For a fresh project where no .kicad_sch exists yet, write a
    minimal stub so KliCAD's load_project + open_schematic has something
    to read.

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

    if not sch_path.exists():
        sch_path.write_text(
            f"(kicad_sch (version 20250114) (generator \"klicad-circuit\")\n"
            f" (generator_version \"10.99\") (uuid \"00000000-0000-0000-0000-000000000001\")\n"
            f" (paper \"A4\") (lib_symbols)\n"
            f" (sheet_instances (path \"/\" (page \"1\"))))\n"
        )

    # Project sym-lib-table: leave empty.  KliCAD's global table provides
    # Device / Transistor_BJT / Simulation_SPICE / power.  A project-scoped
    # table with hardcoded ${KICAD_SYMBOL_DIR} paths invariably gets the
    # env-var name wrong (KliCAD 10 uses ${KICAD10_SYMBOL_DIR}) and / or
    # assumes single-file libs when upstream now ships exploded dirs.
    if not sym_lib_table_path.exists():
        sym_lib_table_path.write_text("(sym_lib_table)\n")

    # Concatenate every distinct .model line from configured model_lib_paths.
    needed_models: set[str] = set()
    for p in c.parts:
        if getattr(p, "model", None):
            needed_models.add(p.model)

    deck_lines: list[str] = [
        f"* Auto-generated from {c.name} — models needed: "
        + ", ".join(sorted(needed_models))
    ]
    for src_path in getattr(c, "model_lib_paths", []) or []:
        if Path(src_path).exists():
            deck_lines.append(f"* --- from {src_path} ---")
            deck_lines.append(Path(src_path).read_text().rstrip())
    models_lib_path.write_text("\n".join(deck_lines) + "\n")

    return pro_path, sym_lib_table_path, models_lib_path


def write_project_shell(c: Any, sch_path) -> dict[str, Any]:
    """Write the offline project shell for a Circuit (no KliCAD needed).

    Generates .kicad_pro / sym-lib-table / models.lib next to sch_path,
    plus a minimal .kicad_sch stub if absent.  This is the always-succeeds
    half that compose() invokes automatically; it's exposed here so
    tooling can prepare a project tree offline (e.g. CI generating a
    placeholder project before booting KliCAD).
    """
    sch_path = Path(sch_path).resolve()
    pro_path, sym_lib_table_path, models_lib_path = _bootstrap_project_files(
        c, sch_path
    )
    return {
        "ok": True,
        "sch_path": str(sch_path),
        "project_path": str(pro_path),
        "models_lib_path": str(models_lib_path),
        "sym_lib_table_path": str(sym_lib_table_path),
    }


def _net_kind_to_string(kind: str | None) -> str:
    """Map ``NetMeta.kind`` to the program's enum-string."""
    if kind is None:
        return "SIGNAL"
    k = kind.upper()
    if k in ("POWER", "GROUND", "HIER_PORT", "SIGNAL"):
        return k
    # Auto-classified kinds end up in here ("power", "signal", etc.)
    # — just upper-case them and trust the C++ side to validate.
    return k


def _sanitize_subckt_filename(name: str) -> str:
    """Sanitize a sub-circuit definition name to a filesystem-safe basename.

    Migrated here from the now-deleted ``_klicad_sch.py`` (F-S3 Phase C).
    Lowercases, collapses runs of non-alphanumeric characters to a single
    underscore, strips leading/trailing underscores, and appends the
    ``.kicad_sch`` extension.  An empty/all-symbol name falls back to
    ``"sheet"`` so we never emit ``".kicad_sch"`` with no stem.
    """
    stem = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    if not stem:
        stem = "sheet"
    return f"{stem}.kicad_sch"


def _subcircuit_instance_to_program_sheet(p: Any) -> dict[str, Any] | None:
    """GOAL.md F-S5 Phase A data shape; consumed by
    ``bindings_schematic_compose.cpp``'s sheet materialization loop.

    Maps one ``SubcircuitInstance`` part onto the C++ ``ProgramSheet``
    struct field-for-field (see
    ``KliCAD/eeschema/auto_layout/schematic_program.h``).  Carries no
    coordinates per invariant 13 / F-S6 — the sheet seed coordinate is
    the C++ binding's job, and cross-sheet KIID resolution
    (``m_matchedEndpoint``) is the C++ side's job per invariant 11 /
    F-S4b.  The producer side carries names + the typed
    ``definition_filename`` reference; that is the entire connectivity
    contract Python owes the layout engine.

    Returns ``None`` for an XSubckt-style binding whose ``definition``
    is ``None`` — compose cannot materialize an undefined sub-sheet, and
    the C++ side will log + ignore unknown definitions.
    """
    definition = getattr(p, "definition", None)
    if definition is None:
        return None
    def_name = getattr(definition, "name", "") or getattr(p, "subckt", "") or ""
    return {
        "ref":                 p.ref,
        "definition_filename": _sanitize_subckt_filename(def_name),
        "port_map":            dict(getattr(p, "port_map", {}) or {}),
        "repeat_count":        int(getattr(p, "repeat_count", 1) or 1),
        "repeat_instances":    list(getattr(p, "repeat_instances", []) or []),
        "extra_fields":        {},
    }


def _part_to_program(part: Part) -> dict[str, Any]:
    """Serialize one Part into the ProgramPart dict shape."""
    extra_fields: dict[str, str] = {}
    # Sim.* fields land in extra_fields so the C++ materializer applies
    # them via SetText on the symbol field (creating if absent).
    for attr in ("sim_type", "sim_params", "sim_pins"):
        v = getattr(part, attr, None)
        if v:
            extra_fields[f"Sim.{attr.removeprefix('sim_').title()}"] = str(v)
    # Klicad.SpecSrc (if present on the part) — used by spec-pane goto.
    src = getattr(part, "_src", None)
    if src:
        extra_fields["Klicad.SpecSrc"] = f"{src[0]}:{src[1]}"

    return {
        "ref":           part.ref,
        "lib_id":        getattr(part, "kicad_lib_id", "") or "",
        "value":         getattr(part, "value", "") or "",
        "footprint":     getattr(part, "footprint", "") or "",
        "kicad_pin_map": dict(getattr(part, "kicad_pin_map", {}) or {}),
        "connections":   dict(getattr(part, "connections", {}) or {}),
        "extra_fields":  extra_fields,
    }


def _circuit_to_program(c: Any, mode: str) -> dict[str, Any]:
    """Build the full SchematicProgram dict from a Circuit.

    Parts pass: every Part in ``c.parts`` is serialized as a
    ``ProgramPart``.  Sheets pass: every ``SubcircuitInstance``
    (``kind == "SUBCIRCUIT"``) is additionally serialized as a
    ``ProgramSheet`` via
    ``_subcircuit_instance_to_program_sheet`` — this unlocks F-S4b /
    F-S5 Phase B / M4, which all need compose to materialize sheets.
    Instances with no ``definition`` (XSubckt-style external bindings)
    are skipped silently; the C++ side handles unknown-definition
    diagnostics.
    """
    parts = [_part_to_program(p) for p in c.parts]

    sheets: list[dict[str, Any]] = []
    for p in c.parts:
        if getattr(p, "kind", "") != "SUBCIRCUIT":
            continue
        entry = _subcircuit_instance_to_program_sheet(p)
        if entry is not None:
            sheets.append(entry)

    nets = [
        {
            "name":            n.name,
            "kind":            _net_kind_to_string(getattr(n, "kind", None)),
            "expect_external": bool(getattr(n, "expect_external", False)),
        }
        for n in c.nets.values()
    ]

    return {
        "mode":   mode,
        "parts":  parts,
        "sheets": sheets,
        "nets":   nets,
    }


def compose_schematic(
    c: Any,
    sch_path: str | Path,
    *,
    kicad: Any = None,
    mode: Literal["replace", "diff", "strict"] = "replace",
) -> dict[str, Any]:
    """Drive the F-S3 single-IPC compose entry.

    Parameters
    ----------
    c
        The Circuit to author.
    sch_path
        Target ``.kicad_sch`` path.  Sibling project file (`.kicad_pro`)
        and per-project lib tables are expected to exist already; the
        C++ side opens via ``OpenProjectFiles``.
    kicad
        An optional live ``klipy.KliCAD`` connection.  If absent, a new
        connection is created.
    mode
        Currently only ``"replace"`` is implemented.  ``"diff"`` and
        ``"strict"`` are stubbed per the 2026-06-07 GOAL.md decision
        — they raise ``NotImplementedError`` with a structured error
        message.  See "Stubbed modes" below for the intended
        implementation gist + an alternative formulation each.

    Returns the ComposeReport dict the C++ side produced.

    Stubbed modes
    -------------
    These exist as documented stubs so callers fail loudly rather than
    silently fall through to ``"replace"`` semantics.  When implemented,
    each should land as its own subphase.

    ``mode="diff"``
        **Gist.**  Read the live schematic state via
        ``klicad_native_schematic_state.list_symbols`` and
        ``list_labels``; compute ``(adds, removes, modifies)`` against
        the new ``SchematicProgram``; mutate only the delta inside one
        ``SchLayoutTransaction``.  Run a light incremental layout
        (move-only) that keeps user-tuned positions for unchanged
        parts so round-trip workflows don't re-shuffle the canvas.

        **Alternative formulation.**  Caller passes a
        ``previous_program`` argument; Python computes the delta
        client-side and sends only ``(adds, removes, modifies)`` + the
        persistent KIIDs of unchanged items.  Trades IPC chatter for
        C++-side simplicity (the C++ binding becomes a thin applier).

    ``mode="strict"``
        **Gist.**  Same as ``"replace"`` but pre-checks the live
        schematic for symbols/sheets that the program doesn't claim;
        raises rather than emit so the caller knows the live state has
        unexpected items.

        **Alternative formulation.**  Return a structured
        ``delta_report`` dict listing the unclaimed items without
        mutating; let the caller decide whether to proceed.  Avoids
        forcing an exception path on the caller's happy path.
    """
    if mode == "diff":
        raise NotImplementedError(
            "compose mode='diff' is a deferred-feature stub "
            "(2026-06-07 GOAL.md). See _compose.py docstring "
            "'Stubbed modes' for the intended implementation gist."
        )
    if mode == "strict":
        raise NotImplementedError(
            "compose mode='strict' is a deferred-feature stub "
            "(2026-06-07 GOAL.md). See _compose.py docstring "
            "'Stubbed modes' for the intended implementation gist."
        )
    if mode != "replace":
        raise ValueError(
            f"compose: unknown mode={mode!r}; valid: 'replace', "
            f"'diff' (stub), 'strict' (stub)"
        )

    from ..klicad import KliCAD

    if kicad is None:
        kicad = KliCAD()

    program = _circuit_to_program(c, mode=mode)

    sch_path_resolved = Path(sch_path).resolve()
    sch_path_str = str(sch_path_resolved)

    # F-S3 Phase C — compose owns project-shell creation.  No more
    # write_project_shell() call required at the call site.
    write_project_shell(c, sch_path_resolved)

    # One IPC call.  This is the F-S3 invariant: ``test_ipc_one_call``
    # asserts exactly one ``compose`` call lands on the C++ side.
    # The program dict travels as a Python repr embedded in the
    # run_python code body — Fork 1 (b).  pybind11 unpacks the
    # literal dict into the C++ ``compose`` entry's ``py::dict aProgram``.
    code = (
        "import klicad_native_schematic_compose as sc\n"
        f"_result_ = sc.compose({sch_path_str!r}, {program!r})\n"
        "_result_\n"
    )
    r = kicad.run_python(code)
    if not r.ok:
        raise RuntimeError(
            f"compose IPC failed: {r.exception_traceback or r.stderr or '?'}"
        )

    # The C++ side returns a dict directly; klicad-python's run_python
    # wraps it in result_repr.  ast.literal_eval is safe — the dict
    # only contains primitives + nested dicts.
    import ast
    try:
        return ast.literal_eval(r.result_repr) if r.result_repr else {}
    except Exception:
        return {"ok": False, "error": f"compose returned non-evaluable: {r.result_repr!r}"}

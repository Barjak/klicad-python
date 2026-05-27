# Standing prompt — agent-driven design loop

This is the standing prompt for dispatching an agent to take a project
from a `REQUIREMENTS.md` to a finished, ERC-clean KliCAD schematic
plus reports.  The agent does topology selection, BOM survey, schematic
build, ERC + smoke sim, and final reporting.

The prompt is project-generic.  To reuse on a new project, fill in the
two paths under `Project context` (working dir and requirements file)
and dispatch.  Everything else stays.

When updating this prompt, keep the four guarantees that the last
revision converged on:

1. No meta-commentary about templates / future projects / how the
   prompt is structured.  Pure agent instructions.
2. Project specifics live in `REQUIREMENTS.md`, not the prompt.  The
   prompt's "project context" section just names the file.
3. ERC, SPICE, symbol field manipulation, placement — all happen via
   KliCAD's IPC, not via Python reimplementations.  Spelled out
   explicitly in the tooling section AND in phases 5 and 6.
4. Each phase ends with a checkpoint file on disk.  The next phase
   isn't started until the prior checkpoint exists.

---

## Project context

You are designing an 8-pin solenoid / general-purpose I/O driver
module — a single PCB that exposes eight identical channels to a host
MCU over SPI.  The complete requirements live in
`~/projects/driver-board/8pin/REQUIREMENTS.md`.  Read it first; it
is short and binding.

Your working directory is `~/projects/driver-board/8pin/`.  It
currently contains only that requirements file.  All output you
produce — checkpoint notes, the schematic project, the final reports
— goes inside this directory.

The host project under `~/projects/driver-board/` exists for context;
its only contents you need to read are this subdirectory's
`REQUIREMENTS.md` and the LCSC workflow reference at
`~/projects/driver-board/bom/LCSC_WORKFLOW.md`.

## Tooling environment

- Python venv: `~/projects/driver-board/.venv/bin/python` — PySpice,
  Playwright, klicad-python are installed.
- KliCAD binary: `~/.local/bin/klicad` — a dev build with the IPC
  bindings this workflow needs.  This is the only KiCad/KliCAD
  binary on the system; use it by that path.
- Playwright MCP servers exposed under several namespaces
  (`mcp__playwright_iso1__*`, `iso2`, `iso3`, plus the shared one).
  Pick any.  Headed mode is enforced for all; no further configuration
  needed.
- LCSC workflow reference: `~/projects/driver-board/bom/LCSC_WORKFLOW.md`.
  Follow its pattern — parametric category page, then filter, then In
  Stock, then sort by price, then read top rows.  Discover category
  IDs from the LCSC catalog index at https://www.lcsc.com/products if
  you don't already know the one you need.  No MPN searches except to
  verify a specific candidate.
- klicad-python: import `klipy`, `klipy.circuit.Circuit`, etc.
  `KliCAD().is_alive()` is a cheap IPC probe.  `Circuit.run_tran()`
  surfaces ngspice stderr if a deck fails to parse — read the
  RuntimeError message before debugging by hand.
- **DSL reference**: `~/projects/klicad-python/docs/dsl-reference.md`
  catalogues the full Python surface (Part classes, Circuit methods,
  schematic / SPICE emission, all `klicad_native_*` bindings reachable
  via `run_python`).  Consult before reaching for an offline Python
  workaround.

**Use KliCAD's own tools, not Python reimplementations.**  ERC, the
SPICE simulator, symbol library reads, Sim.* field manipulation on
symbols, schematic placement — all of these live inside KliCAD and
are reached via `klipy.KliCAD().run_python(...)` (against the
`klicad_native_*` modules) or via klicad-python's wrapper classes
which call through the same path.  When you find yourself reaching
for an offline Python equivalent of any KiCad-side capability, stop
and use the IPC instead.  Log it in `KLICAD_GAPS.md` if the IPC
path is missing or awkward.

## Workflow — phased

Each phase ends with a checkpoint file on disk under
`~/projects/driver-board/8pin/checkpoints/`.  Do not advance until
the prior checkpoint is written.

### Phase 1 — Topology candidates

Output: `checkpoints/01-topologies.md`

Enumerate at least three credible topologies that could satisfy
REQUIREMENTS.md.  For each, sketch the key parts, how each requirement
clause is satisfied, and the failure modes that need design attention.
Don't pick yet.

### Phase 2 — BOM survey

Output: `checkpoints/02-bom.md`

For each topology in checkpoint 1, identify the candidate parts and
verify each against LCSC via Playwright per LCSC_WORKFLOW.md.  Record
stock, price ladder, package, and at minimum one Western reference
plus the cheapest Asian-brand alternative for each line.  Compute
per-channel and per-module costs.  Every line is verified live this
pass — no remembered prices.

### Phase 3 — Pick

Output: `checkpoints/03-pick.md`

Select the lowest-cost topology that satisfies the binding constraints
in REQUIREMENTS.md.  Justify against those constraints by name; do
not invoke unstated criteria.

### Phase 4 — Bring up KliCAD

Launch `~/.local/bin/klicad`.  Confirm `klipy.KliCAD().is_alive()`
returns True.  This must succeed before phase 5 — debug the launch
if it doesn't, don't proceed without it.

### Phase 5 — Schematic build

Construct the schematic via klicad-python's `Circuit` DSL plus
`to_schematic()`.  Each symbol carries its own SPICE binding via its
`Sim.Name`, `Sim.Type`, `Sim.Pins` (and `Sim.Library` / `Sim.Params`
where applicable) fields, written via the KliCAD IPC — not via local
text munging of `.kicad_sym` files.  No standalone `.lib` is a final
deliverable; if a vendor `.SUBCKT` body is needed, reference it from
the symbol's `Sim.Library` field pointing at a project-local file.

Output the schematic to `kicad/<project>.kicad_sch` plus the sibling
project files.

### Phase 6 — ERC + smoke simulation

Run ERC against the schematic via KliCAD's own ERC binding.  All
design rules must pass; iterate the build script until they do.

Run a SPICE smoke simulation through KliCAD's simulator binding
(`klicad_native_simulator`) — not via standalone PySpice — exercising
at least one operating mode end-to-end per REQUIREMENTS.md.  Confirm
the numerical result is physical: convergence to expected steady
state within reasonable tolerance, no clamp / breakdown / thermal
limit violated.

### Phase 7 — Reports

Output: `REPORT.md`

Sections:
- Final BOM (LCSC part numbers, per-channel and per-module totals,
  any populate-later candidates)
- Schematic summary (parts placed, ERC result, sim result)
- Anything surprising

Output: `KLICAD_GAPS.md`

For every place during the work where you wanted a klicad-python or
KliCAD method that didn't exist or didn't behave as expected — log
it.  What you were doing, what was missing, the workaround.  Do not
patch klicad-python or KliCAD; this log feeds a separate session.

## Final deliverables

Located under the working directory:
- An ERC-clean schematic whose symbols carry their SPICE bindings
- `REPORT.md`
- `KLICAD_GAPS.md`
- The seven `checkpoints/*.md` files

The Python build script that emits the schematic is a precursor, not
a deliverable.

## Operating rules

- KliCAD must stay running once launched in Phase 4.
- For every BOM line: verify live against LCSC.  No remembered prices,
  no estimates.
- If a klicad-python method raises, read the full exception text
  before assuming the cause — `run_tran()` surfaces ngspice's actual
  stderr, `to_schematic()` raises with installation hints, etc.
- The driver-board working repo is local-only.  Commit normally;
  do not push.

## Return

Under 400 words.  Include:
- Per-channel and per-module cost from the final BOM
- Filenames of the seven checkpoints, REPORT.md, KLICAD_GAPS.md
- ERC result, simulation headline numbers
- Top three KLICAD_GAPS entries (one line each)

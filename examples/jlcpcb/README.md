# JLCPCB fabrication export

KliCAD tooling + reference data for producing JLCPCB-ready Gerber/drill
output. Captured here "for dev purposes" — it's adjacent to the core
KliCAD fork rather than central to it.

## Files

- **`export_jlcpcb.py`** — the export algorithm. Drives a running KliCAD
  instance to: load the board, run DRC (abort on errors), export Gerbers
  + drill with JLCPCB's recommended settings, and zip the result.
  Reproduces JLCPCB's official KiCad-9 help-article procedure.
- **`JLCPCB_2layer.kicad_dru`** — a corrected JLCPCB design-rule set for
  2-layer boards. Copy into a project as `<project>.kicad_dru` before
  running DRC.

## Why a "corrected" rule file

The community `.kicad_dru` files for JLCPCB are all descendants of one
gist (darkxst → denniskupec → labtroll → EddyBeaupre). Cross-checking
the lineage against the official `jlcpcb.com/capabilities` page found:

| Issue | Community files | Official | Fix here |
|---|---|---|---|
| Annular ring, PTH, 2-layer | 0.075 mm (labtroll/darkxst lineage) | **0.20 mm** | 0.20 mm |
| SMD pad-to-pad, diff nets | 0.127 mm (all of them) | **0.15 mm** | 0.15 mm |
| Track width/space, edge clr, holes | over-strict by 1.5–2× in older files | per capabilities | per capabilities |

The 0.075 mm annular ring is the dangerous one — it green-lights rings
less than half the JLCPCB minimum on the theory that JLCPCB silently
enlarges them, which their own docs say does not happen for single
boards. `JLCPCB_2layer.kicad_dru` here is the EddyBeaupre file (the most
accurate and most recent of the lineage) with the systemic 0.127 mm
SMD-pad-to-pad under-spec raised to the official 0.15 mm.

For multilayer boards the differences are: track width/space 0.09 mm
(vs 0.10 mm) and PTH annular ring 0.15 mm (vs 0.20 mm) — see the
EddyBeaupre `JLCPCB_Multilayer.kicad_dru` upstream, or the official
capabilities page.

## Export settings (what `export_jlcpcb.py` applies)

Gerbers: Protel filename extensions, extended X2 format, netlist
attributes, board edge plotted on every layer, zone fills checked
before plotting.

Drill: Excellon, absolute origin, millimetres, decimal zeros, alternate
oval-hole mode, PTH+NPTH merged into one file.

These match the official JLCPCB KiCad-9 guide. Most are already the
`kicad_native_export_drill` / `export_gerbers` binding defaults.

## Caveat

`export_jlcpcb.py` loads the board into the editor itself before
exporting. This is required: `kicad_native_export_gerbers` and
`export_drill` go through `PCBNEW_JOBS_HANDLER::getBoard()`, which in
GUI mode ignores the job's filename and operates on whatever board the
editor holds. `kicad_native_drc.run` was fixed to compensate
internally; the two export bindings have not been — hence the explicit
`open_board` in this script.

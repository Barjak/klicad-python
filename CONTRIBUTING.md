# Contributing to klicad-python

This is the **local fork** of `kicad-python` ([upstream]). It tracks upstream
for the core IPC bindings and adds an opinionated layer on top — the
`kipy.klicad.*` modules — designed to be driven by LLMs as well as humans.

[upstream]: https://gitlab.com/kicad/code/kicad-python

For day-to-day bug reports and minor fixes, see the [Contributing section of
the README](README.md#contributing). This file documents the *design
posture* of the local fork — the principle and the corollaries that should
shape every architectural decision in `kipy/klicad/`.

## The thin-layer principle

> **klicad-python is a thin orchestration layer between an LLM client and
> KiCad.** Application logic — resolution, validation, semantic
> interpretation of KiCad or SPICE schemas — should live on the C/C++ side
> (in KiCad's own classes), not in Python. Python's job is to marshal
> intent into KiCad calls and answers back. Local code in `kipy/` is debt
> we pay forever; logic in KiCad is amortized across the KiCad project.

The single rule: **when in doubt, ask KiCad, don't reimplement KiCad.**

## Corollaries

These follow from the principle and should be checked against every PR
touching `kipy/klicad/`:

1. **Defer to the authority, don't double-check it.**
   If KiCad will reject a bad pin map at placement time, klicad-python
   re-validating it first only adds drift risk and code mass without
   preventing the real error. Validate the *glue* (does the user's intent
   parse, does the IPC payload assemble), not the *content* (does this
   subckt name exist, does this pin map match the symbol — KiCad knows).

2. **Source of truth lives in KiCad files, not in Python data structures.**
   Mappings, aliases, pin lists, alias tables — if KiCad has a place to
   store them (Sim.* fields on symbols, `sym-lib-table`, `.kicad_sym`,
   `.lib`), do *not* invent a parallel place. Don't ship JSON alias
   libraries or curated lookup tables that KiCad already owns the
   canonical form of.

3. **Schema parsing belongs on the C++ side.**
   If you find yourself writing a parser for a KiCad or SPICE file format
   in Python, **stop**. KiCad already parses these (`SPICE_LIBRARY_PARSER`,
   `SCH_IO_KICAD_SEXPR`, etc.). Expose the existing C++ parser over IPC
   or via the embedded Python interpreter (RunPython + pybind11 binding)
   instead. A Python reimplementation means that any future GUI feature
   in KiCad that needs the same parsed info would have to call back into
   Python — backward. Single implementation, on the side that owns it.

4. **Cost of code lives in the bridge, value lives in the system being
   bridged.** Every line in `kipy/klicad/` is something we maintain.
   Every line in KiCad is something KiCad's maintainers maintain for us.
   Prefer the latter when the choice is real.

5. **LLM friendliness comes from a single source of truth.**
   Two validators (Python pre-check + C++ canonical check) produce two
   error messages for the same problem. The LLM gets confused about
   which to fix. One authoritative error from KiCad is better than two
   helpful-looking errors with subtly different wording.

6. **Iteration speed is the legitimate exception.**
   When prototyping an algorithm — placement, routing, pin alignment —
   iterating in Python is fine and often necessary. The cycle of
   "edit → rebuild KiCad → test" is too slow for early design. **But:**
   once the algorithm is stable, **promote it to C++**. The Python
   prototype is scaffolding, not the finished product. Flag any
   algorithm-in-Python as "prototype, pending C++ promotion" in code
   comments + the PR description.

7. **The KiCad GUI is part of the user-visible loop.**
   Schematics produced by klicad-python are read by humans (sometimes by
   the LLM itself, multimodally). Anything that renders correctly in
   KiCad — symbols, fields, properties — is real. Anything that only
   passes a Python unit test but is invisible/wrong in the GUI is not
   shipped.

8. **Errors travel up to the LLM and from there to the user.**
   They should be sentence-complete, name the cause, suggest the action,
   and not require reading source. Single source, no double-bookkeeping.

## When the principle bites — practical decision template

Before adding code in `kipy/klicad/`, answer these four questions in the
PR description. If you can't answer (1) with "klicad-python," you almost
certainly shouldn't be writing the code there.

1. **Who owns the underlying logic?** KiCad / ngspice / klicad-python.
2. **What's the maintenance cost in Python?** Lines of code, drift risk,
   version-skew risk, parallel-implementation risk.
3. **What class of errors does this catch?** Be specific. "Subckt name
   missing from .lib" is a class. "It feels safer" is not.
4. **What would a thin client do?** If the answer is "call KiCad over
   IPC," do that. If the answer is "write a parser," ask whether the
   parser already exists on the C++ side first.

## Worked examples

### ✅ Good fit for `kipy/klicad/`

- A Python orchestrator that calls `kicad.run_python(...)` to instantiate
  symbols and then issues `place_part(...)` / `add_label(...)` commands.
- A canonical-circuit DSL (`Circuit`, `Part`, etc.) that lets an LLM
  build up a circuit description in Python and then emit it as a SPICE
  deck via the existing emitter + as a schematic via `to_schematic()`
  (which talks to live KiCad).
- A friendly error wrapper that turns `kipy.errors.ConnectionError`
  into "KliCAD isn't running; start KiCad with the IPC API enabled."
- `Circuit.run_tran()` — wraps the well-known PySpice singleton/state
  footguns. Not reimplementing ngspice; just smoothing the rough edges
  of PySpice's surface. (ngspice has no IPC, so this is genuinely a
  thin wrapper around a library we link into.)

### ❌ Bad fit — should live on the C++ side instead

- A Python `.SUBCKT` parser. KiCad already has `SPICE_LIBRARY_PARSER`.
  Expose that to RunPython and call it.
- A Python `.kicad_sym` parser. KiCad already has `SCH_IO_KICAD_SEXPR`.
  Read via `kicad.run_python(...)` or via a new IPC endpoint.
- A curated alias library mapping `(kicad_lib_id, subckt_name)` pairs
  to hand-written pin maps. KiCad symbols carry `Sim.Name` /
  `Sim.Type` / `Sim.Pins` fields for exactly this purpose. Use those;
  if a stock symbol lacks them, ship a derived `.kicad_sym` that adds
  them — let KiCad read the data in its native format.
- A Python `sym-lib-table` resolver. KiCad already does this resolution
  internally. Ask KiCad.

### 🟡 Pragmatic exceptions (flag explicitly)

- **Static config files that are stable and small.** If parsing the file
  in C++ would add more code than reading it in Python, and the file
  format doesn't change across KiCad versions, a thin Python reader can
  be justified. Document the exception in a code comment naming the
  principle being relaxed and why.
- **Prototypes** (see corollary 6).

## Submitting changes

For PRs that touch `kipy/klicad/`:

- State which corollary the change supports (or which exception it
  invokes, per the "🟡 Pragmatic exceptions" section).
- If the change adds Python code that *could* live on the C++ side, the
  PR description must justify the choice and link to the equivalent
  C++ work (filed as a TODO or as an issue on the KliCAD repo).
- Tests should reflect what changes — if you're moving logic to C++,
  the Python tests for the old logic should be removed or replaced
  with tests of the IPC integration, not left in place.

[upstream-readme]: README.md

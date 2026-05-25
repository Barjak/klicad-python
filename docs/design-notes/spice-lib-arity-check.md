# Design note — `.SUBCKT` arity check via the C++ side

**Status:** plan; no code yet.
**Owns the work next session:** klicad-python parent + kicad submodule.

## Why this exists

An earlier patch added a Python `.SUBCKT` parser (`_subckt_lib.py`) and a
validate-time arity check that used it. That violated the thin-layer
principle (see [CONTRIBUTING.md](../../CONTRIBUTING.md)):
klicad-python had its own parallel implementation of a SPICE library
parser that KiCad already ships (`SPICE_LIBRARY_PARSER` →
`SIM_LIBRARY_SPICE` → `SIM_MODEL` with `GetPinCount()` /
`GetPinNames()`).

The parser was removed; the check needs to come back, but as a thin
client of KiCad's parser, not a reimplementation.

## What the eventual surface looks like

From the user's perspective:

```python
c = Circuit("my_module")
c.add_model_lib("vendor/eta7014s2g.lib")
c.add(XSubckt("U1", ["VIN", "VOUT", "EN", "GND"], subckt="ETA7014S2G"))
c.validate_all()  # validates against KiCad's parsed model library
```

When a `kipy.KiCad` instance is reachable, `validate_all()` would issue
a `RunPython` call that uses `SIM_LIBRARY_SPICE` to parse each configured
model_lib_path and return a `{name: pin_count}` map. The check then runs
in Python:

- subckt name not in any lib → warning
- node count mismatches `SIM_MODEL.GetPinCount()` → error

When `kipy.KiCad` is **not** reachable, the check is skipped with a
notice in the warnings list. The validate-time error message tells the
user to start KiCad if they want this class of check to fire.

This mirrors what `to_schematic()` already does for symbol placement —
KiCad must be running for the authoritative behaviour; without it, we
degrade gracefully.

## What needs to happen on the C++ side

KiCad's `SIM_LIBRARY_SPICE` is not currently exposed in the Python
binding surface that `RunPython` lives inside. To unlock the thin-layer
implementation, one of the following:

1. **Add pybind11 bindings for `SIM_LIBRARY_SPICE`,
   `SPICE_LIBRARY_PARSER`, and the relevant `SIM_MODEL` accessors
   (`GetPinCount`, `GetPinNames`).**
   This is the smallest, most reusable lift — once bound, the same
   classes serve future work (gap #8 needs `GetPinNames()` too).

2. **Add a dedicated IPC handler** that takes a list of `.lib` paths
   and returns `[(name, pin_count, pin_names), ...]`.
   Simpler to design, but a one-off — doesn't help gap #8 directly.
   Argues for option 1.

**Recommended:** option 1. Same effort, broader payoff. The next
session should:

- Identify the pybind binding entry point in `kicad/common/` (or
  wherever `RunPython`'s interpreter is set up). Search for existing
  `py::class_<...>` declarations to find the pattern in use.
- Bind `SIM_LIBRARY_SPICE::SIM_LIBRARY_SPICE(...)`,
  `SPICE_LIBRARY_PARSER::ReadFile(...)`, and the `SIM_MODEL` accessor
  set we need: `GetPinCount`, `GetPinNames`, `GetParam`, and the
  bookkeeping methods to iterate `SIM_LIBRARY::GetModels()`.
- Rebuild the kicad submodule. Test by hand via `kicad.run_python(...)`.

## What the Python side does after that

```python
# In Circuit.validate_all(), conditional block:
if self._kicad_client is not None:  # set if user passes kicad=...
    code = "\n".join([
        "import kicad_native_simulator as sim",
        "import json",
        f"lib_paths = {self.model_lib_paths!r}",
        "result = {}",
        "for path in lib_paths:",
        "    lib = sim.SimLibrarySpice()",
        "    sim.SpiceLibraryParser(lib, True).ReadFile(path)",
        "    for model_name, model in lib.GetModels().items():",
        "        result[model_name] = model.GetPinCount()",
        "json.dumps(result)",
    ])
    r = self._kicad_client.run_python(code)
    if r.ok:
        registry = json.loads(r.result_repr)
        # ...then run the arity check as before, using `registry`
```

The exact API of the bound classes depends on the binding choices, but
the shape is: marshal paths in, marshal the parsed dict out. No SPICE
parsing on the Python side.

## What `validate_all()` should do when KiCad is unreachable

Add a single line to the warnings list:

```
XSubckt arity check skipped — no KiCad client available.  Mismatches
will surface at ngspice runtime instead.  To enable validate-time
checks, pass kicad=KiCad() to Circuit(...) (requires a running KliCAD).
```

This keeps the failure mode honest: the user knows the check didn't
run, and knows what to do about it.

## Out of scope for this work

- Pin-name (not pin-count) cross-check against `XSubckt.kicad_pin_map`.
  That's part of gap #8 (the KiCad-symbol side); same pybind work
  unlocks it.
- Validating models defined inline (`.MODEL` cards via `add_model`).
  Those are already inline in the deck and ngspice catches mismatches
  immediately at the `.model` parse — adding a Python-side check
  duplicates that.

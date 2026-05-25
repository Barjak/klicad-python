"""Lightweight SPICE `.SUBCKT` definition parser.

Used by Circuit.validate_all() to catch XSubckt name / arity mismatches at
validate-time instead of letting them surface as confusing ngspice errors
("subcircuit not found", "too few terminals") at simulation runtime.

Parses just enough of the SPICE deck syntax to find `.SUBCKT` lines and
count their terminals:

  * Lines starting with '*' are comments (skipped).
  * Lines starting with '+' are continuations of the previous logical line.
  * End-of-line comments via ';' or ' $ ' are stripped from the right.
  * `.SUBCKT name pin1 pin2 ... [paramN=valueN ...]` — pins are tokens
    after the name up to the first token containing '='.

Returns a dict mapping subckt name → pin count.  Multiple definitions of
the same name in the same file: last one wins (matches ngspice behavior).
"""

from __future__ import annotations

from pathlib import Path


def parse_subckt_lib(path: str | Path) -> dict[str, int]:
    """Parse `.SUBCKT` definitions from a SPICE library file.

    Args:
        path: filesystem path to a SPICE .lib / .cir / .sp / .mod file.

    Returns:
        Dict mapping subckt name → pin count.  Empty if the file does not
        exist or can't be read.  Quiet by design — `validate_all()` already
        warns when a configured model_lib_path doesn't exist on disk.
    """
    p = Path(path)
    if not p.exists():
        return {}
    try:
        text = p.read_text(errors="replace")
    except OSError:
        return {}

    # Stitch continuation lines, strip comments.
    logical: list[str] = []
    for raw in text.splitlines():
        s = raw.rstrip()
        if not s or s.lstrip().startswith("*"):
            continue
        # End-of-line comment via ';' (ngspice extension) or ' $ ' (HSPICE-style).
        for sep in (";", " $ "):
            i = s.find(sep)
            if i >= 0:
                s = s[:i].rstrip()
        if not s:
            continue
        if s.lstrip().startswith("+"):
            if logical:
                logical[-1] += " " + s.lstrip()[1:].lstrip()
            # else: dangling continuation; ignore
        else:
            logical.append(s)

    out: dict[str, int] = {}
    for line in logical:
        tokens = line.split()
        if not tokens:
            continue
        if tokens[0].lower() != ".subckt":
            continue
        if len(tokens) < 2:
            continue
        name = tokens[1]
        # Pin tokens: everything after the name, until the first token that
        # contains '=' (i.e. a param=value assignment).  Strip trailing
        # 'params:' marker if present.
        pin_count = 0
        for tok in tokens[2:]:
            if tok.lower() == "params:":
                break
            if "=" in tok:
                break
            pin_count += 1
        out[name] = pin_count
    return out

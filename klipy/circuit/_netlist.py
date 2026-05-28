"""Thin wrapper around `kicad-cli sch export netlist`.

Orchestration only: emit a schematic via the existing entry point, shell
out to kicad-cli for the netlist, return the resulting `.net` text.  All
netlist content logic lives in KliCAD's C++ netlist_exporter_kicad.cpp.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory


_BUILD_CLI = Path.home() / "projects" / "KliCAD" / "build" / "kicad" / "kicad-cli"


def _find_kicad_cli() -> str:
    path = shutil.which("kicad-cli")
    if path:
        return path
    if _BUILD_CLI.exists():
        return str(_BUILD_CLI)
    raise RuntimeError(
        "kicad-cli not found on PATH or at "
        f"{_BUILD_CLI}.  Install KliCAD or set PATH."
    )


def netlist_from_sch(sch_path: str | Path) -> str:
    """Run `kicad-cli sch export netlist` on an already-written .kicad_sch.

    Used by callers (e.g. ``to_schematic``'s C.6 ratsnest hook) that
    already have a schematic on disk and want the netlist text without
    re-running schematic emission.
    """
    cli = _find_kicad_cli()
    sch = Path(sch_path)
    if not sch.exists():
        raise FileNotFoundError(f"schematic not found: {sch}")
    net = sch.with_suffix(".net")
    proc = subprocess.run(
        [cli, "sch", "export", "netlist",
         "--format", "kicadsexpr", "-o", str(net), str(sch)],
        capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"kicad-cli netlist export failed (rc={proc.returncode}):\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
    return net.read_text()


def to_netlist(circuit, schematic_dir: str | Path | None = None) -> str:
    """Emit `circuit` as a KiCad-sexpr netlist string via kicad-cli."""
    ctx = TemporaryDirectory() if schematic_dir is None else None
    out_dir = Path(ctx.name if ctx else schematic_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        sch = out_dir / f"{circuit.name or 'circuit'}.kicad_sch"
        circuit.to_schematic(str(sch))
        return netlist_from_sch(sch)
    finally:
        if ctx is not None:
            ctx.cleanup()

"""A.12: live-KliCAD verification that multi-channel (repeat=N) works on
the PCB side — i.e. that the N replicated body sheets produced by a
`repeat=N` Circuit get materialized as N peer rule areas by KliCAD's
PCB Multi-Channel placement tool.

End-to-end shape we want pinned (PCB side of the R7 multi-channel feature):

1. Refactor a copy of the 8-channel driver-board archive
   (`~/projects/driver-board/8pin-archive-2026-05-26/`) so that instead
   of 8 explicit per-channel bodies the top-level circuit declares one
   body sheet with `repeat=8`.
2. Regenerate the schematic + netlist from that Circuit into a `tmp_path`
   copy of the archive (NEVER mutate the archive itself, per the
   immutable-design-archive constraint).
3. Push the freshly-emitted netlist into the existing PCB.
4. Run the PCB Multi-Channel "Generate Placement Rule Areas" action.
5. Open the resulting board file and assert that exactly 8 peer rule
   areas — `(zone ... (placement (enabled yes) (sheetname "..."))` —
   appear, one per replicated body sheet.

WHAT THIS TEST CURRENTLY CHECKS
-------------------------------

This first chunk is a **scaffold**: it captures the test intent and the
environment-skip discipline, but the body of the test is gated behind a
`pytest.mark.skip` because two upstream gaps block it from running:

GAP 1: `kicad-cli pcb update --netlist <netfile>` does not exist in the
       current KliCAD 10.99 build.  `kicad-cli pcb --help` only exposes
       `{drc,export,import,render,upgrade}`; there is no subcommand that
       takes a `.net` file and rewrites an existing `.kicad_pcb` with
       the new footprint set / netnames.  Pushing the netlist into the
       PCB headlessly therefore requires either:
         (a) a new `pcb update` subcommand on `kicad-cli`, or
         (b) an IPC binding that performs the equivalent of the GUI's
             "Update PCB from Schematic" action against the loaded
             board.
       Tracked: needs binding for `pcb update from netlist` (mirrors
       the strict-xfail style used in
       `tests/test_multi_channel_netlist.py` for the R3.3 per-slot
       netlist gap).

GAP 2: `pcbnew.Multichannel.generatePlacementRuleAreas` IS a registered
       TOOL_ACTION (see `pcbnew/tools/pcb_actions.cpp:2517` and the
       `klicad_native_pcb_actions.run_action` binding in
       `pcbnew/api/bindings_pcb_actions.cpp`), so the action itself is
       reachable.  However it opens a modal dialog
       (`dialog_multichannel_generate_rule_areas`), and modal dialogs
       hang the IPC server (see project memory:
       `feedback_klicad_modal_dialogs.md`).  Driving this action
       headlessly requires either:
         (a) a non-modal entry point on `MULTICHANNEL_TOOL` that takes
             the dialog's settings as parameters, or
         (b) routing through `klicad_native_gui.click_dialog_button`
             after the action fires, but the dialog's first-pass
             discovery state is non-trivial to drive blind.
       Tracked: needs binding for headless `generatePlacementRuleAreas`
       (or a parameterized C++ entry point bypassing the dialog).

When either gap is closed we lift the corresponding `pytest.mark.skip`
and turn the body of `test_pcb_peer_areas_materialize` into a real
assertion.  The skip reason printed by `pytest -rs` documents which gap
is blocking the test on the current build.

ENVIRONMENT SKIP MATRIX
-----------------------

The test self-skips (collects-but-skips, never errors) when any of
these are missing on the runner, mirroring the pattern in
`tests/test_multi_channel_netlist.py:81-96`:

- `kicad-cli` is not on PATH and not at
  `~/projects/KliCAD/build/kicad/kicad-cli`.
- The user's design archive
  (`~/projects/driver-board/8pin-archive-2026-05-26/`) is not present.
- KliCAD's IPC socket is unreachable (no running instance, or the
  pynng connect raises).

Both runtime gaps (kicad-cli pcb update, headless multichannel) are
expressed as `pytest.mark.skip` decorators with the gap-tracking
reasons above so `pytest -rs` surfaces them.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


_BUILD_CLI = Path.home() / "projects" / "KliCAD" / "build" / "kicad" / "kicad-cli"
_ARCHIVE = (
    Path.home() / "projects" / "driver-board" / "8pin-archive-2026-05-26"
)


def _kicad_cli() -> str | None:
    cli = shutil.which("kicad-cli")
    if cli:
        return cli
    if _BUILD_CLI.exists():
        return str(_BUILD_CLI)
    return None


def _kicad_cli_supports_pcb_update(cli: str) -> bool:
    """Probe whether `kicad-cli pcb` exposes an `update` subcommand.

    On KliCAD 10.99 the pcb subcommand surface is currently
    ``{drc, export, import, render, upgrade}`` — no `update`.  This
    probe is the runtime check that gates GAP 1 above.
    """
    proc = subprocess.run(
        [cli, "pcb", "--help"], capture_output=True, text=True, timeout=15,
    )
    return "update" in (proc.stdout + proc.stderr).split("{", 1)[-1].split("}", 1)[0]


def _build_repeat8_circuit():
    """Build the analogue of the 8pin archive driver as a repeat=8 Circuit.

    The archive's `build/build_module.py` lays down 8 explicit per-channel
    bodies — one NMOS + gate-R + pull-down + freewheel-D + Mode-B-R per
    index in ``range(N_CHAN)``.  The multi-channel equivalent declares
    that body once as a child Circuit with bus ports
    ``DRV_G[0..7]`` / ``DRV_B[0..7]`` / ``CH[0..7]`` and one instance of
    each per-channel part wired to the matching bit; the top-level
    parent then carries a single ``ch.instance("U_CH", repeat=8, …)``.

    We deliberately keep the body minimal (one R per slot is enough to
    drive multichannel placement on the PCB), modeled on
    ``_build_multi_channel_circuit`` in
    ``tests/test_multi_channel_netlist.py``.  The structural goal is
    "N replicated body sheets," not "exact electrical parity with the
    archive."
    """
    from klipy.circuit import Circuit, R

    repeat = 8
    ch = Circuit(
        "ch_body",
        ports=[f"GATE[0..{repeat - 1}]", f"OUT[0..{repeat - 1}]"],
    )
    for k in range(repeat):
        ch.add(R(f"R{k + 1}", f"GATE[{k}]", f"OUT[{k}]", value="10k"))
    top = Circuit("driver8_mc")
    top.add(
        ch.instance(
            "U_CH",
            repeat=repeat,
            GATE=f"GBUS[0..{repeat - 1}]",
            OUT=f"OBUS[0..{repeat - 1}]",
        )
    )
    return top


@pytest.fixture
def pcb_peers_env(tmp_path: Path):
    """Stage a writable copy of the 8pin archive into tmp_path and
    confirm the prerequisites are present.

    Skips (collect-time-clean) when any of:
      - kicad-cli is unavailable
      - the design archive is absent
      - KliCAD IPC isn't reachable
    """
    cli = _kicad_cli()
    if cli is None:
        pytest.skip(
            "kicad-cli not available (PATH or "
            "~/projects/KliCAD/build/kicad/kicad-cli)"
        )

    if not _ARCHIVE.is_dir():
        pytest.skip(
            f"design archive {_ARCHIVE} not present on this machine"
        )

    # IPC reachability — mirror the try/except shape from
    # tests/test_multi_channel_netlist.py so a missing KliCAD process
    # becomes a clean skip, not a hard error.
    try:
        from klipy import KliCAD
        from klipy.errors import ConnectionError as KipyConnectionError
    except Exception as exc:  # pragma: no cover — import surface drift
        pytest.skip(f"klipy import failed: {exc}")

    try:
        k = KliCAD(timeout_ms=10_000)
        _ = k.get_version()
    except KipyConnectionError as exc:
        pytest.skip(f"KliCAD IPC unreachable: {exc}")
    except RuntimeError as exc:
        if "KliCAD" in str(exc) or "IPC" in str(exc):
            pytest.skip(f"KliCAD IPC unreachable: {exc}")
        raise
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"KliCAD probe failed: {exc}")

    work = tmp_path / "driver8_mc"
    shutil.copytree(_ARCHIVE, work)

    # Locate the existing PCB in the copy.  The archive ships a
    # schematic but no .kicad_pcb under kicad/; the test's
    # "push netlist into PCB" step would create one via kicad-cli — see
    # GAP 1.  For the scaffold we just hand the copy to the caller.
    return {
        "cli": cli,
        "work": work,
        "kicad": k,
    }


def test_repeat8_circuit_builds():
    """Smoke check that the repeat=8 Circuit constructs without error.

    This much is purely Python — no KliCAD, no kicad-cli — so it should
    run in every CI configuration regardless of the gaps above.
    """
    top = _build_repeat8_circuit()
    assert top.name == "driver8_mc"
    # One child sheet instance, declared repeat=8.  We don't assert the
    # exact attribute name here (klipy internal); just that the parent
    # carries a single child.
    children = getattr(top, "children", None) or getattr(top, "_children", None)
    if children is not None:
        assert len(list(children)) == 1


@pytest.mark.skip(
    reason=(
        "Tracked: needs binding for `kicad-cli pcb update --netlist` "
        "(GAP 1) AND a non-modal headless entry point for "
        "`pcbnew.Multichannel.generatePlacementRuleAreas` (GAP 2). "
        "See module docstring for details.  Lift this skip when "
        "either pathway lands."
    )
)
def test_pcb_peer_areas_materialize(pcb_peers_env):
    r"""When the gaps above are closed, this is the real assertion:
    8 peer rule areas appear on the regenerated PCB, one per slot.

    Outline (kept here so the post-binding work is a fill-in rather
    than a rewrite):

        from klipy.circuit._netlist import to_netlist

        top = _build_repeat8_circuit()
        work = pcb_peers_env["work"]
        cli  = pcb_peers_env["cli"]
        k    = pcb_peers_env["kicad"]

        # 1. Regenerate schematic + netlist into the tmp copy.
        sch  = work / "kicad" / "driver8.kicad_sch"
        netf = work / "kicad" / "driver8.net"
        netf.write_text(to_netlist(top, schematic_dir=sch.parent))

        # 2. Push netlist into the existing PCB (GAP 1).
        pcb  = work / "kicad" / "driver8.kicad_pcb"
        out  = work / "kicad" / "driver8_updated.kicad_pcb"
        subprocess.run(
            [cli, "pcb", "update", "--netlist", str(netf),
             "-o", str(out), str(pcb)],
            check=True, capture_output=True, text=True, timeout=120,
        )

        # 3. Open the updated PCB in the live KliCAD and run the
        #    Multi-Channel placement tool (GAP 2).
        from klipy.proto.kiapi.common.types import DocumentType
        doc = k.open_document(str(out), DocumentType.DOCTYPE_PCB)
        r = k.run_python(
            "import klicad_native_pcb_actions as pa\n"
            "pa.run_action('pcbnew.Multichannel.generatePlacementRuleAreas')"
        )
        assert r.ok, r.exception_traceback

        # 4. Re-read the saved board and count peer rule areas.
        pcb_text = out.read_text()
        peer_areas = re.findall(
            r"\(placement\s+\(enabled\s+yes\)\s+\(sheetname",
            pcb_text,
        )
        assert len(peer_areas) == 8, (
            f"expected 8 peer rule areas, got {len(peer_areas)}\n"
            f"matches: {peer_areas}"
        )
    """
    # Intentionally unreached — skipped above.
    raise AssertionError("scaffold body should be unreachable while skipped")

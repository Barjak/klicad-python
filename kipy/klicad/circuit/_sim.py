"""Run a Circuit's SPICE deck via libngspice (PySpice).

Wraps the well-known PySpice / NgSpiceShared footguns so callers don't have
to re-write them:

  * NgSpiceShared backs a singleton libngspice; state from a prior `tran`
    leaks into the next iteration unless plots are destroyed first.
  * `tran` inside `.control` does not always auto-run on `.source()`, so
    we generate a non-self-running deck (via `to_spice_deck(self_running=
    False)`) and issue `tran` explicitly.
  * PySpice raises NgSpiceCommandError on *any* non-empty stderr output —
    including ngspice's harmless "Using SPARSE 1.3 ..." informational
    line — so we catch the exception and check for the plot anyway.
  * `ng.plot_names` is a list of plot ids; the latest tran result is the
    last name starting with "tran".  `ng.plot(None, name)` returns a
    dict-like keyed by raw node / branch names, not a Vector you can iter.

The public surface is just Circuit.run_tran() (added in _circuit.py); this
module holds the implementation.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._circuit import Circuit


def _make_spy_class():
    """Build an NgSpiceShared subclass that forwards stderr to our stderr."""
    from PySpice.Spice.NgSpice.Shared import NgSpiceShared

    class Spy(NgSpiceShared):
        def send_char(self, msg, ngspice_id):
            if isinstance(msg, bytes):
                msg = msg.decode("utf-8", "replace")
            if msg.startswith("stderr"):
                sys.stderr.write(f"  [ng] {msg}\n")
            return 0

        def send_stat(self, msg, ngspice_id):  # pragma: no cover — silent
            return 0

    return Spy


def run_tran(circuit: "Circuit",
             step: str | None = None,
             stop: str | None = None,
             *,
             uic: bool | None = None,
             ng=None) -> dict[str, list[float]]:
    """Run a transient simulation and return the result vectors.

    step, stop: tran step + stop time (ngspice syntax: "10u", "50m", etc.).
        If omitted, use circuit.tran's values (which must be set).  If
        given here, override the Circuit's analyses for this run.
    uic: pass `uic` to the tran command if True.  If None, use the value
        from circuit.tran (defaults to False).
    ng: optional pre-constructed NgSpiceShared instance.  If provided,
        the caller owns the lifecycle.  If None, a fresh instance is
        created and destroyed-all'd before sourcing.

    Returns a dict mapping vector name → list[float].  Always includes
    "time" (or "frequency" for AC, etc.).  Other keys are the SPICE node
    and branch names as the simulator reported them — generally lower-cased
    versions of the Circuit's net names plus things like `<ref>#branch`.

    Raises:
        ImportError: if PySpice is not installed.
        RuntimeError: if no tran plot was produced (deck failed to converge
            or had no analysis).
    """
    # Resolve step/stop from c.tran if not given.
    if step is None or stop is None:
        ct = circuit.tran  # property added in _circuit.py
        if ct is None:
            raise ValueError(
                "run_tran(): step+stop not given and Circuit has no .tran set; "
                "either pass step=, stop= or set circuit.tran = Tran(...)."
            )
        step = step or ct.step
        stop = stop or ct.stop
        if uic is None:
            uic = bool(getattr(ct, "uic", False))
    if uic is None:
        uic = False

    # Lazy PySpice import so klicad-python doesn't hard-depend on it.
    try:
        Spy = _make_spy_class()
    except ImportError as e:
        raise ImportError(
            "run_tran() requires PySpice.  Install with: pip install PySpice"
        ) from e

    # Build a non-self-running deck so the explicit `tran` below isn't
    # racing or duplicating a `.control / .endc` block in the deck.
    deck = circuit.to_spice_deck(self_running=False)

    own_ng = ng is None
    if own_ng:
        ng = Spy.new_instance()
        try:
            ng.exec_command("destroy all")
        except Exception:
            pass

    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".cir", prefix="klicad_", delete=False
    )
    tmp.write(deck)
    tmp.flush()
    tmp.close()

    try:
        ng.source(tmp.name)

        cmd = f"tran {step} {stop}" + (" uic" if uic else "")
        # PySpice may raise NgSpiceCommandError on benign stderr output;
        # the plot may still be valid.  Catch and continue.
        try:
            ng.exec_command(cmd)
        except Exception as e:
            sys.stderr.write(
                f"  [klicad.run_tran] tran raised, checking for data: {e}\n"
            )

        # Find the latest tran plot.
        plot_names = list(ng.plot_names)
        tran_plots = [p for p in plot_names if p.startswith("tran")]
        if not tran_plots:
            raise RuntimeError(
                f"run_tran(): no tran plot produced.  Available plots: {plot_names!r}"
            )
        pname = tran_plots[-1]

        plot = ng.plot(None, pname)

        # PySpice's plot is a dict-subclass keyed by node/branch name; each
        # value is a Vector whose .to_waveform() yields a numpy array.
        import numpy as np

        out: dict[str, list[float]] = {}
        for key in plot.keys():
            try:
                wf = plot[key].to_waveform()
                out[key] = np.asarray(wf, dtype=float).tolist()
            except Exception:  # pragma: no cover — defensive
                continue
        return out

    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        if own_ng:
            try:
                ng.exec_command("destroy all")
            except Exception:
                pass

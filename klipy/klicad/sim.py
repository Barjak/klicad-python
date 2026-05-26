"""SPICE simulator (ngspice) proxy.

Wraps ``kicad_native_simulator`` (Pattern B, eeschema-kiface-resident).
The simulator binding requires both the schematic editor and the
simulator frame to be up.  All methods here implicitly spawn the
simulator frame via ``kicad_native_gui.show_frame('simulator')`` before
dispatching.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ._runner import _PyRunner


class Simulator:
    """High-level driver for KliCAD's embedded ngspice simulator.

    The convenience analysis helpers (:meth:`tran`, :meth:`ac`,
    :meth:`dc`, :meth:`op`, :meth:`noise`) optionally accept a
    ``schematic`` path; if given, the schematic is opened first so the
    simulator pulls its netlist from there.
    """

    def __init__(self, runner: _PyRunner) -> None:
        self._run = runner

    # ---- frame lifecycle ------------------------------------------------

    def _ensure_sim_frame(self, schematic: Optional[str] = None) -> None:
        if schematic:
            # Spawn the schematic editor first; the simulator frame needs
            # a SCH parent to source its netlist from.
            self._run.call("kicad_native_gui", "show_frame", "schematic")
            # Open the requested schematic via the editor's open action.
            self._run.call(
                "kicad_native_sch_actions", "run_action",
                "eeschema.EditorControl.openFile",
                args={"filename": schematic},
            )
        self._run.call("kicad_native_gui", "show_frame", "simulator")

    # ---- state ----------------------------------------------------------

    def get_state(self) -> Dict[str, Any]:
        """Snapshot of simulator state.

        Keys: ``has_frame, has_simulator, is_running, current_plot,
        sim_type, sim_command, sim_finished``.
        """
        return self._run.call("kicad_native_simulator", "get_state")

    def generate_netlist_from_schematic(self) -> str:
        """Generate a SPICE netlist from the currently-loaded schematic.

        Returns the netlist as a string.  Requires an active SCH frame.
        """
        self._ensure_sim_frame()
        return str(self._run.call(
            "kicad_native_simulator", "generate_netlist_from_schematic"))

    def load_netlist(self, netlist_text: str) -> Dict[str, Any]:
        """Push a SPICE netlist into ngspice."""
        self._ensure_sim_frame()
        return self._run.call("kicad_native_simulator", "load_netlist",
                              netlist_text)

    def command(self, spice_cmd: str) -> Dict[str, Any]:
        """Run a raw ngspice command (e.g. ``'op'``, ``'setplot tran1'``)."""
        self._ensure_sim_frame()
        return self._run.call("kicad_native_simulator", "command", spice_cmd)

    # ---- typed analyses ------------------------------------------------

    def run_analysis(self, kind: str = "tran", **kwargs: Any) -> Dict[str, Any]:
        """Run a typed SPICE analysis.  See the per-kind helpers below for
        keyword argument shapes.

        :param kind: ``'tran'`` | ``'ac'`` | ``'dc'`` | ``'op'`` | ``'noise'``.
        """
        self._ensure_sim_frame()
        return self._run.call("kicad_native_simulator", "run_analysis",
                              kind=kind, **kwargs)

    def tran(self, schematic: Optional[str] = None, step: str = "1u",
             stop: str = "1m", *, start: Optional[str] = None,
             uic: Optional[bool] = None) -> Dict[str, Any]:
        """Transient analysis.

        :param schematic: optional path to a schematic to open first.
        :param step: spice time-step (e.g. ``'1u'``, ``'10ns'``).
        :param stop: simulation end time.
        :param start: optional simulation start time.
        :param uic: use initial conditions flag.
        """
        self._ensure_sim_frame(schematic)
        kwargs: Dict[str, Any] = {"step": step, "stop": stop}
        if start is not None:
            kwargs["start"] = start
        if uic is not None:
            kwargs["uic"] = uic
        return self._run.call("kicad_native_simulator", "run_analysis",
                              kind="tran", **kwargs)

    def ac(self, schematic: Optional[str] = None,
           *, type: str = "dec", npoints: str = "10",
           fstart: str = "1", fstop: str = "1Meg") -> Dict[str, Any]:
        """AC small-signal analysis.

        :param type: ``'dec'`` | ``'oct'`` | ``'lin'``.
        :param npoints: points per decade/octave (or total for ``'lin'``).
        :param fstart: start frequency (Hz, with SI suffixes).
        :param fstop: stop frequency.
        """
        self._ensure_sim_frame(schematic)
        return self._run.call(
            "kicad_native_simulator", "run_analysis",
            kind="ac", type=type, npoints=npoints, fstart=fstart, fstop=fstop,
        )

    def dc(self, schematic: Optional[str] = None,
           *, source: str, start: str, stop: str, step: str) -> Dict[str, Any]:
        """DC sweep analysis."""
        self._ensure_sim_frame(schematic)
        return self._run.call(
            "kicad_native_simulator", "run_analysis",
            kind="dc", source=source, start=start, stop=stop, step=step,
        )

    def op(self, schematic: Optional[str] = None) -> Dict[str, Any]:
        """Operating-point analysis (no parameters)."""
        self._ensure_sim_frame(schematic)
        return self._run.call("kicad_native_simulator", "run_analysis", kind="op")

    def noise(self, schematic: Optional[str] = None, *, output: str,
              src: str, type: str = "dec", npoints: str = "10",
              fstart: str = "1", fstop: str = "1Meg") -> Dict[str, Any]:
        """Noise analysis.

        :param output: output node, e.g. ``'V(out)'``.
        :param src: noise source name.
        """
        self._ensure_sim_frame(schematic)
        return self._run.call(
            "kicad_native_simulator", "run_analysis",
            kind="noise", output=output, src=src,
            type=type, npoints=npoints, fstart=fstart, fstop=fstop,
        )

    # ---- result access --------------------------------------------------

    def stop(self) -> Dict[str, Any]:
        """Halt an in-flight simulation."""
        return self._run.call("kicad_native_simulator", "stop")

    def list_plots(self) -> List[str]:
        """Return cached plot names (minimum-viable: current plot only)."""
        return list(self._run.call("kicad_native_simulator", "list_plots"))

    def list_vectors(self, *, plot: str = "") -> List[str]:
        """Return signal vector names in the named plot.

        Empty ``plot`` means the current plot.
        """
        return list(self._run.call("kicad_native_simulator", "list_vectors",
                                   plot=plot))

    def get_vector(self, name: str, *, plot: str = "") -> Dict[str, Any]:
        """Fetch a named vector (with x-axis) from ngspice.

        Returns ``{name, kind, xaxis_name, xaxis, data}``; ``data`` is a
        list of floats for real vectors, or ``{real, imag}`` lists for
        complex vectors (e.g. AC analysis output).
        """
        return self._run.call("kicad_native_simulator", "get_vector",
                              name, plot=plot)

    def __repr__(self) -> str:
        return "<klicad.Simulator>"

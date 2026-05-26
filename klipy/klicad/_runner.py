"""Internal helpers for invoking ``KliCAD.run_python`` from façade proxies.

Each public proxy in :mod:`klipy.klicad` ultimately routes through
:class:`_PyRunner.call`, which:

1. Builds a small Python snippet that imports the right ``kicad_native_*``
   module and invokes a single function.
2. Calls ``KliCAD.run_python(snippet)``.
3. Either parses ``result_repr`` with :func:`ast.literal_eval` and returns
   the native value, or raises :class:`KliCADError` carrying the embedded
   traceback if the snippet didn't run cleanly.

Arguments are always passed through :func:`repr` so that user-supplied
strings can't escape the snippet (e.g. closing the call early or splicing
in additional Python statements).
"""

from __future__ import annotations

import ast
from typing import Any, Iterable, Mapping, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from klipy.klicad import KliCAD, RunPythonResult


class KliCADError(RuntimeError):
    """Raised when a ``run_python`` invocation didn't complete cleanly.

    The ``traceback`` attribute contains the full Python traceback as
    captured by KliCAD's embedded interpreter (``exception_traceback`` from
    :class:`klipy.klicad.RunPythonResult`).  ``stdout`` and ``stderr`` carry
    anything the snippet wrote before failing.
    """

    def __init__(self, message: str, *, traceback: str = "",
                 stdout: str = "", stderr: str = "") -> None:
        super().__init__(message)
        self.traceback = traceback
        self.stdout = stdout
        self.stderr = stderr

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        parts = [super().__str__()]
        if self.traceback:
            parts.append("\nEmbedded traceback:\n" + self.traceback)
        if self.stderr:
            parts.append("\nstderr: " + self.stderr)
        return "".join(parts)


def _py_kwargs(kwargs: Mapping[str, Any]) -> str:
    """Render a kwargs dict as ``k=<repr(v)>`` pairs, dropping ``None`` values
    so binding defaults take effect.  Preserves insertion order."""
    return ", ".join(f"{k}={v!r}" for k, v in kwargs.items() if v is not None)


def _py_args(args: Iterable[Any]) -> str:
    return ", ".join(repr(a) for a in args)


class _PyRunner:
    """Wraps a :class:`klipy.klicad.KliCAD` instance to dispatch ``run_python``
    calls and unmarshal their results.

    Not part of the public API — each proxy holds one as ``self._run``.
    """

    def __init__(self, kicad: "KliCAD") -> None:
        self._k = kicad

    # ---- low-level ------------------------------------------------------

    def raw(self, code: str) -> "RunPythonResult":
        """Run an arbitrary snippet and return the full result object."""
        r = self._k.run_python(code)
        if not r.ok:
            raise KliCADError(
                "embedded Python raised; see traceback",
                traceback=r.exception_traceback,
                stdout=r.stdout,
                stderr=r.stderr,
            )
        return r

    def expr(self, code: str) -> Any:
        """Run a snippet whose final statement is an expression; return the
        parsed Python value of ``result_repr`` via :func:`ast.literal_eval`.

        If the snippet returns nothing (empty ``result_repr``), returns
        ``None``.  If ``result_repr`` is present but can't be parsed by
        :func:`ast.literal_eval` (e.g. it embeds non-literal repr like
        ``<object at 0x…>``), returns the raw string unchanged.
        """
        r = self.raw(code)
        if not r.result_repr:
            return None
        try:
            return ast.literal_eval(r.result_repr)
        except (ValueError, SyntaxError):
            return r.result_repr

    # ---- call helpers ---------------------------------------------------

    def call(
        self,
        module: str,
        func: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Dispatch ``import <module> as _m; _m.<func>(...)`` and return
        the parsed result.

        Use this for any binding function whose return value is JSON-shaped
        (dict / list / str / int / float / bool / None / tuple of the
        above).  For anything else, fall back to :meth:`raw` or :meth:`expr`.
        """
        arg_str = _py_args(args)
        kw_str = _py_kwargs(kwargs)
        joined = ", ".join(p for p in (arg_str, kw_str) if p)
        code = f"import {module} as _m\n_m.{func}({joined})"
        return self.expr(code)

    def call_void(
        self,
        module: str,
        func: str,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """Same as :meth:`call`, but throws away the result (and any
        ``result_repr`` produced).  Use for procedures that don't return
        anything meaningful."""
        arg_str = _py_args(args)
        kw_str = _py_kwargs(kwargs)
        joined = ", ".join(p for p in (arg_str, kw_str) if p)
        code = f"import {module} as _m\n_m.{func}({joined})\nNone"
        self.raw(code)

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        return f"<_PyRunner kicad={self._k!r}>"

"""Workflow-shaped helpers for the canonical agent design loop.

Codifies the sequence that downstream agents are expected to follow,
in callable form, so the workflow isn't relying on prose-level
reminders in prompts.  When in doubt, use these instead of writing
ad-hoc subprocess / playwright orchestration.

Two pillars:

1. **`ensure_klicad(...)`** — make sure a KliCAD process is up and
   answering on the IPC API.  Launches the dev-build binary via
   `~/.local/bin/klicad` if needed; waits up to `timeout` for the API
   to respond.  Returns a connected `kipy.KiCad` instance.

2. **`verify_part_on_lcsc(...)` / `survey_lcsc_category(...)`** —
   structured LCSC pricing/availability lookup via the MCP Playwright
   server (or a local playwright instance).  These are the thin Python
   wrappers around the parametric-filter / price-sort flow documented
   in driver-board/bom/LCSC_WORKFLOW.md.

The Playwright helpers are stubs that document the contract — the
actual filter/sort/scrape flow is driven by the agent calling
`mcp__playwright_isoN__*` tools directly.  This module's contribution
is the schemas (input / output shapes) and the workflow guarantees.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


DEFAULT_KLICAD_BINARY = Path.home() / ".local" / "bin" / "klicad"


def ensure_klicad(*,
                  binary: str | Path = DEFAULT_KLICAD_BINARY,
                  timeout: float = 30.0,
                  poll_interval: float = 0.5):
    """Return a connected ``kipy.KiCad`` instance, launching KliCAD if needed.

    Behavior:
      1. Try to connect to an already-running KliCAD via the standard IPC
         socket.  If `is_alive()` returns True, return that client.
      2. Otherwise spawn `binary` (default: ``~/.local/bin/klicad``) as a
         detached subprocess, then poll `is_alive()` every
         `poll_interval` seconds until either it returns True or
         `timeout` elapses.
      3. If the timeout expires without the API answering, raise
         ``RuntimeError`` with the path of the spawned process and the
         elapsed wait.

    This deliberately does NOT "skip gracefully" — the agent flow that
    calls ensure_klicad() needs a live KliCAD, full stop.  If you want
    a conditional flow, call ``KiCad().is_alive()`` directly.

    The launched process becomes a child of the current Python session.
    On a normal exit the user can keep the GUI open; on a crash, KliCAD
    will outlive this process unless the caller manages the subprocess.
    """
    from kipy import KiCad

    # 1. Already up?
    try:
        client = KiCad()
        if client.is_alive():
            return client
    except Exception:
        # Server not reachable; we'll spawn below.
        pass

    binary_path = Path(binary)
    if not binary_path.exists():
        raise RuntimeError(
            f"ensure_klicad(): binary not found at {binary_path}.  "
            f"Expected a symlink to the KliCAD dev build "
            f"(typically ~/.local/bin/klicad -> "
            f"~/projects/KliCAD/build/kicad/kicad).  Either install the "
            f"symlink or pass `binary=` pointing at a real KliCAD binary."
        )

    # 2. Spawn detached.  KliCAD shows a GUI window — that's expected.
    proc = subprocess.Popen(
        [str(binary_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    # 3. Poll.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        time.sleep(poll_interval)
        try:
            client = KiCad()
            if client.is_alive():
                return client
        except Exception:
            continue

    elapsed = timeout
    raise RuntimeError(
        f"ensure_klicad(): launched {binary_path} (pid {proc.pid}) but the "
        f"IPC API didn't come up within {elapsed:.0f}s.  Possible causes:\n"
        f"  • KliCAD is starting but the API socket isn't bound yet — "
        f"increase timeout=\n"
        f"  • Preferences → Plugins → API server is disabled in this build\n"
        f"  • The dev build at {binary_path.resolve()} is broken — try "
        f"running it manually to see the splash / errors"
    )


# ────────────────────────────────────────────────────────────────────────
# LCSC helpers
# ────────────────────────────────────────────────────────────────────────


@dataclass
class LcscPart:
    """Structured result of an LCSC part lookup.

    `price_tiers` is a list of (qty, unit_price) tuples in ascending qty
    order.  `stock` is the integer stock count (0 if out of stock).
    `lcsc_id` is the "C12345" identifier; `mfr` is the manufacturer name
    as LCSC reports it; `package` is the body style (SOT-23, SOIC-8, etc.).
    """
    mpn: str
    lcsc_id: str
    mfr: str
    stock: int
    package: str
    description: str
    price_tiers: list[tuple[int, float]]
    raw_url: str


def lcsc_search_url(mpn: str) -> str:
    """Canonical LCSC URL to land on the parametric search for an MPN."""
    from urllib.parse import quote
    return f"https://www.lcsc.com/search?q={quote(mpn)}"


def lcsc_category_url(category_id: int) -> str:
    """Canonical LCSC URL for a parametric category page."""
    return f"https://www.lcsc.com/category/{category_id}.html"


# Common category IDs the agent will reach for.  Maintained here so the
# agent doesn't have to scrape the catalog index every time.
LCSC_CATEGORIES = {
    "microcontrollers": 941,
    "single_mosfets": 1436,
    "shift_registers": 980,
    "io_expanders": 954,
    "tvs_diodes": 702,
    "load_switches": 1019,
}


def survey_lcsc_category_instructions(
    category_id: int,
    *,
    filters: dict | None = None,
    in_stock_only: bool = True,
    sort: str = "price_asc",
    limit: int = 20,
) -> dict:
    """Return a structured plan for the agent to drive Playwright with.

    This function does NOT itself call Playwright — it returns the
    sequence of clicks and scrapes the agent should perform, with the
    refs / selectors documented in bom/LCSC_WORKFLOW.md.  This keeps
    the workflow knowledge in one place that both the agent and any
    future automation can use.

    Returns a dict with keys:
      url:       the category URL to navigate to
      filters:   the chip-click sequence as a list of {category, value}
      in_stock:  whether to tick the In Stock checkbox
      sort:      sort direction ("price_asc" or "price_desc")
      apply:     the "Apply All" button selector
      scrape:    the row-extraction JS snippet
      limit:     how many top rows to record
    """
    filters = dict(filters or {})
    return {
        "url": lcsc_category_url(category_id),
        "filters": [
            {"category": k, "value": v} for k, v in filters.items()
        ],
        "in_stock": in_stock_only,
        "sort": sort,
        "apply": 'button:has-text("Apply All")',
        "scrape": (
            "() => [...document.querySelectorAll('tbody tr')]"
            ".filter(r => r.querySelectorAll('td').length > 10)"
            ".slice(0, %d)"
            ".map(r => [...r.querySelectorAll('td')]"
            ".map(td => td.innerText.replace(/\\s+/g,' ').trim()))"
        ) % limit,
        "limit": limit,
    }


def lcsc_workflow_doc_path() -> Path:
    """Path to the long-form LCSC workflow markdown (read this if confused).

    The doc covers: category IDs, filter UI quirks, the persistent-state
    gotcha, the horizontal-scroll chip strip, the "Apply All" requirement,
    Asian-brand savings rules of thumb.
    """
    return (
        Path.home()
        / "projects" / "driver-board" / "bom" / "LCSC_WORKFLOW.md"
    )


__all__ = [
    "ensure_klicad",
    "DEFAULT_KLICAD_BINARY",
    "LcscPart",
    "lcsc_search_url",
    "lcsc_category_url",
    "LCSC_CATEGORIES",
    "survey_lcsc_category_instructions",
    "lcsc_workflow_doc_path",
]

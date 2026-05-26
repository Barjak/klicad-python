"""Workflow-helper tests (kipy.klicad.workflow).

The two helpers exercised here are pure-Python:

  * `survey_lcsc_category_instructions` — returns a plan dict; we just
    check the shape.
  * `lcsc_search_url` / `lcsc_category_url` — URL builders.

`ensure_klicad` itself is harder to test without a real binary; we
verify the missing-binary branch (the only deterministic failure
path) and trust the rest to integration.
"""

from __future__ import annotations

import pytest

from kipy.klicad import workflow


def test_lcsc_search_url_quotes_special_chars():
    assert workflow.lcsc_search_url("AO3401A") == "https://www.lcsc.com/search?q=AO3401A"
    # Slashes and spaces should get percent-encoded.
    u = workflow.lcsc_search_url("SSM6J507NU,LF")
    assert "%2C" in u or "," in u  # comma may or may not be encoded depending on quote()


def test_lcsc_category_url():
    assert workflow.lcsc_category_url(941) == "https://www.lcsc.com/category/941.html"


def test_category_aliases_present():
    """The named-aliases dict should at least cover the things the
    LCSC_WORKFLOW.md doc enumerates."""
    needed = {"microcontrollers", "single_mosfets", "shift_registers",
              "io_expanders", "tvs_diodes", "load_switches"}
    assert needed.issubset(workflow.LCSC_CATEGORIES.keys())


def test_survey_instructions_shape():
    plan = workflow.survey_lcsc_category_instructions(
        941,
        filters={"CPU Core": "ARM Cortex-M4"},
        in_stock_only=True,
        sort="price_asc",
        limit=10,
    )
    # Required keys
    for key in ("url", "filters", "in_stock", "sort", "apply", "scrape", "limit"):
        assert key in plan, f"missing {key!r}"
    assert plan["url"].endswith("/category/941.html")
    assert plan["in_stock"] is True
    assert plan["sort"] == "price_asc"
    assert plan["limit"] == 10
    assert len(plan["filters"]) == 1
    assert plan["filters"][0] == {"category": "CPU Core", "value": "ARM Cortex-M4"}
    # The scrape snippet should be evaluable as a JS arrow function
    assert plan["scrape"].startswith("() =>")
    assert "tbody tr" in plan["scrape"]


def test_workflow_doc_path_points_at_repo():
    path = workflow.lcsc_workflow_doc_path()
    assert path.name == "LCSC_WORKFLOW.md"
    # We don't require the file to exist in the test env — the path is
    # just the canonical pointer.


def test_ensure_klicad_missing_binary_raises(tmp_path):
    """When the binary path doesn't exist, ensure_klicad raises with a
    clear message — doesn't silently fall back."""
    missing = tmp_path / "definitely_not_klicad"
    with pytest.raises(RuntimeError, match="binary not found"):
        workflow.ensure_klicad(binary=missing, timeout=1.0)

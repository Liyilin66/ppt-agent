"""Shared test configuration.

Tests that measure pages in a real browser carry the skip condition "no Chromium-based browser"; they
are also marked ``browser`` here, so they can be selected with ``-m browser``.

A few of them depend on the exact glyph widths of the Microsoft Office fonts the pages are measured with
(slides are shown in PowerPoint). Without Office the browser uses stand-in fonts: on CI one known-good
page measured a 10px gap against a 12px threshold and one line broke elsewhere. Those tests carry
``needs_office_fonts`` and skip, with that reason, where Office is not installed.
"""

import pytest

BROWSER_REASON = "no Chromium-based browser"


def pytest_collection_modifyitems(items):
    for item in items:
        if any(mark.kwargs.get("reason") == BROWSER_REASON for mark in item.iter_markers("skipif")):
            item.add_marker(pytest.mark.browser)


def _office_fonts() -> bool:
    from ppt_agent.v2.htmldeck.fonts import office_font_css

    return office_font_css()[1]


# Slides are shown in PowerPoint, so pages are measured with the Chinese fonts Office ships. Without
# Office the browser uses stand-ins that are close in width but not identical; the few tests whose
# expected values depend on the exact glyph widths (a 12px gap, where a line breaks) need the real fonts.
# CI runners have no Office: there these tests skip with this reason, printed by -rs.
needs_office_fonts = pytest.mark.skipif(
    not _office_fonts(), reason="needs the Microsoft Office fonts its layout thresholds were measured with")

"""Shared test configuration.

Tests that measure pages in a real browser carry the skip condition "no Chromium-based browser".
They are also marked ``browser`` here, so CI can run them where their pixel thresholds were
calibrated (macOS fonts) and the rest everywhere: on Ubuntu with Noto CJK fonts, one known-good
page measured a 10px gap against a 12px threshold (the glyph widths differ, not the code).
"""

import pytest

BROWSER_REASON = "no Chromium-based browser"


def pytest_collection_modifyitems(items):
    for item in items:
        if any(mark.kwargs.get("reason") == BROWSER_REASON for mark in item.iter_markers("skipif")):
            item.add_marker(pytest.mark.browser)

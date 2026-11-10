"""Headless Chromium: lay a slide out and report where everything landed."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

from ppt_agent.v2.htmldeck.document import H, W


class BrowserUnavailable(RuntimeError):
    """No usable browser; callers fall back to the code-driven layout engine."""


@dataclass
class Measured:
    background: str
    items: list[dict[str, Any]]
    notes: list[str] = field(default_factory=list)  # CSS the converter cannot reproduce
    clipped: list[dict[str, Any]] = field(default_factory=list)
    scroll_width: float = W
    scroll_height: float = H
    screenshot_png: bytes = b""


def _extract_script() -> str:
    return resources.files("ppt_agent.v2.htmldeck").joinpath("extract.js").read_text("utf-8")


class Session:
    """One browser process shared by every page of a deck.

    Pages are loaded from temporary files rather than ``set_content``: the font
    rules point at ``file://`` URLs, which Chromium only honours for documents
    that are themselves local files.
    """

    def __init__(self, *, concurrency: int = 4, screenshot_scale: float = 1.0) -> None:
        self._concurrency = concurrency
        self._scale = screenshot_scale
        self._playwright = None
        self._browser = None
        self._context = None
        self._gate: asyncio.Semaphore | None = None
        self._dir: Path | None = None
        self._count = 0
        self._script = _extract_script()

    async def __aenter__(self) -> "Session":
        try:
            from playwright.async_api import async_playwright
        except ImportError as error:  # pragma: no cover - dependency is declared
            raise BrowserUnavailable("playwright is not installed") from error
        self._playwright = await async_playwright().start()
        last_error: Exception | None = None
        for options in ({"channel": "chrome"}, {"channel": "msedge"}, {}):
            try:
                self._browser = await self._playwright.chromium.launch(**options)
                break
            except Exception as error:  # noqa: BLE001 - try the next browser
                last_error = error
        if self._browser is None:
            await self._playwright.stop()
            raise BrowserUnavailable(f"no Chromium-based browser could be started: {last_error}")
        self._context = await self._browser.new_context(
            viewport={"width": W, "height": H}, device_scale_factor=self._scale
        )
        self._gate = asyncio.Semaphore(self._concurrency)
        self._dir = Path(tempfile.mkdtemp(prefix="ppt-agent-html-"))
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()
        if self._dir is not None:
            shutil.rmtree(self._dir, ignore_errors=True)

    async def measure(self, html_document: str, *, screenshot: bool = True) -> Measured:
        assert self._context is not None and self._gate is not None and self._dir is not None
        async with self._gate:
            self._count += 1
            path = self._dir / f"page-{self._count}.html"
            path.write_text(html_document, encoding="utf-8")
            page = await self._context.new_page()
            try:
                await page.goto(path.as_uri())
                await page.evaluate("() => document.fonts.ready.then(() => true)")
                data = await page.evaluate(self._script)
                png = (
                    await page.screenshot(clip={"x": 0, "y": 0, "width": W, "height": H})
                    if screenshot
                    else b""
                )
            finally:
                await page.close()
                path.unlink(missing_ok=True)
        return Measured(
            background=data["background"],
            items=data["items"],
            notes=data["notes"],
            clipped=data["clipped"],
            scroll_width=data["scroll"]["w"],
            scroll_height=data["scroll"]["h"],
            screenshot_png=png,
        )


async def _probe() -> bool:
    try:
        async with Session():
            return True
    except Exception:  # noqa: BLE001 - any failure means "not available"
        return False


_AVAILABLE: bool | None = None


def available() -> bool:
    """Whether a browser can be started here (cached for the process)."""

    global _AVAILABLE
    if _AVAILABLE is None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            _AVAILABLE = asyncio.run(_probe())
        else:  # inside a running loop: probe on a helper thread
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(1) as pool:
                _AVAILABLE = pool.submit(lambda: asyncio.run(_probe())).result()
    return _AVAILABLE

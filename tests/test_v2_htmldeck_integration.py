import json
from pathlib import Path

import pytest

from ppt_agent.v2.htmldeck import browser
from ppt_agent.v2.ir import DeckDesign, RichTextItem
from ppt_agent.v2.mock import MockLLMClient
from ppt_agent.v2.orchestrator import BuildRequest, build_deck

needs_browser = pytest.mark.skipif(not browser.available(), reason="no Chromium-based browser")


class Counting(MockLLMClient):
    def __init__(self):
        super().__init__()
        self.text_calls = 0

    async def complete_text(self, **kwargs):
        self.text_calls += 1
        return await super().complete_text(**kwargs)


def request(tmp_path, **over):
    return BuildRequest(prompt="AI 落地汇报", page_count=12, output_dir=str(tmp_path),
                        style_profile="consulting", html_theme="bright", **over)


@needs_browser
def test_a_deck_is_designed_in_html_and_rendered_natively(tmp_path):
    client = Counting()
    result = build_deck(request(tmp_path), client, progress=lambda _: None)
    assert result.pptx_path and Path(result.pptx_path).is_file()
    report = json.loads(Path(result.run_report_path).read_text())
    design = report["html_design"]
    assert design["theme"] == "bright" and design["fell_back_pages"] == []
    assert design["designed_pages"] == client.text_calls > 0
    assert design["structural_fell_back"] == [] and design["reviewed_pages"] == design["designed_pages"]
    deck = DeckDesign.model_validate_json(Path(result.deck_design_path).read_text())
    assert deck.theme.name == "html-bright" and len(deck.pages) == 12
    for page in deck.pages:
        assert page.background_hex and not page.show_chrome
        assert any(isinstance(element, RichTextItem) for element in page.elements)
    assert (tmp_path / "checkpoints/html/page_003.json").is_file() or design["designed_pages"] < 3


@needs_browser
def test_resume_rebuilds_pages_from_saved_html_without_the_model(tmp_path):
    build_deck(request(tmp_path), Counting(), progress=lambda _: None)
    again = Counting()
    result = build_deck(request(tmp_path, resume=True), again, progress=lambda _: None)
    assert again.text_calls == 0 and result.pptx_path


def test_without_a_browser_the_layout_engine_is_used(tmp_path, monkeypatch):
    from ppt_agent.v2.htmldeck import deck as html_deck

    async def unavailable(*args, **kwargs):
        raise browser.BrowserUnavailable("none here")

    monkeypatch.setattr(html_deck, "design_deck", unavailable)
    messages = []
    result = build_deck(request(tmp_path), MockLLMClient(), progress=messages.append)
    assert result.pptx_path
    report = json.loads(Path(result.run_report_path).read_text())
    assert report["html_design"]["unavailable"] == "none here"
    assert any("no browser available" in message for message in messages)


def test_the_theme_option_reaches_the_build_request_from_every_entry_point():
    import argparse

    from ppt_agent.deck_jobs import CreateLongDeckJobRequest
    from ppt_agent.mcp_brief import ClarifyBrief, apply_updates, to_job_request
    from ppt_agent.v2 import cli

    parser = argparse.ArgumentParser()
    cli._add_build_arguments(parser, offline=True)
    args = parser.parse_args(["--prompt", "p", "--output-dir", "o", "--html-theme", "bright", "--no-html-review"])
    built = cli._build_request(args, offline=True)
    assert built.html_theme == "bright" and built.html_review is False
    job = CreateLongDeckJobRequest(topic="t", audience="a", slide_count=10, user_requirements="r",
                                   html_theme="dark")
    assert job.html_theme == "dark"
    brief = apply_updates(ClarifyBrief(), {
        name: {"value": value, "origin": "user"} for name, value in {
            "topic": "AI 落地", "purpose": "向管理层汇报", "audience": "管理层", "slide_count": 10,
            "user_requirements": "讲清机会", "source_policy": "none", "html_theme": "editorial"}.items()
    }).brief
    assert to_job_request(brief).html_theme == "editorial"

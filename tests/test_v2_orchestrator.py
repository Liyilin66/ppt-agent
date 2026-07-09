"""End-to-end orchestrator tests with the deterministic mock client."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pptx import Presentation

from ppt_agent.v2.mock import MockLLMClient
from ppt_agent.v2.orchestrator import BuildRequest, build_deck
from ppt_agent.v2.providers import UsageMeter


def _request(tmp_path: Path, **overrides) -> BuildRequest:
    defaults = dict(
        prompt="AI Agent 产品方案",
        page_count=20,
        output_dir=str(tmp_path / "out"),
        deck_name="test",
    )
    defaults.update(overrides)
    return BuildRequest(**defaults)


class _FailingDesignClient(MockLLMClient):
    """Mock that always fails page_design to exercise the archetype fallback."""

    async def complete_json(self, *, task: str, **kwargs: Any) -> Any:
        if task == "page_design":
            raise RuntimeError("provider exploded")
        return await super().complete_json(task=task, **kwargs)


class TestBuildDeck:
    def test_full_offline_build(self, tmp_path: Path) -> None:
        result = build_deck(_request(tmp_path), MockLLMClient(), progress=lambda _: None)
        assert result.status == "succeeded"
        assert result.page_count == 20
        presentation = Presentation(result.pptx_path)
        assert len(presentation.slides) == 20
        for artifact in (
            result.deck_design_path,
            result.qa_report_path,
            result.run_report_path,
        ):
            assert Path(artifact).is_file()
        report = json.loads(Path(result.run_report_path).read_text(encoding="utf-8"))
        assert len(report["outcomes"]) == 20

    def test_hundred_page_build(self, tmp_path: Path) -> None:
        result = build_deck(
            _request(tmp_path, page_count=100), MockLLMClient(), progress=lambda _: None
        )
        assert result.page_count == 100
        assert len(Presentation(result.pptx_path).slides) == 100

    def test_failed_pages_fall_back_instead_of_holes(self, tmp_path: Path) -> None:
        result = build_deck(
            _request(tmp_path), _FailingDesignClient(), progress=lambda _: None
        )
        assert result.status == "succeeded_with_fallbacks"
        assert result.fallback_pages > 0
        assert result.page_count == 20  # no holes

    def test_resume_reuses_page_checkpoints(self, tmp_path: Path) -> None:
        request = _request(tmp_path)
        build_deck(request, MockLLMClient(), progress=lambda _: None)

        # Poison one checkpointed page title, then resume: the poisoned value
        # must survive, proving the page was not regenerated.
        checkpoint_dir = Path(request.output_dir) / "checkpoints" / "pages"
        target = sorted(checkpoint_dir.glob("page_*.json"))[0]
        payload = json.loads(target.read_text(encoding="utf-8"))
        payload["page"]["title"] = "RESUMED_MARKER"
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        resumed = build_deck(
            request.model_copy(update={"resume": True}),
            MockLLMClient(),
            progress=lambda _: None,
        )
        design = json.loads(Path(resumed.deck_design_path).read_text(encoding="utf-8"))
        assert any(page["title"] == "RESUMED_MARKER" for page in design["pages"])

    def test_language_override_reaches_brief(self, tmp_path: Path) -> None:
        result = build_deck(
            _request(tmp_path, language="en"), MockLLMClient(), progress=lambda _: None
        )
        design = json.loads(Path(result.deck_design_path).read_text(encoding="utf-8"))
        assert design["language"] == "en"


class TestCLI:
    def test_v2_demo_command(self, tmp_path: Path, capsys) -> None:
        from ppt_agent.cli import main

        exit_code = main(
            [
                "v2",
                "demo",
                "--prompt",
                "测试主题",
                "--pages",
                "12",
                "--output-dir",
                str(tmp_path / "cli_out"),
            ]
        )
        assert exit_code == 0
        output = capsys.readouterr().out
        assert "status: succeeded" in output
        assert (tmp_path / "cli_out" / "deck.pptx").is_file()

    def test_v2_preview_command(self, tmp_path: Path, capsys) -> None:
        from ppt_agent.cli import main

        main(
            [
                "v2",
                "demo",
                "--prompt",
                "预览测试",
                "--pages",
                "8",
                "--output-dir",
                str(tmp_path / "pv"),
            ]
        )
        exit_code = main(
            [
                "v2",
                "preview",
                "--design",
                str(tmp_path / "pv" / "deck_design.json"),
                "--output",
                str(tmp_path / "pv" / "preview.html"),
            ]
        )
        assert exit_code == 0
        html = (tmp_path / "pv" / "preview.html").read_text(encoding="utf-8")
        assert html.count('class="page"') == 8

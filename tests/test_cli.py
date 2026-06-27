import json
from pathlib import Path

from pptx import Presentation

from ppt_agent.cli import main


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"


def test_render_cli_generates_pptx(tmp_path: Path) -> None:
    output_path = tmp_path / "sample_deck.pptx"

    status = main(
        [
            "render",
            str(EXAMPLES_DIR / "sample_slide_ir.json"),
            "--theme",
            str(EXAMPLES_DIR / "theme.json"),
            "--output",
            str(output_path),
        ]
    )

    assert status == 0
    assert output_path.exists()
    assert len(Presentation(output_path).slides) == 3


def test_qa_cli_writes_report_json(tmp_path: Path) -> None:
    output_path = tmp_path / "qa_report.json"

    status = main(
        [
            "qa",
            str(EXAMPLES_DIR / "sample_slide_ir.json"),
            "--theme",
            str(EXAMPLES_DIR / "theme.json"),
            "--output",
            str(output_path),
        ]
    )

    assert status == 0
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["deck_id"] == "sample_clean_business_deck"
    assert "score" in report
    assert "issues" in report


def test_generate_cli_without_api_key_exits_with_clear_message(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    output_path = tmp_path / "generated_deck.json"

    status = main(
        [
            "generate",
            "--topic",
            "AI in Education",
            "--audience",
            "university students",
            "--slides",
            "8",
            "--theme",
            str(EXAMPLES_DIR / "theme.json"),
            "--output",
            str(output_path),
        ]
    )

    captured = capsys.readouterr()
    assert status == 1
    assert "OPENAI_API_KEY is not set" in captured.out
    assert not output_path.exists()

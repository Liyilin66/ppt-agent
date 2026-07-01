import importlib.util
import json
import sys
from pathlib import Path

from pptx import Presentation


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_script_module(script_name: str):
    script_path = _repo_root() / "scripts" / script_name
    module_name = script_name.removesuffix(".py")
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load script module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_runner_module():
    return _load_script_module("run_long_deck_demo.py")


def _load_render_module():
    return _load_script_module("render_long_deck_demo.py")


def _load_export_module():
    return _load_script_module("export_to_ppt_master.py")


def test_long_deck_demo_input_exists_and_requests_30_slides() -> None:
    module = _load_runner_module()
    request = module.load_long_deck_run_request()

    assert module.DEFAULT_INPUT_PATH.exists()
    assert request.topic == "AI 产品经理如何设计 Agent 产品"
    assert request.audience == "准备进入 AI 产品岗位的 IT 硕士学生"
    assert request.slide_count == 30
    assert request.batch_size == 2
    assert request.deck_type == "technical_product_share"


def test_long_deck_demo_runner_batch_size_override() -> None:
    module = _load_runner_module()

    args = module.build_parser().parse_args(["--batch-size", "3"])
    request = module.load_long_deck_run_request(batch_size=args.batch_size)

    assert args.batch_size == 3
    assert request.batch_size == 3


def test_long_deck_demo_runner_resume_flag_sets_request_resume() -> None:
    module = _load_runner_module()

    args = module.build_parser().parse_args(["--batch-size", "2", "--resume"])
    request = module.load_long_deck_run_request(batch_size=args.batch_size, resume=args.resume)

    assert args.resume is True
    assert request.resume is True
    assert request.batch_size == 2


def test_long_deck_demo_runner_reports_missing_openai_key(monkeypatch, capsys) -> None:
    module = _load_runner_module()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    exit_code = module.main([])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "OPENAI_API_KEY is not set" in captured.err
    assert "30-page long deck demo dry run" in captured.err


def test_long_deck_demo_readme_does_not_claim_100_page_pptx() -> None:
    readme_path = _repo_root() / "examples" / "demo_long_deck_ai_agent_pm_30" / "README.md"
    text = readme_path.read_text(encoding="utf-8").lower()

    assert "100-page" not in text
    assert "100 页" not in text
    assert "render pptx" in text
    assert "render step does not call the model" in text
    assert "batch_size=2" in text
    assert "15 mini-batches" in text
    assert "web ui long-ppt entrypoint" in text


def test_long_deck_demo_runner_default_output_dir() -> None:
    module = _load_runner_module()

    assert module.default_output_dir() == _repo_root() / "examples" / "demo_long_deck_ai_agent_pm_30" / "output"
    assert module.load_long_deck_run_request().output_dir == module.default_output_dir()


def test_long_deck_render_script_default_paths() -> None:
    module = _load_render_module()
    output_dir = _repo_root() / "examples" / "demo_long_deck_ai_agent_pm_30" / "output"

    assert module.DEFAULT_INPUT_PATH == output_dir / "generated_long_deck_ir.json"
    assert module.DEFAULT_OUTPUT_PATH == output_dir / "generated_long_deck.pptx"
    assert module.DEFAULT_REPORT_PATH == output_dir / "long_deck_render_report.json"


def test_ppt_master_export_script_default_paths() -> None:
    module = _load_export_module()
    output_dir = _repo_root() / "examples" / "demo_long_deck_ai_agent_pm_30" / "output"

    assert module.DEFAULT_INPUT_PATH == output_dir / "generated_long_deck_ir.json"
    assert module.DEFAULT_OUTPUT_PATH == output_dir / "ppt_master_source.md"


def test_ppt_master_export_script_missing_input_reports_clear_error(tmp_path, capsys) -> None:
    module = _load_export_module()
    missing_input = tmp_path / "missing_long_deck_ir.json"
    output_path = tmp_path / "ppt_master_source.md"

    exit_code = module.main(["--input", str(missing_input), "--output", str(output_path)])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "Input Deck IR not found" in captured.err
    assert "Traceback" not in captured.err
    assert not output_path.exists()


def test_ppt_master_export_script_exports_source_markdown(tmp_path) -> None:
    module = _load_export_module()
    output_path = tmp_path / "ppt_master_source.md"

    exit_code = module.main(
        [
            "--input",
            str(_repo_root() / "examples" / "sample_slide_ir.json"),
            "--output",
            str(output_path),
            "--style-notes",
            "Use technical product share pacing.",
        ]
    )
    markdown = output_path.read_text(encoding="utf-8")

    assert exit_code == 0
    assert output_path.exists()
    assert "# Presentation Request" in markdown
    assert "Use technical product share pacing." in markdown


def test_long_deck_render_script_missing_input_writes_clear_report(tmp_path, capsys) -> None:
    module = _load_render_module()
    missing_input = tmp_path / "missing_long_deck_ir.json"
    output_path = tmp_path / "generated_long_deck.pptx"
    report_path = tmp_path / "long_deck_render_report.json"

    exit_code = module.main(
        [
            "--input",
            str(missing_input),
            "--output",
            str(output_path),
            "--report",
            str(report_path),
        ]
    )
    captured = capsys.readouterr()
    report = json.loads(report_path.read_text(encoding="utf-8"))

    assert exit_code == 2
    assert "Input Deck IR not found" in captured.err
    assert "OPENAI_API_KEY" not in captured.err
    assert report["status"] == "failed"
    assert "Input Deck IR not found" in report["error_message"]


def test_long_deck_render_script_does_not_require_openai_key(monkeypatch, tmp_path) -> None:
    module = _load_render_module()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    input_path = _repo_root() / "examples" / "sample_slide_ir.json"
    output_path = tmp_path / "generated_long_deck.pptx"
    report_path = tmp_path / "long_deck_render_report.json"

    report = module.render_long_deck_demo(
        input_deck_ir_path=input_path,
        output_pptx_path=output_path,
        report_path=report_path,
        theme_path=_repo_root() / "examples" / "theme.json",
        assets_dir=_repo_root() / "examples",
    )

    assert report.status == "succeeded"
    assert report.error_message is None
    assert output_path.exists()
    assert report_path.exists()
    presentation = Presentation(output_path)
    assert len(presentation.slides) == report.slide_count


def test_long_deck_render_report_is_serializable(tmp_path) -> None:
    module = _load_render_module()
    report = module.LongDeckRenderReport(
        status="succeeded",
        input_deck_ir_path=tmp_path / "generated_long_deck_ir.json",
        output_pptx_path=tmp_path / "generated_long_deck.pptx",
        slide_count=30,
        generated_at="2026-06-30T00:00:00+00:00",
        warnings=[],
    )

    payload = json.loads(report.model_dump_json())

    assert payload["status"] == "succeeded"
    assert payload["slide_count"] == 30

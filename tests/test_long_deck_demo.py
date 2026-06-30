import importlib.util
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_runner_module():
    script_path = _repo_root() / "scripts" / "run_long_deck_demo.py"
    spec = importlib.util.spec_from_file_location("run_long_deck_demo", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load long deck demo runner from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    assert "does not render pptx" in text
    assert "batch_size=2" in text
    assert "15 mini-batches" in text


def test_long_deck_demo_runner_default_output_dir() -> None:
    module = _load_runner_module()

    assert module.default_output_dir() == _repo_root() / "examples" / "demo_long_deck_ai_agent_pm_30" / "output"
    assert module.load_long_deck_run_request().output_dir == module.default_output_dir()

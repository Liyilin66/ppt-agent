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


def _load_prepare_package_module():
    return _load_script_module("prepare_ppt_master_package.py")


def _load_check_setup_module():
    return _load_script_module("check_ppt_master_setup.py")


def _load_register_output_module():
    return _load_script_module("register_ppt_master_output.py")


def _load_prepare_execution_module():
    return _load_script_module("prepare_ppt_master_execution.py")


def _load_local_export_module():
    return _load_script_module("run_ppt_master_local_export.py")


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
    assert "local ppt master setup check" in text
    assert "check_ppt_master_setup.py" in text
    assert "web handoff ux" in text
    assert "ppt master 渲染包" in text
    assert "run_prompt.md" in text
    assert "does not automatically run ppt-master" in text
    assert "ppt master recovery package" in text
    assert "package_mode: recovery" in text
    assert "not to bypass the quality gate" in text
    assert "timeout / resume" in text
    assert "long_deck_job_timeout_seconds" in text
    assert "resume from the last completed batch" in text
    assert "ppt master output registration" in text
    assert "register_ppt_master_output.py" in text
    assert "ppt master 生成结果" in text
    assert "generated_by_ppt_master.pptx" in text
    assert "ppt master execution bridge v0" in text
    assert "prepare_ppt_master_execution.py" in text
    assert "ppt master 执行桥" in text
    assert "ppt_master_execution_plan.json" in text
    assert "ppt master local runner v0" in text
    assert "run_ppt_master_local_export.py" in text
    assert "ppt master 本地导出" in text
    assert "does not call a model" in text


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


def test_ppt_master_package_script_default_paths() -> None:
    module = _load_prepare_package_module()
    output_dir = _repo_root() / "examples" / "demo_long_deck_ai_agent_pm_30" / "output"

    assert module.DEFAULT_INPUT_PATH == output_dir / "generated_long_deck_ir.json"
    assert module.DEFAULT_OUTPUT_DIR == output_dir / "ppt_master_package"


def test_ppt_master_output_register_script_default_paths() -> None:
    module = _load_register_output_module()
    output_dir = _repo_root() / "data" / "jobs" / module.DEFAULT_JOB_ID / "ppt_master_output"

    assert module.DEFAULT_OUTPUT_DIR == output_dir
    assert module.DEFAULT_DB_PATH == _repo_root() / "data" / "jobs.sqlite3"


def test_ppt_master_execution_script_default_paths() -> None:
    module = _load_prepare_execution_module()

    assert module.DEFAULT_JOB_ID == "02619bd8da5e49449f3b940a0f84771c"
    assert module.DEFAULT_JOB_DIR == _repo_root() / "data" / "jobs" / module.DEFAULT_JOB_ID
    assert module.DEFAULT_DB_PATH == _repo_root() / "data" / "jobs.sqlite3"


def test_ppt_master_local_export_script_default_paths() -> None:
    module = _load_local_export_module()

    assert module.DEFAULT_JOB_ID == "02619bd8da5e49449f3b940a0f84771c"
    assert module.DEFAULT_JOB_DIR == _repo_root() / "data" / "jobs" / module.DEFAULT_JOB_ID
    assert module.DEFAULT_DB_PATH == _repo_root() / "data" / "jobs.sqlite3"


def test_ppt_master_setup_check_script_json_contains_expected_fields(tmp_path, capsys) -> None:
    module = _load_check_setup_module()
    missing_ppt_master_dir = tmp_path / "ppt-master"

    exit_code = module.main(["--ppt-master-dir", str(missing_ppt_master_dir), "--json"])
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    for field in [
        "root_path",
        "is_available",
        "missing_paths",
        "skill_path",
        "scripts_path",
        "has_requirements",
        "has_readme",
        "has_readme_cn",
        "is_git_repo",
        "git_remote_origin",
        "git_branch",
        "git_commit",
        "is_expected_repo",
        "warnings",
        "suggested_commands",
    ]:
        assert field in payload
    assert payload["is_available"] is False
    assert payload["is_expected_repo"] is False
    assert payload["suggested_commands"] == [
        f"cd {tmp_path}",
        "git clone https://github.com/hugohe3/ppt-master.git",
    ]


def test_ppt_master_setup_check_script_human_output_suggests_clone_without_running_it(
    tmp_path,
    capsys,
) -> None:
    module = _load_check_setup_module()
    missing_ppt_master_dir = tmp_path / "ppt-master"

    exit_code = module.main(["--ppt-master-dir", str(missing_ppt_master_dir)])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "root_path:" in captured.out
    assert "is_expected_repo: False" in captured.out
    assert "git clone https://github.com/hugohe3/ppt-master.git" in captured.out
    assert "git pull" not in captured.out
    assert not missing_ppt_master_dir.exists()


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


def test_ppt_master_package_script_supports_arguments_and_missing_ppt_master_warning(
    tmp_path,
    capsys,
) -> None:
    module = _load_prepare_package_module()
    output_dir = tmp_path / "ppt_master_package"
    missing_ppt_master_dir = tmp_path / "missing-ppt-master"

    exit_code = module.main(
        [
            "--input",
            str(_repo_root() / "examples" / "sample_slide_ir.json"),
            "--output-dir",
            str(output_dir),
            "--ppt-master-dir",
            str(missing_ppt_master_dir),
            "--audience",
            "准备进入 AI 产品岗位的 IT 硕士学生",
            "--topic",
            "AI 产品经理如何设计 Agent 产品",
            "--style-notes",
            "Prefer local ppt-master visual polish.",
        ]
    )
    captured = capsys.readouterr()
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    source = (output_dir / "source.md").read_text(encoding="utf-8")
    run_prompt = (output_dir / "run_prompt.md").read_text(encoding="utf-8")

    assert exit_code == 0
    assert "warning:" in captured.err
    assert "PPT_MASTER_DIR" in captured.err
    assert "Traceback" not in captured.err
    assert manifest["is_available"] is False
    assert manifest["audience"] == "准备进入 AI 产品岗位的 IT 硕士学生"
    assert "准备进入 AI 产品岗位的 IT 硕士学生" in source
    assert "准备进入 AI 产品岗位的 IT 硕士学生" in run_prompt


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

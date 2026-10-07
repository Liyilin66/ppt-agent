from pathlib import Path


def _repo_file(*parts: str) -> Path:
    return Path(__file__).resolve().parents[1].joinpath(*parts)


def test_markdown_files_do_not_contain_local_user_path() -> None:
    # Release docs should stay portable across machines and must not leak the author's local path.
    markdown_paths = [
        _repo_file("README.md"),
        _repo_file("docs", "private_beta.md"),
        _repo_file("examples", "demo_ai_agent_pm", "README.md"),
    ]

    for path in markdown_paths:
        text = path.read_text(encoding="utf-8")
        assert "/Users/jay" not in text, f"Local absolute path leaked into {path}"


def test_readme_does_not_make_unsupported_marketing_claims() -> None:
    # README positioning should stay aligned with the actual repo scope and explicit non-goals.
    readme_text = _repo_file("README.md").read_text(encoding="utf-8")

    assert "LangGraph" not in readme_text
    assert "enterprise-ready" not in readme_text
    assert "production-ready SaaS" not in readme_text
    assert "perfect PPT generation" not in readme_text

    # Retrieval and image rebuild now exist; the README must not deny them, and
    # rule-QA counts are not evidence that PowerPoint renders cleanly.
    assert "不支持 RAG" not in readme_text
    assert "不支持 image-to-PPT" not in readme_text
    assert "0 error" not in readme_text
    assert "尚未接搜索" not in readme_text and "尚未接入联网搜索" not in readme_text


def test_release_checklist_exists_and_mentions_required_checks() -> None:
    # The release checklist is part of the repo contract for resume-ready and private-beta handoff.
    checklist_path = _repo_file("docs", "release_checklist.md")
    assert checklist_path.exists()

    text = checklist_path.read_text(encoding="utf-8")
    for required_fragment in [
        "uv sync",
        "uv lock --check",
        "uv run pytest",
        "git diff --check",
        "verify demo screenshots exist",
        "verify example PPTX opens",
        "verify patch demo report exists",
    ]:
        assert required_fragment in text


def test_gitignore_covers_demo_regeneration_output_dir() -> None:
    gitignore_text = _repo_file(".gitignore").read_text(encoding="utf-8")
    assert "examples/demo_ai_agent_pm/output/" in gitignore_text

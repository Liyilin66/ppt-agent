import importlib.util
import re
from pathlib import Path

import pytest


def _load_generate_demo_screenshots_module():
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "generate_demo_screenshots.py"
    spec = importlib.util.spec_from_file_location("generate_demo_screenshots", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load screenshot script from {script_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _markdown_image_paths(text: str) -> list[str]:
    return re.findall(r"!\[[^\]]*]\(([^)]+)\)", text)


def test_generate_demo_screenshots_reports_missing_external_tool(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Screenshot export is intentionally optional; a missing local tool should fail clearly without
    # breaking the rest of the repo's demo pipeline or test suite.
    module = _load_generate_demo_screenshots_module()

    def fake_which(name: str) -> str | None:
        if name == "soffice":
            return None
        return f"/usr/bin/{name}"

    monkeypatch.setattr(module.shutil, "which", fake_which)

    exit_code = module.main(["--skip-contact-sheet"])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "Screenshot generation skipped" in captured.err
    assert "Missing required external tool: 'soffice'" in captured.err


def test_demo_readme_screenshot_paths_exist() -> None:
    # README screenshots are committed demo artifacts, so broken paths are a real docs regression.
    repo_root = Path(__file__).resolve().parents[1]
    readme_paths = [
        repo_root / "README.md",
        repo_root / "examples" / "demo_ai_agent_pm" / "README.md",
    ]

    referenced_images: set[Path] = set()
    for readme_path in readme_paths:
        text = readme_path.read_text(encoding="utf-8")
        for image_path in _markdown_image_paths(text):
            candidate = (readme_path.parent / image_path).resolve()
            if "examples/demo_ai_agent_pm/screenshots/" in str(candidate) or "examples/demo_ai_agent_pm/patches/screenshots/" in str(
                candidate
            ):
                referenced_images.add(candidate)

    assert referenced_images
    for image_path in sorted(referenced_images):
        assert image_path.exists(), f"Missing README screenshot: {image_path}"
        assert image_path.stat().st_size > 0

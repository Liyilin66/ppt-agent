import json
import subprocess
from pathlib import Path

from ppt_agent.ppt_master_integration import (
    EXPECTED_PPT_MASTER_CLONE_URL,
    check_ppt_master_setup,
    create_ppt_master_job_package,
    detect_ppt_master_installation,
)


EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"


def _mock_ppt_master_root(tmp_path: Path, *, include_skill: bool = True) -> Path:
    root = tmp_path / "ppt-master"
    skill_dir = root / "skills" / "ppt-master"
    skill_dir.mkdir(parents=True, exist_ok=True)
    if include_skill:
        (skill_dir / "SKILL.md").write_text("# PPT Master Skill\n", encoding="utf-8")
    (skill_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        '[project]\nname = "ppt-master"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    return root


def _sample_deck_payload() -> dict:
    return json.loads((EXAMPLES_DIR / "sample_slide_ir.json").read_text(encoding="utf-8"))


def _init_git_repo(root: Path, remote_url: str) -> None:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=root, check=True)


def test_detect_ppt_master_installation_accepts_valid_mock_root(tmp_path: Path) -> None:
    root = _mock_ppt_master_root(tmp_path)

    installation = detect_ppt_master_installation(root)

    assert installation.is_available is True
    assert installation.root_path == root.resolve()
    assert installation.skill_path == root.resolve() / "skills" / "ppt-master" / "SKILL.md"
    assert installation.scripts_path == root.resolve() / "skills" / "ppt-master" / "scripts"
    assert installation.missing_paths == []
    assert installation.version_info == "ppt-master 0.1.0"


def test_detect_ppt_master_installation_reports_missing_skill(tmp_path: Path) -> None:
    root = _mock_ppt_master_root(tmp_path, include_skill=False)

    installation = detect_ppt_master_installation(root)

    assert installation.is_available is False
    assert str(root.resolve() / "skills" / "ppt-master" / "SKILL.md") in installation.missing_paths


def test_create_ppt_master_job_package_writes_expected_files(tmp_path: Path) -> None:
    root = _mock_ppt_master_root(tmp_path)
    output_dir = tmp_path / "job" / "ppt_master_package"

    package = create_ppt_master_job_package(
        _sample_deck_payload(),
        output_dir,
        ppt_master_root=root,
        topic="AI 产品经理如何设计 Agent 产品",
        audience="准备进入 AI 产品岗位的 IT 硕士学生",
        style_notes="Use local ppt-master visual polish.",
    )

    assert package.source_path == output_dir.resolve() / "source.md"
    assert package.run_prompt_path == output_dir.resolve() / "run_prompt.md"
    assert package.readme_path == output_dir.resolve() / "README.md"
    assert package.manifest_path == output_dir.resolve() / "manifest.json"
    for path in [package.source_path, package.run_prompt_path, package.readme_path, package.manifest_path]:
        assert path.exists()


def test_create_ppt_master_job_package_prompt_contains_skill_and_source_paths(tmp_path: Path) -> None:
    root = _mock_ppt_master_root(tmp_path)
    package = create_ppt_master_job_package(
        _sample_deck_payload(),
        tmp_path / "package",
        ppt_master_root=root,
        audience="准备进入 AI 产品岗位的 IT 硕士学生",
    )

    run_prompt = package.run_prompt_path.read_text(encoding="utf-8")

    assert str(root.resolve() / "skills" / "ppt-master" / "SKILL.md") in run_prompt
    assert str(package.source_path) in run_prompt
    assert "editable PPTX" in run_prompt
    assert "Do not output risk / impact / mitigation" in run_prompt
    assert "判断点 1 / 判断点 2 / 判断点 3" in run_prompt


def test_create_ppt_master_job_package_manifest_records_availability(tmp_path: Path) -> None:
    root = _mock_ppt_master_root(tmp_path)
    package = create_ppt_master_job_package(
        _sample_deck_payload(),
        tmp_path / "package",
        ppt_master_root=root,
    )

    manifest = json.loads(package.manifest_path.read_text(encoding="utf-8"))

    assert manifest["source_path"] == str(package.source_path)
    assert manifest["ppt_master_root"] == str(root.resolve())
    assert manifest["is_available"] is True
    assert manifest["warnings"] == []


def test_create_ppt_master_job_package_missing_root_keeps_handoff_files(tmp_path: Path) -> None:
    missing_root = tmp_path / "missing-ppt-master"

    package = create_ppt_master_job_package(
        _sample_deck_payload(),
        tmp_path / "package",
        ppt_master_root=missing_root,
    )
    manifest = json.loads(package.manifest_path.read_text(encoding="utf-8"))

    assert package.source_path.exists()
    assert package.run_prompt_path.exists()
    assert package.readme_path.exists()
    assert manifest["is_available"] is False
    assert str(missing_root.resolve()) in manifest["missing_paths"]
    assert "PPT_MASTER_DIR" in manifest["warnings"][0]


def test_check_ppt_master_setup_accepts_expected_official_mock_repo(tmp_path: Path) -> None:
    root = _mock_ppt_master_root(tmp_path)
    _init_git_repo(root, EXPECTED_PPT_MASTER_CLONE_URL)

    report = check_ppt_master_setup(root)

    assert report.is_available is True
    assert report.is_git_repo is True
    assert report.git_remote_origin == EXPECTED_PPT_MASTER_CLONE_URL
    assert report.is_expected_repo is True
    assert report.warnings == []
    assert report.suggested_commands == [
        f"cd {root.resolve()}",
        "git status",
        "git pull",
    ]


def test_check_ppt_master_setup_missing_skill_is_unavailable(tmp_path: Path) -> None:
    root = _mock_ppt_master_root(tmp_path, include_skill=False)
    _init_git_repo(root, EXPECTED_PPT_MASTER_CLONE_URL)

    report = check_ppt_master_setup(root)

    assert report.is_available is False
    assert report.is_expected_repo is False
    assert str(root.resolve() / "skills" / "ppt-master" / "SKILL.md") in report.missing_paths
    assert any("required workflow files are missing" in warning for warning in report.warnings)


def test_check_ppt_master_setup_warns_on_unexpected_remote(tmp_path: Path) -> None:
    root = _mock_ppt_master_root(tmp_path)
    _init_git_repo(root, "https://github.com/example/not-ppt-master.git")

    report = check_ppt_master_setup(root)

    assert report.is_available is True
    assert report.is_git_repo is True
    assert report.is_expected_repo is False
    assert any("remote origin does not look like" in warning for warning in report.warnings)


def test_check_ppt_master_setup_warns_for_non_git_repo_with_valid_structure(tmp_path: Path) -> None:
    root = _mock_ppt_master_root(tmp_path)

    report = check_ppt_master_setup(root)

    assert report.is_available is True
    assert report.is_git_repo is False
    assert report.is_expected_repo is False
    assert any("not a git repository" in warning for warning in report.warnings)


def test_check_ppt_master_setup_missing_dir_suggests_clone_without_running_it(tmp_path: Path) -> None:
    root = tmp_path / "ppt-master"

    report = check_ppt_master_setup(root)

    assert report.is_available is False
    assert report.is_git_repo is False
    assert report.is_expected_repo is False
    assert report.suggested_commands == [
        f"cd {tmp_path}",
        f"git clone {EXPECTED_PPT_MASTER_CLONE_URL}",
    ]
    assert not root.exists()

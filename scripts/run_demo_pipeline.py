from pathlib import Path

from ppt_agent.export import write_model_json
from ppt_agent.load import load_deck, load_patch, load_theme
from ppt_agent.patch import apply_patch
from ppt_agent.qa import analyze_deck
from ppt_agent.renderer import render_deck_to_pptx


def run_demo_pipeline(
    output_dir: str | Path | None = None,
    examples_dir: str | Path | None = None,
) -> dict[str, Path]:
    repo_root = Path(__file__).resolve().parents[1]
    examples_path = Path(examples_dir) if examples_dir is not None else repo_root / "examples"
    output_path = Path(output_dir) if output_dir is not None else examples_path / "output"
    output_path.mkdir(parents=True, exist_ok=True)

    deck = load_deck(examples_path / "sample_slide_ir.json")
    theme = load_theme(examples_path / "theme.json")
    patch = load_patch(examples_path / "sample_patch.json")

    qa_report = analyze_deck(deck, theme)
    qa_report_path = write_model_json(qa_report, output_path / "qa_report.json")

    sample_deck_path = render_deck_to_pptx(
        deck,
        theme,
        output_path / "sample_deck.pptx",
        assets_dir=examples_path,
    )

    patch_result = apply_patch(deck, patch)
    patch_result_path = write_model_json(patch_result, output_path / "patch_result.json")

    patched_qa_report = analyze_deck(patch_result.deck, theme)
    patched_qa_report_path = write_model_json(
        patched_qa_report,
        output_path / "patched_qa_report.json",
    )

    patched_sample_deck_path = render_deck_to_pptx(
        patch_result.deck,
        theme,
        output_path / "patched_sample_deck.pptx",
        assets_dir=examples_path,
    )

    return {
        "qa_report": qa_report_path,
        "sample_deck": sample_deck_path,
        "patch_result": patch_result_path,
        "patched_qa_report": patched_qa_report_path,
        "patched_sample_deck": patched_sample_deck_path,
    }


def main() -> None:
    for label, path in run_demo_pipeline().items():
        print(f"{label}: {path}")


if __name__ == "__main__":
    main()

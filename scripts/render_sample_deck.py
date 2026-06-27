from pathlib import Path

from ppt_agent.load import load_deck, load_theme
from ppt_agent.renderer import render_deck_to_pptx


def main() -> Path:
    repo_root = Path(__file__).resolve().parents[1]
    examples_dir = repo_root / "examples"
    output_path = examples_dir / "output" / "sample_deck.pptx"

    deck = load_deck(examples_dir / "sample_slide_ir.json")
    theme = load_theme(examples_dir / "theme.json")
    return render_deck_to_pptx(deck, theme, output_path, assets_dir=examples_dir)


if __name__ == "__main__":
    print(main())

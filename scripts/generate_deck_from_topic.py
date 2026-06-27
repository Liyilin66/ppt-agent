import os
from pathlib import Path

from ppt_agent.export import write_model_json
from ppt_agent.generation import DeckGenerationRequest, generate_deck_with_model
from ppt_agent.load import load_theme
from ppt_agent.qa import analyze_deck
from ppt_agent.renderer import render_deck_to_pptx


def main() -> int:
    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set. Set it to run the optional LLM deck generation demo.")
        return 0

    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        print("langchain-openai is not installed. Run: uv sync")
        return 1

    repo_root = Path(__file__).resolve().parents[1]
    examples_dir = repo_root / "examples"
    output_dir = examples_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    request = DeckGenerationRequest(
        topic=os.getenv("PPT_AGENT_TOPIC", "AI readiness roadmap for a mid-sized business"),
        audience=os.getenv("PPT_AGENT_AUDIENCE", "executive leadership team"),
        slide_count=int(os.getenv("PPT_AGENT_SLIDE_COUNT", "4")),
        style=os.getenv("PPT_AGENT_STYLE", "clean_business"),
        language=os.getenv("PPT_AGENT_LANGUAGE", "en"),
        key_points=[
            "current-state assessment",
            "highest-value use cases",
            "governance and risk controls",
            "90-day implementation roadmap",
        ],
    )

    model_name = os.getenv("OPENAI_MODEL", "gpt-5.5")
    model = ChatOpenAI(model=model_name)
    theme = load_theme(examples_dir / "theme.json")

    deck = generate_deck_with_model(model, request)
    qa_report = analyze_deck(deck, theme)

    deck_path = write_model_json(deck, output_dir / "generated_deck_ir.json")
    qa_path = write_model_json(qa_report, output_dir / "generated_qa_report.json")
    pptx_path = render_deck_to_pptx(deck, theme, output_dir / "generated_deck.pptx", assets_dir=examples_dir)

    print(f"generated_deck_ir: {deck_path}")
    print(f"generated_qa_report: {qa_path}")
    print(f"generated_deck: {pptx_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

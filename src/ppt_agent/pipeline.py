"""Reusable build pipeline service for Deck IR generation and rendering."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from ppt_agent.export import write_model_json
from ppt_agent.generation import DeckGenerationRequest, GenerationResult, generate_deck_with_quality_gate
from ppt_agent.load import load_patch, load_theme
from ppt_agent.models import StrictModel
from ppt_agent.patch import PatchResult, apply_patch
from ppt_agent.renderer import render_deck_to_pptx


class BuildPipelineRequest(StrictModel):
    generation_request: DeckGenerationRequest
    theme_path: Path
    output_dir: Path
    min_qa_score: int = Field(default=80, ge=0, le=100)
    max_attempts: int = Field(default=2, ge=1)
    assets_dir: Path | None = None
    patch_path: Path | None = None


class BuildArtifact(StrictModel):
    name: str = Field(..., min_length=1)
    path: Path
    kind: Literal["json", "pptx"]


class BuildPipelineResult(StrictModel):
    generation_result: GenerationResult
    patch_result: PatchResult | None = None
    artifacts: list[BuildArtifact] = Field(default_factory=list)
    accepted: bool
    status_code: int
    messages: list[str] = Field(default_factory=list)


def _artifact(name: str, path: Path, kind: Literal["json", "pptx"]) -> BuildArtifact:
    return BuildArtifact(name=name, path=path, kind=kind)


def run_build_pipeline(model: Any, request: BuildPipelineRequest) -> BuildPipelineResult:
    """Run the product build pipeline and write all configured artifacts."""

    theme = load_theme(request.theme_path)
    generation_request = request.generation_request
    if generation_request.style is None:
        generation_request = generation_request.model_copy(update={"style": theme.name})

    generation_result = generate_deck_with_quality_gate(
        model,
        generation_request,
        theme=theme,
        min_score=request.min_qa_score,
        max_attempts=request.max_attempts,
    )

    output_dir = request.output_dir
    artifacts: list[BuildArtifact] = []

    deck_path = write_model_json(generation_result.deck, output_dir / "generated_deck_ir.json")
    artifacts.append(_artifact("generated_deck_ir", deck_path, "json"))

    qa_path = write_model_json(generation_result.qa_report, output_dir / "generated_qa_report.json")
    artifacts.append(_artifact("generated_qa_report", qa_path, "json"))

    attempts_path = write_model_json(generation_result, output_dir / "generated_attempts.json")
    artifacts.append(_artifact("generated_attempts", attempts_path, "json"))

    pptx_path = render_deck_to_pptx(
        generation_result.deck,
        theme,
        output_dir / "generated_deck.pptx",
        assets_dir=request.assets_dir,
    )
    artifacts.append(_artifact("generated_deck", pptx_path, "pptx"))

    status_code = 0
    messages: list[str] = []
    if not generation_result.accepted:
        messages.append(
            "Build completed, but generated Deck IR did not meet the QA score gate: "
            f"{generation_result.qa_report.score} < {request.min_qa_score}"
        )
        status_code = 2

    patch_result: PatchResult | None = None
    if request.patch_path is not None:
        patch_result = apply_patch(generation_result.deck, load_patch(request.patch_path))

        patched_deck_path = write_model_json(patch_result.deck, output_dir / "patched_deck_ir.json")
        artifacts.append(_artifact("patched_deck_ir", patched_deck_path, "json"))

        patch_result_path = write_model_json(patch_result, output_dir / "patch_result.json")
        artifacts.append(_artifact("patch_result", patch_result_path, "json"))

        patched_pptx_path = render_deck_to_pptx(
            patch_result.deck,
            theme,
            output_dir / "patched_deck.pptx",
            assets_dir=request.assets_dir,
        )
        artifacts.append(_artifact("patched_deck", patched_pptx_path, "pptx"))

        if patch_result.issues:
            messages.append(f"Patch completed with {len(patch_result.issues)} issue(s). See {patch_result_path}.")
            status_code = 2

    return BuildPipelineResult(
        generation_result=generation_result,
        patch_result=patch_result,
        artifacts=artifacts,
        accepted=status_code == 0,
        status_code=status_code,
        messages=messages,
    )

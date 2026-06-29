"""Long-deck merge and validation helpers."""

from __future__ import annotations

from ppt_agent.generation import BatchGenerationArtifact, validate_batch_deck_ir_against_batch_range
from ppt_agent.layouts import is_template_layout
from ppt_agent.models import Deck, Slide
from ppt_agent.planning import LongDeckPlan, SectionPlan, get_batch_context


OPENING_SECTION_MARKERS = ("cover", "context", "opening")
CONCLUSION_SECTION_MARKERS = ("conclusion", "closing", "action", "summary")


def _section_for_slide(long_deck_plan: LongDeckPlan, slide_number: int) -> SectionPlan:
    for section in long_deck_plan.sections:
        if section.start_slide <= slide_number <= section.end_slide:
            return section
    raise ValueError(
        f"LongDeckPlan does not contain a section for slide {slide_number}."
    )


def _section_text(section: SectionPlan) -> str:
    return " ".join([section.section_id, section.title, section.purpose]).lower()


def _matches_any_marker(section: SectionPlan, markers: tuple[str, ...]) -> bool:
    text = _section_text(section)
    return any(marker in text for marker in markers)


def _slide_has_non_empty_text(slide: Slide) -> bool:
    return any(
        element.type == "text" and bool(element.text.strip())
        for element in slide.elements
    )


def validate_merged_long_deck_ir(deck_ir: Deck, long_deck_plan: LongDeckPlan) -> Deck:
    expected_count = long_deck_plan.slide_count
    actual_count = len(deck_ir.slides)
    if actual_count != expected_count:
        raise ValueError(
            f"Merged long deck has {actual_count} slides, but LongDeckPlan requires {expected_count}."
        )

    actual_slide_ids = [slide.slide_id for slide in deck_ir.slides]
    expected_slide_ids = [
        f"slide_{slide_number:03d}"
        for slide_number in range(1, expected_count + 1)
    ]

    duplicate_slide_ids = sorted(
        {slide_id for slide_id in actual_slide_ids if actual_slide_ids.count(slide_id) > 1}
    )
    if duplicate_slide_ids:
        raise ValueError(
            "Merged long deck contains duplicate slide_id values: "
            + ", ".join(duplicate_slide_ids)
        )

    if actual_slide_ids != expected_slide_ids:
        expected_set = set(expected_slide_ids)
        actual_set = set(actual_slide_ids)
        missing_slide_ids = [slide_id for slide_id in expected_slide_ids if slide_id not in actual_set]
        extra_slide_ids = [slide_id for slide_id in actual_slide_ids if slide_id not in expected_set]
        raise ValueError(
            "Merged long deck slide_id values must cover the absolute range "
            f"slide_001 to slide_{expected_count:03d}; missing={missing_slide_ids}, extra={extra_slide_ids}."
        )

    for slide in deck_ir.slides:
        if not slide.title.strip():
            raise ValueError(f"Merged long deck contains an empty title on slide '{slide.slide_id}'.")
        if not _slide_has_non_empty_text(slide):
            raise ValueError(
                f"Merged long deck slide '{slide.slide_id}' is missing non-empty text content."
            )
        if not is_template_layout(slide.layout):
            raise ValueError(
                f"Merged long deck slide '{slide.slide_id}' uses unsupported layout '{slide.layout}'."
            )

    first_section = _section_for_slide(long_deck_plan, 1)
    if not _matches_any_marker(first_section, OPENING_SECTION_MARKERS):
        raise ValueError(
            "The first slide must belong to an opening cover/context section."
        )
    if deck_ir.slides[0].layout not in first_section.preferred_layouts:
        raise ValueError(
            f"First slide layout '{deck_ir.slides[0].layout}' does not match opening section "
            f"preferred layouts {first_section.preferred_layouts}."
        )

    last_section = _section_for_slide(long_deck_plan, expected_count)
    if not _matches_any_marker(last_section, CONCLUSION_SECTION_MARKERS):
        raise ValueError(
            "The last slide must belong to a conclusion/action section."
        )
    if deck_ir.slides[-1].layout not in last_section.preferred_layouts:
        raise ValueError(
            f"Last slide layout '{deck_ir.slides[-1].layout}' does not match conclusion section "
            f"preferred layouts {last_section.preferred_layouts}."
        )

    return deck_ir


def merge_batch_deck_irs(
    long_deck_plan: LongDeckPlan,
    batch_artifacts: list[BatchGenerationArtifact],
) -> Deck:
    expected_batch_ids = [batch.batch_id for batch in long_deck_plan.batches]
    artifacts_by_id: dict[str, BatchGenerationArtifact] = {}

    for artifact in batch_artifacts:
        if artifact.batch_id not in expected_batch_ids:
            raise ValueError(
                f"Unknown batch_id '{artifact.batch_id}' for the provided LongDeckPlan."
            )
        if artifact.batch_id in artifacts_by_id:
            raise ValueError(f"Duplicate batch_id '{artifact.batch_id}' in batch artifacts.")
        artifacts_by_id[artifact.batch_id] = artifact

    missing_batch_ids = [
        batch_id for batch_id in expected_batch_ids if batch_id not in artifacts_by_id
    ]
    if missing_batch_ids:
        raise ValueError(
            "Missing batch artifacts for LongDeckPlan batches: "
            + ", ".join(missing_batch_ids)
        )

    ordered_artifacts = [artifacts_by_id[batch_id] for batch_id in expected_batch_ids]
    ordered_decks: list[Deck] = []
    theme_name: str | None = None
    canvas_width_in: float | None = None
    canvas_height_in: float | None = None

    for artifact in ordered_artifacts:
        batch_context = get_batch_context(long_deck_plan, artifact.batch_id)
        deck = validate_batch_deck_ir_against_batch_range(artifact.deck_ir, batch_context)

        if canvas_width_in is None:
            canvas_width_in = deck.canvas_width_in
            canvas_height_in = deck.canvas_height_in
        elif deck.canvas_width_in != canvas_width_in or deck.canvas_height_in != canvas_height_in:
            raise ValueError(
                f"Batch '{artifact.batch_id}' uses a different canvas size and cannot be merged."
            )

        if theme_name is None:
            theme_name = deck.theme_name
        elif deck.theme_name is not None and deck.theme_name != theme_name:
            raise ValueError(
                f"Batch '{artifact.batch_id}' uses theme '{deck.theme_name}', expected '{theme_name}'."
            )

        ordered_decks.append(deck)

    merged_slides = [slide for deck in ordered_decks for slide in deck.slides]
    base_deck = ordered_decks[0]
    merged_deck = base_deck.model_copy(
        update={
            "title": long_deck_plan.topic,
            "theme_name": theme_name,
            "canvas_width_in": canvas_width_in,
            "canvas_height_in": canvas_height_in,
            "slides": merged_slides,
        }
    )
    validate_merged_long_deck_ir(merged_deck, long_deck_plan)
    return Deck.model_validate(merged_deck.model_dump(mode="json"))

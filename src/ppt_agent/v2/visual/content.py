"""Content schemas for layout archetypes: what the model writes.

The model never emits coordinates. It picks an archetype and fills these
fields; hard length limits keep every field typesettable. Ordering carries
meaning: the first insight / metric is the page's key message, which some
compositions promote to a hero element.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from ppt_agent.models import StrictModel


class PageCopy(StrictModel):
    title: str = Field(..., min_length=1, max_length=40)
    kicker: str | None = Field(default=None, max_length=24)
    lead: str | None = Field(default=None, max_length=60)
    takeaway: str | None = Field(default=None, max_length=70)
    # Real URLs run long; the footer shows a shortened "domain · title" form.
    source: str | None = Field(default=None, max_length=400)
    speaker_notes: str | None = None


class Insight(StrictModel):
    value: str | None = Field(default=None, max_length=10)
    text: str = Field(..., min_length=1, max_length=48)


class PointItem(StrictModel):
    heading: str = Field(..., min_length=1, max_length=18)
    body: str = Field(..., min_length=1, max_length=100)
    # Real file citations ("caict_ai_2024.pdf 第15-16页") run past 24 characters.
    ref: str | None = Field(default=None, max_length=40)


class PointsContent(PageCopy):
    archetype: Literal["points"] = "points"
    items: list[PointItem] = Field(..., min_length=2, max_length=5)
    highlight: Insight | None = None  # the page's key figure, when the evidence has one


class StepItem(StrictModel):
    label: str = Field(..., min_length=1, max_length=10)
    body: str = Field(..., min_length=1, max_length=70)


class ProcessContent(PageCopy):
    archetype: Literal["process"] = "process"
    steps: list[StepItem] = Field(..., min_length=3, max_length=6)


class ChartContent(PageCopy):
    archetype: Literal["chart"] = "chart"
    chart: Literal["bar", "column"] = "bar"
    chart_title: str = Field(..., min_length=1, max_length=30)
    categories: list[str] = Field(..., min_length=2, max_length=8)
    values: list[float] = Field(..., min_length=2, max_length=8)
    unit_format: str | None = Field(default=None, description='e.g. 0.0"%"')
    insights: list[Insight] = Field(..., min_length=1, max_length=3)


class MetricItem(StrictModel):
    value: str = Field(..., min_length=1, max_length=10)
    label: str = Field(..., min_length=1, max_length=16)
    note: str | None = Field(default=None, max_length=50)


class MetricsContent(PageCopy):
    archetype: Literal["metrics"] = "metrics"
    metrics: list[MetricItem] = Field(..., min_length=2, max_length=4)


class StatementContent(PageCopy):
    """A breathing page: one sentence carries the slide."""

    archetype: Literal["statement"] = "statement"
    statement: str = Field(..., min_length=1, max_length=44)
    support: str | None = Field(default=None, max_length=100)


class CompareSide(StrictModel):
    heading: str = Field(..., min_length=1, max_length=16)
    points: list[Annotated[str, Field(min_length=1, max_length=50)]] = Field(
        ..., min_length=2, max_length=5
    )


class CompareContent(PageCopy):
    """Two options, states or camps side by side; ``right`` is the favoured one."""

    archetype: Literal["compare"] = "compare"
    left: CompareSide
    right: CompareSide


class Milestone(StrictModel):
    date: str = Field(..., min_length=1, max_length=12)
    label: str = Field(..., min_length=1, max_length=14)
    body: str | None = Field(default=None, max_length=60)


class TimelineContent(PageCopy):
    archetype: Literal["timeline"] = "timeline"
    milestones: list[Milestone] = Field(..., min_length=3, max_length=6)


class TableContent(PageCopy):
    """Facts compared across the same attributes: cases, regions, options."""

    archetype: Literal["table"] = "table"
    headers: list[Annotated[str, Field(min_length=1, max_length=12)]] = Field(..., min_length=2, max_length=5)
    rows: list[list[Annotated[str, Field(min_length=1, max_length=40)]]] = Field(..., min_length=2, max_length=6)

    @model_validator(mode="after")
    def _rows_match_headers(self) -> "TableContent":
        if any(len(row) != len(self.headers) for row in self.rows):
            raise ValueError("every table row needs one cell per header")
        return self


class SummaryRow(StrictModel):
    tag: str = Field(..., min_length=1, max_length=6)
    headline: str = Field(..., min_length=1, max_length=36)
    detail: str = Field(..., min_length=1, max_length=80)


class SummaryContent(PageCopy):
    """Executive summary, written after the rest of the deck exists."""

    archetype: Literal["summary"] = "summary"
    rows: list[SummaryRow] = Field(..., min_length=2, max_length=4)


class RoadmapRow(StrictModel):
    label: str = Field(..., min_length=1, max_length=8)
    body: str = Field(..., min_length=1, max_length=44)
    start: int = Field(..., ge=1)  # first phase, 1-based
    end: int = Field(..., ge=1)  # last phase, inclusive


class RoadmapContent(PageCopy):
    """Recommended actions over time; its phases are advice, not source facts."""

    archetype: Literal["roadmap"] = "roadmap"
    phases: list[Annotated[str, Field(min_length=1, max_length=10)]] = Field(..., min_length=2, max_length=4)
    rows: list[RoadmapRow] = Field(..., min_length=2, max_length=5)

    @model_validator(mode="after")
    def _bars_fit_phases(self) -> "RoadmapContent":
        if any(not row.start <= row.end <= len(self.phases) for row in self.rows):
            raise ValueError("roadmap rows must start and end inside phases")
        return self


Relation = Literal[
    "single",      # one figure or statement carries the page
    "comparison",  # two or more things set against each other
    "trend",       # one measure changing over time
    "proportion",  # a part of a whole
    "ranking",     # the same measure across several things, ordered
    "sequence",    # steps that follow one another
    "timeline",    # dated events
    "structure",   # parts of a whole and how they relate
    "hierarchy",   # levels that rest on one another, the top one first
    "cause",       # one thing leading to another
    "cases",       # named examples supporting one claim
]
Tier = Literal["visual", "standard", "deep"]


class RichItem(StrictModel):
    heading: str = Field(..., min_length=1, max_length=18)
    body: str | None = Field(default=None, max_length=90)
    value: str | None = Field(default=None, max_length=12)  # the item's figure, when it has one
    date: str | None = Field(default=None, max_length=12)   # for timelines
    ref: str | None = Field(default=None, max_length=40)


class DataPoint(StrictModel):
    """One value of a series, with the words of the source it was read from."""

    label: str = Field(..., min_length=1, max_length=20)    # a category or a point in time: "2022年"
    value: float
    display: str = Field(..., min_length=1, max_length=16)  # as the source writes it: "4.5%"
    quote: str = Field(..., min_length=1, max_length=160)   # the source's own words around the value
    ref: str = Field(..., min_length=1, max_length=60)      # "<filename> 第N页"
    projected: bool = False                                 # a forecast or a plan, not an observation


SeriesKind = Literal["time", "category", "share", "distribution"]


class DataSeries(StrictModel):
    """One measure in one unit at several points: the only thing a chart may draw."""

    metric: str = Field(..., min_length=1, max_length=36)   # what is measured
    unit: str = Field(default="", max_length=10)            # "%", "亿美元"
    kind: SeriesKind
    period: str | None = Field(default=None, max_length=28)  # "2022年—2024年上半年"
    scope: str | None = Field(default=None, max_length=30)   # whose it is: "全球"
    points: list[DataPoint] = Field(..., min_length=2, max_length=16)
    # Values the source also gives that could not be checked, so are not drawn: named, never hidden.
    omitted: list[Annotated[str, Field(max_length=20)]] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def _points_are_one_series(self) -> "DataSeries":
        labels = [point.label for point in self.points]
        if len(set(labels)) != len(labels):
            raise ValueError("a series cannot have two points with one label")
        if self.kind == "share":
            values = [point.value for point in self.points]
            if any(not 0 <= value <= 100 for value in values) or sum(values) > 100.5:
                raise ValueError("shares lie between 0 and 100 and cannot add up to more than the whole")
        return self


class RichContent(PageCopy):
    """A page for the model-designed (HTML) engine: one claim, one focal fact,
    supporting facts, and how they relate. Deliberately not part of
    ``AnyContent``: the code-driven engine lays it out as plain points."""

    archetype: Literal["rich"] = "rich"
    relation: Relation = "cases"
    tier: Tier = "standard"
    highlight: Insight | None = None  # the focal fact: {value, text}
    items: list[RichItem] = Field(..., min_length=1, max_length=8)
    # One measure across categories, when the evidence has it (becomes a native chart).
    chart_title: str | None = Field(default=None, max_length=30)
    categories: list[str] | None = Field(default=None, max_length=8)
    values: list[float] | None = Field(default=None, max_length=8)
    unit_format: str | None = None
    # A grid of short cells, when rows share the same attributes (becomes a native table).
    headers: list[Annotated[str, Field(min_length=1, max_length=12)]] | None = Field(default=None, max_length=6)
    rows: list[list[Annotated[str, Field(min_length=1, max_length=40)]]] | None = Field(default=None, max_length=8)
    # Series checked against the source by code (v2.data_series), never written by the page's author.
    series: list[DataSeries] = Field(default_factory=list, max_length=2)
    # A figure reproduced from the source document: {"id", "caption", "ref", "path", "width", "height"}.
    figure: dict | None = None
    # A figure of the source redrawn from what two readings of it agree on: {"kind","levels","groups","caption"}.
    diagram: dict | None = None
    # What the page claims, the measure it rests on, and the part its visual plays (v2.visual_brief).
    visual: dict | None = None

    @model_validator(mode="after")
    def _data_is_consistent(self) -> "RichContent":
        if (self.categories is None) != (self.values is None):
            raise ValueError("categories and values go together")
        if self.categories is not None and len(self.categories) != len(self.values):
            raise ValueError("categories and values must have identical lengths")
        if self.categories is not None and len(self.categories) < 2:
            raise ValueError("a series needs at least two values")
        if (self.headers is None) != (self.rows is None):
            raise ValueError("headers and rows go together")
        if self.rows and any(len(row) != len(self.headers) for row in self.rows):
            raise ValueError("every table row needs one cell per header")
        return self

    def to_points(self) -> "PointsContent":
        """The same page as plain points, for the code-driven layout engine."""

        items = [
            PointItem(heading=item.heading, body=(
                f"{item.value} {item.body or ''}".strip() if item.value else item.body or item.heading)[:100])
            for item in self.items[:4]
        ]
        while len(items) < 2:
            items.append(PointItem(heading=self.title[:18], body=(self.takeaway or self.lead or self.title)[:100]))
        return PointsContent(title=self.title, kicker=self.kicker, lead=self.lead, takeaway=self.takeaway,
                             source=self.source, speaker_notes=self.speaker_notes, items=items,
                             highlight=self.highlight)


AnyContent = (
    PointsContent | ProcessContent | ChartContent | MetricsContent | StatementContent
    | CompareContent | TimelineContent | TableContent | SummaryContent | RoadmapContent
)

ArchetypeContent = Annotated[AnyContent, Field(discriminator="archetype")]

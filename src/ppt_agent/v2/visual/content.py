"""Content schemas for layout archetypes: what the model writes.

The model never emits coordinates. It picks an archetype and fills these
fields; hard length limits keep every field typesettable. Ordering carries
meaning: the first insight / metric is the page's key message, which some
compositions promote to a hero element.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from ppt_agent.models import StrictModel


class PageCopy(StrictModel):
    title: str = Field(..., min_length=1, max_length=40)
    kicker: str | None = Field(default=None, max_length=24)
    lead: str | None = Field(default=None, max_length=60)
    takeaway: str | None = Field(default=None, max_length=70)
    source: str | None = Field(default=None, max_length=90)
    speaker_notes: str | None = None


class PointItem(StrictModel):
    heading: str = Field(..., min_length=1, max_length=18)
    body: str = Field(..., min_length=1, max_length=80)
    ref: str | None = Field(default=None, max_length=24)


class PointsContent(PageCopy):
    archetype: Literal["points"] = "points"
    items: list[PointItem] = Field(..., min_length=2, max_length=5)


class StepItem(StrictModel):
    label: str = Field(..., min_length=1, max_length=10)
    body: str = Field(..., min_length=1, max_length=44)


class ProcessContent(PageCopy):
    archetype: Literal["process"] = "process"
    steps: list[StepItem] = Field(..., min_length=3, max_length=6)


class Insight(StrictModel):
    value: str | None = Field(default=None, max_length=10)
    text: str = Field(..., min_length=1, max_length=48)


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
    note: str | None = Field(default=None, max_length=40)


class MetricsContent(PageCopy):
    archetype: Literal["metrics"] = "metrics"
    metrics: list[MetricItem] = Field(..., min_length=2, max_length=4)


class StatementContent(PageCopy):
    """A breathing page: one sentence carries the slide."""

    archetype: Literal["statement"] = "statement"
    statement: str = Field(..., min_length=1, max_length=44)
    support: str | None = Field(default=None, max_length=100)


AnyContent = PointsContent | ProcessContent | ChartContent | MetricsContent | StatementContent

ArchetypeContent = Annotated[AnyContent, Field(discriminator="archetype")]

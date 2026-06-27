"""Theme schema for consistent future slide generation and rendering."""

from __future__ import annotations

from pydantic import Field

from ppt_agent.models import HexColor, StrictModel, TextStyle


class ThemeSlideSize(StrictModel):
    width_in: float = Field(default=13.333, gt=0)
    height_in: float = Field(default=7.5, gt=0)


class ThemeColors(StrictModel):
    background: HexColor
    surface: HexColor
    text: HexColor
    muted_text: HexColor
    primary: HexColor
    secondary: HexColor
    accent: HexColor


class ThemeFonts(StrictModel):
    heading: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)


class Theme(StrictModel):
    name: str = Field(..., min_length=1)
    description: str | None = None
    slide_size: ThemeSlideSize = Field(default_factory=ThemeSlideSize)
    colors: ThemeColors
    fonts: ThemeFonts
    default_text_style: TextStyle = Field(default_factory=TextStyle)

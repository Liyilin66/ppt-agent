"""How many slides a set of sources can carry before pages start repeating.

Calibrated on one observation, so treat it as advice: a 64-page report with
about 40,000 characters and 93 distinct figures made a sound 20-page deck and
a visibly repetitive 100-page one (the same figure on eight pages).
"""

from __future__ import annotations

CHARS_PER_SLIDE = 800       # source text one content slide can draw on
CHARS_PER_THIN_SLIDE = 1500  # floor for sources with few figures
FIGURES_WEIGHT = 0.6        # most figures support a page; few can lead one
SMALL_DECK = 12             # below this the estimate is too coarse to object


def content_slides(characters: int, distinct_figures: int) -> int:
    by_text = characters / CHARS_PER_SLIDE
    by_substance = max(characters / CHARS_PER_THIN_SLIDE,
                       distinct_figures * FIGURES_WEIGHT + characters / 3000)
    return max(1, round(min(by_text, by_substance)))


def suggested_slides(characters: int, distinct_figures: int, *, chapters: int = 0,
                     structural: int = 3) -> int:
    """Total slides including cover, agenda, closing and one divider per chapter."""

    return max(SMALL_DECK, content_slides(characters, distinct_figures) + structural + chapters)

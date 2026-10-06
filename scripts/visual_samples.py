"""Render the visual-system samples: one 8-page CNNIC briefing per style profile.

Usage: uv run python scripts/visual_samples.py [output_dir]
Writes one PPTX per profile (each profile is its own theme) plus notes.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ppt_agent.v2.render import render_deck
from ppt_agent.v2.visual.archetypes import assemble_deck
from ppt_agent.v2.visual.profiles import PROFILES
from ppt_agent.v2.visual.samples import CNNIC_DECK, CNNIC_DECK_TITLE


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "data/visual-system/decks")
    out.mkdir(parents=True, exist_ok=True)
    report = {}
    for name, profile in PROFILES.items():
        deck, notes = assemble_deck(CNNIC_DECK, profile, deck_title=CNNIC_DECK_TITLE,
                                    subtitle="基于 CNNIC《生成式人工智能应用发展报告（2025）》")
        path = render_deck(deck, out / f"{name}.pptx")
        (out / f"{name}_design.json").write_text(deck.model_dump_json(indent=1), encoding="utf-8")
        report[name] = {"pptx": str(path), "notes": notes}
        print(name, path, notes)
    (out / "notes.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()

"""Build a deck from the best version of each page.

Versions are whole runs. Choosing among them happens page by page, on the
pages as they were already designed and checked: nothing is generated again.
The chosen pages are taken from each run's saved design and rendered together.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from ppt_agent.v2.ir import DeckDesign
from ppt_agent.v2.render import render_deck


def design_of(run: Path) -> dict[str, Any]:
    return json.loads(next(Path(run).glob("*_design.json")).read_text(encoding="utf-8"))


def assemble(base_run: Path, choices: dict[int, Path], out_dir: Path, name: str = "selected") -> Path:
    """The base run's deck, with each page in ``choices`` taken from another run.

    The base run is only read. Pictures the chosen pages use are copied along."""

    out_dir = Path(out_dir)
    (out_dir / "assets").mkdir(parents=True, exist_ok=True)
    deck = design_of(base_run)
    for source in {Path(base_run), *map(Path, choices.values())}:
        if (source / "assets").is_dir():
            for asset in (source / "assets").iterdir():
                shutil.copy2(asset, out_dir / "assets" / asset.name)
    for number, run in choices.items():
        page = next(page for page in design_of(run)["pages"] if page["page_number"] == number)
        index = next(index for index, existing in enumerate(deck["pages"]) if existing["page_number"] == number)
        deck["pages"][index] = page
    design = DeckDesign.model_validate(deck)
    (out_dir / f"{name}_design.json").write_text(design.model_dump_json(indent=2), encoding="utf-8")
    return render_deck(design, out_dir / f"{name}.pptx", assets_dir=out_dir / "assets")

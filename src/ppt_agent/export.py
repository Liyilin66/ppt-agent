"""Export helpers for Pydantic artifacts."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


def write_model_json(model: BaseModel, path: str | Path) -> Path:
    """Write a Pydantic model as readable JSON."""

    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return output_path

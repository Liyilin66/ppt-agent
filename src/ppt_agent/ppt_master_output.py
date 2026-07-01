"""Registration helpers for locally generated ppt-master output artifacts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from pydantic import Field

from ppt_agent.job_store import ArtifactRecord, JobStore
from ppt_agent.models import StrictModel


PPT_MASTER_OUTPUT_PPTX_ARTIFACT = "ppt_master_generated_pptx"
PPT_MASTER_OUTPUT_NOTES_ARTIFACT = "ppt_master_generation_notes"
PPT_MASTER_OUTPUT_MANIFEST_ARTIFACT = "ppt_master_output_manifest"
PPT_MASTER_OUTPUT_MANIFEST_FILENAME = "ppt_master_output_manifest.json"
PPT_MASTER_OUTPUT_PPTX_FILENAME = "generated_by_ppt_master.pptx"
PPT_MASTER_OUTPUT_NOTES_FILENAME = "generation_notes.md"


class PptMasterOutputManifest(StrictModel):
    job_id: str | None = None
    output_dir: Path
    detected: bool
    pptx_path: Path | None = None
    notes_path: Path | None = None
    project_dir: Path | None = None
    slide_count: int | None = Field(default=None, ge=0)
    is_editable_claimed: bool | None = None
    generation_status: str
    warnings: list[str] = Field(default_factory=list)
    created_at: str


class PptMasterOutputRegistrationResult(StrictModel):
    manifest_path: Path
    manifest: PptMasterOutputManifest
    pptx_artifact: ArtifactRecord | None = None
    notes_artifact: ArtifactRecord | None = None
    manifest_artifact: ArtifactRecord


def detect_ppt_master_output(output_dir: Path) -> PptMasterOutputManifest:
    """Inspect a local ppt-master output directory without running ppt-master."""

    resolved_output_dir = output_dir.expanduser().resolve(strict=False)
    warnings: list[str] = []
    pptx_path = resolved_output_dir / PPT_MASTER_OUTPUT_PPTX_FILENAME
    notes_path = resolved_output_dir / PPT_MASTER_OUTPUT_NOTES_FILENAME

    if not resolved_output_dir.is_dir():
        return PptMasterOutputManifest(
            job_id=_detect_job_id_from_output_dir(resolved_output_dir),
            output_dir=resolved_output_dir,
            detected=False,
            generation_status="missing_output_dir",
            warnings=[f"Output directory not found: {resolved_output_dir}"],
            created_at=_now(),
        )

    detected = pptx_path.is_file()
    if not detected:
        warnings.append(f"PPTX output not found: {pptx_path}")

    notes_text: str | None = None
    if notes_path.is_file():
        try:
            notes_text = notes_path.read_text(encoding="utf-8")
        except OSError as exc:
            warnings.append(f"Could not read generation notes: {exc}")
    else:
        warnings.append(f"Generation notes not found: {notes_path}")

    slide_count: int | None = None
    if detected:
        try:
            from pptx import Presentation

            slide_count = len(Presentation(pptx_path).slides)
        except Exception as exc:  # pragma: no cover - dependency/runtime failures are environment-specific.
            warnings.append(f"Could not read slide count from PPTX: {exc}")

    return PptMasterOutputManifest(
        job_id=_detect_job_id_from_output_dir(resolved_output_dir),
        output_dir=resolved_output_dir,
        detected=detected,
        pptx_path=pptx_path if detected else None,
        notes_path=notes_path if notes_path.is_file() else None,
        project_dir=_detect_project_dir(resolved_output_dir),
        slide_count=slide_count,
        is_editable_claimed=_detect_editable_claim(notes_text),
        generation_status="succeeded" if detected else "missing_pptx",
        warnings=warnings,
        created_at=_now(),
    )


def write_ppt_master_output_manifest(
    output_dir: Path,
    *,
    job_id: str | None = None,
) -> tuple[Path, PptMasterOutputManifest]:
    manifest = detect_ppt_master_output(output_dir)
    if job_id is not None:
        manifest = manifest.model_copy(update={"job_id": job_id})
    manifest_path = output_dir.expanduser().resolve(strict=False) / PPT_MASTER_OUTPUT_MANIFEST_FILENAME
    manifest_path.write_text(manifest.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return manifest_path, manifest


def register_ppt_master_output_artifacts(
    store: JobStore,
    *,
    job_id: str,
    output_dir: Path,
) -> PptMasterOutputRegistrationResult:
    manifest_path, manifest = write_ppt_master_output_manifest(output_dir, job_id=job_id)
    if not manifest.detected or manifest.pptx_path is None:
        raise ValueError(
            "PPT Master output is incomplete: generated_by_ppt_master.pptx was not found."
        )

    pptx_artifact = _ensure_artifact(
        store,
        job_id=job_id,
        name=PPT_MASTER_OUTPUT_PPTX_ARTIFACT,
        kind="pptx",
        path=manifest.pptx_path,
    )
    notes_artifact = None
    if manifest.notes_path is not None:
        notes_artifact = _ensure_artifact(
            store,
            job_id=job_id,
            name=PPT_MASTER_OUTPUT_NOTES_ARTIFACT,
            kind="md",
            path=manifest.notes_path,
        )
    manifest_artifact = _ensure_artifact(
        store,
        job_id=job_id,
        name=PPT_MASTER_OUTPUT_MANIFEST_ARTIFACT,
        kind="json",
        path=manifest_path,
    )

    return PptMasterOutputRegistrationResult(
        manifest_path=manifest_path,
        manifest=manifest,
        pptx_artifact=pptx_artifact,
        notes_artifact=notes_artifact,
        manifest_artifact=manifest_artifact,
    )


def _ensure_artifact(
    store: JobStore,
    *,
    job_id: str,
    name: str,
    kind: str,
    path: Path,
) -> ArtifactRecord:
    resolved_path = path.expanduser().resolve(strict=False)
    for artifact in reversed(store.list_artifacts(job_id)):
        if artifact.name != name:
            continue
        if artifact.kind == kind and artifact.path.expanduser().resolve(strict=False) == resolved_path:
            return artifact
    return store.add_artifact(job_id, name=name, kind=kind, path=resolved_path)


def _detect_project_dir(output_dir: Path) -> Path | None:
    for child in sorted(output_dir.iterdir()):
        if not child.is_dir():
            continue
        if (child / "svg_output").is_dir() or (child / "svg_final").is_dir() or (child / "sources").is_dir():
            return child.resolve(strict=False)
    return None


def _detect_editable_claim(notes_text: str | None) -> bool | None:
    if not notes_text:
        return None
    lowered = notes_text.lower()
    if "native drawingml shapes" in lowered or "directly editable" in lowered or "editable shapes" in lowered:
        return True
    return None


def _detect_job_id_from_output_dir(output_dir: Path) -> str | None:
    parts = output_dir.parts
    for index, part in enumerate(parts[:-1]):
        if part == "jobs" and index + 1 < len(parts):
            candidate = parts[index + 1]
            if candidate and candidate != "ppt_master_output":
                return candidate
    if output_dir.name == "ppt_master_output" and output_dir.parent.name:
        return output_dir.parent.name
    return None


def _now() -> str:
    return datetime.now(UTC).isoformat()

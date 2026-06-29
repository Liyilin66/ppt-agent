"""Local SQLite job and artifact store for the private beta API."""

from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import Field

from ppt_agent.models import StrictModel


JobStatus = Literal["pending", "running", "succeeded", "failed"]


class JobRecord(StrictModel):
    job_id: str = Field(..., min_length=1)
    status: JobStatus
    created_at: str
    updated_at: str
    current_stage: str | None = None
    last_updated_at: str
    elapsed_seconds: int = Field(default=0, ge=0)
    error_message: str | None = None
    accepted: bool | None = None
    qa_score: int | None = Field(default=None, ge=0, le=100)


class ArtifactRecord(StrictModel):
    artifact_id: str = Field(..., min_length=1)
    job_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    kind: Literal["json", "pptx"]
    path: Path
    created_at: str


class JobStore:
    """Small SQLite-backed store with one connection per operation."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self._initialized = False

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    def _ensure_schema(self) -> None:
        if self._initialized:
            return

        with self._lock:
            if self._initialized:
                return

            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(self.db_path) as connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS jobs (
                        job_id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        current_stage TEXT,
                        error_message TEXT,
                        accepted INTEGER,
                        qa_score INTEGER
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS artifacts (
                        artifact_id TEXT PRIMARY KEY,
                        job_id TEXT NOT NULL,
                        name TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        path TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(job_id) REFERENCES jobs(job_id)
                    )
                    """
                )
                connection.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_job_id ON artifacts(job_id)")
                self._ensure_job_columns(connection)
            self._initialized = True

    def _ensure_job_columns(self, connection: sqlite3.Connection) -> None:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()}
        if "current_stage" not in columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN current_stage TEXT")

    def _connect(self) -> sqlite3.Connection:
        self._ensure_schema()
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def create_job(self) -> JobRecord:
        job_id = uuid.uuid4().hex
        now = self._now()

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs (job_id, status, created_at, updated_at, current_stage)
                VALUES (?, ?, ?, ?, ?)
                """,
                (job_id, "pending", now, now, "create_job"),
            )

        job = self.get_job(job_id)
        if job is None:
            raise RuntimeError(f"Created job '{job_id}' could not be loaded.")
        return job

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT job_id, status, created_at, updated_at, current_stage, error_message, accepted, qa_score
                FROM jobs
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()

        if row is None:
            return None

        return JobRecord(
            job_id=row["job_id"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            current_stage=row["current_stage"],
            last_updated_at=row["updated_at"],
            elapsed_seconds=self._elapsed_seconds(row["created_at"], row["updated_at"], row["status"]),
            error_message=row["error_message"],
            accepted=None if row["accepted"] is None else bool(row["accepted"]),
            qa_score=row["qa_score"],
        )

    def update_job(
        self,
        job_id: str,
        *,
        status: JobStatus,
        error_message: str | None = None,
        accepted: bool | None = None,
        qa_score: int | None = None,
        current_stage: str | None = None,
    ) -> None:
        accepted_value = None if accepted is None else int(accepted)

        with self._connect() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET status = ?,
                    updated_at = ?,
                    current_stage = COALESCE(?, current_stage),
                    error_message = ?,
                    accepted = ?,
                    qa_score = ?
                WHERE job_id = ?
                """,
                (status, self._now(), current_stage, error_message, accepted_value, qa_score, job_id),
            )

    def update_progress(self, job_id: str, *, current_stage: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET current_stage = ?,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (current_stage, self._now(), job_id),
            )

    def add_artifact(self, job_id: str, *, name: str, kind: Literal["json", "pptx"], path: str | Path) -> ArtifactRecord:
        artifact_id = uuid.uuid4().hex
        now = self._now()
        artifact_path = Path(path)

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO artifacts (artifact_id, job_id, name, kind, path, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (artifact_id, job_id, name, kind, str(artifact_path), now),
            )

        artifact = self.get_artifact(artifact_id)
        if artifact is None:
            raise RuntimeError(f"Created artifact '{artifact_id}' could not be loaded.")
        return artifact

    def list_artifacts(self, job_id: str) -> list[ArtifactRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT artifact_id, job_id, name, kind, path, created_at
                FROM artifacts
                WHERE job_id = ?
                ORDER BY created_at, name
                """,
                (job_id,),
            ).fetchall()

        return [self._artifact_from_row(row) for row in rows]

    def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT artifact_id, job_id, name, kind, path, created_at
                FROM artifacts
                WHERE artifact_id = ?
                """,
                (artifact_id,),
            ).fetchone()

        if row is None:
            return None

        return self._artifact_from_row(row)

    def _artifact_from_row(self, row: sqlite3.Row) -> ArtifactRecord:
        return ArtifactRecord(
            artifact_id=row["artifact_id"],
            job_id=row["job_id"],
            name=row["name"],
            kind=row["kind"],
            path=Path(row["path"]),
            created_at=row["created_at"],
        )

    def _elapsed_seconds(self, created_at: str, updated_at: str, status: str) -> int:
        try:
            created = datetime.fromisoformat(created_at)
            end = datetime.now(UTC) if status in {"pending", "running"} else datetime.fromisoformat(updated_at)
        except ValueError:
            return 0
        return max(0, int((end - created).total_seconds()))

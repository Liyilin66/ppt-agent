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


JobStatus = Literal[
    "pending",
    "running",
    "succeeded",
    "failed",
    "failed_quality_gate",
    "partial_failed_quality_gate",
    "cancelled",
    "partial_cancelled",
]
ArtifactKind = Literal["json", "pptx", "md"]


class JobRecord(StrictModel):
    job_id: str = Field(..., min_length=1)
    status: JobStatus
    created_at: str
    updated_at: str
    current_stage: str | None = None
    last_updated_at: str
    elapsed_seconds: int = Field(default=0, ge=0)
    job_type: str | None = None
    error_message: str | None = None
    accepted: bool | None = None
    qa_score: int | None = Field(default=None, ge=0, le=100)
    cancel_requested: bool = False
    total_batches: int | None = Field(default=None, ge=0)
    completed_batches: int = Field(default=0, ge=0)
    failed_batches: int = Field(default=0, ge=0)
    current_batch: str | None = None


class ArtifactRecord(StrictModel):
    artifact_id: str = Field(..., min_length=1)
    job_id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    kind: ArtifactKind
    path: Path
    created_at: str


class PresentationRequestRecord(StrictModel):
    job_id: str = Field(..., min_length=1)
    topic: str = Field(..., min_length=1)
    audience: str = ""
    user_requirements: str = ""
    slide_count: int = Field(..., ge=1, le=100)
    interview_id: str | None = None
    resumed_from_job_id: str | None = None
    created_at: str


class PresentationHistoryRecord(StrictModel):
    job_id: str = Field(..., min_length=1)
    status: JobStatus
    job_type: str | None = None
    topic: str | None = None
    audience: str | None = None
    user_requirements: str | None = None
    slide_count: int | None = Field(default=None, ge=1, le=100)
    created_at: str
    updated_at: str
    accepted: bool | None = None
    qa_score: int | None = Field(default=None, ge=0, le=100)
    pptx_artifact_id: str | None = None
    pptx_artifact_name: str | None = None
    pptx_path: Path | None = None


class PresentationInterviewRecord(StrictModel):
    interview_id: str = Field(..., min_length=1)
    status: Literal["clarifying", "ready"]
    messages_json: str = Field(..., min_length=1)
    decision_json: str = Field(..., min_length=1)
    turn_count: int = Field(..., ge=1, le=20)
    created_at: str
    updated_at: str


DeckPlanStatus = Literal["planning", "ready", "failed", "confirmed"]

DeckRevisionStatus = Literal["running", "succeeded", "failed"]


class DeckRevisionRecord(StrictModel):
    revision_id: str = Field(..., min_length=1)
    job_id: str = Field(..., min_length=1)
    status: DeckRevisionStatus
    message: str = Field(..., min_length=1)
    reply: str = ""
    revised_pages_json: str = "[]"
    error_message: str | None = None
    created_at: str
    updated_at: str


class DeckPlanRecord(StrictModel):
    plan_id: str = Field(..., min_length=1)
    status: DeckPlanStatus
    request_json: str = Field(..., min_length=1)
    plan_json: str = ""
    error_message: str | None = None
    job_id: str | None = None
    created_at: str
    updated_at: str


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
                        job_type TEXT,
                        current_stage TEXT,
                        error_message TEXT,
                        accepted INTEGER,
                        qa_score INTEGER,
                        cancel_requested INTEGER DEFAULT 0,
                        total_batches INTEGER,
                        completed_batches INTEGER DEFAULT 0,
                        failed_batches INTEGER DEFAULT 0,
                        current_batch TEXT
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
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS presentation_requests (
                        job_id TEXT PRIMARY KEY,
                        topic TEXT NOT NULL,
                        audience TEXT NOT NULL,
                        user_requirements TEXT NOT NULL,
                        slide_count INTEGER NOT NULL,
                        interview_id TEXT,
                        resumed_from_job_id TEXT,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY(job_id) REFERENCES jobs(job_id)
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS presentation_interviews (
                        interview_id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        messages_json TEXT NOT NULL,
                        decision_json TEXT NOT NULL,
                        turn_count INTEGER NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS deck_plans (
                        plan_id TEXT PRIMARY KEY,
                        status TEXT NOT NULL,
                        request_json TEXT NOT NULL,
                        plan_json TEXT NOT NULL DEFAULT '',
                        error_message TEXT,
                        job_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS deck_revisions (
                        revision_id TEXT PRIMARY KEY,
                        job_id TEXT NOT NULL,
                        status TEXT NOT NULL,
                        message TEXT NOT NULL,
                        reply TEXT NOT NULL DEFAULT '',
                        revised_pages_json TEXT NOT NULL DEFAULT '[]',
                        error_message TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_deck_revisions_job_id ON deck_revisions(job_id)"
                )
                connection.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_job_id ON artifacts(job_id)")
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_presentation_requests_created_at "
                    "ON presentation_requests(created_at DESC)"
                )
                self._ensure_job_columns(connection)
                self._ensure_presentation_request_columns(connection)
            self._initialized = True

    def _ensure_job_columns(self, connection: sqlite3.Connection) -> None:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()}
        if "error_message" not in columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN error_message TEXT")
        if "accepted" not in columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN accepted INTEGER")
        if "qa_score" not in columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN qa_score INTEGER")
        if "current_stage" not in columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN current_stage TEXT")
        if "job_type" not in columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN job_type TEXT")
        if "cancel_requested" not in columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN cancel_requested INTEGER DEFAULT 0")
        if "total_batches" not in columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN total_batches INTEGER")
        if "completed_batches" not in columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN completed_batches INTEGER DEFAULT 0")
        if "failed_batches" not in columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN failed_batches INTEGER DEFAULT 0")
        if "current_batch" not in columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN current_batch TEXT")

    def _ensure_presentation_request_columns(self, connection: sqlite3.Connection) -> None:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(presentation_requests)").fetchall()
        }
        if "interview_id" not in columns:
            connection.execute("ALTER TABLE presentation_requests ADD COLUMN interview_id TEXT")

    def _connect(self) -> sqlite3.Connection:
        self._ensure_schema()
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def create_job(self, *, job_type: str | None = None) -> JobRecord:
        job_id = uuid.uuid4().hex
        now = self._now()

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO jobs (
                    job_id,
                    status,
                    created_at,
                    updated_at,
                    job_type,
                    current_stage,
                    cancel_requested,
                    completed_batches,
                    failed_batches
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (job_id, "pending", now, now, job_type, "create_job", 0, 0, 0),
            )

        job = self.get_job(job_id)
        if job is None:
            raise RuntimeError(f"Created job '{job_id}' could not be loaded.")
        return job

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    job_id,
                    status,
                    created_at,
                    updated_at,
                    job_type,
                    current_stage,
                    error_message,
                    accepted,
                    qa_score,
                    cancel_requested,
                    total_batches,
                    completed_batches,
                    failed_batches,
                    current_batch
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
            job_type=row["job_type"],
            error_message=row["error_message"],
            accepted=None if row["accepted"] is None else bool(row["accepted"]),
            qa_score=row["qa_score"],
            cancel_requested=bool(row["cancel_requested"]),
            total_batches=row["total_batches"],
            completed_batches=row["completed_batches"] or 0,
            failed_batches=row["failed_batches"] or 0,
            current_batch=row["current_batch"],
        )

    def get_latest_job(self, *, job_type: str | None = None) -> JobRecord | None:
        query = """
            SELECT
                job_id,
                status,
                created_at,
                updated_at,
                job_type,
                current_stage,
                error_message,
                accepted,
                qa_score,
                cancel_requested,
                total_batches,
                completed_batches,
                failed_batches,
                current_batch
            FROM jobs
        """
        params: tuple[str, ...] = ()
        if job_type is not None:
            query += " WHERE job_type = ?"
            params = (job_type,)
        query += " ORDER BY created_at DESC, updated_at DESC LIMIT 1"

        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
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
            job_type=row["job_type"],
            error_message=row["error_message"],
            accepted=None if row["accepted"] is None else bool(row["accepted"]),
            qa_score=row["qa_score"],
            cancel_requested=bool(row["cancel_requested"]),
            total_batches=row["total_batches"],
            completed_batches=row["completed_batches"] or 0,
            failed_batches=row["failed_batches"] or 0,
            current_batch=row["current_batch"],
        )

    def save_presentation_request(
        self,
        job_id: str,
        *,
        topic: str,
        audience: str,
        user_requirements: str,
        slide_count: int,
        interview_id: str | None = None,
        resumed_from_job_id: str | None = None,
    ) -> PresentationRequestRecord:
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO presentation_requests (
                    job_id,
                    topic,
                    audience,
                    user_requirements,
                    slide_count,
                    interview_id,
                    resumed_from_job_id,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET
                    topic = excluded.topic,
                    audience = excluded.audience,
                    user_requirements = excluded.user_requirements,
                    slide_count = excluded.slide_count,
                    interview_id = excluded.interview_id,
                    resumed_from_job_id = excluded.resumed_from_job_id
                """,
                (
                    job_id,
                    topic,
                    audience,
                    user_requirements,
                    slide_count,
                    interview_id,
                    resumed_from_job_id,
                    now,
                ),
            )

        request = self.get_presentation_request(job_id)
        if request is None:
            raise RuntimeError(f"Presentation request for job '{job_id}' could not be loaded.")
        return request

    def get_presentation_request(self, job_id: str) -> PresentationRequestRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    job_id,
                    topic,
                    audience,
                    user_requirements,
                    slide_count,
                    interview_id,
                    resumed_from_job_id,
                    created_at
                FROM presentation_requests
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        return PresentationRequestRecord(
            job_id=row["job_id"],
            topic=row["topic"],
            audience=row["audience"],
            user_requirements=row["user_requirements"],
            slide_count=row["slide_count"],
            interview_id=row["interview_id"],
            resumed_from_job_id=row["resumed_from_job_id"],
            created_at=row["created_at"],
        )

    def job_ids_missing_presentation_request(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT jobs.job_id
                FROM jobs
                LEFT JOIN presentation_requests
                    ON presentation_requests.job_id = jobs.job_id
                WHERE presentation_requests.job_id IS NULL
                ORDER BY jobs.created_at DESC
                """
            ).fetchall()
        return [row["job_id"] for row in rows]

    def list_presentation_history(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: JobStatus | None = None,
        query: str | None = None,
    ) -> tuple[list[PresentationHistoryRecord], int]:
        conditions: list[str] = []
        params: list[str | int] = []
        if status is not None:
            conditions.append("jobs.status = ?")
            params.append(status)
        normalized_query = (query or "").strip().lower()
        if normalized_query:
            pattern = f"%{normalized_query}%"
            conditions.append(
                "(LOWER(COALESCE(presentation_requests.topic, '')) LIKE ? "
                "OR LOWER(COALESCE(presentation_requests.audience, '')) LIKE ? "
                "OR LOWER(jobs.job_id) LIKE ?)"
            )
            params.extend([pattern, pattern, pattern])
        where_sql = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        joins = """
            FROM jobs
            LEFT JOIN presentation_requests
                ON presentation_requests.job_id = jobs.job_id
        """
        with self._connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) {joins}{where_sql}",
                params,
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT
                    jobs.job_id,
                    jobs.status,
                    jobs.job_type,
                    jobs.created_at,
                    jobs.updated_at,
                    jobs.accepted,
                    jobs.qa_score,
                    presentation_requests.topic,
                    presentation_requests.audience,
                    presentation_requests.user_requirements,
                    presentation_requests.slide_count,
                    final_artifact.artifact_id AS pptx_artifact_id,
                    final_artifact.name AS pptx_artifact_name,
                    final_artifact.path AS pptx_path
                {joins}
                LEFT JOIN artifacts AS final_artifact
                    ON final_artifact.artifact_id = (
                        SELECT candidate.artifact_id
                        FROM artifacts AS candidate
                        WHERE candidate.job_id = jobs.job_id
                            AND candidate.kind = 'pptx'
                        ORDER BY
                            CASE candidate.name
                                WHEN 'ppt_master_generated_pptx' THEN 0
                                WHEN 'generated_long_deck_v2' THEN 1
                                WHEN 'generated_long_deck' THEN 2
                                WHEN 'generated_pptx' THEN 3
                                WHEN 'generated_deck' THEN 4
                                ELSE 10
                            END,
                            candidate.created_at DESC
                        LIMIT 1
                    )
                {where_sql}
                ORDER BY jobs.created_at DESC, jobs.updated_at DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()

        return [self._presentation_history_from_row(row) for row in rows], int(total)

    def save_presentation_interview(
        self,
        *,
        interview_id: str,
        status: Literal["clarifying", "ready"],
        messages_json: str,
        decision_json: str,
        turn_count: int,
    ) -> PresentationInterviewRecord:
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO presentation_interviews (
                    interview_id,
                    status,
                    messages_json,
                    decision_json,
                    turn_count,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(interview_id) DO UPDATE SET
                    status = excluded.status,
                    messages_json = excluded.messages_json,
                    decision_json = excluded.decision_json,
                    turn_count = excluded.turn_count,
                    updated_at = excluded.updated_at
                """,
                (
                    interview_id,
                    status,
                    messages_json,
                    decision_json,
                    turn_count,
                    now,
                    now,
                ),
            )
        interview = self.get_presentation_interview(interview_id)
        if interview is None:
            raise RuntimeError(f"Presentation interview '{interview_id}' could not be loaded.")
        return interview

    def get_presentation_interview(self, interview_id: str) -> PresentationInterviewRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    interview_id,
                    status,
                    messages_json,
                    decision_json,
                    turn_count,
                    created_at,
                    updated_at
                FROM presentation_interviews
                WHERE interview_id = ?
                """,
                (interview_id,),
            ).fetchone()
        if row is None:
            return None
        return PresentationInterviewRecord(
            interview_id=row["interview_id"],
            status=row["status"],
            messages_json=row["messages_json"],
            decision_json=row["decision_json"],
            turn_count=row["turn_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def create_deck_plan(self, *, request_json: str) -> DeckPlanRecord:
        plan_id = uuid.uuid4().hex
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO deck_plans (
                    plan_id, status, request_json, plan_json, created_at, updated_at
                )
                VALUES (?, 'planning', ?, '', ?, ?)
                """,
                (plan_id, request_json, now, now),
            )
        plan = self.get_deck_plan(plan_id)
        if plan is None:
            raise RuntimeError(f"Deck plan '{plan_id}' could not be loaded after insert.")
        return plan

    def get_deck_plan(self, plan_id: str) -> DeckPlanRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT plan_id, status, request_json, plan_json, error_message,
                       job_id, created_at, updated_at
                FROM deck_plans
                WHERE plan_id = ?
                """,
                (plan_id,),
            ).fetchone()
        if row is None:
            return None
        return DeckPlanRecord(
            plan_id=row["plan_id"],
            status=row["status"],
            request_json=row["request_json"],
            plan_json=row["plan_json"],
            error_message=row["error_message"],
            job_id=row["job_id"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def update_deck_plan(
        self,
        plan_id: str,
        *,
        status: DeckPlanStatus | None = None,
        plan_json: str | None = None,
        error_message: str | None = None,
        job_id: str | None = None,
    ) -> DeckPlanRecord:
        assignments = ["updated_at = ?"]
        values: list[object] = [self._now()]
        if status is not None:
            assignments.append("status = ?")
            values.append(status)
        if plan_json is not None:
            assignments.append("plan_json = ?")
            values.append(plan_json)
        if error_message is not None:
            assignments.append("error_message = ?")
            values.append(error_message)
        if job_id is not None:
            assignments.append("job_id = ?")
            values.append(job_id)
        values.append(plan_id)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE deck_plans SET {', '.join(assignments)} WHERE plan_id = ?",
                values,
            )
        plan = self.get_deck_plan(plan_id)
        if plan is None:
            raise RuntimeError(f"Deck plan '{plan_id}' does not exist.")
        return plan

    def create_deck_revision(self, *, job_id: str, message: str) -> DeckRevisionRecord:
        revision_id = uuid.uuid4().hex
        now = self._now()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO deck_revisions (
                    revision_id, job_id, status, message, created_at, updated_at
                )
                VALUES (?, ?, 'running', ?, ?, ?)
                """,
                (revision_id, job_id, message, now, now),
            )
        revision = self.get_deck_revision(revision_id)
        if revision is None:
            raise RuntimeError(f"Deck revision '{revision_id}' could not be loaded.")
        return revision

    def get_deck_revision(self, revision_id: str) -> DeckRevisionRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT revision_id, job_id, status, message, reply,
                       revised_pages_json, error_message, created_at, updated_at
                FROM deck_revisions
                WHERE revision_id = ?
                """,
                (revision_id,),
            ).fetchone()
        return self._deck_revision_from_row(row) if row is not None else None

    def list_deck_revisions(self, job_id: str) -> list[DeckRevisionRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT revision_id, job_id, status, message, reply,
                       revised_pages_json, error_message, created_at, updated_at
                FROM deck_revisions
                WHERE job_id = ?
                ORDER BY created_at ASC, revision_id ASC
                """,
                (job_id,),
            ).fetchall()
        return [self._deck_revision_from_row(row) for row in rows]

    def has_running_deck_revision(self, job_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM deck_revisions WHERE job_id = ? AND status = 'running' LIMIT 1",
                (job_id,),
            ).fetchone()
        return row is not None

    def update_deck_revision(
        self,
        revision_id: str,
        *,
        status: DeckRevisionStatus | None = None,
        reply: str | None = None,
        revised_pages_json: str | None = None,
        error_message: str | None = None,
    ) -> DeckRevisionRecord:
        assignments = ["updated_at = ?"]
        values: list[object] = [self._now()]
        if status is not None:
            assignments.append("status = ?")
            values.append(status)
        if reply is not None:
            assignments.append("reply = ?")
            values.append(reply)
        if revised_pages_json is not None:
            assignments.append("revised_pages_json = ?")
            values.append(revised_pages_json)
        if error_message is not None:
            assignments.append("error_message = ?")
            values.append(error_message)
        values.append(revision_id)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE deck_revisions SET {', '.join(assignments)} WHERE revision_id = ?",
                values,
            )
        revision = self.get_deck_revision(revision_id)
        if revision is None:
            raise RuntimeError(f"Deck revision '{revision_id}' does not exist.")
        return revision

    def _deck_revision_from_row(self, row: sqlite3.Row) -> DeckRevisionRecord:
        return DeckRevisionRecord(
            revision_id=row["revision_id"],
            job_id=row["job_id"],
            status=row["status"],
            message=row["message"],
            reply=row["reply"],
            revised_pages_json=row["revised_pages_json"],
            error_message=row["error_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
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
        self.update_long_deck_progress(job_id, current_stage=current_stage)

    def update_long_deck_progress(
        self,
        job_id: str,
        *,
        current_stage: str | None = None,
        total_batches: int | None = None,
        completed_batches: int | None = None,
        failed_batches: int | None = None,
        current_batch: str | None = None,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET current_stage = COALESCE(?, current_stage),
                    total_batches = COALESCE(?, total_batches),
                    completed_batches = COALESCE(?, completed_batches),
                    failed_batches = COALESCE(?, failed_batches),
                    current_batch = COALESCE(?, current_batch),
                    updated_at = ?
                WHERE job_id = ?
                """,
                (
                    current_stage,
                    total_batches,
                    completed_batches,
                    failed_batches,
                    current_batch,
                    self._now(),
                    job_id,
                ),
            )

    def request_cancel(self, job_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE jobs
                SET cancel_requested = ?,
                    current_stage = ?,
                    updated_at = ?
                WHERE job_id = ?
                """,
                (1, "cancel_requested", self._now(), job_id),
            )

    def is_cancel_requested(self, job_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT cancel_requested
                FROM jobs
                WHERE job_id = ?
                """,
                (job_id,),
            ).fetchone()
        return bool(row["cancel_requested"]) if row is not None else False

    def add_artifact(self, job_id: str, *, name: str, kind: ArtifactKind, path: str | Path) -> ArtifactRecord:
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

    def _presentation_history_from_row(self, row: sqlite3.Row) -> PresentationHistoryRecord:
        return PresentationHistoryRecord(
            job_id=row["job_id"],
            status=row["status"],
            job_type=row["job_type"],
            topic=row["topic"],
            audience=row["audience"],
            user_requirements=row["user_requirements"],
            slide_count=row["slide_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            accepted=None if row["accepted"] is None else bool(row["accepted"]),
            qa_score=row["qa_score"],
            pptx_artifact_id=row["pptx_artifact_id"],
            pptx_artifact_name=row["pptx_artifact_name"],
            pptx_path=None if row["pptx_path"] is None else Path(row["pptx_path"]),
        )

    def _elapsed_seconds(self, created_at: str, updated_at: str, status: str) -> int:
        try:
            created = datetime.fromisoformat(created_at)
            end = datetime.now(UTC) if status in {"pending", "running"} else datetime.fromisoformat(updated_at)
        except ValueError:
            return 0
        return max(0, int((end - created).total_seconds()))

import sqlite3
from pathlib import Path

from ppt_agent.job_store import JobStore


def test_presentation_request_persists_across_store_instances(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite3"
    store = JobStore(db_path)
    job = store.create_job(job_type="long_deck_v2")

    store.save_presentation_request(
        job.job_id,
        topic="未来智慧校园",
        audience="高校管理者",
        user_requirements="生成一份可编辑的中文技术产品演示。",
        slide_count=100,
    )

    request = JobStore(db_path).get_presentation_request(job.job_id)

    assert request is not None
    assert request.topic == "未来智慧校园"
    assert request.audience == "高校管理者"
    assert request.slide_count == 100


def test_presentation_history_selects_preferred_pptx_and_filters(tmp_path: Path) -> None:
    store = JobStore(tmp_path / "jobs.sqlite3")
    job = store.create_job(job_type="long_deck")
    store.save_presentation_request(
        job.job_id,
        topic="Agent 产品设计",
        audience="产品经理",
        user_requirements="技术产品分享",
        slide_count=30,
    )
    legacy_pptx = tmp_path / "generated_long_deck.pptx"
    legacy_pptx.write_bytes(b"legacy")
    master_pptx = tmp_path / "generated_by_ppt_master.pptx"
    master_pptx.write_bytes(b"master")
    store.add_artifact(job.job_id, name="generated_long_deck", kind="pptx", path=legacy_pptx)
    preferred = store.add_artifact(
        job.job_id,
        name="ppt_master_generated_pptx",
        kind="pptx",
        path=master_pptx,
    )
    store.update_job(job.job_id, status="succeeded", accepted=True, qa_score=91)

    records, total = store.list_presentation_history(query="产品经理", status="succeeded")

    assert total == 1
    assert records[0].topic == "Agent 产品设计"
    assert records[0].pptx_artifact_id == preferred.artifact_id
    assert records[0].pptx_path == master_pptx


def test_existing_database_is_migrated_without_losing_jobs(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE jobs (
                job_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO jobs (job_id, status, created_at, updated_at) VALUES (?, ?, ?, ?)",
            ("legacy-job", "succeeded", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:01+00:00"),
        )

    store = JobStore(db_path)
    store.save_presentation_request(
        "legacy-job",
        topic="旧演示",
        audience="",
        user_requirements="",
        slide_count=8,
    )

    assert store.get_job("legacy-job") is not None
    assert store.get_presentation_request("legacy-job") is not None


def test_presentation_interview_is_persisted_in_sqlite(tmp_path: Path) -> None:
    db_path = tmp_path / "jobs.sqlite3"
    store = JobStore(db_path)

    store.save_presentation_interview(
        interview_id="interview-1",
        status="clarifying",
        messages_json='[{"role":"user","content":"做一份环保 PPT"}]',
        decision_json='{"status":"clarifying"}',
        turn_count=1,
    )

    interview = JobStore(db_path).get_presentation_interview("interview-1")

    assert interview is not None
    assert interview.status == "clarifying"
    assert interview.turn_count == 1
    assert "环保 PPT" in interview.messages_json

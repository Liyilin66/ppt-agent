"""Framework-free entry points preserve the existing web job contract."""
import subprocess
import sys

from ppt_agent import deck_jobs
from ppt_agent.job_store import JobStore


def test_deck_jobs_import_does_not_load_web_framework():
    subprocess.run(
        [sys.executable, '-c',
         "import sys; import ppt_agent.deck_jobs; "
         "assert 'ppt_agent.api' not in sys.modules; assert 'fastapi' not in sys.modules"],
        check=True,
    )


def test_v2_entry_schedules_job_with_resolved_local_paths(tmp_path):
    store = JobStore(tmp_path / 'jobs.sqlite3')
    tasks = []
    payload = deck_jobs.CreateLongDeckJobRequest(
        topic='报告', audience='管理层', slide_count=20, user_requirements='覆盖各章',
    )
    client = object()
    job = deck_jobs.start_v2_job(
        store, tmp_path / 'jobs', client, payload,
        submit=lambda *args, **kwargs: tasks.append((args, kwargs)),
        document_paths=['/reports/report.pdf'], image_paths=['/images/chart.png'],
    )
    assert job.status == 'pending'
    assert store.get_job(job.job_id).current_stage == 'v2_intake'
    assert len(tasks) == 1
    args, kwargs = tasks[0]
    assert args == (deck_jobs.run_v2_long_deck_job, store, tmp_path / 'jobs', job.job_id, client, payload)
    assert kwargs['document_paths'] == ['/reports/report.pdf']
    assert kwargs['image_paths'] == ['/images/chart.png']


def test_revision_entry_schedules_revision_with_resolved_local_paths(tmp_path):
    store = JobStore(tmp_path / 'jobs.sqlite3')
    job = store.create_job(job_type='long_deck_v2')
    tasks = []
    client = object()
    revision = deck_jobs.start_deck_revision(
        store, tmp_path / 'jobs', job.job_id, client, '修改第3页', [3],
        submit=lambda *args, **kwargs: tasks.append((args, kwargs)),
        document_paths=['/reports/report.pdf'], image_paths=['/images/chart.png'],
    )
    assert revision.status == 'running'
    assert store.get_deck_revision(revision.revision_id).job_id == job.job_id
    args, kwargs = tasks[0]
    assert args == (deck_jobs.run_deck_revision, store, tmp_path / 'jobs', job.job_id,
                    revision.revision_id, client, '修改第3页', [3])
    assert kwargs['document_paths'] == ['/reports/report.pdf']
    assert kwargs['image_paths'] == ['/images/chart.png']

"""Local stdio MCP tools over the shared deck_jobs execution path."""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import json
import logging
import os
import shutil
from pathlib import Path
import sys
from uuid import uuid4
from threading import Condition, Thread
from time import monotonic
from typing import Annotated, Literal

from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from ppt_agent import __version__, deck_jobs
from ppt_agent.runtime import load_dotenv_file, sanitize_error_message

logger = logging.getLogger(__name__)
DOCUMENT_SUFFIXES = {'.pdf', '.docx', '.md', '.txt'}
IMAGE_SUFFIXES = {'.png', '.jpg', '.webp'}
MAX_SOURCES = 20  # Same limit as CreateLongDeckJobRequest.attachment_ids.


@dataclass
class MCPContext:
    data_dir: Path
    store: deck_jobs.JobStore
    condition: Condition = field(default_factory=Condition, repr=False)
    threads: set[Thread] = field(default_factory=set, repr=False)
    active_generation: str | None = None
    active_revisions: dict[str, str] = field(default_factory=dict)
    closed: bool = False

    def _submit(self, kind, function, *args, **kwargs):
        """Submit synchronous executors without nesting their event loops."""
        job_id = args[2]
        revision_id = args[3] if kind == 'revision' else None

        def execute():
            try:
                function(*args, **kwargs)
            except Exception as exc:
                # Also capture failures before the executor's own try/except.
                error = sanitize_error_message(exc)
                logger.error('MCP background execution failed: %s', error)
                if revision_id:
                    self.store.update_deck_revision(revision_id, status='failed', error_message=error)
                else:
                    self.store.update_job(job_id, status='failed', current_stage='v2_failed',
                                          accepted=False, error_message=error)
            finally:
                with self.condition:
                    if revision_id:
                        self.active_revisions.pop(job_id, None)
                    elif self.active_generation == job_id:
                        self.active_generation = None
                    self.condition.notify_all()

        with self.condition:
            if self.closed:
                raise RuntimeError('MCP 服务已关闭，不能提交任务。')
            self.threads = {thread for thread in self.threads if thread.is_alive()}
            thread = Thread(target=execute, name=f'ppt-agent-{kind}-{job_id}', daemon=True)
            self.threads.add(thread)
            if revision_id:
                self.active_revisions[job_id] = revision_id
            else:
                self.active_generation = job_id
            try:
                thread.start()
            except Exception as exc:
                self.threads.discard(thread)
                if revision_id:
                    self.active_revisions.pop(job_id, None)
                    self.store.update_deck_revision(revision_id, status='failed',
                                                    error_message=sanitize_error_message(exc))
                else:
                    self.active_generation = None
                    self.store.update_job(job_id, status='failed', current_stage='v2_failed',
                                          error_message=sanitize_error_message(exc), accepted=False)
                raise

    def submit_generation(self, function, *args, **kwargs):
        self._submit('generation', function, *args, **kwargs)

    def submit_revision(self, function, *args, **kwargs):
        self._submit('revision', function, *args, **kwargs)

    def wait_for_idle(self, timeout: float = 10) -> None:
        """Join submitted threads deterministically (also used by offline tests)."""
        deadline = monotonic() + timeout
        while True:
            with self.condition:
                threads = list(self.threads)
            for thread in threads:
                thread.join(max(0, deadline - monotonic()))
                if thread.is_alive():
                    raise TimeoutError('等待后台任务结束超时。')
            with self.condition:
                self.threads.difference_update(threads)
                if not self.threads:
                    return


def _source_paths(paths: list[str]) -> tuple[list[str], list[str]]:
    if len(paths) > MAX_SOURCES:
        raise ValueError(f'最多支持 {MAX_SOURCES} 个附件。')
    documents, images = [], []
    for value in paths:
        path = Path(value)
        if not path.is_absolute():
            raise ValueError(f'文件路径必须是本地绝对路径：{value}')
        if not path.is_file():
            raise ValueError(f'文件不存在或不是普通文件：{value}')
        suffix = path.suffix.lower()
        if suffix not in DOCUMENT_SUFFIXES | IMAGE_SUFFIXES:
            raise ValueError(f'不支持的文件格式：{path.name}')
        (documents if suffix in DOCUMENT_SUFFIXES else images).append(str(path.resolve()))
    return documents, images


def _stage_sources(data_dir: Path, documents: list[str], images: list[str]):
    """Use the web upload layout so its resume path retains local MCP inputs."""
    ids, document_copies, image_copies, directories = [], [], [], []
    try:
        for sources, copies in ((documents, document_copies), (images, image_copies)):
            for source in sources:
                upload_id = uuid4().hex
                directory = data_dir / 'uploads' / upload_id
                directory.mkdir(parents=True)
                directories.append(directory)
                target = directory / Path(source).name
                shutil.copy2(source, target)
                ids.append(upload_id)
                copies.append(str(target))
    except Exception:
        for directory in directories:
            shutil.rmtree(directory)
        raise
    return ids, document_copies, image_copies


def _stage(job, revision) -> str:
    if revision is not None and revision.status == 'running':
        return '正在修改演示文稿，请等待修订完成'
    labels = {
        'v2_intake': '正在读取资料', 'v2_brief': '正在提炼需求与资料',
        'v2_theme': '正在选择视觉风格', 'v2_outline': '正在规划目录和章节',
        'v2_page_briefs': '正在规划各页内容', 'v2_content_generation': '正在生成页面内容',
        'v2_page_designs': '正在设计页面', 'v2_typesetting': '正在排版页面',
        'v2_quality_gate': '正在核对内容与检查质量', 'v2_rendering_complete': '正在完成导出',
        'v2_completed': '生成完成', 'v2_quality_gate_failed': '质量检查未通过',
        'v2_failed': '生成失败', 'v2_cancelled': '任务已取消',
    }
    return labels.get(job.current_stage, {
        'pending': '等待执行', 'running': '正在处理', 'succeeded': '生成完成',
        'failed': '任务失败', 'failed_quality_gate': '质量检查未通过',
        'cancelled': '任务已取消',
    }.get(job.status, '正在处理'))


def _statistics(output_dir: Path) -> tuple[dict | None, str | None]:
    """Read existing report values; never run QA or persist new metrics."""
    path = output_dir / 'generated_long_deck_v2_run_report.json'
    if not path.is_file():
        return None, None
    try:
        report = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(report, dict):
            raise ValueError('运行报告不是对象')
        content = report.get('content_statistics') or {}
        usage = report.get('usage') or {}
        # This is the same fallback count already used by the generation result,
        # exposed from its persisted per-page outcomes rather than a new check.
        outcomes = report.get('outcomes')
        fallback_count = (sum(item.get('status') == 'fallback' for item in outcomes)
                          if isinstance(outcomes, list) else None)
        return {
            'fallback_pages': fallback_count,
            'source_empty_pages': content.get('source_empty_pages'),
            'source_invalid_pages': content.get('source_invalid_pages'),
            'source_invalid_attempts': content.get('source_invalid_attempts'),
            'estimated_cost_usd': usage.get('estimated_cost_usd'),
        }, None
    except (OSError, ValueError, TypeError, AttributeError):
        return None, '运行报告尚不可读，可能正在写入或更新，请稍后重试。'


def create_server() -> FastMCP:
    """Use the web app's environment, data directory and SQLite job store."""
    load_dotenv_file()
    data_dir = Path(os.getenv('PPT_AGENT_DATA_DIR', 'data')).resolve()

    @asynccontextmanager
    async def lifespan(server: FastMCP):
        store = deck_jobs.JobStore(data_dir / 'jobs.sqlite3')
        store.get_latest_job()
        context = MCPContext(data_dir=data_dir, store=store)
        logger.info('ppt-agent MCP ready: %s', data_dir)
        try:
            yield context
        finally:
            # Workers belong to this process. Daemon threads must not keep a
            # disconnected stdio process alive for minutes. Web resume uses the
            # existing checkpoints after an interrupted process exits.
            with context.condition:
                context.closed = True

    server = FastMCP('ppt-agent', lifespan=lifespan)

    @server.tool()
    async def create_deck(
        topic: Annotated[str, Field(min_length=1)],
        requirements: Annotated[str, Field(min_length=1)],
        ctx: Context,
        pages: Annotated[int, Field(ge=4, le=100)] = 20,
        audience: str = '未指定',
        source_paths: list[str] | None = None,
        search: bool = False,
        style: Literal['consulting', 'launch', 'training', 'corporate'] | None = None,
    ) -> dict:
        """Start an editable PPT task and return its ID without waiting for generation.

        source_paths accepts up to 20 absolute local document/image paths.
        Only one generation may run at a time. Poll get_deck_status for the PPTX.
        """
        context: MCPContext = ctx.request_context.lifespan_context
        try:
            documents, images = _source_paths(source_paths or [])
            if search and not os.environ.get('TAVILY_API_KEY', '').strip():
                raise ValueError('联网搜索未配置：请设置 TAVILY_API_KEY。')
            payload = deck_jobs.CreateLongDeckJobRequest(
                topic=topic, audience=audience, user_requirements=requirements,
                slide_count=pages, deck_type='visual_design_v2', layout_engine='typeset',
                style_profile=style, enable_search=search,
            )
            with context.condition:
                if context.closed:
                    return {'error': 'MCP 服务已关闭。'}
                if context.active_generation:
                    return {'error': '已有生成任务正在执行，请先查询其状态。',
                            'job_id': context.active_generation}
                client = deck_jobs.create_v2_model_client()
                attachment_ids, documents, images = _stage_sources(context.data_dir, documents, images)
                payload = payload.model_copy(update={'attachment_ids': attachment_ids})
                job = deck_jobs.start_v2_job(
                    context.store, context.data_dir / 'jobs', client, payload,
                    submit=context.submit_generation, document_paths=documents, image_paths=images,
                )
            return {'job_id': job.job_id, 'status': job.status,
                    'message': '生成通常需要几分钟，请用 get_deck_status 查询进度和 PPTX 路径。'}
        except Exception as exc:
            return {'error': '无法创建任务：' + sanitize_error_message(exc)}

    @server.tool()
    async def get_deck_status(job_id: str, ctx: Context) -> dict:
        """Read generation progress, existing report statistics and latest revision."""
        context: MCPContext = ctx.request_context.lifespan_context
        job = context.store.get_job(job_id)
        if job is None:
            return {'error': '任务不存在：' + job_id}
        output_dir = context.data_dir / 'jobs' / job_id
        revisions = context.store.list_deck_revisions(job_id)
        revision = revisions[-1] if revisions else None
        latest = None
        if revision is not None:
            try:
                revised_pages = [int(n) for n in json.loads(revision.revised_pages_json)]
            except (ValueError, TypeError):
                revised_pages = []
            latest = {'revision_id': revision.revision_id, 'status': revision.status,
                      'reply': revision.reply, 'revised_pages': revised_pages,
                      'error_message': revision.error_message}
        pptx = None
        if job.status in {'succeeded', 'failed_quality_gate'} and not (
            revision is not None and revision.status == 'running'
        ):
            pptx = next((str(artifact.path.resolve()) for artifact in context.store.list_artifacts(job_id)
                         if artifact.kind == 'pptx' and artifact.path.is_file()), None)
        statistics, statistics_error = _statistics(output_dir)
        return {
            'job_id': job_id, 'status': job.status, 'stage': _stage(job, revision),
            'completed_pages': job.completed_batches or 0, 'total_pages': job.total_batches,
            'error_message': job.error_message, 'pptx_path': pptx,
            'statistics': statistics, 'statistics_error': statistics_error,
            'latest_revision': latest, 'version': __version__, 'data_dir': str(context.data_dir),
        }

    @server.tool()
    async def revise_deck(
        job_id: str, instruction: Annotated[str, Field(min_length=1, max_length=4000)],
        ctx: Context, page_numbers: list[int] | None = None,
    ) -> dict:
        """Start a revision; poll get_deck_status for its reply, pages and updated PPTX."""
        context: MCPContext = ctx.request_context.lifespan_context
        try:
            with context.condition:
                if context.closed:
                    return {'error': 'MCP 服务已关闭。'}
                job = context.store.get_job(job_id)
                if job is None:
                    return {'error': '任务不存在：' + job_id}
                if job.job_type not in {'long_deck_v2', 'image_rebuild'}:
                    return {'error': '只有 v2 演示文稿支持修订。'}
                if job.status not in {'succeeded', 'failed_quality_gate'}:
                    return {'error': '任务尚未完成或不可修订，请等待生成结束。', 'job_id': job_id}
                if not (context.data_dir / 'jobs' / job_id / 'checkpoints').is_dir():
                    return {'error': '任务没有 checkpoints 检查点，无法修订。'}
                if job_id in context.active_revisions or context.store.has_running_deck_revision(job_id):
                    return {'error': '这个任务已有修订正在执行，请先查询修订结果。', 'job_id': job_id}
                client = deck_jobs.create_v2_model_client()
                record = deck_jobs.start_deck_revision(
                    context.store, context.data_dir / 'jobs', job_id, client, instruction, page_numbers,
                    submit=context.submit_revision,
                )
            return {'revision_id': record.revision_id, 'job_id': job_id, 'status': record.status,
                    'message': '修订已开始，请用 get_deck_status 查看结果。'}
        except Exception as exc:
            return {'error': '无法开始修订：' + sanitize_error_message(exc)}

    return server


def run_server() -> None:
    # The SDK owns stdout. Existing deck executors explicitly route progress to
    # logging, and only this command reconfigures logging for stdio safety.
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, force=True)
    create_server().run(transport='stdio')

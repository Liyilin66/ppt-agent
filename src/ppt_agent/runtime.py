"""Small runtime helpers for observable build jobs and guarded model calls."""

from __future__ import annotations

import re
import queue
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Literal, TypeVar


StageEvent = Literal["start", "finish", "error"]
StageObserver = Callable[[str, StageEvent, dict[str, Any]], None]

T = TypeVar("T")


class JobTimeoutError(TimeoutError):
    """Raised when a build job exceeds the configured total runtime budget."""


class LLMCallTimeoutError(TimeoutError):
    """Raised when a single structured model invocation exceeds its time budget."""


_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(OPENAI_API_KEY\s*=\s*)\S+", re.IGNORECASE),
    re.compile(r"(api[_-]?key\s*[:=]\s*)\S+", re.IGNORECASE),
)


def utc_now_iso() -> str:
    """Return a compact UTC ISO timestamp for job metadata and logs."""

    return datetime.now(UTC).isoformat()


def sanitize_error_message(message: object) -> str:
    """Remove likely API credentials from error text before persisting or logging."""

    text = str(message)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1)}[redacted]" if match.groups() else "[redacted]", text)
    return text


def emit_stage_event(
    observer: StageObserver | None,
    stage_name: str,
    event: StageEvent,
    **metadata: Any,
) -> None:
    """Notify a stage observer when one is configured."""

    if observer is None:
        return
    observer(stage_name, event, metadata)


@contextmanager
def observed_stage(
    observer: StageObserver | None,
    stage_name: str,
    **metadata: Any,
) -> Generator[None, None, None]:
    """Emit start/finish/error events with stable timing metadata."""

    started_at = utc_now_iso()
    started_perf = perf_counter()
    emit_stage_event(observer, stage_name, "start", started_at=started_at, **metadata)

    try:
        yield
    except Exception as exc:
        finished_at = utc_now_iso()
        duration_ms = int((perf_counter() - started_perf) * 1000)
        emit_stage_event(
            observer,
            stage_name,
            "error",
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            error_message=sanitize_error_message(exc),
            **metadata,
        )
        raise

    finished_at = utc_now_iso()
    duration_ms = int((perf_counter() - started_perf) * 1000)
    emit_stage_event(
        observer,
        stage_name,
        "finish",
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=duration_ms,
        **metadata,
    )


def invoke_with_timeout(
    call: Callable[[], T],
    *,
    timeout_seconds: float | None,
    stage_name: str,
    timeout_detail: str | None = None,
) -> T:
    """Run a blocking model call with an optional per-call timeout guard."""

    if timeout_seconds is None:
        return call()

    result_queue: queue.Queue[tuple[Literal["ok", "error"], T | BaseException]] = queue.Queue(maxsize=1)

    def run_call() -> None:
        try:
            result_queue.put(("ok", call()))
        except BaseException as exc:
            result_queue.put(("error", exc))

    thread = threading.Thread(target=run_call, daemon=True)
    thread.start()
    try:
        status, payload = result_queue.get(timeout=timeout_seconds)
    except queue.Empty as exc:
        detail = f" {timeout_detail}" if timeout_detail else ""
        raise LLMCallTimeoutError(
            f"LLM call timed out in stage '{stage_name}'{detail} after {timeout_seconds:g} seconds."
        ) from exc

    if status == "error":
        raise payload
    return payload

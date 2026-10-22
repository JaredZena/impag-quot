"""
Background work for AI steps that can outlive an HTTP request.

App Runner closes every request at 120 s (reading the body through writing
the response, streaming included), and a Claude call with web search or a
long structured answer can take longer. So the Agroproyectos steps and the
Apoyos discovery topics run on this small thread pool: the endpoint marks the
row as "generating", submits the work and answers at once; the caller polls
the row. The service runs one uvicorn process per instance, so a submitted
job lives as long as that process; a job lost to a deploy or a crash is
detected by its age (each caller treats a "generating" mark older than its
own stale limit as failed and lets the step be retried).

run() gives the job its own database session and never lets an exception
escape the worker thread.
"""

import logging
import os
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

from sqlalchemy.orm import Session

from models import SessionLocal

logger = logging.getLogger(__name__)

_executor: ThreadPoolExecutor | None = None


def _pool() -> ThreadPoolExecutor:
    global _executor
    if _executor is None:
        try:
            workers = int(os.getenv("AI_BACKGROUND_WORKERS", "4"))
        except ValueError:
            workers = 4
        _executor = ThreadPoolExecutor(
            max_workers=max(1, workers), thread_name_prefix="ai-bg"
        )
    return _executor


def submit(name: str, job: Callable[[Session], None]) -> Future:
    """Run job(db) on the pool with a fresh session; returns the future
    (tests may wait on it). Exceptions are logged, never raised."""

    def runner() -> None:
        db = SessionLocal()
        try:
            job(db)
        except Exception:
            db.rollback()
            logger.exception("background job %s failed", name)
        finally:
            db.close()

    return _pool().submit(runner)


def set_sync_for_tests(enabled: bool) -> None:
    """Tests: run submitted jobs inline so assertions see the result."""
    global _executor
    if enabled:
        _executor = _InlineExecutor()  # type: ignore[assignment]
    else:
        _executor = None


class _InlineExecutor:
    def submit(self, fn: Callable[[], None]) -> Future:
        future: Future = Future()
        try:
            fn()
            future.set_result(None)
        except Exception as exc:  # noqa: BLE001
            future.set_exception(exc)
        return future

"""Standalone background worker: `python -m app.worker`.

Run this as its own process (its own systemd unit in production), separate
from the API, so media or recording trouble never slows the portal. Running
several copies is safe: jobs are claimed with a compare-and-swap lease, so no
job is ever executed by two workers at once.

Each loop it (1) makes sure a periodic schedule reconciliation is queued, then
(2) drains every claimable job. Nothing depends on a browser being open.
"""
import asyncio
import logging
import os
import signal
import socket
import uuid
from datetime import timedelta

from sqlalchemy import delete

from app.config import settings
from app.core import job_handlers  # noqa: F401  (registers every handler)
from app.core import job_queue
from app.core.class_runs import utcnow
from app.database import SessionLocal
from app.models.class_run import JOB_SUCCEEDED, BackgroundJob

logger = logging.getLogger("worker")
RECONCILE_EVERY_SECONDS = 15
RECONCILE_KEEP = timedelta(hours=1)


def make_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


def enqueue_periodic(session_factory) -> None:
    now = utcnow()
    bucket = int(now.timestamp() // RECONCILE_EVERY_SECONDS)
    with session_factory() as db:
        job_queue.enqueue(db, "sessions.reconcile", f"sessions.reconcile:{bucket}", {}, run_at=now, max_attempts=3)
        # One reconcile job is queued every few seconds; keep only a short
        # history of the successful ones. No other job kind is ever pruned.
        db.execute(delete(BackgroundJob).where(
            BackgroundJob.kind == "sessions.reconcile", BackgroundJob.state == JOB_SUCCEEDED,
            BackgroundJob.completed_at < now - RECONCILE_KEEP))
        db.commit()


def drain(session_factory, worker_id: str, limit: int = 100) -> int:
    done = 0
    while done < limit and job_queue.run_one(session_factory, worker_id):
        done += 1
    return done


async def run_worker(session_factory=SessionLocal, worker_id: str | None = None,
                     stop: asyncio.Event | None = None) -> None:
    worker_id = worker_id or make_worker_id()
    stop = stop or asyncio.Event()
    logger.info("worker started id=%s", worker_id)
    while not stop.is_set():
        try:
            await asyncio.to_thread(enqueue_periodic, session_factory)
            await asyncio.to_thread(drain, session_factory, worker_id)
        except Exception:  # noqa: BLE001 - the loop itself must survive anything
            logger.exception("worker iteration failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=settings.job_poll_seconds)
        except asyncio.TimeoutError:
            pass
    logger.info("worker stopped id=%s", worker_id)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from app.core.log_redaction import install as install_log_redaction
    install_log_redaction()  # never log Graph's pre-authenticated URLs (httpx logs URLs at INFO)
    stop = asyncio.Event()

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, stop.set)
            except NotImplementedError:  # Windows
                pass
        await run_worker(stop=stop)

    asyncio.run(_run())


if __name__ == "__main__":
    main()

"""Feedback -> Sheets retry safety against an explicitly marked disposable PostgreSQL DB.

Covers what SQLite cannot: real row locks (FOR UPDATE SKIP LOCKED) across concurrent
workers, and a process killed in the middle of a Google Sheets request.
"""

import subprocess
import sys
import textwrap
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier, Lock
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core import feedback_sheets as fs
from app.database import engine
from app.models.inauguration_feedback import InaugurationFeedback
from tests.postgres_safety import isolated_postgres_enabled

TAG = "sheets-qa-"


def make_rows(count, next_attempt_at=None):
    with Session(bind=engine) as db:
        rows = [
            InaugurationFeedback(
                name=f"{TAG}{uuid4().hex[:8]}", domain="IT", overall_session="Good", usefulness="Useful",
                learned_something="Yes", liked_topic="AI", attend_future="Yes", contact_preference="no",
                sheets_next_attempt_at=next_attempt_at,
            )
            for _ in range(count)
        ]
        db.add_all(rows)
        db.commit()
        return [r.id for r in rows]


def load(row_ids):
    with Session(bind=engine) as db:
        return {r.id: r for r in db.scalars(select(InaugurationFeedback).where(InaugurationFeedback.id.in_(row_ids)))}


class SlowSender:
    """Counts sends per submission and stays 'in flight' long enough for workers to overlap."""

    def __init__(self, outcome="appended", delay=0.3):
        self.outcome, self.delay = outcome, delay
        self.sent, self.lock = [], Lock()

    def __call__(self, payload):
        with self.lock:
            self.sent.append(payload["submission_id"])
        time.sleep(self.delay)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@unittest.skipUnless(isolated_postgres_enabled(), "Requires marked disposable PostgreSQL")
class FeedbackSheetsPostgresTests(unittest.TestCase):
    def setUp(self):
        self.ids = []

    def tearDown(self):
        with Session(bind=engine) as db:
            db.execute(delete(InaugurationFeedback).where(InaugurationFeedback.name.like(f"{TAG}%")))
            db.commit()

    def run_workers(self, count, sender, now=None):
        barrier = Barrier(count)

        def worker():
            barrier.wait()
            with Session(bind=engine) as db:
                return fs.run_due_syncs(db, sender=sender, now=now)

        with ThreadPoolExecutor(max_workers=count) as pool:
            return [f.result() for f in [pool.submit(worker) for _ in range(count)]]

    def test_concurrent_workers_send_each_pending_row_exactly_once(self):
        self.ids = make_rows(6)
        sender = SlowSender()
        attempted = self.run_workers(4, sender)
        self.assertEqual(sorted(sender.sent), sorted({s for s in sender.sent}), "a row was sent twice")
        self.assertEqual(len(sender.sent), 6)
        self.assertEqual(sum(attempted), 6)
        rows = load(self.ids)
        self.assertTrue(all(r.sheets_sync_status == fs.SYNCED and r.sheets_sync_attempts == 1 for r in rows.values()))

    def test_second_worker_does_not_retry_a_row_the_first_just_rescheduled(self):
        """Both workers list the row as due; the loser must re-check the schedule once it gets the lock."""
        self.ids = make_rows(1)
        sender = SlowSender(fs.SheetsSyncError("http_503"), delay=0.4)
        self.run_workers(2, sender)
        self.assertEqual(len(sender.sent), 1, "row was retried before its backoff elapsed")
        row = load(self.ids)[self.ids[0]]
        self.assertEqual((row.sheets_sync_status, row.sheets_sync_attempts), (fs.PENDING, 1))

    def test_first_sync_and_worker_do_not_both_send(self):
        """The immediate background sync and the retry worker racing on one row."""
        self.ids = make_rows(1, next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        sender = SlowSender(delay=0.5)
        barrier = Barrier(2)

        def first_sync():
            barrier.wait()
            with Session(bind=engine) as db:
                fs.sync_row_if_unclaimed(db, self.ids[0], sender=sender)

        def worker():
            barrier.wait()
            with Session(bind=engine) as db:
                return fs.run_due_syncs(db, sender=sender)

        with ThreadPoolExecutor(max_workers=2) as pool:
            for f in [pool.submit(first_sync), pool.submit(worker)]:
                f.result()
        self.assertEqual(len(sender.sent), 1)
        self.assertEqual(load(self.ids)[self.ids[0]].sheets_sync_status, fs.SYNCED)

    def test_process_killed_mid_request_loses_nothing_and_retry_deduplicates(self):
        self.ids = make_rows(1, next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        child = textwrap.dedent(f"""
            import sys, time
            from sqlalchemy.orm import Session
            from app.core import feedback_sheets as fs
            from app.database import engine
            def hang(payload):
                print("SENDING", flush=True)   # request is now 'in flight' to Google
                time.sleep(60)
            with Session(bind=engine) as db:
                fs.run_due_syncs(db, sender=hang)
        """)
        proc = subprocess.Popen([sys.executable, "-c", child], stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(proc.stdout.readline().strip(), "SENDING")
            proc.kill()  # simulates a restart / OOM kill during the Google request
            proc.wait(timeout=10)
        finally:
            if proc.poll() is None:
                proc.kill()
        row = load(self.ids)[self.ids[0]]
        # The attempt was never committed: still PENDING, not SYNCED, not counted, not stuck.
        self.assertEqual((row.sheets_sync_status, row.sheets_sync_attempts), (fs.PENDING, 0))
        # The restarted worker retries it. The sheet already has the row, so Apps Script says duplicate.
        # PostgreSQL frees the dead client's row lock as soon as it notices the closed socket; wait for that.
        sender = SlowSender("duplicate", delay=0)
        started, attempted = time.monotonic(), 0
        while attempted == 0 and time.monotonic() - started < 15:
            with Session(bind=engine) as db:
                attempted = fs.run_due_syncs(db, sender=sender)
            if attempted == 0:
                time.sleep(0.25)
        print(f"lock released after {time.monotonic() - started:.2f}s")
        self.assertEqual(attempted, 1)
        row = load(self.ids)[self.ids[0]]
        self.assertEqual((row.sheets_sync_status, row.sheets_sync_attempts), (fs.SYNCED, 1))

    def test_database_unique_submission_id(self):
        self.ids = make_rows(1)
        existing = load(self.ids)[self.ids[0]].submission_id
        with Session(bind=engine) as db:
            db.add(InaugurationFeedback(
                name=f"{TAG}dup", domain="IT", overall_session="Good", usefulness="Useful", learned_something="Yes",
                liked_topic="AI", attend_future="Yes", contact_preference="no", submission_id=existing))
            with self.assertRaises(Exception) as ctx:
                db.commit()
            self.assertIn("unique", str(ctx.exception).lower())


if __name__ == "__main__":
    unittest.main()

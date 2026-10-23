"""Trainer scheduling against a marked disposable PostgreSQL: HTTP routes, RBAC,
conflict rules, idempotency, notifications and concurrent allocation."""
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from threading import Barrier
from unittest.mock import patch

from fastapi import Depends, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core import scheduling as sch
from app.core import scheduling_service as svc
from app.core.deps import get_current_user
from app.database import engine, get_db
from app.main import app
from app.models.admin_role import AdminRole, SUPER_ADMIN_SLUG
from app.models.audit_log import AuthAuditLog, AuthEventType
from app.models.batch import Batch, BatchStatusEnum, BatchTypeEnum
from app.models.program import Program
from app.models.trainer_scheduling import (
    BatchTrainerAssignment, SchedulingNotice, TrainerAvailabilityEntry, TrainerAvailabilityRule, TrainerExpertise,
)
from app.models.user import RoleEnum, User
from tests.postgres_safety import isolated_postgres_enabled

BASE = "/api/admin/trainer-scheduling"
ME = "/api/staff/me/availability"


def _next_monday() -> date:
    d = datetime.now(timezone.utc).date() + timedelta(days=14)
    return d + timedelta(days=(7 - d.weekday()) % 7)


@unittest.skipUnless(isolated_postgres_enabled(), "Requires marked disposable PostgreSQL")
class TrainerSchedulingIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tag = uuid.uuid4().hex[:10]
        self.monday = _next_monday()
        self.user_ids, self.batch_ids = [], []
        with Session(bind=engine) as db:
            sup = db.scalar(select(AdminRole).where(AdminRole.slug == SUPER_ADMIN_SLUG))
            plain = db.scalar(select(AdminRole).where(AdminRole.slug == "admin"))
            support = db.scalar(select(AdminRole).where(AdminRole.slug == "support_admin"))
            self.program_id = db.scalar(select(Program.id).order_by(Program.id))
            self.super_admin = self._user(db, "super", RoleEnum.admin, sup.id)
            self.admin = self._user(db, "admin", RoleEnum.admin, plain.id)
            self.viewer = self._user(db, "viewer", RoleEnum.admin, support.id)
            self.ta = self._user(db, "ta", RoleEnum.staff)
            self.tb = self._user(db, "tb", RoleEnum.staff)
            self.student = self._user(db, "stu", RoleEnum.student)
            db.commit()
            self.ids = {n: getattr(self, n) for n in ("super_admin", "admin", "viewer", "ta", "tb", "student")}
        self.client = TestClient(app)
        self.current: int | None = None

        def fake_user(request: Request, db: Session = Depends(get_db)):
            header = request.headers.get("x-test-user")
            if not header:
                raise HTTPException(status_code=401, detail="Not authenticated")
            return db.get(User, int(header))

        app.dependency_overrides[get_current_user] = fake_user

    def tearDown(self):
        app.dependency_overrides.pop(get_current_user, None)
        uids = list(self.ids.values())
        with Session(bind=engine) as db:
            db.execute(delete(SchedulingNotice).where(SchedulingNotice.created_by_id.in_(uids) | SchedulingNotice.trainer_id.in_(uids)
                                                      | SchedulingNotice.batch_id.in_(self._batch_ids(db))))
            db.execute(delete(BatchTrainerAssignment).where(BatchTrainerAssignment.actor_id.in_(uids)))
            from app.models.class_session import ClassSession
            db.execute(delete(ClassSession).where(ClassSession.batch_id.in_(self._batch_ids(db))))
            db.execute(delete(Batch).where(Batch.name.like(f"sched-{self.tag}-%")))
            db.execute(delete(TrainerAvailabilityEntry).where(TrainerAvailabilityEntry.trainer_id.in_(uids)))
            db.execute(delete(TrainerAvailabilityRule).where(TrainerAvailabilityRule.trainer_id.in_(uids)))
            db.execute(delete(TrainerExpertise).where(TrainerExpertise.trainer_id.in_(uids)))
            db.execute(delete(AuthAuditLog).where(AuthAuditLog.user_id.in_(uids)))
            db.execute(delete(User).where(User.id.in_(uids)))
            db.commit()

    def _batch_ids(self, db):
        return select(Batch.id).where(Batch.name.like(f"sched-{self.tag}-%"))

    def _user(self, db, label, role, admin_role_id=None) -> int:
        u = User(username=f"sched-{self.tag}-{label}@example.test", password_hash="unused", full_name=f"Sched {label} {self.tag}",
                 role=role, admin_role_id=admin_role_id, is_active=True)
        db.add(u)
        db.flush()
        return u.id

    def _batch(self, label, days="Mon,Wed", start="10:00", end="12:00", weeks=4, program=True, trainer=None) -> int:
        from datetime import time
        with Session(bind=engine) as db:
            b = Batch(name=f"sched-{self.tag}-{label}", batch_type=BatchTypeEnum.online, status=BatchStatusEnum.upcoming,
                      class_days=days, start_time=time.fromisoformat(start), end_time=time.fromisoformat(end),
                      start_date=self.monday, end_date=self.monday + timedelta(weeks=weeks),
                      program_id=self.program_id if program else None, trainer_id=trainer)
            db.add(b)
            db.commit()
            return b.id

    def call(self, who, method, path, **kw):
        headers = {"x-test-user": str(self.ids[who])} if who else {}
        return self.client.request(method, path, headers=headers, **kw)

    def weekdays_9_to_5(self, who="ta"):
        r = self.call(who, "POST", f"{ME}/rules", json={"weekdays": [0, 1, 2, 3, 4], "start_time": "09:00", "end_time": "17:00",
                                                          "effective_from": (self.monday - timedelta(days=30)).isoformat()})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()

    def expertise(self, trainer="ta"):
        r = self.call("admin", "POST", f"{BASE}/trainers/{self.ids[trainer]}/expertise", json={"program_ids": [self.program_id]})
        self.assertEqual(r.status_code, 200, r.text)

    def assign(self, who, batch_id, trainer, key=None, **extra):
        return self.call(who, "POST", f"{BASE}/batches/{batch_id}/assign",
                         json={"trainer_id": self.ids[trainer] if trainer else None, "idempotency_key": key or uuid.uuid4().hex, **extra})

    # ------------------------------------------------------------- availability

    def test_trainer_availability_crud_persists(self):
        self.weekdays_9_to_5()
        day = (self.monday + timedelta(days=1)).isoformat()
        r = self.call("ta", "POST", f"{ME}/entries", json={"kind": "leave", "category": "leave", "start_date": day,
                                                           "start_time": "00:00", "end_time": "23:59", "note": "Family"})
        self.assertEqual(r.status_code, 201, r.text)
        entry_id = r.json()["id"]
        r = self.call("ta", "PATCH", f"{ME}/entries/{entry_id}", json={"kind": "unavailable", "category": "personal"})
        self.assertEqual((r.status_code, r.json()["kind"]), (200, "unavailable"))
        cal = self.call("ta", "GET", ME, params={"start": self.monday.isoformat(), "end": (self.monday + timedelta(days=6)).isoformat()}).json()
        self.assertEqual(len(cal["rules"]), 5)
        self.assertTrue(any(s["state"] == "unavailable" and s["date"] == day for s in cal["segments"]))
        self.assertGreater(cal["summary"]["available_hours"], 0)
        self.assertEqual(self.call("ta", "DELETE", f"{ME}/entries/{entry_id}").status_code, 200)
        cal = self.call("ta", "GET", ME, params={"start": self.monday.isoformat(), "end": (self.monday + timedelta(days=6)).isoformat()}).json()
        self.assertFalse(any(s["state"] == "unavailable" for s in cal["segments"]))
        with Session(bind=engine) as db:  # audit trail written
            self.assertGreaterEqual(db.scalar(select(func.count()).select_from(AuthAuditLog).where(
                AuthAuditLog.user_id == self.ids["ta"], AuthAuditLog.event_type == AuthEventType.trainer_availability_changed)), 3)

    def test_validation_errors(self):
        day = self.monday.isoformat()
        bad = self.call("ta", "POST", f"{ME}/entries", json={"kind": "leave", "start_date": day, "start_time": "10:00", "end_time": "09:00"})
        self.assertEqual((bad.status_code, bad.json()["code"]), (422, "invalid_range"))
        past = self.call("ta", "POST", f"{ME}/entries", json={"kind": "leave", "start_date": "2020-01-01", "start_time": "09:00", "end_time": "10:00"})
        self.assertEqual((past.status_code, past.json()["code"]), (409, "past_entry"))
        self.assertEqual(self.call("ta", "POST", f"{ME}/rules", json={"weekdays": [9], "start_time": "09:00", "end_time": "10:00",
                                                                      "effective_from": day}).status_code, 422)
        self.assertEqual(self.call("ta", "GET", ME, params={"start": "2026-01-01", "end": "2027-01-01"}).status_code, 422)

    def test_trainer_cannot_touch_another_trainers_data_or_admin_routes(self):
        self.weekdays_9_to_5("tb")
        with Session(bind=engine) as db:
            rule_b = db.scalar(select(TrainerAvailabilityRule.id).where(TrainerAvailabilityRule.trainer_id == self.ids["tb"]))
        self.assertEqual(self.call("ta", "DELETE", f"{ME}/rules/{rule_b}").status_code, 404)
        self.assertEqual(self.call("ta", "PATCH", f"{ME}/rules/{rule_b}", json={"start_time": "08:00"}).status_code, 404)
        r = self.call("tb", "POST", f"{ME}/entries", json={"kind": "leave", "start_date": (self.monday).isoformat(),
                                                           "start_time": "09:00", "end_time": "10:00"})
        self.assertEqual(self.call("ta", "DELETE", f"{ME}/entries/{r.json()['id']}").status_code, 404)
        self.assertEqual(self.call("ta", "PATCH", f"{ME}/entries/{r.json()['id']}", json={"note": "x"}).status_code, 404)
        # ta's own calendar never contains tb's rows, and a client-supplied trainer_id is ignored.
        mine = self.call("ta", "GET", ME, params={"trainer_id": self.ids["tb"]}).json()
        self.assertEqual((mine["trainer_id"], mine["rules"]), (self.ids["ta"], []))
        for path in (f"{BASE}/trainers", f"{BASE}/notices", f"{BASE}/history"):
            self.assertEqual(self.call("ta", "GET", path).status_code, 403, path)
        b = self._batch("rbac")
        self.assertEqual(self.assign("ta", b, "ta").status_code, 403)
        self.assertEqual(self.call("student", "GET", ME).status_code, 403)
        self.assertEqual(self.call(None, "GET", ME).status_code, 401)

    def test_admin_permissions_are_enforced_on_the_backend(self):
        b = self._batch("perm")
        self.assertEqual(self.call("viewer", "GET", f"{BASE}/trainers").status_code, 200)  # scheduling.view
        self.assertEqual(self.assign("viewer", b, "ta").status_code, 403)                  # lacks scheduling.manage
        self.assertEqual(self.call("viewer", "POST", f"{BASE}/trainers/{self.ids['ta']}/expertise", json={"program_ids": []}).status_code, 403)

    # ---------------------------------------------------------------- allocation

    def test_valid_assignment_records_history_audit_and_notifies(self):
        self.weekdays_9_to_5()
        self.expertise()
        b = self._batch("ok")
        with patch("app.core.scheduling_service.notify_batch_update") as email:
            r = self.assign("admin", b, "ta")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual((r.json()["action"], r.json()["trainer_id"]), ("assigned", self.ids["ta"]))
        self.assertEqual(email.call_args[0][1], f"sched-{self.tag}-ok")
        with Session(bind=engine) as db:
            self.assertEqual(db.get(Batch, b).trainer_id, self.ids["ta"])
            self.assertEqual(db.scalar(select(func.count()).select_from(BatchTrainerAssignment).where(BatchTrainerAssignment.batch_id == b)), 1)
            self.assertEqual(db.scalar(select(SchedulingNotice.kind).where(SchedulingNotice.recipient_id == self.ids["ta"],
                                                                           SchedulingNotice.batch_id == b)), "batch_assigned")
            self.assertEqual(db.scalar(select(func.count()).select_from(AuthAuditLog).where(
                AuthAuditLog.user_id == self.ids["admin"], AuthAuditLog.event_type == AuthEventType.batch_trainer_assigned)), 1)
        hist = self.call("admin", "GET", f"{BASE}/history", params={"batch_id": b}).json()
        self.assertEqual((hist["total"], hist["items"][0]["to_trainer"].startswith("Sched ta")), (1, True))
        # reassigning notifies both trainers
        self.weekdays_9_to_5("tb")
        with patch("app.core.scheduling_service.notify_batch_update") as email:
            r = self.assign("admin", b, "tb")
        self.assertEqual(r.json()["action"], "changed")
        self.assertEqual(email.call_count, 2)

    def test_retried_confirm_is_idempotent(self):
        self.weekdays_9_to_5()
        b = self._batch("idem")
        key = uuid.uuid4().hex
        first = self.assign("admin", b, "ta", key=key)
        second = self.assign("admin", b, "ta", key=key)
        self.assertEqual((first.status_code, second.status_code, second.json()["replayed"]), (200, 200, True))
        other = self._batch("idem2")
        self.assertEqual(self.assign("admin", other, "ta", key=key).json()["code"], "idempotency_conflict")
        with Session(bind=engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(BatchTrainerAssignment).where(BatchTrainerAssignment.batch_id == b)), 1)

    def test_overlapping_confirmed_session_is_rejected_and_cannot_be_overridden(self):
        self.weekdays_9_to_5()
        first = self._batch("first")
        clash = self._batch("clash", start="11:00", end="13:00")
        self.assertEqual(self.assign("admin", first, "ta").status_code, 200)
        r = self.assign("super_admin", clash, "ta", override=True, reason="Needs to happen regardless of the clash")
        self.assertEqual((r.status_code, r.json()["code"], r.json()["can_override"]), (409, "overlap", False))
        with Session(bind=engine) as db:
            self.assertIsNone(db.get(Batch, clash).trainer_id)

    def test_leave_blocks_assignment_until_authorised_override_with_reason(self):
        self.weekdays_9_to_5()
        wed = (self.monday + timedelta(days=2)).isoformat()
        self.call("ta", "POST", f"{ME}/entries", json={"kind": "leave", "category": "leave", "start_date": wed,
                                                       "start_time": "00:00", "end_time": "23:59"})
        b = self._batch("leave")
        r = self.assign("admin", b, "ta")
        self.assertEqual((r.status_code, r.json()["code"], r.json()["can_override"]), (409, "unavailable", False))
        self.assertEqual(r.json()["conflicts"][0]["code"], "leave")
        self.assertEqual(self.assign("admin", b, "ta", override=True, reason="Trainer agreed to work").json()["code"], "override_forbidden")
        self.assertEqual(self.assign("super_admin", b, "ta", override=True, reason="short").json()["code"], "override_reason_required")
        with patch("app.core.scheduling_service.notify_batch_update"):
            ok = self.assign("super_admin", b, "ta", override=True, reason="Trainer agreed to work this day")
        self.assertEqual((ok.status_code, ok.json()["was_override"]), (200, True))
        with Session(bind=engine) as db:
            row = db.scalar(select(BatchTrainerAssignment).where(BatchTrainerAssignment.batch_id == b))
            self.assertTrue(row.was_override)
            self.assertEqual(row.overridden_conflicts[0]["code"], "leave")
            kinds = set(db.scalars(select(SchedulingNotice.kind).where(SchedulingNotice.recipient_id == self.ids["ta"])))
            self.assertIn("scheduling_conflict", kinds)
            self.assertEqual(db.scalar(select(func.count()).select_from(AuthAuditLog).where(
                AuthAuditLog.event_type == AuthEventType.batch_trainer_override, AuthAuditLog.user_id == self.ids["super_admin"])), 1)

    def test_assignment_cannot_exceed_available_time(self):
        self.call("ta", "POST", f"{ME}/rules", json={"weekdays": [0], "start_time": "10:00", "end_time": "12:00",
                                                     "effective_from": (self.monday - timedelta(days=7)).isoformat()})
        b = self._batch("short")  # Mon + Wed, but only Monday is declared
        r = self.assign("admin", b, "ta")
        self.assertEqual((r.status_code, r.json()["conflicts"][0]["code"]), (409, "outside_availability"))

    def test_closed_batch_and_noop_are_rejected(self):
        self.weekdays_9_to_5()
        b = self._batch("closed")
        with Session(bind=engine) as db:
            db.get(Batch, b).status = BatchStatusEnum.completed
            db.commit()
        self.assertEqual(self.assign("admin", b, "ta").json()["code"], "batch_closed")
        self.assertEqual(self.assign("admin", 999999999, "ta").status_code, 404)
        self.assertEqual(self.assign("admin", self._batch("ghost"), "student").json()["code"], "invalid_trainer")

    def test_concurrent_allocations_never_double_book(self):
        self.weekdays_9_to_5()
        b1, b2 = self._batch("race1"), self._batch("race2", start="11:00", end="13:00")
        barrier = Barrier(2)
        ip_ua = ("127.0.0.1", "test")

        def attempt(batch_id):
            with Session(bind=engine) as db:
                actor = db.get(User, self.ids["admin"])
                barrier.wait(timeout=10)
                try:
                    with patch("app.core.scheduling_service.notify_batch_update"):
                        svc.assign_trainer(db, batch_id=batch_id, trainer_id=self.ids["ta"], actor=actor, idempotency_key=uuid.uuid4().hex,
                                           override=False, reason=None, ip=ip_ua[0], ua=ip_ua[1])
                    return "ok"
                except svc.SchedulingError as exc:
                    return exc.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = sorted(f.result(timeout=60) for f in (pool.submit(attempt, b1), pool.submit(attempt, b2)))
        self.assertEqual(results, ["ok", "overlap"])
        with Session(bind=engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Batch).where(Batch.id.in_([b1, b2]), Batch.trainer_id == self.ids["ta"])), 1)

    def test_concurrent_retries_with_one_key_create_one_assignment(self):
        self.weekdays_9_to_5()
        b = self._batch("retry")
        key = uuid.uuid4().hex
        barrier = Barrier(2)

        def attempt():
            with Session(bind=engine) as db:
                actor = db.get(User, self.ids["admin"])
                barrier.wait(timeout=10)
                with patch("app.core.scheduling_service.notify_batch_update"):
                    return svc.assign_trainer(db, batch_id=b, trainer_id=self.ids["ta"], actor=actor, idempotency_key=key,
                                              override=False, reason=None, ip="127.0.0.1", ua="test").replayed

        with ThreadPoolExecutor(max_workers=2) as pool:
            replays = sorted(f.result(timeout=60) for f in (pool.submit(attempt), pool.submit(attempt)))
        self.assertEqual(replays, [False, True])
        with Session(bind=engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(BatchTrainerAssignment).where(BatchTrainerAssignment.batch_id == b)), 1)

    # ----------------------------------------------------------- recommendations

    def test_recommendations_rank_explain_and_never_assign(self):
        self.weekdays_9_to_5("ta")
        self.weekdays_9_to_5("tb")
        self.expertise("ta")
        self.expertise("tb")
        self.call("tb", "POST", f"{ME}/rules", json={"weekdays": [0, 2], "start_time": "10:00", "end_time": "12:00", "kind": "preferred",
                                                      "effective_from": (self.monday - timedelta(days=7)).isoformat()})
        b = self._batch("rec")
        data = self.call("admin", "GET", f"{BASE}/batches/{b}/recommendations").json()
        mine = [r for r in data["suitable"] if r["trainer_id"] in (self.ids["ta"], self.ids["tb"])]
        self.assertEqual([r["trainer_id"] for r in mine], [self.ids["tb"], self.ids["ta"]])  # preferred hours win the tie
        self.assertEqual(sum(p["points"] for p in mine[0]["score_breakdown"]), mine[0]["score"])
        self.assertTrue(all(p["note"] for p in mine[0]["score_breakdown"]))
        with Session(bind=engine) as db:
            self.assertIsNone(db.get(Batch, b).trainer_id)  # suggestions never allocate

    def test_current_assignment_ranks_first_and_unqualified_are_excluded_with_reasons(self):
        self.weekdays_9_to_5("ta")
        self.weekdays_9_to_5("tb")
        self.expertise("tb")  # ta has no expertise
        b = self._batch("cur", trainer=self.ids["tb"])
        data = self.call("admin", "GET", f"{BASE}/batches/{b}/recommendations").json()
        self.assertTrue(data["suitable"][0]["is_current"] and data["suitable"][0]["trainer_id"] == self.ids["tb"])
        ex = next(r for r in data["excluded"] if r["trainer_id"] == self.ids["ta"])
        self.assertTrue(any("expertise" in reason for reason in ex["reasons"]))

    def test_no_suitable_trainer_explains_suggests_alternatives_and_alerts_admins_once(self):
        # Make every active trainer in the database unavailable for the batch by choosing a time nobody declared.
        b = self._batch("none", days="Sun", start="02:00", end="03:00")
        with Session(bind=engine) as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(SchedulingNotice).where(SchedulingNotice.batch_id == b)), 0)
        self.weekdays_9_to_5("ta")
        self.expertise("ta")
        for _ in range(2):
            data = self.call("admin", "GET", f"{BASE}/batches/{b}/recommendations").json()
        self.assertEqual(data["suitable"], [])
        ta_row = next(r for r in data["excluded"] if r["trainer_id"] == self.ids["ta"])
        self.assertEqual(ta_row["conflicts"][0]["code"], "outside_availability")
        self.assertTrue(any(a["trainer_id"] == self.ids["ta"] for a in data["alternatives"]))
        notices = self.call("admin", "GET", f"{BASE}/notices").json()
        self.assertEqual(len([n for n in notices if n["batch_id"] == b and n["kind"] == "no_suitable_trainer"]), 1)
        # assigning later (with override) resolves the alert
        with patch("app.core.scheduling_service.notify_batch_update"):
            self.assertEqual(self.assign("super_admin", b, "ta", override=True, reason="Weekend class approved by ops").status_code, 200)
        self.assertFalse([n for n in self.call("admin", "GET", f"{BASE}/notices").json() if n["batch_id"] == b])

    # ------------------------------------------- availability changes vs bookings

    def test_removing_availability_under_a_confirmed_batch_is_blocked_then_reviewable(self):
        self.weekdays_9_to_5()
        b = self._batch("held")
        with patch("app.core.scheduling_service.notify_batch_update"):
            self.assertEqual(self.assign("admin", b, "ta").status_code, 200)
        with Session(bind=engine) as db:
            rule_id = db.scalar(select(TrainerAvailabilityRule.id).where(TrainerAvailabilityRule.trainer_id == self.ids["ta"],
                                                                         TrainerAvailabilityRule.weekday == 0))
        blocked = self.call("ta", "DELETE", f"{ME}/rules/{rule_id}")
        self.assertEqual((blocked.status_code, blocked.json()["code"], blocked.json()["can_submit_for_review"]), (409, "confirmed_batch_conflict", True))
        self.assertTrue(blocked.json()["conflicts"][0]["batch_name"].startswith("sched-"))
        wed = (self.monday + timedelta(days=2)).isoformat()
        leave = self.call("ta", "POST", f"{ME}/entries", json={"kind": "leave", "start_date": wed, "start_time": "00:00", "end_time": "23:59"})
        self.assertEqual(leave.status_code, 409)
        review = self.call("ta", "POST", f"{ME}/entries", json={"kind": "leave", "start_date": wed, "start_time": "00:00",
                                                                "end_time": "23:59", "submit_for_review": True, "review_note": "Surgery"})
        self.assertEqual((review.status_code, review.json()["status"]), (202, "submitted_for_review"))
        with Session(bind=engine) as db:  # nothing was applied; admins got an alert
            self.assertEqual(db.scalar(select(func.count()).select_from(TrainerAvailabilityRule).where(TrainerAvailabilityRule.id == rule_id)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(TrainerAvailabilityEntry).where(TrainerAvailabilityEntry.trainer_id == self.ids["ta"])), 0)
        alerts = self.call("admin", "GET", f"{BASE}/notices").json()
        self.assertTrue(any(n["kind"] == "availability_review" and n["trainer_id"] == self.ids["ta"] for n in alerts))
        # unrelated availability changes are still free
        self.assertEqual(self.call("ta", "POST", f"{ME}/entries", json={"kind": "leave", "start_date": (self.monday + timedelta(days=3)).isoformat(),
                                                                        "start_time": "00:00", "end_time": "23:59"}).status_code, 201)

    def test_started_weekly_rule_is_closed_not_erased_and_edit_splits(self):
        r = self.call("ta", "POST", f"{ME}/rules", json={"weekdays": [5], "start_time": "09:00", "end_time": "12:00",
                                                         "effective_from": (self.monday - timedelta(days=60)).isoformat()}).json()[0]
        edited = self.call("ta", "PATCH", f"{ME}/rules/{r['id']}", json={"end_time": "13:00"})
        self.assertEqual(edited.status_code, 200, edited.text)
        with Session(bind=engine) as db:
            rows = db.scalars(select(TrainerAvailabilityRule).where(TrainerAvailabilityRule.trainer_id == self.ids["ta"])
                              .order_by(TrainerAvailabilityRule.effective_from)).all()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0].end_time.hour, 12)
            self.assertLess(rows[0].effective_until, rows[1].effective_from)

    # ------------------------------------------------------------ admin views

    def test_admin_list_filters_compare_and_request_availability(self):
        self.weekdays_9_to_5("ta")
        self.expertise("ta")
        b = self._batch("busy", days="Mon", start="10:00", end="12:00")
        with patch("app.core.scheduling_service.notify_batch_update"):
            self.assign("admin", b, "ta")
        lst = self.call("admin", "GET", f"{BASE}/trainers", params={"search": f"ta {self.tag}", "program_id": self.program_id,
                                                                     "start": self.monday.isoformat(), "end": (self.monday + timedelta(days=6)).isoformat()}).json()
        row = lst["items"][0]
        self.assertEqual((lst["total"], row["id"], row["batch_count"], row["assigned_hours"]), (1, self.ids["ta"], 1, 2.0))
        self.assertEqual((row["available_hours"], row["free_hours"]), (40.0, 38.0))
        slot = {"slot_date": self.monday.isoformat(), "slot_start": "10:30", "slot_end": "11:30", "search": f"{self.tag}"}
        busy = self.call("admin", "GET", f"{BASE}/trainers", params={**slot, "availability": "booked"}).json()
        self.assertEqual([r["id"] for r in busy["items"]], [self.ids["ta"]])
        free = self.call("admin", "GET", f"{BASE}/trainers", params={**slot, "availability": "free"}).json()
        self.assertNotIn(self.ids["ta"], [r["id"] for r in free["items"]])
        self.assertEqual(self.call("admin", "GET", f"{BASE}/trainers", params={"availability": "free"}).status_code, 422)
        both = self.call("admin", "GET", f"{BASE}/calendar", params={"trainer_ids": f"{self.ids['ta']},{self.ids['tb']}", "start": self.monday.isoformat(),
                                                                     "end": (self.monday + timedelta(days=6)).isoformat()}).json()
        self.assertEqual(len(both), 2)
        self.assertTrue(any(s["state"] == "booked" for s in both[0]["segments"] + both[1]["segments"]))
        self.assertEqual(self.call("admin", "GET", f"{BASE}/calendar", params={"trainer_ids": "1,2,3,4,5,6,7"}).status_code, 422)
        with patch("app.api.routes_admin_trainer_scheduling.notify_user") as email:
            r = self.call("admin", "POST", f"{BASE}/trainers/{self.ids['ta']}/availability-requests",
                          json={"message": "Please add December hours", "start_date": self.monday.isoformat()})
        self.assertEqual(r.status_code, 201)
        email.assert_called_once()
        mine = self.call("ta", "GET", f"{ME}/notices").json()
        self.assertTrue(any(n["kind"] == "availability_request" for n in mine))
        cal = self.call("ta", "GET", ME, params={"start": self.monday.isoformat(), "end": self.monday.isoformat()}).json()
        self.assertTrue(any(s["state"] == "requested" for s in cal["segments"]))

    # --------------------------------------------------------- legacy endpoint

    def test_legacy_batch_editor_enforces_overlap_and_leave_and_records_history(self):
        self.weekdays_9_to_5()
        a = self._batch("la")
        c = self._batch("lc", start="11:00", end="13:00")
        with patch("app.api.routes_admin_batches.write_audit_event"):
            ok = self.call("admin", "PATCH", f"/api/admin/batches/{a}", json={"trainer_id": self.ids["ta"]})
            self.assertEqual(ok.status_code, 200, ok.text)
            clash = self.call("admin", "PATCH", f"/api/admin/batches/{c}", json={"trainer_id": self.ids["ta"]})
        self.assertEqual((clash.status_code, "overlapping" in clash.text or "overlap" in clash.text), (409, True))
        with Session(bind=engine) as db:
            self.assertIsNone(db.get(Batch, c).trainer_id)
            self.assertEqual(db.scalar(select(func.count()).select_from(BatchTrainerAssignment).where(BatchTrainerAssignment.batch_id == a)), 1)
            self.assertEqual(db.scalar(select(func.count()).select_from(BatchTrainerAssignment).where(BatchTrainerAssignment.batch_id == c)), 0)

    # ------------------------------------------------- class-session routes

    def _session_setup(self):
        """ta teaches `own` (Mon/Wed 10-12) and `other` (Tue 10-12); sessions are added to `other`."""
        self.weekdays_9_to_5()
        own = self._batch("sown", trainer=self.ids["ta"])
        other = self._batch("soth", days="Tue", trainer=self.ids["ta"])
        return own, other

    def _sess(self, day_offset, start, end, title="Extra"):
        return {"title": title, "session_date": (self.monday + timedelta(days=day_offset)).isoformat(), "start_time": start, "end_time": end}

    def test_admin_session_create_and_update_reject_trainer_overlap(self):
        own, other = self._session_setup()
        url = f"/api/admin/batches/{other}/sessions"
        with patch("app.api.routes_admin_batches.notify_batch_update"):
            bad = self.call("admin", "POST", url, json=self._sess(0, "11:00", "13:00"))  # Monday, clashes with `own`
            self.assertEqual((bad.status_code, bad.json()["code"]), (409, "scheduling_conflict"))
            ok = self.call("admin", "POST", url, json=self._sess(0, "14:00", "15:00"))
            self.assertEqual(ok.status_code, 201, ok.text)
            sid = ok.json()["id"]
            self.assertEqual(self.call("admin", "PATCH", f"{url}/{sid}", json={"start_time": "11:00", "end_time": "12:00"}).status_code, 409)
            self.assertEqual(self.call("admin", "PATCH", f"{url}/{sid}", json={"session_date": (self.monday + timedelta(days=2)).isoformat(), "start_time": "10:30"}).status_code, 409)
            # editing a session never conflicts with itself; unrelated fields are not re-checked
            self.assertEqual(self.call("admin", "PATCH", f"{url}/{sid}", json={"start_time": "14:30", "end_time": "15:30"}).status_code, 200)
            self.assertEqual(self.call("admin", "PATCH", f"{url}/{sid}", json={"title": "Renamed"}).status_code, 200)
        with Session(bind=engine) as db:
            from app.models.class_session import ClassSession
            self.assertEqual(db.scalar(select(func.count()).select_from(ClassSession).where(ClassSession.batch_id == other)), 1)

    def test_trainer_session_routes_reject_overlap_and_leave(self):
        own, other = self._session_setup()
        url = f"/api/staff/me/batches/{other}/sessions"
        bad = self.call("ta", "POST", url, json=self._sess(2, "10:00", "11:00"))
        self.assertEqual((bad.status_code, bad.json()["code"]), (409, "scheduling_conflict"))
        ok = self.call("ta", "POST", url, json=self._sess(4, "09:00", "10:00"))
        self.assertEqual(ok.status_code, 201, ok.text)
        self.assertEqual(self.call("ta", "PATCH", f"{url}/{ok.json()['id']}", json=self._sess(0, "10:30", "11:30")).status_code, 409)
        # leave blocks too
        fri = (self.monday + timedelta(days=4)).isoformat()
        self.call("ta", "POST", f"{ME}/entries", json={"kind": "leave", "start_date": fri, "start_time": "13:00", "end_time": "18:00"})
        self.assertEqual(self.call("ta", "POST", url, json=self._sess(4, "14:00", "15:00")).status_code, 409)

    def test_session_routes_keep_authorization_and_allow_unassigned_batches(self):
        own, other = self._session_setup()
        url = f"/api/admin/batches/{other}/sessions"
        self.assertEqual(self.call("viewer", "POST", url, json=self._sess(0, "14:00", "15:00")).status_code, 403)  # lacks batches.edit
        self.assertEqual(self.call("ta", "POST", url, json=self._sess(0, "14:00", "15:00")).status_code, 403)
        self.assertEqual(self.call("tb", "POST", f"/api/staff/me/batches/{other}/sessions", json=self._sess(0, "14:00", "15:00")).status_code, 404)
        free = self._batch("sfree")  # no trainer -> nothing to double-book
        with patch("app.api.routes_admin_batches.notify_batch_update"):
            self.assertEqual(self.call("admin", "POST", f"/api/admin/batches/{free}/sessions", json=self._sess(0, "11:00", "12:00")).status_code, 201)

    def test_concurrent_session_creation_cannot_double_book(self):
        own, other = self._session_setup()
        third = self._batch("sthird", days="Thu", trainer=self.ids["ta"])
        barrier = Barrier(2)
        payload = self._sess(1, "16:00", "17:00")  # Tuesday 16-17 for two different batches of the same trainer

        def attempt(batch_id):
            client = TestClient(app)
            barrier.wait(timeout=10)
            with patch("app.api.routes_admin_batches.notify_batch_update"):
                return client.post(f"/api/admin/batches/{batch_id}/sessions", json=payload, headers={"x-test-user": str(self.ids["admin"])}).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            codes = sorted(f.result(timeout=60) for f in (pool.submit(attempt, other), pool.submit(attempt, third)))
        self.assertEqual(codes, [201, 409])

    # ------------------------------------------------------ trainer deletion

    def test_trainer_with_only_own_scheduling_data_can_still_be_deleted_but_history_blocks(self):
        self.weekdays_9_to_5("ta")
        self.expertise("ta")
        self.call("admin", "POST", f"{BASE}/trainers/{self.ids['ta']}/availability-requests",
                  json={"message": "Please add hours", "start_date": self.monday.isoformat()})
        gone = self.call("super_admin", "DELETE", f"/api/admin/staff/{self.ids['ta']}")
        self.assertEqual(gone.status_code, 204, gone.text)
        with Session(bind=engine) as db:
            for model, col in ((TrainerAvailabilityRule, TrainerAvailabilityRule.trainer_id), (TrainerExpertise, TrainerExpertise.trainer_id),
                               (SchedulingNotice, SchedulingNotice.trainer_id)):
                self.assertEqual(db.scalar(select(func.count()).select_from(model).where(col == self.ids["ta"])), 0)
        self.weekdays_9_to_5("tb")
        b = self._batch("hist")
        with patch("app.core.scheduling_service.notify_batch_update"):
            self.assertEqual(self.assign("admin", b, "tb").status_code, 200)
            self.assertEqual(self.assign("admin", b, None).status_code, 200)  # tb now only appears in assignment history
        blocked = self.call("super_admin", "DELETE", f"/api/admin/staff/{self.ids['tb']}")
        self.assertEqual(blocked.status_code, 409, blocked.text)


if __name__ == "__main__":
    unittest.main()

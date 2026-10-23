"""Pure-logic tests for the trainer scheduling engine (no database)."""
import unittest
from datetime import date, datetime, time, timedelta, timezone
from unittest.mock import patch

from app.core import scheduling as sch
from app.models.batch import Batch, BatchStatusEnum
from app.models.class_session import ClassSession
from app.models.trainer_scheduling import TrainerAvailabilityEntry, TrainerAvailabilityRule

UTC = timezone.utc
IST = sch.ZoneInfo("Asia/Kolkata")
NY = sch.ZoneInfo("America/New_York")


def rule(weekday, start="09:00", end="17:00", kind="available", frm=date(2026, 1, 1), until=None):
    return TrainerAvailabilityRule(weekday=weekday, start_time=time.fromisoformat(start), end_time=time.fromisoformat(end),
                                   kind=kind, effective_from=frm, effective_until=until)


def entry(kind, start_utc, end_utc):
    return TrainerAvailabilityEntry(kind=kind, starts_at=start_utc, ends_at=end_utc)


def batch(bid, days="Mon,Wed", start="10:00", end="12:00", frm=date(2026, 11, 2), until=date(2026, 11, 30), program=None):
    b = Batch(id=bid, name=f"Batch {bid}", class_days=days, start_time=time.fromisoformat(start), end_time=time.fromisoformat(end),
              start_date=frm, end_date=until, program_id=program, status=BatchStatusEnum.upcoming)
    b.sessions = []
    return b


NOW = datetime(2026, 10, 23, 6, 0, tzinfo=UTC)


class IntervalTests(unittest.TestCase):
    def test_merge_and_subtract(self):
        a = datetime(2026, 1, 1, 9, tzinfo=UTC)
        h = lambda n: a + timedelta(hours=n)
        self.assertEqual(sch.merge([(h(0), h(2)), (h(2), h(3)), (h(5), h(6))]), [(h(0), h(3)), (h(5), h(6))])
        self.assertEqual(sch.subtract([(h(0), h(8))], [(h(2), h(3)), (h(6), h(9))]), [(h(0), h(2)), (h(3), h(6))])
        self.assertTrue(sch.is_covered((h(1), h(2)), [(h(0), h(3))]))
        self.assertFalse(sch.is_covered((h(1), h(4)), [(h(0), h(3))]))


class TimezoneTests(unittest.TestCase):
    def test_local_midnight_boundary_keeps_local_date(self):
        # 00:00-01:00 IST on 10 Jun is 18:30-19:30 UTC on 9 Jun, but is still "10 Jun" locally.
        iv = (sch.local_to_utc(date(2026, 6, 10), time(0, 0), IST), sch.local_to_utc(date(2026, 6, 10), time(1, 0), IST))
        self.assertEqual(iv[0], datetime(2026, 6, 9, 18, 30, tzinfo=UTC))
        self.assertEqual(list(sch.split_by_local_day(iv, IST)), [(date(2026, 6, 10), "00:00", "01:00")])

    def test_interval_crossing_midnight_is_split_per_local_day(self):
        iv = (sch.local_to_utc(date(2026, 6, 10), time(23, 0), IST), sch.local_to_utc(date(2026, 6, 11), time(1, 0), IST))
        self.assertEqual(list(sch.split_by_local_day(iv, IST)),
                         [(date(2026, 6, 10), "23:00", "24:00"), (date(2026, 6, 11), "00:00", "01:00")])

    def test_dst_days_have_23_and_25_hours_and_wall_clock_is_stable(self):
        spring = sch.day_bounds_utc(date(2026, 3, 8), NY)
        fall = sch.day_bounds_utc(date(2026, 11, 1), NY)
        self.assertEqual(spring[1] - spring[0], timedelta(hours=23))
        self.assertEqual(fall[1] - fall[0], timedelta(hours=25))
        # A 09:00 start is 14:00Z before the change and 13:00Z after: the local working day does not move.
        self.assertEqual(sch.local_to_utc(date(2026, 3, 7), time(9), NY).hour, 14)
        self.assertEqual(sch.local_to_utc(date(2026, 3, 9), time(9), NY).hour, 13)

    def test_weekly_rule_follows_wall_clock_across_dst(self):
        with patch.object(sch.settings, "scheduling_timezone", "America/New_York"):
            cal = sch.TrainerCalendar(1, rules=[rule(0), rule(1), rule(2), rule(3), rule(4)])
            lo, hi = datetime(2026, 3, 6, tzinfo=UTC), datetime(2026, 3, 11, tzinfo=UTC)
            starts = sorted(s for s, _ in cal.rule_intervals("available", lo, hi, NY))
            self.assertEqual([s.hour for s in starts], [14, 13, 13])  # Fri 6th is EST; Mon 9th and Tue 10th are EDT


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.weekdays = [rule(d) for d in range(5)]  # Mon-Fri 09:00-17:00

    def eval(self, b, cal, expertise=frozenset(), **kw):
        return sch.evaluate(sch.required_occurrences(b, NOW, IST), cal, set(expertise), b.program_id, b.id, IST, **kw)

    def test_free_trainer_is_eligible_with_expertise(self):
        b = batch(1, program=7)
        ev = self.eval(b, sch.TrainerCalendar(1, rules=self.weekdays), {7})
        self.assertTrue(ev.eligible)
        self.assertEqual(ev.session_count, 9)  # Mon/Wed between 2 and 30 Nov 2026
        self.assertEqual(ev.required_hours, 18.0)

    def test_missing_expertise_excludes(self):
        b = batch(1, program=7)
        ev = self.eval(b, sch.TrainerCalendar(1, rules=self.weekdays), {8})
        self.assertFalse(ev.eligible)
        self.assertIs(ev.expertise_matched, False)

    def test_no_declared_availability_is_a_conflict(self):
        ev = self.eval(batch(1), sch.TrainerCalendar(1))
        self.assertEqual([c.code for c in ev.conflicts], ["outside_availability"])
        self.assertTrue(ev.conflicts[0].overridable)

    def test_leave_exception_removes_one_day_and_is_reported(self):
        wed = sch.local_to_utc(date(2026, 11, 4), time(0), IST)
        cal = sch.TrainerCalendar(1, rules=self.weekdays, entries=[entry("leave", wed, wed + timedelta(days=1))])
        ev = self.eval(batch(1), cal)
        self.assertEqual([c.code for c in ev.conflicts], ["leave"])
        self.assertEqual(ev.conflicts[0].count, 1)
        self.assertEqual(ev.conflicts[0].examples[0]["date"], "2026-11-04")

    def test_extra_available_entry_covers_a_weekend_session(self):
        b = batch(1, days="Sat", frm=date(2026, 11, 7), until=date(2026, 11, 7))
        sat = sch.local_to_utc(date(2026, 11, 7), time(9), IST)
        self.assertFalse(self.eval(b, sch.TrainerCalendar(1, rules=self.weekdays)).eligible)
        ok = self.eval(b, sch.TrainerCalendar(1, rules=self.weekdays, entries=[entry("available", sat, sat + timedelta(hours=8))]))
        self.assertTrue(ok.eligible)

    def test_overlap_with_other_confirmed_batch_is_not_overridable(self):
        other = batch(2, start="11:00", end="13:00")
        cal = sch.TrainerCalendar(1, rules=self.weekdays, batches=[other])
        ev = self.eval(batch(1), cal)
        codes = {c.code: c for c in ev.conflicts}
        self.assertFalse(codes["overlap"].overridable)
        self.assertTrue(ev.hard_blocked)

    def test_back_to_back_sessions_do_not_overlap(self):
        cal = sch.TrainerCalendar(1, rules=self.weekdays, batches=[batch(2, start="12:00", end="14:00")])
        self.assertTrue(self.eval(batch(1), cal).eligible)

    def test_own_batch_is_ignored_when_reevaluating_current_trainer(self):
        b = batch(1)
        cal = sch.TrainerCalendar(1, rules=self.weekdays, batches=[b])
        self.assertTrue(self.eval(b, cal).eligible)

    def test_session_longer_than_window_is_outside_availability(self):
        ev = self.eval(batch(1, start="16:00", end="18:00"), sch.TrainerCalendar(1, rules=self.weekdays))
        self.assertEqual([c.code for c in ev.conflicts], ["outside_availability"])

    def test_workload_limit(self):
        ev = self.eval(batch(1), sch.TrainerCalendar(1, rules=self.weekdays), max_weekly_hours=3)
        self.assertEqual([c.code for c in ev.conflicts], ["workload"])

    def test_preferred_hours_raise_ratio(self):
        cal = sch.TrainerCalendar(1, rules=self.weekdays + [rule(0, "10:00", "12:00", "preferred"), rule(2, "10:00", "11:00", "preferred")])
        ev = self.eval(batch(1), cal)
        self.assertTrue(ev.eligible)  # preferred time also counts as available
        self.assertAlmostEqual(ev.preferred_ratio, 14 / 18, places=2)  # 5 Mondays x 2h + 4 Wednesdays x 1h of 18h

    def test_explicit_class_session_is_also_a_booking(self):
        other = batch(2, days="", start="09:00", end="10:00")
        other.class_days = None
        s = ClassSession(title="Extra", session_date=date(2026, 11, 2), start_time=time(10, 30), end_time=time(11, 30))
        other.sessions = [s]
        cal = sch.TrainerCalendar(1, rules=self.weekdays, batches=[other])
        ev = self.eval(batch(1), cal)
        self.assertEqual(ev.conflicts[0].code, "overlap")
        self.assertEqual(ev.conflicts[0].count, 1)

    def test_batch_without_schedule_cannot_be_checked(self):
        b = batch(1)
        b.class_days = None
        ev = self.eval(b, sch.TrainerCalendar(1))
        self.assertFalse(ev.schedule_checked)
        self.assertEqual(ev.conflicts, [])

    def test_past_sessions_are_ignored(self):
        b = batch(1, frm=date(2026, 9, 1), until=date(2026, 10, 1))
        ev = self.eval(b, sch.TrainerCalendar(1))
        self.assertEqual(ev.session_count, 0)

    def test_score_breakdown_is_explained(self):
        parts = sch.score_breakdown(sch.Evaluation(1, expertise_matched=True, preferred_ratio=0.5, peak_week_hours=10), 40)
        self.assertEqual([p["points"] for p in parts], [40, 17.5, 18.8])
        self.assertTrue(all(p["note"] for p in parts))


class UncoveredSessionsTests(unittest.TestCase):
    def test_detects_only_sessions_that_lose_coverage(self):
        b = batch(1)
        full = sch.TrainerCalendar(1, rules=[rule(d) for d in range(5)], batches=[b])
        self.assertEqual(sch.uncovered_confirmed_sessions(full, NOW, IST), set())
        wed = sch.local_to_utc(date(2026, 11, 4), time(0), IST)
        full.entries.append(entry("unavailable", wed, wed + timedelta(days=1)))
        gone = sch.uncovered_confirmed_sessions(full, NOW, IST)
        self.assertEqual(len(gone), 1)
        self.assertEqual(next(iter(gone))[0], 1)


class ResetPolicyClassificationTests(unittest.TestCase):
    def test_scheduling_tables_are_classified_conservatively(self):
        from maintenance.reset_policy import ALL_TABLES, MANUAL, OWNED
        for table in ("trainer_availability_rules", "trainer_availability_entries", "trainer_expertise", "scheduling_notices"):
            self.assertEqual(OWNED[table], "trainer_id")
            self.assertIn(table, ALL_TABLES)
        # Shared batch history is held for a human retention decision, never auto-deleted or auto-kept.
        self.assertIn("batch_trainer_assignments", MANUAL)
        self.assertNotIn("batch_trainer_assignments", OWNED)


if __name__ == "__main__":
    unittest.main()

"""Tests for v7.1.17 — "Расписание" schedule module: SOURCE sync engine.

Covers spec section 23 SOURCE checks 1-12: period bounds, pagination
primitive used, retry/backoff, 429 handling, partial-error non-activation,
idempotent re-run, old snapshot preservation, no duplicate entity
requests, cancelled-lesson exclusion, reschedule-not-becoming-main-slot,
restart/resume, and real (non-fabricated) progress counters.

Run offline (MoyKlass client is always a MagicMock here):
    python -m unittest tests.test_schedule_source_sync_v7117 -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import schedule_sync
from storage import Storage, SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END


def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _mk_result(ok=True, data=None, status=200, error=""):
    r = MagicMock()
    r.ok = ok
    r.data = data or {}
    r.status = status
    r.error = error
    return r


def _lesson_record(user_id, lesson_id, date, start_time="17:00", end_time="18:00",
                    class_id="500", status="1", filial_id="1", teacher_id="200", visit=True):
    return {
        "id": f"rec-{lesson_id}-{user_id}",
        "userId": user_id,
        "visit": visit,
        "lesson": {
            "id": lesson_id, "classId": class_id, "date": date,
            "beginTime": start_time, "endTime": end_time, "status": status,
            "filialId": filial_id, "teacherIds": [teacher_id],
        },
    }


def _make_moyklass(records, classes=None):
    mk = MagicMock()
    mk.is_configured = True
    mk.get_classes.return_value = _mk_result(data={"classes": classes if classes is not None else [
        {"id": "500", "name": "Творчество, четверг"},
    ]})
    mk.list_lesson_records_between.return_value = _mk_result(data={"lessonRecords": records})
    mk._lookup_maps_cached.return_value = {
        "filials": {"1": "Кульман 1/1"}, "teachers": {"200": "Мария И."}, "classes": {}, "rooms": {},
    }
    return mk


class _ThreadStub:
    """Runs the target synchronously instead of spawning a real thread —
    keeps resume/start tests deterministic without sleeping/polling."""
    def __init__(self, target=None, args=(), daemon=None):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)


def _regular_thursday_records(n_weeks=10, user_ids=("9001", "9002")):
    records = []
    from datetime import date, timedelta
    d = date(2025, 9, 4)  # a Thursday
    for i in range(n_weeks):
        lesson_date = (d + timedelta(weeks=i)).isoformat()
        for uid in user_ids:
            records.append(_lesson_record(uid, f"L{i}", lesson_date))
    return records


class TestSourcePeriod(unittest.TestCase):
    """Check 1 — fixed academic-year window, not configurable per run."""

    def test_1_period_constants(self):
        self.assertEqual(SCHEDULE_SOURCE_PERIOD_START, "2025-09-01")
        self.assertEqual(SCHEDULE_SOURCE_PERIOD_END, "2026-05-31")

    def test_1b_execute_sync_uses_fixed_period(self):
        storage = _make_storage()
        mk = _make_moyklass(_regular_thursday_records())
        snap = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        schedule_sync._execute_sync(storage, mk, snap["id"])
        (start_arg, end_arg), _kwargs = mk.list_lesson_records_between.call_args
        self.assertEqual(start_arg.isoformat(), "2025-09-01")
        self.assertEqual(end_arg.isoformat(), "2026-05-31")


class TestSourcePaginationAndRequestBudget(unittest.TestCase):
    """Checks 2, 8 — uses the paginated wrapper; no duplicate per-entity calls."""

    def test_2_uses_paginated_bulk_wrapper(self):
        storage = _make_storage()
        mk = _make_moyklass(_regular_thursday_records())
        snap = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        schedule_sync._execute_sync(storage, mk, snap["id"])
        mk.list_lesson_records_between.assert_called_once()

    def test_8_no_duplicate_entity_requests(self):
        storage = _make_storage()
        mk = _make_moyklass(_regular_thursday_records())
        snap = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        schedule_sync._execute_sync(storage, mk, snap["id"])
        self.assertEqual(mk.get_classes.call_count, 1)
        self.assertEqual(mk.list_lesson_records_between.call_count, 1)
        # get_class_users (N+1 per-student) must never be used by the bulk sync
        mk.get_class_users.assert_not_called()


class TestSourceRetry(unittest.TestCase):
    """Checks 3, 4 — retry/backoff on 429/5xx/timeout, gives up after budget."""

    def setUp(self):
        self._sleep_patch = patch("schedule_sync.time.sleep", lambda *_a: None)
        self._sleep_patch.start()
        self.addCleanup(self._sleep_patch.stop)

    def test_3_retries_on_500_then_succeeds(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                return _mk_result(ok=False, status=500, error="boom")
            return _mk_result(ok=True, data={"classes": []})

        result = schedule_sync._call_with_retry(flaky, "get_classes")
        self.assertTrue(result.ok)
        self.assertEqual(calls["n"], 3)

    def test_4_retries_on_429(self):
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            return _mk_result(ok=False, status=429, error="rate limited") if calls["n"] < 2 else _mk_result(ok=True)

        result = schedule_sync._call_with_retry(flaky, "list_lesson_records_between")
        self.assertTrue(result.ok)
        self.assertEqual(calls["n"], 2)

    def test_4b_gives_up_after_retry_budget(self):
        calls = {"n": 0}

        def always_fails():
            calls["n"] += 1
            return _mk_result(ok=False, status=503, error="down")

        result = schedule_sync._call_with_retry(always_fails, "get_classes")
        self.assertFalse(result.ok)
        self.assertEqual(calls["n"], len(schedule_sync._RETRY_BACKOFF_SECONDS) + 1)

    def test_4c_non_retryable_status_stops_immediately(self):
        calls = {"n": 0}

        def bad_request():
            calls["n"] += 1
            return _mk_result(ok=False, status=400, error="bad request")

        result = schedule_sync._call_with_retry(bad_request, "get_classes")
        self.assertFalse(result.ok)
        self.assertEqual(calls["n"], 1)


class TestSourceActivation(unittest.TestCase):
    """Checks 5, 6, 7 — partial errors never activate; resync is idempotent;
    old good snapshot survives a later failed attempt."""

    def test_5_partial_error_snapshot_not_activated(self):
        storage = _make_storage()
        records = _regular_thursday_records()
        records.append(_lesson_record("9003", "Lx", "2025-09-11", class_id=""))  # missing classId -> logged error
        mk = _make_moyklass(records)
        snap = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        schedule_sync._execute_sync(storage, mk, snap["id"])
        final = storage.get_schedule_snapshot(snap["id"])
        self.assertEqual(final["status"], "partial")
        self.assertEqual(final["is_active"], 0)
        self.assertIsNone(storage.get_active_schedule_snapshot())

    def test_6_rerun_is_idempotent(self):
        storage = _make_storage()
        mk = _make_moyklass(_regular_thursday_records())
        snap = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        schedule_sync._execute_sync(storage, mk, snap["id"])
        groups1, total1 = storage.list_schedule_source_groups(snap["id"])
        # simulate re-processing the SAME snapshot_id (resume-style re-run)
        schedule_sync._execute_sync(storage, mk, snap["id"])
        groups2, total2 = storage.list_schedule_source_groups(snap["id"])
        self.assertEqual(total1, total2)
        self.assertEqual(groups1[0]["id"], groups2[0]["id"])
        students, _ = (storage.list_schedule_source_group_students(groups2[0]["id"]), None)
        self.assertEqual(len(students), 2)  # not duplicated

    def test_7_old_active_snapshot_preserved_after_later_failure(self):
        storage = _make_storage()
        mk_good = _make_moyklass(_regular_thursday_records())
        snap1 = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        schedule_sync._execute_sync(storage, mk_good, snap1["id"])
        active1 = storage.get_active_schedule_snapshot()
        self.assertEqual(active1["id"], snap1["id"])

        mk_bad = _make_moyklass([])
        mk_bad.get_classes.return_value = _mk_result(ok=False, status=500, error="down")
        snap2 = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        with patch("schedule_sync.time.sleep", lambda *_a: None):
            schedule_sync._execute_sync(storage, mk_bad, snap2["id"])
        active2 = storage.get_active_schedule_snapshot()
        self.assertEqual(active2["id"], snap1["id"], "old good snapshot must remain active")
        self.assertEqual(storage.get_schedule_snapshot(snap2["id"])["status"], "failed")


class TestSourceLessonFiltering(unittest.TestCase):
    """Checks 9, 10 — cancelled lessons excluded; a lone reschedule never
    becomes the reported main slot."""

    def test_9_cancelled_lessons_excluded_from_slot(self):
        storage = _make_storage()
        records = _regular_thursday_records(n_weeks=8)
        # a 9th, cancelled Thursday lesson with a DIFFERENT time — if wrongly
        # counted it could tie with the real slot
        records.append(_lesson_record("9001", "Lcancel", "2025-11-06", start_time="10:00", end_time="11:00", status="cancelled"))
        mk = _make_moyklass(records)
        snap = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        schedule_sync._execute_sync(storage, mk, snap["id"])
        groups, _ = storage.list_schedule_source_groups(snap["id"])
        g = groups[0]
        self.assertEqual(g["start_time"], "17:00")
        self.assertEqual(g["lessons_count"], 9)          # stored, including the cancelled one
        self.assertEqual(g["lessons_considered"], 8)     # cancelled excluded from slot math
        lessons = storage.list_schedule_source_lessons_for_group(g["id"])
        cancelled = [l for l in lessons if l["mk_lesson_id"] == "Lcancel"]
        self.assertEqual(cancelled[0]["status"], "cancelled")

    def test_10_lone_reschedule_does_not_become_main_slot(self):
        storage = _make_storage()
        records = _regular_thursday_records(n_weeks=9)
        # one held lesson moved to a Tuesday, single occurrence only
        records.append(_lesson_record("9001", "Lmove", "2025-11-11", start_time="15:00", end_time="16:00"))
        mk = _make_moyklass(records)
        snap = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        schedule_sync._execute_sync(storage, mk, snap["id"])
        groups, _ = storage.list_schedule_source_groups(snap["id"])
        g = groups[0]
        self.assertEqual(g["weekday"], 4)  # Thursday, not the one-off Tuesday
        self.assertEqual(g["start_time"], "17:00")
        self.assertEqual(g["confidence"], "high")


class TestSourceResume(unittest.TestCase):
    """Check 11 — restart/resume: a failed+resumable snapshot can be
    continued via the SAME snapshot_id, never a new one."""

    def test_11_resume_reuses_same_snapshot_id(self):
        storage = _make_storage()
        mk_bad = _make_moyklass([])
        mk_bad.get_classes.return_value = _mk_result(ok=False, status=500, error="down")
        with patch("schedule_sync.time.sleep", lambda *_a: None), patch("schedule_sync.threading.Thread", _ThreadStub):
            res1 = schedule_sync.start_schedule_sync(storage, mk_bad, 1, "Tester")
        self.assertTrue(res1["ok"])
        failed_id = res1["snapshot"]["id"]
        self.assertEqual(storage.get_schedule_snapshot(failed_id)["status"], "failed")
        self.assertEqual(storage.get_schedule_snapshot(failed_id)["resumable"], 1)

        mk_good = _make_moyklass(_regular_thursday_records())
        with patch("schedule_sync.threading.Thread", _ThreadStub):
            res2 = schedule_sync.start_schedule_sync(storage, mk_good, 1, "Tester", resume_snapshot_id=failed_id)
        self.assertTrue(res2["ok"])
        self.assertEqual(res2["snapshot"]["id"], failed_id, "resume must reuse the same snapshot row")
        self.assertEqual(storage.get_schedule_snapshot(failed_id)["status"], "completed")

    def test_11b_cannot_resume_a_completed_snapshot(self):
        storage = _make_storage()
        mk = _make_moyklass(_regular_thursday_records())
        with patch("schedule_sync.threading.Thread", _ThreadStub):
            res = schedule_sync.start_schedule_sync(storage, mk, 1, "Tester")
        good_id = res["snapshot"]["id"]
        with patch("schedule_sync.threading.Thread", _ThreadStub):
            res2 = schedule_sync.start_schedule_sync(storage, mk, 1, "Tester", resume_snapshot_id=good_id)
        self.assertFalse(res2["ok"])
        self.assertEqual(res2["reason_code"], "not_resumable")

    def test_11c_second_concurrent_sync_rejected(self):
        storage = _make_storage()
        mk = _make_moyklass(_regular_thursday_records())
        storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        # simulate an already-running snapshot without going through the lock
        with storage._connect() as conn:
            conn.execute("UPDATE schedule_source_snapshots SET status='running'")
        res = schedule_sync.start_schedule_sync(storage, mk, 1, "Tester")
        self.assertFalse(res["ok"])
        self.assertEqual(res["reason_code"], "sync_already_running")


class TestSourceProgress(unittest.TestCase):
    """Check 12 — progress counters reflect real processed data, not a
    fabricated/fixed progress bar."""

    def test_12_progress_counters_match_real_data(self):
        storage = _make_storage()
        records = _regular_thursday_records(n_weeks=6, user_ids=("9001", "9002", "9003"))
        mk = _make_moyklass(records, classes=[{"id": "500", "name": "A"}, {"id": "999", "name": "unused"}])
        snap = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        schedule_sync._execute_sync(storage, mk, snap["id"])
        final = storage.get_schedule_snapshot(snap["id"])
        self.assertEqual(final["groups_found"], 2)       # both classes from get_classes, even the empty one
        self.assertEqual(final["groups_processed"], 1)   # only the class that actually had lesson records
        self.assertEqual(final["lessons_fetched"], 18)    # 6 weeks * 3 students
        self.assertEqual(final["students_found"], 3)
        self.assertEqual(final["stage"], "done")


if __name__ == "__main__":
    unittest.main()

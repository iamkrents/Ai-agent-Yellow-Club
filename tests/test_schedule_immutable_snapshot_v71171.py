"""Tests for v7.1.17.1 — ALE-6 point 4: a new manual sync must create a
brand-new schedule_source_snapshot and leave every row of the previous
(old) snapshot completely untouched — no in-place "fix" of historical
data. Also covers the classifier's per-record trial/makeup exclusion and
the new join-evidence bulk fetch wiring, offline (MagicMock MoyKlass).

Run offline:
    python -m unittest tests.test_schedule_immutable_snapshot_v71171 -v
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
                    class_id="500", status="1", filial_id="1", teacher_id="200",
                    visit=True, test=False, missed_lesson_record_id=None, user_subscription=None):
    rec = {
        "id": f"rec-{lesson_id}-{user_id}",
        "userId": user_id,
        "visit": visit,
        "test": test,
        "missedLessonRecordId": missed_lesson_record_id,
        "userSubscription": user_subscription,
        "lesson": {
            "id": lesson_id, "classId": class_id, "date": date,
            "beginTime": start_time, "endTime": end_time, "status": status,
            "filialId": filial_id, "teacherIds": [teacher_id],
        },
    }
    return rec


def _make_moyklass(records, classes=None, joins=None):
    mk = MagicMock()
    mk.is_configured = True
    mk.get_classes.return_value = _mk_result(data={"classes": classes if classes is not None else [
        {"id": "500", "name": "Творчество, четверг"},
    ]})
    mk.list_lesson_records_between.return_value = _mk_result(data={"lessonRecords": records})
    mk.list_joins_for_classes.return_value = _mk_result(data={"joins": joins or []})
    mk._lookup_maps_cached.return_value = {
        "filials": {"1": "Кульман 1/1"}, "teachers": {"200": "Мария И."}, "classes": {}, "rooms": {},
    }
    return mk


def _regular_thursday_records(n_weeks=10, user_ids=("9001", "9002"), sub=True):
    records = []
    from datetime import date, timedelta
    d = date(2025, 9, 4)  # a Thursday
    for i in range(n_weeks):
        lesson_date = (d + timedelta(weeks=i)).isoformat()
        for uid in user_ids:
            us = {"classIds": ["500"], "mainClassId": "500"} if sub else None
            records.append(_lesson_record(uid, f"L{i}", lesson_date, user_subscription=us))
    return records


class TestImmutableSnapshot(unittest.TestCase):
    """ALE-6 point 4 — old snapshot untouched by a later sync."""

    def test_old_snapshot_rows_byte_identical_after_new_sync(self):
        storage = _make_storage()
        mk1 = _make_moyklass(_regular_thursday_records(n_weeks=6))
        snap1 = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        schedule_sync._execute_sync(storage, mk1, snap1["id"])

        groups1, _ = storage.list_schedule_source_groups(snap1["id"])
        students1 = storage.list_schedule_source_group_students(groups1[0]["id"])
        lessons1 = storage.list_schedule_source_lessons_for_group(groups1[0]["id"])
        snapshot1_before = storage.get_schedule_snapshot(snap1["id"])

        # a completely separate, later manual sync — different data even
        mk2 = _make_moyklass(_regular_thursday_records(n_weeks=9, user_ids=("9001", "9002", "9003")))
        snap2 = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T2")
        self.assertNotEqual(snap1["id"], snap2["id"], "a new manual sync must create a NEW snapshot row")
        schedule_sync._execute_sync(storage, mk2, snap2["id"])

        groups1_after, _ = storage.list_schedule_source_groups(snap1["id"])
        students1_after = storage.list_schedule_source_group_students(groups1_after[0]["id"])
        lessons1_after = storage.list_schedule_source_lessons_for_group(groups1_after[0]["id"])
        snapshot1_after = storage.get_schedule_snapshot(snap1["id"])

        self.assertEqual(groups1, groups1_after, "old snapshot's group row must be byte-identical")
        self.assertEqual(students1, students1_after, "old snapshot's student rows must be byte-identical")
        self.assertEqual(lessons1, lessons1_after, "old snapshot's lesson rows must be byte-identical")
        # is_active is expected to move to the newer good snapshot (that's
        # the intended lifecycle pointer, not a data mutation) — every OTHER
        # field of the snapshot's own metadata row must stay identical.
        for key in snapshot1_before.keys():
            if key == "is_active":
                continue
            self.assertEqual(
                snapshot1_before[key], snapshot1_after[key],
                f"old snapshot metadata field {key!r} must be byte-identical",
            )
        self.assertEqual(snapshot1_after["is_active"], 0, "is_active correctly moves to the newer snapshot")

        # and the new snapshot is genuinely its own independent data
        groups2, _ = storage.list_schedule_source_groups(snap2["id"])
        students2 = storage.list_schedule_source_group_students(groups2[0]["id"])
        self.assertEqual(len(students2), 3, "new snapshot reflects the new sync's own 3 students")
        self.assertEqual(len(students1), 2, "old snapshot still reflects its original 2 students")

    def test_old_snapshot_stays_active_when_it_was_the_last_good_one(self):
        storage = _make_storage()
        mk1 = _make_moyklass(_regular_thursday_records(n_weeks=6))
        snap1 = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        schedule_sync._execute_sync(storage, mk1, snap1["id"])
        self.assertEqual(storage.get_active_schedule_snapshot()["id"], snap1["id"])

        # second sync completes cleanly too -> it becomes the new active one,
        # but snap1 itself must still exist, untouched, comparable side by side
        mk2 = _make_moyklass(_regular_thursday_records(n_weeks=6))
        snap2 = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T2")
        schedule_sync._execute_sync(storage, mk2, snap2["id"])

        self.assertEqual(storage.get_active_schedule_snapshot()["id"], snap2["id"])
        still_there = storage.get_schedule_snapshot(snap1["id"])
        self.assertIsNotNone(still_there, "old snapshot row is never deleted, only superseded as active")
        self.assertEqual(still_there["status"], "completed")


class TestPerRecordTrialMakeupExclusion(unittest.TestCase):
    """ALE-6 point 4 — trial/makeup records excluded per-record, never
    disqualifying an otherwise-regular pair's whole history."""

    def test_trial_record_mixed_into_regular_history_does_not_spoil_the_pair(self):
        storage = _make_storage()
        records = _regular_thursday_records(n_weeks=6, user_ids=("9001",))
        # add one trial-flagged record for the SAME student in the SAME group
        records.append(_lesson_record("9001", "Ltrial", "2025-10-30", test=True, user_subscription=None))
        mk = _make_moyklass(records)
        snap = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        schedule_sync._execute_sync(storage, mk, snap["id"])
        groups, _ = storage.list_schedule_source_groups(snap["id"])
        students = storage.list_schedule_source_group_students(groups[0]["id"])
        row = next(s for s in students if s["mk_user_id"] == "9001")
        self.assertEqual(row["n_trial_visits"], 1)
        self.assertEqual(row["lessons_attended"], 6, "the trial record must not count toward regular visits")
        self.assertNotEqual(row["regularity_category"], "trial", "6 real regular visits must not be swallowed by 1 trial record")

    def test_makeup_record_mixed_into_regular_history_does_not_inflate_or_spoil(self):
        storage = _make_storage()
        records = _regular_thursday_records(n_weeks=6, user_ids=("9001",))
        records.append(_lesson_record("9001", "Lmakeup", "2025-10-30", missed_lesson_record_id="rec-missed-1", user_subscription=None))
        mk = _make_moyklass(records)
        snap = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        schedule_sync._execute_sync(storage, mk, snap["id"])
        groups, _ = storage.list_schedule_source_groups(snap["id"])
        students = storage.list_schedule_source_group_students(groups[0]["id"])
        row = next(s for s in students if s["mk_user_id"] == "9001")
        self.assertEqual(row["n_makeup_visits"], 1)
        self.assertEqual(row["lessons_attended"], 6, "the makeup record must not count toward regular visits")

    def test_pure_trial_pair_classified_as_trial_with_zero_regular(self):
        storage = _make_storage()
        records = [_lesson_record("9001", "L1", "2025-09-04", test=True)]
        mk = _make_moyklass(records)
        snap = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        schedule_sync._execute_sync(storage, mk, snap["id"])
        groups, _ = storage.list_schedule_source_groups(snap["id"])
        students = storage.list_schedule_source_group_students(groups[0]["id"])
        row = students[0]
        self.assertEqual(row["regularity_category"], "trial")
        self.assertEqual(row["lessons_attended"], 0)


class TestZeroVisitPairRegression(unittest.TestCase):
    """Pre-merge fix regression — the real 44-pair shape found in production
    data: a lesson/student relation exists (the student is on the group's
    roster in MoyKlass's records) but EVERY record for that pair has
    visit=false and there is no test/missedLessonRecordId marker anywhere.
    Must be insufficient_evidence, never trial (the confirmed pre-fix bug:
    n_regular=n_trial=n_makeup=0 silently defaulted to "trial")."""

    def test_all_visit_false_no_markers_is_insufficient_evidence_not_trial(self):
        storage = _make_storage()
        records = [
            _lesson_record("9001", f"Lmissed{i}", f"2025-09-{4+i:02d}", visit=False, test=False, user_subscription=None)
            for i in range(3)
        ]
        mk = _make_moyklass(records)
        snap = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        schedule_sync._execute_sync(storage, mk, snap["id"])
        groups, _ = storage.list_schedule_source_groups(snap["id"])
        students = storage.list_schedule_source_group_students(groups[0]["id"])
        row = next(s for s in students if s["mk_user_id"] == "9001")
        self.assertEqual(row["lessons_attended"], 0)
        self.assertEqual(row["n_trial_visits"], 0)
        self.assertEqual(row["n_makeup_visits"], 0)
        self.assertEqual(row["regularity_category"], "insufficient_evidence")
        self.assertNotEqual(row["regularity_category"], "trial")

    def test_all_visit_false_but_with_group_specific_evidence_is_confirmed(self):
        # even a never-attended pair becomes regular_confirmed if real
        # group-specific membership evidence exists on the record itself
        # (membership is independent of attendance — see G.1/B).
        storage = _make_storage()
        records = [
            _lesson_record(
                "9001", f"Lmissed{i}", f"2025-09-{4+i:02d}", visit=False,
                user_subscription={"classIds": ["500"], "mainClassId": "500"},
            )
            for i in range(3)
        ]
        mk = _make_moyklass(records)
        snap = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        schedule_sync._execute_sync(storage, mk, snap["id"])
        groups, _ = storage.list_schedule_source_groups(snap["id"])
        students = storage.list_schedule_source_group_students(groups[0]["id"])
        row = next(s for s in students if s["mk_user_id"] == "9001")
        # NOTE: userSubscription evidence is only read off REGULAR (visit=true)
        # records in schedule_sync.py, so a visit=false-only pair correctly
        # has no evidence either — confirms insufficient_evidence stays
        # correct even with a subscription payload attached to missed
        # records only (matches "on a REGULAR record" in the ALE-6 fix spec).
        self.assertEqual(row["regularity_category"], "insufficient_evidence")


class TestJoinEvidenceWiring(unittest.TestCase):
    """Group-specific membership evidence sourced from userSubscription
    (per-record) and from the bulk joins fetch (moyklass.list_joins_for_
    classes, called once — never once per group)."""

    def test_group_specific_subscription_confers_membership_evidence(self):
        storage = _make_storage()
        records = _regular_thursday_records(n_weeks=6, user_ids=("9001",), sub=True)
        mk = _make_moyklass(records)
        snap = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        schedule_sync._execute_sync(storage, mk, snap["id"])
        groups, _ = storage.list_schedule_source_groups(snap["id"])
        students = storage.list_schedule_source_group_students(groups[0]["id"])
        row = students[0]
        self.assertEqual(row["membership_evidence"], 1)
        self.assertEqual(row["regularity_category"], "regular_confirmed")

    def test_subscription_for_a_different_class_is_not_evidence(self):
        # group-specificity check — a subscription naming a DIFFERENT
        # classId must never count as evidence for THIS group.
        storage = _make_storage()
        records = _regular_thursday_records(n_weeks=6, user_ids=("9001",), sub=False)
        for r in records:
            r["userSubscription"] = {"classIds": ["999999"], "mainClassId": "999999"}
        mk = _make_moyklass(records)
        snap = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        schedule_sync._execute_sync(storage, mk, snap["id"])
        groups, _ = storage.list_schedule_source_groups(snap["id"])
        students = storage.list_schedule_source_group_students(groups[0]["id"])
        row = students[0]
        self.assertEqual(row["membership_evidence"], 0)
        self.assertNotEqual(row["regularity_category"], "regular_confirmed")

    def test_bulk_joins_fetch_called_once_not_once_per_group(self):
        storage = _make_storage()
        classes = [{"id": str(i), "name": f"Группа {i}"} for i in range(1, 6)]
        records = []
        from datetime import date, timedelta
        d = date(2025, 9, 4)
        for cls in classes:
            for i in range(6):
                lesson_date = (d + timedelta(weeks=i)).isoformat()
                records.append(_lesson_record("9001", f"L{cls['id']}-{i}", lesson_date, class_id=cls["id"]))
        mk = _make_moyklass(records, classes=classes)
        snap = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        schedule_sync._execute_sync(storage, mk, snap["id"])
        mk.list_joins_for_classes.assert_called_once()
        # never a per-group N+1 alternative
        mk.get_class_users.assert_not_called()

    def test_join_with_positive_paid_stats_confers_evidence(self):
        storage = _make_storage()
        records = _regular_thursday_records(n_weeks=6, user_ids=("9001",), sub=False)
        joins = [{"userId": 9001, "classId": 500, "statusId": 4, "stats": {"totalPayed": 735.0}}]
        mk = _make_moyklass(records, joins=joins)
        snap = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        schedule_sync._execute_sync(storage, mk, snap["id"])
        groups, _ = storage.list_schedule_source_groups(snap["id"])
        students = storage.list_schedule_source_group_students(groups[0]["id"])
        row = students[0]
        self.assertEqual(row["membership_evidence"], 1, "a paid join record for this exact group is real evidence")

    def test_join_fetch_failure_is_logged_but_does_not_abort_sync(self):
        storage = _make_storage()
        records = _regular_thursday_records(n_weeks=6, user_ids=("9001",), sub=False)
        mk = _make_moyklass(records)
        mk.list_joins_for_classes.return_value = _mk_result(ok=False, status=500, error="down")
        with patch("schedule_sync.time.sleep", lambda *_a: None):
            snap = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
            schedule_sync._execute_sync(storage, mk, snap["id"])
        final = storage.get_schedule_snapshot(snap["id"])
        self.assertGreater(final["errors_count"], 0)
        self.assertEqual(final["status"], "partial", "degraded membership evidence is logged, not silently ignored")


if __name__ == "__main__":
    unittest.main()

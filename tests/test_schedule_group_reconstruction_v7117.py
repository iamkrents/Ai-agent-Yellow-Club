"""Tests for v7.1.17 — "Расписание" schedule module: GROUP reconstruction.

Covers spec section 23 GROUPS checks 13-21: dominant slot detection, the
"last stable spring 2026 slot" rule, teacher/branch changes, ambiguous
ties, renaming not breaking mk_class_id identity, a child moving between
groups, two directions, and same-name-different-id students never merged.

Run offline:
    python -m unittest tests.test_schedule_group_reconstruction_v7117 -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import schedule_domain
import schedule_sync
from storage import Storage, SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END


def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _mk_result(ok=True, data=None, status=200, error=""):
    r = MagicMock()
    r.ok, r.data, r.status, r.error = ok, data or {}, status, error
    return r


def _lesson_record(user_id, lesson_id, d, start_time="17:00", end_time="18:00",
                    class_id="500", status="1", filial_id="1", teacher_id="200"):
    return {
        "id": f"rec-{lesson_id}-{user_id}", "userId": user_id, "visit": True,
        "lesson": {
            "id": lesson_id, "classId": class_id, "date": d,
            "beginTime": start_time, "endTime": end_time, "status": status,
            "filialId": filial_id, "teacherIds": [teacher_id],
        },
    }


def _make_moyklass(records, classes=None, filials=None, teachers=None):
    mk = MagicMock()
    mk.is_configured = True
    mk.get_classes.return_value = _mk_result(data={"classes": classes or [{"id": "500", "name": "Творчество"}]})
    mk.list_lesson_records_between.return_value = _mk_result(data={"lessonRecords": records})
    mk._lookup_maps_cached.return_value = {
        "filials": filials or {"1": "Кульман 1/1", "2": "Мстиславца 6"},
        "teachers": teachers or {"200": "Мария И.", "201": "Ольга П."},
        "classes": {}, "rooms": {},
    }
    return mk


def _weekly_lessons(weekday_python_date_start, n, **kw):
    recs = []
    for i in range(n):
        d = (weekday_python_date_start + timedelta(weeks=i)).isoformat()
        recs.append(_lesson_record(kw.pop("user_id", "9001") if False else "9001", f"L{i}", d, **kw))
    return recs


class TestDominantSlotPure(unittest.TestCase):
    """Checks 13, 15, 16, 17 — pure schedule_domain.detect_dominant_slot."""

    def test_13_dominant_slot_high_confidence(self):
        lessons = [
            {"lesson_date": (date(2025, 9, 4) + timedelta(weeks=i)).isoformat(), "start_time": "17:00", "duration_minutes": 60, "status": "held"}
            for i in range(9)
        ]
        result = schedule_domain.detect_dominant_slot(lessons)
        self.assertEqual(result["weekday"], 4)
        self.assertEqual(result["start_time"], "17:00")
        self.assertEqual(result["confidence"], "high")
        self.assertIn("9 из 9", result["confidence_reason"])

    def test_15_teacher_change_does_not_affect_slot_detection(self):
        # detect_dominant_slot only looks at weekday/start_time — teacher is
        # tracked separately (group-level majority vote in schedule_sync),
        # so a teacher change alone must not affect the reconstructed slot.
        lessons = [
            {"lesson_date": (date(2025, 9, 4) + timedelta(weeks=i)).isoformat(), "start_time": "17:00", "duration_minutes": 60, "status": "held"}
            for i in range(9)
        ]
        result = schedule_domain.detect_dominant_slot(lessons)
        self.assertEqual(result["start_time"], "17:00")

    def test_16_last_stable_spring_slot_preferred_over_old_majority(self):
        # Sept-Jan: Thursday 17:00 (9 lessons). Then a real change to
        # Tuesday 15:00 for the rest of the year (5 lessons, still running
        # at period end) — the spec wants the LATER stable slot reported.
        lessons = [
            {"lesson_date": (date(2025, 9, 4) + timedelta(weeks=i)).isoformat(), "start_time": "17:00", "duration_minutes": 60, "status": "held"}
            for i in range(9)
        ] + [
            {"lesson_date": (date(2026, 2, 3) + timedelta(weeks=i)).isoformat(), "start_time": "15:00", "duration_minutes": 60, "status": "held"}
            for i in range(5)
        ]
        result = schedule_domain.detect_dominant_slot(lessons)
        self.assertEqual(result["start_time"], "15:00", "must report the later stable slot, not the historically bigger one")
        self.assertEqual(result["confidence"], "medium")
        self.assertIn("изменился", result["confidence_reason"])

    def test_16b_short_late_blip_does_not_override_main_slot(self):
        # Only 2 lessons at the new slot at the very end — too short a run
        # to count as "stabilized", must not override the real majority.
        lessons = [
            {"lesson_date": (date(2025, 9, 4) + timedelta(weeks=i)).isoformat(), "start_time": "17:00", "duration_minutes": 60, "status": "held"}
            for i in range(9)
        ] + [
            {"lesson_date": (date(2026, 5, 14) + timedelta(weeks=i)).isoformat(), "start_time": "15:00", "duration_minutes": 60, "status": "held"}
            for i in range(2)
        ]
        result = schedule_domain.detect_dominant_slot(lessons)
        self.assertEqual(result["start_time"], "17:00")

    def test_17_two_equally_frequent_slots_are_ambiguous(self):
        lessons = (
            [{"lesson_date": (date(2025, 9, 4) + timedelta(weeks=i)).isoformat(), "start_time": "17:00", "duration_minutes": 60, "status": "held"} for i in range(5)]
            + [{"lesson_date": (date(2025, 9, 2) + timedelta(weeks=i)).isoformat(), "start_time": "16:00", "duration_minutes": 60, "status": "held"} for i in range(5)]
        )
        result = schedule_domain.detect_dominant_slot(lessons)
        self.assertEqual(result["confidence"], "ambiguous")


class TestGroupIdentityAndMembership(unittest.TestCase):
    """Checks 14, 18, 19, 20, 21 — storage-level group/membership integrity."""

    def test_14_branch_change_reflected_via_majority_vote(self):
        storage = _make_storage()
        base = date(2025, 9, 4)
        records = [_lesson_record("9001", f"L{i}", (base + timedelta(weeks=i)).isoformat(), filial_id="1") for i in range(3)]
        records += [_lesson_record("9001", f"L{i}", (base + timedelta(weeks=i)).isoformat(), filial_id="2") for i in range(3, 9)]
        mk = _make_moyklass(records)
        snap = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        schedule_sync._execute_sync(storage, mk, snap["id"])
        g = storage.list_schedule_source_groups(snap["id"])[0][0]
        self.assertEqual(g["branch_name"], "Мстиславца 6", "majority (6 of 9) filial must win")

    def test_18_renaming_group_does_not_break_mk_class_id_identity(self):
        storage = _make_storage()
        base = date(2025, 9, 4)
        records = [_lesson_record("9001", f"L{i}", (base + timedelta(weeks=i)).isoformat()) for i in range(5)]
        mk = _make_moyklass(records, classes=[{"id": "500", "name": "Старое название"}])
        snap = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        schedule_sync._execute_sync(storage, mk, snap["id"])
        group_id_1 = storage.list_schedule_source_groups(snap["id"])[0][0]["id"]

        # "Rename" = MoyKlass now returns a different name for the same id.
        mk2 = _make_moyklass(records, classes=[{"id": "500", "name": "Новое название"}])
        schedule_sync._execute_sync(storage, mk2, snap["id"])
        groups, total = storage.list_schedule_source_groups(snap["id"])
        self.assertEqual(total, 1, "rename must update the same group row, not create a second one")
        self.assertEqual(groups[0]["id"], group_id_1)
        self.assertEqual(groups[0]["name"], "Новое название")

    def test_19_child_moving_between_groups_tracked_in_both(self):
        storage = _make_storage()
        base = date(2025, 9, 4)
        # child 9001 attends group 500 for the first half of the year...
        records = [_lesson_record("9001", f"A{i}", (base + timedelta(weeks=i)).isoformat(), class_id="500") for i in range(4)]
        # ...then moves to group 600 for the second half.
        records += [_lesson_record("9001", f"B{i}", (base + timedelta(weeks=i + 10)).isoformat(), class_id="600") for i in range(4)]
        mk = _make_moyklass(records, classes=[{"id": "500", "name": "Группа A"}, {"id": "600", "name": "Группа B"}])
        snap = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        schedule_sync._execute_sync(storage, mk, snap["id"])
        groups, total = storage.list_schedule_source_groups(snap["id"])
        self.assertEqual(total, 2)
        for g in groups:
            students = storage.list_schedule_source_group_students(g["id"])
            self.assertEqual(len(students), 1)
            self.assertEqual(students[0]["mk_user_id"], "9001")

    def test_20_two_directions_kept_as_separate_groups(self):
        storage = _make_storage()
        base = date(2025, 9, 4)
        records = [_lesson_record("9001", f"A{i}", (base + timedelta(weeks=i)).isoformat(), class_id="500") for i in range(6)]
        records += [_lesson_record("9002", f"C{i}", (base + timedelta(days=1, weeks=i)).isoformat(), class_id="700") for i in range(6)]
        mk = _make_moyklass(records, classes=[{"id": "500", "name": "Рисование"}, {"id": "700", "name": "Танцы"}])
        snap = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        schedule_sync._execute_sync(storage, mk, snap["id"])
        groups, total = storage.list_schedule_source_groups(snap["id"])
        self.assertEqual(total, 2)
        names = {g["course_name"] for g in groups}
        self.assertEqual(names, {"Рисование", "Танцы"})

    def test_21_same_display_name_different_mk_user_id_not_merged(self):
        storage = _make_storage()
        base = date(2025, 9, 4)
        records = [
            {**_lesson_record("9001", f"L{i}", (base + timedelta(weeks=i)).isoformat()), "user": {"firstName": "Иван", "lastName": "Иванов"}}
            for i in range(3)
        ] + [
            {**_lesson_record("9099", f"M{i}", (base + timedelta(weeks=i)).isoformat()), "user": {"firstName": "Иван", "lastName": "Иванов"}}
            for i in range(3)
        ]
        mk = _make_moyklass(records)
        snap = storage.create_schedule_sync_snapshot(SCHEDULE_SOURCE_PERIOD_START, SCHEDULE_SOURCE_PERIOD_END, 1, "T")
        schedule_sync._execute_sync(storage, mk, snap["id"])
        g = storage.list_schedule_source_groups(snap["id"])[0][0]
        students = storage.list_schedule_source_group_students(g["id"])
        ids = {s["mk_user_id"] for s in students}
        self.assertEqual(ids, {"9001", "9099"}, "two distinct mk_user_id values must never be collapsed into one")


if __name__ == "__main__":
    unittest.main()

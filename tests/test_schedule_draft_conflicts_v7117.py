"""Tests for v7.1.17 — "Расписание" schedule module: DRAFT editing & conflicts.

Covers spec section 23 DRAFTS checks 42-46, 48-50: manual include/exclude,
audit log entries, optimistic version conflicts, child-in-two-drafts and
teacher-in-two-drafts conflict detection, double-submit safety (backend
idempotency via version bump), and archived drafts being genuinely final
(no further edits possible, matching the required archive confirmation).

Run offline:
    python -m unittest tests.test_schedule_draft_conflicts_v7117 -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage


def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _seed_draft(storage: Storage, mk_class_id, mk_user_ids, weekday=4, start_time="17:00",
                 teacher_mk_id="200", branch="Кульман 1/1") -> dict:
    snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
    group_id = storage.upsert_schedule_source_group(
        snap["id"], mk_class_id, name=f"Группа {mk_class_id}", branch_name=branch,
        course_name=f"Группа {mk_class_id}", teacher_mk_id=teacher_mk_id, teacher_name="Преподаватель",
    )
    storage.finalize_schedule_source_group_slot(
        group_id, weekday=weekday, start_time=start_time, duration_minutes=60, confidence="high",
        confidence_reason="test", lessons_count=9, lessons_considered=9,
        first_lesson_date="2025-09-04", last_lesson_date="2025-11-06", students_count=len(mk_user_ids),
    )
    for uid in mk_user_ids:
        storage.upsert_schedule_source_group_student(snap["id"], group_id, uid, child_display_name=f"Ребёнок {uid}", lessons_attended=9, evidence_source="attendance", confidence="high")
    storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)
    storage.generate_schedule_draft_foundation(snap["id"], 1, "T", "Кульман 1/1", "Мстиславца 6")
    return storage.list_schedule_drafts(source_snapshot_id=snap["id"])[0][0]


class TestManualIncludeExclude(unittest.TestCase):
    def test_42_exclude_then_include_member(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        r = storage.exclude_schedule_draft_member(draft["id"], "9001", 1, "T")
        self.assertTrue(r["ok"])
        member = storage.list_schedule_draft_members(draft["id"])[0]
        self.assertEqual(member["manually_excluded"], 1)

        r2 = storage.include_schedule_draft_member(draft["id"], "9001", "Ребёнок 9001", None, 1, "T", "Кульман 1/1", "Мстиславца 6")
        self.assertTrue(r2["ok"])
        member2 = storage.list_schedule_draft_members(draft["id"])[0]
        self.assertEqual(member2["manually_excluded"], 0)

    def test_42b_include_brand_new_child_not_in_source_roster(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        r = storage.include_schedule_draft_member(draft["id"], "9999", "Добавленный вручную", None, 1, "T", "Кульман 1/1", "Мстиславца 6")
        self.assertTrue(r["ok"])
        ids = {m["mk_user_id"] for m in storage.list_schedule_draft_members(draft["id"])}
        self.assertIn("9999", ids)
        added = [m for m in storage.list_schedule_draft_members(draft["id"]) if m["mk_user_id"] == "9999"][0]
        self.assertEqual(added["manually_included"], 1)


class TestAuditLog(unittest.TestCase):
    def test_43_audit_log_records_creation_and_edits(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        storage.update_schedule_draft_fields(draft["id"], 1, "T", draft["version"], {"name": "Новое имя"}, "Кульман 1/1", "Мстиславца 6")
        storage.exclude_schedule_draft_member(draft["id"], "9001", 1, "T")
        storage.set_schedule_draft_status(draft["id"], 1, "T", draft["version"] + 1, "needs_review")

        audit = storage.list_schedule_draft_audit_log(draft["id"], limit=50)
        actions = {a["action"] for a in audit}
        self.assertIn("created", actions)
        self.assertIn("field_changed", actions)
        self.assertIn("member_excluded", actions)
        self.assertIn("status_changed", actions)


class TestOptimisticVersioning(unittest.TestCase):
    def test_44_stale_version_rejected(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        ok1 = storage.update_schedule_draft_fields(draft["id"], 1, "T", draft["version"], {"name": "A"}, "Кульман 1/1", "Мстиславца 6")
        self.assertTrue(ok1["ok"])
        stale = storage.update_schedule_draft_fields(draft["id"], 1, "T", draft["version"], {"name": "B"}, "Кульман 1/1", "Мстиславца 6")
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["reason_code"], "version_conflict")
        # the first, accepted edit must not have been clobbered
        self.assertEqual(storage.get_schedule_draft(draft["id"])["name"], "A")


class TestCrossDraftConflicts(unittest.TestCase):
    def test_45_child_in_two_drafts_overlapping_time(self):
        storage = _make_storage()
        draft_a = _seed_draft(storage, "500", ["9001"], weekday=4, start_time="17:00", teacher_mk_id="200")
        draft_b = _seed_draft(storage, "600", ["9001"], weekday=4, start_time="17:30", teacher_mk_id="201")
        conflicts = storage.get_schedule_draft_conflicts(draft_a["id"])
        self.assertEqual(len(conflicts["child_conflicts"]), 1)
        self.assertEqual(conflicts["child_conflicts"][0]["mk_user_id"], "9001")
        self.assertEqual(conflicts["child_conflicts"][0]["other_draft_id"], draft_b["id"])

    def test_45b_child_in_two_drafts_non_overlapping_time_no_conflict(self):
        storage = _make_storage()
        draft_a = _seed_draft(storage, "500", ["9001"], weekday=4, start_time="17:00")
        _draft_b = _seed_draft(storage, "600", ["9001"], weekday=2, start_time="10:00")
        conflicts = storage.get_schedule_draft_conflicts(draft_a["id"])
        self.assertEqual(conflicts["child_conflicts"], [])

    def test_46_teacher_double_booked_across_drafts(self):
        storage = _make_storage()
        draft_a = _seed_draft(storage, "500", ["9001"], weekday=3, start_time="10:00", teacher_mk_id="200")
        draft_b = _seed_draft(storage, "600", ["9002"], weekday=3, start_time="10:15", teacher_mk_id="200")
        conflicts = storage.get_schedule_draft_conflicts(draft_a["id"])
        self.assertEqual(len(conflicts["teacher_conflict_drafts"]), 1)
        self.assertEqual(conflicts["teacher_conflict_drafts"][0]["other_draft_id"], draft_b["id"])

    def test_46b_excluded_child_not_counted_toward_conflicts(self):
        storage = _make_storage()
        draft_a = _seed_draft(storage, "500", ["9001"], weekday=4, start_time="17:00")
        draft_b = _seed_draft(storage, "600", ["9001"], weekday=4, start_time="17:15")
        storage.exclude_schedule_draft_member(draft_a["id"], "9001", 1, "T")
        conflicts = storage.get_schedule_draft_conflicts(draft_a["id"])
        self.assertEqual(conflicts["child_conflicts"], [], "an excluded member must not trigger a conflict")


class TestDoubleSubmitAndArchive(unittest.TestCase):
    """Checks 49, 50 — backend-observable half of the double-submit guard
    (version bump makes a repeat identical request harmless) and the
    finality of archiving (frontend confirms; backend enforces it's real)."""

    def test_49_repeat_identical_update_is_safe_via_version_bump(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        r1 = storage.update_schedule_draft_fields(draft["id"], 1, "T", draft["version"], {"name": "X"}, "Кульман 1/1", "Мстиславца 6")
        self.assertTrue(r1["ok"])
        # a naive double-submit retry with the OLD version must fail closed,
        # not silently apply twice
        r2 = storage.update_schedule_draft_fields(draft["id"], 1, "T", draft["version"], {"name": "X"}, "Кульман 1/1", "Мстиславца 6")
        self.assertFalse(r2["ok"])
        self.assertEqual(r2["reason_code"], "version_conflict")

    def test_50_archived_draft_rejects_further_edits(self):
        storage = _make_storage()
        draft = _seed_draft(storage, "500", ["9001"])
        r = storage.set_schedule_draft_status(draft["id"], 1, "T", draft["version"], "archived")
        self.assertTrue(r["ok"])
        blocked = storage.update_schedule_draft_fields(draft["id"], 1, "T", r["draft"]["version"], {"name": "X"}, "Кульман 1/1", "Мстиславца 6")
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["reason_code"], "archived")


if __name__ == "__main__":
    unittest.main()

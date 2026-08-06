"""Tests for v7.1.17 — "Расписание" schedule module: DRAFT generation.

Covers spec section 23 DRAFTS checks 39, 40, 41, 47: exactly one draft per
source group, idempotent regeneration (no duplicates, existing manual
edits untouched), slot-change recomputes member availability match, and
editing a draft never mutates its source snapshot.

Run offline:
    python -m unittest tests.test_schedule_draft_generation_v7117 -v
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


def _seed_snapshot_with_group(storage: Storage, weekday=4, start_time="17:00", branch="Кульман 1/1") -> tuple[dict, int]:
    snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
    group_id = storage.upsert_schedule_source_group(snap["id"], "500", name="Группа", branch_name=branch, course_name="Группа", teacher_mk_id="200", teacher_name="Мария И.")
    storage.finalize_schedule_source_group_slot(
        group_id, weekday=weekday, start_time=start_time, duration_minutes=60, confidence="high",
        confidence_reason="test", lessons_count=9, lessons_considered=9,
        first_lesson_date="2025-09-04", last_lesson_date="2025-11-06", students_count=1,
    )
    storage.upsert_schedule_source_group_student(snap["id"], group_id, "9001", child_display_name="Иван", lessons_attended=9, evidence_source="attendance", confidence="high")
    storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)
    return storage.get_schedule_snapshot(snap["id"]), group_id


class TestDraftGenerationBasics(unittest.TestCase):
    def test_39_one_draft_per_source_group(self):
        storage = _make_storage()
        snap, group_id = _seed_snapshot_with_group(storage)
        storage.generate_schedule_draft_foundation(snap["id"], 1, "T", "Кульман 1/1", "Мстиславца 6")
        drafts, total = storage.list_schedule_drafts(source_snapshot_id=snap["id"])
        self.assertEqual(total, 1)
        self.assertEqual(drafts[0]["source_group_id"], group_id)

    def test_40_regeneration_creates_no_duplicate(self):
        storage = _make_storage()
        snap, group_id = _seed_snapshot_with_group(storage)
        r1 = storage.generate_schedule_draft_foundation(snap["id"], 1, "T", "Кульман 1/1", "Мстиславца 6")
        self.assertEqual(r1["drafts_created"], 1)
        draft_id = storage.list_schedule_drafts(source_snapshot_id=snap["id"])[0][0]["id"]
        # staff manually edits the draft before regenerating
        storage.update_schedule_draft_fields(draft_id, 1, "T", 1, {"name": "Моё название"}, "Кульман 1/1", "Мстиславца 6")

        r2 = storage.generate_schedule_draft_foundation(snap["id"], 1, "T", "Кульман 1/1", "Мстиславца 6")
        self.assertEqual(r2["drafts_created"], 0)
        self.assertEqual(r2["drafts_already_existed"], 1)
        drafts, total = storage.list_schedule_drafts(source_snapshot_id=snap["id"])
        self.assertEqual(total, 1, "regeneration must never create a duplicate draft")
        self.assertEqual(drafts[0]["name"], "Моё название", "existing manual edit must survive regeneration untouched")

    def test_41_slot_change_recomputes_member_match(self):
        storage = _make_storage()
        snap, group_id = _seed_snapshot_with_group(storage, weekday=4, start_time="17:00")
        storage.generate_schedule_draft_foundation(snap["id"], 1, "T", "Кульман 1/1", "Мстиславца 6")
        draft = storage.list_schedule_drafts(source_snapshot_id=snap["id"])[0][0]
        member_before = storage.list_schedule_draft_members(draft["id"])[0]
        # No onboarding record at all yet -> continuation unconfirmed, which
        # short-circuits availability matching before any interval check.
        self.assertEqual(member_before["continuation_status"], "unconfirmed")
        self.assertEqual(member_before["availability_match"], "continuation_unconfirmed")

        rec = storage.find_onboarding_recipients_by_mk_user("9001")
        self.assertEqual(rec, [])  # sanity: nothing seeded yet for this student

        campaign = storage.create_onboarding_campaign("T", "2025-2026", "1")["campaign"]
        storage.import_onboarding_campaign_recipients(campaign["id"], [{"mk_user_id": "9001", "child_display_name": "Иван"}], "1")
        recipient = storage.find_onboarding_recipients_by_mk_user("9001")[0]
        storage.update_recipient_continuation_status(recipient["id"], "continues", "1", "owner")
        storage.submit_schedule_availability(
            recipient["id"], "1", "owner", preferred_branch="YC1",
            intervals=[{"weekday": 3, "start_time": "10:00", "end_time": "11:00", "preference": "preferred"}],
        )

        # Change the draft's day to Wednesday 10:00 — should now match.
        result = storage.update_schedule_draft_fields(
            draft["id"], 1, "T", draft["version"], {"weekday": 3, "start_time": "10:00"}, "Кульман 1/1", "Мстиславца 6",
        )
        self.assertTrue(result["ok"])
        self.assertTrue(result["recomputed"])
        member_after = storage.list_schedule_draft_members(result["draft"]["id"] if False else draft["id"])
        member_after = [m for m in storage.list_schedule_draft_members(draft["id"]) if m["mk_user_id"] == "9001"][0]
        self.assertEqual(member_after["continuation_status"], "continues")
        self.assertEqual(member_after["availability_match"], "preferred_match")

    def test_47_editing_draft_never_mutates_source_snapshot(self):
        storage = _make_storage()
        snap, group_id = _seed_snapshot_with_group(storage)
        before_group = storage.get_schedule_source_group(group_id)
        storage.generate_schedule_draft_foundation(snap["id"], 1, "T", "Кульман 1/1", "Мстиславца 6")
        draft = storage.list_schedule_drafts(source_snapshot_id=snap["id"])[0][0]
        storage.update_schedule_draft_fields(
            draft["id"], 1, "T", draft["version"], {"weekday": 2, "start_time": "09:00", "name": "Другое"}, "Кульман 1/1", "Мстиславца 6",
        )
        after_group = storage.get_schedule_source_group(group_id)
        self.assertEqual(before_group, after_group, "source group snapshot row must be byte-identical after draft edits")
        after_snapshot = storage.get_schedule_snapshot(snap["id"])
        self.assertEqual(after_snapshot["status"], "completed")
        self.assertEqual(after_snapshot["is_active"], 1)


if __name__ == "__main__":
    unittest.main()

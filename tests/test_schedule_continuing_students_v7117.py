"""Tests for v7.1.17 — "Расписание" schedule module: CONTINUATION status.

Covers spec section 23 CONTINUATION checks 22-27: continues/discontinued/
unconfirmed resolution, "explicit refusal beats availability" priority,
discontinued students excluded from generated drafts, and independent
resolution for several children of one parent.

Run offline:
    python -m unittest tests.test_schedule_continuing_students_v7117 -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import schedule_domain
from storage import Storage


def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _seed_recipient(storage: Storage, mk_user_id: str, child_name: str = "Ребёнок") -> dict:
    campaign = storage.create_onboarding_campaign("Test Campaign", "2025-2026", "1")["campaign"]
    storage.import_onboarding_campaign_recipients(campaign["id"], [{"mk_user_id": mk_user_id, "child_display_name": child_name}], "1")
    return storage.find_onboarding_recipients_by_mk_user(mk_user_id)[0]


class TestContinuationResolutionPure(unittest.TestCase):
    """Pure schedule_domain.resolve_continuation — priority rules."""

    def test_22_continues(self):
        self.assertEqual(schedule_domain.resolve_continuation(["continues"])["status"], "continues")

    def test_23_not_continuing_maps_to_discontinued(self):
        self.assertEqual(schedule_domain.resolve_continuation(["not_continuing"])["status"], "discontinued")

    def test_24_no_data_is_unconfirmed(self):
        self.assertEqual(schedule_domain.resolve_continuation([])["status"], "unconfirmed")
        self.assertEqual(schedule_domain.resolve_continuation(["unknown"])["status"], "unconfirmed")

    def test_24b_pending_status_is_unconfirmed(self):
        self.assertEqual(schedule_domain.resolve_continuation(["undecided"])["status"], "unconfirmed")
        self.assertEqual(schedule_domain.resolve_continuation(["needs_consultation"])["status"], "unconfirmed")

    def test_conflicting_signals_are_ambiguous_not_guessed(self):
        self.assertEqual(schedule_domain.resolve_continuation(["continues", "undecided"])["status"], "ambiguous")

    def test_refusal_always_wins_even_with_continues_elsewhere(self):
        self.assertEqual(schedule_domain.resolve_continuation(["continues", "not_continuing"])["status"], "discontinued")


class TestAvailabilityNeverOverridesRefusal(unittest.TestCase):
    """Check 25 — a filled-in Availability must never upgrade a discontinued
    or unconfirmed student into a real match."""

    def test_25_discontinued_short_circuits_before_interval_matching(self):
        match = schedule_domain.match_availability(
            "discontinued", weekday=4, start_time="17:00", duration_minutes=60,
            group_branch_code="YC1", intervals=[{"weekday": 4, "start_time": "16:00", "end_time": "19:00", "preference": "preferred"}],
            preferred_branch="YC1",
        )
        self.assertEqual(match["match"], "discontinued")

    def test_25b_unconfirmed_short_circuits_before_interval_matching(self):
        match = schedule_domain.match_availability(
            "unconfirmed", weekday=4, start_time="17:00", duration_minutes=60,
            group_branch_code="YC1", intervals=[{"weekday": 4, "start_time": "16:00", "end_time": "19:00", "preference": "preferred"}],
            preferred_branch="YC1",
        )
        self.assertEqual(match["match"], "continuation_unconfirmed")

    def test_25c_end_to_end_via_resolve_schedule_student_status(self):
        storage = _make_storage()
        rec = _seed_recipient(storage, "9001")
        storage.update_recipient_continuation_status(rec["id"], "not_continuing", "1", "owner")
        storage.submit_schedule_availability(
            rec["id"], "1", "owner", preferred_branch="either", available_from="2025-08-01",
            intervals=[{"weekday": 4, "start_time": "16:00", "end_time": "19:00", "preference": "preferred"}],
        )
        status = storage.resolve_schedule_student_status(
            "9001", weekday=4, start_time="17:00", duration_minutes=60, group_branch_code="YC1",
        )
        self.assertEqual(status["continuation_status"], "discontinued")
        self.assertEqual(status["availability_match"], "discontinued")


class TestDiscontinuedExcludedFromDraft(unittest.TestCase):
    """Check 26 — discontinued students are never auto-added to a draft."""

    def test_26_discontinued_not_in_generated_draft(self):
        storage = _make_storage()
        now = "2025-09-01T00:00:00"
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = storage.upsert_schedule_source_group(snap["id"], "500", name="Группа", branch_name="Кульман 1/1", course_name="Группа")
        storage.finalize_schedule_source_group_slot(
            group_id, weekday=4, start_time="17:00", duration_minutes=60, confidence="high",
            confidence_reason="test", lessons_count=9, lessons_considered=9,
            first_lesson_date="2025-09-04", last_lesson_date="2025-11-06", students_count=2,
        )
        storage.upsert_schedule_source_group_student(snap["id"], group_id, "9001", child_display_name="Продолжает", lessons_attended=9, evidence_source="attendance", confidence="high")
        storage.upsert_schedule_source_group_student(snap["id"], group_id, "9002", child_display_name="Прекратил", lessons_attended=9, evidence_source="attendance", confidence="high")
        rec2 = _seed_recipient(storage, "9002", "Прекратил")
        storage.update_recipient_continuation_status(rec2["id"], "not_continuing", "1", "owner")
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)

        result = storage.generate_schedule_draft_foundation(snap["id"], 1, "Tester", "Кульман 1/1", "Мстиславца 6")
        self.assertEqual(result["members_skipped_discontinued"], 1)
        draft = storage.list_schedule_drafts(source_snapshot_id=snap["id"])[0][0]
        members = storage.list_schedule_draft_members(draft["id"])
        mk_ids = {m["mk_user_id"] for m in members}
        self.assertIn("9001", mk_ids)
        self.assertNotIn("9002", mk_ids, "a discontinued student must never be inserted as a draft member")


class TestMultiChildParent(unittest.TestCase):
    """Check 27 — a parent with several children: each child's continuation
    resolves independently of the others."""

    def test_27_siblings_resolve_independently(self):
        storage = _make_storage()
        rec_a = _seed_recipient(storage, "9101", "Ребёнок А")
        rec_b = _seed_recipient(storage, "9102", "Ребёнок Б")
        storage.update_recipient_continuation_status(rec_a["id"], "continues", "1", "owner")
        storage.update_recipient_continuation_status(rec_b["id"], "not_continuing", "1", "owner")

        status_a = storage.resolve_schedule_student_status("9101", weekday=None, start_time=None, duration_minutes=None, group_branch_code="unknown")
        status_b = storage.resolve_schedule_student_status("9102", weekday=None, start_time=None, duration_minutes=None, group_branch_code="unknown")
        self.assertEqual(status_a["continuation_status"], "continues")
        self.assertEqual(status_b["continuation_status"], "discontinued")


if __name__ == "__main__":
    unittest.main()

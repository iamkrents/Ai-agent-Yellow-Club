"""Tests for v7.1.17.1 — ALE-6 sections 6/7: finer-grained continuation
and availability data-quality categories, additive alongside the existing
coarse status/match vocabulary (never renamed/removed — see CLAUDE.md
"понятные, стабильные reason_code... не переименовывать существующие").

continuation detail: continues / discontinued / awaiting_confirmation /
status_not_found / ambiguous_multiple_records.
availability detail: filled / invited_not_filled / no_onboarding_record /
parent_not_connected / ambiguous.

Run offline:
    python -m unittest tests.test_schedule_continuation_availability_detail_v71171 -v
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


class TestContinuationDetailPure(unittest.TestCase):
    def test_no_recipient_at_all_is_status_not_found(self):
        result = schedule_domain.resolve_continuation([], has_any_recipient=False)
        self.assertEqual(result["status"], "unconfirmed", "coarse status unchanged")
        self.assertEqual(result["detail"], "status_not_found")

    def test_recipient_exists_but_unanswered_is_awaiting_confirmation(self):
        result = schedule_domain.resolve_continuation(["unknown"], has_any_recipient=True)
        self.assertEqual(result["status"], "unconfirmed")
        self.assertEqual(result["detail"], "awaiting_confirmation")

    def test_pending_status_is_awaiting_confirmation(self):
        result = schedule_domain.resolve_continuation(["undecided"], has_any_recipient=True)
        self.assertEqual(result["detail"], "awaiting_confirmation")

    def test_continues_detail_matches_status(self):
        result = schedule_domain.resolve_continuation(["continues"], has_any_recipient=True)
        self.assertEqual(result["status"], "continues")
        self.assertEqual(result["detail"], "continues")

    def test_discontinued_detail_matches_status(self):
        result = schedule_domain.resolve_continuation(["not_continuing"], has_any_recipient=True)
        self.assertEqual(result["status"], "discontinued")
        self.assertEqual(result["detail"], "discontinued")

    def test_conflicting_records_is_ambiguous_multiple_records(self):
        result = schedule_domain.resolve_continuation(["continues", "undecided"], has_any_recipient=True)
        self.assertEqual(result["status"], "ambiguous")
        self.assertEqual(result["detail"], "ambiguous_multiple_records")

    def test_default_inference_when_flag_omitted_backward_compatible(self):
        # old call sites that never pass has_any_recipient must still work
        self.assertEqual(schedule_domain.resolve_continuation([])["status"], "unconfirmed")
        self.assertEqual(schedule_domain.resolve_continuation(["continues"])["status"], "continues")


class TestAvailabilityDetailPure(unittest.TestCase):
    def _match(self, **kw):
        defaults = dict(
            continuation_status="continues", weekday=4, start_time="17:00", duration_minutes=60,
            group_branch_code="YC1", intervals=[], preferred_branch="YC1",
        )
        defaults.update(kw)
        return schedule_domain.match_availability(**defaults)

    def test_no_recipient_is_no_onboarding_record(self):
        result = self._match(has_any_recipient=False)
        self.assertEqual(result["detail"], "no_onboarding_record")

    def test_recipient_but_no_parent_link_is_parent_not_connected(self):
        result = self._match(has_any_recipient=True, has_parent_link=False)
        self.assertEqual(result["detail"], "parent_not_connected")

    def test_connected_but_empty_intervals_is_invited_not_filled(self):
        result = self._match(has_any_recipient=True, has_parent_link=True, intervals=[])
        self.assertEqual(result["detail"], "invited_not_filled")

    def test_connected_with_intervals_is_filled(self):
        result = self._match(
            has_any_recipient=True, has_parent_link=True,
            intervals=[{"weekday": 4, "start_time": "16:00", "end_time": "19:00", "preference": "preferred"}],
        )
        self.assertEqual(result["detail"], "filled")
        self.assertEqual(result["match"], "preferred_match", "detail is orthogonal to match quality")

    def test_ambiguous_continuation_forces_ambiguous_detail(self):
        result = self._match(continuation_status="ambiguous", has_any_recipient=True, has_parent_link=True, intervals=[])
        self.assertEqual(result["detail"], "ambiguous")

    def test_detail_omitted_params_do_not_crash_backward_compatible(self):
        result = self._match()
        self.assertIn(result["detail"], schedule_domain.SCHEDULE_AVAILABILITY_DETAILS)


class TestHasActiveParentLink(unittest.TestCase):
    def test_no_link_at_all(self):
        storage = _make_storage()
        self.assertFalse(storage.has_active_parent_link("9001"))

    def test_active_link_found(self):
        storage = _make_storage()
        with storage._connect() as conn:
            conn.execute(
                "INSERT INTO client_parent_child_links "
                "(parent_telegram_user_id, mk_user_id, child_display_name, status, linked_at, created_at, updated_at) "
                "VALUES ('700001', '9001', 'Ребёнок', 'active', '2025-01-01T00:00:00', '2025-01-01T00:00:00', '2025-01-01T00:00:00')"
            )
        self.assertTrue(storage.has_active_parent_link("9001"))

    def test_unlinked_status_does_not_count(self):
        storage = _make_storage()
        with storage._connect() as conn:
            conn.execute(
                "INSERT INTO client_parent_child_links "
                "(parent_telegram_user_id, mk_user_id, child_display_name, status, linked_at, created_at, updated_at) "
                "VALUES ('700001', '9001', 'Ребёнок', 'unlinked', '2025-01-01T00:00:00', '2025-01-01T00:00:00', '2025-01-01T00:00:00')"
            )
        self.assertFalse(storage.has_active_parent_link("9001"))


class TestResolveScheduleStudentStatusEndToEnd(unittest.TestCase):
    def test_unknown_student_gets_status_not_found_and_no_onboarding_record(self):
        storage = _make_storage()
        status = storage.resolve_schedule_student_status(
            "9999999", weekday=4, start_time="17:00", duration_minutes=60, group_branch_code="YC1",
        )
        self.assertEqual(status["continuation_detail"], "status_not_found")
        self.assertEqual(status["availability_detail"], "no_onboarding_record")

    def test_overview_stats_breakdown_reflects_new_detail_categories(self):
        storage = _make_storage()
        now = "2025-09-01T00:00:00"
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = storage.upsert_schedule_source_group(snap["id"], "500", name="Группа", branch_name="Кульман 1/1", course_name="Группа")
        storage.finalize_schedule_source_group_slot(
            group_id, weekday=4, start_time="17:00", duration_minutes=60, confidence="high",
            confidence_reason="test", lessons_count=9, lessons_considered=9,
            first_lesson_date="2025-09-04", last_lesson_date="2025-11-06", students_count=1,
        )
        storage.upsert_schedule_source_group_student(
            snap["id"], group_id, "9001", child_display_name="Ребёнок", lessons_attended=9,
            evidence_source="attendance", confidence="high", regularity_category="regular_confirmed",
            membership_evidence=1, n_trial_visits=0, n_makeup_visits=0,
        )
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)

        stats = storage.get_schedule_overview_stats(snap["id"], "Кульман 1/1", "Мстиславца 6")
        self.assertEqual(stats["regular_confirmed_count"], 1)
        self.assertEqual(stats["raw_unique_student_ids_count"], 1)
        self.assertEqual(stats["continuation_detail_breakdown"].get("status_not_found"), 1)
        self.assertEqual(stats["availability_detail_breakdown"].get("no_onboarding_record"), 1)
        self.assertEqual(stats["excluded_irregular_count"], 0)
        self.assertEqual(stats["insufficient_evidence_count"], 0)
        self.assertEqual(stats["ambiguous_regularity_count"], 0)
        self.assertEqual(stats["sync_errors_count"], 0)


if __name__ == "__main__":
    unittest.main()

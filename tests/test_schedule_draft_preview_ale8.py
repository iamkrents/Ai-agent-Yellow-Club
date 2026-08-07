"""Tests for ALE-8 — first safe draft-planning preview.

Product model (clarified for ALE-8): MoyKlass is historical evidence ONLY
(who attended which group last year, when, with whom). The actual plan for
the NEW academic year comes entirely from the client's own continuation +
Availability answers, layered on top of that historical baseline. This
preview never writes to schedule_drafts/schedule_draft_members and never
calls MoyKlass — see storage.get_schedule_draft_preview's docstring for why
it is deliberately separate from the pre-existing generate_schedule_draft_
foundation() write path.

Two layers tested:
  - schedule_domain.select_historical_baseline_group /
    build_schedule_draft_preview_decision — pure, no I/O.
  - storage.get_schedule_draft_preview — read-only orchestration against a
    temp SQLite DB, same fixture idioms already used by the other
    ALE-6/ALE-6-fix schedule test files (create_schedule_sync_snapshot,
    upsert_schedule_source_group[_student], raw INSERT into
    client_onboarding_recipients/client_schedule_availability).

Run offline:
    python -m unittest tests.test_schedule_draft_preview_ale8 -v
"""
from __future__ import annotations

import contextlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import schedule_domain
from storage import Storage

WEB_APP_SERVER_PY = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
STORAGE_PY = (ROOT / "storage.py").read_text(encoding="utf-8")


def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _seed_recipient(storage: Storage, mk_user_id: str, continuation_status: str, campaign_id: int = 1) -> int:
    # UNIQUE(campaign_id, mk_user_id) — a second conflicting record for the
    # same child (e.g. two onboarding campaigns/years) needs a distinct
    # campaign_id, same as real multi-campaign data would.
    now = "2026-06-01T00:00:00"
    with storage._connect() as conn:
        cur = conn.execute(
            "INSERT INTO client_onboarding_recipients "
            "(campaign_id, mk_user_id, child_display_name, continuation_status, added_by, created_at, updated_at) "
            "VALUES (?, ?, 'Ребёнок', ?, 'test', ?, ?)",
            (campaign_id, mk_user_id, continuation_status, now, now),
        )
        return int(cur.lastrowid)


def _seed_availability(storage: Storage, recipient_id: int, weekday: int, start_time: str, end_time: str, preference: str = "possible") -> None:
    now = "2026-06-01T00:00:00"
    with storage._connect() as conn:
        conn.execute(
            "INSERT INTO client_schedule_availability "
            "(campaign_id, recipient_id, weekday, start_time, end_time, preference, created_at, updated_at) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
            (recipient_id, weekday, start_time, end_time, preference, now, now),
        )


def _seed_group(storage: Storage, snapshot_id: int, mk_class_id: str, *, weekday: int = 4, start_time: str = "17:00",
                 duration_minutes: int = 60, branch_name: str = "Кульман 1/1") -> int:
    group_id = storage.upsert_schedule_source_group(
        snapshot_id, mk_class_id, name=f"Группа {mk_class_id}", branch_name=branch_name, course_name="Курс",
    )
    storage.finalize_schedule_source_group_slot(
        group_id, weekday=weekday, start_time=start_time, duration_minutes=duration_minutes, confidence="high",
        confidence_reason="test", lessons_count=9, lessons_considered=9,
        first_lesson_date="2025-09-04", last_lesson_date="2025-11-06", students_count=1,
    )
    return group_id


# ─────────────────────────────────────────────────────────────────────────
# Pure domain layer
# ─────────────────────────────────────────────────────────────────────────

class TestSelectHistoricalBaselineGroup(unittest.TestCase):
    def test_trial_visitor_never_becomes_baseline(self):
        # rule 8/6 — makeup/trial/one_off/other_group_visitor rows must
        # never become the baseline no matter what is_current_group says.
        rows = [{"group_id": 1, "regularity_category": "trial", "is_current_group": True}]
        result = schedule_domain.select_historical_baseline_group(rows)
        self.assertEqual(result["outcome"], "none")

    def test_makeup_visitor_never_becomes_baseline(self):
        rows = [{"group_id": 1, "regularity_category": "makeup", "is_current_group": None}]
        result = schedule_domain.select_historical_baseline_group(rows)
        self.assertEqual(result["outcome"], "none")

    def test_other_group_visitor_never_becomes_baseline(self):
        rows = [{"group_id": 1, "regularity_category": "other_group_visitor", "is_current_group": False}]
        result = schedule_domain.select_historical_baseline_group(rows)
        self.assertEqual(result["outcome"], "none")

    def test_regular_confirmed_current_group_is_found(self):
        rows = [{"group_id": 7, "regularity_category": "regular_confirmed", "is_current_group": True}]
        result = schedule_domain.select_historical_baseline_group(rows)
        self.assertEqual(result, {"outcome": "found", "group_id": 7})

    def test_regular_inferred_high_current_group_is_found(self):
        rows = [{"group_id": 8, "regularity_category": "regular_inferred_high", "is_current_group": True}]
        result = schedule_domain.select_historical_baseline_group(rows)
        self.assertEqual(result, {"outcome": "found", "group_id": 8})

    def test_strong_category_but_not_current_group_yields_none(self):
        # a superseded (non-current) strong group from a sequential
        # transition must not become the baseline either — only the one
        # ALE-6 already marked current.
        rows = [{"group_id": 5, "regularity_category": "regular_confirmed", "is_current_group": False}]
        result = schedule_domain.select_historical_baseline_group(rows)
        self.assertEqual(result["outcome"], "none")

    def test_ambiguous_category_row_forces_ambiguous_outcome(self):
        rows = [
            {"group_id": 1, "regularity_category": "ambiguous", "is_current_group": False},
            {"group_id": 2, "regularity_category": "ambiguous", "is_current_group": False},
        ]
        result = schedule_domain.select_historical_baseline_group(rows)
        self.assertEqual(result["outcome"], "ambiguous")
        self.assertCountEqual(result["candidate_group_ids"], [1, 2])

    def test_two_current_strong_rows_is_defensively_ambiguous(self):
        # should not happen given ALE-6's own invariant, but must never be
        # silently resolved by picking one if it ever does.
        rows = [
            {"group_id": 1, "regularity_category": "regular_confirmed", "is_current_group": True},
            {"group_id": 2, "regularity_category": "regular_inferred_high", "is_current_group": True},
        ]
        result = schedule_domain.select_historical_baseline_group(rows)
        self.assertEqual(result["outcome"], "ambiguous")

    def test_no_rows_at_all_yields_none(self):
        result = schedule_domain.select_historical_baseline_group([])
        self.assertEqual(result["outcome"], "none")

    def test_regular_inferred_medium_never_becomes_baseline(self):
        rows = [{"group_id": 1, "regularity_category": "regular_inferred_medium", "is_current_group": True}]
        result = schedule_domain.select_historical_baseline_group(rows)
        self.assertEqual(result["outcome"], "none")


class TestBuildScheduleDraftPreviewDecision(unittest.TestCase):
    def _decide(self, **kw):
        defaults = dict(
            baseline_outcome="found", continuation_status="continues", continuation_detail="continues",
            availability_match="preferred_match", availability_detail="filled",
        )
        defaults.update(kw)
        return schedule_domain.build_schedule_draft_preview_decision(**defaults)

    def test_continue_and_availability_includes_slot_keeps_historical(self):
        result = self._decide(availability_match="preferred_match")
        self.assertEqual(result["decision"], "keep_historical_slot")
        result2 = self._decide(availability_match="possible_match")
        self.assertEqual(result2["decision"], "keep_historical_slot")

    def test_continue_and_availability_excludes_slot_needs_reassignment(self):
        for match in ("branch_conflict", "time_conflict", "start_date_conflict"):
            result = self._decide(availability_match=match)
            self.assertEqual(result["decision"], "needs_reassignment", match)

    def test_stop_is_stopped_regardless_of_everything_else(self):
        result = self._decide(
            continuation_status="discontinued", continuation_detail="discontinued",
            baseline_outcome="ambiguous", availability_match="branch_conflict",
        )
        self.assertEqual(result["decision"], "stopped")

    def test_pending_or_no_response_is_pending_confirmation(self):
        for status, detail in (("unconfirmed", "awaiting_confirmation"), ("unconfirmed", "status_not_found")):
            result = self._decide(continuation_status=status, continuation_detail=detail)
            self.assertEqual(result["decision"], "pending_confirmation", (status, detail))

    def test_conflicting_continuation_records_is_manual_review_not_pending(self):
        # review-gate finding D — resolve_continuation's "ambiguous" coarse
        # status means conflicting onboarding records exist (one says
        # continues, another says undecided/needs_consultation), which is
        # a different situation from "no answer yet" (unconfirmed): data
        # already exists and disagrees with itself, so a human decides
        # rather than the case sitting in the same "still waiting" bucket
        # as an unanswered family.
        result = self._decide(continuation_status="ambiguous", continuation_detail="ambiguous_multiple_records")
        self.assertEqual(result["decision"], "manual_review")
        self.assertIn("continuation_ambiguous_multiple_records", result["reason_codes"])

    def test_ambiguous_historical_group_is_manual_review(self):
        result = self._decide(baseline_outcome="ambiguous")
        self.assertEqual(result["decision"], "manual_review")
        self.assertIn("historical_group_ambiguous", result["reason_codes"])

    def test_no_historical_baseline_is_manual_review(self):
        result = self._decide(baseline_outcome="none")
        self.assertEqual(result["decision"], "manual_review")
        self.assertIn("no_historical_baseline", result["reason_codes"])

    def test_no_availability_at_all_is_safe_unresolved_not_automatic_placement(self):
        # rule 8/9 — absence of Availability must never resolve to
        # keep_historical_slot (that would be automatic placement).
        result = self._decide(availability_match="no_availability")
        self.assertEqual(result["decision"], "pending_confirmation")
        self.assertNotEqual(result["decision"], "keep_historical_slot")

    def test_deterministic_same_input_same_output(self):
        kwargs = dict(
            baseline_outcome="found", continuation_status="continues", continuation_detail="continues",
            availability_match="possible_match", availability_detail="filled",
        )
        r1 = schedule_domain.build_schedule_draft_preview_decision(**kwargs)
        r2 = schedule_domain.build_schedule_draft_preview_decision(**kwargs)
        self.assertEqual(r1, r2)

    def test_result_decision_always_a_known_value(self):
        for status in ("continues", "unconfirmed", "discontinued", "ambiguous"):
            for baseline in ("found", "ambiguous", "none"):
                for match in schedule_domain.SCHEDULE_AVAILABILITY_MATCHES:
                    result = self._decide(
                        continuation_status=status, continuation_detail=status,
                        baseline_outcome=baseline, availability_match=match,
                    )
                    self.assertIn(result["decision"], schedule_domain.SCHEDULE_PREVIEW_DECISIONS)


# ─────────────────────────────────────────────────────────────────────────
# Storage orchestration layer — real temp SQLite DB
# ─────────────────────────────────────────────────────────────────────────

class TestGetScheduleDraftPreviewEndToEnd(unittest.TestCase):
    def test_continue_and_compatible_availability_keeps_historical_slot(self):
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = _seed_group(storage, snap["id"], "500")
        storage.upsert_schedule_source_group_student(
            snap["id"], group_id, "9001", child_display_name="Аня", lessons_attended=9,
            evidence_source="attendance", confidence="high", regularity_category="regular_confirmed",
            membership_evidence=1, is_current_group=1,
        )
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)
        recipient_id = _seed_recipient(storage, "9001", "continues")
        _seed_availability(storage, recipient_id, weekday=4, start_time="16:00", end_time="19:00")

        result = storage.get_schedule_draft_preview(snap["id"], "Кульман 1/1", "Мстиславца 6")
        self.assertTrue(result["ok"])
        row = next(s for s in result["students"] if s["mk_user_id"] == "9001")
        self.assertEqual(row["decision"], "keep_historical_slot")
        self.assertEqual(row["historical_group_id"], group_id)
        self.assertEqual(row["historical_group_name"], "Группа 500")
        self.assertEqual(row["historical_weekday"], 4)
        self.assertEqual(row["historical_start_time"], "17:00")
        self.assertEqual(result["summary"]["keep_historical_slot"], 1)
        self.assertEqual(result["summary"]["total"], 1)

    def test_continue_and_incompatible_availability_needs_reassignment(self):
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = _seed_group(storage, snap["id"], "500", weekday=4, start_time="17:00")
        storage.upsert_schedule_source_group_student(
            snap["id"], group_id, "9002", child_display_name="Боря", lessons_attended=9,
            evidence_source="attendance", confidence="high", regularity_category="regular_confirmed",
            membership_evidence=1, is_current_group=1,
        )
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)
        recipient_id = _seed_recipient(storage, "9002", "continues")
        # only available on a different weekday -> time_conflict
        _seed_availability(storage, recipient_id, weekday=2, start_time="16:00", end_time="19:00")

        result = storage.get_schedule_draft_preview(snap["id"], "Кульман 1/1", "Мстиславца 6")
        row = next(s for s in result["students"] if s["mk_user_id"] == "9002")
        self.assertEqual(row["decision"], "needs_reassignment")
        self.assertEqual(result["summary"]["needs_reassignment"], 1)

    def test_stop_is_stopped(self):
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = _seed_group(storage, snap["id"], "500")
        storage.upsert_schedule_source_group_student(
            snap["id"], group_id, "9003", child_display_name="Вика", lessons_attended=9,
            evidence_source="attendance", confidence="high", regularity_category="regular_confirmed",
            membership_evidence=1, is_current_group=1,
        )
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)
        _seed_recipient(storage, "9003", "not_continuing")

        result = storage.get_schedule_draft_preview(snap["id"], "Кульман 1/1", "Мстиславца 6")
        row = next(s for s in result["students"] if s["mk_user_id"] == "9003")
        self.assertEqual(row["decision"], "stopped")
        self.assertEqual(result["summary"]["stopped"], 1)

    def test_pending_or_no_response_is_pending_confirmation(self):
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = _seed_group(storage, snap["id"], "500")
        storage.upsert_schedule_source_group_student(
            snap["id"], group_id, "9004", child_display_name="Гриша", lessons_attended=9,
            evidence_source="attendance", confidence="high", regularity_category="regular_confirmed",
            membership_evidence=1, is_current_group=1,
        )
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)
        # no client_onboarding_recipients row at all for this student

        result = storage.get_schedule_draft_preview(snap["id"], "Кульман 1/1", "Мстиславца 6")
        row = next(s for s in result["students"] if s["mk_user_id"] == "9004")
        self.assertEqual(row["decision"], "pending_confirmation")
        self.assertEqual(result["summary"]["pending_confirmation"], 1)

    def test_ambiguous_historical_group_is_manual_review(self):
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_a = _seed_group(storage, snap["id"], "500")
        group_b = _seed_group(storage, snap["id"], "501")
        storage.upsert_schedule_source_group_student(
            snap["id"], group_a, "9005", child_display_name="Даша", lessons_attended=9,
            evidence_source="attendance", confidence="high", regularity_category="ambiguous",
            membership_evidence=0, is_current_group=0, ambiguous_peer_group_ids=str(group_b),
        )
        storage.upsert_schedule_source_group_student(
            snap["id"], group_b, "9005", child_display_name="Даша", lessons_attended=9,
            evidence_source="attendance", confidence="high", regularity_category="ambiguous",
            membership_evidence=0, is_current_group=0, ambiguous_peer_group_ids=str(group_a),
        )
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)
        _seed_recipient(storage, "9005", "continues")

        result = storage.get_schedule_draft_preview(snap["id"], "Кульман 1/1", "Мстиславца 6")
        row = next(s for s in result["students"] if s["mk_user_id"] == "9005")
        self.assertEqual(row["decision"], "manual_review")
        self.assertEqual(row["baseline_outcome"], "ambiguous")
        self.assertCountEqual(row["ambiguous_candidate_group_ids"], [group_a, group_b])
        self.assertEqual(result["summary"]["manual_review"], 1)

    def test_conflicting_continuation_records_is_manual_review_end_to_end(self):
        # review-gate finding D — two client_onboarding_recipients rows for
        # the same child with contradictory answers (one continues, one
        # undecided) must resolve to manual_review, not pending_confirmation.
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = _seed_group(storage, snap["id"], "500")
        storage.upsert_schedule_source_group_student(
            snap["id"], group_id, "9010", child_display_name="Лена", lessons_attended=9,
            evidence_source="attendance", confidence="high", regularity_category="regular_confirmed",
            membership_evidence=1, is_current_group=1,
        )
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)
        _seed_recipient(storage, "9010", "continues", campaign_id=1)
        _seed_recipient(storage, "9010", "undecided", campaign_id=2)

        result = storage.get_schedule_draft_preview(snap["id"], "Кульман 1/1", "Мстиславца 6")
        row = next(s for s in result["students"] if s["mk_user_id"] == "9010")
        self.assertEqual(row["continuation_status"], "ambiguous")
        self.assertEqual(row["decision"], "manual_review")
        self.assertNotEqual(row["decision"], "pending_confirmation")
        self.assertEqual(result["summary"]["manual_review"], 1)

    def test_student_in_multiple_historical_groups_appears_exactly_once(self):
        # review-gate finding F — a student with several historical rows
        # (here: one strong current group + one weak/trial row in another
        # group) must still produce exactly one row in the preview output,
        # never one per (student, group) pair.
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_a = _seed_group(storage, snap["id"], "500")
        group_b = _seed_group(storage, snap["id"], "501")
        storage.upsert_schedule_source_group_student(
            snap["id"], group_a, "9011", child_display_name="Миша", lessons_attended=9,
            evidence_source="attendance", confidence="high", regularity_category="regular_confirmed",
            membership_evidence=1, is_current_group=1,
        )
        storage.upsert_schedule_source_group_student(
            snap["id"], group_b, "9011", child_display_name="Миша", lessons_attended=1,
            evidence_source="attendance", confidence="low", regularity_category="one_off",
            membership_evidence=0, is_current_group=None,
        )
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)
        recipient_id = _seed_recipient(storage, "9011", "continues")
        _seed_availability(storage, recipient_id, weekday=4, start_time="16:00", end_time="19:00")

        result = storage.get_schedule_draft_preview(snap["id"], "Кульман 1/1", "Мстиславца 6")
        matches = [s for s in result["students"] if s["mk_user_id"] == "9011"]
        self.assertEqual(len(matches), 1, "student must appear exactly once, not once per historical group")
        self.assertEqual(matches[0]["historical_group_id"], group_a, "must pick the strong current group, not the one_off row")
        self.assertEqual(result["summary"]["total"], 1)

    def test_baseline_selection_is_independent_of_row_insertion_order(self):
        # review-gate finding F — deliberately insert the weak/non-current
        # row BEFORE the strong current row for the same student, across
        # two snapshots, and confirm both resolve to the identical result
        # regardless of insertion/row order.
        def _build(order: str) -> tuple[dict, int]:
            storage = _make_storage()
            snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
            group_strong = _seed_group(storage, snap["id"], "500")
            group_weak = _seed_group(storage, snap["id"], "499")

            def _insert_strong():
                storage.upsert_schedule_source_group_student(
                    snap["id"], group_strong, "9012", child_display_name="Настя", lessons_attended=9,
                    evidence_source="attendance", confidence="high", regularity_category="regular_confirmed",
                    membership_evidence=1, is_current_group=1,
                )

            def _insert_weak():
                storage.upsert_schedule_source_group_student(
                    snap["id"], group_weak, "9012", child_display_name="Настя", lessons_attended=1,
                    evidence_source="attendance", confidence="low", regularity_category="one_off",
                    membership_evidence=0, is_current_group=None,
                )

            if order == "strong_first":
                _insert_strong(); _insert_weak()
            else:
                _insert_weak(); _insert_strong()
            storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)
            recipient_id = _seed_recipient(storage, "9012", "continues")
            _seed_availability(storage, recipient_id, weekday=4, start_time="16:00", end_time="19:00")
            result = storage.get_schedule_draft_preview(snap["id"], "Кульман 1/1", "Мстиславца 6")
            row = next(s for s in result["students"] if s["mk_user_id"] == "9012")
            return row, group_strong

        row_a, group_strong_a = _build("strong_first")
        row_b, group_strong_b = _build("weak_first")
        self.assertEqual(row_a["historical_group_id"], group_strong_a, "must pick the strong group, not the one_off row")
        self.assertEqual(row_a["historical_group_id"], row_b["historical_group_id"])
        self.assertEqual(row_a["decision"], row_b["decision"])
        self.assertEqual(row_a["decision"], "keep_historical_slot")

    def test_makeup_trial_visitor_never_becomes_primary_baseline_end_to_end(self):
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = _seed_group(storage, snap["id"], "500")
        storage.upsert_schedule_source_group_student(
            snap["id"], group_id, "9006", child_display_name="Женя", lessons_attended=0,
            evidence_source="membership", confidence="low", regularity_category="trial",
            n_trial_visits=3, membership_evidence=0, is_current_group=None,
        )
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)
        _seed_recipient(storage, "9006", "continues")

        result = storage.get_schedule_draft_preview(snap["id"], "Кульман 1/1", "Мстиславца 6")
        row = next(s for s in result["students"] if s["mk_user_id"] == "9006")
        self.assertEqual(row["baseline_outcome"], "none")
        self.assertEqual(row["decision"], "manual_review")
        self.assertIsNone(row["historical_group_id"])

    def test_multiple_availability_windows_one_of_which_fits(self):
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = _seed_group(storage, snap["id"], "500", weekday=4, start_time="17:00")
        storage.upsert_schedule_source_group_student(
            snap["id"], group_id, "9007", child_display_name="Зоя", lessons_attended=9,
            evidence_source="attendance", confidence="high", regularity_category="regular_confirmed",
            membership_evidence=1, is_current_group=1,
        )
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)
        recipient_id = _seed_recipient(storage, "9007", "continues")
        # several windows on unrelated days/times, plus one that actually fits
        _seed_availability(storage, recipient_id, weekday=1, start_time="10:00", end_time="12:00")
        _seed_availability(storage, recipient_id, weekday=3, start_time="09:00", end_time="10:00")
        _seed_availability(storage, recipient_id, weekday=4, start_time="16:30", end_time="19:00")

        result = storage.get_schedule_draft_preview(snap["id"], "Кульман 1/1", "Мстиславца 6")
        row = next(s for s in result["students"] if s["mk_user_id"] == "9007")
        self.assertEqual(row["decision"], "keep_historical_slot")

    def test_no_availability_at_all_is_pending_confirmation_not_automatic_placement(self):
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = _seed_group(storage, snap["id"], "500")
        storage.upsert_schedule_source_group_student(
            snap["id"], group_id, "9008", child_display_name="Игорь", lessons_attended=9,
            evidence_source="attendance", confidence="high", regularity_category="regular_confirmed",
            membership_evidence=1, is_current_group=1,
        )
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)
        _seed_recipient(storage, "9008", "continues")
        # recipient exists, continuation=continues, but zero availability rows

        result = storage.get_schedule_draft_preview(snap["id"], "Кульман 1/1", "Мстиславца 6")
        row = next(s for s in result["students"] if s["mk_user_id"] == "9008")
        self.assertEqual(row["decision"], "pending_confirmation")
        self.assertNotEqual(row["decision"], "keep_historical_slot")

    def test_deterministic_repeated_calls_same_input_same_output(self):
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = _seed_group(storage, snap["id"], "500")
        storage.upsert_schedule_source_group_student(
            snap["id"], group_id, "9009", child_display_name="Клим", lessons_attended=9,
            evidence_source="attendance", confidence="high", regularity_category="regular_confirmed",
            membership_evidence=1, is_current_group=1,
        )
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)
        recipient_id = _seed_recipient(storage, "9009", "continues")
        _seed_availability(storage, recipient_id, weekday=4, start_time="16:00", end_time="19:00")

        result1 = storage.get_schedule_draft_preview(snap["id"], "Кульман 1/1", "Мстиславца 6")
        result2 = storage.get_schedule_draft_preview(snap["id"], "Кульман 1/1", "Мстиславца 6")
        self.assertEqual(result1["students"], result2["students"])
        self.assertEqual(result1["summary"], result2["summary"])

    def test_summary_totals_match_student_count(self):
        storage = _make_storage()
        snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
        group_id = _seed_group(storage, snap["id"], "500")
        for i, (continuation, category, current) in enumerate([
            ("continues", "regular_confirmed", 1),
            ("not_continuing", "regular_confirmed", 1),
            ("unknown", "regular_confirmed", 1),
        ]):
            storage.upsert_schedule_source_group_student(
                snap["id"], group_id, f"910{i}", child_display_name=f"Ребёнок {i}", lessons_attended=9,
                evidence_source="attendance", confidence="high", regularity_category=category,
                membership_evidence=1, is_current_group=current,
            )
            _seed_recipient(storage, f"910{i}", continuation)
        storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)

        result = storage.get_schedule_draft_preview(snap["id"], "Кульман 1/1", "Мстиславца 6")
        self.assertEqual(result["summary"]["total"], 3)
        self.assertEqual(
            sum(v for k, v in result["summary"].items() if k != "total"),
            result["summary"]["total"],
        )


@contextlib.contextmanager
def _count_sql(storage: Storage):
    """Counts every SQL statement executed against `storage` for the
    duration of the with-block — same idiom as tests.
    test_schedule_sql_bulk_resolution_v71171._count_sql."""
    log: list[str] = []
    orig_connect = Storage._connect

    class _CountingConn:
        def __init__(self, real_conn):
            self._real = real_conn

        def execute(self, sql, params=()):
            log.append(sql.strip().split("\n")[0][:100])
            return self._real.execute(sql, params)

        def __getattr__(self, item):
            return getattr(self._real, item)

    @contextlib.contextmanager
    def counting_connect(self):
        with orig_connect(self) as conn:
            yield _CountingConn(conn)

    Storage._connect = counting_connect
    try:
        yield log
    finally:
        Storage._connect = orig_connect


def _seed_snapshot_with_n_students(storage: Storage, n_students: int, group_id_suffix: str = "500") -> int:
    """One group, n_students students, each with a real onboarding
    recipient + a fitting Availability window — enough real data flowing
    through continuation/Availability resolution to exercise the bulk
    path, not just N rows resolving to the same not-found shortcut."""
    snap = storage.create_schedule_sync_snapshot("2025-09-01", "2026-05-31", 1, "T")
    group_id = _seed_group(storage, snap["id"], group_id_suffix, weekday=4, start_time="17:00")
    for i in range(n_students):
        uid = f"9{group_id_suffix}{i:05d}"
        storage.upsert_schedule_source_group_student(
            snap["id"], group_id, uid, child_display_name=f"Ребёнок {i}", lessons_attended=9,
            evidence_source="attendance", confidence="high", regularity_category="regular_confirmed",
            membership_evidence=1, is_current_group=1,
        )
        recipient_id = _seed_recipient(storage, uid, "continues")
        _seed_availability(storage, recipient_id, weekday=4, start_time="16:00", end_time="19:00")
    storage.finish_schedule_sync_snapshot(snap["id"], "completed", activate=True)
    return snap["id"]


class TestNoN1QueryPattern(unittest.TestCase):
    """Review-gate finding G — proves query count is chunk-bounded, not
    student-count-bounded, the same discipline (and the same real bug
    class, ALE-6's pre-merge N+1) already enforced for the other schedule
    read paths in tests.test_schedule_sql_bulk_resolution_v71171."""

    def test_query_count_does_not_grow_linearly_with_student_count(self):
        storage_small = _make_storage()
        snap_small = _seed_snapshot_with_n_students(storage_small, 5, group_id_suffix="500")
        storage_large = _make_storage()
        snap_large = _seed_snapshot_with_n_students(storage_large, 300, group_id_suffix="600")

        with _count_sql(storage_small) as log_small:
            storage_small.get_schedule_draft_preview(snap_small, "Кульман 1/1", "Мстиславца 6")
        with _count_sql(storage_large) as log_large:
            storage_large.get_schedule_draft_preview(snap_large, "Кульман 1/1", "Мстиславца 6")

        n_small, n_large = len(log_small), len(log_large)
        self.assertLess(n_large, n_small + 20, f"query count grew from {n_small} to {n_large} — looks student-bounded, not chunk-bounded")
        self.assertLess(n_large, 60, "300 students must not need anywhere near 300+ queries")

    def test_no_per_student_onboarding_or_availability_query_pattern(self):
        storage = _make_storage()
        snap_id = _seed_snapshot_with_n_students(storage, 50)
        with _count_sql(storage) as log:
            storage.get_schedule_draft_preview(snap_id, "Кульман 1/1", "Мстиславца 6")
        onboarding_queries = [q for q in log if "client_onboarding_recipients" in q]
        availability_queries = [q for q in log if "client_schedule_availability" in q]
        self.assertLessEqual(len(onboarding_queries), 3, f"expected a handful of chunked queries, got {len(onboarding_queries)} for 50 students")
        self.assertLessEqual(len(availability_queries), 3, f"expected a handful of chunked queries, got {len(availability_queries)} for 50 students")


class TestNoMoyKlassWrites(unittest.TestCase):
    """Static proof: neither the new storage method nor the new API route
    ever references self.moyklass / a MoyKlass client write call. The
    domain layer (schedule_domain.py) already has zero I/O imports at all,
    so it structurally cannot write anywhere."""

    def test_storage_preview_method_never_references_moyklass(self):
        start = STORAGE_PY.index("def get_schedule_draft_preview(")
        end = STORAGE_PY.index("\n    # ── Conflict detection", start)
        body = STORAGE_PY[start:end]
        self.assertNotIn("moyklass", body.lower())

    def test_api_preview_method_never_references_moyklass_client(self):
        start = WEB_APP_SERVER_PY.index("def schedule_draft_preview(")
        end = WEB_APP_SERVER_PY.index("\n    def schedule_groups_list(", start)
        body = WEB_APP_SERVER_PY[start:end]
        self.assertNotIn("self.moyklass", body)

    def test_api_preview_method_never_gated_by_draft_mutations_flag(self):
        # confirms this stays a pure read — never blocked by (and never
        # needs) SCHEDULE_DRAFT_MUTATIONS_ENABLED, since nothing is saved.
        start = WEB_APP_SERVER_PY.index("def schedule_draft_preview(")
        end = WEB_APP_SERVER_PY.index("\n    def schedule_groups_list(", start)
        body = WEB_APP_SERVER_PY[start:end]
        self.assertNotIn("schedule_draft_mutations_enabled", body)

    def test_domain_module_has_no_io_imports(self):
        domain_src = (ROOT / "schedule_domain.py").read_text(encoding="utf-8")
        header = "\n".join(domain_src.split("\n")[:20])
        self.assertNotIn("import moyklass_client", header)
        self.assertNotIn("import requests", header)
        self.assertNotIn("import sqlite3", header)


if __name__ == "__main__":
    unittest.main()

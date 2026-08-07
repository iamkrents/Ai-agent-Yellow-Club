"""Tests for v7.1.17.1 — ALE-6 Schedule Data Quality: the regularity
classifier pure logic in schedule_domain.py.

Covers: classify_group_student_regularity for every category (regular_
confirmed, regular_inferred_high, regular_inferred_medium, trial, makeup,
one_off, other_group_visitor, insufficient_evidence), slot_regularity_
ratio, and resolve_current_and_ambiguous_groups (sequential transition vs
material overlap -> ambiguous, per real ALE-6 audit thresholds: threshold
B = >=5 regular visits + slot ratio >=0.75; overlap ambiguous = >14 days
AND >=2 regular visits from each group inside the overlap window).

Run offline:
    python -m unittest tests.test_schedule_regularity_classifier_v71171 -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import schedule_domain


class TestClassifyGroupStudentRegularity(unittest.TestCase):
    def test_regular_confirmed_needs_evidence_visits_and_ratio(self):
        result = schedule_domain.classify_group_student_regularity(
            n_regular=6, n_trial=0, n_makeup=0, group_specific_evidence=True, slot_ratio=0.9,
        )
        self.assertEqual(result["category"], "regular_confirmed")
        self.assertTrue(result["membership_evidence"])

    def test_regular_confirmed_boundary_at_threshold_b(self):
        # threshold B: >=5 visits, ratio >=0.75 — exact boundary must confirm
        result = schedule_domain.classify_group_student_regularity(
            n_regular=5, n_trial=0, n_makeup=0, group_specific_evidence=True, slot_ratio=0.75,
        )
        self.assertEqual(result["category"], "regular_confirmed")

    def test_below_visit_threshold_with_evidence_is_medium(self):
        result = schedule_domain.classify_group_student_regularity(
            n_regular=3, n_trial=0, n_makeup=0, group_specific_evidence=True, slot_ratio=1.0,
        )
        self.assertEqual(result["category"], "regular_inferred_medium")

    def test_high_visits_but_scattered_schedule_is_inferred_high_not_confirmed(self):
        # evidence + enough visits, but ratio below threshold -> confirmed
        # membership, but NOT confirmed schedule -> regular_inferred_high,
        # never silently promoted to regular_confirmed.
        result = schedule_domain.classify_group_student_regularity(
            n_regular=8, n_trial=0, n_makeup=0, group_specific_evidence=True, slot_ratio=0.4,
        )
        self.assertEqual(result["category"], "regular_inferred_high")

    def test_unknown_ratio_none_does_not_block_confirmation(self):
        # slot_ratio is None (e.g. missing weekday/time data) must not be
        # treated as "irregular" — None is "unknown", not "bad".
        result = schedule_domain.classify_group_student_regularity(
            n_regular=6, n_trial=0, n_makeup=0, group_specific_evidence=True, slot_ratio=None,
        )
        self.assertEqual(result["category"], "regular_confirmed")

    def test_high_visits_without_evidence_is_never_confirmed(self):
        # point 2 — regular_confirmed requires group-specific membership
        # evidence; visits/ratio alone can never produce it.
        result = schedule_domain.classify_group_student_regularity(
            n_regular=20, n_trial=0, n_makeup=0, group_specific_evidence=False, slot_ratio=1.0,
        )
        self.assertNotEqual(result["category"], "regular_confirmed")
        self.assertNotEqual(result["category"], "regular_inferred_high")
        self.assertNotEqual(result["category"], "regular_inferred_medium")

    def test_zero_regular_all_trial_is_trial(self):
        result = schedule_domain.classify_group_student_regularity(
            n_regular=0, n_trial=1, n_makeup=0, group_specific_evidence=False, slot_ratio=None,
        )
        self.assertEqual(result["category"], "trial")

    def test_zero_regular_all_makeup_is_makeup(self):
        result = schedule_domain.classify_group_student_regularity(
            n_regular=0, n_trial=0, n_makeup=2, group_specific_evidence=False, slot_ratio=None,
        )
        self.assertEqual(result["category"], "makeup")

    def test_one_regular_visit_with_evidence_is_one_off(self):
        result = schedule_domain.classify_group_student_regularity(
            n_regular=1, n_trial=0, n_makeup=0, group_specific_evidence=True, slot_ratio=None,
        )
        self.assertEqual(result["category"], "one_off")

    def test_one_regular_visit_without_evidence_is_insufficient(self):
        result = schedule_domain.classify_group_student_regularity(
            n_regular=1, n_trial=0, n_makeup=0, group_specific_evidence=False, slot_ratio=None,
        )
        self.assertEqual(result["category"], "insufficient_evidence")

    def test_other_group_visitor_low_visits_no_evidence_non_primary(self):
        result = schedule_domain.classify_group_student_regularity(
            n_regular=2, n_trial=0, n_makeup=0, group_specific_evidence=False, slot_ratio=None,
            is_primary_group_for_student=False,
        )
        self.assertEqual(result["category"], "other_group_visitor")

    def test_same_low_visits_no_evidence_but_IS_primary_group_is_insufficient(self):
        # the only distinguishing signal between other_group_visitor and
        # insufficient_evidence for a no-evidence low-visit pair.
        result = schedule_domain.classify_group_student_regularity(
            n_regular=2, n_trial=0, n_makeup=0, group_specific_evidence=False, slot_ratio=None,
            is_primary_group_for_student=True,
        )
        self.assertEqual(result["category"], "insufficient_evidence")

    def test_membership_and_slot_confidence_kept_independent(self):
        # point 3 — never blended into one combined score.
        result = schedule_domain.classify_group_student_regularity(
            n_regular=6, n_trial=0, n_makeup=0, group_specific_evidence=True, slot_ratio=0.5,
        )
        self.assertIn("membership_evidence", result)
        self.assertIn("slot_ratio", result)
        self.assertTrue(result["membership_evidence"])
        self.assertEqual(result["slot_ratio"], 0.5)


class TestSlotRegularityRatio(unittest.TestCase):
    def test_fewer_than_3_visits_returns_none(self):
        self.assertIsNone(schedule_domain.slot_regularity_ratio([(4, "17:00"), (4, "17:00")]))

    def test_perfectly_regular_ratio_is_1(self):
        slots = [(4, "17:00")] * 5
        self.assertEqual(schedule_domain.slot_regularity_ratio(slots), 1.0)

    def test_scattered_ratio_reflects_dominant_share(self):
        slots = [(4, "17:00"), (4, "17:00"), (4, "17:00"), (2, "10:00"), (6, "12:00")]
        self.assertAlmostEqual(schedule_domain.slot_regularity_ratio(slots), 3 / 5)


class TestResolveCurrentAndAmbiguousGroups(unittest.TestCase):
    def test_single_strong_group_is_current(self):
        candidates = [
            {"group_key": "A", "category": "regular_confirmed", "regular_dates": ["2025-09-01", "2025-09-08", "2025-09-15"]},
        ]
        result = schedule_domain.resolve_current_and_ambiguous_groups(candidates)
        self.assertTrue(result["A"]["is_current"])
        self.assertEqual(result["A"]["final_category"], "regular_confirmed")

    def test_non_strong_categories_pass_through_untouched(self):
        candidates = [{"group_key": "A", "category": "trial", "regular_dates": []}]
        result = schedule_domain.resolve_current_and_ambiguous_groups(candidates)
        self.assertIsNone(result["A"]["is_current"])
        self.assertEqual(result["A"]["final_category"], "trial")

    def test_sequential_transition_latest_is_current_earlier_kept_as_history(self):
        # real ALE-6 audit case pattern: group A Sep-Jan, group B Feb-May, no overlap
        candidates = [
            {"group_key": "A_old", "category": "regular_confirmed", "regular_dates": [f"2025-{m:02d}-01" for m in (9, 10, 11, 12)] + ["2026-01-05"]},
            {"group_key": "B_new", "category": "regular_confirmed", "regular_dates": [f"2026-{m:02d}-15" for m in (2, 3, 4, 5)] + ["2026-05-20"]},
        ]
        result = schedule_domain.resolve_current_and_ambiguous_groups(candidates)
        self.assertFalse(result["A_old"]["is_current"])
        self.assertEqual(result["A_old"]["final_category"], "regular_confirmed", "history preserved, not dropped/mislabeled")
        self.assertTrue(result["B_new"]["is_current"])
        self.assertEqual(result["B_new"]["ambiguous_peer_keys"], [])

    def test_material_overlap_both_become_ambiguous_never_auto_resolved(self):
        # real ALE-6 audit case pattern (student 8479317): two groups both
        # strong, running concurrently for months.
        overlap_dates_a = [f"2025-{m:02d}-{d:02d}" for m in (11, 12) for d in (5, 12, 19, 26)]
        overlap_dates_b = [f"2025-{m:02d}-{d:02d}" for m in (11, 12) for d in (3, 10, 17, 24)]
        candidates = [
            {"group_key": "X", "category": "regular_confirmed", "regular_dates": overlap_dates_a},
            {"group_key": "Y", "category": "regular_confirmed", "regular_dates": overlap_dates_b},
        ]
        result = schedule_domain.resolve_current_and_ambiguous_groups(candidates)
        self.assertEqual(result["X"]["final_category"], "ambiguous")
        self.assertEqual(result["Y"]["final_category"], "ambiguous")
        self.assertFalse(result["X"]["is_current"])
        self.assertFalse(result["Y"]["is_current"])
        self.assertIn("Y", result["X"]["ambiguous_peer_keys"])
        self.assertIn("X", result["Y"]["ambiguous_peer_keys"])

    def test_short_overlap_under_14_days_is_not_material_stays_sequential(self):
        # brief overlap during a transition week must NOT trigger ambiguous.
        candidates = [
            {"group_key": "A", "category": "regular_confirmed", "regular_dates": ["2025-09-01", "2025-09-08", "2025-09-15", "2025-09-22", "2025-09-29"]},
            {"group_key": "B", "category": "regular_confirmed", "regular_dates": ["2025-09-25", "2025-10-02", "2025-10-09", "2025-10-16", "2025-10-23"]},
        ]
        result = schedule_domain.resolve_current_and_ambiguous_groups(candidates)
        self.assertNotEqual(result["A"]["final_category"], "ambiguous")
        self.assertNotEqual(result["B"]["final_category"], "ambiguous")
        self.assertTrue(result["B"]["is_current"], "latest last-visit group should be current")
        self.assertFalse(result["A"]["is_current"])

    def test_long_overlap_but_too_few_visits_inside_window_is_not_material(self):
        # overlap span >14 days on paper, but one side barely visited during
        # that actual window (only 1 visit inside it) -> not material.
        candidates = [
            {"group_key": "A", "category": "regular_confirmed", "regular_dates": ["2025-09-01", "2025-09-08", "2025-09-15", "2025-09-22", "2025-11-20"]},
            {"group_key": "B", "category": "regular_confirmed", "regular_dates": ["2025-10-01", "2025-10-08", "2025-10-15", "2025-10-22", "2025-11-25"]},
        ]
        result = schedule_domain.resolve_current_and_ambiguous_groups(candidates)
        # overlap window is [2025-10-01 .. 2025-11-20]; A has only one date
        # (2025-11-20) inside it -> below REGULARITY_OVERLAP_AMBIGUOUS_MIN_VISITS_EACH
        self.assertNotEqual(result["A"]["final_category"], "ambiguous")
        self.assertNotEqual(result["B"]["final_category"], "ambiguous")

    def test_three_way_only_overlapping_pair_flagged(self):
        candidates = [
            {"group_key": "A", "category": "regular_confirmed", "regular_dates": [f"2025-10-{d:02d}" for d in (1, 8, 15, 22, 29)]},
            {"group_key": "B", "category": "regular_confirmed", "regular_dates": [f"2025-10-{d:02d}" for d in (2, 9, 16, 23, 30)]},
            {"group_key": "C", "category": "regular_confirmed", "regular_dates": [f"2026-{m:02d}-01" for m in (2, 3, 4, 5)]},
        ]
        result = schedule_domain.resolve_current_and_ambiguous_groups(candidates)
        self.assertEqual(result["A"]["final_category"], "ambiguous")
        self.assertEqual(result["B"]["final_category"], "ambiguous")
        self.assertNotEqual(result["C"]["final_category"], "ambiguous")
        self.assertTrue(result["C"]["is_current"])


if __name__ == "__main__":
    unittest.main()

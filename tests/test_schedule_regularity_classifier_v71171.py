"""Tests for v7.1.17.1 — ALE-6 Schedule Data Quality: the regularity
classifier pure logic in schedule_domain.py.

Rewritten for the pre-merge semantic fix: regular_confirmed means ONLY
"reliable group-specific membership evidence exists" — no attendance
threshold gates it. regular_inferred_high/medium only ever apply when
group_specific_evidence is False (pure attendance-pattern inference used
BECAUSE no membership evidence exists — never a weaker version of
regular_confirmed). Foundation/dominant-slot eligibility is a separate
question (is_foundation_eligible), never implied by category alone.

Covers: classify_group_student_regularity for every category, the
zero-regular/zero-trial/zero-makeup "lesson relation exists but nobody
ever attended" case (must be insufficient_evidence, never trial),
slot_regularity_ratio, is_foundation_eligible, and resolve_current_and_
ambiguous_groups (sequential transition vs material overlap -> ambiguous,
per real ALE-6 audit thresholds: threshold B = >=5 regular visits + slot
ratio >=0.75; overlap ambiguous = >14 days AND >=2 regular visits from
each group inside the overlap window).

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


class TestRegularConfirmedIsEvidenceOnly(unittest.TestCase):
    """regular_confirmed = group-specific membership evidence, full stop —
    no attendance/slot threshold gates the category itself."""

    def test_evidence_with_zero_regular_visits_is_confirmed(self):
        result = schedule_domain.classify_group_student_regularity(
            n_regular=0, n_trial=0, n_makeup=0, group_specific_evidence=True, slot_ratio=None,
        )
        self.assertEqual(result["category"], "regular_confirmed")
        self.assertTrue(result["membership_evidence"])

    def test_evidence_with_one_regular_visit_is_confirmed(self):
        result = schedule_domain.classify_group_student_regularity(
            n_regular=1, n_trial=0, n_makeup=0, group_specific_evidence=True, slot_ratio=None,
        )
        self.assertEqual(result["category"], "regular_confirmed")

    def test_evidence_with_weak_scattered_slot_is_still_confirmed(self):
        # membership and schedule confidence are independent — a weak
        # slot_ratio must never downgrade a confirmed member.
        result = schedule_domain.classify_group_student_regularity(
            n_regular=8, n_trial=0, n_makeup=0, group_specific_evidence=True, slot_ratio=0.1,
        )
        self.assertEqual(result["category"], "regular_confirmed")
        self.assertEqual(result["slot_ratio"], 0.1, "slot_ratio still reported separately, just not used for category")

    def test_evidence_with_many_regular_visits_and_high_ratio_is_confirmed(self):
        result = schedule_domain.classify_group_student_regularity(
            n_regular=30, n_trial=0, n_makeup=0, group_specific_evidence=True, slot_ratio=1.0,
        )
        self.assertEqual(result["category"], "regular_confirmed")

    def test_evidence_overrides_trial_and_makeup_counts_too(self):
        # G.1 — evidence alone is final even when trial/makeup counts are
        # nonzero and n_regular is 0.
        result = schedule_domain.classify_group_student_regularity(
            n_regular=0, n_trial=3, n_makeup=2, group_specific_evidence=True, slot_ratio=None,
        )
        self.assertEqual(result["category"], "regular_confirmed")


class TestNoEvidenceZeroRegular(unittest.TestCase):
    """Zero regular visits, no membership evidence — the exact shape of
    the 44 real-world zero-visit pairs (lesson/student relation exists,
    every record visit=false, no trial/makeup markers at all)."""

    def test_zero_everything_is_insufficient_evidence_never_trial(self):
        result = schedule_domain.classify_group_student_regularity(
            n_regular=0, n_trial=0, n_makeup=0, group_specific_evidence=False, slot_ratio=None,
        )
        self.assertEqual(result["category"], "insufficient_evidence")
        self.assertNotEqual(result["category"], "trial")

    def test_trial_only_is_trial(self):
        result = schedule_domain.classify_group_student_regularity(
            n_regular=0, n_trial=1, n_makeup=0, group_specific_evidence=False, slot_ratio=None,
        )
        self.assertEqual(result["category"], "trial")

    def test_makeup_only_is_makeup(self):
        result = schedule_domain.classify_group_student_regularity(
            n_regular=0, n_trial=0, n_makeup=2, group_specific_evidence=False, slot_ratio=None,
        )
        self.assertEqual(result["category"], "makeup")

    def test_mixed_trial_and_makeup_only_is_insufficient_evidence(self):
        result = schedule_domain.classify_group_student_regularity(
            n_regular=0, n_trial=1, n_makeup=1, group_specific_evidence=False, slot_ratio=None,
        )
        self.assertEqual(result["category"], "insufficient_evidence")


class TestNoEvidenceWithRegularHistory(unittest.TestCase):
    """No membership evidence, real regular attendance exists — pure
    attendance-pattern inference."""

    def test_no_evidence_5plus_visits_high_ratio_is_inferred_high(self):
        result = schedule_domain.classify_group_student_regularity(
            n_regular=6, n_trial=0, n_makeup=0, group_specific_evidence=False, slot_ratio=0.9,
        )
        self.assertEqual(result["category"], "regular_inferred_high")

    def test_boundary_exactly_5_visits_ratio_exactly_075_is_inferred_high(self):
        result = schedule_domain.classify_group_student_regularity(
            n_regular=5, n_trial=0, n_makeup=0, group_specific_evidence=False, slot_ratio=0.75,
        )
        self.assertEqual(result["category"], "regular_inferred_high")

    def test_no_evidence_5plus_visits_low_ratio_is_inferred_medium(self):
        result = schedule_domain.classify_group_student_regularity(
            n_regular=8, n_trial=0, n_makeup=0, group_specific_evidence=False, slot_ratio=0.4,
        )
        self.assertEqual(result["category"], "regular_inferred_medium")

    def test_no_evidence_5plus_visits_unknown_ratio_is_inferred_medium_not_high(self):
        # slot_ratio=None ("unknown") must NOT satisfy the strict >=0.75
        # requirement for the high tier — falls to medium.
        result = schedule_domain.classify_group_student_regularity(
            n_regular=6, n_trial=0, n_makeup=0, group_specific_evidence=False, slot_ratio=None,
        )
        self.assertEqual(result["category"], "regular_inferred_medium")

    def test_no_evidence_2_to_4_primary_visits_is_inferred_medium(self):
        for n in (2, 3, 4):
            with self.subTest(n_regular=n):
                result = schedule_domain.classify_group_student_regularity(
                    n_regular=n, n_trial=0, n_makeup=0, group_specific_evidence=False, slot_ratio=1.0,
                    is_primary_group_for_student=True,
                )
                self.assertEqual(result["category"], "regular_inferred_medium")

    def test_no_evidence_1_primary_visit_is_one_off(self):
        result = schedule_domain.classify_group_student_regularity(
            n_regular=1, n_trial=0, n_makeup=0, group_specific_evidence=False, slot_ratio=None,
            is_primary_group_for_student=True,
        )
        self.assertEqual(result["category"], "one_off")

    def test_no_evidence_1_to_3_secondary_visits_is_other_group_visitor(self):
        for n in (1, 2, 3):
            with self.subTest(n_regular=n):
                result = schedule_domain.classify_group_student_regularity(
                    n_regular=n, n_trial=0, n_makeup=0, group_specific_evidence=False, slot_ratio=None,
                    is_primary_group_for_student=False,
                )
                self.assertEqual(result["category"], "other_group_visitor")

    def test_other_group_visitor_takes_priority_over_medium(self):
        # a secondary pair with n_regular=3 must be other_group_visitor,
        # never medium, even though 3 would qualify for medium if primary.
        result = schedule_domain.classify_group_student_regularity(
            n_regular=3, n_trial=0, n_makeup=0, group_specific_evidence=False, slot_ratio=1.0,
            is_primary_group_for_student=False,
        )
        self.assertEqual(result["category"], "other_group_visitor")

    def test_secondary_4_visits_no_longer_other_group_visitor(self):
        # E caps at n_regular<=3 — at 4 it falls through to medium/high
        # regardless of primary status.
        result = schedule_domain.classify_group_student_regularity(
            n_regular=4, n_trial=0, n_makeup=0, group_specific_evidence=False, slot_ratio=1.0,
            is_primary_group_for_student=False,
        )
        self.assertEqual(result["category"], "regular_inferred_medium")

    def test_high_visit_count_never_becomes_confirmed_without_evidence(self):
        result = schedule_domain.classify_group_student_regularity(
            n_regular=30, n_trial=0, n_makeup=0, group_specific_evidence=False, slot_ratio=1.0,
        )
        self.assertNotEqual(result["category"], "regular_confirmed")
        self.assertEqual(result["category"], "regular_inferred_high")


class TestMembershipAndSlotConfidenceIndependent(unittest.TestCase):
    def test_fields_returned_separately_never_blended(self):
        result = schedule_domain.classify_group_student_regularity(
            n_regular=6, n_trial=0, n_makeup=0, group_specific_evidence=False, slot_ratio=0.5,
        )
        self.assertIn("membership_evidence", result)
        self.assertIn("slot_ratio", result)
        self.assertFalse(result["membership_evidence"])
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


class TestFoundationEligibility(unittest.TestCase):
    """ALE-6 point 2 — foundation eligibility is never implied by category
    alone; all conditions must hold simultaneously."""

    def test_confirmed_but_not_current_is_not_eligible(self):
        self.assertFalse(schedule_domain.is_foundation_eligible(
            category="regular_confirmed", is_current_group=False, n_regular=10, slot_ratio=1.0,
        ))

    def test_confirmed_but_weak_attendance_is_not_eligible(self):
        # the core point-2 case: confirmed membership, near-zero history.
        self.assertFalse(schedule_domain.is_foundation_eligible(
            category="regular_confirmed", is_current_group=True, n_regular=0, slot_ratio=None,
        ))

    def test_confirmed_current_strong_attendance_is_eligible(self):
        self.assertTrue(schedule_domain.is_foundation_eligible(
            category="regular_confirmed", is_current_group=True, n_regular=8, slot_ratio=0.9,
        ))

    def test_inferred_high_current_strong_attendance_is_eligible(self):
        self.assertTrue(schedule_domain.is_foundation_eligible(
            category="regular_inferred_high", is_current_group=True, n_regular=6, slot_ratio=0.8,
        ))

    def test_inferred_medium_never_eligible(self):
        self.assertFalse(schedule_domain.is_foundation_eligible(
            category="regular_inferred_medium", is_current_group=True, n_regular=10, slot_ratio=1.0,
        ))

    def test_ambiguous_never_eligible(self):
        self.assertFalse(schedule_domain.is_foundation_eligible(
            category="ambiguous", is_current_group=False, n_regular=10, slot_ratio=1.0,
        ))

    def test_is_current_group_none_is_not_eligible(self):
        # None ("not applicable") must not be treated as truthy-enough.
        self.assertFalse(schedule_domain.is_foundation_eligible(
            category="regular_confirmed", is_current_group=None, n_regular=10, slot_ratio=1.0,
        ))

    def test_below_visit_threshold_not_eligible_even_if_confirmed_current(self):
        self.assertFalse(schedule_domain.is_foundation_eligible(
            category="regular_confirmed", is_current_group=True, n_regular=2, slot_ratio=1.0,
        ))


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
        candidates = [
            {"group_key": "A", "category": "regular_confirmed", "regular_dates": ["2025-09-01", "2025-09-08", "2025-09-15", "2025-09-22", "2025-11-20"]},
            {"group_key": "B", "category": "regular_confirmed", "regular_dates": ["2025-10-01", "2025-10-08", "2025-10-15", "2025-10-22", "2025-11-25"]},
        ]
        result = schedule_domain.resolve_current_and_ambiguous_groups(candidates)
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

    def test_real_parallel_membership_is_never_architecturally_forbidden(self):
        # two groups, both regular_confirmed via real membership evidence,
        # genuinely overlapping for months (true dual enrollment candidate)
        # — must still resolve to ambiguous for review, not be silently
        # merged/rejected/forced into a single-current invariant.
        dates_a = [f"2025-{m:02d}-05" for m in range(9, 13)] + [f"2026-{m:02d}-05" for m in range(1, 6)]
        dates_b = [f"2025-{m:02d}-12" for m in range(9, 13)] + [f"2026-{m:02d}-12" for m in range(1, 6)]
        candidates = [
            {"group_key": "P", "category": "regular_confirmed", "regular_dates": dates_a},
            {"group_key": "Q", "category": "regular_confirmed", "regular_dates": dates_b},
        ]
        result = schedule_domain.resolve_current_and_ambiguous_groups(candidates)
        self.assertEqual(result["P"]["final_category"], "ambiguous")
        self.assertEqual(result["Q"]["final_category"], "ambiguous")


if __name__ == "__main__":
    unittest.main()

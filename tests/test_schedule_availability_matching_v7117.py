"""Tests for v7.1.17 — "Расписание" schedule module: AVAILABILITY matching.

Covers spec section 23 AVAILABILITY checks 28-38: preferred/possible match,
branch/time/start-date conflicts, no-availability, "any branch", full vs.
partial interval overlap, multiple intervals, and weekday-from-date
correctness (the calendar-date, timezone-unambiguous convention this
module uses instead of raw UTC).

Run offline:
    python -m unittest tests.test_schedule_availability_matching_v7117 -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import schedule_domain

THU_INTERVAL = {"weekday": 4, "start_time": "16:00", "end_time": "19:00", "preference": "possible"}
THU_INTERVAL_PREFERRED = {"weekday": 4, "start_time": "16:00", "end_time": "19:00", "preference": "preferred"}


def _match(**kw):
    defaults = dict(
        continuation_status="continues", weekday=4, start_time="17:00", duration_minutes=60,
        group_branch_code="YC1", intervals=[THU_INTERVAL], preferred_branch="YC1",
        available_from=None, planned_start_date=None,
    )
    defaults.update(kw)
    return schedule_domain.match_availability(**defaults)


class TestAvailabilityMatching(unittest.TestCase):
    def test_28_preferred_match(self):
        result = _match(intervals=[THU_INTERVAL_PREFERRED])
        self.assertEqual(result["match"], "preferred_match")

    def test_29_possible_match(self):
        result = _match(intervals=[THU_INTERVAL])
        self.assertEqual(result["match"], "possible_match")

    def test_30_branch_conflict(self):
        result = _match(preferred_branch="YC2", group_branch_code="YC1")
        self.assertEqual(result["match"], "branch_conflict")

    def test_31_time_conflict_no_overlap_same_day(self):
        result = _match(intervals=[{"weekday": 4, "start_time": "08:00", "end_time": "09:00", "preference": "possible"}])
        self.assertEqual(result["match"], "time_conflict")

    def test_31b_time_conflict_wrong_day(self):
        result = _match(intervals=[{"weekday": 2, "start_time": "16:00", "end_time": "19:00", "preference": "possible"}])
        self.assertEqual(result["match"], "time_conflict")

    def test_32_start_date_conflict(self):
        result = _match(available_from="2025-10-01", planned_start_date="2025-09-01")
        self.assertEqual(result["match"], "start_date_conflict")

    def test_32b_start_date_ok_when_available_before_planned(self):
        result = _match(intervals=[THU_INTERVAL_PREFERRED], available_from="2025-08-01", planned_start_date="2025-09-01")
        self.assertEqual(result["match"], "preferred_match")

    def test_33_no_availability(self):
        result = _match(intervals=[])
        self.assertEqual(result["match"], "no_availability")

    def test_34_any_branch_never_conflicts(self):
        result = _match(preferred_branch="either", group_branch_code="YC2", intervals=[THU_INTERVAL_PREFERRED])
        self.assertEqual(result["match"], "preferred_match")

    def test_34b_unmapped_group_branch_never_fabricates_conflict(self):
        result = _match(preferred_branch="YC1", group_branch_code="unknown", intervals=[THU_INTERVAL_PREFERRED])
        self.assertNotEqual(result["match"], "branch_conflict")

    def test_35_full_containment_matches(self):
        result = _match(start_time="16:30", duration_minutes=60, intervals=[{"weekday": 4, "start_time": "16:00", "end_time": "19:00", "preference": "possible"}])
        self.assertEqual(result["match"], "possible_match")

    def test_36_partial_overlap_is_never_a_match(self):
        # lesson 17:00-18:00, availability only 16:00-17:30 — 30 min overlap only
        result = _match(start_time="17:00", duration_minutes=60, intervals=[{"weekday": 4, "start_time": "16:00", "end_time": "17:30", "preference": "possible"}])
        self.assertEqual(result["match"], "time_conflict")

    def test_37_multiple_intervals_any_full_fit_wins(self):
        result = _match(intervals=[
            {"weekday": 2, "start_time": "10:00", "end_time": "11:00", "preference": "possible"},
            {"weekday": 4, "start_time": "16:00", "end_time": "19:00", "preference": "preferred"},
        ])
        self.assertEqual(result["match"], "preferred_match")

    def test_38_weekday_from_date_matches_real_calendar(self):
        # 2025-09-04 is a real Thursday; no timezone offset applies to a
        # plain calendar-date string, so this must be stable regardless of
        # server timezone.
        self.assertEqual(schedule_domain.weekday_from_date("2025-09-04"), 4)
        self.assertEqual(schedule_domain.weekday_from_date("2025-09-01"), 1)  # Monday
        self.assertEqual(schedule_domain.weekday_from_date("2025-09-07"), 7)  # Sunday
        self.assertIsNone(schedule_domain.weekday_from_date(""))
        self.assertIsNone(schedule_domain.weekday_from_date(None))


if __name__ == "__main__":
    unittest.main()

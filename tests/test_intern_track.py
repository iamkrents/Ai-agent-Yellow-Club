"""Intern onboarding track tests (Stage 1: data layer + stage machine).

Runs fully offline against a throwaway SQLite DB:

    python3 -m unittest tests.test_intern_track -v

Covers the three product decisions and the linear gated flow:
  1. Final status after the demo is "active".
  2. Auto-access to the trial stage needs exactly 2 commented observations.
  3. Only "methodist" may review work / grant approval.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import intern_track as it  # noqa: E402
from storage import Storage  # noqa: E402


def fresh_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


class TestDecisions(unittest.TestCase):
    def test_final_status_is_active(self):
        self.assertEqual(it.final_status(), "active")

    def test_only_methodist_reviews(self):
        self.assertTrue(it.can_review_intern("methodist"))
        for role in ["teacher", "owner", "operations", "client_manager", "intern", "", None]:
            self.assertFalse(it.can_review_intern(role), f"{role!r} must not review interns")

    def test_threshold_is_exactly_two(self):
        self.assertEqual(it.REQUIRED_OBSERVATIONS, 2)
        self.assertFalse(it.should_unlock_trial("trainee", 0))
        self.assertFalse(it.should_unlock_trial("trainee", 1))
        self.assertTrue(it.should_unlock_trial("trainee", 2))
        self.assertTrue(it.should_unlock_trial("trainee", 3))
        # Only a trainee can unlock this way.
        self.assertFalse(it.should_unlock_trial("trial_allowed", 5))


class TestStageMachine(unittest.TestCase):
    def test_observing(self):
        s = it.compute_intern_stage("trainee", completed_observations=1)
        self.assertEqual(s["stage"], it.STAGE_OBSERVING)
        self.assertEqual(s["observations"], {"done": 1, "required": 2, "remaining": 1})
        self.assertFalse(s["isDone"])

    def test_trial_work_paths(self):
        for ws, expect in [("", it.STAGE_TRIAL_WORK), ("submitted", it.STAGE_TRIAL_WORK), ("rejected", it.STAGE_TRIAL_WORK)]:
            s = it.compute_intern_stage("trial_allowed", work_status=ws)
            self.assertEqual(s["stage"], expect, f"work_status={ws}")

    def test_demo_booking_after_work_accepted(self):
        s = it.compute_intern_stage("trial_allowed", work_status="accepted")
        self.assertEqual(s["stage"], it.STAGE_DEMO_BOOKING)
        s2 = it.compute_intern_stage("trial_allowed", work_status="accepted", demo_status="failed")
        self.assertEqual(s2["stage"], it.STAGE_DEMO_BOOKING)

    def test_demo_review(self):
        s = it.compute_intern_stage("trial_allowed", work_status="accepted", demo_status="conducted")
        self.assertEqual(s["stage"], it.STAGE_DEMO_REVIEW)

    def test_done(self):
        s = it.compute_intern_stage("active")
        self.assertEqual(s["stage"], it.STAGE_DONE)
        self.assertTrue(s["isDone"])


class TestStorageObservations(unittest.TestCase):
    def setUp(self):
        self.db = fresh_storage()

    def test_signup_dedup_and_completion_gate(self):
        uid = 1001
        o1 = self.db.add_intern_observation(uid, mk_lesson_id="L1", lesson_title="Roblox пробное")
        # Duplicate signup for same lesson must not create a second row.
        again = self.db.add_intern_observation(uid, mk_lesson_id="L1")
        self.assertEqual(o1["id"], again["id"])
        o2 = self.db.add_intern_observation(uid, mk_lesson_id="L2", lesson_title="Python пробное")

        # Signed up but not commented -> 0 completed.
        self.assertEqual(self.db.count_intern_completed_observations(uid), 0)
        self.assertFalse(it.should_unlock_trial("trainee", self.db.count_intern_completed_observations(uid)))

        # One comment -> 1 completed, still locked.
        self.db.set_intern_observation_comment(o1["id"], "Понравилось, был вопрос по группам")
        self.assertEqual(self.db.count_intern_completed_observations(uid), 1)
        self.assertFalse(it.should_unlock_trial("trainee", 1))

        # Empty comment is ignored (does not complete an observation).
        empty = self.db.set_intern_observation_comment(o1["id"], "   ")
        self.assertEqual(empty, {})

        # Second comment on a DIFFERENT observation -> 2 completed -> unlock.
        self.db.set_intern_observation_comment(o2["id"], "Записал замечания по темпу")
        self.assertEqual(self.db.count_intern_completed_observations(uid), 2)
        self.assertTrue(it.should_unlock_trial("trainee", 2))

    def test_observations_are_isolated_per_user(self):
        self.db.add_intern_observation(1, mk_lesson_id="A")
        self.db.set_intern_observation_comment(self.db.add_intern_observation(2, mk_lesson_id="B")["id"], "ok")
        self.assertEqual(self.db.count_intern_completed_observations(1), 0)
        self.assertEqual(self.db.count_intern_completed_observations(2), 1)


class TestStorageWorkReview(unittest.TestCase):
    def setUp(self):
        self.db = fresh_storage()

    def test_submit_reject_then_accept(self):
        uid = 2002
        w = self.db.add_intern_work(uid, file_name="work.zip", stored_path="/tmp/work.zip", size_bytes=10)
        self.assertEqual(w["status"], "submitted")

        rejected = self.db.review_intern_work(w["id"], reviewer_user_id=9, status="rejected", comment="Доработай задание 3")
        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["reviewer_comment"], "Доработай задание 3")

        # Invalid status is a no-op.
        self.assertEqual(self.db.review_intern_work(w["id"], 9, "maybe"), {})

        accepted = self.db.review_intern_work(w["id"], reviewer_user_id=9, status="accepted")
        self.assertEqual(accepted["status"], "accepted")
        # Stage machine should now move past trial_work.
        s = it.compute_intern_stage("trial_allowed", work_status=accepted["status"])
        self.assertEqual(s["stage"], it.STAGE_DEMO_BOOKING)


class TestStorageDemoBooking(unittest.TestCase):
    def setUp(self):
        self.db = fresh_storage()

    def test_booking_lifecycle(self):
        uid = 3003
        b = self.db.add_intern_demo_booking(uid, supervisor_user_id=7, demo_date="2026-06-25", demo_time="17:00", location="Кульман 1/1")
        self.assertEqual(b["status"], "requested")
        for st in ["approved", "conducted", "passed"]:
            row = self.db.review_intern_demo_booking(b["id"], reviewer_user_id=7, status=st)
            self.assertEqual(row["status"], st)
        # Unknown verdict is rejected.
        self.assertEqual(self.db.review_intern_demo_booking(b["id"], 7, "promoted"), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)

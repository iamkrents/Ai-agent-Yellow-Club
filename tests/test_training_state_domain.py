"""Tests for v7.1.8 — training_state_domain pure resolver.

Covers normalization of MoyKlass join/subscription statuses into
active/paused/finished/unknown, using real confirmed join status IDs
(2, 99046, 1, 4, 5, 49850, 49851) and subscription statusId=3 (frozen).

Pure module: no MoyKlass/HTTP/DB access. Run offline:
    python -m unittest tests.test_training_state_domain -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from training_state_domain import (
    resolve_training_state,
    unavailable_training_state_result,
    training_reason_message,
    STATE_ACTIVE,
    STATE_PAUSED,
    STATE_FINISHED,
    STATE_UNKNOWN,
    REASON_CLIENT_TRAINING_PAUSED,
    REASON_TRAINING_SUBSCRIPTION_FROZEN,
    REASON_CLIENT_TRAINING_FINISHED,
    REASON_TRAINING_JOIN_COMPLETED,
    REASON_TRAINING_SUBSCRIPTION_NOT_FOUND,
    REASON_TRAINING_SUBSCRIPTION_CLASS_UNKNOWN,
    REASON_TRAINING_JOIN_NOT_FOUND,
    REASON_TRAINING_JOIN_STATUS_REVIEW,
    REASON_TRAINING_JOIN_STATUS_UNKNOWN,
    REASON_TRAINING_JOIN_STATUS_AMBIGUOUS,
    REASON_TRAINING_STATE_UNAVAILABLE,
)

NOW = "2026-07-28T12:00:00"


def _sub(sub_id="SUB1", status_id="2", main_class_id="690395", class_ids=None):
    d = {"id": sub_id, "statusId": status_id, "mainClassId": main_class_id}
    if class_ids is not None:
        d["classIds"] = class_ids
    else:
        d["classIds"] = [main_class_id] if main_class_id else []
    return d


def _join(join_id="J1", class_id="690395", status_id="2"):
    return {"id": join_id, "classId": class_id, "statusId": status_id}


SAFE_RESULT_KEYS = {
    "state", "reason_code", "mk_user_id", "mk_user_subscription_id",
    "subscription_status_id", "matched_class_ids", "matched_join_ids",
    "matched_join_status_ids", "checked_at",
}


class TestJoinStatusNormalization(unittest.TestCase):
    def test_01_active_join_status_2(self):
        r = resolve_training_state("9748998", "SUB1", [_sub()], [_join(status_id="2")], NOW)
        self.assertEqual(r["state"], STATE_ACTIVE)
        self.assertIsNone(r["reason_code"])

    def test_02_paused_join_status_99046(self):
        r = resolve_training_state("9748998", "SUB1", [_sub()], [_join(status_id="99046")], NOW)
        self.assertEqual(r["state"], STATE_PAUSED)
        self.assertEqual(r["reason_code"], REASON_CLIENT_TRAINING_PAUSED)

    def test_03_finished_join_status_1(self):
        r = resolve_training_state("9748998", "SUB1", [_sub()], [_join(status_id="1")], NOW)
        self.assertEqual(r["state"], STATE_FINISHED)
        self.assertEqual(r["reason_code"], REASON_CLIENT_TRAINING_FINISHED)

    def test_04_completed_moved_join_status_4(self):
        r = resolve_training_state("9748998", "SUB1", [_sub()], [_join(status_id="4")], NOW)
        self.assertEqual(r["state"], STATE_FINISHED)
        self.assertEqual(r["reason_code"], REASON_TRAINING_JOIN_COMPLETED)

    def test_05_review_status_49850(self):
        r = resolve_training_state("9748998", "SUB1", [_sub()], [_join(status_id="49850")], NOW)
        self.assertEqual(r["state"], STATE_UNKNOWN)
        self.assertEqual(r["reason_code"], REASON_TRAINING_JOIN_STATUS_REVIEW)

    def test_06_review_status_5(self):
        r = resolve_training_state("9748998", "SUB1", [_sub()], [_join(status_id="5")], NOW)
        self.assertEqual(r["state"], STATE_UNKNOWN)
        self.assertEqual(r["reason_code"], REASON_TRAINING_JOIN_STATUS_REVIEW)

    def test_07_review_status_49851(self):
        r = resolve_training_state("9748998", "SUB1", [_sub()], [_join(status_id="49851")], NOW)
        self.assertEqual(r["state"], STATE_UNKNOWN)
        self.assertEqual(r["reason_code"], REASON_TRAINING_JOIN_STATUS_REVIEW)

    def test_08_unknown_join_status(self):
        r = resolve_training_state("9748998", "SUB1", [_sub()], [_join(status_id="777777")], NOW)
        self.assertEqual(r["state"], STATE_UNKNOWN)
        self.assertEqual(r["reason_code"], REASON_TRAINING_JOIN_STATUS_UNKNOWN)


class TestSubscriptionLevel(unittest.TestCase):
    def test_09_frozen_subscription_overrides_active_join(self):
        r = resolve_training_state(
            "9748998", "SUB1", [_sub(status_id="3")], [_join(status_id="2")], NOW,
        )
        self.assertEqual(r["state"], STATE_PAUSED)
        self.assertEqual(r["reason_code"], REASON_TRAINING_SUBSCRIPTION_FROZEN)
        self.assertEqual(r["subscription_status_id"], "3")

    def test_10_subscription_not_found(self):
        r = resolve_training_state("9748998", "MISSING_SUB", [_sub(sub_id="SUB1")], [_join()], NOW)
        self.assertEqual(r["state"], STATE_UNKNOWN)
        self.assertEqual(r["reason_code"], REASON_TRAINING_SUBSCRIPTION_NOT_FOUND)

    def test_11_missing_main_class_and_class_ids(self):
        sub = {"id": "SUB1", "statusId": "2"}  # no mainClassId, no classIds
        r = resolve_training_state("9748998", "SUB1", [sub], [_join()], NOW)
        self.assertEqual(r["state"], STATE_UNKNOWN)
        self.assertEqual(r["reason_code"], REASON_TRAINING_SUBSCRIPTION_CLASS_UNKNOWN)

    def test_12_join_not_found(self):
        r = resolve_training_state(
            "9748998", "SUB1", [_sub(main_class_id="690395", class_ids=["690395"])],
            [_join(class_id="638027", status_id="49850")], NOW,
        )
        self.assertEqual(r["state"], STATE_UNKNOWN)
        self.assertEqual(r["reason_code"], REASON_TRAINING_JOIN_NOT_FOUND)
        self.assertEqual(r["matched_class_ids"], ["690395"])


class TestClassIdCollection(unittest.TestCase):
    def test_13_main_class_id_match(self):
        r = resolve_training_state(
            "9748998", "SUB1", [_sub(main_class_id="690395", class_ids=[])],
            [_join(class_id="690395", status_id="2")], NOW,
        )
        self.assertEqual(r["state"], STATE_ACTIVE)
        self.assertIn("690395", r["matched_class_ids"])

    def test_14_class_ids_list_match(self):
        r = resolve_training_state(
            "9748998", "SUB1", [_sub(main_class_id="", class_ids=["638027"])],
            [_join(class_id="638027", status_id="99046")], NOW,
        )
        self.assertEqual(r["state"], STATE_PAUSED)

    def test_15_duplicate_class_ids_removed(self):
        sub = _sub(main_class_id="690395", class_ids=["690395", "690395"])
        r = resolve_training_state("9748998", "SUB1", [sub], [_join(class_id="690395")], NOW)
        self.assertEqual(r["matched_class_ids"], ["690395"])

    def test_16_numeric_string_id_normalization(self):
        sub = {"id": 18012324, "statusId": 2, "mainClassId": 690395, "classIds": [690395]}
        join = {"id": 10463724, "classId": 690395, "statusId": 2}
        r = resolve_training_state(9748998, 18012324, [sub], [join], NOW)
        self.assertEqual(r["state"], STATE_ACTIVE)
        self.assertEqual(r["mk_user_subscription_id"], "18012324")


class TestMultipleJoins(unittest.TestCase):
    def test_17_several_joins_all_active(self):
        sub = _sub(main_class_id="690395", class_ids=["690395", "690396"])
        joins = [_join(join_id="J1", class_id="690395", status_id="2"),
                 _join(join_id="J2", class_id="690396", status_id="2")]
        r = resolve_training_state("U", "SUB1", [sub], joins, NOW)
        self.assertEqual(r["state"], STATE_ACTIVE)

    def test_18_several_joins_all_paused(self):
        sub = _sub(main_class_id="690395", class_ids=["690395", "690396"])
        joins = [_join(join_id="J1", class_id="690395", status_id="99046"),
                 _join(join_id="J2", class_id="690396", status_id="99046")]
        r = resolve_training_state("U", "SUB1", [sub], joins, NOW)
        self.assertEqual(r["state"], STATE_PAUSED)

    def test_19_mixed_active_paused_ambiguous(self):
        sub = _sub(main_class_id="690395", class_ids=["690395", "690396"])
        joins = [_join(join_id="J1", class_id="690395", status_id="2"),
                 _join(join_id="J2", class_id="690396", status_id="99046")]
        r = resolve_training_state("U", "SUB1", [sub], joins, NOW)
        self.assertEqual(r["state"], STATE_UNKNOWN)
        self.assertEqual(r["reason_code"], REASON_TRAINING_JOIN_STATUS_AMBIGUOUS)

    def test_20_mixed_finished_active_ambiguous(self):
        sub = _sub(main_class_id="690395", class_ids=["690395", "690396"])
        joins = [_join(join_id="J1", class_id="690395", status_id="1"),
                 _join(join_id="J2", class_id="690396", status_id="2")]
        r = resolve_training_state("U", "SUB1", [sub], joins, NOW)
        self.assertEqual(r["state"], STATE_UNKNOWN)
        self.assertEqual(r["reason_code"], REASON_TRAINING_JOIN_STATUS_AMBIGUOUS)

    def test_21_join_from_unrelated_class_ignored(self):
        sub = _sub(main_class_id="690395", class_ids=["690395"])
        joins = [_join(join_id="J1", class_id="690395", status_id="2"),
                 _join(join_id="J2", class_id="999999", status_id="99046")]
        r = resolve_training_state("U", "SUB1", [sub], joins, NOW)
        self.assertEqual(r["state"], STATE_ACTIVE)
        self.assertNotIn("J2", r["matched_join_ids"])

    def test_22_paused_join_other_class_does_not_block_active_invoice(self):
        # Client has one active subscription for class A, and a separately
        # paused join for unrelated class B. The invoice for subscription A
        # must resolve active, unaffected by class B's paused join.
        sub_a = _sub(sub_id="SUB_A", main_class_id="A1", class_ids=["A1"])
        joins = [_join(join_id="JA", class_id="A1", status_id="2"),
                 _join(join_id="JB", class_id="B1", status_id="99046")]
        r = resolve_training_state("U", "SUB_A", [sub_a], joins, NOW)
        self.assertEqual(r["state"], STATE_ACTIVE)

    def test_23_active_join_other_class_does_not_unblock_paused_invoice(self):
        sub_b = _sub(sub_id="SUB_B", main_class_id="B1", class_ids=["B1"])
        joins = [_join(join_id="JA", class_id="A1", status_id="2"),
                 _join(join_id="JB", class_id="B1", status_id="99046")]
        r = resolve_training_state("U", "SUB_B", [sub_b], joins, NOW)
        self.assertEqual(r["state"], STATE_PAUSED)


class TestSafety(unittest.TestCase):
    def test_24_no_personal_data_in_result(self):
        sub = _sub()
        sub["comment"] = "SECRET COMMENT"
        sub["price"] = 239.0
        join = _join()
        join["comment"] = "SECRET JOIN COMMENT"
        r = resolve_training_state("9748998", "SUB1", [sub], [join], NOW)
        self.assertEqual(set(r.keys()), SAFE_RESULT_KEYS)
        dumped = str(r)
        self.assertNotIn("SECRET", dumped)
        self.assertNotIn("239.0", dumped)

    def test_unavailable_result_is_unknown_and_fail_closed(self):
        r = unavailable_training_state_result("9748998", "SUB1", NOW)
        self.assertEqual(r["state"], STATE_UNKNOWN)
        self.assertEqual(r["reason_code"], REASON_TRAINING_STATE_UNAVAILABLE)

    def test_message_mapping_never_returns_raw_code_as_only_text(self):
        msg = training_reason_message(REASON_CLIENT_TRAINING_PAUSED)
        self.assertNotEqual(msg, REASON_CLIENT_TRAINING_PAUSED)
        self.assertIn("приостановлено", msg)

    def test_message_mapping_unknown_code_has_fallback(self):
        msg = training_reason_message("some_future_reason_code")
        self.assertTrue(msg)
        self.assertNotEqual(msg, "some_future_reason_code")

    def test_no_subscription_id_is_unknown(self):
        r = resolve_training_state("9748998", "", [_sub()], [_join()], NOW)
        self.assertEqual(r["state"], STATE_UNKNOWN)
        self.assertEqual(r["reason_code"], REASON_TRAINING_SUBSCRIPTION_NOT_FOUND)


if __name__ == "__main__":
    unittest.main()

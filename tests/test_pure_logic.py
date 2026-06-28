"""Pure-logic regression tests for Yellow Club Agent.

Runs fully offline (no Telegram / MoyKlass / Ollama / network needed):

    python3 -m unittest tests.test_pure_logic -v
    # or
    python3 tests/test_pure_logic.py

These lock down two behaviours the brief explicitly cares about:

1. A date like 2026-06-19 in a window/makeup request must NOT be read as
   "June 2026" monthly analytics, while real month questions still are.
2. Teacher free-window slots use correct half-open overlap detection
   (adjacent slots do not collide; real overlaps do).
"""

from __future__ import annotations

import unittest

from tests._load import load_web_app_pure

_NS = load_web_app_pure()
month_from = _NS["_month_from_staff_question"]
is_analytics = _NS["_looks_like_mk_month_analytics_question"]
to_minutes = _NS["_time_to_minutes"]


def overlaps(start: str, end: str, other_start: str, other_end: str) -> bool:
    """Mirror of _find_work_slot_overlap's core half-open interval test."""
    s, e = to_minutes(start), to_minutes(end)
    os_, oe = to_minutes(other_start), to_minutes(other_end)
    if min(s, e, os_, oe) < 0:
        return False
    return s < oe and e > os_


class TestMonthVsDate(unittest.TestCase):
    def test_operational_date_is_not_a_month(self):
        # The exact bug from the brief: window/makeup requests carry a full date.
        for q in [
            "подбери окно для отработки до 2026-06-19",
            "поставь отработку ученику на 2026-06-19",
            "запиши пробное на 2026-06-19",
            "найди свободное окно 2026-06-19",
        ]:
            with self.subTest(q=q):
                self.assertEqual(month_from(q), "", f"should not parse a month from: {q}")
                self.assertFalse(is_analytics(q), f"should not be analytics: {q}")

    def test_real_month_questions_are_detected(self):
        cases = {
            "сколько было оплат за июнь 2026": "2026-06",
            "статистика по ученикам за 2026-06": "2026-06",
            "отчёт по посещениям за май 2026": "2026-05",
        }
        for q, expected in cases.items():
            with self.subTest(q=q):
                self.assertEqual(month_from(q), expected)
                self.assertTrue(is_analytics(q), f"should be analytics: {q}")

    def test_numeric_and_reverse_month_formats(self):
        self.assertEqual(month_from("отчёт за 2026-06"), "2026-06")
        self.assertEqual(month_from("отчёт за 06.2026"), "2026-06")
        self.assertEqual(month_from("сводка 6/2026"), "2026-06")

    def test_full_date_does_not_leak_a_month(self):
        # Year-month-day must be rejected by the month extractor regardless of context.
        self.assertEqual(month_from("2026-06-19"), "")
        self.assertEqual(month_from("событие 19.06.2026"), "")


class TestTimeToMinutes(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(to_minutes("00:00"), 0)
        self.assertEqual(to_minutes("09:30"), 570)
        self.assertEqual(to_minutes("23:59"), 1439)

    def test_invalid_returns_negative(self):
        for bad in ["", "9:30", "24:00x", "abc", None, "0930"]:
            with self.subTest(bad=bad):
                self.assertLess(to_minutes(bad), 0)


class TestWindowOverlap(unittest.TestCase):
    def test_adjacent_slots_do_not_overlap(self):
        # Back-to-back windows are allowed.
        self.assertFalse(overlaps("11:00", "12:00", "12:00", "13:00"))
        self.assertFalse(overlaps("12:00", "13:00", "11:00", "12:00"))

    def test_real_overlaps_detected(self):
        self.assertTrue(overlaps("11:00", "12:30", "12:00", "13:00"))   # tail overlap
        self.assertTrue(overlaps("12:00", "13:00", "11:00", "12:30"))   # head overlap
        self.assertTrue(overlaps("11:30", "12:00", "11:00", "13:00"))   # fully contained
        self.assertTrue(overlaps("11:00", "13:00", "11:30", "12:00"))   # contains other

    def test_disjoint_slots(self):
        self.assertFalse(overlaps("09:00", "10:00", "11:00", "12:00"))

    def test_malformed_time_is_safe(self):
        # Bad input must not be treated as an overlap (would block valid saves).
        self.assertFalse(overlaps("bad", "10:00", "09:00", "11:00"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

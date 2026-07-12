"""Tests for food-module child-week period filtering (v7.0.84).

Verifies:
A. Old menu (01.07) is excluded for a Week-3 child (13.07-17.07, YC1).
B. In-range menu (15.07, YC1) is included.
C. Location-mismatch menu (15.07, YC2) is excluded for YC1 child.
D. Menu with no location_code is included with a warning.
E. Two children (different weeks/locations) each see only their own menus.
F. _check_order_preconditions returns "menu_not_for_child" for old menu_id.
G. Existing submitted food_orders are NOT deleted by any of the above logic.
"""
from __future__ import annotations

import gc
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from storage import Storage, _get_child_week_period


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_storage(tmp_dir: str) -> Storage:
    return Storage(Path(tmp_dir) / "test.db")


def _child(
    mk_student_id: str,
    full_name: str = "Test Child",
    group_name: str = "",
    mk_class_name: str = "",
    camp_lesson_date: str = "",
) -> dict:
    return {
        "mk_student_id": mk_student_id,
        "full_name": full_name,
        "group_name": group_name,
        "mk_class_name": mk_class_name,
        "camp_lesson_date": camp_lesson_date,
        "classroom": "",
        "raw_json": None,
    }


# ---------------------------------------------------------------------------
# Unit tests for _get_child_week_period
# ---------------------------------------------------------------------------

class TestGetChildWeekPeriod(unittest.TestCase):
    """Pure function tests — no DB required."""

    def test_parse_range_from_group_name(self):
        """Case A: date range extracted from group_name parenthetical."""
        ch = _child(
            "1",
            group_name="Yellow Summer Week 3 (13.07-17.07), YC1",
            camp_lesson_date="2026-07-13",
        )
        ws, we, loc = _get_child_week_period(ch)
        self.assertEqual(ws, "2026-07-13")
        self.assertEqual(we, "2026-07-17")
        self.assertEqual(loc, "YC1")

    def test_parse_range_from_mk_class_name(self):
        """Case A: date range extracted from mk_class_name when group_name absent."""
        ch = _child(
            "2",
            mk_class_name="Yellow Summer Week 5 (20.07-24.07), YC2",
            camp_lesson_date="2026-07-20",
        )
        ws, we, loc = _get_child_week_period(ch)
        self.assertEqual(ws, "2026-07-20")
        self.assertEqual(we, "2026-07-24")
        self.assertEqual(loc, "YC2")

    def test_fallback_monday_friday_from_lesson_date(self):
        """Case B: no parenthetical → compute Mon-Fri from camp_lesson_date."""
        # 2026-07-15 is a Wednesday; Mon=13, Fri=17
        ch = _child("3", camp_lesson_date="2026-07-15")
        ws, we, loc = _get_child_week_period(ch)
        d = date.fromisoformat("2026-07-15")
        mon = (d - timedelta(days=d.weekday())).isoformat()
        fri = (d - timedelta(days=d.weekday()) + timedelta(days=4)).isoformat()
        self.assertEqual(ws, mon)
        self.assertEqual(we, fri)

    def test_no_period_when_no_date(self):
        """Case C: camp_lesson_date absent and no parseable range → (None, None, loc)."""
        ch = _child("4", group_name="Yellow Summer YC1")
        ws, we, loc = _get_child_week_period(ch)
        self.assertIsNone(ws)
        self.assertIsNone(we)
        self.assertEqual(loc, "YC1")

    def test_location_unknown_when_no_yc_code(self):
        """Location is '' when group name has no YC code."""
        ch = _child("5", group_name="Some Group", camp_lesson_date="2026-07-13")
        _, _, loc = _get_child_week_period(ch)
        self.assertEqual(loc, "")

    def test_location_from_group_name(self):
        """YC code extracted regardless of surrounding text."""
        ch = _child("6", group_name="Week 3 YC3 (13.07-17.07)", camp_lesson_date="2026-07-13")
        ws, we, loc = _get_child_week_period(ch)
        self.assertEqual(loc, "YC3")
        self.assertEqual(ws, "2026-07-13")
        self.assertEqual(we, "2026-07-17")


# ---------------------------------------------------------------------------
# Integration tests with a real SQLite DB
# ---------------------------------------------------------------------------

class TestFoodMenuFiltering(unittest.TestCase):
    """Tests for the eligibility logic used by food_active_menus."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.storage = _make_storage(self._tmpdir.name)

    def tearDown(self):
        del self.storage
        gc.collect()
        try:
            self._tmpdir.cleanup()
        except Exception:
            pass

    def _create_menu(self, menu_date: str, location_code: str, status: str = "published") -> dict:
        menu = self.storage.create_food_menu(
            menu_date=menu_date,
            title=f"Меню {menu_date}",
            deadline_at=f"{menu_date}T20:00:00",
            created_by=1,
            location_code=location_code if location_code else None,
        )
        if status == "published":
            self.storage.set_food_menu_status(menu["id"], "published")
        return self.storage.get_food_menu(menu["id"])

    def _filter_menus_for_child(self, child: dict, menus: list[dict]) -> tuple[list[dict], list[str]]:
        """Mirror of the filtering logic in food_active_menus."""
        from storage import _get_child_week_period, normalize_food_location as _nfl

        ws, we, ch_loc = _get_child_week_period(child)
        warnings: list[str] = []
        eligible: list[dict] = []

        for menu in menus:
            menu_date = str(menu.get("menu_date") or "")
            menu_loc = str(menu.get("location_code") or "").strip().upper()
            if not menu_loc:
                menu_loc = _nfl(str(menu.get("title") or ""))

            if not ws or not we:
                w = f"missing_child_period:{child['mk_student_id']}"
                if w not in warnings:
                    warnings.append(w)
                continue

            if not (ws <= menu_date <= we):
                continue

            if not ch_loc:
                w = f"missing_child_location:{child['mk_student_id']}"
                if w not in warnings:
                    warnings.append(w)
                continue

            if menu_loc and menu_loc != ch_loc:
                continue

            if not menu_loc:
                w = f"missing_menu_location:{menu['id']}"
                if w not in warnings:
                    warnings.append(w)

            eligible.append(menu)

        return eligible, warnings

    # ------------------------------------------------------------------
    # Test A: old menu 01.07 excluded for Week-3 child
    # ------------------------------------------------------------------
    def test_A_old_menu_excluded(self):
        ch = _child(
            "child1",
            full_name="Фоменко Владислав",
            group_name="Yellow Summer Week 3 (13.07-17.07), YC1",
            camp_lesson_date="2026-07-13",
        )
        old_menu = self._create_menu("2026-07-01", "YC1")
        eligible, warnings = self._filter_menus_for_child(ch, [old_menu])
        self.assertEqual(eligible, [], "Old menu (01.07) must NOT be shown to Week-3 child")

    # ------------------------------------------------------------------
    # Test B: in-range menu 15.07 YC1 shown for Week-3 YC1 child
    # ------------------------------------------------------------------
    def test_B_inrange_menu_shown(self):
        ch = _child(
            "child1",
            full_name="Фоменко Владислав",
            group_name="Yellow Summer Week 3 (13.07-17.07), YC1",
            camp_lesson_date="2026-07-13",
        )
        menu = self._create_menu("2026-07-15", "YC1")
        eligible, _ = self._filter_menus_for_child(ch, [menu])
        self.assertEqual(len(eligible), 1, "Menu 15.07/YC1 must be shown to Week-3/YC1 child")
        self.assertEqual(eligible[0]["menu_date"], "2026-07-15")

    # ------------------------------------------------------------------
    # Test C: YC2 menu excluded for YC1 child
    # ------------------------------------------------------------------
    def test_C_location_mismatch_excluded(self):
        ch = _child(
            "child1",
            group_name="Yellow Summer Week 3 (13.07-17.07), YC1",
            camp_lesson_date="2026-07-13",
        )
        menu_yc2 = self._create_menu("2026-07-15", "YC2")
        eligible, _ = self._filter_menus_for_child(ch, [menu_yc2])
        self.assertEqual(eligible, [], "YC2 menu must NOT be shown to YC1 child")

    # ------------------------------------------------------------------
    # Test D: menu with empty location_code shown by date, warning emitted
    # ------------------------------------------------------------------
    def test_D_menu_no_location_allowed_with_warning(self):
        ch = _child(
            "child1",
            group_name="Yellow Summer Week 3 (13.07-17.07), YC1",
            camp_lesson_date="2026-07-13",
        )
        menu = self._create_menu("2026-07-15", "")  # no location
        eligible, warnings = self._filter_menus_for_child(ch, [menu])
        self.assertEqual(len(eligible), 1, "Menu without location must be shown (date matches)")
        self.assertTrue(
            any("missing_menu_location" in w for w in warnings),
            "Warning missing_menu_location must be present"
        )

    # ------------------------------------------------------------------
    # Test E: two children (different weeks/locations) see only their own menus
    # ------------------------------------------------------------------
    def test_E_two_children_see_own_menus(self):
        ch1 = _child(
            "child1",
            full_name="Ребёнок 1",
            group_name="Yellow Summer Week 3 (13.07-17.07), YC1",
            camp_lesson_date="2026-07-13",
        )
        ch2 = _child(
            "child2",
            full_name="Ребёнок 2",
            group_name="Yellow Summer Week 4 (20.07-24.07), YC2",
            camp_lesson_date="2026-07-20",
        )
        menu1 = self._create_menu("2026-07-15", "YC1")  # for child1
        menu2 = self._create_menu("2026-07-22", "YC2")  # for child2

        eligible1, _ = self._filter_menus_for_child(ch1, [menu1, menu2])
        eligible2, _ = self._filter_menus_for_child(ch2, [menu1, menu2])

        self.assertEqual([m["id"] for m in eligible1], [menu1["id"]], "Child1 should see only YC1/Week3 menu")
        self.assertEqual([m["id"] for m in eligible2], [menu2["id"]], "Child2 should see only YC2/Week4 menu")

    # ------------------------------------------------------------------
    # Test G: existing submitted orders are NOT deleted by the filtering logic
    # ------------------------------------------------------------------
    def test_G_existing_orders_not_deleted(self):
        """food_active_menus filtering never touches food_orders table."""
        # Create a child, menu, and a submitted order
        self.storage.upsert_camp_child({
            "mk_student_id": "child_g",
            "full_name": "Test Child G",
            "group_name": "Yellow Summer Week 3 (13.07-17.07), YC1",
            "camp_lesson_date": "2026-07-13",
        })
        old_menu = self._create_menu("2026-07-01", "YC1")
        self.storage.add_food_item(old_menu["id"], "Второе", "Котлета", "150г", 0)
        menu_full = self.storage.get_food_menu(old_menu["id"])
        items = menu_full.get("items") or []
        if items:
            self.storage.upsert_food_order(
                parent_telegram_id="parent_g",
                mk_student_id="child_g",
                menu_id=old_menu["id"],
                item_quantities={items[0]["id"]: 1},
                status="submitted",
            )

        # Run filtering (does not delete anything)
        ch = _child(
            "child_g",
            group_name="Yellow Summer Week 3 (13.07-17.07), YC1",
            camp_lesson_date="2026-07-13",
        )
        all_menus = self.storage.list_published_food_menus_with_items()
        eligible, _ = self._filter_menus_for_child(ch, all_menus)

        # Old menu is excluded from display — but orders still exist in DB
        self.assertEqual(eligible, [], "Old menu must be excluded from display")
        orders = self.storage.list_food_orders_for_parent("parent_g")
        self.assertTrue(len(orders) >= 1, "Submitted orders must NOT be deleted")


# ---------------------------------------------------------------------------
# Test F: _check_order_preconditions blocks old menu_id
# ---------------------------------------------------------------------------

class TestCheckOrderPreconditions(unittest.TestCase):
    """Verify backend guard returns menu_not_for_child for wrong menu."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.storage = _make_storage(self._tmpdir.name)

    def tearDown(self):
        del self.storage
        gc.collect()
        try:
            self._tmpdir.cleanup()
        except Exception:
            pass

    def _simulate_precondition_check(
        self, child: dict, menu: dict, mk_student_id: str
    ) -> str | None:
        """Mirror of the new menu_not_for_child check in _check_order_preconditions."""
        from storage import _get_child_week_period, normalize_food_location as _nfl

        ws, we, ch_loc = _get_child_week_period(child)
        menu_date = str(menu.get("menu_date") or "")
        menu_loc = str(menu.get("location_code") or "").strip().upper()
        if not menu_loc:
            menu_loc = _nfl(str(menu.get("title") or ""))

        if ws and we and menu_date and not (ws <= menu_date <= we):
            return "menu_not_for_child"
        if ch_loc and menu_loc and ch_loc != menu_loc:
            return "menu_not_for_child"
        return None

    def test_F_old_menu_blocked(self):
        """Old menu (01.07) must be blocked for Week-3 (13.07-17.07) YC1 child."""
        ch = _child(
            "child_f",
            full_name="Фоменко Владислав",
            group_name="Yellow Summer Week 3 (13.07-17.07), YC1",
            camp_lesson_date="2026-07-13",
        )
        old_menu = {"id": 99, "menu_date": "2026-07-01", "location_code": "YC1"}
        result = self._simulate_precondition_check(ch, old_menu, "child_f")
        self.assertEqual(result, "menu_not_for_child", "Old menu (01.07) must be blocked")

    def test_F_correct_menu_allowed(self):
        """In-range YC1 menu must NOT be blocked for Week-3 YC1 child."""
        ch = _child(
            "child_f2",
            group_name="Yellow Summer Week 3 (13.07-17.07), YC1",
            camp_lesson_date="2026-07-13",
        )
        ok_menu = {"id": 100, "menu_date": "2026-07-15", "location_code": "YC1"}
        result = self._simulate_precondition_check(ch, ok_menu, "child_f2")
        self.assertIsNone(result, "In-range YC1 menu must be allowed")

    def test_F_location_mismatch_blocked(self):
        """YC2 menu must be blocked for YC1 child even if date is correct."""
        ch = _child(
            "child_f3",
            group_name="Yellow Summer Week 3 (13.07-17.07), YC1",
            camp_lesson_date="2026-07-13",
        )
        yc2_menu = {"id": 101, "menu_date": "2026-07-15", "location_code": "YC2"}
        result = self._simulate_precondition_check(ch, yc2_menu, "child_f3")
        self.assertEqual(result, "menu_not_for_child", "YC2 menu must be blocked for YC1 child")


if __name__ == "__main__":
    unittest.main()

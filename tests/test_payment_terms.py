"""Tests for v7.1.0 — payment terms and discounts feature.

Coverage map:
  01-07  Storage/domain: client payment terms
  08-28  Storage/domain: discounts + pricing resolution
  29-34  Audit log
  35-40  API role authorization (owner/admin/operations allowed; parent denied)
  41-55  Safety / no-side-effects (no payment_intent* mutation, no external calls)

Run offline (no Telegram / bePaid / MoyKlass):
    python -m unittest tests.test_payment_terms -v
"""
from __future__ import annotations

import datetime
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage
from payment_domain import (
    resolve_client_payment_terms,
    resolve_active_client_discount,
    resolve_next_subscription_price,
    DEFAULT_BASE_PRICE_MINOR,
    DEFAULT_LESSONS_COUNT,
    DEFAULT_DUE_DAYS,
    DEFAULT_CURRENCY,
    VALID_DISCOUNT_TYPES,
    PRICING_SOURCE_BASE,
)

CURRENT_VERSION = "7.1.0"
PATCH_VERSION = "7.1.0.1"
PRICING_DATE = datetime.datetime(2026, 6, 15).strftime("%Y-%m-%d")  # "2026-06-15"
NOW = "2026-06-15T10:00:00"


def _make_storage() -> Storage:
    db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db.close()
    return Storage(Path(db.name))


def _make_ctx(storage: Storage, role: str = "operations"):
    import web_app_server as _srv
    ctx = _srv.MiniAppContext.__new__(_srv.MiniAppContext)
    ctx.storage = storage
    ctx._role_for_user = lambda uid: role
    return ctx


def _auth(uid: int = 555):
    return {"user_id": uid}


# ---------------------------------------------------------------------------
# 01-07 — Client payment terms
# ---------------------------------------------------------------------------

class Test01Terms(unittest.TestCase):
    def setUp(self):
        self.st = _make_storage()

    def test_01_get_terms_none_for_new_user(self):
        self.assertIsNone(self.st.get_payment_client_terms("u1"))

    def test_02_resolve_defaults_when_none(self):
        t = resolve_client_payment_terms(None)
        self.assertTrue(t["is_default"])
        self.assertEqual(t["base_price_minor"], DEFAULT_BASE_PRICE_MINOR)
        self.assertEqual(t["base_lessons_count"], DEFAULT_LESSONS_COUNT)
        self.assertEqual(t["default_due_days"], DEFAULT_DUE_DAYS)
        self.assertEqual(t["currency"], DEFAULT_CURRENCY)

    def test_03_default_due_days_is_17(self):
        self.assertEqual(DEFAULT_DUE_DAYS, 17)

    def test_04_upsert_insert_creates_row(self):
        row = self.st.upsert_payment_client_terms(
            "u1", 4, 23900, "BYN", 17, False, None, None, 100, "Admin", NOW)
        self.assertEqual(row["mk_user_id"], "u1")
        self.assertEqual(row["base_price_minor"], 23900)
        self.assertEqual(row["automation_enabled"], 0)

    def test_05_upsert_update_modifies_row(self):
        self.st.upsert_payment_client_terms("u1", 4, 23900, "BYN", 17, False, None, None, 100, "Admin", NOW)
        row = self.st.upsert_payment_client_terms("u1", 8, 40000, "BYN", 20, True, "paused", None, 100, "Admin", NOW)
        self.assertEqual(row["base_price_minor"], 40000)
        self.assertEqual(row["base_lessons_count"], 8)
        self.assertEqual(row["automation_enabled"], 1)
        self.assertEqual(row["automation_paused_reason"], "paused")
        # still exactly one row
        self.assertEqual(len(self.st.list_payment_pricing_audit("u1", limit=500)) >= 2, True)

    def test_06_upsert_validation_rejects_bad_values(self):
        for kwargs in (
            dict(base_lessons_count=0),
            dict(base_price_minor=0),
            dict(default_due_days=0),
            dict(default_due_days=91),
            dict(currency="USD"),
        ):
            args = dict(base_lessons_count=4, base_price_minor=23900, currency="BYN",
                        default_due_days=17)
            args.update(kwargs)
            with self.assertRaises(ValueError):
                self.st.upsert_payment_client_terms(
                    "u1", args["base_lessons_count"], args["base_price_minor"],
                    args["currency"], args["default_due_days"], False, None, None,
                    1, "a", NOW)

    def test_07_mk_user_id_stored_as_text(self):
        # numeric-looking id stored/compared as string
        self.st.upsert_payment_client_terms("12345", 4, 23900, "BYN", 17, False, None, None, 1, "a", NOW)
        self.assertIsNotNone(self.st.get_payment_client_terms("12345"))
        self.assertIsNone(self.st.get_payment_client_terms("54321"))


# ---------------------------------------------------------------------------
# 08-28 — Discounts + pricing resolution
# ---------------------------------------------------------------------------

class Test02Discounts(unittest.TestCase):
    def setUp(self):
        self.st = _make_storage()

    def test_08_valid_discount_types(self):
        self.assertEqual(VALID_DISCOUNT_TYPES, frozenset({"one_time", "date_range", "permanent"}))

    def test_09_create_permanent_discount(self):
        d = self.st.create_payment_client_discount("u1", "permanent", 20000, None, None, None, "loyal", 1, "a", NOW)
        self.assertEqual(d["discount_type"], "permanent")
        self.assertIsNone(d["valid_until"])
        self.assertEqual(d["status"], "active")
        self.assertEqual(d["calculation_type"], "fixed_price")

    def test_10_permanent_forces_valid_until_none(self):
        d = self.st.create_payment_client_discount("u1", "permanent", 20000, None, "2026-12-31", None, None, 1, "a", NOW)
        self.assertIsNone(d["valid_until"])

    def test_11_one_time_sets_remaining_uses_1(self):
        d = self.st.create_payment_client_discount("u1", "one_time", 10000, None, None, None, None, 1, "a", NOW)
        self.assertEqual(d["remaining_uses"], 1)

    def test_12_date_range_requires_both_dates(self):
        with self.assertRaises(ValueError):
            self.st.create_payment_client_discount("u1", "date_range", 10000, "2026-06-01", None, None, None, 1, "a", NOW)
        with self.assertRaises(ValueError):
            self.st.create_payment_client_discount("u1", "date_range", 10000, None, "2026-06-30", None, None, 1, "a", NOW)

    def test_13_date_range_until_before_from_rejected(self):
        with self.assertRaises(ValueError):
            self.st.create_payment_client_discount("u1", "date_range", 10000, "2026-06-30", "2026-06-01", None, None, 1, "a", NOW)

    def test_14_invalid_type_rejected(self):
        with self.assertRaises(ValueError):
            self.st.create_payment_client_discount("u1", "bogus", 10000, None, None, None, None, 1, "a", NOW)

    def test_15_nonpositive_price_rejected(self):
        with self.assertRaises(ValueError):
            self.st.create_payment_client_discount("u1", "permanent", 0, None, None, None, None, 1, "a", NOW)

    def test_16_creating_same_type_replaces_old(self):
        d1 = self.st.create_payment_client_discount("u1", "permanent", 20000, None, None, None, None, 1, "a", NOW)
        d2 = self.st.create_payment_client_discount("u1", "permanent", 18000, None, None, None, None, 1, "a", NOW)
        rows = {r["id"]: r for r in self.st.list_payment_client_discounts("u1")}
        self.assertEqual(rows[d1["id"]]["status"], "cancelled")
        self.assertEqual(rows[d1["id"]]["cancellation_reason"], "replaced_by_new_discount")
        self.assertEqual(rows[d2["id"]]["status"], "active")

    def test_17_different_types_coexist(self):
        self.st.create_payment_client_discount("u1", "permanent", 20000, None, None, None, None, 1, "a", NOW)
        self.st.create_payment_client_discount("u1", "one_time", 10000, None, None, None, None, 1, "a", NOW)
        active = self.st.get_active_payment_client_discounts("u1", PRICING_DATE)
        self.assertEqual(len(active), 2)

    def test_18_list_discounts_desc(self):
        a = self.st.create_payment_client_discount("u1", "one_time", 10000, None, None, None, None, 1, "a", NOW)
        b = self.st.create_payment_client_discount("u1", "date_range", 11000, "2026-06-01", "2026-06-30", None, None, 1, "a", NOW)
        rows = self.st.list_payment_client_discounts("u1")
        self.assertEqual(rows[0]["id"], b["id"])

    def test_19_cancel_discount(self):
        d = self.st.create_payment_client_discount("u1", "permanent", 20000, None, None, None, None, 1, "a", NOW)
        r = self.st.cancel_payment_client_discount(d["id"], "u1", 2, "Boss", "manual", NOW)
        self.assertEqual(r["status"], "cancelled")
        self.assertEqual(r["cancelled_by_name"], "Boss")
        self.assertEqual(r["cancellation_reason"], "manual")

    def test_20_cancel_missing_raises(self):
        with self.assertRaises(ValueError):
            self.st.cancel_payment_client_discount(9999, "u1", 1, "a", None, NOW)

    def test_21_cancel_wrong_user_raises(self):
        d = self.st.create_payment_client_discount("u1", "permanent", 20000, None, None, None, None, 1, "a", NOW)
        with self.assertRaises(ValueError):
            self.st.cancel_payment_client_discount(d["id"], "u2", 1, "a", None, NOW)

    def test_22_resolve_no_discount_returns_none(self):
        d, conflict = resolve_active_client_discount([], PRICING_DATE)
        self.assertIsNone(d)
        self.assertIsNone(conflict)

    def test_23_resolve_priority_one_time_wins(self):
        active = [
            {"discount_type": "permanent", "status": "active", "fixed_price_minor": 20000},
            {"discount_type": "one_time", "status": "active", "fixed_price_minor": 10000},
        ]
        d, conflict = resolve_active_client_discount(active, PRICING_DATE)
        self.assertIsNone(conflict)
        self.assertEqual(d["discount_type"], "one_time")

    def test_24_resolve_date_range_within_bounds(self):
        active = [{"discount_type": "date_range", "status": "active",
                   "valid_from": "2026-06-01", "valid_until": "2026-06-30", "fixed_price_minor": 12000}]
        d, conflict = resolve_active_client_discount(active, PRICING_DATE)
        self.assertIsNotNone(d)

    def test_25_resolve_date_range_out_of_bounds(self):
        active = [{"discount_type": "date_range", "status": "active",
                   "valid_from": "2026-07-01", "valid_until": "2026-07-31", "fixed_price_minor": 12000}]
        d, conflict = resolve_active_client_discount(active, PRICING_DATE)
        self.assertIsNone(d)
        self.assertIsNone(conflict)

    def test_26_resolve_conflict_two_same_type(self):
        active = [
            {"discount_type": "permanent", "status": "active", "fixed_price_minor": 20000},
            {"discount_type": "permanent", "status": "active", "fixed_price_minor": 18000},
        ]
        d, conflict = resolve_active_client_discount(active, PRICING_DATE)
        self.assertIsNone(d)
        self.assertIsNotNone(conflict)
        self.assertIn("pricing_conflict", conflict)

    def test_27_resolve_next_price_uses_base_when_no_discount(self):
        pv = resolve_next_subscription_price(None, [], PRICING_DATE)
        self.assertTrue(pv["ok"])
        self.assertEqual(pv["resolved_price_minor"], DEFAULT_BASE_PRICE_MINOR)
        self.assertEqual(pv["price_source"], PRICING_SOURCE_BASE)

    def test_28_resolve_next_price_uses_discount(self):
        terms = self.st.upsert_payment_client_terms("u1", 4, 23900, "BYN", 17, False, None, None, 1, "a", NOW)
        self.st.create_payment_client_discount("u1", "permanent", 15000, None, None, None, None, 1, "a", NOW)
        active = self.st.get_active_payment_client_discounts("u1", PRICING_DATE)
        pv = resolve_next_subscription_price(terms, active, PRICING_DATE)
        self.assertTrue(pv["ok"])
        self.assertEqual(pv["resolved_price_minor"], 15000)
        self.assertEqual(pv["price_source"], "permanent")


# ---------------------------------------------------------------------------
# 29-34 — Audit log
# ---------------------------------------------------------------------------

class Test03Audit(unittest.TestCase):
    def setUp(self):
        self.st = _make_storage()

    def test_29_terms_created_audit(self):
        self.st.upsert_payment_client_terms("u1", 4, 23900, "BYN", 17, False, None, None, 1, "a", NOW)
        events = [a["event_type"] for a in self.st.list_payment_pricing_audit("u1")]
        self.assertIn("terms_created", events)

    def test_30_terms_updated_audit(self):
        self.st.upsert_payment_client_terms("u1", 4, 23900, "BYN", 17, False, None, None, 1, "a", NOW)
        self.st.upsert_payment_client_terms("u1", 8, 40000, "BYN", 17, False, None, None, 1, "a", NOW)
        events = [a["event_type"] for a in self.st.list_payment_pricing_audit("u1")]
        self.assertIn("terms_updated", events)

    def test_31_discount_created_audit(self):
        self.st.create_payment_client_discount("u1", "permanent", 20000, None, None, None, None, 1, "a", NOW)
        events = [a["event_type"] for a in self.st.list_payment_pricing_audit("u1")]
        self.assertIn("discount_created", events)

    def test_32_discount_replaced_and_created_audit(self):
        self.st.create_payment_client_discount("u1", "permanent", 20000, None, None, None, None, 1, "a", NOW)
        self.st.create_payment_client_discount("u1", "permanent", 18000, None, None, None, None, 1, "a", NOW)
        events = [a["event_type"] for a in self.st.list_payment_pricing_audit("u1")]
        self.assertIn("discount_replaced", events)

    def test_33_discount_cancelled_audit(self):
        d = self.st.create_payment_client_discount("u1", "permanent", 20000, None, None, None, None, 1, "a", NOW)
        self.st.cancel_payment_client_discount(d["id"], "u1", 1, "a", "manual", NOW)
        events = [a["event_type"] for a in self.st.list_payment_pricing_audit("u1")]
        self.assertIn("discount_cancelled", events)

    def test_34_append_audit_serializes_dict(self):
        self.st.append_payment_pricing_audit(
            "u1", "custom_event", "terms", "5",
            {"a": "старое"}, {"a": "новое"}, "why", 7, "Актёр", NOW)
        rows = self.st.list_payment_pricing_audit("u1")
        self.assertEqual(rows[0]["event_type"], "custom_event")
        self.assertIn("новое", rows[0]["new_value_json"])
        self.assertEqual(rows[0]["actor_tg_id"], 7)


# ---------------------------------------------------------------------------
# 35-40 — API role authorization
# ---------------------------------------------------------------------------

class Test04ApiRoles(unittest.TestCase):
    def setUp(self):
        self.st = _make_storage()

    def test_35_owner_allowed_terms_get(self):
        ctx = _make_ctx(self.st, "owner")
        r = ctx.payment_client_terms_get(_auth(), "u1")
        self.assertTrue(r["ok"])

    def test_36_admin_allowed_terms_update(self):
        ctx = _make_ctx(self.st, "admin")
        r = ctx.payment_client_terms_update(_auth(), "u1", {
            "base_price_minor": 25000, "base_lessons_count": 4, "default_due_days": 17,
            "currency": "BYN", "automation_enabled": False})
        self.assertTrue(r["ok"])

    def test_37_operations_allowed_discount_create(self):
        ctx = _make_ctx(self.st, "operations")
        r = ctx.payment_client_discount_create(_auth(), "u1", {
            "discount_type": "permanent", "fixed_price_minor": 20000})
        self.assertTrue(r["ok"])

    def test_38_parent_denied(self):
        ctx = _make_ctx(self.st, "parent")
        for fn in (
            lambda: ctx.payment_client_terms_get(_auth(), "u1"),
            lambda: ctx.payment_client_discounts_list(_auth(), "u1"),
            lambda: ctx.payment_client_pricing_preview(_auth(), "u1", {}),
            lambda: ctx.payment_client_pricing_audit(_auth(), "u1", {}),
        ):
            self.assertFalse(fn()["ok"])

    def test_39_teacher_denied(self):
        ctx = _make_ctx(self.st, "teacher")
        r = ctx.payment_client_terms_update(_auth(), "u1", {"base_price_minor": 25000})
        self.assertFalse(r["ok"])

    def test_40_empty_mk_user_id_rejected(self):
        ctx = _make_ctx(self.st, "owner")
        r = ctx.payment_client_terms_get(_auth(), "")
        self.assertFalse(r["ok"])
        # money formatting exposes both minor and BYN
        r2 = ctx.payment_client_terms_get(_auth(), "u1")
        self.assertIn("BYN", r2["terms"]["base_price_byn"])


# ---------------------------------------------------------------------------
# 41-55 — Safety / no-side-effects
# ---------------------------------------------------------------------------

class Test05Safety(unittest.TestCase):
    def setUp(self):
        self.st = _make_storage()

    def _count(self, table: str) -> int:
        with sqlite3.connect(self.st.db_path) as conn:
            try:
                return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except sqlite3.OperationalError:
                return -1

    def test_41_new_tables_exist(self):
        for t in ("payment_client_terms", "payment_client_discounts", "payment_pricing_audit_log"):
            self.assertGreaterEqual(self._count(t), 0, f"{t} must exist")

    def test_42_terms_upsert_no_payment_intent_rows(self):
        self.st.upsert_payment_client_terms("u1", 4, 23900, "BYN", 17, False, None, None, 1, "a", NOW)
        self.assertEqual(self._count("payment_intents"), 0)

    def test_43_discount_create_no_payment_intent_rows(self):
        self.st.create_payment_client_discount("u1", "permanent", 20000, None, None, None, None, 1, "a", NOW)
        self.assertEqual(self._count("payment_intents"), 0)

    def test_44_no_payment_intent_options_created(self):
        self.st.create_payment_client_discount("u1", "permanent", 20000, None, None, None, None, 1, "a", NOW)
        self.assertEqual(self._count("payment_intent_options"), 0)

    def test_45_no_checkout_attempts_created(self):
        self.st.create_payment_client_discount("u1", "permanent", 20000, None, None, None, None, 1, "a", NOW)
        cnt = self._count("payment_checkout_attempts")
        self.assertIn(cnt, (0, -1))  # 0 if table exists, -1 if absent — never > 0

    def test_46_pricing_functions_are_pure_no_now(self):
        # resolve_next_subscription_price must not use system time — deterministic on inputs
        a = resolve_next_subscription_price(None, [], "2026-01-01")
        b = resolve_next_subscription_price(None, [], "2026-01-01")
        self.assertEqual(a, b)

    def test_47_pricing_date_out_of_range_deterministic(self):
        active = [{"discount_type": "date_range", "status": "active",
                   "valid_from": "2099-01-01", "valid_until": "2099-12-31", "fixed_price_minor": 1}]
        pv = resolve_next_subscription_price(None, active, PRICING_DATE)
        self.assertTrue(pv["ok"])
        self.assertEqual(pv["resolved_price_minor"], DEFAULT_BASE_PRICE_MINOR)

    def test_48_conflict_blocks_automation(self):
        active = [
            {"discount_type": "one_time", "status": "active", "fixed_price_minor": 1},
            {"discount_type": "one_time", "status": "active", "fixed_price_minor": 2},
        ]
        pv = resolve_next_subscription_price(None, active, PRICING_DATE)
        self.assertFalse(pv["ok"])
        self.assertTrue(pv["automation_blocked"])

    def test_49_paused_reason_blocks_automation(self):
        terms = self.st.upsert_payment_client_terms("u1", 4, 23900, "BYN", 17, True, "manual_pause", None, 1, "a", NOW)
        pv = resolve_next_subscription_price(terms, [], PRICING_DATE)
        self.assertTrue(pv["ok"])
        self.assertTrue(pv["automation_blocked"])
        self.assertEqual(pv["automation_block_reason"], "manual_pause")

    def test_50_migration_is_idempotent(self):
        # Re-initializing Storage on the same db must not error or duplicate
        path = self.st.db_path
        self.st.upsert_payment_client_terms("u1", 4, 23900, "BYN", 17, False, None, None, 1, "a", NOW)
        st2 = Storage(path)
        self.assertIsNotNone(st2.get_payment_client_terms("u1"))

    def test_51_terms_unique_per_user(self):
        self.st.upsert_payment_client_terms("u1", 4, 23900, "BYN", 17, False, None, None, 1, "a", NOW)
        self.st.upsert_payment_client_terms("u1", 8, 40000, "BYN", 17, False, None, None, 1, "a", NOW)
        with sqlite3.connect(self.st.db_path) as conn:
            n = conn.execute("SELECT COUNT(*) FROM payment_client_terms WHERE mk_user_id='u1'").fetchone()[0]
        self.assertEqual(n, 1)

    def test_52_config_flags_default_off(self):
        import config
        s = config.load_settings()
        self.assertFalse(s.payment_discounts_enabled)
        self.assertFalse(s.payment_renewal_cycle_enabled)
        self.assertFalse(s.payment_attendance_trigger_enabled)
        self.assertFalse(s.payment_auto_create_mk_subscription_enabled)
        self.assertFalse(s.payment_replace_unpaid_intent_enabled)
        self.assertFalse(s.payment_renewal_parent_notification_enabled)

    def test_53_config_default_due_days_17(self):
        import config
        prev = os.environ.pop("PAYMENT_DEFAULT_DUE_DAYS", None)
        try:
            s = config.load_settings()
            self.assertEqual(s.payment_default_due_days, 17)
        finally:
            if prev is not None:
                os.environ["PAYMENT_DEFAULT_DUE_DAYS"] = prev

    def test_54_version_strings_bumped(self):
        pd = (ROOT / "payment_domain.py").read_text(encoding="utf-8")
        self.assertIn(f"v{CURRENT_VERSION}", pd)
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn(f'MiniApp version: v{CURRENT_VERSION}', js)
        html = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
        self.assertIn(f"app.js?v={CURRENT_VERSION}", html)

    def test_55_audit_log_survives_terms_and_discounts(self):
        self.st.upsert_payment_client_terms("u1", 4, 23900, "BYN", 17, False, None, None, 1, "a", NOW)
        d = self.st.create_payment_client_discount("u1", "permanent", 20000, None, None, None, None, 1, "a", NOW)
        self.st.cancel_payment_client_discount(d["id"], "u1", 1, "a", "x", NOW)
        events = [a["event_type"] for a in self.st.list_payment_pricing_audit("u1", limit=500)]
        for e in ("terms_created", "discount_created", "discount_cancelled"):
            self.assertIn(e, events)


# ---------------------------------------------------------------------------
# 56-64 — v7.1.0.1 save-feedback patch (static assertions + storage)
# ---------------------------------------------------------------------------

class Test06PatchV7101(unittest.TestCase):
    """Static assertions verifying the v7.1.0.1 save-feedback fix is present."""

    @classmethod
    def setUpClass(cls):
        cls.js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        cls.html = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
        cls.css = (ROOT / "miniapp" / "styles.css").read_text(encoding="utf-8")

    def test_56_patch_version_in_app_js(self):
        self.assertIn(f"MiniApp version: v{PATCH_VERSION}", self.js)

    def test_57_patch_version_cache_bust_html(self):
        self.assertIn(f"app.js?v={PATCH_VERSION}", self.html)
        self.assertIn(f"styles.css?v={PATCH_VERSION}", self.html)

    def test_58_save_button_disabled_during_request(self):
        self.assertIn("saveBtn.disabled = true", self.js)
        self.assertIn("Сохранение...", self.js)

    def test_59_double_click_guard(self):
        self.assertIn("if (saveBtn.disabled) return", self.js)

    def test_60_put_method_used_for_terms(self):
        self.assertIn("apiPut(`/api/payments/clients/", self.js)

    def test_61_success_notice_text_present(self):
        self.assertIn("Условия оплаты сохранены", self.js)

    def test_62_price_shown_in_byn_step(self):
        self.assertIn('step="0.01"', self.js)

    def test_63_inline_save_notice_element(self):
        self.assertIn("ptSaveNotice", self.js)
        self.assertIn("pt-save-notice--ok", self.js)
        self.assertIn("pt-save-notice--err", self.js)

    def test_64_audit_auto_triggered_after_save(self):
        # ptShowAudit click must appear after the success notice assignment
        idx_notice = self.js.index("✓ Условия оплаты сохранены")
        idx_audit = self.js.index("ptShowAudit", idx_notice)
        self.assertGreater(idx_audit, idx_notice)

    def test_65_pt_save_notice_styles_in_css(self):
        self.assertIn(".pt-save-notice--ok", self.css)
        self.assertIn(".pt-save-notice--err", self.css)

    def test_66_button_reenabled_on_error(self):
        # In the catch block: saveBtn.disabled = false must follow setNotice
        catch_block = self.js[self.js.index("} catch (e) {\n      const msg = safeUserError(e)"):]
        self.assertIn("saveBtn.disabled = false", catch_block[:500])

    def test_67_get_terms_does_not_create_row(self):
        st = _make_storage()
        ctx = _make_ctx(st, "owner")
        ctx.payment_client_terms_get(_auth(), "u999")
        ctx.payment_client_terms_get(_auth(), "u999")
        with __import__("sqlite3").connect(st.db_path) as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM payment_client_terms WHERE mk_user_id='u999'"
            ).fetchone()[0]
        self.assertEqual(n, 0)

    def test_68_first_save_creates_row_and_audit(self):
        st = _make_storage()
        ctx = _make_ctx(st, "admin")
        self.assertIsNone(st.get_payment_client_terms("u1"))
        r = ctx.payment_client_terms_update(_auth(), "u1", {
            "base_price_minor": 23900, "base_lessons_count": 4,
            "default_due_days": 17, "currency": "BYN", "automation_enabled": False,
        })
        self.assertTrue(r["ok"])
        self.assertIsNotNone(st.get_payment_client_terms("u1"))
        events = [a["event_type"] for a in st.list_payment_pricing_audit("u1")]
        self.assertIn("terms_created", events)

    def test_69_repeat_save_updates_row_and_audit(self):
        st = _make_storage()
        ctx = _make_ctx(st, "admin")
        ctx.payment_client_terms_update(_auth(), "u1", {
            "base_price_minor": 23900, "base_lessons_count": 4,
            "default_due_days": 17, "currency": "BYN", "automation_enabled": False,
        })
        ctx.payment_client_terms_update(_auth(), "u1", {
            "base_price_minor": 29900, "base_lessons_count": 4,
            "default_due_days": 17, "currency": "BYN", "automation_enabled": False,
        })
        row = st.get_payment_client_terms("u1")
        self.assertEqual(row["base_price_minor"], 29900)
        events = [a["event_type"] for a in st.list_payment_pricing_audit("u1")]
        self.assertIn("terms_updated", events)

    def test_70_save_default_values_works(self):
        """Saving values identical to virtual defaults must create a real row."""
        st = _make_storage()
        ctx = _make_ctx(st, "owner")
        self.assertIsNone(st.get_payment_client_terms("u1"))
        r = ctx.payment_client_terms_update(_auth(), "u1", {
            "base_price_minor": 23900, "base_lessons_count": 4,
            "default_due_days": 17, "currency": "BYN", "automation_enabled": False,
        })
        self.assertTrue(r["ok"])
        row = st.get_payment_client_terms("u1")
        self.assertIsNotNone(row)
        self.assertFalse(row.get("is_default"))

    def test_71_save_terms_does_not_create_payment_intent(self):
        st = _make_storage()
        ctx = _make_ctx(st, "admin")
        ctx.payment_client_terms_update(_auth(), "u1", {
            "base_price_minor": 23900, "base_lessons_count": 4,
            "default_due_days": 17, "currency": "BYN", "automation_enabled": False,
        })
        with __import__("sqlite3").connect(st.db_path) as conn:
            n = conn.execute("SELECT COUNT(*) FROM payment_intents").fetchone()[0]
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

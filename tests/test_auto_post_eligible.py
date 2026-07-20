"""Tests for v7.0.96.1 — auto_post_eligible eligibility guard.

Verifies that automatic posting of bePaid-confirmed payments to MoyKlass
is gated by per-item auto_post_eligible flag (INSERT OR IGNORE pattern),
two-level protection (env var + DB setting), and strict status guards.

Historical/existing records must never be auto-posted.
Production record ycpi_202607_22 (eligible=0 after migration) must NOT be posted.

Run offline (no bePaid / MoyKlass / Telegram):
    python -m unittest tests.test_auto_post_eligible -v
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage

CURRENT_VERSION = "7.0.98.2"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


_INV_COUNTER = [8000]


def _next_inv_id() -> int:
    _INV_COUNTER[0] += 1
    return _INV_COUNTER[0]


def _mk_invoice(
    inv_id: int = 0,
    user_id: int = 7850022,
    price: float = 150.0,
    payed: float = 0.0,
) -> dict:
    if inv_id == 0:
        inv_id = _next_inv_id()
    return {
        "id": inv_id,
        "userId": user_id,
        "price": price,
        "payed": payed,
        "comment": "",
        "payUntil": "2026-07-31",
        "userSubscriptionId": 6000 + inv_id,
        "userSubscription": {
            "name": "Подписка",
            "clientName": "Тестовый Ученик",
            "beginDate": "2026-07-01",
        },
    }


class _FakeResult:
    def __init__(self, data, ok=True, status=200, error=""):
        self.data = data
        self.ok = ok
        self.status = status
        self.error = error


def _make_ctx(storage: Storage, invoices: list[dict], *,
              bepaid_auto_post=False, mk_configured=True):
    from web_app_server import MiniAppContext
    ctx = MiniAppContext.__new__(MiniAppContext)
    ctx.storage = storage

    mk = MagicMock()
    mk.is_configured = mk_configured

    def _fake_request(method, path, **kwargs):
        return _FakeResult({"invoices": invoices})
    mk.request = _fake_request

    settings = MagicMock()
    settings.payment_invoice_automation_enabled = True
    settings.bepaid_erip_shop_id = ""
    settings.bepaid_erip_secret_key = ""
    settings.bepaid_acq_shop_id = ""
    settings.bepaid_acq_secret_key = ""
    settings.bepaid_public_base_url = ""
    settings.bepaid_webhook_path_secret = ""
    settings.moyklass_erip_payment_type_id = 55948
    settings.moyklass_acquiring_payment_type_id = 111861
    settings.bepaid_auto_post_to_moyklass = bepaid_auto_post
    ctx.settings = settings
    ctx.moyklass = mk
    return ctx


def _seed_parent_link(st: Storage, mk_user_id: str, parent_tg_id: str, now: str) -> None:
    with st._connect() as conn:
        conn.execute(
            """INSERT INTO client_parent_child_links
               (parent_telegram_user_id, mk_user_id, child_display_name, status,
                linked_at, created_at, updated_at)
               VALUES (?,?,'Test','active',?,?,?)""",
            (parent_tg_id, mk_user_id, now, now, now),
        )


def _enable_automation(
    st: Storage,
    *,
    create: bool = True,
    publish: bool = False,
    post: bool = False,
) -> None:
    st.update_automation_settings(
        discovery_enabled=True,
        create_payment_options_enabled=create,
        publish_to_parent_enabled=publish,
        post_to_moyklass_enabled=post,
        scan_interval_minutes=10,
        updated_by="test",
        now=_now(),
    )


def _seed_intent(
    st: Storage,
    mk_invoice_id: str,
    mk_user_id: int = 7850022,
    status: str = "paid",
    client_visibility: str = "published",
    now: str = "",
    amount_minor: int = 15000,
    paid_amount_minor: int = 15000,
) -> dict:
    now = now or _now()
    intent = st.create_payment_intent({
        "mk_user_id": mk_user_id,
        "student_name": "Тест ycpi_202607_22",
        "amount_minor": amount_minor,
        "amount_byn": amount_minor / 100,
        "currency": "BYN",
        "purpose": "subscription",
        "payment_method": "acquiring",
        "status": status,
        "created_at": now,
        "mk_invoice_id": mk_invoice_id,
        "source": "moyklass_invoice_automation",
        "source_reference": f"mk_invoice_{mk_invoice_id}",
    })
    if status == "paid":
        with st._connect() as conn:
            conn.execute(
                """UPDATE payment_intents SET
                   status='paid', paid_amount_minor=?, paid_currency='BYN',
                   webhook_verified=1, paid_transaction_uid='test_tx_uid',
                   paid_channel='acquiring', paid_at=?, client_visibility=?
                   WHERE public_id=?""",
                (paid_amount_minor, now, client_visibility, intent["public_id"]),
            )
        intent = st.get_payment_intent(intent["public_id"])
    return intent


def _seed_automation_item(
    st: Storage,
    mk_invoice_id: str,
    mk_user_id: str = "7850022",
    intent_public_id: str | None = None,
    stage: str = "payment_options_created",
    auto_post_eligible: int = 0,
    now: str = "",
) -> dict:
    now = now or _now()
    item = st.upsert_automation_item(
        mk_invoice_id, mk_user_id, "Тест", "{}", now,
        auto_post_eligible=auto_post_eligible,
    )
    if stage != "discovered" or intent_public_id:
        st.update_automation_item_stage(
            item["id"], stage,
            intent_public_id=intent_public_id,
            now=now,
        )
        item = st.get_automation_item_by_invoice(mk_invoice_id) or item
    return item


def _make_readiness_ok(*, all_ok: bool = True, fingerprint: str = "fp_test_123") -> dict:
    """Fake readiness response."""
    checks = [{"code": "intent_paid", "ok": True, "label": "paid", "detail": ""}]
    return {
        "ok": True,
        "ready": all_ok,
        "checks": checks if all_ok else [{"code": "intent_paid", "ok": False, "label": "paid", "detail": "status=awaiting_payment"}],
        "warnings": [],
        "snapshot_fingerprint": fingerprint if all_ok else "",
        "preview": {},
        "invoice_error": None,
    }


# ---------------------------------------------------------------------------
# 1. Migration: existing rows get auto_post_eligible=0
# ---------------------------------------------------------------------------

class TestAutoPostMigration(unittest.TestCase):

    def test_01_new_storage_has_auto_post_eligible_column(self):
        st = _make_storage()
        now = _now()
        item = st.upsert_automation_item("mig_p1", "1", None, "{}", now)
        self.assertIn("auto_post_eligible", item)

    def test_02_new_storage_has_auto_post_eligible_at_column(self):
        st = _make_storage()
        now = _now()
        item = st.upsert_automation_item("mig_p2", "1", None, "{}", now)
        self.assertIn("auto_post_eligible_at", item)

    def test_03_default_auto_post_eligible_is_zero(self):
        st = _make_storage()
        now = _now()
        item = st.upsert_automation_item("mig_p3", "1", None, "{}", now)
        self.assertEqual(item.get("auto_post_eligible", -1), 0)

    def test_04_insert_or_ignore_does_not_overwrite_existing_eligible(self):
        st = _make_storage()
        now = _now()
        # First insert: eligible=0
        st.upsert_automation_item("mig_p4", "1", None, "{}", now, auto_post_eligible=0)
        # Second call (IGNORE fires): must not become 1
        st.upsert_automation_item("mig_p4", "1", None, "{}", now, auto_post_eligible=1)
        item = st.get_automation_item_by_invoice("mig_p4")
        self.assertEqual(item.get("auto_post_eligible"), 0,
                         "INSERT OR IGNORE must not overwrite existing auto_post_eligible")

    def test_05_eligible_at_set_when_eligible_1(self):
        st = _make_storage()
        now = _now()
        item = st.upsert_automation_item("mig_p5", "1", None, "{}", now, auto_post_eligible=1)
        self.assertIsNotNone(item.get("auto_post_eligible_at"),
                             "auto_post_eligible_at must be set when auto_post_eligible=1")

    def test_06_eligible_at_null_when_eligible_0(self):
        st = _make_storage()
        now = _now()
        item = st.upsert_automation_item("mig_p6", "1", None, "{}", now, auto_post_eligible=0)
        self.assertIsNone(item.get("auto_post_eligible_at"),
                          "auto_post_eligible_at must be NULL when auto_post_eligible=0")

    def test_07_auto_publish_eligible_at_also_set_when_eligible_1(self):
        """Fix for v7.0.95.1 bug: auto_publish_eligible_at must now be set."""
        st = _make_storage()
        now = _now()
        item = st.upsert_automation_item("mig_p7", "1", None, "{}", now,
                                         auto_publish_eligible=1)
        self.assertIsNotNone(item.get("auto_publish_eligible_at"),
                             "auto_publish_eligible_at bug fix: must be set when eligible=1")

    def test_08_settings_has_post_to_moyklass_enabled_column(self):
        st = _make_storage()
        s = st.get_automation_settings()
        self.assertIn("post_to_moyklass_enabled", s)

    def test_09_settings_post_to_moyklass_enabled_default_zero(self):
        st = _make_storage()
        s = st.get_automation_settings()
        self.assertEqual(s.get("post_to_moyklass_enabled", -1), 0)


# ---------------------------------------------------------------------------
# 2. Settings CRUD
# ---------------------------------------------------------------------------

class TestAutoPostSettings(unittest.TestCase):

    def test_10_update_settings_saves_post_enabled(self):
        st = _make_storage()
        now = _now()
        st.update_automation_settings(
            discovery_enabled=True,
            create_payment_options_enabled=True,
            publish_to_parent_enabled=False,
            post_to_moyklass_enabled=True,
            scan_interval_minutes=10,
            updated_by="test",
            now=now,
        )
        s = st.get_automation_settings()
        self.assertEqual(s.get("post_to_moyklass_enabled"), 1)

    def test_11_update_settings_saves_post_disabled(self):
        st = _make_storage()
        now = _now()
        st.update_automation_settings(
            discovery_enabled=True,
            create_payment_options_enabled=True,
            publish_to_parent_enabled=False,
            post_to_moyklass_enabled=False,
            scan_interval_minutes=10,
            updated_by="test",
            now=now,
        )
        s = st.get_automation_settings()
        self.assertEqual(s.get("post_to_moyklass_enabled"), 0)

    def test_12_update_settings_post_enabled_default_false(self):
        """update_automation_settings works without post_to_moyklass_enabled kwarg."""
        st = _make_storage()
        now = _now()
        st.update_automation_settings(
            discovery_enabled=True,
            create_payment_options_enabled=True,
            publish_to_parent_enabled=False,
            scan_interval_minutes=10,
            updated_by="test",
            now=now,
        )
        s = st.get_automation_settings()
        self.assertEqual(s.get("post_to_moyklass_enabled"), 0)


# ---------------------------------------------------------------------------
# 3. ycpi_202607_22 equivalent: historical record with eligible=0 must NOT be auto-posted
# ---------------------------------------------------------------------------

class TestYcpi202607_22Equivalent(unittest.TestCase):
    """Production record ycpi_202607_22: status=paid, auto_post_eligible=0 after migration."""

    def setUp(self):
        self.st = _make_storage()
        self.now = _now()
        inv_id = str(_next_inv_id())
        self.inv_id = inv_id
        _seed_parent_link(self.st, "7850022", "parent_22", self.now)
        self.intent = _seed_intent(self.st, inv_id, status="paid")
        # Historical item: auto_post_eligible=0 (as set by migration default)
        self.item = _seed_automation_item(
            self.st, inv_id,
            intent_public_id=self.intent["public_id"],
            stage="published",
            auto_post_eligible=0,
        )

    def test_13_historical_eligible_0_item_not_auto_posted(self):
        """item.auto_post_eligible=0 must prevent auto-posting."""
        from web_app_server import MiniAppContext
        ctx = MiniAppContext.__new__(MiniAppContext)
        ctx.storage = self.st
        ctx.settings = MagicMock()
        ctx.settings.bepaid_auto_post_to_moyklass = True
        ctx.moyklass = MagicMock()

        _enable_automation(self.st, post=True)

        # Even with post_enabled=True, eligible=0 must block auto-post
        result = ctx._process_single_automation_item_from_invoice(
            _mk_invoice(inv_id=int(self.inv_id), price=150.0, payed=0.0),
            now=self.now,
            create_enabled=True,
            publish_enabled=False,
            post_enabled=True,
        )
        # Must not trigger auto-post (no "posted" key)
        self.assertFalse(result.get("posted"), f"Historical item must not be auto-posted: {result}")
        # Item stage must NOT change to posted_to_moyklass
        item = self.st.get_automation_item_by_invoice(self.inv_id)
        self.assertNotEqual(item.get("current_stage"), "posted_to_moyklass",
                            "Historical item stage must not become posted_to_moyklass")


# ---------------------------------------------------------------------------
# 4. Two-level protection
# ---------------------------------------------------------------------------

class TestTwoLevelProtection(unittest.TestCase):
    """Both global env var AND DB setting must be True to enable auto-post."""

    def setUp(self):
        self.st = _make_storage()
        self.now = _now()
        inv_id = str(_next_inv_id())
        self.inv_id = inv_id
        _seed_parent_link(self.st, "7850022", "parent_prot", self.now)
        self.intent = _seed_intent(self.st, inv_id, status="paid")
        self.item = _seed_automation_item(
            self.st, inv_id,
            intent_public_id=self.intent["public_id"],
            stage="published",
            auto_post_eligible=1,
        )

    def _run_pipeline(self, *, global_on: bool, db_on: bool) -> dict:
        from web_app_server import MiniAppContext
        ctx = MiniAppContext.__new__(MiniAppContext)
        ctx.storage = self.st
        ctx.settings = MagicMock()
        ctx.settings.bepaid_auto_post_to_moyklass = global_on
        ctx.moyklass = MagicMock()

        _enable_automation(self.st, post=db_on)
        _global = getattr(ctx.settings, "bepaid_auto_post_to_moyklass", False)
        _db = bool(self.st.get_automation_settings().get("post_to_moyklass_enabled", 0))
        post_enabled = _global and _db

        return ctx._process_single_automation_item_from_invoice(
            _mk_invoice(inv_id=int(self.inv_id), price=150.0, payed=0.0),
            now=self.now,
            create_enabled=True,
            publish_enabled=False,
            post_enabled=post_enabled,
        )

    def test_14_global_off_db_off_no_auto_post(self):
        result = self._run_pipeline(global_on=False, db_on=False)
        self.assertFalse(result.get("posted"), "Both off → no auto-post")

    def test_15_global_on_db_off_no_auto_post(self):
        result = self._run_pipeline(global_on=True, db_on=False)
        self.assertFalse(result.get("posted"), "Global on, DB off → no auto-post")

    def test_16_global_off_db_on_no_auto_post(self):
        result = self._run_pipeline(global_on=False, db_on=True)
        self.assertFalse(result.get("posted"), "Global off, DB on → no auto-post")


# ---------------------------------------------------------------------------
# 5. _try_auto_post_automation_item unit tests
# ---------------------------------------------------------------------------

class TestTryAutoPostItem(unittest.TestCase):
    """Unit tests for _try_auto_post_automation_item method."""

    def setUp(self):
        self.st = _make_storage()
        self.now = _now()
        inv_id = str(_next_inv_id())
        self.inv_id = inv_id
        self.intent = _seed_intent(self.st, inv_id, status="paid")
        self.item = _seed_automation_item(
            self.st, inv_id,
            intent_public_id=self.intent["public_id"],
            stage="published",
            auto_post_eligible=1,
        )

    def _make_ctx(self):
        from web_app_server import MiniAppContext
        ctx = MiniAppContext.__new__(MiniAppContext)
        ctx.storage = self.st
        ctx.settings = MagicMock()
        ctx.settings.bepaid_auto_post_to_moyklass = True
        ctx.moyklass = MagicMock()
        return ctx

    def test_17_not_paid_status_skipped(self):
        intent = dict(self.intent)
        intent["status"] = "awaiting_payment"
        ctx = self._make_ctx()
        result = ctx._try_auto_post_automation_item(
            self.item["id"], intent, self.now, False
        )
        self.assertFalse(result.get("posted"))
        self.assertEqual(result.get("existing"), True)

    def test_18_mk_payment_id_already_set_marks_completed(self):
        intent = dict(self.intent)
        intent["mk_payment_id"] = 999888
        ctx = self._make_ctx()
        result = ctx._try_auto_post_automation_item(
            self.item["id"], intent, self.now, False
        )
        self.assertFalse(result.get("posted"))
        self.assertEqual(result.get("existing"), True)
        item = self.st.get_automation_item_by_invoice(self.inv_id)
        self.assertEqual(item.get("current_stage"), "posted_to_moyklass")

    def test_19_claiming_status_skipped(self):
        intent = dict(self.intent)
        intent["mk_posting_status"] = "claiming"
        ctx = self._make_ctx()
        result = ctx._try_auto_post_automation_item(
            self.item["id"], intent, self.now, False
        )
        self.assertFalse(result.get("posted"))
        self.assertEqual(result.get("existing"), True)

    def test_20_ambiguous_status_skipped(self):
        intent = dict(self.intent)
        intent["mk_posting_status"] = "ambiguous"
        ctx = self._make_ctx()
        result = ctx._try_auto_post_automation_item(
            self.item["id"], intent, self.now, False
        )
        self.assertFalse(result.get("posted"))
        self.assertEqual(result.get("existing"), True)

    def test_21_readiness_error_schedules_retry(self):
        # v7.0.96.1: readiness ok=False is a transient error → retry_scheduled (not requires_check)
        ctx = self._make_ctx()
        with patch.object(ctx, "payment_intent_moyklass_readiness",
                          return_value={"ok": False, "error": "MK API down"}):
            result = ctx._try_auto_post_automation_item(
                self.item["id"], self.intent, self.now, False
            )
        self.assertTrue(result.get("retry_scheduled"), result)
        self.assertFalse(result.get("posted"))
        item = self.st.get_automation_item_by_invoice(self.inv_id)
        self.assertEqual(item.get("current_stage"), "published")
        self.assertEqual(item.get("reason_code"), "auto_post_readiness_error")
        self.assertEqual(item.get("auto_post_attempt_count"), 1)

    def test_22_readiness_not_ready_sets_requires_check(self):
        ctx = self._make_ctx()
        with patch.object(ctx, "payment_intent_moyklass_readiness",
                          return_value=_make_readiness_ok(all_ok=False)):
            result = ctx._try_auto_post_automation_item(
                self.item["id"], self.intent, self.now, False
            )
        self.assertEqual(result.get("requires_check"), True)
        item = self.st.get_automation_item_by_invoice(self.inv_id)
        self.assertEqual(item.get("reason_code"), "auto_post_not_ready")

    def test_23_no_fingerprint_sets_requires_check(self):
        ctx = self._make_ctx()
        readiness = _make_readiness_ok(all_ok=True, fingerprint="")
        with patch.object(ctx, "payment_intent_moyklass_readiness", return_value=readiness):
            result = ctx._try_auto_post_automation_item(
                self.item["id"], self.intent, self.now, False
            )
        self.assertEqual(result.get("requires_check"), True)
        item = self.st.get_automation_item_by_invoice(self.inv_id)
        self.assertEqual(item.get("reason_code"), "auto_post_no_fingerprint")

    def test_24_post_succeeds_sets_posted_to_moyklass(self):
        ctx = self._make_ctx()
        with patch.object(ctx, "payment_intent_moyklass_readiness",
                          return_value=_make_readiness_ok()), \
             patch.object(ctx, "payment_intent_post_to_moyklass",
                          return_value={"ok": True, "mk_payment_id": 77001}):
            result = ctx._try_auto_post_automation_item(
                self.item["id"], self.intent, self.now, False
            )
        self.assertTrue(result.get("posted"))
        item = self.st.get_automation_item_by_invoice(self.inv_id)
        self.assertEqual(item.get("current_stage"), "posted_to_moyklass")

    def test_25_post_idempotent_marks_completed(self):
        ctx = self._make_ctx()
        with patch.object(ctx, "payment_intent_moyklass_readiness",
                          return_value=_make_readiness_ok()), \
             patch.object(ctx, "payment_intent_post_to_moyklass",
                          return_value={"ok": True, "idempotent": True, "mk_payment_id": 77001}):
            result = ctx._try_auto_post_automation_item(
                self.item["id"], self.intent, self.now, False
            )
        self.assertFalse(result.get("posted"))
        self.assertEqual(result.get("existing"), True)
        item = self.st.get_automation_item_by_invoice(self.inv_id)
        self.assertEqual(item.get("current_stage"), "posted_to_moyklass")

    def test_26_post_fails_sets_requires_check(self):
        ctx = self._make_ctx()
        with patch.object(ctx, "payment_intent_moyklass_readiness",
                          return_value=_make_readiness_ok()), \
             patch.object(ctx, "payment_intent_post_to_moyklass",
                          return_value={"ok": False, "error": "MK create failed"}):
            result = ctx._try_auto_post_automation_item(
                self.item["id"], self.intent, self.now, False
            )
        self.assertEqual(result.get("requires_check"), True)
        item = self.st.get_automation_item_by_invoice(self.inv_id)
        self.assertEqual(item.get("reason_code"), "auto_post_failed")

    def test_27_ambiguous_block_reason_sets_requires_check(self):
        ctx = self._make_ctx()
        with patch.object(ctx, "payment_intent_moyklass_readiness",
                          return_value=_make_readiness_ok()), \
             patch.object(ctx, "payment_intent_post_to_moyklass",
                          return_value={"ok": False,
                                        "block_reason": "ambiguous_requires_reconciliation"}):
            result = ctx._try_auto_post_automation_item(
                self.item["id"], self.intent, self.now, False
            )
        self.assertEqual(result.get("requires_check"), True)
        item = self.st.get_automation_item_by_invoice(self.inv_id)
        self.assertEqual(item.get("reason_code"), "auto_post_ambiguous")

    def test_28_invoice_changed_sets_requires_check(self):
        ctx = self._make_ctx()
        with patch.object(ctx, "payment_intent_moyklass_readiness",
                          return_value=_make_readiness_ok()), \
             patch.object(ctx, "payment_intent_post_to_moyklass",
                          return_value={"ok": False,
                                        "error_code": "invoice_changed_after_preview"}):
            result = ctx._try_auto_post_automation_item(
                self.item["id"], self.intent, self.now, False
            )
        self.assertEqual(result.get("requires_check"), True)
        item = self.st.get_automation_item_by_invoice(self.inv_id)
        self.assertEqual(item.get("reason_code"), "auto_post_invoice_changed")


# ---------------------------------------------------------------------------
# 6. Pipeline: post_enabled gate in _process_single_automation_item_from_invoice
# ---------------------------------------------------------------------------

class TestPipelinePostGate(unittest.TestCase):
    """auto_post only fires when post_enabled=True AND auto_post_eligible=1."""

    def setUp(self):
        self.st = _make_storage()
        self.now = _now()
        self.inv_id = str(_next_inv_id())
        _seed_parent_link(self.st, "7850022", "parent_pipe", self.now)
        self.intent = _seed_intent(self.st, self.inv_id, status="paid")
        self.item = _seed_automation_item(
            self.st, self.inv_id,
            intent_public_id=self.intent["public_id"],
            stage="published",
            auto_post_eligible=1,
        )

    def _make_ctx_with_mocked_posting(self):
        from web_app_server import MiniAppContext
        ctx = MiniAppContext.__new__(MiniAppContext)
        ctx.storage = self.st
        ctx.settings = MagicMock()
        ctx.settings.bepaid_auto_post_to_moyklass = True
        ctx.moyklass = MagicMock()
        return ctx

    def test_29_post_enabled_false_no_auto_post(self):
        ctx = self._make_ctx_with_mocked_posting()
        result = ctx._process_single_automation_item_from_invoice(
            _mk_invoice(inv_id=int(self.inv_id), price=150.0, payed=0.0),
            now=self.now,
            create_enabled=True,
            publish_enabled=False,
            post_enabled=False,
        )
        self.assertFalse(result.get("posted"), "post_enabled=False must not trigger auto-post")

    def test_30_post_enabled_true_eligible_1_paid_triggers_auto_post(self):
        from web_app_server import MiniAppContext
        ctx = self._make_ctx_with_mocked_posting()
        # Mock at class level so instance method lookup finds the patch
        with patch.object(MiniAppContext, "_try_auto_post_automation_item",
                          return_value={"posted": True}) as mock_post:
            result = ctx._process_single_automation_item_from_invoice(
                _mk_invoice(inv_id=int(self.inv_id), price=150.0, payed=0.0),
                now=self.now,
                create_enabled=True,
                publish_enabled=False,
                post_enabled=True,
            )
        mock_post.assert_called_once()
        self.assertTrue(result.get("posted"))

    def test_31_post_enabled_true_eligible_0_no_auto_post(self):
        """Historical item (eligible=0) must be skipped even when post_enabled=True."""
        from web_app_server import MiniAppContext
        # Override item to have eligible=0
        with self.st._connect() as conn:
            conn.execute(
                "UPDATE invoice_automation_items SET auto_post_eligible=0 WHERE mk_invoice_id=?",
                (self.inv_id,),
            )
        ctx = self._make_ctx_with_mocked_posting()
        with patch.object(MiniAppContext, "_try_auto_post_automation_item") as mock_post:
            result = ctx._process_single_automation_item_from_invoice(
                _mk_invoice(inv_id=int(self.inv_id), price=150.0, payed=0.0),
                now=self.now,
                create_enabled=True,
                publish_enabled=False,
                post_enabled=True,
            )
        mock_post.assert_not_called()
        self.assertFalse(result.get("posted"))

    def test_32_post_enabled_true_status_not_paid_no_auto_post(self):
        """Intent in awaiting_payment: no auto-post even if eligible=1."""
        from web_app_server import MiniAppContext
        # Change intent status to awaiting_payment
        with self.st._connect() as conn:
            conn.execute(
                "UPDATE payment_intents SET status='awaiting_payment' WHERE mk_invoice_id=?",
                (self.inv_id,),
            )
        ctx = self._make_ctx_with_mocked_posting()
        with patch.object(MiniAppContext, "_try_auto_post_automation_item") as mock_post:
            ctx._process_single_automation_item_from_invoice(
                _mk_invoice(inv_id=int(self.inv_id), price=150.0, payed=0.0),
                now=self.now,
                create_enabled=True,
                publish_enabled=False,
                post_enabled=True,
            )
        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# 7. Readiness endpoint fix: manual posting no longer blocked by env var
# ---------------------------------------------------------------------------

class TestReadinessFix(unittest.TestCase):
    """BEPAID_AUTO_POST_TO_MOYKLASS=True must NOT block manual posting readiness."""

    def test_33_readiness_not_blocked_when_auto_post_true(self):
        st = _make_storage()
        now = _now()
        inv_id = str(_next_inv_id())
        intent = _seed_intent(st, inv_id, status="paid")

        from web_app_server import MiniAppContext
        ctx = MiniAppContext.__new__(MiniAppContext)
        ctx.storage = st
        ctx.settings = MagicMock()
        ctx.settings.bepaid_auto_post_to_moyklass = True
        ctx.settings.moyklass_erip_payment_type_id = 55948
        ctx.settings.moyklass_acquiring_payment_type_id = 111861

        mk = MagicMock()
        mk.is_configured = False  # skip live checks
        ctx.moyklass = mk

        _AUTO_AUTH = {"user_id": "automation", "_is_automation": True}
        result = ctx.payment_intent_moyklass_readiness(_AUTO_AUTH, intent["public_id"])
        self.assertTrue(result.get("ok"), f"Readiness call should succeed: {result}")

        # The auto_post_disabled check must NOT appear in checks list
        check_codes = [c["code"] for c in result.get("checks", [])]
        self.assertNotIn("auto_post_disabled", check_codes,
                         "auto_post_disabled must no longer block readiness")

    def test_34_readiness_auto_post_enabled_appears_in_warnings_not_checks(self):
        st = _make_storage()
        now = _now()
        inv_id = str(_next_inv_id())
        intent = _seed_intent(st, inv_id, status="paid")

        from web_app_server import MiniAppContext
        ctx = MiniAppContext.__new__(MiniAppContext)
        ctx.storage = st
        ctx.settings = MagicMock()
        ctx.settings.bepaid_auto_post_to_moyklass = True
        ctx.settings.moyklass_erip_payment_type_id = 55948
        ctx.settings.moyklass_acquiring_payment_type_id = 111861

        mk = MagicMock()
        mk.is_configured = False
        ctx.moyklass = mk

        _AUTO_AUTH = {"user_id": "automation", "_is_automation": True}
        result = ctx.payment_intent_moyklass_readiness(_AUTO_AUTH, intent["public_id"])
        warnings = result.get("warnings", [])
        self.assertTrue(
            any("BEPAID_AUTO_POST_TO_MOYKLASS" in w for w in warnings),
            f"Auto-post enabled warning must appear in warnings, got: {warnings}",
        )

    def test_35_readiness_no_warning_when_auto_post_false(self):
        st = _make_storage()
        now = _now()
        inv_id = str(_next_inv_id())
        intent = _seed_intent(st, inv_id, status="paid")

        from web_app_server import MiniAppContext
        ctx = MiniAppContext.__new__(MiniAppContext)
        ctx.storage = st
        ctx.settings = MagicMock()
        ctx.settings.bepaid_auto_post_to_moyklass = False
        ctx.settings.moyklass_erip_payment_type_id = 55948
        ctx.settings.moyklass_acquiring_payment_type_id = 111861

        mk = MagicMock()
        mk.is_configured = False
        ctx.moyklass = mk

        _AUTO_AUTH = {"user_id": "automation", "_is_automation": True}
        result = ctx.payment_intent_moyklass_readiness(_AUTO_AUTH, intent["public_id"])
        warnings = result.get("warnings", [])
        self.assertFalse(
            any("BEPAID_AUTO_POST_TO_MOYKLASS" in w for w in warnings),
            "No auto-post warning when flag=False",
        )


# ---------------------------------------------------------------------------
# 8. New invoice first processed with post_enabled → auto_post_eligible=1
# ---------------------------------------------------------------------------

class TestNewInvoicePostEnabled(unittest.TestCase):

    def setUp(self):
        self.st = _make_storage()
        self.now = _now()

    def test_36_new_invoice_gets_auto_post_eligible_1_when_post_enabled(self):
        inv_id = str(_next_inv_id())
        item = self.st.upsert_automation_item(
            inv_id, "7850022", "Тест", "{}", self.now,
            auto_post_eligible=1,
        )
        self.assertEqual(item.get("auto_post_eligible"), 1)
        self.assertIsNotNone(item.get("auto_post_eligible_at"))

    def test_37_new_invoice_gets_auto_post_eligible_0_when_post_disabled(self):
        inv_id = str(_next_inv_id())
        item = self.st.upsert_automation_item(
            inv_id, "7850022", "Тест", "{}", self.now,
            auto_post_eligible=0,
        )
        self.assertEqual(item.get("auto_post_eligible"), 0)
        self.assertIsNone(item.get("auto_post_eligible_at"))


# ---------------------------------------------------------------------------
# 9. Already-posted item stage skipped in pipeline
# ---------------------------------------------------------------------------

class TestPostedToMoyklassStageSkipped(unittest.TestCase):

    def test_38_posted_to_moyklass_stage_skipped_in_pipeline(self):
        st = _make_storage()
        now = _now()
        inv_id = str(_next_inv_id())
        _seed_parent_link(st, "7850022", "parent_skip", now)
        intent = _seed_intent(st, inv_id, status="paid")
        item = _seed_automation_item(
            st, inv_id,
            intent_public_id=intent["public_id"],
            stage="posted_to_moyklass",
            auto_post_eligible=1,
        )

        from web_app_server import MiniAppContext
        ctx = MiniAppContext.__new__(MiniAppContext)
        ctx.storage = st
        ctx.settings = MagicMock()
        ctx.settings.bepaid_auto_post_to_moyklass = True
        ctx.moyklass = MagicMock()

        result = ctx._process_single_automation_item_from_invoice(
            _mk_invoice(inv_id=int(inv_id), price=150.0, payed=0.0),
            now=now,
            create_enabled=True,
            publish_enabled=False,
            post_enabled=True,
        )
        self.assertEqual(result.get("skip"), True,
                         "posted_to_moyklass stage must be skipped in pipeline")


# ---------------------------------------------------------------------------
# 10. Version check
# ---------------------------------------------------------------------------

class TestVersion(unittest.TestCase):

    def test_39_version_string(self):
        self.assertEqual(CURRENT_VERSION, "7.0.98.2")

    def test_40_app_js_version(self):
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("v7.0.98.2", js)

    def test_41_index_html_cache_bust(self):
        html = (ROOT / "miniapp" / "index.html").read_bytes().decode("utf-8-sig")
        self.assertIn("v=7.0.98.2", html)

    def test_42_auto_toggle_post_in_html(self):
        html = (ROOT / "miniapp" / "index.html").read_bytes().decode("utf-8-sig")
        self.assertIn('id="autoTogglePost"', html,
                      "autoTogglePost checkbox must be in index.html")

    def test_43_auto_toggle_post_not_disabled(self):
        html = (ROOT / "miniapp" / "index.html").read_bytes().decode("utf-8-sig")
        # The checkbox must be functional (no 'disabled' attribute near it)
        import re
        # Find the autoTogglePost input
        m = re.search(r'<input[^>]*id="autoTogglePost"[^>]*>', html)
        self.assertIsNotNone(m, "autoTogglePost input must exist")
        self.assertNotIn("disabled", m.group(0), "autoTogglePost must not have disabled attribute")

    def test_44_app_js_loads_post_to_moyklass_enabled(self):
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("post_to_moyklass_enabled", js)

    def test_45_app_js_sends_post_to_moyklass_enabled(self):
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        # The save function must include the field in the request body
        self.assertIn("post_to_moyklass_enabled: postEnabled", js)

    def test_46_app_js_has_auto_toggle_post_confirm(self):
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("autoTogglePost", js)


if __name__ == "__main__":
    unittest.main()

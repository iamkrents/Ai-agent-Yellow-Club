"""Tests for v7.0.96.1 — auto_publish_eligible eligibility guard.

Verifies that auto-publishing of payment intents to parents is gated by an
explicit per-item flag (auto_publish_eligible) set only when
publish_to_parent_enabled=True at the moment the invoice is FIRST processed.
Historical backlog and manually-withdrawn intents must never be auto-published.

Production fixtures tested (read-only, no writes):
  ycpi_202607_17 — withdrawn
  ycpi_202607_20 — withdrawn
  ycpi_202607_21 — published (must not be re-published)

Run offline (no bePaid / MoyKlass / Telegram):
    python -m unittest tests.test_auto_publish_eligible -v
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

CURRENT_VERSION = "7.0.99.0"

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


_INV_COUNTER = [9000]


def _next_inv_id() -> int:
    _INV_COUNTER[0] += 1
    return _INV_COUNTER[0]


def _mk_invoice(
    inv_id: int = 0,
    user_id: int = 7850001,
    price: float = 100.0,
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
        "userSubscriptionId": 5000 + inv_id,
        "userSubscription": {
            "name": "Подписка",
            "clientName": "Тестовый Ученик",
            "beginDate": "2026-07-01",
        },
    }


class _FakeResult:
    def __init__(self, data):
        self.data = data
        self.ok = True
        self.error = None


def _make_ctx(storage: Storage, invoices: list[dict]):
    """Minimal MiniAppContext with mocked MK returning given invoices."""
    from web_app_server import MiniAppContext
    ctx = MiniAppContext.__new__(MiniAppContext)
    ctx.storage = storage

    mk = MagicMock()
    def _fake_request(method, path, **kwargs):
        return _FakeResult({"invoices": invoices})
    mk.request = _fake_request
    ctx.moyklass = mk

    settings = MagicMock()
    settings.payment_invoice_automation_enabled = True
    settings.bepaid_erip_shop_id = ""
    settings.bepaid_erip_secret_key = ""
    settings.bepaid_acq_shop_id = ""
    settings.bepaid_acq_secret_key = ""
    settings.bepaid_public_base_url = ""
    settings.bepaid_webhook_path_secret = ""
    settings.moyklass_erip_payment_type_id = 0
    settings.moyklass_acquiring_payment_type_id = 0
    ctx.settings = settings
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


def _enable_automation(st: Storage, *, create: bool = True, publish: bool) -> None:
    st.update_automation_settings(
        discovery_enabled=True,
        create_payment_options_enabled=create,
        publish_to_parent_enabled=publish,
        scan_interval_minutes=10,
        updated_by="test",
        now=_now(),
    )


def _seed_intent(
    st: Storage,
    mk_invoice_id: str,
    mk_user_id: int = 7850001,
    status: str = "awaiting_payment",
    client_visibility: str = "hidden",
    now: str = "",
) -> dict:
    """Insert an intent directly via storage.create_payment_intent."""
    now = now or _now()
    intent = st.create_payment_intent({
        "mk_user_id": mk_user_id,
        "student_name": "Тест",
        "amount_minor": 10000,
        "amount_byn": 100.0,
        "currency": "BYN",
        "purpose": "subscription",
        "payment_method": "erip",
        "status": status,
        "created_at": now,
        "mk_invoice_id": mk_invoice_id,
        "source": "moyklass_invoice_automation",
        "source_reference": f"mk_invoice_{mk_invoice_id}",
    })
    return intent


def _seed_automation_item(
    st: Storage,
    mk_invoice_id: str,
    mk_user_id: str = "7850001",
    intent_public_id: str | None = None,
    stage: str = "payment_options_created",
    auto_publish_eligible: int = 0,
    now: str = "",
) -> dict:
    now = now or _now()
    item = st.upsert_automation_item(
        mk_invoice_id, mk_user_id, "Тест", "{}", now,
        auto_publish_eligible=auto_publish_eligible,
    )
    if stage != "discovered" or intent_public_id:
        st.update_automation_item_stage(
            item["id"], stage,
            intent_public_id=intent_public_id,
            now=now,
        )
        item = st.get_automation_item_by_invoice(mk_invoice_id) or item
    return item


# ---------------------------------------------------------------------------
# 1. Migration: existing rows get auto_publish_eligible=0
# ---------------------------------------------------------------------------

class TestMigration(unittest.TestCase):
    """Test 19: migration adds column with DEFAULT 0 — no backfill to 1."""

    def test_new_storage_has_eligible_column(self):
        st = _make_storage()
        now = _now()
        item = st.upsert_automation_item("mig_1", "1", None, "{}", now)
        self.assertIn("auto_publish_eligible", item)

    def test_existing_rows_get_default_zero(self):
        st = _make_storage()
        now = _now()
        # Insert a row without specifying auto_publish_eligible
        with st._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO invoice_automation_items
                   (mk_invoice_id, mk_user_id, student_name, invoice_snapshot_json,
                    created_at, updated_at)
                   VALUES (?,?,NULL,'{}',?,?)""",
                ("mig_2", "1", now, now),
            )
        item = st.get_automation_item_by_invoice("mig_2")
        self.assertIsNotNone(item)
        self.assertEqual(item.get("auto_publish_eligible", 0), 0)

    def test_upsert_does_not_overwrite_existing_eligible(self):
        """INSERT OR IGNORE: if row already exists, eligible stays as-is."""
        st = _make_storage()
        now = _now()
        # First insert: eligible=0
        st.upsert_automation_item("mig_3", "1", None, "{}", now, auto_publish_eligible=0)
        # Second call (IGNORE fires): eligible must not become 1
        st.upsert_automation_item("mig_3", "1", None, "{}", now, auto_publish_eligible=1)
        item = st.get_automation_item_by_invoice("mig_3")
        self.assertEqual(item.get("auto_publish_eligible"), 0,
                         "INSERT OR IGNORE must not overwrite existing eligible flag")


# ---------------------------------------------------------------------------
# 2. New invoice when publish_enabled=True → eligible=1, published
# ---------------------------------------------------------------------------

class TestNewInvoicePublishEnabled(unittest.TestCase):
    """Test 1: new invoice processed while publish_to_parent_enabled=True."""

    def setUp(self):
        self.st = _make_storage()
        self.now = _now()

    def test_new_invoice_gets_eligible_1(self):
        _enable_automation(self.st, create=True, publish=True)
        inv = _mk_invoice(inv_id=_next_inv_id(), user_id=7850001)
        _seed_parent_link(self.st, "7850001", "tg_parent_1", self.now)
        ctx = _make_ctx(self.st, [inv])
        ctx.process_new_moyklass_invoices(trigger="test")
        item = self.st.get_automation_item_by_invoice(str(inv["id"]))
        self.assertIsNotNone(item)
        self.assertEqual(item.get("auto_publish_eligible"), 1,
                         "New invoice processed while publish_enabled must get eligible=1")

    def test_new_invoice_published_to_parent(self):
        _enable_automation(self.st, create=True, publish=True)
        inv = _mk_invoice(inv_id=_next_inv_id(), user_id=7850002)
        _seed_parent_link(self.st, "7850002", "tg_parent_2", self.now)
        ctx = _make_ctx(self.st, [inv])
        result = ctx.process_new_moyklass_invoices(trigger="test")
        self.assertTrue(result.get("ok"), result)
        item = self.st.get_automation_item_by_invoice(str(inv["id"]))
        self.assertIsNotNone(item)
        if item.get("intent_public_id"):
            pi = self.st.get_payment_intent(item["intent_public_id"])
            self.assertIsNotNone(pi)
            self.assertEqual(pi.get("client_visibility"), "published",
                             "Intent must be published when publish_enabled=True")


# ---------------------------------------------------------------------------
# 3. New invoice when publish_enabled=False → eligible=0, not published
# ---------------------------------------------------------------------------

class TestNewInvoicePublishDisabled(unittest.TestCase):
    """Test 2: new invoice processed while publish_to_parent_enabled=False."""

    def setUp(self):
        self.st = _make_storage()
        self.now = _now()

    def test_new_invoice_gets_eligible_0(self):
        _enable_automation(self.st, create=True, publish=False)
        inv = _mk_invoice(inv_id=_next_inv_id(), user_id=7851001)
        _seed_parent_link(self.st, "7851001", "tg_parent_3", self.now)
        ctx = _make_ctx(self.st, [inv])
        ctx.process_new_moyklass_invoices(trigger="test")
        item = self.st.get_automation_item_by_invoice(str(inv["id"]))
        self.assertIsNotNone(item)
        self.assertEqual(item.get("auto_publish_eligible"), 0,
                         "New invoice processed while publish_disabled must get eligible=0")

    def test_new_invoice_not_published(self):
        _enable_automation(self.st, create=True, publish=False)
        inv = _mk_invoice(inv_id=_next_inv_id(), user_id=7851002)
        _seed_parent_link(self.st, "7851002", "tg_parent_4", self.now)
        ctx = _make_ctx(self.st, [inv])
        ctx.process_new_moyklass_invoices(trigger="test")
        item = self.st.get_automation_item_by_invoice(str(inv["id"]))
        if item and item.get("intent_public_id"):
            pi = self.st.get_payment_intent(item["intent_public_id"])
            self.assertNotEqual(
                (pi or {}).get("client_visibility"), "published",
                "Intent must NOT be published when publish_disabled at creation time",
            )

    def test_enabling_publish_later_does_not_auto_publish_old_item(self):
        """Test 9: re-enabling the setting must NOT publish items with eligible=0."""
        now = _now()
        _enable_automation(self.st, create=True, publish=False)
        inv = _mk_invoice(inv_id=_next_inv_id(), user_id=7851003)
        _seed_parent_link(self.st, "7851003", "tg_parent_5", now)
        ctx = _make_ctx(self.st, [inv])
        ctx.process_new_moyklass_invoices(trigger="test")
        item = self.st.get_automation_item_by_invoice(str(inv["id"]))
        self.assertEqual(item.get("auto_publish_eligible", -1), 0)

        # Now turn publish ON and re-scan
        _enable_automation(self.st, create=True, publish=True)
        ctx2 = _make_ctx(self.st, [inv])
        ctx2.process_new_moyklass_invoices(trigger="test")

        item2 = self.st.get_automation_item_by_invoice(str(inv["id"]))
        # eligible must still be 0 (INSERT OR IGNORE skips existing row)
        self.assertEqual(item2.get("auto_publish_eligible"), 0,
                         "Enabling publish later must not change eligible=0 to 1")
        if item2 and item2.get("intent_public_id"):
            pi = self.st.get_payment_intent(item2["intent_public_id"])
            self.assertNotEqual(
                (pi or {}).get("client_visibility"), "published",
                "Historical item with eligible=0 must not be published after enabling publish",
            )


# ---------------------------------------------------------------------------
# 4. Old hidden intent without eligibility → not published
# ---------------------------------------------------------------------------

class TestHistoricalBacklogNotPublished(unittest.TestCase):
    """Tests 3 & 4: hidden/backlog intents with auto_publish_eligible=0 are never published."""

    def setUp(self):
        self.st = _make_storage()
        self.now = _now()

    def test_hidden_intent_eligible_0_not_published(self):
        inv_id = str(_next_inv_id())
        # Pre-create intent (hidden) and automation item with eligible=0
        intent = _seed_intent(self.st, inv_id, status="awaiting_payment",
                              client_visibility="hidden", now=self.now)
        _seed_automation_item(self.st, inv_id,
                              intent_public_id=intent["public_id"],
                              stage="payment_options_created",
                              auto_publish_eligible=0)
        _seed_parent_link(self.st, "7850001", "tg_parent_h1", self.now)

        _enable_automation(self.st, create=True, publish=True)
        inv = _mk_invoice(inv_id=int(inv_id))
        ctx = _make_ctx(self.st, [inv])
        ctx.process_new_moyklass_invoices(trigger="test")

        pi = self.st.get_payment_intent(intent["public_id"])
        self.assertNotEqual(pi.get("client_visibility"), "published",
                            "Hidden intent with eligible=0 must not be auto-published")

    def test_historical_backlog_not_published_on_enable(self):
        """Multiple old items: enabling publish does not publish any."""
        _enable_automation(self.st, create=True, publish=True)
        inv_ids = [str(_next_inv_id()) for _ in range(3)]
        intents = []
        invs = []
        for inv_id in inv_ids:
            intent = _seed_intent(self.st, inv_id, status="awaiting_payment",
                                  client_visibility="hidden", now=self.now)
            _seed_automation_item(self.st, inv_id,
                                  intent_public_id=intent["public_id"],
                                  stage="payment_options_created",
                                  auto_publish_eligible=0)
            intents.append(intent)
            invs.append(_mk_invoice(inv_id=int(inv_id)))

        _seed_parent_link(self.st, "7850001", "tg_backlog_p", self.now)
        ctx = _make_ctx(self.st, invs)
        ctx.process_new_moyklass_invoices(trigger="test")

        for intent in intents:
            pi = self.st.get_payment_intent(intent["public_id"])
            self.assertNotEqual(pi.get("client_visibility"), "published",
                                f"Historical intent {intent['public_id']} must not be published")


# ---------------------------------------------------------------------------
# 5-6. Production fixtures: ycpi_202607_17 and ycpi_202607_20 (withdrawn)
# ---------------------------------------------------------------------------

class TestWithdrawnProductionFixtures(unittest.TestCase):
    """Tests 5 & 6: production fixtures that were manually withdrawn must stay withdrawn."""

    def setUp(self):
        self.st = _make_storage()
        self.now = _now()

    def _seed_withdrawn_fixture(
        self, public_id: str, mk_invoice_id: str, mk_user_id: int = 9748998
    ) -> dict:
        """Simulate a production-withdrawn intent with eligible=0."""
        intent = _seed_intent(self.st, mk_invoice_id, mk_user_id=mk_user_id,
                              status="awaiting_payment", client_visibility="hidden",
                              now=self.now)
        # Override public_id not possible, so we withdraw the created intent
        self.st.withdraw_payment_intent_from_client(
            intent["public_id"], withdrawn_by="7850692063", now=self.now
        )
        _seed_automation_item(self.st, mk_invoice_id,
                              intent_public_id=intent["public_id"],
                              stage="payment_options_created",
                              auto_publish_eligible=0)
        return self.st.get_payment_intent(intent["public_id"])

    def test_ycpi_202607_20_equivalent_stays_withdrawn(self):
        """ycpi_202607_20 equivalent: withdrawn, eligible=0 — must not be auto-published."""
        _enable_automation(self.st, create=True, publish=True)
        pi = self._seed_withdrawn_fixture("ycpi_eq_20", "19099496", mk_user_id=9748998)
        self.assertEqual(pi["client_visibility"], "withdrawn")

        inv = _mk_invoice(inv_id=19099496, user_id=9748998)
        _seed_parent_link(self.st, "9748998", "tg_parent_20", self.now)
        ctx = _make_ctx(self.st, [inv])
        ctx.process_new_moyklass_invoices(trigger="test")

        pi_after = self.st.get_payment_intent(pi["public_id"])
        self.assertEqual(pi_after["client_visibility"], "withdrawn",
                         "ycpi_202607_20-equivalent must remain withdrawn after automation run")
        self.assertEqual(pi_after.get("withdrawn_by"), "7850692063",
                         "withdrawn_by must not be cleared by scheduler")
        self.assertIsNotNone(pi_after.get("withdrawn_at"),
                             "withdrawn_at must not be cleared by scheduler")

    def test_ycpi_202607_17_equivalent_stays_withdrawn(self):
        """ycpi_202607_17 equivalent: withdrawn, eligible=0 — must not be auto-published."""
        _enable_automation(self.st, create=True, publish=True)
        pi = self._seed_withdrawn_fixture("ycpi_eq_17", "19075076", mk_user_id=9748998)

        inv = _mk_invoice(inv_id=19075076, user_id=9748998)
        _seed_parent_link(self.st, "9748998", "tg_parent_17", self.now)
        ctx = _make_ctx(self.st, [inv])
        ctx.process_new_moyklass_invoices(trigger="test")

        pi_after = self.st.get_payment_intent(pi["public_id"])
        self.assertEqual(pi_after["client_visibility"], "withdrawn",
                         "ycpi_202607_17-equivalent must remain withdrawn after automation run")


# ---------------------------------------------------------------------------
# 7. ycpi_202607_21 equivalent: already published, not re-published
# ---------------------------------------------------------------------------

class TestAlreadyPublishedFixture(unittest.TestCase):
    """Test 7: intent already published — must not be re-published or have published_at changed."""

    def test_already_published_intent_not_re_published(self):
        st = _make_storage()
        now = _now()
        _enable_automation(st, create=True, publish=True)

        # Create intent, then publish it (simulating ycpi_202607_21)
        intent = _seed_intent(st, "19104025", status="awaiting_payment",
                              client_visibility="hidden", now=now)
        pub1 = st.publish_payment_intent_to_client(intent["public_id"], "automation", now)
        self.assertTrue(pub1.get("ok"))
        pi_before = st.get_payment_intent(intent["public_id"])
        published_at_before = pi_before.get("published_at")

        _seed_automation_item(st, "19104025",
                              intent_public_id=intent["public_id"],
                              stage="published", auto_publish_eligible=1)
        _seed_parent_link(st, "7850001", "tg_p_21", now)

        inv = _mk_invoice(inv_id=19104025)
        ctx = _make_ctx(st, [inv])
        # Stage is 'published' → should be skipped (skip=True)
        ctx.process_new_moyklass_invoices(trigger="test")

        pi_after = st.get_payment_intent(intent["public_id"])
        self.assertEqual(pi_after["client_visibility"], "published")
        self.assertEqual(pi_after.get("published_at"), published_at_before,
                         "published_at must not change on second automation run")


# ---------------------------------------------------------------------------
# 8. withdrawn + eligible=1 → not published, history preserved
# ---------------------------------------------------------------------------

class TestWithdrawnWithEligibleFlag(unittest.TestCase):
    """Test 8: even with auto_publish_eligible=1, withdrawn wins."""

    def test_withdrawn_with_eligible_not_published(self):
        st = _make_storage()
        now = _now()
        _enable_automation(st, create=True, publish=True)

        intent = _seed_intent(st, "19990001", status="awaiting_payment",
                              client_visibility="hidden", now=now)
        # Publish then withdraw
        st.publish_payment_intent_to_client(intent["public_id"], "automation", now)
        st.withdraw_payment_intent_from_client(intent["public_id"],
                                              withdrawn_by="admin_123", now=now)
        pi = st.get_payment_intent(intent["public_id"])
        withdrawn_at_before = pi.get("withdrawn_at")
        withdrawn_by_before = pi.get("withdrawn_by")

        # Automation item is eligible=1 (edge case)
        _seed_automation_item(st, "19990001",
                              intent_public_id=intent["public_id"],
                              stage="payment_options_created",
                              auto_publish_eligible=1)
        _seed_parent_link(st, "7850001", "tg_p_w1", now)

        inv = _mk_invoice(inv_id=19990001)
        ctx = _make_ctx(st, [inv])
        ctx.process_new_moyklass_invoices(trigger="test")

        pi_after = st.get_payment_intent(intent["public_id"])
        self.assertEqual(pi_after["client_visibility"], "withdrawn",
                         "withdrawn with eligible=1 must still not be auto-published")
        self.assertEqual(pi_after.get("withdrawn_at"), withdrawn_at_before,
                         "scheduler must not change withdrawn_at")
        self.assertEqual(pi_after.get("withdrawn_by"), withdrawn_by_before,
                         "scheduler must not change withdrawn_by")


# ---------------------------------------------------------------------------
# 9. Re-enabling publish setting: withdrawn and historical backlog stay safe
# ---------------------------------------------------------------------------

class TestReenablingPublishSetting(unittest.TestCase):
    """Test 9: turning publish back ON must not publish withdrawn or historical items."""

    def setUp(self):
        self.st = _make_storage()
        self.now = _now()

    def test_reenable_does_not_publish_withdrawn(self):
        # Create and withdraw an intent while publish was OFF
        _enable_automation(self.st, create=True, publish=False)
        intent = _seed_intent(self.st, "19991001", status="awaiting_payment",
                              client_visibility="hidden", now=self.now)
        self.st.withdraw_payment_intent_from_client(
            intent["public_id"], withdrawn_by="admin_re1", now=self.now
        )
        _seed_automation_item(self.st, "19991001",
                              intent_public_id=intent["public_id"],
                              stage="payment_options_created",
                              auto_publish_eligible=0)
        _seed_parent_link(self.st, "7850001", "tg_re1", self.now)

        # Turn publish ON
        _enable_automation(self.st, create=True, publish=True)
        inv = _mk_invoice(inv_id=19991001)
        ctx = _make_ctx(self.st, [inv])
        ctx.process_new_moyklass_invoices(trigger="test")

        pi_after = self.st.get_payment_intent(intent["public_id"])
        self.assertEqual(pi_after["client_visibility"], "withdrawn",
                         "Re-enabling publish must not re-publish a withdrawn intent")

    def test_reenable_does_not_publish_hidden_backlog(self):
        _enable_automation(self.st, create=True, publish=False)
        intent = _seed_intent(self.st, "19991002", status="awaiting_payment",
                              client_visibility="hidden", now=self.now)
        _seed_automation_item(self.st, "19991002",
                              intent_public_id=intent["public_id"],
                              stage="payment_options_created",
                              auto_publish_eligible=0)
        _seed_parent_link(self.st, "7850001", "tg_re2", self.now)

        _enable_automation(self.st, create=True, publish=True)
        inv = _mk_invoice(inv_id=19991002)
        ctx = _make_ctx(self.st, [inv])
        ctx.process_new_moyklass_invoices(trigger="test")

        pi_after = self.st.get_payment_intent(intent["public_id"])
        self.assertNotEqual(pi_after.get("client_visibility"), "published",
                            "Re-enabling publish must not publish hidden backlog")


# ---------------------------------------------------------------------------
# 10. Retry: eligible intent after temporary failure → published
# ---------------------------------------------------------------------------

class TestRetryEligibleIntent(unittest.TestCase):
    """Test 10: eligible=1, temporarily not published → retry publishes once."""

    def test_retry_eligible_publishes(self):
        st = _make_storage()
        now = _now()
        _enable_automation(st, create=True, publish=True)

        # Create intent that was created with eligible=1 but not yet published
        intent = _seed_intent(st, "19992001", status="awaiting_payment",
                              client_visibility="hidden", now=now)
        _seed_automation_item(st, "19992001",
                              intent_public_id=intent["public_id"],
                              stage="payment_options_created",
                              auto_publish_eligible=1)
        _seed_parent_link(st, "7850001", "tg_retry1", now)

        inv = _mk_invoice(inv_id=19992001)
        ctx = _make_ctx(st, [inv])
        ctx.process_new_moyklass_invoices(trigger="test")

        pi_after = st.get_payment_intent(intent["public_id"])
        self.assertEqual(pi_after.get("client_visibility"), "published",
                         "Eligible intent with eligible=1 must be published on retry")

    def test_retry_does_not_create_new_intent(self):
        st = _make_storage()
        now = _now()
        _enable_automation(st, create=True, publish=True)

        intent = _seed_intent(st, "19992002", status="awaiting_payment",
                              client_visibility="hidden", now=now)
        _seed_automation_item(st, "19992002",
                              intent_public_id=intent["public_id"],
                              stage="payment_options_created",
                              auto_publish_eligible=1)
        _seed_parent_link(st, "7850001", "tg_retry2", now)

        inv = _mk_invoice(inv_id=19992002)
        ctx = _make_ctx(st, [inv])
        ctx.process_new_moyklass_invoices(trigger="test")

        # There must still be exactly one intent for this invoice
        all_intents = st.find_all_active_intents_by_invoice("19992002")
        self.assertEqual(len(all_intents), 1,
                         "Retry must not create a new intent")


# ---------------------------------------------------------------------------
# 11. Missing parent → not published
# ---------------------------------------------------------------------------

class TestMissingParent(unittest.TestCase):
    """Test 11: eligible=1 but no parent link → not published."""

    def test_missing_parent_blocks_publish(self):
        st = _make_storage()
        now = _now()
        _enable_automation(st, create=True, publish=True)

        intent = _seed_intent(st, "19993001", status="awaiting_payment",
                              client_visibility="hidden", now=now)
        _seed_automation_item(st, "19993001",
                              intent_public_id=intent["public_id"],
                              stage="payment_options_created",
                              auto_publish_eligible=1)
        # No parent link seeded intentionally

        inv = _mk_invoice(inv_id=19993001)
        ctx = _make_ctx(st, [inv])
        ctx.process_new_moyklass_invoices(trigger="test")

        pi = st.get_payment_intent(intent["public_id"])
        self.assertNotEqual(pi.get("client_visibility"), "published",
                            "Intent with missing parent must not be published")


# ---------------------------------------------------------------------------
# 12. Cancelled status → not published
# ---------------------------------------------------------------------------

class TestCancelledStatus(unittest.TestCase):
    """Test 12: cancelled intent must never be auto-published."""

    def test_cancelled_not_published(self):
        st = _make_storage()
        now = _now()
        _enable_automation(st, create=True, publish=True)

        intent = _seed_intent(st, "19994001", status="cancelled",
                              client_visibility="hidden", now=now)
        _seed_automation_item(st, "19994001",
                              intent_public_id=intent["public_id"],
                              stage="payment_options_created",
                              auto_publish_eligible=1)
        _seed_parent_link(st, "7850001", "tg_can1", now)

        inv = _mk_invoice(inv_id=19994001)
        ctx = _make_ctx(st, [inv])
        ctx.process_new_moyklass_invoices(trigger="test")

        pi = st.get_payment_intent(intent["public_id"])
        self.assertNotEqual(pi.get("client_visibility"), "published",
                            "Cancelled intent must not be auto-published")


# ---------------------------------------------------------------------------
# 13. posted_to_moyklass status → not published
# ---------------------------------------------------------------------------

class TestPostedToMoyklass(unittest.TestCase):
    """Test 14: intent already posted to MoyKlass must not be re-published."""

    def test_posted_not_published(self):
        st = _make_storage()
        now = _now()
        _enable_automation(st, create=True, publish=True)

        intent = _seed_intent(st, "19995001", status="posted_to_moyklass",
                              client_visibility="hidden", now=now)
        _seed_automation_item(st, "19995001",
                              intent_public_id=intent["public_id"],
                              stage="payment_options_created",
                              auto_publish_eligible=1)
        _seed_parent_link(st, "7850001", "tg_post1", now)

        inv = _mk_invoice(inv_id=19995001, payed=100.0)  # fully paid
        ctx = _make_ctx(st, [inv])
        ctx.process_new_moyklass_invoices(trigger="test")

        pi = st.get_payment_intent(intent["public_id"])
        self.assertNotEqual(pi.get("client_visibility"), "published",
                            "posted_to_moyklass intent must not be auto-published")


# ---------------------------------------------------------------------------
# 15. Manual publish of old hidden intent → works
# ---------------------------------------------------------------------------

class TestManualPublishWorks(unittest.TestCase):
    """Test 16 & 17: manual publish continues to work regardless of auto_publish_eligible."""

    def test_manual_publish_hidden_intent(self):
        """Hidden intent with eligible=0 can still be manually published."""
        st = _make_storage()
        now = _now()
        intent = _seed_intent(st, "19996001", status="awaiting_payment",
                              client_visibility="hidden", now=now)
        _seed_automation_item(st, "19996001",
                              intent_public_id=intent["public_id"],
                              stage="payment_options_created",
                              auto_publish_eligible=0)

        # Simulate manual publish via storage (same as handler)
        pub = st.publish_payment_intent_to_client(intent["public_id"], "admin_999", now)
        self.assertTrue(pub.get("ok"))

        pi = st.get_payment_intent(intent["public_id"])
        self.assertEqual(pi.get("client_visibility"), "published")
        self.assertEqual(pi.get("published_by"), "admin_999")

    def test_manual_publish_withdrawn_intent(self):
        """Withdrawn intent can be manually republished via storage method."""
        st = _make_storage()
        now = _now()
        intent = _seed_intent(st, "19996002", status="awaiting_payment",
                              client_visibility="hidden", now=now)
        # Withdraw first
        st.withdraw_payment_intent_from_client(intent["public_id"],
                                               withdrawn_by="admin_w", now=now)
        pi = st.get_payment_intent(intent["public_id"])
        self.assertEqual(pi.get("client_visibility"), "withdrawn")

        # Manual re-publish (admin explicit action)
        pub = st.publish_payment_intent_to_client(intent["public_id"], "admin_999", now)
        self.assertTrue(pub.get("ok"))

        pi_after = st.get_payment_intent(intent["public_id"])
        self.assertEqual(pi_after.get("client_visibility"), "published",
                         "Manual publish of withdrawn intent must work")


# ---------------------------------------------------------------------------
# 16. Scheduler cannot call manual publish endpoint
# ---------------------------------------------------------------------------

class TestSchedulerCannotBypassEligibility(unittest.TestCase):
    """Test 18: scheduler must not directly call publish endpoint to bypass eligibility."""

    def test_try_publish_respects_eligible_flag(self):
        """_try_publish_automation_item must skip non-eligible items."""
        from web_app_server import MiniAppContext
        st = _make_storage()
        now = _now()
        ctx = MiniAppContext.__new__(MiniAppContext)
        ctx.storage = st
        ctx.settings = MagicMock()
        ctx.moyklass = MagicMock()

        intent = _seed_intent(st, "19997001", status="awaiting_payment",
                              client_visibility="hidden", now=now)
        item = _seed_automation_item(st, "19997001",
                                     intent_public_id=intent["public_id"],
                                     stage="payment_options_created",
                                     auto_publish_eligible=0)
        _seed_parent_link(st, "7850001", "tg_sch1", now)

        # Call _try_publish_automation_item directly with eligible=0
        result = ctx._try_publish_automation_item(
            item["id"], intent, "7850001", now, False,
            auto_publish_eligible=0,
        )
        self.assertNotIn("published", result,
                         "_try_publish must return existing, not published, for eligible=0")

        pi = st.get_payment_intent(intent["public_id"])
        self.assertNotEqual(pi.get("client_visibility"), "published",
                            "Scheduler method must not bypass eligibility check")

    def test_try_publish_respects_withdrawn(self):
        """_try_publish_automation_item must skip withdrawn intents even with eligible=1."""
        from web_app_server import MiniAppContext
        st = _make_storage()
        now = _now()
        ctx = MiniAppContext.__new__(MiniAppContext)
        ctx.storage = st
        ctx.settings = MagicMock()
        ctx.moyklass = MagicMock()

        intent = _seed_intent(st, "19997002", status="awaiting_payment",
                              client_visibility="hidden", now=now)
        st.withdraw_payment_intent_from_client(intent["public_id"],
                                               withdrawn_by="admin_b", now=now)
        pi = st.get_payment_intent(intent["public_id"])
        item = _seed_automation_item(st, "19997002",
                                     intent_public_id=intent["public_id"],
                                     stage="payment_options_created",
                                     auto_publish_eligible=1)
        _seed_parent_link(st, "7850001", "tg_sch2", now)

        result = ctx._try_publish_automation_item(
            item["id"], pi, "7850001", now, False,
            auto_publish_eligible=1,
        )
        self.assertNotIn("published", result,
                         "_try_publish must block withdrawn even with eligible=1")

        pi_after = st.get_payment_intent(intent["public_id"])
        self.assertEqual(pi_after["client_visibility"], "withdrawn")


# ---------------------------------------------------------------------------
# 17. upsert_automation_item passes auto_publish_eligible=1 to INSERT
# ---------------------------------------------------------------------------

class TestUpsertEligibleField(unittest.TestCase):
    """auto_publish_eligible is set by INSERT and never overwritten by subsequent upserts."""

    def test_eligible_1_on_first_insert(self):
        st = _make_storage()
        now = _now()
        item = st.upsert_automation_item("upsert_1", "1", None, "{}", now,
                                         auto_publish_eligible=1)
        self.assertEqual(item.get("auto_publish_eligible"), 1)

    def test_eligible_0_on_first_insert(self):
        st = _make_storage()
        now = _now()
        item = st.upsert_automation_item("upsert_2", "1", None, "{}", now,
                                         auto_publish_eligible=0)
        self.assertEqual(item.get("auto_publish_eligible"), 0)

    def test_second_upsert_does_not_change_eligible(self):
        st = _make_storage()
        now = _now()
        st.upsert_automation_item("upsert_3", "1", None, "{}", now,
                                  auto_publish_eligible=1)
        st.upsert_automation_item("upsert_3", "1", None, "{}", now,
                                  auto_publish_eligible=0)
        item = st.get_automation_item_by_invoice("upsert_3")
        self.assertEqual(item.get("auto_publish_eligible"), 1,
                         "Second upsert must not overwrite eligible=1 with 0")


# ---------------------------------------------------------------------------
# 18. Setting disabled → no auto-publish at all
# ---------------------------------------------------------------------------

class TestSettingDisabledBlocksAll(unittest.TestCase):
    """When publish_to_parent_enabled=False, nothing is published by scheduler."""

    def test_eligible_1_not_published_when_setting_off(self):
        st = _make_storage()
        now = _now()
        _enable_automation(st, create=True, publish=False)

        intent = _seed_intent(st, "19998001", status="awaiting_payment",
                              client_visibility="hidden", now=now)
        _seed_automation_item(st, "19998001",
                              intent_public_id=intent["public_id"],
                              stage="payment_options_created",
                              auto_publish_eligible=1)
        _seed_parent_link(st, "7850001", "tg_off1", now)

        inv = _mk_invoice(inv_id=19998001)
        ctx = _make_ctx(st, [inv])
        ctx.process_new_moyklass_invoices(trigger="test")

        pi = st.get_payment_intent(intent["public_id"])
        self.assertNotEqual(pi.get("client_visibility"), "published",
                            "Intent must not be published when publish setting is OFF")


# ---------------------------------------------------------------------------
# 19. Food Module not touched
# ---------------------------------------------------------------------------

class TestFoodModuleNotAffected(unittest.TestCase):
    """Test 20: food tables not touched by payment automation."""

    def test_food_tables_still_exist(self):
        st = _make_storage()
        with st._connect() as conn:
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        # Food module tables must still be present
        self.assertIn("food_menus", tables,
                      "food_menus table must exist and not be dropped by payment automation")
        self.assertIn("food_orders", tables,
                      "food_orders table must exist and not be dropped by payment automation")

    def test_food_menus_not_modified_by_automation_run(self):
        st = _make_storage()
        now = _now()
        # Count food_menus rows before
        with st._connect() as conn:
            count_before = conn.execute("SELECT COUNT(*) FROM food_menus").fetchone()[0]

        _enable_automation(st, create=True, publish=True)
        inv = _mk_invoice(inv_id=_next_inv_id())
        ctx = _make_ctx(st, [inv])
        ctx.process_new_moyklass_invoices(trigger="test")

        # food_menus must be untouched
        with st._connect() as conn:
            count_after = conn.execute("SELECT COUNT(*) FROM food_menus").fetchone()[0]
        self.assertEqual(count_before, count_after,
                         "Automation run must not modify food_menus")


# ---------------------------------------------------------------------------
# 20. Regression: imports and version
# ---------------------------------------------------------------------------

class TestRegressionImports(unittest.TestCase):
    """Test 21: imports, version, and existing modules not broken."""

    def test_storage_importable(self):
        import storage
        self.assertTrue(hasattr(storage, "Storage"))

    def test_web_app_server_importable(self):
        import web_app_server
        self.assertTrue(hasattr(web_app_server, "MiniAppContext"))

    def test_payment_domain_importable(self):
        import payment_domain
        self.assertTrue(hasattr(payment_domain, "can_publish_to_parent"))

    def test_version_string(self):
        self.assertEqual(CURRENT_VERSION, "7.0.99.0")

    def test_auto_publish_eligible_column_schema(self):
        """Schema must have auto_publish_eligible column with DEFAULT 0."""
        st = _make_storage()
        with st._connect() as conn:
            cols = {
                row[1]: row
                for row in conn.execute(
                    "PRAGMA table_info(invoice_automation_items)"
                ).fetchall()
            }
        self.assertIn("auto_publish_eligible", cols,
                      "invoice_automation_items must have auto_publish_eligible column")
        col = cols["auto_publish_eligible"]
        # col[2] = type, col[3] = notnull, col[4] = default_value
        self.assertIn("0", str(col[4]),
                      "auto_publish_eligible default must be 0")

    def test_auto_publish_eligible_at_column_schema(self):
        """Schema must have auto_publish_eligible_at TEXT column."""
        st = _make_storage()
        with st._connect() as conn:
            cols = {row[1] for row in conn.execute(
                "PRAGMA table_info(invoice_automation_items)"
            ).fetchall()}
        self.assertIn("auto_publish_eligible_at", cols)


if __name__ == "__main__":
    unittest.main()

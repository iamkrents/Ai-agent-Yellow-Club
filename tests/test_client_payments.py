"""Regression tests for v7.0.93.2.3 — client parent payment display fix.

Root cause: client_payments_list read ERIP account only from payment_intent_options,
so legacy intents (ERIP in payment_intents.bepaid_account_number, no option row) showed
no ERIP to parent.  Card used if/else so acquiring hid ERIP.  awaiting_payment status
was not in CLIENT_PAYMENT_STATUS_LABELS so raw string showed to parent.

Covers:
  Backend (server client_payments_list):
    1.  Legacy ERIP account from payment_intents returned to parent
    2.  ERIP account from active option row returned to parent
    3.  Option-row account takes priority over legacy field when both exist
    4.  Cancelled ERIP option not returned (fallback to legacy if uid set)
    5.  Superseded ERIP option not returned (fallback to legacy if uid set)
    6.  Acquiring URL from active option returned to parent
    7.  Dual-channel intent: both erip_account_number and acquiring_payment_url returned
    8.  ycpi-like fixture with 9748998260715 returns ERIP in client response

  Frontend static analysis (app.js):
    9.  awaiting_payment in CLIENT_PAYMENT_STATUS_LABELS
    10. awaiting_payment maps to «Ожидает оплаты»
    11. posted_to_moyklass maps to «Оплата зачислена»
    12. ERIP copy button (cp-copy-btn) present in renderClientPaymentCard
    13. Card pay button (cp-card-pay-btn) present for acquiring
    14. navigator.clipboard.writeText called in cpCopyErip
    15. cpCopyErip function exists
    16. _fmtPeriodRu function exists

  Security (static analysis):
    17. checkout_token not returned from client_payments_list
    18. bepaid_uid not returned from client_payments_list
    19. client_payments_list does not call BePaidClient
    20. client_payments_list does not call self.moyklass

  CSS:
    21. cp-card styles defined in styles.css
    22. cp-copy-btn styles defined in styles.css
    23. cp-card-pay-btn styles defined in styles.css
    24. cp-status-paid and cp-status-pending defined in styles.css

  Existing suite guard:
    25. test_parent_payments importable
    26. test_publish_preview importable
    27. test_client_parent_links importable
    28. version marker is v7.0.93.2.3
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage

APP_JS = ROOT / "miniapp" / "app.js"
INDEX_HTML = ROOT / "miniapp" / "index.html"
SERVER_PY = ROOT / "web_app_server.py"
STYLES_CSS = ROOT / "miniapp" / "styles.css"

CURRENT_VERSION = "7.1.0"
NOW = "2026-07-16T10:00:00"
PARENT_ID = "789012"  # numeric-string telegram id used across tests


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _seed_erip_intent(storage: Storage, mk_user_id: int = 6001,
                      bepaid_uid: str | None = "uid_erip",
                      bepaid_account_number: str | None = "9748998260715") -> dict:
    pi = storage.create_payment_intent({
        "mk_user_id": mk_user_id,
        "student_name": "Тест Студент",
        "amount_minor": 100,
        "amount_byn": 1.0,
        "currency": "BYN",
        "purpose": "current_month",
        "period_month": "2026-07",
        "payment_method": "erip",
        "created_by_tg_id": 1,
        "created_by_name": "admin",
        "comment": "",
        "source": "manual",
    })
    public_id = pi["public_id"]
    if bepaid_uid:
        storage.payment_intent_update_status(public_id, "bepaid_creating")
        storage.payment_intent_save_bepaid_success(
            public_id,
            bepaid_uid=bepaid_uid,
            bepaid_order_id="ord_test",
            bepaid_account_number=bepaid_account_number or "",
            bepaid_payment_url="",
            bepaid_status="pending",
        )
    return storage.get_payment_intent(public_id)


def _seed_parent_link(storage: Storage, mk_user_id: str, parent_telegram_id: str) -> None:
    result = storage.create_client_link_code(mk_user_id, "Тест Ученик", "test_admin")
    storage.link_client_child(parent_telegram_id, result["code"], NOW)


def _publish(storage: Storage, public_id: str) -> None:
    storage.publish_payment_intent_to_client(public_id, "admin", NOW)


def _make_parent_ctx(storage: Storage):
    """Minimal MiniAppContext patched so client_payments_list runs as parent."""
    import web_app_server as _srv

    ctx = _srv.MiniAppContext.__new__(_srv.MiniAppContext)
    ctx.storage = storage
    ctx._role_for_user = lambda uid: "parent"
    return ctx


def _parent_auth():
    return {"user_id": int(PARENT_ID)}


def _get_payments(storage: Storage) -> list[dict]:
    ctx = _make_parent_ctx(storage)
    result = ctx.client_payments_list(_parent_auth())
    assert result.get("ok"), f"client_payments_list failed: {result}"
    return result["payments"]


# ---------------------------------------------------------------------------
# Backend tests
# ---------------------------------------------------------------------------

class Test01BackendLegacyErip(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        pi = _seed_erip_intent(self.storage, bepaid_uid="uid_leg",
                               bepaid_account_number="9748998260715")
        _seed_parent_link(self.storage, str(pi["mk_user_id"]), PARENT_ID)
        _publish(self.storage, pi["public_id"])

    def test_01_legacy_erip_returned_to_parent(self):
        payments = _get_payments(self.storage)
        self.assertEqual(len(payments), 1)
        self.assertEqual(payments[0]["erip_account_number"], "9748998260715",
                         "legacy bepaid_account_number must be returned as erip_account_number")


class Test02BackendOptionRowErip(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        pi = _seed_erip_intent(self.storage, bepaid_uid=None, bepaid_account_number=None)
        self.storage.create_payment_intent_option(
            payment_intent_id=int(pi["id"]),
            intent_public_id=pi["public_id"],
            channel="erip",
            shop_type="erip",
            bepaid_account_number="9748998260716",
        )
        _seed_parent_link(self.storage, str(pi["mk_user_id"]), PARENT_ID)
        _publish(self.storage, pi["public_id"])

    def test_02_option_row_erip_returned_to_parent(self):
        payments = _get_payments(self.storage)
        self.assertEqual(len(payments), 1)
        self.assertEqual(payments[0]["erip_account_number"], "9748998260716",
                         "erip option row bepaid_account_number must be returned")


class Test03BackendOptionPriority(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        # Both: legacy field AND option row (option should win)
        pi = _seed_erip_intent(self.storage, bepaid_uid="uid_both",
                               bepaid_account_number="LEGACY_ACCT")
        self.storage.create_payment_intent_option(
            payment_intent_id=int(pi["id"]),
            intent_public_id=pi["public_id"],
            channel="erip",
            shop_type="erip",
            bepaid_account_number="OPTION_ACCT",
        )
        _seed_parent_link(self.storage, str(pi["mk_user_id"]), PARENT_ID)
        _publish(self.storage, pi["public_id"])

    def test_03_option_takes_priority_over_legacy(self):
        payments = _get_payments(self.storage)
        self.assertEqual(len(payments), 1)
        self.assertEqual(payments[0]["erip_account_number"], "OPTION_ACCT",
                         "active option row must take priority over legacy field")


class Test04BackendCancelledOptionFallback(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        pi = _seed_erip_intent(self.storage, bepaid_uid="uid_cancel",
                               bepaid_account_number="9748998260715")
        # Create option row and cancel it
        self.storage.create_payment_intent_option(
            payment_intent_id=int(pi["id"]),
            intent_public_id=pi["public_id"],
            channel="erip",
            shop_type="erip",
            bepaid_account_number="CANCELLED_ACCT",
        )
        self.storage.cancel_options_for_cleanup(pi["public_id"], NOW)
        _seed_parent_link(self.storage, str(pi["mk_user_id"]), PARENT_ID)
        _publish(self.storage, pi["public_id"])

    def test_04_cancelled_option_falls_back_to_legacy(self):
        payments = _get_payments(self.storage)
        self.assertEqual(len(payments), 1)
        acct = payments[0]["erip_account_number"]
        self.assertNotEqual(acct, "CANCELLED_ACCT",
                            "cancelled option must not be returned")
        # Falls back to legacy field since bepaid_uid is set
        self.assertEqual(acct, "9748998260715",
                         "must fallback to legacy payment_intents field when option cancelled")


class Test05BackendSupersededOptionFallback(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        pi = _seed_erip_intent(self.storage, bepaid_uid="uid_sup",
                               bepaid_account_number="9748998260715")
        opt1 = self.storage.create_payment_intent_option(
            payment_intent_id=int(pi["id"]),
            intent_public_id=pi["public_id"],
            channel="erip",
            shop_type="erip",
            bepaid_account_number="SUPERSEDED_ACCT",
        )
        # Supersede opt1 by creating a second that wins
        opt2 = self.storage.create_payment_intent_option(
            payment_intent_id=int(pi["id"]),
            intent_public_id=pi["public_id"],
            channel="erip",
            shop_type="erip",
            bepaid_account_number="",  # winner has no account (legacy field applies)
        )
        self.storage.supersede_sibling_options(pi["public_id"], int(opt2["id"]))
        _seed_parent_link(self.storage, str(pi["mk_user_id"]), PARENT_ID)
        _publish(self.storage, pi["public_id"])

    def test_05_superseded_option_not_returned(self):
        payments = _get_payments(self.storage)
        self.assertEqual(len(payments), 1)
        acct = payments[0]["erip_account_number"]
        self.assertNotEqual(acct, "SUPERSEDED_ACCT",
                            "superseded option account must not be returned")


class Test06BackendAcquiringUrl(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        pi = _seed_erip_intent(self.storage, bepaid_uid=None, bepaid_account_number=None)
        self.storage.create_payment_intent_option(
            payment_intent_id=int(pi["id"]),
            intent_public_id=pi["public_id"],
            channel="acquiring",
            shop_type="acquiring",
            payment_url="https://checkout.bepaid.by/pay/test123",
        )
        _seed_parent_link(self.storage, str(pi["mk_user_id"]), PARENT_ID)
        _publish(self.storage, pi["public_id"])

    def test_06_acquiring_url_returned(self):
        payments = _get_payments(self.storage)
        self.assertEqual(len(payments), 1)
        self.assertEqual(payments[0]["acquiring_payment_url"],
                         "https://checkout.bepaid.by/pay/test123")


class Test07BackendDualChannel(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        pi = _seed_erip_intent(self.storage, bepaid_uid="uid_dual",
                               bepaid_account_number="9748998260715")
        self.storage.create_payment_intent_option(
            payment_intent_id=int(pi["id"]),
            intent_public_id=pi["public_id"],
            channel="acquiring",
            shop_type="acquiring",
            payment_url="https://checkout.bepaid.by/pay/dual",
        )
        _seed_parent_link(self.storage, str(pi["mk_user_id"]), PARENT_ID)
        _publish(self.storage, pi["public_id"])

    def test_07_both_channels_returned(self):
        payments = _get_payments(self.storage)
        self.assertEqual(len(payments), 1)
        p = payments[0]
        self.assertEqual(p["erip_account_number"], "9748998260715",
                         "ERIP must be returned for dual-channel intent")
        self.assertEqual(p["acquiring_payment_url"],
                         "https://checkout.bepaid.by/pay/dual",
                         "acquiring URL must be returned for dual-channel intent")


class Test08BackendFixtureYcpi(unittest.TestCase):
    """Reproduces the exact scenario of ycpi_202607_15."""
    def setUp(self):
        self.storage = _tmp_storage()
        pi = _seed_erip_intent(self.storage, bepaid_uid="uid_real",
                               bepaid_account_number="9748998260715")
        # Acquiring option as prepare_options would have created
        self.storage.create_payment_intent_option(
            payment_intent_id=int(pi["id"]),
            intent_public_id=pi["public_id"],
            channel="acquiring",
            shop_type="acquiring",
            payment_url="https://checkout.bepaid.by/pay/fixture",
        )
        _seed_parent_link(self.storage, str(pi["mk_user_id"]), PARENT_ID)
        _publish(self.storage, pi["public_id"])

    def test_08_erip_9748998260715_returned_to_parent(self):
        payments = _get_payments(self.storage)
        self.assertEqual(len(payments), 1)
        self.assertEqual(payments[0]["erip_account_number"], "9748998260715",
                         "account 9748998260715 must be visible to parent")
        self.assertIsNotNone(payments[0]["acquiring_payment_url"],
                             "acquiring URL must also be returned")


# ---------------------------------------------------------------------------
# Frontend static analysis
# ---------------------------------------------------------------------------

class Test09FrontendStaticAnalysis(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")
        # Extract CLIENT_PAYMENT_STATUS_LABELS block
        start = cls.js.find("CLIENT_PAYMENT_STATUS_LABELS")
        end = cls.js.find("};", start) + 2
        cls.labels_block = cls.js[start:end]
        # Extract renderClientPaymentCard body
        start2 = cls.js.find("function renderClientPaymentCard(")
        end2 = cls.js.find("\nfunction ", start2 + 1)
        cls.card_fn = cls.js[start2:end2]

    def test_09_awaiting_payment_in_labels(self):
        self.assertIn("awaiting_payment", self.labels_block)

    def test_10_awaiting_payment_maps_to_ru(self):
        idx = self.labels_block.find("awaiting_payment")
        line_end = self.labels_block.find("\n", idx)
        line = self.labels_block[idx:line_end]
        self.assertIn("Ожидает оплаты", line,
                      "awaiting_payment must map to «Ожидает оплаты»")

    def test_11_posted_to_moyklass_maps_to_zachislena(self):
        idx = self.labels_block.find("posted_to_moyklass")
        line_end = self.labels_block.find("\n", idx)
        line = self.labels_block[idx:line_end]
        self.assertIn("зачислена", line.lower(),
                      "posted_to_moyklass must contain «зачислена»")

    def test_12_erip_copy_button_in_card(self):
        self.assertIn("cp-copy-btn", self.card_fn)
        self.assertIn("Скопировать номер заказа", self.card_fn)

    def test_13_card_pay_button_for_acquiring(self):
        self.assertIn("cp-card-pay-btn", self.card_fn)
        self.assertIn("Оплатить банковской картой", self.card_fn)

    def test_14_clipboard_used_in_copy_fn(self):
        self.assertIn("navigator.clipboard.writeText", self.js)

    def test_15_copy_functions_exist(self):
        self.assertIn("function cpCopyOrderNum(", self.js)
        self.assertIn("function cpCopyEripCode(", self.js)

    def test_16_fmtPeriodRu_function_exists(self):
        self.assertIn("function _fmtPeriodRu(", self.js)

    def test_17_both_channels_shown_not_else_if(self):
        """Card must show BOTH erip and acquiring, not either/or."""
        # renderClientPaymentCard must not use 'else if' between erip and acquiring
        # Check that both blocks are independent (no else if pattern)
        self.assertIn("erip_account_number", self.card_fn)
        self.assertIn("acquiring_payment_url", self.card_fn)
        # Verify they're not in an else-if chain: both are standalone if blocks
        self.assertNotIn("} else if (pi.erip_account_number", self.card_fn)
        self.assertNotIn("} else if (pi.acquiring_payment_url", self.card_fn)


# ---------------------------------------------------------------------------
# Security static analysis
# ---------------------------------------------------------------------------

class Test10Security(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = SERVER_PY.read_text(encoding="utf-8")
        start = cls.src.find("def client_payments_list(")
        end = cls.src.find("\n    def ", start + 1)
        cls.method = cls.src[start:end]

    def test_17_checkout_token_not_in_response(self):
        """checkout_token must never be returned to parent."""
        self.assertNotIn('"checkout_token"', self.method)
        self.assertNotIn("checkout_token", self.method)

    def test_18_bepaid_uid_not_in_response(self):
        """bepaid_uid must not be returned to parent (only used for fallback lookup)."""
        # bepaid_uid may appear as lookup key but not in the returned dict
        result_start = self.method.rfind("result.append({")
        result_block = self.method[result_start:self.method.find("})", result_start) + 2]
        self.assertNotIn('"bepaid_uid"', result_block)

    def test_19_no_bepaid_client_call(self):
        """client_payments_list must not call bePaid."""
        self.assertNotIn("BePaidClient(", self.method)
        self.assertNotIn("bepaid_client", self.method)

    def test_20_no_moyklass_call(self):
        """client_payments_list must not call MoyKlass."""
        self.assertNotIn("self.moyklass", self.method)


# ---------------------------------------------------------------------------
# CSS tests
# ---------------------------------------------------------------------------

class Test11CSS(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = STYLES_CSS.read_text(encoding="utf-8")

    def test_21_cp_card_defined(self):
        self.assertIn(".cp-card", self.css)

    def test_22_cp_copy_btn_defined(self):
        self.assertIn(".cp-copy-btn", self.css)

    def test_23_cp_card_pay_btn_defined(self):
        self.assertIn(".cp-card-pay-btn", self.css)

    def test_24_cp_status_classes_defined(self):
        self.assertIn(".cp-status-paid", self.css)
        self.assertIn(".cp-status-pending", self.css)


# ---------------------------------------------------------------------------
# Existing suite guard
# ---------------------------------------------------------------------------

class Test12ExistingGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_25_parent_payments_importable(self):
        import tests.test_parent_payments  # noqa: F401

    def test_26_publish_preview_importable(self):
        import tests.test_publish_preview  # noqa: F401

    def test_27_client_parent_links_importable(self):
        import tests.test_client_parent_links  # noqa: F401

    def test_28_version_marker(self):
        self.assertIn(f'console.log("MiniApp version: v{CURRENT_VERSION}")', self.js)
        self.assertIn(f"v={CURRENT_VERSION}", self.html)


if __name__ == "__main__":
    unittest.main()

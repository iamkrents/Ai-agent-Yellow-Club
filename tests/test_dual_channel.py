"""Tests for v7.0.92.2 dual-channel payment options (ERIP + acquiring).

Covers:
  - config.py: moyklass_acquiring_payment_type_id
  - storage.py: payment_intent_options table + new methods
  - bepaid_client.py: create_acquiring_checkout stub
  - web_app_server.py: moyklass_payment_types dual-channel response,
                        payment_intent_post_to_moyklass paid_channel selection

All tests use temporary in-process storage; no network calls; no .env reads.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage


# ─── helpers ──────────────────────────────────────────────────────────────────

def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _make_intent(st: Storage, *, public_id: str = "ycpi_test_001",
                 mk_user_id: int = 1001, amount_minor: int = 22900,
                 status: str = "bepaid_created") -> dict:
    return st.create_payment_intent({
        "public_id": public_id,
        "mk_user_id": mk_user_id,
        "student_name": "Test Student",
        "amount_minor": amount_minor,
        "amount_byn": amount_minor / 100,
        "currency": "BYN",
        "purpose": "current_month",
        "period_month": "2026-07",
        "payment_method": "erip",
        "status": status,
        "created_by_tg_id": 9999,
        "created_by_name": "tester",
    })


# ─── 1. config.py ─────────────────────────────────────────────────────────────

class TestConfigAcquiringPaymentTypeId(unittest.TestCase):

    def _load(self, env_overrides: dict) -> object:
        from config import load_settings
        patched = {k: v for k, v in os.environ.items()}
        patched.update(env_overrides)
        with patch.dict(os.environ, patched, clear=True):
            return load_settings()

    def test_acquiring_payment_type_id_default_zero(self):
        """MOYKLASS_ACQUIRING_PAYMENT_TYPE_ID defaults to 0 when not set."""
        cfg = self._load({"MOYKLASS_ACQUIRING_PAYMENT_TYPE_ID": ""})
        self.assertEqual(cfg.moyklass_acquiring_payment_type_id, 0)

    def test_acquiring_payment_type_id_reads_env(self):
        """MOYKLASS_ACQUIRING_PAYMENT_TYPE_ID is loaded from env."""
        cfg = self._load({"MOYKLASS_ACQUIRING_PAYMENT_TYPE_ID": "55949"})
        self.assertEqual(cfg.moyklass_acquiring_payment_type_id, 55949)

    def test_erip_payment_type_id_still_works(self):
        """moyklass_erip_payment_type_id is unaffected by new field."""
        cfg = self._load({
            "MOYKLASS_ERIP_PAYMENT_TYPE_ID": "55948",
            "MOYKLASS_ACQUIRING_PAYMENT_TYPE_ID": "55949",
        })
        self.assertEqual(cfg.moyklass_erip_payment_type_id, 55948)
        self.assertEqual(cfg.moyklass_acquiring_payment_type_id, 55949)

    def test_both_fields_independent(self):
        """ERIP and acquiring type IDs are independent fields."""
        cfg = self._load({
            "MOYKLASS_ERIP_PAYMENT_TYPE_ID": "100",
            "MOYKLASS_ACQUIRING_PAYMENT_TYPE_ID": "200",
        })
        self.assertNotEqual(cfg.moyklass_erip_payment_type_id,
                            cfg.moyklass_acquiring_payment_type_id)


# ─── 2. storage: payment_intent_options table ─────────────────────────────────

class TestCreatePaymentIntentOption(unittest.TestCase):

    def setUp(self):
        self.st = _tmp_storage()
        pi = _make_intent(self.st)
        self.pi_id = pi["id"]
        self.public_id = pi["public_id"]

    def test_create_option_erip(self):
        """create_payment_intent_option stores erip channel row."""
        opt = self.st.create_payment_intent_option(
            payment_intent_id=self.pi_id,
            intent_public_id=self.public_id,
            channel="erip",
            shop_type="erip",
            bepaid_tracking_id="ycpi_test_001",
            bepaid_order_id="100000000001",
            bepaid_account_number="100012607001",
            payment_url="https://example.com/pay/erip",
        )
        self.assertEqual(opt["channel"], "erip")
        self.assertEqual(opt["status"], "created")
        self.assertIsNotNone(opt["id"])

    def test_create_option_acquiring(self):
        """create_payment_intent_option stores acquiring channel row."""
        opt = self.st.create_payment_intent_option(
            payment_intent_id=self.pi_id,
            intent_public_id=self.public_id,
            channel="acquiring",
            shop_type="acquiring",
            bepaid_tracking_id="ycpi_test_001",
            bepaid_order_id="100000000002",
            payment_url="https://example.com/pay/card",
        )
        self.assertEqual(opt["channel"], "acquiring")
        self.assertEqual(opt["status"], "created")

    def test_create_option_returns_full_row(self):
        """create_payment_intent_option returns all fields including timestamps."""
        opt = self.st.create_payment_intent_option(
            payment_intent_id=self.pi_id,
            intent_public_id=self.public_id,
            channel="erip",
            shop_type="erip",
        )
        self.assertIn("created_at", opt)
        self.assertIn("updated_at", opt)
        self.assertEqual(opt["intent_public_id"], self.public_id)


class TestGetOptionsForIntent(unittest.TestCase):

    def setUp(self):
        self.st = _tmp_storage()
        pi = _make_intent(self.st)
        self.pi_id = pi["id"]
        self.public_id = pi["public_id"]

    def test_get_options_empty(self):
        """get_options_for_intent returns [] when no options exist."""
        result = self.st.get_options_for_intent(self.public_id)
        self.assertEqual(result, [])

    def test_get_options_two_channels(self):
        """get_options_for_intent returns both erip and acquiring rows."""
        self.st.create_payment_intent_option(
            payment_intent_id=self.pi_id, intent_public_id=self.public_id,
            channel="erip", shop_type="erip",
        )
        self.st.create_payment_intent_option(
            payment_intent_id=self.pi_id, intent_public_id=self.public_id,
            channel="acquiring", shop_type="acquiring",
        )
        opts = self.st.get_options_for_intent(self.public_id)
        self.assertEqual(len(opts), 2)
        channels = {o["channel"] for o in opts}
        self.assertEqual(channels, {"erip", "acquiring"})

    def test_get_option_by_channel_erip(self):
        """get_option_by_channel returns the erip row."""
        self.st.create_payment_intent_option(
            payment_intent_id=self.pi_id, intent_public_id=self.public_id,
            channel="erip", shop_type="erip",
        )
        self.st.create_payment_intent_option(
            payment_intent_id=self.pi_id, intent_public_id=self.public_id,
            channel="acquiring", shop_type="acquiring",
        )
        opt = self.st.get_option_by_channel(self.public_id, "erip")
        self.assertIsNotNone(opt)
        self.assertEqual(opt["channel"], "erip")

    def test_get_option_by_channel_not_found(self):
        """get_option_by_channel returns None for unknown channel."""
        opt = self.st.get_option_by_channel(self.public_id, "erip")
        self.assertIsNone(opt)


class TestGetOptionByProviderRef(unittest.TestCase):

    def setUp(self):
        self.st = _tmp_storage()
        pi = _make_intent(self.st)
        self.pi_id = pi["id"]
        self.public_id = pi["public_id"]
        self.opt = self.st.create_payment_intent_option(
            payment_intent_id=self.pi_id,
            intent_public_id=self.public_id,
            channel="erip",
            shop_type="erip",
            bepaid_tracking_id="ycpi_tc_tracking",
            bepaid_order_id="100000000042",
            bepaid_uid="uid-xyz-001",
            bepaid_account_number="100112607042",
        )

    def test_lookup_by_tracking_id(self):
        """get_option_by_provider_ref finds option by bepaid_tracking_id."""
        found = self.st.get_option_by_provider_ref(bepaid_tracking_id="ycpi_tc_tracking")
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], self.opt["id"])

    def test_lookup_by_order_id(self):
        """get_option_by_provider_ref finds option by bepaid_order_id."""
        found = self.st.get_option_by_provider_ref(bepaid_order_id="100000000042")
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], self.opt["id"])

    def test_lookup_by_bepaid_uid(self):
        """get_option_by_provider_ref finds option by bepaid_uid."""
        found = self.st.get_option_by_provider_ref(bepaid_uid="uid-xyz-001")
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], self.opt["id"])

    def test_lookup_not_found(self):
        """get_option_by_provider_ref returns None when no match."""
        found = self.st.get_option_by_provider_ref(bepaid_tracking_id="no_such_tracking")
        self.assertIsNone(found)


# ─── 3. storage: option state transitions ─────────────────────────────────────

class TestMarkOptionPaid(unittest.TestCase):

    def setUp(self):
        self.st = _tmp_storage()
        pi = _make_intent(self.st)
        self.pi_id = pi["id"]
        self.public_id = pi["public_id"]
        self.opt = self.st.create_payment_intent_option(
            payment_intent_id=self.pi_id, intent_public_id=self.public_id,
            channel="erip", shop_type="erip",
        )

    def test_mark_option_paid_success(self):
        """mark_option_paid transitions status to 'paid'."""
        result = self.st.mark_option_paid(
            self.opt["id"],
            tx_uid="tx-001",
            paid_at="2026-07-14T10:00:00",
            amount_minor=22900,
            currency="BYN",
        )
        self.assertTrue(result.get("ok"))
        self.assertTrue(result.get("marked_paid"))
        self.assertEqual(result["option"]["status"], "paid")
        self.assertEqual(result["option"]["transaction_uid"], "tx-001")

    def test_mark_option_paid_idempotent(self):
        """mark_option_paid with same tx_uid is idempotent."""
        self.st.mark_option_paid(
            self.opt["id"], tx_uid="tx-001", paid_at="2026-07-14T10:00:00",
            amount_minor=22900, currency="BYN",
        )
        result = self.st.mark_option_paid(
            self.opt["id"], tx_uid="tx-001", paid_at="2026-07-14T10:00:00",
            amount_minor=22900, currency="BYN",
        )
        self.assertTrue(result.get("ok"))
        self.assertTrue(result.get("idempotent"))

    def test_mark_option_paid_wrong_status(self):
        """mark_option_paid on superseded option returns wrong_state."""
        with self.st._connect() as conn:
            conn.execute(
                "UPDATE payment_intent_options SET status='superseded' WHERE id=?",
                (self.opt["id"],),
            )
        result = self.st.mark_option_paid(
            self.opt["id"], tx_uid="tx-002", paid_at="2026-07-14T10:00:00",
            amount_minor=22900, currency="BYN",
        )
        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("wrong_state"))


class TestMarkOptionFailed(unittest.TestCase):

    def test_mark_option_failed_success(self):
        """mark_option_failed transitions status to 'failed'."""
        st = _tmp_storage()
        pi = _make_intent(st)
        opt = st.create_payment_intent_option(
            payment_intent_id=pi["id"], intent_public_id=pi["public_id"],
            channel="acquiring", shop_type="acquiring",
        )
        result = st.mark_option_failed(
            opt["id"], error_code="payment_declined", error_message="Card declined",
        )
        self.assertTrue(result.get("ok"))
        self.assertEqual(result["option"]["status"], "failed")
        self.assertEqual(result["option"]["error_code"], "payment_declined")

    def test_failed_option_does_not_block_sibling(self):
        """A failed acquiring option leaves the erip option still in 'created'."""
        st = _tmp_storage()
        pi = _make_intent(st)
        erip_opt = st.create_payment_intent_option(
            payment_intent_id=pi["id"], intent_public_id=pi["public_id"],
            channel="erip", shop_type="erip",
        )
        acq_opt = st.create_payment_intent_option(
            payment_intent_id=pi["id"], intent_public_id=pi["public_id"],
            channel="acquiring", shop_type="acquiring",
        )
        st.mark_option_failed(acq_opt["id"], error_code="declined")
        erip = st.get_option_by_channel(pi["public_id"], "erip")
        self.assertEqual(erip["status"], "created")


class TestMarkOptionExpired(unittest.TestCase):

    def test_mark_option_expired_success(self):
        """mark_option_expired transitions status to 'expired'."""
        st = _tmp_storage()
        pi = _make_intent(st)
        opt = st.create_payment_intent_option(
            payment_intent_id=pi["id"], intent_public_id=pi["public_id"],
            channel="erip", shop_type="erip",
        )
        result = st.mark_option_expired(opt["id"])
        self.assertTrue(result.get("ok"))
        self.assertEqual(result["option"]["status"], "expired")

    def test_cannot_expire_paid_option(self):
        """mark_option_expired on paid option returns wrong_state."""
        st = _tmp_storage()
        pi = _make_intent(st)
        opt = st.create_payment_intent_option(
            payment_intent_id=pi["id"], intent_public_id=pi["public_id"],
            channel="erip", shop_type="erip",
        )
        st.mark_option_paid(
            opt["id"], tx_uid="tx-z", paid_at="2026-07-14T10:00:00",
            amount_minor=22900, currency="BYN",
        )
        result = st.mark_option_expired(opt["id"])
        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("wrong_state"))


class TestSupersedeSiblingOptions(unittest.TestCase):

    def test_supersede_sibling_on_erip_win(self):
        """After ERIP pays, acquiring sibling becomes superseded."""
        st = _tmp_storage()
        pi = _make_intent(st)
        erip_opt = st.create_payment_intent_option(
            payment_intent_id=pi["id"], intent_public_id=pi["public_id"],
            channel="erip", shop_type="erip",
        )
        acq_opt = st.create_payment_intent_option(
            payment_intent_id=pi["id"], intent_public_id=pi["public_id"],
            channel="acquiring", shop_type="acquiring",
        )
        changed = st.supersede_sibling_options(pi["public_id"], erip_opt["id"])
        self.assertEqual(changed, 1)
        acq = st.get_option_by_channel(pi["public_id"], "acquiring")
        self.assertEqual(acq["status"], "superseded")
        erip = st.get_option_by_channel(pi["public_id"], "erip")
        self.assertEqual(erip["status"], "created")

    def test_supersede_does_not_touch_paid_sibling(self):
        """supersede_sibling_options leaves already-paid rows alone."""
        st = _tmp_storage()
        pi = _make_intent(st)
        erip_opt = st.create_payment_intent_option(
            payment_intent_id=pi["id"], intent_public_id=pi["public_id"],
            channel="erip", shop_type="erip",
        )
        acq_opt = st.create_payment_intent_option(
            payment_intent_id=pi["id"], intent_public_id=pi["public_id"],
            channel="acquiring", shop_type="acquiring",
        )
        st.mark_option_paid(
            acq_opt["id"], tx_uid="tx-acq", paid_at="2026-07-14T10:00:00",
            amount_minor=22900, currency="BYN",
        )
        st.supersede_sibling_options(pi["public_id"], erip_opt["id"])
        acq = st.get_option_by_channel(pi["public_id"], "acquiring")
        self.assertEqual(acq["status"], "paid")


# ─── 4. storage: payment_intent_mark_paid_via_option ─────────────────────────

class TestMarkPaidViaOption(unittest.TestCase):

    def setUp(self):
        self.st = _tmp_storage()
        pi = _make_intent(self.st)  # default status='bepaid_created'
        self.pi_id = pi["id"]
        self.public_id = pi["public_id"]
        self.erip_opt = self.st.create_payment_intent_option(
            payment_intent_id=self.pi_id, intent_public_id=self.public_id,
            channel="erip", shop_type="erip",
            bepaid_tracking_id="ycpi_test_001",
        )
        self.acq_opt = self.st.create_payment_intent_option(
            payment_intent_id=self.pi_id, intent_public_id=self.public_id,
            channel="acquiring", shop_type="acquiring",
            bepaid_tracking_id="ycpi_test_001",
        )

    def test_mark_paid_via_erip_option(self):
        """payment_intent_mark_paid_via_option sets status=paid and paid_channel=erip."""
        result = self.st.payment_intent_mark_paid_via_option(
            self.public_id,
            option_id=self.erip_opt["id"],
            channel="erip",
            tx_uid="tx-erip-001",
            amount_minor=22900,
            currency="BYN",
            paid_at="2026-07-14T10:00:00",
        )
        self.assertTrue(result.get("ok"))
        self.assertTrue(result.get("marked_paid"))
        pi = result["intent"]
        self.assertEqual(pi["status"], "paid")
        self.assertEqual(pi["paid_channel"], "erip")
        self.assertEqual(pi["paid_option_id"], self.erip_opt["id"])

    def test_mark_paid_via_acquiring_option(self):
        """payment_intent_mark_paid_via_option sets paid_channel=acquiring."""
        result = self.st.payment_intent_mark_paid_via_option(
            self.public_id,
            option_id=self.acq_opt["id"],
            channel="acquiring",
            tx_uid="tx-acq-001",
            amount_minor=22900,
            currency="BYN",
            paid_at="2026-07-14T10:00:00",
        )
        self.assertTrue(result.get("ok"))
        pi = result["intent"]
        self.assertEqual(pi["paid_channel"], "acquiring")

    def test_siblings_superseded_on_payment(self):
        """payment_intent_mark_paid_via_option supersedes sibling options."""
        result = self.st.payment_intent_mark_paid_via_option(
            self.public_id,
            option_id=self.erip_opt["id"],
            channel="erip",
            tx_uid="tx-erip-001",
            amount_minor=22900,
            currency="BYN",
            paid_at="2026-07-14T10:00:00",
        )
        self.assertEqual(result.get("siblings_superseded"), 1)
        acq = self.st.get_option_by_channel(self.public_id, "acquiring")
        self.assertEqual(acq["status"], "superseded")

    def test_mark_paid_via_option_idempotent(self):
        """payment_intent_mark_paid_via_option is idempotent on same tx_uid."""
        kw = dict(
            option_id=self.erip_opt["id"], channel="erip",
            tx_uid="tx-erip-001", amount_minor=22900, currency="BYN",
            paid_at="2026-07-14T10:00:00",
        )
        self.st.payment_intent_mark_paid_via_option(self.public_id, **kw)
        result = self.st.payment_intent_mark_paid_via_option(self.public_id, **kw)
        self.assertTrue(result.get("ok"))
        self.assertTrue(result.get("idempotent"))

    def test_double_payment_detected(self):
        """Second payment with different tx_uid → double_payment_requires_check."""
        self.st.payment_intent_mark_paid_via_option(
            self.public_id,
            option_id=self.erip_opt["id"], channel="erip",
            tx_uid="tx-erip-001", amount_minor=22900, currency="BYN",
            paid_at="2026-07-14T10:00:00",
        )
        result = self.st.payment_intent_mark_paid_via_option(
            self.public_id,
            option_id=self.acq_opt["id"], channel="acquiring",
            tx_uid="tx-acq-different", amount_minor=22900, currency="BYN",
            paid_at="2026-07-14T10:05:00",
        )
        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("double_payment"))
        with self.st._connect() as conn:
            row = conn.execute(
                "SELECT status FROM payment_intents WHERE public_id=?",
                (self.public_id,),
            ).fetchone()
        self.assertEqual(row["status"], "double_payment_requires_check")

    def test_paid_channel_persisted_in_intent(self):
        """paid_channel is stored on the parent intent record."""
        self.st.payment_intent_mark_paid_via_option(
            self.public_id,
            option_id=self.acq_opt["id"], channel="acquiring",
            tx_uid="tx-acq-001", amount_minor=22900, currency="BYN",
            paid_at="2026-07-14T10:00:00",
        )
        with self.st._connect() as conn:
            row = conn.execute(
                "SELECT paid_channel FROM payment_intents WHERE public_id=?",
                (self.public_id,),
            ).fetchone()
        self.assertEqual(row["paid_channel"], "acquiring")

    def test_paid_option_id_persisted_in_intent(self):
        """paid_option_id is stored on the parent intent record."""
        self.st.payment_intent_mark_paid_via_option(
            self.public_id,
            option_id=self.erip_opt["id"], channel="erip",
            tx_uid="tx-erip-001", amount_minor=22900, currency="BYN",
            paid_at="2026-07-14T10:00:00",
        )
        with self.st._connect() as conn:
            row = conn.execute(
                "SELECT paid_option_id FROM payment_intents WHERE public_id=?",
                (self.public_id,),
            ).fetchone()
        self.assertEqual(row["paid_option_id"], self.erip_opt["id"])

    def test_wrong_state_returns_error(self):
        """payment_intent_mark_paid_via_option on draft intent returns wrong_state."""
        result = self.st.payment_intent_mark_paid_via_option(
            "ycpi_no_such",
            option_id=self.erip_opt["id"], channel="erip",
            tx_uid="tx-x", amount_minor=22900, currency="BYN",
            paid_at="2026-07-14T10:00:00",
        )
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "intent_not_found")


# ─── 5. legacy intent_mark_paid is unaffected ─────────────────────────────────

class TestLegacyMarkPaidUnaffected(unittest.TestCase):

    def test_legacy_mark_paid_still_works(self):
        """Legacy payment_intent_mark_paid (no options) is unaffected by v7.0.92.2."""
        st = _tmp_storage()
        pi = _make_intent(st)  # default status='bepaid_created'; auto-generates public_id
        result = st.payment_intent_mark_paid(
            pi["public_id"],
            tx_uid="tx-legacy-001",
            amount_minor=22900,
            currency="BYN",
            paid_at="2026-07-14T10:00:00",
        )
        self.assertTrue(result.get("ok"))
        self.assertTrue(result.get("marked_paid"))
        pi_after = result["intent"]
        self.assertEqual(pi_after["status"], "paid")
        # paid_channel is NULL for legacy intents
        self.assertIsNone(pi_after.get("paid_channel"))


# ─── 6. bepaid_client: acquiring checkout (v7.0.92.3 — stub replaced) ────────

class TestBePaidClientAcquiringStub(unittest.TestCase):

    def test_create_acquiring_checkout_invalid_amount_raises(self):
        """v7.0.92.3: create_acquiring_checkout raises ValueError for invalid amount."""
        from bepaid_client import BePaidClient
        client = BePaidClient(shop_id="dummy", secret_key="dummy")
        with self.assertRaises(ValueError):
            client.create_acquiring_checkout(
                amount_minor=0, currency="BYN",
                description="T", tracking_id="t",
                notification_url="https://example.com/hook",
                return_url="https://example.com/return",
            )

    def test_acquiring_checkout_uses_real_endpoint(self):
        """v7.0.92.3: create_acquiring_checkout uses confirmed BEPAID_CHECKOUT_ENDPOINT."""
        from bepaid_client import BePaidClient, BEPAID_CHECKOUT_ENDPOINT
        from unittest.mock import patch, MagicMock
        client = BePaidClient(shop_id="s", secret_key="k")
        fake_resp = MagicMock()
        fake_resp.status_code = 200
        fake_resp.json.return_value = {
            "checkout": {"token": "tok1", "redirect_url": "https://pay.bepaid.by/tok1"}
        }
        with patch("requests.post", return_value=fake_resp) as mock_post:
            result = client.create_acquiring_checkout(
                amount_minor=22900, currency="BYN",
                description="Тест", tracking_id="ycpi_202607_1_acq",
                notification_url="https://example.com/hook",
                return_url="https://example.com/return",
            )
        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args[0][0], BEPAID_CHECKOUT_ENDPOINT)
        self.assertTrue(result.ok)


# ─── 7. web_app_server: moyklass_payment_types dual-channel ───────────────────

class TestMoyklassPaymentTypesDualChannel(unittest.TestCase):

    def _make_server(self, erip_id: int = 0, acq_id: int = 0):
        from unittest.mock import MagicMock, patch
        import importlib
        wasm = importlib.import_module("web_app_server")

        cfg = MagicMock()
        cfg.moyklass_erip_payment_type_id = erip_id
        cfg.moyklass_acquiring_payment_type_id = acq_id

        mk_client = MagicMock()
        mk_client.is_configured = True

        server = MagicMock()
        server.settings = cfg
        server.moyklass = mk_client
        server._require_moyklass_post_access = MagicMock(return_value=None)
        server._role_for_user = MagicMock(return_value="owner")

        return server, wasm

    def _mk_result(self, items: list):
        from unittest.mock import MagicMock
        r = MagicMock()
        r.ok = True
        r.data = items
        r.status = 200
        return r

    def test_response_contains_erip_block(self):
        """moyklass_payment_types response includes 'erip' key (v7.0.92.2)."""
        server, wasm = self._make_server(erip_id=55948, acq_id=55949)
        items = [
            {"id": 55948, "name": "bePaid ЕРИП", "active": True},
            {"id": 55949, "name": "bePaid Эквайринг", "active": True},
        ]
        server.moyklass.get_payment_types.return_value = self._mk_result(items)
        result = wasm.MiniAppContext.moyklass_payment_types(
            server, auth={"ok": True, "user_id": 1}
        )
        self.assertTrue(result.get("ok"))
        self.assertIn("erip", result)
        self.assertIn("acquiring", result)

    def test_erip_readiness_valid_when_configured(self):
        """erip.payment_type.valid is True when configured ID matches active type."""
        server, wasm = self._make_server(erip_id=55948, acq_id=0)
        items = [{"id": 55948, "name": "bePaid ЕРИП", "active": True}]
        server.moyklass.get_payment_types.return_value = self._mk_result(items)
        result = wasm.MiniAppContext.moyklass_payment_types(
            server, auth={"ok": True, "user_id": 1}
        )
        self.assertTrue(result["erip"]["payment_type"]["valid"])

    def test_acquiring_readiness_not_configured_when_id_zero(self):
        """acquiring.payment_type.configured is False when acq_id=0."""
        server, wasm = self._make_server(erip_id=55948, acq_id=0)
        items = [{"id": 55948, "name": "bePaid ЕРИП", "active": True}]
        server.moyklass.get_payment_types.return_value = self._mk_result(items)
        result = wasm.MiniAppContext.moyklass_payment_types(
            server, auth={"ok": True, "user_id": 1}
        )
        self.assertFalse(result["acquiring"]["payment_type"]["configured"])

    def test_acquiring_candidates_detected(self):
        """Acquiring candidates are detected by _is_acquiring_candidate."""
        server, wasm = self._make_server(erip_id=55948, acq_id=55949)
        items = [
            {"id": 55948, "name": "bePaid ЕРИП", "active": True},
            {"id": 55949, "name": "Эквайринг", "active": True},
        ]
        server.moyklass.get_payment_types.return_value = self._mk_result(items)
        result = wasm.MiniAppContext.moyklass_payment_types(
            server, auth={"ok": True, "user_id": 1}
        )
        acq_cands = result["diagnostics"]["acquiring_candidates"]
        self.assertTrue(any(c["id"] == 55949 for c in acq_cands))

    def test_legacy_configured_payment_type_backward_compat(self):
        """configured_payment_type (legacy field) still references ERIP type."""
        server, wasm = self._make_server(erip_id=55948, acq_id=0)
        items = [{"id": 55948, "name": "bePaid ЕРИП", "active": True}]
        server.moyklass.get_payment_types.return_value = self._mk_result(items)
        result = wasm.MiniAppContext.moyklass_payment_types(
            server, auth={"ok": True, "user_id": 1}
        )
        self.assertEqual(result.get("configured_payment_type_id"), 55948)


# ─── 8. web_app_server: posting uses paid_channel to select payment type ──────

class TestPaymentIntentPostToMoyklassChannel(unittest.TestCase):
    """Test that posting selects the correct payment_type_id based on paid_channel.

    Strategy: reach the payment_type_id <= 0 guard by setting the respective
    payment type to 0 and verifying the error message names the right env var.
    This avoids mocking the full MoyKlass invoice flow.
    """

    def _paid_intent(self, paid_channel):
        return {
            "status": "paid",
            "mk_user_id": 1001,
            "mk_invoice_id": "INV-001",
            "mk_user_subscription_id": "SUB-001",
            "paid_amount_minor": 22900,
            "paid_channel": paid_channel,
            "paid_transaction_uid": "tx-001",
            "mk_posting_status": None,
        }

    def _server(self, erip_id: int, acq_id: int, paid_channel):
        import importlib
        wasm = importlib.import_module("web_app_server")
        cfg = MagicMock()
        cfg.moyklass_erip_payment_type_id = erip_id
        cfg.moyklass_acquiring_payment_type_id = acq_id
        server = MagicMock()
        server.settings = cfg
        server.moyklass.is_configured = True
        server._require_moyklass_post_access.return_value = None
        server.storage.get_payment_intent.return_value = self._paid_intent(paid_channel)
        return server, wasm

    def test_erip_channel_blocked_when_erip_type_not_set(self):
        """When paid_channel='erip' and erip_id=0, error names MOYKLASS_ERIP_PAYMENT_TYPE_ID."""
        server, wasm = self._server(erip_id=0, acq_id=55949, paid_channel="erip")
        result = wasm.MiniAppContext.payment_intent_post_to_moyklass(
            server,
            auth={"ok": True, "user_id": 1},
            public_id="ycpi_test_001",
            body={"confirm": True, "snapshot_fingerprint": "fp-test"},
        )
        self.assertFalse(result.get("ok"))
        self.assertIn("MOYKLASS_ERIP_PAYMENT_TYPE_ID", result.get("error", ""))

    def test_acquiring_channel_blocked_when_not_configured(self):
        """When paid_channel='acquiring' and acq_id=0, error names MOYKLASS_ACQUIRING_PAYMENT_TYPE_ID."""
        server, wasm = self._server(erip_id=55948, acq_id=0, paid_channel="acquiring")
        result = wasm.MiniAppContext.payment_intent_post_to_moyklass(
            server,
            auth={"ok": True, "user_id": 1},
            public_id="ycpi_test_001",
            body={"confirm": True, "snapshot_fingerprint": "fp-test"},
        )
        self.assertFalse(result.get("ok"))
        self.assertIn("MOYKLASS_ACQUIRING_PAYMENT_TYPE_ID", result.get("error", ""))

    def test_no_paid_channel_falls_back_to_erip_error_message(self):
        """When paid_channel=None and erip_id=0, error names MOYKLASS_ERIP_PAYMENT_TYPE_ID (fallback)."""
        server, wasm = self._server(erip_id=0, acq_id=55949, paid_channel=None)
        result = wasm.MiniAppContext.payment_intent_post_to_moyklass(
            server,
            auth={"ok": True, "user_id": 1},
            public_id="ycpi_test_001",
            body={"confirm": True, "snapshot_fingerprint": "fp-test"},
        )
        self.assertFalse(result.get("ok"))
        self.assertIn("MOYKLASS_ERIP_PAYMENT_TYPE_ID", result.get("error", ""))

    def test_both_configured_erip_proceeds_past_type_guard(self):
        """When paid_channel='erip' and erip_id>0, does NOT return ERIP payment type error."""
        server, wasm = self._server(erip_id=55948, acq_id=55949, paid_channel="erip")
        result = wasm.MiniAppContext.payment_intent_post_to_moyklass(
            server,
            auth={"ok": True, "user_id": 1},
            public_id="ycpi_test_001",
            body={"confirm": True, "snapshot_fingerprint": "fp-test"},
        )
        self.assertNotEqual(result.get("error", ""), "Не настроен тип оплаты МойКласс (MOYKLASS_ERIP_PAYMENT_TYPE_ID)")


# ─── 9. web_app_server: _is_acquiring_candidate ───────────────────────────────

class TestIsAcquiringCandidate(unittest.TestCase):

    def setUp(self):
        import importlib
        self.wasm = importlib.import_module("web_app_server")

    def test_эквайринг_detected(self):
        self.assertTrue(self.wasm._is_acquiring_candidate("Эквайринг"))

    def test_acquiring_detected(self):
        self.assertTrue(self.wasm._is_acquiring_candidate("Acquiring"))

    def test_банковская_карта_detected(self):
        self.assertTrue(self.wasm._is_acquiring_candidate("Банковская карта"))

    def test_bepaid_card_detected(self):
        self.assertTrue(self.wasm._is_acquiring_candidate("bePaid Card"))

    def test_erip_not_acquiring_candidate(self):
        self.assertFalse(self.wasm._is_acquiring_candidate("bePaid ЕРИП"))

    def test_наличные_not_acquiring_candidate(self):
        self.assertFalse(self.wasm._is_acquiring_candidate("Наличные"))


if __name__ == "__main__":
    unittest.main()

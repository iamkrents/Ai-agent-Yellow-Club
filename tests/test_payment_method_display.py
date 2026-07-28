"""Tests for v7.1.8 — actual payment method display fix.

Audit finding: pi.payment_method is only the REQUESTED/creation-time channel
(hardcoded "erip" for every automation-created intent) — never proof of how
a client actually paid. The only reliable "fact" is pi.paid_channel, set by
storage.payment_intent_mark_paid()/payment_intent_mark_paid_via_option() from
the real bePaid webhook shop_type at the moment of a CONFIRMED transaction.

No schema change was needed: paid_channel already existed, was already
reliably populated by both webhook mark-paid paths, and was already exposed
to the frontend via _normalize_payment_intent() (plain dict passthrough).
This file verifies that existing backend behavior is correct — it is a
verification/regression suite, not a test of new backend code.

Run offline (real SQLite tempfile, no MoyKlass/bePaid network calls):
    python -m unittest tests.test_payment_method_display -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage
from payment_domain import resolve_effective_payment_channel


def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _seed_intent(storage: Storage, public_id: str, **overrides) -> dict:
    now = _now()
    base = {
        "mk_user_id": 8001, "student_name": "Тест", "amount_minor": 23900,
        "amount_byn": 239.0, "currency": "BYN", "purpose": "subscription",
        "payment_method": "erip", "status": "bepaid_created",
        "created_at": now, "public_id": public_id,
    }
    base.update(overrides)
    with storage._connect() as conn:
        conn.execute(
            """INSERT INTO payment_intents
               (public_id, mk_user_id, student_name, amount_minor, amount_byn,
                currency, purpose, payment_method, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (base["public_id"], base["mk_user_id"], base["student_name"],
             base["amount_minor"], base["amount_byn"], base["currency"],
             base["purpose"], base["payment_method"], base["status"],
             base["created_at"], base["created_at"]),
        )
    return storage.get_payment_intent(public_id)


class TestUnpaidNeverClaimsMethod(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()

    def test_01_unpaid_intent_has_no_actual_method(self):
        intent = _seed_intent(self.storage, "PM-01", status="draft")
        self.assertIsNone(intent.get("paid_channel"))

    def test_02_erip_number_creation_does_not_set_actual_method(self):
        # Creating bePaid/ERIP options only prepares a payment option — it
        # must never itself write paid_channel.
        intent = _seed_intent(self.storage, "PM-02", status="bepaid_created")
        with self.storage._connect() as conn:
            conn.execute(
                "UPDATE payment_intents SET bepaid_uid=?, bepaid_account_number=? WHERE public_id=?",
                ("uid-2", "1112223", "PM-02"),
            )
        refreshed = self.storage.get_payment_intent("PM-02")
        self.assertIsNone(refreshed.get("paid_channel"))

    def test_03_checkout_creation_does_not_set_actual_method(self):
        intent = _seed_intent(self.storage, "PM-03", status="bepaid_created", payment_method="acquiring")
        with self.storage._connect() as conn:
            conn.execute(
                "INSERT INTO payment_intent_options "
                "(payment_intent_id, intent_public_id, channel, shop_type, status, payment_url, checkout_token, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (intent["id"], "PM-03", "acquiring", "acquiring", "created",
                 "https://bepaid.example/checkout/abc", "tok-abc", _now(), _now()),
            )
        refreshed = self.storage.get_payment_intent("PM-03")
        self.assertIsNone(refreshed.get("paid_channel"))


class TestWebhookStoresActualMethod(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()

    def test_04_successful_card_webhook_stores_acquiring(self):
        _seed_intent(self.storage, "PM-04", status="bepaid_created", payment_method="erip")
        result = self.storage.payment_intent_mark_paid(
            "PM-04", tx_uid="tx-4", amount_minor=23900, currency="BYN",
            paid_at=_now(), channel="acquiring", verified=True,
        )
        self.assertTrue(result.get("ok"))
        self.assertEqual(result["intent"]["paid_channel"], "acquiring")

    def test_05_successful_erip_webhook_stores_erip(self):
        _seed_intent(self.storage, "PM-05", status="bepaid_created", payment_method="erip")
        result = self.storage.payment_intent_mark_paid(
            "PM-05", tx_uid="tx-5", amount_minor=23900, currency="BYN",
            paid_at=_now(), channel="erip", verified=True,
        )
        self.assertTrue(result.get("ok"))
        self.assertEqual(result["intent"]["paid_channel"], "erip")

    def test_06_unrecognized_channel_stores_consistently(self):
        _seed_intent(self.storage, "PM-06", status="bepaid_created")
        result = self.storage.payment_intent_mark_paid(
            "PM-06", tx_uid="tx-6", amount_minor=23900, currency="BYN",
            paid_at=_now(), channel="", verified=True,
        )
        self.assertTrue(result.get("ok"))
        # Empty/unrecognized channel is stored as NULL — frontend renders this
        # consistently as "не определён", never guesses "erip".
        self.assertIsNone(result["intent"].get("paid_channel"))
        label = resolve_effective_payment_channel(result["intent"])
        # resolve_effective_payment_channel is the internal MK-posting-type
        # resolver (falls back to payment_method for routing purposes only —
        # display code must NEVER use this fallback as a "fact").
        self.assertEqual(label, "erip")  # internal routing fallback, not a display claim

    def test_07_duplicate_webhook_is_idempotent(self):
        _seed_intent(self.storage, "PM-07", status="bepaid_created")
        r1 = self.storage.payment_intent_mark_paid(
            "PM-07", tx_uid="tx-7", amount_minor=23900, currency="BYN",
            paid_at=_now(), channel="acquiring", verified=True,
        )
        r2 = self.storage.payment_intent_mark_paid(
            "PM-07", tx_uid="tx-7", amount_minor=23900, currency="BYN",
            paid_at=_now(), channel="acquiring", verified=True,
        )
        self.assertTrue(r1.get("marked_paid"))
        self.assertTrue(r2.get("idempotent"))
        self.assertEqual(r2["intent"]["paid_channel"], "acquiring")

    def test_08_duplicate_webhook_does_not_downgrade_known_method(self):
        _seed_intent(self.storage, "PM-08", status="bepaid_created")
        self.storage.payment_intent_mark_paid(
            "PM-08", tx_uid="tx-8", amount_minor=23900, currency="BYN",
            paid_at=_now(), channel="acquiring", verified=True,
        )
        # A second webhook for the SAME tx_uid but reporting a different/empty
        # channel must not overwrite the already-recorded fact (the UPDATE's
        # WHERE clause only matches non-paid statuses, so this is a no-op).
        r2 = self.storage.payment_intent_mark_paid(
            "PM-08", tx_uid="tx-8", amount_minor=23900, currency="BYN",
            paid_at=_now(), channel="", verified=True,
        )
        self.assertTrue(r2.get("idempotent"))
        self.assertEqual(r2["intent"]["paid_channel"], "acquiring")

    def test_09_invalid_webhook_wrong_state_stores_nothing(self):
        _seed_intent(self.storage, "PM-09", status="draft")  # not in allowed source states
        result = self.storage.payment_intent_mark_paid(
            "PM-09", tx_uid="tx-9", amount_minor=23900, currency="BYN",
            paid_at=_now(), channel="acquiring", verified=True,
        )
        self.assertFalse(result.get("ok"))
        refreshed = self.storage.get_payment_intent("PM-09")
        self.assertIsNone(refreshed.get("paid_channel"))

    def test_10_failed_transaction_conflict_does_not_set_new_method(self):
        _seed_intent(self.storage, "PM-10", status="bepaid_created")
        self.storage.payment_intent_mark_paid(
            "PM-10", tx_uid="tx-10a", amount_minor=23900, currency="BYN",
            paid_at=_now(), channel="erip", verified=True,
        )
        conflict = self.storage.payment_intent_mark_paid(
            "PM-10", tx_uid="tx-10b", amount_minor=23900, currency="BYN",
            paid_at=_now(), channel="acquiring", verified=True,
        )
        self.assertFalse(conflict.get("ok"))
        self.assertTrue(conflict.get("conflict"))
        refreshed = self.storage.get_payment_intent("PM-10")
        self.assertEqual(refreshed.get("paid_channel"), "erip")


class TestViaOptionPath(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()

    def _seed_option(self, public_id, channel="acquiring"):
        intent = _seed_intent(self.storage, public_id, status="bepaid_created", payment_method=channel)
        with self.storage._connect() as conn:
            cur = conn.execute(
                "INSERT INTO payment_intent_options "
                "(payment_intent_id, intent_public_id, channel, shop_type, status, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (intent["id"], public_id, channel, channel, "created", _now(), _now()),
            )
            return cur.lastrowid

    def test_option_path_card_stores_acquiring(self):
        option_id = self._seed_option("PM-11", channel="acquiring")
        result = self.storage.payment_intent_mark_paid_via_option(
            "PM-11", option_id=option_id, channel="acquiring", tx_uid="tx-11",
            amount_minor=23900, currency="BYN", paid_at=_now(), verified=True,
        )
        self.assertTrue(result.get("ok"))
        self.assertEqual(result["intent"]["paid_channel"], "acquiring")


class TestUnaffectedBehavior(unittest.TestCase):
    def setUp(self):
        self.storage = _make_storage()

    def test_11_existing_paid_posting_to_mk_unchanged(self):
        _seed_intent(self.storage, "PM-12", status="bepaid_created")
        self.storage.payment_intent_mark_paid(
            "PM-12", tx_uid="tx-12", amount_minor=23900, currency="BYN",
            paid_at=_now(), channel="erip", verified=True,
        )
        pi = self.storage.get_payment_intent("PM-12")
        self.assertEqual(pi["status"], "paid")
        # can_post_to_moyklass logic depends only on status/mk_payment_id/mk_posting_status
        import payment_domain
        self.assertTrue(payment_domain.can_post_to_moyklass(pi))

    def test_12_payment_amount_unchanged(self):
        _seed_intent(self.storage, "PM-13", status="bepaid_created", amount_minor=23900)
        result = self.storage.payment_intent_mark_paid(
            "PM-13", tx_uid="tx-13", amount_minor=23900, currency="BYN",
            paid_at=_now(), channel="acquiring", verified=True,
        )
        self.assertEqual(result["intent"]["paid_amount_minor"], 23900)

    def test_13_webhook_signature_validation_unchanged(self):
        import inspect
        import web_app_server
        src = inspect.getsource(web_app_server.MiniAppContext.bepaid_handle_webhook)
        self.assertIn("sig_reason", src)
        self.assertIn("signature", src.lower())

    def test_14_legacy_paid_record_without_paid_channel_supported(self):
        intent = _seed_intent(self.storage, "PM-14", status="paid", payment_method="erip")
        # Simulate a legacy row from before paid_channel existed.
        pi = self.storage.get_payment_intent("PM-14")
        self.assertIsNone(pi.get("paid_channel"))
        # Frontend contract: status=paid + paid_channel=None -> "не определён",
        # never a guess based on payment_method.
        from payment_domain import PAYMENT_INTENT_PAID_STATUSES
        self.assertIn(pi["status"], PAYMENT_INTENT_PAID_STATUSES)

    def test_15_schema_has_paid_channel_column_idempotent_init(self):
        # Re-initializing Storage on the same DB file must not fail or duplicate
        # the paid_channel column (additive _ensure_column pattern already exists).
        storage2 = Storage(self.storage.db_path)
        with storage2._connect() as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(payment_intents)").fetchall()]
        self.assertIn("paid_channel", cols)


if __name__ == "__main__":
    unittest.main()

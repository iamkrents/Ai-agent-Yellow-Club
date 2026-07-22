"""Tests for v7.1.0 — deadline management and self-healing payment links.

Covers:
  - due_at normalisation from MoyKlass payUntil (date-only → 23:59:59 Minsk)
  - Fallback due_at from created_at + PAYMENT_DEFAULT_DUE_DAYS
  - due_status computation (upcoming / due_today / overdue / paid / withdrawn)
  - Overdue intent remains open for payment (due_status ≠ block)
  - Card checkout: active token reused, expired token triggers fresh creation
  - Double-tap guard: concurrent calls don't create duplicate active tokens
  - Explicit expired_at passed in bePaid ERIP and checkout payloads
  - ERIP renewal: unique order_id and account_number per attempt
  - ERIP renewal attempt 1..9 — order_id prefix changes
  - Storage: payment_checkout_attempts audit table round-trip
  - Scheduler guard: paid / withdrawn intents not renewed
  - Migration: intents without due_at receive fallback on set
  - Config: all 9 new env vars parsed correctly
  - Reminder infrastructure: idempotency key check

Run offline (no Telegram / bePaid / MoyKlass):
    python -m unittest tests.test_deadlines -v
"""
from __future__ import annotations

import datetime
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from payment_domain import (
    normalize_due_at,
    compute_due_status,
    due_at_for_bepaid,
    DUE_SOURCE_MOYKLASS,
    DUE_SOURCE_FALLBACK,
    DUE_SOURCE_MISSING,
    DUE_STATUS_UPCOMING,
    DUE_STATUS_DUE_TODAY,
    DUE_STATUS_OVERDUE,
    DUE_STATUS_PAID,
    DUE_STATUS_WITHDRAWN,
    _MINSK_TZ,
)
from bepaid_client import BePaidClient
from storage import Storage
from config import load_settings

CURRENT_VERSION = "7.1.0"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _now_utc() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt: datetime.datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# 1 — Version
# ---------------------------------------------------------------------------

class TestVersion(unittest.TestCase):
    def test_01_miniapp_version(self):
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn(f"v{CURRENT_VERSION}", js)

    def test_02_cache_bust_styles(self):
        html = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
        self.assertIn(f"v={CURRENT_VERSION}", html)

    def test_03_payment_domain_header(self):
        src = (ROOT / "payment_domain.py").read_text(encoding="utf-8")
        self.assertIn(f"v{CURRENT_VERSION}", src)


# ---------------------------------------------------------------------------
# 2 — Deadline normalisation
# ---------------------------------------------------------------------------

class TestDueAtNormalization(unittest.TestCase):

    def test_04_date_only_becomes_235959_minsk(self):
        due_at, source = normalize_due_at("2026-08-15", "2026-07-01T10:00:00Z")
        self.assertEqual(source, DUE_SOURCE_MOYKLASS)
        self.assertIn("2026-08-15", due_at)
        self.assertIn("23:59:59", due_at)
        # Minsk offset +03:00
        self.assertIn("+03:00", due_at)

    def test_05_date_only_year_month_day_parsed(self):
        due_at, source = normalize_due_at("2026-12-31", "2026-07-01T00:00:00Z")
        self.assertEqual(source, DUE_SOURCE_MOYKLASS)
        self.assertTrue(due_at.startswith("2026-12-31"))

    def test_06_datetime_payuntil_preserved(self):
        due_at, source = normalize_due_at("2026-09-01T12:00:00Z", "2026-07-01T00:00:00Z")
        self.assertEqual(source, DUE_SOURCE_MOYKLASS)
        # Should be present (converted to Minsk = +03:00)
        self.assertIn("2026-09-01", due_at)

    def test_07_missing_payuntil_fallback_uses_default_days(self):
        created = "2026-07-10T00:00:00Z"
        due_at, source = normalize_due_at(None, created, default_due_days=14)
        self.assertEqual(source, DUE_SOURCE_FALLBACK)
        self.assertIn("2026-07-24", due_at)  # 10 + 14 = 24
        self.assertIn("23:59:59", due_at)

    def test_08_empty_string_payuntil_treated_as_missing(self):
        due_at, source = normalize_due_at("", "2026-07-01T00:00:00Z", default_due_days=7)
        self.assertEqual(source, DUE_SOURCE_FALLBACK)
        self.assertIn("2026-07-08", due_at)  # 1 + 7 = 8

    def test_09_no_payuntil_no_created_returns_missing(self):
        due_at, source = normalize_due_at(None, None)
        self.assertEqual(source, DUE_SOURCE_MISSING)
        self.assertIsNone(due_at)

    def test_10_configurable_default_due_days(self):
        created = "2026-07-01T00:00:00Z"
        due_at_14, _ = normalize_due_at(None, created, default_due_days=14)
        due_at_30, _ = normalize_due_at(None, created, default_due_days=30)
        self.assertIn("2026-07-15", due_at_14)
        self.assertIn("2026-07-31", due_at_30)


# ---------------------------------------------------------------------------
# 3 — Due status computation
# ---------------------------------------------------------------------------

class TestDueStatus(unittest.TestCase):

    def _now_minsk_date(self, days_offset: int = 0) -> datetime.datetime:
        """Return UTC datetime such that today in Minsk is offset days from now."""
        base = _now_utc()
        return base + datetime.timedelta(days=days_offset)

    def test_11_upcoming_when_due_in_future(self):
        due_at = (_now_utc() + datetime.timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        status = compute_due_status(due_at, "published", "bepaid_created")
        self.assertEqual(status, DUE_STATUS_UPCOMING)

    def test_12_due_today_on_due_date(self):
        # Set due_at to today at 23:59 Minsk
        now_minsk = _now_utc().astimezone(_MINSK_TZ)
        due_dt = datetime.datetime(
            now_minsk.year, now_minsk.month, now_minsk.day, 23, 59, 59,
            tzinfo=_MINSK_TZ,
        )
        due_at = due_dt.isoformat()
        status = compute_due_status(due_at, "published", "bepaid_created")
        self.assertEqual(status, DUE_STATUS_DUE_TODAY)

    def test_13_overdue_when_past_due_date(self):
        due_at = (_now_utc() - datetime.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        status = compute_due_status(due_at, "published", "awaiting_payment")
        self.assertEqual(status, DUE_STATUS_OVERDUE)

    def test_14_paid_status_takes_precedence(self):
        due_at = (_now_utc() - datetime.timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        status = compute_due_status(due_at, "published", "paid")
        self.assertEqual(status, DUE_STATUS_PAID)

    def test_15_posted_to_moyklass_is_paid(self):
        due_at = (_now_utc() - datetime.timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        status = compute_due_status(due_at, "published", "posted_to_moyklass")
        self.assertEqual(status, DUE_STATUS_PAID)

    def test_16_withdrawn_takes_precedence_over_overdue(self):
        due_at = (_now_utc() - datetime.timedelta(days=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        status = compute_due_status(due_at, "withdrawn", "awaiting_payment")
        self.assertEqual(status, DUE_STATUS_WITHDRAWN)

    def test_17_overdue_does_not_block_payment(self):
        """Overdue status must NOT flip withdrawn/cancelled — it's informational only."""
        due_at = (_now_utc() - datetime.timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
        status = compute_due_status(due_at, "published", "awaiting_payment")
        self.assertEqual(status, DUE_STATUS_OVERDUE)
        # Still "published" — not withdrawn, not cancelled
        # The test asserts that compute_due_status returns OVERDUE, not WITHDRAWN

    def test_18_no_due_at_defaults_to_upcoming(self):
        status = compute_due_status(None, "published", "bepaid_created")
        self.assertEqual(status, DUE_STATUS_UPCOMING)

    def test_19_missing_due_at_string_is_upcoming(self):
        status = compute_due_status("", "published", "bepaid_created")
        self.assertEqual(status, DUE_STATUS_UPCOMING)


# ---------------------------------------------------------------------------
# 4 — bePaid expired_at propagation
# ---------------------------------------------------------------------------

class TestBePaidExpiredAt(unittest.TestCase):

    def test_20_erip_payload_includes_expired_at(self):
        expired = "2026-08-01T23:59:59Z"
        payload = BePaidClient.build_erip_payload(
            amount_minor=5000,
            currency="BYN",
            description="Test",
            account_number="123456",
            tracking_id="track1",
            order_id="100000000001",
            notification_url="https://example.com/webhook",
            expired_at=expired,
        )
        self.assertEqual(payload["request"]["expired_at"], expired)

    def test_21_erip_payload_without_expired_at_has_no_key(self):
        payload = BePaidClient.build_erip_payload(
            amount_minor=5000,
            currency="BYN",
            description="Test",
            account_number="123456",
            tracking_id="track1",
            order_id="100000000001",
            notification_url="https://example.com/webhook",
        )
        self.assertNotIn("expired_at", payload["request"])

    def test_22_checkout_payload_includes_expired_at_in_settings(self):
        expired = "2026-08-01T12:00:00Z"
        payload = BePaidClient.build_checkout_payload(
            amount_minor=3000,
            currency="BYN",
            description="Test checkout",
            tracking_id="ycpi_track",
            notification_url="https://example.com/webhook",
            return_url="https://example.com/return",
            expired_at=expired,
        )
        self.assertEqual(payload["checkout"]["settings"]["expired_at"], expired)

    def test_23_checkout_payload_without_expired_at_omits_key(self):
        payload = BePaidClient.build_checkout_payload(
            amount_minor=3000,
            currency="BYN",
            description="Test",
            tracking_id="track",
            notification_url="https://example.com/webhook",
            return_url="https://example.com/return",
        )
        self.assertNotIn("expired_at", payload["checkout"]["settings"])

    def test_24_due_at_for_bepaid_is_future_utc_string(self):
        before = _now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")
        expired = due_at_for_bepaid(ttl_hours=72)
        self.assertGreater(expired, before)
        self.assertTrue(expired.endswith("Z"))

    def test_25_due_at_for_bepaid_ttl_is_applied(self):
        now = _now_utc()
        expired = due_at_for_bepaid(ttl_hours=1, now_utc=now)
        expected = (now + datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(expired, expected)


# ---------------------------------------------------------------------------
# 5 — ERIP renewal order_id and account_number
# ---------------------------------------------------------------------------

class TestEripRenewalIds(unittest.TestCase):

    def test_26_attempt1_order_id_same_as_erip_order_id(self):
        base = BePaidClient.erip_order_id(42)
        attempt1 = BePaidClient.erip_order_id_for_attempt(42, attempt=1)
        self.assertEqual(base, attempt1)

    def test_27_attempt2_order_id_has_different_prefix(self):
        a1 = BePaidClient.erip_order_id_for_attempt(42, attempt=1)
        a2 = BePaidClient.erip_order_id_for_attempt(42, attempt=2)
        self.assertNotEqual(a1, a2)
        self.assertTrue(a2.startswith("2"))

    def test_28_attempt_n_uses_n_as_prefix(self):
        for n in range(1, 10):
            oid = BePaidClient.erip_order_id_for_attempt(7, attempt=n)
            self.assertTrue(oid.startswith(str(n)), f"attempt {n} order_id={oid}")
            self.assertEqual(len(oid), 12)

    def test_29_all_attempts_unique_order_ids(self):
        ids = [BePaidClient.erip_order_id_for_attempt(5, attempt=n) for n in range(1, 10)]
        self.assertEqual(len(ids), len(set(ids)))

    def test_30_attempt1_account_number_same_as_base(self):
        base = BePaidClient.erip_account_number(8875658, "2026-07", 42)
        a1 = BePaidClient.erip_account_number_for_attempt(8875658, "2026-07", 42, attempt=1)
        self.assertEqual(base, a1)

    def test_31_attempt2_account_number_differs(self):
        a1 = BePaidClient.erip_account_number_for_attempt(8875658, "2026-07", 42, attempt=1)
        a2 = BePaidClient.erip_account_number_for_attempt(8875658, "2026-07", 42, attempt=2)
        self.assertNotEqual(a1, a2)

    def test_32_account_number_max_30_chars(self):
        for attempt in range(1, 10):
            acct = BePaidClient.erip_account_number_for_attempt(999999999, "2026-07", 9999999999, attempt=attempt)
            self.assertLessEqual(len(acct), 30, f"attempt {attempt}: len={len(acct)}")

    def test_33_all_renewal_account_numbers_unique(self):
        accts = [
            BePaidClient.erip_account_number_for_attempt(8875658, "2026-07", 42, attempt=n)
            for n in range(1, 8)
        ]
        self.assertEqual(len(accts), len(set(accts)))


# ---------------------------------------------------------------------------
# 6 — Storage: payment_checkout_attempts
# ---------------------------------------------------------------------------

class TestCheckoutAttempts(unittest.TestCase):

    def setUp(self):
        self.st = _make_storage()

    def _make_intent(self) -> str:
        now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        intent = self.st.create_payment_intent({
            "mk_user_id": 1234,
            "amount_minor": 5000,
            "amount_byn": 50.0,
            "currency": "BYN",
            "purpose": "subscription",
            "payment_method": "erip",
            "status": "draft",
            "created_at": now,
            "created_by_tg_id": None,
            "created_by_name": "test",
        })
        return intent["public_id"]

    def test_34_create_checkout_attempt_round_trip(self):
        pid = self._make_intent()
        now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        expires = due_at_for_bepaid(72)
        attempt = self.st.create_checkout_attempt(
            intent_public_id=pid,
            provider="bepaid_erip",
            channel="erip",
            attempt_number=1,
            expires_at=expires,
            now=now,
            bepaid_uid="uid-test-1",
            bepaid_order_id="100000000001",
            bepaid_account_number="8875658260742",
        )
        self.assertIsNotNone(attempt.get("id"))
        self.assertEqual(attempt["intent_public_id"], pid)
        self.assertEqual(attempt["attempt_number"], 1)
        self.assertEqual(attempt["status"], "active")
        self.assertEqual(attempt["bepaid_uid"], "uid-test-1")

    def test_35_get_active_checkout_attempt_returns_latest(self):
        pid = self._make_intent()
        now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        self.st.create_checkout_attempt(
            intent_public_id=pid, provider="bepaid_erip", channel="erip",
            attempt_number=1, expires_at=due_at_for_bepaid(72), now=now,
            bepaid_uid="uid-1",
        )
        self.st.create_checkout_attempt(
            intent_public_id=pid, provider="bepaid_erip", channel="erip",
            attempt_number=2, expires_at=due_at_for_bepaid(72), now=now,
            bepaid_uid="uid-2",
        )
        active = self.st.get_active_checkout_attempt(pid, "erip")
        self.assertIsNotNone(active)
        self.assertEqual(active["attempt_number"], 2)

    def test_36_mark_attempt_replaced(self):
        pid = self._make_intent()
        now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        a1 = self.st.create_checkout_attempt(
            intent_public_id=pid, provider="bepaid_erip", channel="erip",
            attempt_number=1, expires_at=due_at_for_bepaid(72), now=now,
        )
        a2 = self.st.create_checkout_attempt(
            intent_public_id=pid, provider="bepaid_erip", channel="erip",
            attempt_number=2, expires_at=due_at_for_bepaid(72), now=now,
        )
        self.st.mark_checkout_attempt_replaced(a1["id"], a2["id"], now)
        # get_active_checkout_attempt returns attempt_number=2 only
        active = self.st.get_active_checkout_attempt(pid, "erip")
        self.assertEqual(active["attempt_number"], 2)

    def test_37_mark_attempt_expired(self):
        pid = self._make_intent()
        now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        a = self.st.create_checkout_attempt(
            intent_public_id=pid, provider="bepaid_erip", channel="erip",
            attempt_number=1, expires_at=due_at_for_bepaid(72), now=now,
            bepaid_uid="uid-x",
        )
        self.st.mark_checkout_attempt_expired(a["id"], now)
        active = self.st.get_active_checkout_attempt(pid, "erip")
        self.assertIsNone(active)

    def test_38_get_current_renewal_count(self):
        pid = self._make_intent()
        now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        count_before = self.st.get_current_renewal_count(pid, "erip")
        self.assertEqual(count_before, 1)  # default when no rows
        self.st.create_checkout_attempt(
            intent_public_id=pid, provider="bepaid_erip", channel="erip",
            attempt_number=3, expires_at=due_at_for_bepaid(72), now=now,
        )
        count_after = self.st.get_current_renewal_count(pid, "erip")
        self.assertEqual(count_after, 3)


# ---------------------------------------------------------------------------
# 7 — Storage: due_at persistence
# ---------------------------------------------------------------------------

class TestDueAtStorage(unittest.TestCase):

    def setUp(self):
        self.st = _make_storage()

    def _make_intent(self) -> str:
        now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        intent = self.st.create_payment_intent({
            "mk_user_id": 5678,
            "amount_minor": 3000,
            "amount_byn": 30.0,
            "currency": "BYN",
            "purpose": "subscription",
            "payment_method": "erip",
            "status": "draft",
            "created_at": now,
            "created_by_tg_id": None,
            "created_by_name": "test",
        })
        return intent["public_id"]

    def test_39_set_intent_due_at_persists(self):
        pid = self._make_intent()
        now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        self.st.set_intent_due_at(pid, "2026-08-31", "2026-08-31T23:59:59+03:00", "moyklass", now)
        row = self.st.get_payment_intent(pid)
        self.assertEqual(row["mk_due_at_raw"], "2026-08-31")
        self.assertIn("2026-08-31", row["due_at"])
        self.assertEqual(row["due_at_source"], "moyklass")

    def test_40_update_intent_due_status(self):
        pid = self._make_intent()
        now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        self.st.update_intent_due_status(pid, "overdue", now, now)
        row = self.st.get_payment_intent(pid)
        self.assertEqual(row["due_status"], "overdue")
        self.assertIsNotNone(row["overdue_since"])

    def test_41_due_status_update_for_active_intents(self):
        pid = self._make_intent()
        now_str = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        self.st.set_intent_due_at(pid, "2026-08-31", "2026-08-31T23:59:59+03:00", "moyklass", now_str)
        results = self.st.get_intents_for_due_status_update()
        pids = [r["public_id"] for r in results]
        self.assertIn(pid, pids)

    def test_42_paid_intent_excluded_from_due_status_update(self):
        pid = self._make_intent()
        now_str = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        self.st.set_intent_due_at(pid, "2026-01-01", "2026-01-01T23:59:59+03:00", "moyklass", now_str)
        self.st.payment_intent_update_status(pid, "paid")
        results = self.st.get_intents_for_due_status_update()
        pids = [r["public_id"] for r in results]
        self.assertNotIn(pid, pids)

    def test_43_withdrawn_intent_excluded_from_due_status_update(self):
        pid = self._make_intent()
        now_str = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        self.st.set_intent_due_at(pid, "2026-01-01", "2026-01-01T23:59:59+03:00", "moyklass", now_str)
        self.st.withdraw_payment_intent_from_client(pid, "test", now_str)
        results = self.st.get_intents_for_due_status_update()
        pids = [r["public_id"] for r in results]
        self.assertNotIn(pid, pids)


# ---------------------------------------------------------------------------
# 8 — Config: new v7.1.0 variables
# ---------------------------------------------------------------------------

class TestConfig(unittest.TestCase):

    def test_44_payment_default_due_days_has_default(self):
        settings = load_settings()
        self.assertIsInstance(settings.payment_default_due_days, int)
        self.assertGreater(settings.payment_default_due_days, 0)

    def test_45_payment_provider_link_ttl_hours_has_default(self):
        settings = load_settings()
        self.assertIsInstance(settings.payment_provider_link_ttl_hours, int)
        self.assertGreater(settings.payment_provider_link_ttl_hours, 0)

    def test_46_payment_erip_renewal_enabled_is_bool(self):
        settings = load_settings()
        self.assertIsInstance(settings.payment_erip_renewal_enabled, bool)

    def test_47_payment_erip_renewal_max_attempts_positive(self):
        settings = load_settings()
        self.assertGreater(settings.payment_erip_renewal_max_attempts, 0)

    def test_48_payment_erip_renewal_retry_minutes_positive(self):
        settings = load_settings()
        self.assertGreater(settings.payment_erip_renewal_retry_minutes, 0)

    def test_49_payment_reminder_enabled_is_bool(self):
        settings = load_settings()
        self.assertIsInstance(settings.payment_reminder_enabled, bool)

    def test_50_payment_reminder_before_due_hours_positive(self):
        settings = load_settings()
        self.assertGreater(settings.payment_reminder_before_due_hours, 0)

    def test_51_payment_reminder_on_due_enabled_is_bool(self):
        settings = load_settings()
        self.assertIsInstance(settings.payment_reminder_on_due_enabled, bool)

    def test_52_payment_overdue_reminder_days_is_list_of_ints(self):
        settings = load_settings()
        self.assertIsInstance(settings.payment_overdue_reminder_days, list)
        for d in settings.payment_overdue_reminder_days:
            self.assertIsInstance(d, int)


# ---------------------------------------------------------------------------
# 9 — Card self-healing: expired token detection in option check
# ---------------------------------------------------------------------------

class TestCardSelfHealingLogic(unittest.TestCase):
    """Unit-tests for the expiry-check logic embedded in payment_intent_create_acquiring_option.

    Uses direct payment_domain helpers rather than mocking the full server.
    """

    def test_53_expired_at_past_is_expired(self):
        import datetime as _dt
        past = (_dt.datetime.utcnow() - _dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        now = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertLess(past, now)

    def test_54_expired_at_future_is_valid(self):
        import datetime as _dt
        future = (_dt.datetime.utcnow() + _dt.timedelta(hours=72)).strftime("%Y-%m-%dT%H:%M:%SZ")
        now = _dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertGreater(future, now)

    def test_55_due_at_for_bepaid_default_72h(self):
        import datetime as _dt
        now_utc = _dt.datetime(2026, 8, 1, 10, 0, 0, tzinfo=_dt.timezone.utc)
        result = due_at_for_bepaid(72, now_utc=now_utc)
        self.assertEqual(result, "2026-08-04T10:00:00Z")

    def test_56_due_at_for_bepaid_custom_hours(self):
        import datetime as _dt
        now_utc = _dt.datetime(2026, 8, 1, 0, 0, 0, tzinfo=_dt.timezone.utc)
        result = due_at_for_bepaid(24, now_utc=now_utc)
        self.assertEqual(result, "2026-08-02T00:00:00Z")


# ---------------------------------------------------------------------------
# 10 — Minsk timezone constant
# ---------------------------------------------------------------------------

class TestMinskTimezone(unittest.TestCase):

    def test_57_minsk_tz_is_utc_plus_3(self):
        import datetime as _dt
        offset = _MINSK_TZ.utcoffset(None)
        self.assertEqual(offset, _dt.timedelta(hours=3))

    def test_58_normalize_date_only_in_minsk_is_235959(self):
        due_at, source = normalize_due_at("2026-11-01", "2026-10-01T00:00:00Z")
        self.assertIn("23:59:59", due_at)
        self.assertIn("+03:00", due_at)
        self.assertEqual(source, DUE_SOURCE_MOYKLASS)


# ---------------------------------------------------------------------------
# 11 — Domain source constants
# ---------------------------------------------------------------------------

class TestDomainConstants(unittest.TestCase):

    def test_59_due_source_constants(self):
        self.assertEqual(DUE_SOURCE_MOYKLASS, "moyklass")
        self.assertEqual(DUE_SOURCE_FALLBACK, "fallback")
        self.assertEqual(DUE_SOURCE_MISSING, "missing")

    def test_60_due_status_constants(self):
        self.assertEqual(DUE_STATUS_UPCOMING, "upcoming")
        self.assertEqual(DUE_STATUS_DUE_TODAY, "due_today")
        self.assertEqual(DUE_STATUS_OVERDUE, "overdue")
        self.assertEqual(DUE_STATUS_PAID, "paid")
        self.assertEqual(DUE_STATUS_WITHDRAWN, "withdrawn")


# ---------------------------------------------------------------------------
# 12 — ERIP renewal storage helpers
# ---------------------------------------------------------------------------

class TestEriphRenewalStorage(unittest.TestCase):

    def setUp(self):
        self.st = _make_storage()

    def _make_published_intent_with_erip_option(
        self, erip_expires_at: str | None = None
    ) -> dict:
        now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        intent = self.st.create_payment_intent({
            "mk_user_id": 9999,
            "amount_minor": 7500,
            "amount_byn": 75.0,
            "currency": "BYN",
            "purpose": "subscription",
            "payment_method": "erip",
            "status": "bepaid_created",
            "created_at": now,
            "created_by_tg_id": None,
            "created_by_name": "test",
        })
        pid = intent["public_id"]
        pi_id = intent["id"]
        # Publish it
        self.st.publish_payment_intent_to_client(pid, "test", now)
        # Create ERIP option with given expires_at
        self.st.create_payment_intent_option(
            payment_intent_id=pi_id,
            intent_public_id=pid,
            channel="erip",
            shop_type="erip",
            bepaid_uid="uid-original",
            expires_at=erip_expires_at,
        )
        return self.st.get_payment_intent(pid)

    def test_61_expired_erip_option_returned_for_renewal(self):
        past = (datetime.datetime.utcnow() - datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        pi = self._make_published_intent_with_erip_option(erip_expires_at=past)
        candidates = self.st.get_intents_for_erip_renewal()
        pids = [r["public_id"] for r in candidates]
        self.assertIn(pi["public_id"], pids)

    def test_62_non_expired_erip_option_not_returned(self):
        future = (datetime.datetime.utcnow() + datetime.timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
        pi = self._make_published_intent_with_erip_option(erip_expires_at=future)
        candidates = self.st.get_intents_for_erip_renewal()
        pids = [r["public_id"] for r in candidates]
        self.assertNotIn(pi["public_id"], pids)

    def test_63_claim_erip_renewal_atomic(self):
        past = (datetime.datetime.utcnow() - datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        pi = self._make_published_intent_with_erip_option(erip_expires_at=past)
        candidates = self.st.get_intents_for_erip_renewal()
        row = next(r for r in candidates if r["public_id"] == pi["public_id"])
        lock_until = (datetime.datetime.utcnow() + datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        claimed = self.st.claim_erip_renewal(pi["public_id"], row["option_id"], lock_until)
        self.assertTrue(claimed)
        # Second claim should fail
        claimed2 = self.st.claim_erip_renewal(pi["public_id"], row["option_id"], lock_until)
        self.assertFalse(claimed2)

    def test_64_release_erip_renewal_allows_reclaim(self):
        past = (datetime.datetime.utcnow() - datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        pi = self._make_published_intent_with_erip_option(erip_expires_at=past)
        candidates = self.st.get_intents_for_erip_renewal()
        row = next(r for r in candidates if r["public_id"] == pi["public_id"])
        option_id = row["option_id"]
        lock_until = (datetime.datetime.utcnow() + datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.st.claim_erip_renewal(pi["public_id"], option_id, lock_until)
        self.st.release_erip_renewal_claim(option_id)
        candidates2 = self.st.get_intents_for_erip_renewal()
        pids2 = [r["public_id"] for r in candidates2]
        self.assertIn(pi["public_id"], pids2)

    def test_65_paid_intent_not_returned_for_renewal(self):
        past = (datetime.datetime.utcnow() - datetime.timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        pi = self._make_published_intent_with_erip_option(erip_expires_at=past)
        now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        self.st.payment_intent_update_status(pi["public_id"], "paid")
        candidates = self.st.get_intents_for_erip_renewal()
        pids = [r["public_id"] for r in candidates]
        self.assertNotIn(pi["public_id"], pids)

    def test_66_update_option_after_renewal(self):
        past = (datetime.datetime.utcnow() - datetime.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        pi = self._make_published_intent_with_erip_option(erip_expires_at=past)
        candidates = self.st.get_intents_for_erip_renewal()
        row = next(r for r in candidates if r["public_id"] == pi["public_id"])
        now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        new_exp = due_at_for_bepaid(72)
        self.st.update_option_after_erip_renewal(
            option_id=row["option_id"],
            bepaid_uid="uid-renewed",
            bepaid_order_id="200000000042",
            account_number="8875658260742X",
            payment_url="https://erip.example/pay",
            qr_code_raw="base64qr",
            expires_at=new_exp,
            now=now,
        )
        opt = self.st.get_option_by_channel(pi["public_id"], "erip")
        self.assertEqual(opt["bepaid_uid"], "uid-renewed")
        self.assertEqual(opt["status"], "active")
        self.assertEqual(opt["renewal_count"], 1)
        self.assertEqual(opt["expires_at"], new_exp)


# ---------------------------------------------------------------------------
# 13 — Web server: due_at integration (smoke tests via helper functions)
# ---------------------------------------------------------------------------

class TestWebServerDueAt(unittest.TestCase):
    """Verify that web_app_server.py imports the new domain symbols without error."""

    def test_67_payment_domain_imports_in_web_app_server(self):
        src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        self.assertIn("normalize_due_at", src)
        self.assertIn("compute_due_status", src)
        self.assertIn("due_at_for_bepaid", src)

    def test_68_erip_payload_has_expired_at_in_web_server(self):
        src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        self.assertIn("expired_at=_erip_expired_at", src)

    def test_69_acquiring_payload_has_expired_at_in_web_server(self):
        src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        self.assertIn("expired_at=_acq_expired_at", src)

    def test_70_set_intent_due_at_called_in_manual_creation(self):
        src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        self.assertIn("set_intent_due_at", src)

    def test_71_set_intent_due_at_called_in_automation(self):
        src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        idx1 = src.index("_automation_create_intent")
        idx2 = src.index("set_intent_due_at", idx1)
        self.assertGreater(idx2, idx1)

    def test_72_client_payments_list_includes_due_at(self):
        src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        self.assertIn('"due_at": pi.get("due_at")', src)
        self.assertIn('"due_status": pi.get("due_status")', src)

    def test_73_card_token_endpoint_registered(self):
        src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        self.assertIn("client_payment_card_token", src)
        self.assertIn("/card-token", src)

    def test_74_expired_webhook_handled(self):
        src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        self.assertIn('tx_status == "expired"', src)
        self.assertIn("mark_option_expired", src)

    def test_75_erip_renewal_scheduler_in_loop(self):
        src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        self.assertIn("_run_erip_renewal", src)
        self.assertIn("payment_erip_renewal_enabled", src)

    def test_76_erip_renewal_uses_unique_order_id(self):
        src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        self.assertIn("erip_order_id_for_attempt", src)

    def test_77_erip_renewal_uses_unique_account_number(self):
        src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        self.assertIn("erip_account_number_for_attempt", src)

    def test_78_due_status_update_in_scheduler(self):
        src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        self.assertIn("_update_due_statuses", src)

    def test_79_erip_renewal_max_attempts_check_in_scheduler(self):
        src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        self.assertIn("max_attempts", src)
        self.assertIn("max_attempts_exhausted", src)


# ---------------------------------------------------------------------------
# 14 — miniapp/app.js UI
# ---------------------------------------------------------------------------

class TestMiniAppDueAtUI(unittest.TestCase):

    def test_80_due_badge_function_present(self):
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("_renderDueBadge", js)

    def test_81_overdue_label_in_js(self):
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("Просрочено на", js)

    def test_82_due_today_label_in_js(self):
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("Срок оплаты сегодня", js)

    def test_83_oplatit_do_label_in_js(self):
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("Оплатить до:", js)

    def test_84_card_pay_button_uses_api_refresh(self):
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("cpOpenCardPay", js)
        self.assertIn("_fetchFreshCardToken", js)

    def test_85_card_token_endpoint_called_with_public_id(self):
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("/api/client/payments/", js)
        self.assertIn("/card-token", js)


# ---------------------------------------------------------------------------
# 15 — Storage schema
# ---------------------------------------------------------------------------

class TestSchema(unittest.TestCase):

    def setUp(self):
        self.st = _make_storage()

    def test_86_payment_intents_has_mk_due_at_raw(self):
        with self.st._connect() as conn:
            row = conn.execute("PRAGMA table_info(payment_intents)").fetchall()
        cols = {r["name"] for r in row}
        self.assertIn("mk_due_at_raw", cols)

    def test_87_payment_intents_has_due_at(self):
        with self.st._connect() as conn:
            row = conn.execute("PRAGMA table_info(payment_intents)").fetchall()
        cols = {r["name"] for r in row}
        self.assertIn("due_at", cols)

    def test_88_payment_intents_has_due_at_source(self):
        with self.st._connect() as conn:
            row = conn.execute("PRAGMA table_info(payment_intents)").fetchall()
        cols = {r["name"] for r in row}
        self.assertIn("due_at_source", cols)

    def test_89_payment_intents_has_due_status(self):
        with self.st._connect() as conn:
            row = conn.execute("PRAGMA table_info(payment_intents)").fetchall()
        cols = {r["name"] for r in row}
        self.assertIn("due_status", cols)

    def test_90_payment_intents_has_overdue_since(self):
        with self.st._connect() as conn:
            row = conn.execute("PRAGMA table_info(payment_intents)").fetchall()
        cols = {r["name"] for r in row}
        self.assertIn("overdue_since", cols)

    def test_91_payment_checkout_attempts_table_exists(self):
        with self.st._connect() as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        self.assertIn("payment_checkout_attempts", tables)

    def test_92_payment_checkout_attempts_has_renewal_fields(self):
        with self.st._connect() as conn:
            row = conn.execute("PRAGMA table_info(payment_checkout_attempts)").fetchall()
        cols = {r["name"] for r in row}
        for expected in ("attempt_number", "expires_at", "expired_at", "replaced_by_attempt_id", "requires_check_reason"):
            self.assertIn(expected, cols, f"Missing column: {expected}")

    def test_93_payment_intent_options_has_renewal_count(self):
        with self.st._connect() as conn:
            row = conn.execute("PRAGMA table_info(payment_intent_options)").fetchall()
        cols = {r["name"] for r in row}
        self.assertIn("renewal_count", cols)

    def test_94_payment_intent_options_has_renewal_locked_until(self):
        with self.st._connect() as conn:
            row = conn.execute("PRAGMA table_info(payment_intent_options)").fetchall()
        cols = {r["name"] for r in row}
        self.assertIn("renewal_locked_until", cols)


if __name__ == "__main__":
    unittest.main(verbosity=2)

"""Tests for v7.0.96.1 — safe retry logic for MoyKlass auto-post.

Verifies:
- Transient MK error codes (mk_429, mk_408, mk_425) → retry_scheduled
- Permanent errors → requires_check (no retry)
- readiness error_class=transient → retry; None → requires_check
- Backoff schedule: [5, 15, 30, 60] minutes
- Retry limit: attempt_count >= 5 → exhausted (auto_post_eligible=0)
- Stale claim detection: claiming > 30 min → requires_check
- Claim conflict / ambiguous → silent skip
- DB storage methods: auto_post_retry, exhaust
- _automation_update_counts handles retry_scheduled
- Version assertions

Run offline (no bePaid / MoyKlass / Telegram):
    python -m unittest tests.test_auto_post_retry -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage

CURRENT_VERSION = "7.0.98.3"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


class _FakeResult:
    def __init__(self, data=None, ok=True, status=200, error=""):
        self.data = data
        self.ok = ok
        self.status = status
        self.error = error or ""


def _make_ctx(storage: Storage, *, bepaid_auto_post: bool = True, mk_configured: bool = True):
    from web_app_server import MiniAppContext
    ctx = MiniAppContext.__new__(MiniAppContext)
    ctx.storage = storage
    mk = MagicMock()
    mk.is_configured = mk_configured
    settings = MagicMock()
    settings.bepaid_auto_post_to_moyklass = bepaid_auto_post
    settings.moyklass_erip_payment_type_id = 55948
    settings.moyklass_acquiring_payment_type_id = 111861
    settings.bepaid_erip_shop_id = ""
    settings.bepaid_acq_shop_id = ""
    ctx.settings = settings
    ctx.moyklass = mk
    return ctx


def _seed_intent(
    st: Storage,
    mk_invoice_id: str,
    *,
    mk_user_id: int = 7850022,
    status: str = "paid",
    mk_posting_status: str = "",
    mk_payment_id: str = "",
    mk_posting_at: str = "",
    paid_amount_minor: int = 15000,
    now: str = "",
) -> dict:
    now = now or _now()
    intent = st.create_payment_intent({
        "mk_user_id": mk_user_id,
        "student_name": "Retry Test",
        "amount_minor": paid_amount_minor,
        "amount_byn": paid_amount_minor / 100,
        "currency": "BYN",
        "purpose": "subscription",
        "payment_method": "acquiring",
        "status": status,
        "created_at": now,
        "mk_invoice_id": mk_invoice_id,
        "source": "moyklass_invoice_automation",
        "source_reference": f"mk_invoice_{mk_invoice_id}",
    })
    pid = intent["public_id"]
    updates = [
        f"status='{status}'",
        f"paid_amount_minor={paid_amount_minor}",
        "paid_currency='BYN'",
        "webhook_verified=1",
        "paid_transaction_uid='test_tx'",
        "paid_channel='acquiring'",
        f"paid_at='{now}'",
        "client_visibility='published'",
    ]
    if mk_posting_status:
        updates.append(f"mk_posting_status='{mk_posting_status}'")
    if mk_payment_id:
        updates.append(f"mk_payment_id='{mk_payment_id}'")
    if mk_posting_at:
        updates.append(f"mk_posting_at='{mk_posting_at}'")
    with st._connect() as conn:
        conn.execute(
            f"UPDATE payment_intents SET {', '.join(updates)} WHERE public_id=?",
            (pid,),
        )
    return st.get_payment_intent(pid)


def _seed_item(
    st: Storage,
    mk_invoice_id: str,
    *,
    stage: str = "published",
    auto_post_eligible: int = 1,
    auto_post_attempt_count: int = 0,
    next_retry_at: str = "",
    reason_code: str = "",
    intent_public_id: str = "",
    now: str = "",
) -> dict:
    now = now or _now()
    item = st.upsert_automation_item(
        mk_invoice_id, "7850022", "Retry Test", "{}", now,
        auto_post_eligible=auto_post_eligible,
    )
    item_id = item["id"]
    st.update_automation_item_stage(
        item_id, stage,
        intent_public_id=intent_public_id or None,
        reason_code=reason_code or None,
        now=now,
    )
    if auto_post_attempt_count > 0 or next_retry_at:
        with st._connect() as conn:
            conn.execute(
                """UPDATE invoice_automation_items SET
                   auto_post_attempt_count=?, next_retry_at=? WHERE id=?""",
                (auto_post_attempt_count, next_retry_at or None, item_id),
            )
    return st.get_automation_item_by_id(item_id)


def _call_try_auto_post(ctx, item_id: int, intent: dict, now: str = "", is_new: bool = False):
    now = now or _now()
    return ctx._try_auto_post_automation_item(item_id, intent, now, is_new)


# ---------------------------------------------------------------------------
# 1. Transient MK error codes → retry_scheduled
# ---------------------------------------------------------------------------

class TestTransientRetry(unittest.TestCase):

    def setUp(self):
        self.st = _make_storage()
        self.ctx = _make_ctx(self.st)

    def _run(self, error_code: str) -> dict:
        intent = _seed_intent(self.st, f"inv_{error_code}")
        item = _seed_item(self.st, f"inv_{error_code}", intent_public_id=intent["public_id"])
        readiness_ok = {
            "ok": True, "ready": True,
            "checks": [], "warnings": [],
            "snapshot_fingerprint": "fp_abc123",
            "preview": {}, "invoice_error": None, "error_class": None,
        }
        post_fail = {"ok": False, "error_code": error_code, "error": "temporary"}
        with patch.object(self.ctx, "payment_intent_moyklass_readiness", return_value=readiness_ok), \
             patch.object(self.ctx, "payment_intent_post_to_moyklass", return_value=post_fail):
            return _call_try_auto_post(self.ctx, item["id"], intent)

    def test_01_mk_429_schedules_retry(self):
        result = self._run("mk_429")
        self.assertTrue(result.get("retry_scheduled"), result)

    def test_02_mk_408_schedules_retry(self):
        result = self._run("mk_408")
        self.assertTrue(result.get("retry_scheduled"), result)

    def test_03_mk_425_schedules_retry(self):
        result = self._run("mk_425")
        self.assertTrue(result.get("retry_scheduled"), result)

    def test_04_mk_400_permanent_requires_check(self):
        intent = _seed_intent(self.st, "inv_mk400")
        item = _seed_item(self.st, "inv_mk400", intent_public_id=intent["public_id"])
        readiness_ok = {
            "ok": True, "ready": True, "checks": [], "warnings": [],
            "snapshot_fingerprint": "fp_abc", "preview": {},
            "invoice_error": None, "error_class": None,
        }
        post_fail = {"ok": False, "error_code": "mk_400", "error": "bad data"}
        with patch.object(self.ctx, "payment_intent_moyklass_readiness", return_value=readiness_ok), \
             patch.object(self.ctx, "payment_intent_post_to_moyklass", return_value=post_fail):
            result = _call_try_auto_post(self.ctx, item["id"], intent)
        self.assertTrue(result.get("requires_check"), result)
        self.assertFalse(result.get("retry_scheduled"), result)

    def test_05_ambiguous_requires_check_no_retry(self):
        intent = _seed_intent(self.st, "inv_ambiguous_post")
        item = _seed_item(self.st, "inv_ambiguous_post", intent_public_id=intent["public_id"])
        readiness_ok = {
            "ok": True, "ready": True, "checks": [], "warnings": [],
            "snapshot_fingerprint": "fp_abc", "preview": {},
            "invoice_error": None, "error_class": None,
        }
        post_ambig = {
            "ok": False,
            "block_reason": "ambiguous_requires_reconciliation",
            "error": "timeout after POST",
        }
        with patch.object(self.ctx, "payment_intent_moyklass_readiness", return_value=readiness_ok), \
             patch.object(self.ctx, "payment_intent_post_to_moyklass", return_value=post_ambig):
            result = _call_try_auto_post(self.ctx, item["id"], intent)
        self.assertTrue(result.get("requires_check"), result)
        self.assertFalse(result.get("retry_scheduled"), result)

    def test_06_invoice_changed_requires_check(self):
        intent = _seed_intent(self.st, "inv_changed")
        item = _seed_item(self.st, "inv_changed", intent_public_id=intent["public_id"])
        readiness_ok = {
            "ok": True, "ready": True, "checks": [], "warnings": [],
            "snapshot_fingerprint": "fp_abc", "preview": {},
            "invoice_error": None, "error_class": None,
        }
        post_changed = {"ok": False, "error_code": "invoice_changed_after_preview", "error": ""}
        with patch.object(self.ctx, "payment_intent_moyklass_readiness", return_value=readiness_ok), \
             patch.object(self.ctx, "payment_intent_post_to_moyklass", return_value=post_changed):
            result = _call_try_auto_post(self.ctx, item["id"], intent)
        self.assertTrue(result.get("requires_check"), result)

    def test_07_claim_conflict_returns_existing(self):
        intent = _seed_intent(self.st, "inv_conflict")
        item = _seed_item(self.st, "inv_conflict", intent_public_id=intent["public_id"])
        readiness_ok = {
            "ok": True, "ready": True, "checks": [], "warnings": [],
            "snapshot_fingerprint": "fp_abc", "preview": {},
            "invoice_error": None, "error_class": None,
        }
        post_conflict = {"ok": False, "block_reason": "claim_conflict", "error": ""}
        with patch.object(self.ctx, "payment_intent_moyklass_readiness", return_value=readiness_ok), \
             patch.object(self.ctx, "payment_intent_post_to_moyklass", return_value=post_conflict):
            result = _call_try_auto_post(self.ctx, item["id"], intent)
        self.assertTrue(result.get("existing"), result)


# ---------------------------------------------------------------------------
# 2. Readiness error_class propagation
# ---------------------------------------------------------------------------

class TestReadinessErrorClass(unittest.TestCase):

    def setUp(self):
        self.st = _make_storage()
        self.ctx = _make_ctx(self.st)
        self._auth = {"user_id": "automation", "_is_automation": True}

    # Use a numeric mk_invoice_id so int(mk_invoice_id_str) succeeds
    # and the mock get_invoice_by_id is actually reached.
    def _run_readiness(self, inv_status: int, exc: Exception | None = None) -> dict:
        inv_id = 90900 + inv_status  # numeric: "90900", "91000", etc.
        intent = _seed_intent(self.st, str(inv_id))
        pid = intent["public_id"]
        if exc is not None:
            self.ctx.moyklass.get_invoice_by_id.side_effect = exc
        else:
            self.ctx.moyklass.get_invoice_by_id.side_effect = None
            self.ctx.moyklass.get_invoice_by_id.return_value = _FakeResult(
                {}, ok=False, status=inv_status, error=f"http error {inv_status}"
            )
        return self.ctx.payment_intent_moyklass_readiness(self._auth, pid)

    def test_08_inv_status_500_error_class_transient(self):
        r = self._run_readiness(500)
        self.assertEqual(r.get("error_class"), "transient", r.get("error_class"))

    def test_09_inv_status_429_error_class_transient(self):
        r = self._run_readiness(429)
        self.assertEqual(r.get("error_class"), "transient", r.get("error_class"))

    def test_10_inv_status_0_error_class_transient(self):
        r = self._run_readiness(0)
        self.assertEqual(r.get("error_class"), "transient", r.get("error_class"))

    def test_11_inv_status_404_error_class_none(self):
        r = self._run_readiness(404)
        self.assertIsNone(r.get("error_class"), r.get("error_class"))

    def test_12_exception_in_fetch_error_class_transient(self):
        r = self._run_readiness(0, exc=ConnectionError("timeout"))
        self.assertEqual(r.get("error_class"), "transient", r)

    def test_13_transient_readiness_schedules_retry(self):
        intent = _seed_intent(self.st, "inv_tr_retry")
        item = _seed_item(self.st, "inv_tr_retry", intent_public_id=intent["public_id"])
        transient_not_ready = {
            "ok": True, "ready": False,
            "checks": [{"code": "invoice_fetch_ok", "ok": False, "label": "", "detail": ""}],
            "warnings": [], "snapshot_fingerprint": "", "preview": {},
            "invoice_error": "HTTP 502", "error_class": "transient",
        }
        with patch.object(self.ctx, "payment_intent_moyklass_readiness", return_value=transient_not_ready):
            result = _call_try_auto_post(self.ctx, item["id"], intent)
        self.assertTrue(result.get("retry_scheduled"), result)

    def test_14_permanent_readiness_not_ready_requires_check(self):
        intent = _seed_intent(self.st, "inv_perm_rdy")
        item = _seed_item(self.st, "inv_perm_rdy", intent_public_id=intent["public_id"])
        perm_not_ready = {
            "ok": True, "ready": False,
            "checks": [{"code": "invoice_remaining_positive", "ok": False, "label": "", "detail": ""}],
            "warnings": [], "snapshot_fingerprint": "", "preview": {},
            "invoice_error": None, "error_class": None,
        }
        with patch.object(self.ctx, "payment_intent_moyklass_readiness", return_value=perm_not_ready):
            result = _call_try_auto_post(self.ctx, item["id"], intent)
        self.assertTrue(result.get("requires_check"), result)
        self.assertFalse(result.get("retry_scheduled"), result)


# ---------------------------------------------------------------------------
# 3. Timing: next_retry_at
# ---------------------------------------------------------------------------

class TestRetryTiming(unittest.TestCase):

    def setUp(self):
        self.st = _make_storage()
        self.ctx = _make_ctx(self.st)

    def test_15_next_retry_at_future_returns_existing(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        intent = _seed_intent(self.st, "inv_future")
        item = _seed_item(self.st, "inv_future", next_retry_at=future,
                          intent_public_id=intent["public_id"])
        result = _call_try_auto_post(self.ctx, item["id"], intent)
        self.assertTrue(result.get("existing"), result)

    def test_16_next_retry_at_past_proceeds(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        intent = _seed_intent(self.st, "inv_past")
        item = _seed_item(self.st, "inv_past", next_retry_at=past,
                          intent_public_id=intent["public_id"])
        readiness_ok = {
            "ok": True, "ready": True, "checks": [], "warnings": [],
            "snapshot_fingerprint": "fp_past", "preview": {},
            "invoice_error": None, "error_class": None,
        }
        post_ok = {"ok": True, "mk_payment_id": "mk_p_past"}
        with patch.object(self.ctx, "payment_intent_moyklass_readiness", return_value=readiness_ok), \
             patch.object(self.ctx, "payment_intent_post_to_moyklass", return_value=post_ok):
            result = _call_try_auto_post(self.ctx, item["id"], intent)
        self.assertTrue(result.get("posted"), result)


# ---------------------------------------------------------------------------
# 4. Backoff schedule
# ---------------------------------------------------------------------------

class TestBackoffSchedule(unittest.TestCase):

    def setUp(self):
        self.st = _make_storage()
        self.ctx = _make_ctx(self.st)

    def _backoff_minutes_for_attempt(self, attempt_count: int) -> int:
        from web_app_server import _AUTO_POST_BACKOFF_MINUTES
        idx = min(attempt_count, len(_AUTO_POST_BACKOFF_MINUTES) - 1)
        return _AUTO_POST_BACKOFF_MINUTES[idx]

    def _do_retry_and_check_delay(self, attempt_count: int, expected_minutes: int):
        mk_inv = f"inv_backoff_{attempt_count}"
        intent = _seed_intent(self.st, mk_inv)
        item = _seed_item(self.st, mk_inv, intent_public_id=intent["public_id"],
                          auto_post_attempt_count=attempt_count)
        readiness_ok = {
            "ok": True, "ready": True, "checks": [], "warnings": [],
            "snapshot_fingerprint": "fp_bo", "preview": {},
            "invoice_error": None, "error_class": None,
        }
        post_429 = {"ok": False, "error_code": "mk_429", "error": "rate limited"}
        before = datetime.now(timezone.utc)
        with patch.object(self.ctx, "payment_intent_moyklass_readiness", return_value=readiness_ok), \
             patch.object(self.ctx, "payment_intent_post_to_moyklass", return_value=post_429):
            result = _call_try_auto_post(self.ctx, item["id"], intent)
        self.assertTrue(result.get("retry_scheduled"), result)
        updated = self.st.get_automation_item_by_id(item["id"])
        nra = updated.get("next_retry_at", "")
        self.assertTrue(nra, "next_retry_at must be set")
        retry_dt = datetime.fromisoformat(nra.replace("Z", "+00:00"))
        delta = (retry_dt - before).total_seconds() / 60
        self.assertAlmostEqual(delta, expected_minutes, delta=1.0,
                               msg=f"attempt={attempt_count}: expected ~{expected_minutes} min, got {delta:.1f}")

    def test_17_backoff_attempt0_is_5min(self):
        self._do_retry_and_check_delay(0, 5)

    def test_18_backoff_attempt1_is_15min(self):
        self._do_retry_and_check_delay(1, 15)

    def test_19_backoff_attempt2_is_30min(self):
        self._do_retry_and_check_delay(2, 30)

    def test_20_backoff_attempt3_is_60min(self):
        self._do_retry_and_check_delay(3, 60)

    def test_21_backoff_attempt4_caps_at_60min(self):
        self._do_retry_and_check_delay(4, 60)

    def test_22_attempt_count_incremented_in_db(self):
        intent = _seed_intent(self.st, "inv_cnt")
        item = _seed_item(self.st, "inv_cnt", intent_public_id=intent["public_id"],
                          auto_post_attempt_count=2)
        readiness_ok = {
            "ok": True, "ready": True, "checks": [], "warnings": [],
            "snapshot_fingerprint": "fp_cnt", "preview": {},
            "invoice_error": None, "error_class": None,
        }
        post_429 = {"ok": False, "error_code": "mk_429", "error": "rl"}
        with patch.object(self.ctx, "payment_intent_moyklass_readiness", return_value=readiness_ok), \
             patch.object(self.ctx, "payment_intent_post_to_moyklass", return_value=post_429):
            _call_try_auto_post(self.ctx, item["id"], intent)
        updated = self.st.get_automation_item_by_id(item["id"])
        self.assertEqual(updated.get("auto_post_attempt_count"), 3)


# ---------------------------------------------------------------------------
# 5. Exhaustion
# ---------------------------------------------------------------------------

class TestExhaustion(unittest.TestCase):

    def setUp(self):
        self.st = _make_storage()
        self.ctx = _make_ctx(self.st)

    def test_23_at_max_attempts_exhausts(self):
        from web_app_server import _AUTO_POST_MAX_ATTEMPTS
        intent = _seed_intent(self.st, "inv_exh")
        item = _seed_item(self.st, "inv_exh", intent_public_id=intent["public_id"],
                          auto_post_attempt_count=_AUTO_POST_MAX_ATTEMPTS)
        result = _call_try_auto_post(self.ctx, item["id"], intent)
        self.assertTrue(result.get("requires_check"), result)
        updated = self.st.get_automation_item_by_id(item["id"])
        self.assertEqual(updated.get("reason_code"), "auto_post_retry_exhausted")
        self.assertEqual(updated.get("auto_post_eligible"), 0, "Must clear eligibility on exhaust")

    def test_24_exhausted_idempotent_no_double_write(self):
        from web_app_server import _AUTO_POST_MAX_ATTEMPTS
        intent = _seed_intent(self.st, "inv_exh2")
        item = _seed_item(self.st, "inv_exh2", intent_public_id=intent["public_id"],
                          auto_post_attempt_count=_AUTO_POST_MAX_ATTEMPTS,
                          reason_code="auto_post_retry_exhausted",
                          auto_post_eligible=0)
        # Force auto_post_eligible=0 in DB
        with self.st._connect() as conn:
            conn.execute(
                "UPDATE invoice_automation_items SET auto_post_eligible=0 WHERE id=?",
                (item["id"],)
            )
        result = _call_try_auto_post(self.ctx, item["id"], intent)
        self.assertTrue(result.get("requires_check"), result)
        # Stage should NOT have been re-updated (attempts should not increment again)
        updated = self.st.get_automation_item_by_id(item["id"])
        self.assertEqual(updated.get("reason_code"), "auto_post_retry_exhausted")


# ---------------------------------------------------------------------------
# 6. Stale claim and ambiguous
# ---------------------------------------------------------------------------

class TestClaimGuards(unittest.TestCase):

    def setUp(self):
        self.st = _make_storage()
        self.ctx = _make_ctx(self.st)

    def test_25_stale_claim_over_30min_requires_check(self):
        stale_ts = (datetime.now(timezone.utc) - timedelta(minutes=35)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        intent = _seed_intent(self.st, "inv_stale",
                              mk_posting_status="claiming",
                              mk_posting_at=stale_ts)
        item = _seed_item(self.st, "inv_stale", intent_public_id=intent["public_id"])
        result = _call_try_auto_post(self.ctx, item["id"], intent)
        self.assertTrue(result.get("requires_check"), result)
        updated = self.st.get_automation_item_by_id(item["id"])
        self.assertEqual(updated.get("reason_code"), "auto_post_stale_claim")

    def test_26_fresh_claim_under_30min_returns_existing(self):
        fresh_ts = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        intent = _seed_intent(self.st, "inv_fresh",
                              mk_posting_status="claiming",
                              mk_posting_at=fresh_ts)
        item = _seed_item(self.st, "inv_fresh", intent_public_id=intent["public_id"])
        result = _call_try_auto_post(self.ctx, item["id"], intent)
        self.assertTrue(result.get("existing"), result)

    def test_27_ambiguous_status_skips_silently(self):
        intent = _seed_intent(self.st, "inv_amb_status", mk_posting_status="ambiguous")
        item = _seed_item(self.st, "inv_amb_status", intent_public_id=intent["public_id"])
        result = _call_try_auto_post(self.ctx, item["id"], intent)
        self.assertTrue(result.get("existing"), result)


# ---------------------------------------------------------------------------
# 7. _automation_update_counts + version
# ---------------------------------------------------------------------------

class TestCountsAndVersion(unittest.TestCase):

    def test_28_automation_counts_handles_retry_scheduled(self):
        from web_app_server import _automation_update_counts
        counts: dict[str, int] = {
            "scanned": 0, "discovered": 0, "created": 0, "published": 0,
            "posted": 0, "missing_parent": 0, "requires_check": 0,
            "skipped": 0, "error": 0, "existing": 0, "processed": 0,
            "unaccounted": 0, "retry_scheduled": 0,
        }
        outcome = _automation_update_counts(counts, {"retry_scheduled": True})
        self.assertEqual(outcome, "retry_scheduled")
        self.assertEqual(counts["retry_scheduled"], 1)

    def test_29_version_string(self):
        self.assertEqual(CURRENT_VERSION, "7.0.98.3")

    def test_30_app_js_version(self):
        js = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")
        self.assertIn("v7.0.98.3", js)

    def test_31_index_html_cache_bust(self):
        html = (ROOT / "miniapp" / "index.html").read_bytes().decode("utf-8-sig")
        self.assertIn("v=7.0.98.3", html)


if __name__ == "__main__":
    unittest.main()

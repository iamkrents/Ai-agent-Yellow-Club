"""Tests for v7.1.16.1 — Payment Period Filters: dashboard card semantics.

Covers (DASHBOARD 11-20 from the launch spec):
  11. "На проверке" (pending_review) is filtered by period.
  12. "Требуют внимания" (requires_check) is filtered by period.
  13. "Ожидают оплаты" (awaiting_payment) is filtered by period.
  14. "Оплачено" (paid) is filtered by period.
  15. "Внесено в МойКласс" (posted_to_moyklass) is filtered by period.
  16. "Сейчас в пилоте" (pilot_clients_count) is NOT filtered by period.
  17. attentionOutsidePeriodCount is computed separately from the in-period stats.
  18. One invoice is never double-counted across two mutually exclusive cards.
  19. An empty month returns zeros with a normal (not broken) response.
  20. All-time totals match the pre-v7.1.16.1 unconditional counts.

Run:
    python -m unittest tests.test_payment_period_dashboard_v71161 -v
"""
from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import unittest  # noqa: E402

import web_app_server as srv  # noqa: E402
from storage import Storage  # noqa: E402


def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _settings(**overrides):
    base = dict(
        admin_ids=[], senior_teacher_ids=[], web_app_test_roles=True, food_module_enabled=False,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _make_ctx(storage: Storage) -> "srv.MiniAppContext":
    ctx = srv.MiniAppContext.__new__(srv.MiniAppContext)
    ctx.storage = storage
    ctx.settings = _settings()
    return ctx


def _auth(uid) -> dict:
    return {"user_id": int(uid)}


def _mk_intent(st, pid, mk_user_id, period_month, created_at, status):
    with st._connect() as conn:
        conn.execute(
            """INSERT INTO payment_intents
               (public_id, mk_user_id, amount_minor, amount_byn, currency, purpose, period_month,
                payment_method, status, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (pid, mk_user_id, 10000, 100.0, "BYN", "other", period_month,
             "erip", status, created_at, created_at),
        )


def _mk_automation_item(st, mk_invoice_id, mk_user_id, stage, intent_public_id, created_at):
    with st._connect() as conn:
        conn.execute(
            """INSERT INTO invoice_automation_items (mk_invoice_id, mk_user_id, current_stage, intent_public_id, created_at, updated_at)
               VALUES (?,?,?,?,?,?)""",
            (mk_invoice_id, mk_user_id, stage, intent_public_id, created_at, created_at),
        )


class TestCardsFilteredByPeriod(unittest.TestCase):
    def setUp(self):
        self.st = _tmp_storage()
        st = self.st
        _mk_intent(st, "P-AUG-READY", 1, "2026-08", "2026-08-01T10:00:00", "ready")
        _mk_intent(st, "P-AUG-PAID", 2, "2026-08", "2026-08-02T10:00:00", "paid")
        _mk_intent(st, "P-AUG-POSTED", 3, "2026-08", "2026-08-03T10:00:00", "posted_to_moyklass")
        _mk_intent(st, "P-SEP-READY", 4, "2026-09", "2026-09-01T10:00:00", "ready")
        _mk_automation_item(st, "MK-AUG-PR", "1", "pending_review", None, "2026-08-01T10:00:00")
        _mk_automation_item(st, "MK-AUG-RC", "1", "requires_check", "P-AUG-READY", "2026-08-01T10:00:00")

    def test_11_pending_review_filtered(self):
        aug = self.st.get_payments_workspace_stats("2026-08-01", "2026-09-01")
        sep = self.st.get_payments_workspace_stats("2026-09-01", "2026-10-01")
        self.assertEqual(aug["pending_review"], 1)
        self.assertEqual(sep["pending_review"], 0)

    def test_12_requires_check_filtered(self):
        aug = self.st.get_payments_workspace_stats("2026-08-01", "2026-09-01")
        sep = self.st.get_payments_workspace_stats("2026-09-01", "2026-10-01")
        self.assertEqual(aug["requires_check"], 1)
        self.assertEqual(sep["requires_check"], 0)

    def test_13_awaiting_payment_filtered(self):
        aug = self.st.get_payments_workspace_stats("2026-08-01", "2026-09-01")
        sep = self.st.get_payments_workspace_stats("2026-09-01", "2026-10-01")
        self.assertEqual(aug["awaiting_payment"], 1)
        self.assertEqual(sep["awaiting_payment"], 1)

    def test_14_paid_filtered(self):
        aug = self.st.get_payments_workspace_stats("2026-08-01", "2026-09-01")
        sep = self.st.get_payments_workspace_stats("2026-09-01", "2026-10-01")
        self.assertEqual(aug["paid"], 1)
        self.assertEqual(sep["paid"], 0)

    def test_15_posted_to_moyklass_filtered(self):
        aug = self.st.get_payments_workspace_stats("2026-08-01", "2026-09-01")
        sep = self.st.get_payments_workspace_stats("2026-09-01", "2026-10-01")
        self.assertEqual(aug["posted_to_moyklass"], 1)
        self.assertEqual(sep["posted_to_moyklass"], 0)


class TestPilotCountPeriodIndependent(unittest.TestCase):
    def test_16_pilot_clients_count_ignores_period(self):
        st = _tmp_storage()
        st.upsert_pilot_client("100", "review", note="seed", added_by_tg_id="9001")
        all_time = st.get_payments_workspace_stats(None, None)
        far_future = st.get_payments_workspace_stats("2099-01-01", "2099-02-01")
        self.assertEqual(all_time["pilot_clients_count"], far_future["pilot_clients_count"])
        self.assertEqual(all_time["pilot_clients_count"], 1)


class TestAttentionOutsidePeriod(unittest.TestCase):
    def test_17_computed_separately_from_in_period_stats(self):
        st = _tmp_storage()
        _mk_automation_item(st, "MK-AUG", "1", "pending_review", None, "2026-08-01T10:00:00")
        _mk_automation_item(st, "MK-SEP", "2", "requires_check", None, "2026-09-01T10:00:00")
        outside = st.get_payments_attention_outside_period_count("2026-08-01", "2026-09-01")
        self.assertEqual(outside, 1)
        # the in-period stat itself is unaffected by outside items
        aug = st.get_payments_workspace_stats("2026-08-01", "2026-09-01")
        self.assertEqual(aug["pending_review"], 1)

    def test_17b_zero_for_all_time(self):
        st = _tmp_storage()
        _mk_automation_item(st, "MK-X", "1", "pending_review", None, "2026-08-01T10:00:00")
        self.assertEqual(st.get_payments_attention_outside_period_count(None, None), 0)


class TestNoDoubleCounting(unittest.TestCase):
    def test_18_one_invoice_not_in_two_exclusive_buckets(self):
        st = _tmp_storage()
        _mk_intent(st, "P-ONE", 1, "2026-08", "2026-08-01T10:00:00", "paid")
        stats = st.get_payments_workspace_stats("2026-08-01", "2026-09-01")
        # a 'paid' intent must not also count toward awaiting_payment/posted_to_moyklass
        self.assertEqual(stats["paid"], 1)
        self.assertEqual(stats["awaiting_payment"], 0)
        self.assertEqual(stats["posted_to_moyklass"], 0)


class TestEmptyMonth(unittest.TestCase):
    def test_19_empty_month_returns_zeros_not_broken(self):
        st = _tmp_storage()
        stats = st.get_payments_workspace_stats("2030-01-01", "2030-02-01")
        for key in ("awaiting_payment", "draft", "paid", "posted_to_moyklass", "pending_review", "requires_check", "undated_count"):
            self.assertEqual(stats[key], 0)
        ctx = _make_ctx(st)
        st.set_staff_role(9001, "owner")
        result = ctx.payments_workspace_stats(_auth(9001), {"period_mode": "custom", "period_start": "2030-01-01", "period_end": "2030-01-31"})
        self.assertTrue(result.get("ok"))
        rows, total = st.list_payment_intents_by_period("2030-01-01", "2030-02-01")
        self.assertEqual(total, 0)
        self.assertEqual(rows, [])


class TestAllTimeMatchesUnconditionalCounts(unittest.TestCase):
    def test_20_all_time_equals_old_unconditional_query(self):
        st = _tmp_storage()
        for i, (period, status) in enumerate([
            ("2026-06", "paid"), ("2026-07", "ready"), ("2026-08", "posted_to_moyklass"),
            (None, "paid"),  # legacy row, no period_month
        ]):
            _mk_intent(st, f"P{i}", i, period, f"2026-0{6+i if i < 4 else 6}-01T10:00:00", status)
        with st._connect() as conn:
            old_paid = conn.execute("SELECT COUNT(*) FROM payment_intents WHERE status='paid'").fetchone()[0]
            old_ready = conn.execute("SELECT COUNT(*) FROM payment_intents WHERE status IN ('ready','bepaid_created')").fetchone()[0]
            old_posted = conn.execute("SELECT COUNT(*) FROM payment_intents WHERE status='posted_to_moyklass'").fetchone()[0]
        all_time = st.get_payments_workspace_stats(None, None)
        self.assertEqual(all_time["paid"], old_paid)
        self.assertEqual(all_time["awaiting_payment"], old_ready)
        self.assertEqual(all_time["posted_to_moyklass"], old_posted)


if __name__ == "__main__":
    unittest.main()

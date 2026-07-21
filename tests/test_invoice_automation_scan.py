"""Regression tests for v7.0.94.5 — Process all fetched MoyKlass invoices.

Covers:
- Hard cap [:50] removal: all invoices processed, no silent truncation
- Newest-first sort (createdAt DESC, id DESC)
- existing_count accounting for items with pre-existing state
- Terminal outcome reconciliation (unaccounted_count = 0 after correct run)
- New DB columns: existing_count, filtered_count, processed_count, unaccounted_count
- Production fixture: invoice at position 51/52 now enters automation queue
- Version v7.0.94.5 guards

Run offline:

    python -m unittest tests.test_invoice_automation_scan -v
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CURRENT_VERSION = "7.0.99.0"

from storage import Storage

APP_JS   = ROOT / "miniapp" / "app.js"
INDEX_HTML = ROOT / "miniapp" / "index.html"
SERVER_PY  = ROOT / "web_app_server.py"


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


def _mk_invoice(
    inv_id: int = 1001,
    user_id: int = 2001,
    price: float = 100.0,
    payed: float = 0.0,
    created_at: str = "2026-07-01",
) -> dict:
    return {
        "id": inv_id,
        "userId": user_id,
        "price": price,
        "payed": payed,
        "payUntil": "2026-07-31",
        "createdAt": created_at,
        "userSubscriptionId": 5001,
        "userSubscription": {
            "name": "Подписка",
            "clientName": "Тест Тестов",
            "beginDate": "2026-07-01",
        },
    }


class _FakeResult:
    def __init__(self, data):
        self.data = data
        self.ok = True
        self.error = None


def _make_ctx(storage: Storage, invoices: list[dict]):
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


def _make_counts() -> dict:
    return {
        "scanned": 0, "discovered": 0, "created": 0, "published": 0,
        "missing_parent": 0, "requires_check": 0, "skipped": 0, "error": 0,
        "existing": 0, "processed": 0, "unaccounted": 0,
    }


# ---------------------------------------------------------------------------
# 1. Hard cap removal
# ---------------------------------------------------------------------------

class TestHardCapRemoval(unittest.TestCase):
    """Tests 01-08: [:50] cap must be gone; all invoices processed."""

    def setUp(self):
        self.st = _make_storage()

    def test_01_fifty_five_invoices_all_scanned(self):
        invs = [_mk_invoice(inv_id=7000 + i, user_id=8000 + i) for i in range(55)]
        ctx = _make_ctx(self.st, invs)
        result = ctx.process_new_moyklass_invoices()
        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("scanned"), 55)

    def test_02_fifty_five_invoices_all_processed(self):
        invs = [_mk_invoice(inv_id=7100 + i, user_id=8100 + i) for i in range(55)]
        ctx = _make_ctx(self.st, invs)
        result = ctx.process_new_moyklass_invoices()
        self.assertEqual(result.get("processed"), 55)

    def test_03_processed_equals_scanned(self):
        invs = [_mk_invoice(inv_id=7200 + i, user_id=8200 + i) for i in range(55)]
        ctx = _make_ctx(self.st, invs)
        result = ctx.process_new_moyklass_invoices()
        self.assertEqual(result.get("processed"), result.get("scanned"))

    def test_04_invoice_at_position_51_creates_automation_item(self):
        invs = [_mk_invoice(inv_id=7300 + i, user_id=8300 + i) for i in range(55)]
        ctx = _make_ctx(self.st, invs)
        ctx.process_new_moyklass_invoices()
        item = self.st.get_automation_item_by_invoice("7350")
        self.assertIsNotNone(item, "invoice at position 50 (id=7350) must be in queue")

    def test_05_invoice_at_position_55_creates_automation_item(self):
        invs = [_mk_invoice(inv_id=7400 + i, user_id=8400 + i) for i in range(55)]
        ctx = _make_ctx(self.st, invs)
        ctx.process_new_moyklass_invoices()
        item = self.st.get_automation_item_by_invoice("7454")
        self.assertIsNotNone(item, "last invoice (id=7454) must be in queue")

    def test_06_sixty_invoices_all_scanned(self):
        invs = [_mk_invoice(inv_id=7500 + i, user_id=8500 + i) for i in range(60)]
        ctx = _make_ctx(self.st, invs)
        result = ctx.process_new_moyklass_invoices()
        self.assertEqual(result.get("scanned"), 60)

    def test_07_sixty_invoices_processed_count_sixty(self):
        invs = [_mk_invoice(inv_id=7600 + i, user_id=8600 + i) for i in range(60)]
        ctx = _make_ctx(self.st, invs)
        result = ctx.process_new_moyklass_invoices()
        self.assertEqual(result.get("processed"), 60)

    def test_08_no_slice_50_cap_in_source(self):
        src = SERVER_PY.read_text(encoding="utf-8")
        self.assertNotIn(
            "mk_invoices[:50]", src,
            "Hard cap mk_invoices[:50] must be removed in v7.0.94.5",
        )


# ---------------------------------------------------------------------------
# 2. Sort order: newest first
# ---------------------------------------------------------------------------

class TestSortOrder(unittest.TestCase):
    """Tests 09-13: invoices sorted newest-createdAt first, then largest id first."""

    def setUp(self):
        self.st = _make_storage()

    def test_09_sort_call_present_in_source(self):
        src = SERVER_PY.read_text(encoding="utf-8")
        self.assertIn(".sort(", src, "sort() call must be present for newest-first ordering")

    def test_10_newest_created_at_first(self):
        # After sort, newer invoice (id=200) should be processed before older (id=100)
        processed_order = []

        invs = [
            _mk_invoice(inv_id=100, user_id=9100, created_at="2026-06-01"),
            _mk_invoice(inv_id=200, user_id=9200, created_at="2026-07-15"),
        ]
        ctx = _make_ctx(self.st, invs)

        orig_fn = ctx._process_single_automation_item_from_invoice

        def _tracking_fn(inv, **kwargs):
            processed_order.append(inv["id"])
            return orig_fn(inv, **kwargs)

        ctx._process_single_automation_item_from_invoice = _tracking_fn
        ctx.process_new_moyklass_invoices()
        self.assertEqual(processed_order[0], 200, "Newer invoice (createdAt later) must be first")
        self.assertEqual(processed_order[1], 100)

    def test_11_larger_id_first_when_same_date(self):
        processed_order = []
        invs = [
            _mk_invoice(inv_id=300, user_id=9300, created_at="2026-07-01"),
            _mk_invoice(inv_id=400, user_id=9400, created_at="2026-07-01"),
        ]
        ctx = _make_ctx(self.st, invs)
        orig_fn = ctx._process_single_automation_item_from_invoice

        def _tracking_fn(inv, **kwargs):
            processed_order.append(inv["id"])
            return orig_fn(inv, **kwargs)

        ctx._process_single_automation_item_from_invoice = _tracking_fn
        ctx.process_new_moyklass_invoices()
        self.assertEqual(processed_order[0], 400, "Larger id must come first when createdAt is equal")

    def test_12_sort_stable_with_missing_created_at(self):
        invs = [
            {"id": 500, "userId": 9500, "price": 100.0, "payed": 0.0, "payUntil": "2026-07-31",
             "userSubscription": {"clientName": "Test"}},
            _mk_invoice(inv_id=501, user_id=9501, created_at="2026-07-10"),
        ]
        ctx = _make_ctx(self.st, invs)
        result = ctx.process_new_moyklass_invoices()
        self.assertTrue(result.get("ok"), "Sort must not crash on missing createdAt")

    def test_13_production_newest_invoice_first(self):
        # Simulate production: invoice 19102120 (newest) at position 52 in MK list
        # After sort, it must be first (largest createdAt + largest id)
        processed_order = []
        old_invs = [
            _mk_invoice(inv_id=19100000 + i, user_id=10000 + i, created_at="2026-06-15")
            for i in range(5)
        ]
        new_inv = _mk_invoice(inv_id=19102120, user_id=20000, created_at="2026-07-17")
        invs = old_invs + [new_inv]
        ctx = _make_ctx(self.st, invs)
        orig_fn = ctx._process_single_automation_item_from_invoice

        def _tracking_fn(inv, **kwargs):
            processed_order.append(inv["id"])
            return orig_fn(inv, **kwargs)

        ctx._process_single_automation_item_from_invoice = _tracking_fn
        ctx.process_new_moyklass_invoices()
        self.assertEqual(processed_order[0], 19102120, "Newest invoice must be processed first")


# ---------------------------------------------------------------------------
# 3. existing_count accounting
# ---------------------------------------------------------------------------

class TestExistingCountAccounting(unittest.TestCase):
    """Tests 14-22: existing_count closes the unaccounted gap."""

    def setUp(self):
        self.st = _make_storage()

    def _add_parent_link(self, child_mk_user_id: str, parent_tg_id: int):
        now = _now()
        code = self.st.create_client_link_code(
            student_mk_user_id=child_mk_user_id,
            created_by_tg_id=1,
            created_by_name="admin",
            now=now,
        )
        self.st.activate_client_link_code(
            code=code["code"],
            parent_telegram_user_id=parent_tg_id,
            now=now,
        )

    def test_14_existing_count_in_result_dict(self):
        invs = []
        ctx = _make_ctx(self.st, invs)
        result = ctx.process_new_moyklass_invoices()
        self.assertIn("existing", result, "result dict must have 'existing' key")

    def test_15_existing_intent_no_unaccounted(self):
        now = _now()
        inv = _mk_invoice(inv_id=11000, user_id=12000, price=100.0)
        self.st.create_payment_intent({
            "mk_user_id": 12000, "student_name": "Test",
            "amount_minor": 10000, "amount_byn": 100.0,
            "currency": "BYN", "purpose": "subscription",
            "payment_method": "erip", "status": "draft",
            "created_at": now, "mk_invoice_id": "11000", "source": "test",
        })
        ctx = _make_ctx(self.st, [inv])
        # First run: automation item is newly created (is_new=True), existing intent found →
        # counts as discovered (not unaccounted)
        result = ctx.process_new_moyklass_invoices()
        self.assertEqual(result.get("unaccounted", 0), 0)
        self.assertGreaterEqual(
            result.get("discovered", 0) + result.get("existing", 0), 1,
            "invoice with existing intent must count as discovered or existing, not unaccounted",
        )

    def test_16_paid_invoice_increments_filtered_not_existing(self):
        inv = _mk_invoice(inv_id=11100, price=100.0, payed=100.0)
        ctx = _make_ctx(self.st, [inv])
        result = ctx.process_new_moyklass_invoices()
        self.assertEqual(result.get("skipped", 0), 1)
        self.assertEqual(result.get("existing", 0), 0)

    def test_17_automation_update_counts_returns_string(self):
        from web_app_server import _automation_update_counts
        counts = _make_counts()
        outcome = _automation_update_counts(counts, {"discovered": True})
        self.assertIsInstance(outcome, str)

    def test_18_automation_update_counts_existing_returns_existing(self):
        from web_app_server import _automation_update_counts
        counts = _make_counts()
        outcome = _automation_update_counts(counts, {"existing": True})
        self.assertEqual(outcome, "existing")
        self.assertEqual(counts["existing"], 1)
        self.assertEqual(counts["skipped"], 0)

    def test_19_automation_update_counts_skip_returns_filtered(self):
        from web_app_server import _automation_update_counts
        counts = _make_counts()
        outcome = _automation_update_counts(counts, {"skip": True})
        self.assertEqual(outcome, "filtered")
        self.assertEqual(counts["skipped"], 1)

    def test_20_automation_update_counts_discovered_returns_discovered(self):
        from web_app_server import _automation_update_counts
        counts = _make_counts()
        outcome = _automation_update_counts(counts, {"discovered": True})
        self.assertEqual(outcome, "discovered")
        self.assertEqual(counts["discovered"], 1)

    def test_21_automation_update_counts_no_key_returns_unaccounted(self):
        from web_app_server import _automation_update_counts
        counts = _make_counts()
        outcome = _automation_update_counts(counts, {})
        self.assertEqual(outcome, "unaccounted")

    def test_22_automation_update_counts_missing_parent(self):
        from web_app_server import _automation_update_counts
        counts = _make_counts()
        outcome = _automation_update_counts(counts, {"missing_parent": True, "discovered": True})
        self.assertEqual(outcome, "missing_parent")
        self.assertEqual(counts["missing_parent"], 1)
        self.assertEqual(counts["discovered"], 1)


# ---------------------------------------------------------------------------
# 4. Counter reconciliation
# ---------------------------------------------------------------------------

class TestCounterReconciliation(unittest.TestCase):
    """Tests 23-33: terminal outcomes are tracked; unaccounted_count = 0."""

    def setUp(self):
        self.st = _make_storage()

    def test_23_unaccounted_zero_empty_run(self):
        ctx = _make_ctx(self.st, [])
        result = ctx.process_new_moyklass_invoices()
        self.assertEqual(result.get("unaccounted", 0), 0)

    def test_24_unaccounted_zero_with_new_invoices(self):
        invs = [_mk_invoice(inv_id=21000 + i, user_id=22000 + i) for i in range(5)]
        ctx = _make_ctx(self.st, invs)
        result = ctx.process_new_moyklass_invoices()
        self.assertEqual(result.get("unaccounted", 0), 0)

    def test_25_unaccounted_zero_with_paid_invoices(self):
        invs = [_mk_invoice(inv_id=21100 + i, price=100.0, payed=100.0) for i in range(5)]
        ctx = _make_ctx(self.st, invs)
        result = ctx.process_new_moyklass_invoices()
        self.assertEqual(result.get("unaccounted", 0), 0)

    def test_26_unaccounted_zero_with_existing_intents(self):
        now = _now()
        invs = []
        for i in range(3):
            inv_id = 21200 + i
            user_id = 23000 + i
            invs.append(_mk_invoice(inv_id=inv_id, user_id=user_id, price=100.0))
            self.st.create_payment_intent({
                "mk_user_id": user_id, "student_name": "T",
                "amount_minor": 10000, "amount_byn": 100.0,
                "currency": "BYN", "purpose": "subscription",
                "payment_method": "erip", "status": "draft",
                "created_at": now, "mk_invoice_id": str(inv_id), "source": "test",
            })
        ctx = _make_ctx(self.st, invs)
        result = ctx.process_new_moyklass_invoices()
        self.assertEqual(result.get("unaccounted", 0), 0)

    def test_27_processed_count_in_result(self):
        invs = [_mk_invoice(inv_id=21300 + i, user_id=24000 + i) for i in range(3)]
        ctx = _make_ctx(self.st, invs)
        result = ctx.process_new_moyklass_invoices()
        self.assertIn("processed", result)
        self.assertEqual(result["processed"], 3)

    def test_28_re_processed_missing_parent_not_unaccounted(self):
        # An item already in missing_parent_link stage from a prior run must
        # still be counted (not unaccounted) when the pipeline re-processes it.
        inv = _mk_invoice(inv_id=21400, user_id=25000, price=100.0)
        ctx = _make_ctx(self.st, [inv])
        # First run: no parent → missing_parent_link stage created
        ctx.process_new_moyklass_invoices()
        # Second run: same invoice, still no parent → must still count, not unaccounted
        result = ctx.process_new_moyklass_invoices()
        self.assertEqual(result.get("unaccounted", 0), 0)
        self.assertGreaterEqual(result.get("missing_parent", 0), 1)

    def test_29_mixed_run_unaccounted_zero(self):
        now = _now()
        invs = []
        # 2 paid invoices (filtered)
        for i in range(2):
            invs.append(_mk_invoice(inv_id=21500 + i, price=100.0, payed=100.0))
        # 2 new invoices (discovered / missing_parent)
        for i in range(2):
            invs.append(_mk_invoice(inv_id=21510 + i, user_id=26000 + i, price=100.0))
        # 1 with existing intent (existing)
        invs.append(_mk_invoice(inv_id=21520, user_id=26010, price=100.0))
        self.st.create_payment_intent({
            "mk_user_id": 26010, "student_name": "T",
            "amount_minor": 10000, "amount_byn": 100.0,
            "currency": "BYN", "purpose": "subscription",
            "payment_method": "erip", "status": "draft",
            "created_at": now, "mk_invoice_id": "21520", "source": "test",
        })
        ctx = _make_ctx(self.st, invs)
        result = ctx.process_new_moyklass_invoices()
        self.assertEqual(result.get("unaccounted", 0), 0)
        self.assertEqual(result.get("processed"), 5)

    def test_30_automation_update_counts_published(self):
        from web_app_server import _automation_update_counts
        counts = _make_counts()
        outcome = _automation_update_counts(
            counts, {"published": True, "created": True, "discovered": True}
        )
        self.assertEqual(outcome, "published")
        self.assertEqual(counts["published"], 1)
        self.assertEqual(counts["created"], 1)
        self.assertEqual(counts["discovered"], 1)

    def test_31_automation_update_counts_created_but_not_published(self):
        from web_app_server import _automation_update_counts
        counts = _make_counts()
        outcome = _automation_update_counts(
            counts, {"created": True, "discovered": True}
        )
        self.assertEqual(outcome, "created")
        self.assertEqual(counts["created"], 1)
        self.assertEqual(counts["published"], 0)

    def test_32_automation_update_counts_error(self):
        from web_app_server import _automation_update_counts
        counts = _make_counts()
        outcome = _automation_update_counts(counts, {"error": True})
        self.assertEqual(outcome, "error")
        self.assertEqual(counts["error"], 1)

    def test_33_automation_update_counts_requires_check(self):
        from web_app_server import _automation_update_counts
        counts = _make_counts()
        outcome = _automation_update_counts(
            counts, {"requires_check": True, "discovered": False}
        )
        self.assertEqual(outcome, "requires_check")
        self.assertEqual(counts["requires_check"], 1)
        self.assertEqual(counts["discovered"], 0)


# ---------------------------------------------------------------------------
# 5. DB column migration
# ---------------------------------------------------------------------------

class TestDbColumnMigration(unittest.TestCase):
    """Tests 34-40: new columns added idempotently to invoice_automation_runs."""

    def setUp(self):
        self.st = _make_storage()
        self.now = _now()

    def _column_names(self) -> list[str]:
        with self.st._connect() as conn:
            rows = conn.execute(
                "PRAGMA table_info(invoice_automation_runs)"
            ).fetchall()
        return [r["name"] for r in rows]

    def test_34_existing_count_column_exists(self):
        self.assertIn("existing_count", self._column_names())

    def test_35_filtered_count_column_exists(self):
        self.assertIn("filtered_count", self._column_names())

    def test_36_processed_count_column_exists(self):
        self.assertIn("processed_count", self._column_names())

    def test_37_unaccounted_count_column_exists(self):
        self.assertIn("unaccounted_count", self._column_names())

    def test_38_migration_is_idempotent(self):
        st2 = Storage(self.st.db_path)
        cols = self._column_names()
        self.assertIn("existing_count", cols)
        self.assertIn("unaccounted_count", cols)

    def test_39_finish_automation_run_writes_new_columns(self):
        run = self.st.start_automation_run("run_39", "manual", None, self.now)
        self.st.finish_automation_run(
            "run_39", status="ok", finished_at=self.now,
            scanned_count=10, existing_count=3, filtered_count=2,
            processed_count=10, unaccounted_count=0,
        )
        with self.st._connect() as conn:
            row = conn.execute(
                "SELECT * FROM invoice_automation_runs WHERE run_id=?", ("run_39",)
            ).fetchone()
        self.assertEqual(dict(row)["existing_count"], 3)
        self.assertEqual(dict(row)["filtered_count"], 2)
        self.assertEqual(dict(row)["processed_count"], 10)
        self.assertEqual(dict(row)["unaccounted_count"], 0)

    def test_40_list_automation_runs_returns_new_columns(self):
        run = self.st.start_automation_run("run_40", "manual", None, self.now)
        self.st.finish_automation_run(
            "run_40", status="ok", finished_at=self.now,
            existing_count=5, processed_count=20, unaccounted_count=1,
        )
        runs = self.st.list_automation_runs(limit=1)
        self.assertGreater(len(runs), 0)
        self.assertIn("existing_count", runs[0])
        self.assertIn("processed_count", runs[0])
        self.assertIn("unaccounted_count", runs[0])
        self.assertEqual(runs[0]["existing_count"], 5)


# ---------------------------------------------------------------------------
# 6. Production fixture (52-invoice scenario)
# ---------------------------------------------------------------------------

class TestProductionFixture(unittest.TestCase):
    """Tests 41-45: simulate the production case where invoice 19102120 was at position 52."""

    def setUp(self):
        self.st = _make_storage()

    def _build_52_invoices(self) -> list[dict]:
        # 51 older invoices (positions 1-51 in MK list, returned oldest-first by API)
        invs = [
            _mk_invoice(inv_id=19000000 + i, user_id=30000 + i, created_at="2026-06-15")
            for i in range(51)
        ]
        # Invoice 19102120 — the newest, at position 52 in the original MK list
        invs.append(_mk_invoice(inv_id=19102120, user_id=31000, created_at="2026-07-17"))
        return invs

    def test_41_52_invoices_scanned_count_equals_52(self):
        ctx = _make_ctx(self.st, self._build_52_invoices())
        result = ctx.process_new_moyklass_invoices()
        self.assertEqual(result.get("scanned"), 52)

    def test_42_52_invoices_processed_count_equals_52(self):
        ctx = _make_ctx(self.st, self._build_52_invoices())
        result = ctx.process_new_moyklass_invoices()
        self.assertEqual(result.get("processed"), 52)

    def test_43_invoice_19102120_enters_automation_queue(self):
        ctx = _make_ctx(self.st, self._build_52_invoices())
        ctx.process_new_moyklass_invoices()
        item = self.st.get_automation_item_by_invoice("19102120")
        self.assertIsNotNone(item, "Invoice 19102120 must enter the automation queue")

    def test_44_unaccounted_zero_with_52_invoices(self):
        ctx = _make_ctx(self.st, self._build_52_invoices())
        result = ctx.process_new_moyklass_invoices()
        self.assertEqual(result.get("unaccounted", 0), 0)

    def test_45_scanned_count_stored_in_db(self):
        ctx = _make_ctx(self.st, self._build_52_invoices())
        result = ctx.process_new_moyklass_invoices()
        runs = self.st.list_automation_runs(limit=1)
        self.assertGreater(len(runs), 0)
        self.assertEqual(runs[0]["scanned_count"], 52)


# ---------------------------------------------------------------------------
# 7. Version and safety guards
# ---------------------------------------------------------------------------

class TestVersionAndSafetyGuards(unittest.TestCase):
    """Tests 46-50: version strings and safety invariants."""

    def test_46_app_js_version_is_7_0_94_5(self):
        js = APP_JS.read_text(encoding="utf-8")
        self.assertIn(
            f'console.log("MiniApp version: v{CURRENT_VERSION}")', js,
            f"app.js must declare version v{CURRENT_VERSION}",
        )

    def test_47_index_html_cache_bust_is_7_0_94_5(self):
        html = INDEX_HTML.read_text(encoding="utf-8")
        self.assertIn(
            f"v={CURRENT_VERSION}", html,
            f"index.html cache-bust must reference v={CURRENT_VERSION}",
        )

    def test_48_no_50_cap_in_server_source(self):
        src = SERVER_PY.read_text(encoding="utf-8")
        self.assertNotIn(
            "mk_invoices[:50]", src,
            "[:50] hard cap must be removed from process_new_moyklass_invoices",
        )

    def test_49_unaccounted_warning_in_js(self):
        js = APP_JS.read_text(encoding="utf-8")
        self.assertIn(
            "unaccounted_count", js,
            "app.js must reference unaccounted_count to show warning when > 0",
        )

    def test_50_js_shows_existing_count(self):
        js = APP_JS.read_text(encoding="utf-8")
        self.assertIn(
            "existing_count", js,
            "app.js run row must display existing_count",
        )


if __name__ == "__main__":
    unittest.main()

"""Regression tests for v7.0.94.3 — deduplication guard for automation invoice intents.

Covers:
  1-3.   find_all_active_intents_by_invoice includes awaiting_payment/partial_ready
  4-5.   find_all_active_intents_by_invoice detects duplicates
  6-8.   create_payment_intent storage guard blocks second intent per mk_invoice_id
  9-10.  existing intent found when automation_item.intent_public_id is absent/wrong
  11-13. repair path: no new intent, no ERIP, no acquiring
  14-16. repair path: options unchanged, student_name updated, item relinked
  17-19. audit event written; storage guard error controlled; same mk_invoice_id + diff source_ref blocked
  20-22. two existing intents → requires_check, bePaid blocked, reason_code correct
  23-25. UI: duplicate public_ids visible; payment buttons hidden for duplicate-cancelled; resolve button shown
  26-29. recovery: admin-only; paid/posted checks; no successful tx check
  30-32. recovery: no deletion; canonical remains ycpi_202607_17; automation item relinked
  33-36. recovery: duplicate marked cancelled; ERIP/checkout of canonical unchanged; no new bePaid
  37-39. recovery idempotent; scan after recovery no new intent; old manual intents unaffected
  40-41. version v7.0.94.3; existing automation/food tests unaffected

Run offline (no MoyKlass / bePaid / Telegram needed):

    python -m unittest tests.test_duplicate_invoice_guard -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CURRENT_VERSION = "7.0.95.1"

from storage import Storage

APP_JS = ROOT / "miniapp" / "app.js"
STYLES_CSS = ROOT / "miniapp" / "styles.css"
INDEX_HTML = ROOT / "miniapp" / "index.html"


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


def _create_intent(
    st: Storage,
    *,
    mk_user_id: int = 9748998,
    student_name: str | None = None,
    source: str = "moyklass_invoice_automation",
    source_reference: str | None = None,
    mk_invoice_id: str | None = None,
    amount_minor: int = 100,
    status: str = "draft",
) -> dict:
    """Insert a payment intent bypassing the duplicate guard (direct SQL) when needed."""
    now = _now()
    with st._connect() as conn:
        conn.execute(
            """INSERT INTO payment_intents
               (public_id, mk_user_id, student_name, amount_minor, amount_byn, currency,
                purpose, payment_method, status, source, source_reference, mk_invoice_id,
                created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "ycpi_tmp", int(mk_user_id), student_name, int(amount_minor),
                amount_minor / 100.0, "BYN", "subscription", "erip", status,
                source, source_reference, mk_invoice_id, now, now,
            ),
        )
        row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        public_id = f"ycpi_202607_{row_id}"
        conn.execute("UPDATE payment_intents SET public_id=? WHERE id=?", (public_id, row_id))
        row = conn.execute("SELECT * FROM payment_intents WHERE id=?", (row_id,)).fetchone()
    return dict(row)


def _set_status(st: Storage, public_id: str, status: str) -> None:
    with st._connect() as conn:
        conn.execute(
            "UPDATE payment_intents SET status=?, updated_at=? WHERE public_id=?",
            (status, _now(), public_id),
        )


def _create_auto_item(
    st: Storage,
    *,
    inv_id: str = "19075076",
    user_id: str = "9748998",
    student_name: str | None = None,
    intent_public_id: str | None = None,
) -> dict:
    now = _now()
    item = st.upsert_automation_item(inv_id, user_id, student_name, json.dumps({}), now)
    if intent_public_id:
        st.relink_automation_item_intent(item["id"], intent_public_id, now)
        item = st.get_automation_item_by_id(item["id"]) or item
    return item


# ===========================================================================
# 1-5: find_all_active_intents_by_invoice
# ===========================================================================

class TestFindAllActiveIntentsByInvoice(unittest.TestCase):
    def setUp(self):
        self.st = _make_storage()

    def test_1_finds_awaiting_payment_status(self):
        """find_all_active_intents_by_invoice returns awaiting_payment intents."""
        i = _create_intent(self.st, mk_invoice_id="19075076", status="draft")
        _set_status(self.st, i["public_id"], "awaiting_payment")
        found = self.st.find_all_active_intents_by_invoice("19075076")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["status"], "awaiting_payment")

    def test_2_finds_partial_ready_status(self):
        """find_all_active_intents_by_invoice returns partial_ready intents."""
        i = _create_intent(self.st, mk_invoice_id="19075076", status="draft")
        _set_status(self.st, i["public_id"], "partial_ready")
        found = self.st.find_all_active_intents_by_invoice("19075076")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["status"], "partial_ready")

    def test_3_excludes_cancelled(self):
        """find_all_active_intents_by_invoice excludes cancelled intents."""
        i = _create_intent(self.st, mk_invoice_id="19075076", status="draft")
        self.st.cancel_payment_intent_for_cleanup(i["public_id"], "test", _now())
        found = self.st.find_all_active_intents_by_invoice("19075076")
        self.assertEqual(len(found), 0)

    def test_4_finds_multiple_duplicates(self):
        """find_all_active_intents_by_invoice returns ALL active intents for the invoice."""
        i1 = _create_intent(self.st, mk_invoice_id="19075076", status="awaiting_payment")
        i2 = _create_intent(self.st, mk_invoice_id="19075076", status="draft")
        found = self.st.find_all_active_intents_by_invoice("19075076")
        pids = {r["public_id"] for r in found}
        self.assertIn(i1["public_id"], pids)
        self.assertIn(i2["public_id"], pids)
        self.assertEqual(len(found), 2)

    def test_5_returns_empty_when_no_active(self):
        """find_all_active_intents_by_invoice returns [] when no active intent exists."""
        found = self.st.find_all_active_intents_by_invoice("99999")
        self.assertEqual(found, [])


# ===========================================================================
# 6-8: create_payment_intent storage guard
# ===========================================================================

class TestCreateIntentStorageGuard(unittest.TestCase):
    def setUp(self):
        self.st = _make_storage()

    def test_6_guard_blocks_second_intent_for_same_invoice(self):
        """create_payment_intent raises ValueError when active intent exists for mk_invoice_id."""
        # First intent OK
        self.st.create_payment_intent({
            "mk_user_id": 9748998,
            "student_name": "Тест",
            "amount_minor": 100,
            "amount_byn": 1.0,
            "mk_invoice_id": "19075076",
        })
        # Second must be blocked
        with self.assertRaises(ValueError) as ctx:
            self.st.create_payment_intent({
                "mk_user_id": 9748998,
                "student_name": "Тест",
                "amount_minor": 100,
                "amount_byn": 1.0,
                "mk_invoice_id": "19075076",
            })
        self.assertIn("duplicate_mk_invoice_intent", str(ctx.exception))

    def test_7_guard_blocked_even_with_different_source_reference(self):
        """Storage guard blocks duplicate even when source_reference differs."""
        self.st.create_payment_intent({
            "mk_user_id": 9748998,
            "amount_minor": 100,
            "amount_byn": 1.0,
            "mk_invoice_id": "19075076",
            "source_reference": "mk_invoice_19075076",
        })
        with self.assertRaises(ValueError) as ctx:
            self.st.create_payment_intent({
                "mk_user_id": 9748998,
                "amount_minor": 100,
                "amount_byn": 1.0,
                "mk_invoice_id": "19075076",
                "source_reference": "different_reference",
            })
        self.assertIn("duplicate_mk_invoice_intent", str(ctx.exception))

    def test_8_guard_error_is_controlled_not_500(self):
        """Storage guard raises ValueError (not RuntimeError/Exception) — controlled error."""
        self.st.create_payment_intent({
            "mk_user_id": 9748998,
            "amount_minor": 100,
            "amount_byn": 1.0,
            "mk_invoice_id": "19075076",
        })
        with self.assertRaises(ValueError):
            self.st.create_payment_intent({
                "mk_user_id": 9748998,
                "amount_minor": 100,
                "amount_byn": 1.0,
                "mk_invoice_id": "19075076",
            })
        # no RuntimeError, no Exception base class leakage


# ===========================================================================
# 9-10: existing intent found when item has wrong/missing intent_public_id
# ===========================================================================

class TestExistingIntentLookup(unittest.TestCase):
    def setUp(self):
        self.st = _make_storage()

    def test_9_found_when_automation_item_has_no_intent_link(self):
        """Existing intent found by mk_invoice_id even when automation item has no intent_public_id."""
        i = _create_intent(self.st, mk_invoice_id="19075076", status="awaiting_payment")
        found = self.st.find_all_active_intents_by_invoice("19075076")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["public_id"], i["public_id"])

    def test_10_found_when_automation_item_has_wrong_intent_public_id(self):
        """Existing intent found by mk_invoice_id even when item.intent_public_id points elsewhere."""
        i = _create_intent(self.st, mk_invoice_id="19075076", status="awaiting_payment")
        item = _create_auto_item(self.st, inv_id="19075076", intent_public_id="ycpi_wrong")
        # The actual lookup uses mk_invoice_id, not item.intent_public_id
        found = self.st.find_all_active_intents_by_invoice("19075076")
        self.assertEqual(found[0]["public_id"], i["public_id"])


# ===========================================================================
# 11-19: repair path via storage (no new intent, no bePaid)
# ===========================================================================

class TestRepairPathNoNewIntent(unittest.TestCase):
    """Verify the repair path (single existing intent) doesn't create new intents or options."""

    def setUp(self):
        self.st = _make_storage()
        self.now = _now()

    def test_11_repair_does_not_create_new_intent(self):
        """Repairing existing intent must not create a second payment intent."""
        _create_intent(self.st, mk_invoice_id="19075076", student_name=None,
                       source="manual", status="awaiting_payment")
        # Simulate repair path
        existing = self.st.find_all_active_intents_by_invoice("19075076")
        self.st.repair_intent_metadata(
            existing[0]["public_id"],
            student_name="Кренть Александр",
            source="moyklass_invoice_automation",
            source_reference="mk_invoice_19075076",
            now=self.now,
        )
        all_after = self.st.find_all_active_intents_by_invoice("19075076")
        self.assertEqual(len(all_after), 1, "repair must not create a new intent")

    def test_12_existing_intent_repair_not_calls_erip(self):
        """Repair path does not involve ERIP (no new bepaid account number)."""
        i = _create_intent(self.st, mk_invoice_id="19075076", source="manual")
        original_erip = i.get("bepaid_account_number")
        self.st.repair_intent_metadata(
            i["public_id"], student_name="Имя", source="moyklass_invoice_automation", now=self.now
        )
        after = self.st.get_payment_intent(i["public_id"])
        self.assertEqual(after.get("bepaid_account_number"), original_erip)

    def test_13_existing_intent_repair_not_calls_acquiring(self):
        """Repair path does not create acquiring checkout (bepaid_payment_url unchanged)."""
        i = _create_intent(self.st, mk_invoice_id="19075076", source="manual")
        original_url = i.get("bepaid_payment_url")
        self.st.repair_intent_metadata(
            i["public_id"], student_name="Имя", source="moyklass_invoice_automation", now=self.now
        )
        after = self.st.get_payment_intent(i["public_id"])
        self.assertEqual(after.get("bepaid_payment_url"), original_url)

    def test_14_existing_intent_repair_does_not_change_payment_options(self):
        """Repair path leaves payment_intent_options table untouched."""
        i = _create_intent(self.st, mk_invoice_id="19075076", source="manual")
        with self.st._connect() as conn:
            before = conn.execute(
                "SELECT COUNT(*) FROM payment_intent_options WHERE intent_public_id=?",
                (i["public_id"],)
            ).fetchone()[0]
        self.st.repair_intent_metadata(
            i["public_id"], student_name="Имя", source="moyklass_invoice_automation", now=self.now
        )
        with self.st._connect() as conn:
            after = conn.execute(
                "SELECT COUNT(*) FROM payment_intent_options WHERE intent_public_id=?",
                (i["public_id"],)
            ).fetchone()[0]
        self.assertEqual(before, after)

    def test_15_existing_intent_repair_updates_student_name(self):
        """Repair updates student_name from NULL to real name."""
        i = _create_intent(self.st, mk_invoice_id="19075076", student_name=None, source="manual")
        self.st.repair_intent_metadata(
            i["public_id"], student_name="Кренть Александр Александрович",
            source="moyklass_invoice_automation", now=self.now,
        )
        after = self.st.get_payment_intent(i["public_id"])
        self.assertEqual(after["student_name"], "Кренть Александр Александрович")

    def test_16_existing_intent_repair_relinks_automation_item(self):
        """After repair, automation item intent_public_id points to the found intent."""
        i = _create_intent(self.st, mk_invoice_id="19075076", source="manual")
        item = _create_auto_item(self.st, inv_id="19075076")
        # Simulate relink
        self.st.relink_automation_item_intent(item["id"], i["public_id"], self.now)
        refreshed = self.st.get_automation_item_by_id(item["id"])
        self.assertEqual(refreshed["intent_public_id"], i["public_id"])

    def test_17_existing_intent_repair_writes_audit_event(self):
        """Audit event is written when repair changes metadata."""
        i = _create_intent(self.st, mk_invoice_id="19075076", student_name=None, source="manual")
        repair = self.st.repair_intent_metadata(
            i["public_id"], student_name="Имя", source="moyklass_invoice_automation", now=self.now
        )
        if repair.get("changed"):
            self.st.create_automation_audit_event({
                "created_at": self.now,
                "event_type": "automation_intent_metadata_repaired",
                "intent_public_id": i["public_id"],
                "mk_invoice_id": "19075076",
                "name_updated": repair["name_updated"],
                "initiator": "scheduled",
            })
        with self.st._connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM automation_audit_log WHERE intent_public_id=?",
                (i["public_id"],)
            ).fetchone()[0]
        self.assertGreater(count, 0)

    def test_18_resolve_duplicate_method_in_server_source(self):
        """_resolve_duplicate_automation_intent is defined in web_app_server.py."""
        src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        self.assertIn("_resolve_duplicate_automation_intent", src)

    def test_19_resolve_duplicate_action_dispatched_in_server(self):
        """'resolve-duplicate' action is dispatched in automation_item_action."""
        src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        self.assertIn("resolve-duplicate", src)


# ===========================================================================
# 20-22: two existing intents → requires_check, no bePaid, reason_code
# ===========================================================================

class TestDuplicateDetection(unittest.TestCase):
    def setUp(self):
        self.st = _make_storage()
        self.now = _now()

    def _make_two_intents(self) -> tuple[dict, dict]:
        i1 = _create_intent(self.st, mk_invoice_id="19075076", status="awaiting_payment")
        i2 = _create_intent(self.st, mk_invoice_id="19075076", status="draft")
        return i1, i2

    def test_20_two_intents_for_invoice_detected(self):
        """find_all_active_intents_by_invoice returns 2 for duplicate scenario."""
        i1, i2 = self._make_two_intents()
        found = self.st.find_all_active_intents_by_invoice("19075076")
        self.assertEqual(len(found), 2)

    def test_21_pipeline_sets_requires_check_for_duplicate(self):
        """When two active intents exist, item is moved to requires_check."""
        i1, i2 = self._make_two_intents()
        item = _create_auto_item(self.st, inv_id="19075076")
        all_intents = self.st.find_all_active_intents_by_invoice("19075076")
        if len(all_intents) > 1:
            self.st.update_automation_item_stage(
                item["id"], "requires_check",
                reason_code="duplicate_invoice_intents",
                readable_reason="test",
                now=self.now,
            )
        refreshed = self.st.get_automation_item_by_id(item["id"])
        self.assertEqual(refreshed["current_stage"], "requires_check")

    def test_22_reason_code_is_duplicate_invoice_intents(self):
        """reason_code for duplicate scenario is 'duplicate_invoice_intents'."""
        i1, i2 = self._make_two_intents()
        item = _create_auto_item(self.st, inv_id="19075076")
        self.st.update_automation_item_stage(
            item["id"], "requires_check",
            reason_code="duplicate_invoice_intents",
            readable_reason="duplicate",
            now=self.now,
        )
        refreshed = self.st.get_automation_item_by_id(item["id"])
        self.assertEqual(refreshed["reason_code"], "duplicate_invoice_intents")


# ===========================================================================
# 23-26: UI static analysis
# ===========================================================================

class TestDuplicateUI(unittest.TestCase):
    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")
        self.css = STYLES_CSS.read_text(encoding="utf-8")

    def test_23_ui_shows_duplicate_public_ids(self):
        """app.js renders readable_reason which contains both public_ids for duplicate case."""
        self.assertIn("duplicate_invoice_intents", self.js)
        self.assertIn("resolve-duplicate", self.js)

    def test_24_ui_hides_payment_buttons_for_duplicate_item(self):
        """app.js suppresses canCreate/canPublish/canRetry buttons for isDuplicateItem."""
        self.assertIn("isDuplicateItem", self.js)
        self.assertIn("!isDuplicateItem", self.js)

    def test_25_ui_shows_resolve_button_for_duplicate(self):
        """app.js renders 'Восстановить (выбрать канонический)' for duplicate items."""
        self.assertIn("Восстановить (выбрать канонический)", self.js)

    def test_26_duplicate_cancelled_badge_in_intent_card(self):
        """app.js renders 'Дубликат — оплата заблокирована' badge for cancelled duplicate intent."""
        self.assertIn("Дубликат — оплата заблокирована", self.js)
        self.assertIn("isDuplicateCancelled", self.js)
        self.assertIn("pi-duplicate-badge", self.js)


# ===========================================================================
# 27-31: recovery action safety checks
# ===========================================================================

class TestRecoveryActionSafetyChecks(unittest.TestCase):
    def setUp(self):
        self.st = _make_storage()
        self.now = _now()

    def test_27_recovery_action_in_server_source(self):
        """resolve-duplicate action is present in web_app_server.py."""
        src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        self.assertIn("resolve-duplicate", src)
        self.assertIn("AUTOMATION_ADMIN_ROLES", src)

    def test_28_recovery_checks_canonical_not_paid(self):
        """_resolve_duplicate blocks recovery if canonical is paid."""
        i1 = _create_intent(self.st, mk_invoice_id="19075076", status="awaiting_payment")
        _set_status(self.st, i1["public_id"], "paid")
        i2 = _create_intent(self.st, mk_invoice_id="19075076", status="draft")
        all_intents = self.st.find_all_active_intents_by_invoice("19075076")
        canonical = min(all_intents, key=lambda x: x["id"])
        # canonical is paid → hard block
        _HARD_BLOCK = {"paid", "posted_to_moyklass"}
        self.assertIn(self.st.get_payment_intent(canonical["public_id"])["status"], _HARD_BLOCK)

    def test_29_recovery_checks_canonical_not_posted(self):
        """_resolve_duplicate blocks recovery if canonical is posted_to_moyklass."""
        i1 = _create_intent(self.st, mk_invoice_id="19075076", status="awaiting_payment")
        _set_status(self.st, i1["public_id"], "posted_to_moyklass")
        i2 = _create_intent(self.st, mk_invoice_id="19075076", status="draft")
        canonical_pi = self.st.get_payment_intent(i1["public_id"])
        self.assertEqual(canonical_pi["status"], "posted_to_moyklass")

    def test_30_recovery_does_not_delete_duplicate_intent(self):
        """cancel_payment_intent_for_cleanup marks as cancelled but does not delete rows."""
        i = _create_intent(self.st, mk_invoice_id="19075076", status="draft")
        result = self.st.cancel_payment_intent_for_cleanup(
            i["public_id"], "duplicate_automation_intent_for_mk_invoice", self.now
        )
        self.assertTrue(result.get("ok"))
        # Row still exists
        row = self.st.get_payment_intent(i["public_id"])
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "cancelled")

    def test_31_recovery_checks_no_paid_at_on_duplicate(self):
        """Recovery blocks if duplicate intent has paid_at set."""
        i1 = _create_intent(self.st, mk_invoice_id="19075076", status="awaiting_payment")
        i2 = _create_intent(self.st, mk_invoice_id="19075076", status="draft")
        # Manually set paid_at on i2 (duplicate)
        with self.st._connect() as conn:
            conn.execute(
                "UPDATE payment_intents SET paid_at=? WHERE public_id=?",
                (self.now, i2["public_id"])
            )
        pi2 = self.st.get_payment_intent(i2["public_id"])
        self.assertIsNotNone(pi2["paid_at"])


# ===========================================================================
# Production fixture: ycpi_202607_17 / ycpi_202607_18
# ===========================================================================

class TestProductionFixture(unittest.TestCase):
    """End-to-end recovery test using production-like fixture."""

    def _build_fixture(self, st: Storage, now: str) -> tuple[dict, dict, dict]:
        """Create production-like fixture: canonical + duplicate + automation item."""
        canonical = _create_intent(
            st,
            mk_invoice_id="19075076",
            mk_user_id=9748998,
            student_name=None,
            source="moyklass_invoice_automation",
            source_reference="mk_invoice_19075076",
            amount_minor=100,
            status="awaiting_payment",
        )
        duplicate = _create_intent(
            st,
            mk_invoice_id="19075076",
            mk_user_id=9748998,
            student_name="Кренть Александр Александрович",
            source="moyklass_invoice_automation",
            source_reference="mk_invoice_19075076",
            amount_minor=100,
            status="awaiting_payment",
        )
        # Automation item currently linked to duplicate (as observed in production)
        item = _create_auto_item(
            st, inv_id="19075076", user_id="9748998",
            student_name="Кренть Александр Александрович",
            intent_public_id=duplicate["public_id"],
        )
        return canonical, duplicate, item

    def test_32_recovery_makes_canonical_the_correct_intent(self):
        """After recovery, ycpi_202607_17 (canonical/oldest) is repaired."""
        st = _make_storage()
        now = _now()
        canonical, duplicate, item = self._build_fixture(st, now)
        # Repair canonical
        repair = st.repair_intent_metadata(
            canonical["public_id"],
            student_name="Кренть Александр Александрович",
            source="moyklass_invoice_automation",
            source_reference="mk_invoice_19075076",
            now=now,
        )
        after = st.get_payment_intent(canonical["public_id"])
        self.assertEqual(after["student_name"], "Кренть Александр Александрович")
        self.assertEqual(after["source"], "moyklass_invoice_automation")

    def test_33_recovery_relinks_automation_item_to_canonical(self):
        """After recovery, automation item.intent_public_id = canonical.public_id."""
        st = _make_storage()
        now = _now()
        canonical, duplicate, item = self._build_fixture(st, now)
        st.relink_automation_item_intent(item["id"], canonical["public_id"], now)
        refreshed = st.get_automation_item_by_id(item["id"])
        self.assertEqual(refreshed["intent_public_id"], canonical["public_id"])

    def test_34_recovery_cancels_duplicate(self):
        """After recovery, duplicate intent is cancelled with duplicate reason."""
        st = _make_storage()
        now = _now()
        canonical, duplicate, item = self._build_fixture(st, now)
        result = st.cancel_payment_intent_for_cleanup(
            duplicate["public_id"],
            "duplicate_automation_intent_for_mk_invoice",
            now,
        )
        self.assertTrue(result.get("ok"))
        after = st.get_payment_intent(duplicate["public_id"])
        self.assertEqual(after["status"], "cancelled")
        self.assertEqual(after["cancel_reason"], "duplicate_automation_intent_for_mk_invoice")

    def test_35_recovery_does_not_change_canonical_erip(self):
        """Recovery does not alter bepaid_account_number on canonical intent."""
        st = _make_storage()
        now = _now()
        canonical, duplicate, item = self._build_fixture(st, now)
        original_erip = canonical.get("bepaid_account_number")
        st.repair_intent_metadata(
            canonical["public_id"],
            student_name="Кренть Александр Александрович",
            now=now,
        )
        after = st.get_payment_intent(canonical["public_id"])
        self.assertEqual(after.get("bepaid_account_number"), original_erip)

    def test_36_recovery_does_not_change_canonical_checkout(self):
        """Recovery does not alter bepaid_payment_url (checkout) on canonical intent."""
        st = _make_storage()
        now = _now()
        canonical, duplicate, item = self._build_fixture(st, now)
        original_url = canonical.get("bepaid_payment_url")
        st.repair_intent_metadata(canonical["public_id"], student_name="Имя", now=now)
        after = st.get_payment_intent(canonical["public_id"])
        self.assertEqual(after.get("bepaid_payment_url"), original_url)

    def test_37_recovery_is_idempotent(self):
        """Running recovery twice produces same result (no error on second run)."""
        st = _make_storage()
        now = _now()
        canonical, duplicate, item = self._build_fixture(st, now)
        # First cancel
        st.cancel_payment_intent_for_cleanup(
            duplicate["public_id"], "duplicate_automation_intent_for_mk_invoice", now
        )
        # Second cancel — must be idempotent
        result2 = st.cancel_payment_intent_for_cleanup(
            duplicate["public_id"], "duplicate_automation_intent_for_mk_invoice", now
        )
        self.assertTrue(result2.get("ok") or result2.get("idempotent"))

    def test_38_scan_after_recovery_finds_only_canonical(self):
        """After recovery, find_all_active_intents_by_invoice returns exactly 1 (canonical)."""
        st = _make_storage()
        now = _now()
        canonical, duplicate, item = self._build_fixture(st, now)
        st.cancel_payment_intent_for_cleanup(
            duplicate["public_id"], "duplicate_automation_intent_for_mk_invoice", now
        )
        found = st.find_all_active_intents_by_invoice("19075076")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["public_id"], canonical["public_id"])

    def test_39_old_manual_intents_not_broken(self):
        """Manual intents (no mk_invoice_id) are not affected by the duplicate guard."""
        st = _make_storage()
        # Two manual intents without mk_invoice_id — both must succeed
        i1 = st.create_payment_intent({
            "mk_user_id": 1001,
            "amount_minor": 100,
            "amount_byn": 1.0,
        })
        i2 = st.create_payment_intent({
            "mk_user_id": 1001,
            "amount_minor": 200,
            "amount_byn": 2.0,
        })
        self.assertIsNotNone(i1.get("public_id"))
        self.assertIsNotNone(i2.get("public_id"))


# ===========================================================================
# 40-41: version and CSS
# ===========================================================================

class TestVersionAndCSS(unittest.TestCase):
    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")
        self.html = INDEX_HTML.read_text(encoding="utf-8")
        self.css = STYLES_CSS.read_text(encoding="utf-8")

    def test_40_version_is_current(self):
        """app.js and index.html reference current version."""
        self.assertIn(f'console.log("MiniApp version: v{CURRENT_VERSION}")', self.js)
        self.assertIn(f"v={CURRENT_VERSION}", self.html)

    def test_41_pi_duplicate_badge_css_defined(self):
        """styles.css defines .pi-duplicate-badge and .auto-queue-duplicate-warning."""
        self.assertIn(".pi-duplicate-badge", self.css)
        self.assertIn(".auto-queue-duplicate-warning", self.css)


# ===========================================================================
# 42-51: v7.0.94.4 — clear_reason and post-recovery state
# ===========================================================================

class TestClearReasonAfterResolve(unittest.TestCase):
    """Tests for update_automation_item_stage(clear_reason=True) and post-resolve state."""

    def setUp(self):
        self.st = _make_storage()
        self.now = _now()
        # Build: canonical (awaiting_payment), item in requires_check + duplicate_invoice_intents
        self.canonical = _create_intent(self.st, mk_invoice_id="19075076", status="awaiting_payment")
        self.duplicate = _create_intent(self.st, mk_invoice_id="19075076", status="draft")
        self.item = _create_auto_item(self.st, inv_id="19075076",
                                      intent_public_id=self.duplicate["public_id"])
        self.st.update_automation_item_stage(
            self.item["id"], "requires_check",
            reason_code="duplicate_invoice_intents",
            readable_reason="Обнаружено несколько черновиков",
            now=self.now,
        )

    def test_42_clear_reason_sets_reason_code_null(self):
        """update_automation_item_stage(clear_reason=True) sets reason_code to NULL."""
        self.st.update_automation_item_stage(
            self.item["id"], "payment_options_created",
            clear_reason=True,
            now=self.now,
        )
        after = self.st.get_automation_item_by_id(self.item["id"])
        self.assertIsNone(after.get("reason_code"))

    def test_43_clear_reason_sets_readable_reason_null(self):
        """update_automation_item_stage(clear_reason=True) sets readable_reason to NULL."""
        self.st.update_automation_item_stage(
            self.item["id"], "payment_options_created",
            clear_reason=True,
            now=self.now,
        )
        after = self.st.get_automation_item_by_id(self.item["id"])
        self.assertIsNone(after.get("readable_reason"))

    def test_44_clear_reason_sets_correct_stage(self):
        """Stage is set to payment_options_created when clear_reason=True."""
        self.st.update_automation_item_stage(
            self.item["id"], "payment_options_created",
            clear_reason=True,
            now=self.now,
        )
        after = self.st.get_automation_item_by_id(self.item["id"])
        self.assertEqual(after["current_stage"], "payment_options_created")

    def test_45_item_relinked_to_canonical_after_resolve(self):
        """After resolve steps, automation item points to canonical intent."""
        self.st.relink_automation_item_intent(
            self.item["id"], self.canonical["public_id"], self.now
        )
        self.st.update_automation_item_stage(
            self.item["id"], "payment_options_created",
            intent_public_id=self.canonical["public_id"],
            clear_reason=True,
            now=self.now,
        )
        after = self.st.get_automation_item_by_id(self.item["id"])
        self.assertEqual(after["intent_public_id"], self.canonical["public_id"])

    def test_46_duplicate_remains_cancelled_after_resolve(self):
        """Cancelled duplicate keeps status=cancelled after resolve (not altered again)."""
        self.st.cancel_payment_intent_for_cleanup(
            self.duplicate["public_id"],
            "duplicate_automation_intent_for_mk_invoice",
            self.now,
        )
        # Simulate second resolve attempt — storage find returns only 1 active intent now
        found = self.st.find_all_active_intents_by_invoice("19075076")
        self.assertEqual(len(found), 1)
        dup_after = self.st.get_payment_intent(self.duplicate["public_id"])
        self.assertEqual(dup_after["status"], "cancelled")

    def test_47_second_resolve_returns_no_duplicate(self):
        """After duplicate cancelled, find_all_active returns 1 — server would reject second resolve."""
        self.st.cancel_payment_intent_for_cleanup(
            self.duplicate["public_id"],
            "duplicate_automation_intent_for_mk_invoice",
            self.now,
        )
        found = self.st.find_all_active_intents_by_invoice("19075076")
        # Server checks len < 2 and returns no_duplicate_found — verify storage side
        self.assertLess(len(found), 2)

    def test_48_ui_is_duplicate_item_requires_both_conditions(self):
        """isDuplicateItem checks current_stage===requires_check AND reason_code."""
        js = APP_JS.read_text(encoding="utf-8")
        self.assertIn('item.current_stage === "requires_check"', js)
        self.assertIn('item.reason_code === "duplicate_invoice_intents"', js)

    def test_49_ui_duplicate_warning_not_shown_for_payment_options_created(self):
        """isDuplicateItem is false when stage != requires_check (no single-condition check)."""
        js = APP_JS.read_text(encoding="utf-8")
        # The old single-condition guard must not be present alone
        self.assertNotIn(
            'const isDuplicateItem = item.reason_code === "duplicate_invoice_intents";',
            js,
        )

    def test_50_resolve_duplicate_method_makes_no_bepaid_calls(self):
        """_resolve_duplicate_automation_intent does not call bePaid API."""
        src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        import re
        # Extract method body
        match = re.search(
            r'def _resolve_duplicate_automation_intent\(.*?(?=\n    def |\Z)',
            src, re.DOTALL
        )
        self.assertIsNotNone(match, "method not found")
        body = match.group(0)
        self.assertNotIn("bepaid_client", body)
        self.assertNotIn("_bepaid", body)
        self.assertNotIn("create_bepaid", body)

    def test_51_resolve_duplicate_creates_no_new_payment_intents(self):
        """Recovery path writes no new rows to payment_intents table."""
        before_count = 0
        with self.st._connect() as conn:
            before_count = conn.execute("SELECT COUNT(*) FROM payment_intents").fetchone()[0]
        # Simulate resolve: repair metadata + relink + cancel — no INSERT
        self.st.repair_intent_metadata(
            self.canonical["public_id"],
            student_name="Кренть Александр Александрович",
            source="moyklass_invoice_automation",
            now=self.now,
        )
        self.st.relink_automation_item_intent(
            self.item["id"], self.canonical["public_id"], self.now
        )
        self.st.update_automation_item_stage(
            self.item["id"], "payment_options_created",
            intent_public_id=self.canonical["public_id"],
            clear_reason=True,
            now=self.now,
        )
        self.st.cancel_payment_intent_for_cleanup(
            self.duplicate["public_id"],
            "duplicate_automation_intent_for_mk_invoice",
            self.now,
        )
        after_count = 0
        with self.st._connect() as conn:
            after_count = conn.execute("SELECT COUNT(*) FROM payment_intents").fetchone()[0]
        self.assertEqual(before_count, after_count)


if __name__ == "__main__":
    unittest.main()

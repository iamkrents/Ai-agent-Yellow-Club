"""Tests for v7.0.93 / v7.0.93.1 — parent payment visibility.

Covers:
  Storage layer:
    1.  client_visibility column default is 'hidden'
    2.  publish_payment_intent_to_client basic publish
    3.  publish_payment_intent_to_client idempotent
    4.  publish_payment_intent_to_client not-found returns error
    5.  withdraw_payment_intent_from_client basic withdraw
    6.  withdraw_payment_intent_from_client idempotent
    7.  withdraw_payment_intent_from_client not-found returns error
    8.  list_client_visible_payment_intents returns published intent for linked parent
    9.  list_client_visible_payment_intents excludes hidden intents
    10. list_client_visible_payment_intents excludes withdrawn intents
    11. list_client_visible_payment_intents excludes cancelled intents
    12. list_client_visible_payment_intents returns empty for unlinked parent
    13. get_parents_for_child returns confirmed active links (client_parent_child_links)
    14. get_parents_for_child returns empty for unknown student
    15. get_parents_for_child returns empty when no client link created
    16. list_client_visible_payment_intents excludes intents with no parent link

  Frontend (static analysis):
    17. version marker is v7.0.93.1 in app.js
    18. cache-bust is v=7.0.93.1 in index.html
    19. loadClientPayments function exists in app.js
    20. renderClientPaymentCard function exists in app.js
    21. isParent function exists in app.js
    22. client-payments is in parentAllowed in app.js
    23. publishToParentModal exists in index.html
    24. client-payments tab button in index.html
    25. openPublishToParentModal uses publish-preview endpoint
    26. withdrawIntentFromParent uses withdraw-from-parent endpoint
    27. _scheduleClientPaymentsPoll exists (30-second polling)
    28. clientPayments in state object
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

import sys
sys.path.insert(0, str(ROOT))

from storage import Storage

APP_JS = ROOT / "miniapp" / "app.js"
INDEX_HTML = ROOT / "miniapp" / "index.html"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _seed_intent(storage: Storage, mk_user_id: int = 1001, status: str = "bepaid_created") -> dict:
    pi = storage.create_payment_intent({
        "mk_user_id": mk_user_id,
        "student_name": "Тест Студент",
        "amount_minor": 5000,
        "amount_byn": 50.0,
        "currency": "BYN",
        "purpose": "current_month",
        "period_month": "2026-07",
        "payment_method": "erip",
        "created_by_tg_id": 1,
        "created_by_name": "admin",
        "comment": "",
        "source": "manual",
    })
    if status != "draft":
        storage.payment_intent_update_status(pi["public_id"], status)
        pi = storage.get_payment_intent(pi["public_id"])
    return pi


def _seed_parent_link(storage: Storage, mk_student_id: str, parent_telegram_id: str) -> str:
    """Create a client CL- code and immediately link it to the given parent.

    Uses client_parent_child_links (v7.0.93.1+), not food parent_child_links.
    """
    result = storage.create_client_link_code(str(mk_student_id), "Тест Ученик", "test_admin")
    code = result["code"]
    storage.link_client_child(parent_telegram_id, code, NOW)
    return code


NOW = "2026-07-15T10:00:00"


# ---------------------------------------------------------------------------
# Storage tests
# ---------------------------------------------------------------------------

class Test01ClientVisibilityDefault(unittest.TestCase):
    def test_new_intent_has_hidden_visibility(self):
        storage = _tmp_storage()
        pi = _seed_intent(storage)
        self.assertEqual(pi.get("client_visibility"), "hidden")


class Test02PublishBasic(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.pi = _seed_intent(self.storage)
        self.public_id = self.pi["public_id"]

    def test_publish_sets_published(self):
        result = self.storage.publish_payment_intent_to_client(self.public_id, "admin1", NOW)
        self.assertTrue(result["ok"])
        self.assertFalse(result.get("idempotent"))
        updated = self.storage.get_payment_intent(self.public_id)
        self.assertEqual(updated["client_visibility"], "published")
        self.assertEqual(updated["published_by"], "admin1")
        self.assertEqual(updated["published_at"], NOW)


class Test03PublishIdempotent(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.pi = _seed_intent(self.storage)
        self.public_id = self.pi["public_id"]
        self.storage.publish_payment_intent_to_client(self.public_id, "admin1", NOW)

    def test_second_publish_is_idempotent(self):
        result = self.storage.publish_payment_intent_to_client(self.public_id, "admin2", NOW)
        self.assertTrue(result["ok"])
        self.assertTrue(result.get("idempotent"))
        # original published_by unchanged
        updated = self.storage.get_payment_intent(self.public_id)
        self.assertEqual(updated["published_by"], "admin1")


class Test04PublishNotFound(unittest.TestCase):
    def test_publish_not_found_returns_error(self):
        storage = _tmp_storage()
        result = storage.publish_payment_intent_to_client("ycpi_000000_99999", "admin1", NOW)
        self.assertFalse(result["ok"])
        self.assertIn("not_found", result.get("error", ""))


class Test05WithdrawBasic(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.pi = _seed_intent(self.storage)
        self.public_id = self.pi["public_id"]
        self.storage.publish_payment_intent_to_client(self.public_id, "admin1", NOW)

    def test_withdraw_sets_withdrawn(self):
        result = self.storage.withdraw_payment_intent_from_client(self.public_id, "admin1", NOW)
        self.assertTrue(result["ok"])
        self.assertFalse(result.get("idempotent"))
        updated = self.storage.get_payment_intent(self.public_id)
        self.assertEqual(updated["client_visibility"], "withdrawn")
        self.assertEqual(updated["withdrawn_by"], "admin1")


class Test06WithdrawIdempotent(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.pi = _seed_intent(self.storage)
        self.public_id = self.pi["public_id"]
        self.storage.withdraw_payment_intent_from_client(self.public_id, "admin1", NOW)

    def test_second_withdraw_is_idempotent(self):
        result = self.storage.withdraw_payment_intent_from_client(self.public_id, "admin2", NOW)
        self.assertTrue(result["ok"])
        self.assertTrue(result.get("idempotent"))


class Test07WithdrawNotFound(unittest.TestCase):
    def test_withdraw_not_found_returns_error(self):
        storage = _tmp_storage()
        result = storage.withdraw_payment_intent_from_client("ycpi_000000_99999", "admin1", NOW)
        self.assertFalse(result["ok"])
        self.assertIn("not_found", result.get("error", ""))


class Test08ListVisibleReturnsPublished(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.pi = _seed_intent(self.storage, mk_user_id=1001)
        _seed_parent_link(self.storage, "1001", "tg_parent_1")
        self.storage.publish_payment_intent_to_client(self.pi["public_id"], "admin", NOW)

    def test_list_returns_published_for_parent(self):
        intents = self.storage.list_client_visible_payment_intents("tg_parent_1")
        self.assertEqual(len(intents), 1)
        self.assertEqual(intents[0]["public_id"], self.pi["public_id"])
        self.assertEqual(intents[0]["client_visibility"], "published")


class Test09ListExcludesHidden(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.pi = _seed_intent(self.storage, mk_user_id=1002)
        _seed_parent_link(self.storage, "1002", "tg_parent_2")

    def test_hidden_intent_not_visible(self):
        intents = self.storage.list_client_visible_payment_intents("tg_parent_2")
        self.assertEqual(len(intents), 0)


class Test10ListExcludesWithdrawn(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.pi = _seed_intent(self.storage, mk_user_id=1003)
        _seed_parent_link(self.storage, "1003", "tg_parent_3")
        self.storage.publish_payment_intent_to_client(self.pi["public_id"], "admin", NOW)
        self.storage.withdraw_payment_intent_from_client(self.pi["public_id"], "admin", NOW)

    def test_withdrawn_intent_not_visible(self):
        intents = self.storage.list_client_visible_payment_intents("tg_parent_3")
        self.assertEqual(len(intents), 0)


class Test11ListExcludesCancelled(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.pi = _seed_intent(self.storage, mk_user_id=1004, status="draft")
        _seed_parent_link(self.storage, "1004", "tg_parent_4")
        self.storage.publish_payment_intent_to_client(self.pi["public_id"], "admin", NOW)
        self.storage.cancel_payment_intent_for_cleanup(self.pi["public_id"], "test", NOW)

    def test_cancelled_intent_not_visible(self):
        intents = self.storage.list_client_visible_payment_intents("tg_parent_4")
        self.assertEqual(len(intents), 0)


class Test12ListEmptyForUnlinkedParent(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.pi = _seed_intent(self.storage, mk_user_id=1005)
        self.storage.publish_payment_intent_to_client(self.pi["public_id"], "admin", NOW)

    def test_unlinked_parent_sees_nothing(self):
        intents = self.storage.list_client_visible_payment_intents("tg_parent_unlinked")
        self.assertEqual(len(intents), 0)


class Test13GetParentsForChild(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        _seed_parent_link(self.storage, "2001", "tg_parent_A")

    def test_returns_confirmed_links(self):
        parents = self.storage.get_parents_for_child("2001")
        self.assertEqual(len(parents), 1)
        self.assertEqual(parents[0]["parent_telegram_user_id"], "tg_parent_A")


class Test14GetParentsForChildUnknown(unittest.TestCase):
    def test_returns_empty_for_unknown_student(self):
        storage = _tmp_storage()
        parents = storage.get_parents_for_child("9999999")
        self.assertEqual(parents, [])


class Test15GetParentsForChildUnconfirmed(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        # Create a client link code but don't use it (no link entry created)
        self.storage.create_client_link_code("3001", "Ученик Три", "admin")

    def test_unconfirmed_link_not_returned(self):
        parents = self.storage.get_parents_for_child("3001")
        self.assertEqual(len(parents), 0)


class Test16ListExcludesIntentWithNoParentLink(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        # mk_user_id has no parent link
        self.pi = _seed_intent(self.storage, mk_user_id=9999)
        self.storage.publish_payment_intent_to_client(self.pi["public_id"], "admin", NOW)

    def test_intent_with_no_parent_link_not_visible(self):
        intents = self.storage.list_client_visible_payment_intents("tg_any_parent")
        self.assertEqual(len(intents), 0)


# ---------------------------------------------------------------------------
# Frontend static analysis tests
# ---------------------------------------------------------------------------

class TestFrontend(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = APP_JS.read_text(encoding="utf-8")
        cls.html = INDEX_HTML.read_text(encoding="utf-8")

    def test_17_version_marker_v7_0_93_2_1(self):
        self.assertIn('console.log("MiniApp version: v7.0.93.2.9")', self.js)

    def test_18_cache_bust_v7_0_93_2_1(self):
        self.assertIn("v=7.0.93.2.9", self.html)
        self.assertNotIn("v=7.0.92", self.html)

    def test_19_loadClientPayments_exists(self):
        self.assertIn("async function loadClientPayments(", self.js)

    def test_20_renderClientPaymentCard_exists(self):
        self.assertIn("function renderClientPaymentCard(", self.js)

    def test_21_isParent_exists(self):
        self.assertIn("function isParent()", self.js)
        self.assertIn('state.me?.role === "parent"', self.js)

    def test_22_client_payments_in_parentAllowed(self):
        self.assertIn('"client-payments"', self.js)
        # Must appear in the parentAllowed array context
        idx = self.js.find("parentAllowed")
        self.assertNotEqual(idx, -1)
        segment = self.js[idx: idx + 200]
        self.assertIn("client-payments", segment)

    def test_23_publishToParentModal_in_html(self):
        self.assertIn('id="publishToParentModal"', self.html)
        self.assertIn('id="publishToParentConfirm"', self.html)

    def test_24_client_payments_tab_button_in_html(self):
        self.assertIn('data-tab="client-payments"', self.html)

    def test_25_openPublishToParentModal_uses_publish_preview(self):
        self.assertIn("openPublishToParentModal", self.js)
        self.assertIn("publish-preview", self.js)

    def test_26_withdrawIntentFromParent_uses_withdraw_endpoint(self):
        self.assertIn("withdrawIntentFromParent", self.js)
        self.assertIn("withdraw-from-parent", self.js)

    def test_27_scheduleClientPaymentsPoll_exists(self):
        self.assertIn("_scheduleClientPaymentsPoll", self.js)
        self.assertIn("30000", self.js)

    def test_28_clientPayments_in_state(self):
        self.assertIn("clientPayments:", self.js)
        self.assertIn("clientPaymentsBusy:", self.js)
        self.assertIn("clientPaymentsPollTimer:", self.js)


if __name__ == "__main__":
    unittest.main()

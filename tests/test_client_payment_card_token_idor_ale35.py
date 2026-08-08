"""Tests for ALE-35 — IDOR fix on GET /api/client/payments/{public_id}/card-token.

Audit finding (from the ALE-34 launch-readiness audit): client_payment_
card_token (web_app_server.py) fetched the payment_intents row directly
via storage.get_payment_intent(public_id) — a plain, UNSCOPED lookup by
primary key — with no check that the intent's mk_user_id actually belongs
to the calling parent (contrast with the sibling client_payments_list,
which correctly scopes through storage.list_client_visible_payment_
intents(parent_telegram_id)). Any authenticated parent who knew or
guessed another family's public_id could receive a live bePaid acquiring
checkout URL/token for that other family's invoice.

Fixed by adding the SAME ownership-check idiom already used by
_require_availability_access (MiniAppContext.client_continuation_status_*
/ client_schedule_availability_*, ALE-34): re-derive the caller's own
children from storage.list_client_children_for_parent (client_parent_
child_links), never trust the public_id path parameter's mk_user_id
alone. A denied request returns the exact same generic "Счёт не найден."
used for a genuinely nonexistent public_id — never a distinct message
that would let a caller distinguish "exists, not yours" from "does not
exist" (payment/financial data — a stricter non-enumeration bar than the
child-ownership message _require_availability_access already uses).

No storage method changed, no new table, no change to card-token
generation/payment automation/MoyKlass/invoice semantics — only an
ownership gate added before any of the endpoint's existing detail checks.

Run offline:
    python -m unittest tests.test_client_payment_card_token_idor_ale35 -v
"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage
from web_app_server import MiniAppContext
from utils import now_iso

OWNER_UID = 900001
ADMIN_UID = 900002
CLIENT_MANAGER_UID = 900003
TEACHER_UID = 900004
PARENT_A_UID = 700001
PARENT_B_UID = 700002


def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _make_ctx(storage: Storage) -> MiniAppContext:
    ctx = object.__new__(MiniAppContext)
    ctx.storage = storage
    ctx.settings = types.SimpleNamespace(
        bot_username="yellowclubagent_bot", telegram_bot_token="test-secret",
        admin_ids=[OWNER_UID], senior_teacher_ids=[], web_app_test_roles=False,
        client_food_entry_visible=True, food_module_enabled=True,
        client_cabinet_v7113_enabled=False, client_cabinet_v7113_pilot_telegram_ids=[],
        client_notifications_enabled=False, client_notifications_pilot_telegram_ids=[],
    )
    ctx._role_store: dict[int, str] = {ADMIN_UID: "admin", CLIENT_MANAGER_UID: "client_manager", TEACHER_UID: "teacher"}
    ctx._role_for_user = lambda uid: ("owner" if int(uid) == OWNER_UID else ctx._role_store.get(int(uid), "parent" if int(uid) >= 700000 else "other"))
    ctx.moyklass = types.SimpleNamespace(request=lambda *a, **k: types.SimpleNamespace(ok=False, data=None))
    return ctx


def _auth(uid: int) -> dict:
    return {"user_id": uid}


def _cl_link(storage: Storage, mk_user_id, child_name, parent_tid, staff_uid="1") -> dict:
    code = storage.create_client_link_code(mk_user_id, child_name, staff_uid)
    assert code["ok"], code
    r = storage.link_client_child(str(parent_tid), code["code"], now_iso())
    assert r["ok"], r
    return r


def _food_link(storage: Storage, mk_student_id, full_name, parent_tid) -> None:
    with storage._connect() as conn:
        conn.execute(
            "INSERT INTO camp_children (created_at, updated_at, mk_student_id, full_name, active) VALUES (?, ?, ?, ?, 1)",
            (now_iso(), now_iso(), mk_student_id, full_name),
        )
        conn.execute(
            "INSERT INTO parent_child_links (created_at, confirmed_at, parent_telegram_id, mk_student_id, link_code, active) VALUES (?, ?, ?, ?, ?, 1)",
            (now_iso(), now_iso(), str(parent_tid), mk_student_id, f"YC-{mk_student_id}"),
        )


def _seed_intent(storage: Storage, public_id: str, mk_user_id: str, *, status: str = "ready",
                  client_visibility: str = "published", student_name: str = "Kid") -> None:
    with storage._connect() as conn:
        conn.execute(
            """INSERT INTO payment_intents
               (public_id, mk_user_id, student_name, amount_minor, amount_byn, purpose,
                status, client_visibility, created_at, updated_at)
               VALUES (?, ?, ?, 13500, 135.0, 'other', ?, ?, ?, ?)""",
            (public_id, mk_user_id, student_name, status, client_visibility, now_iso(), now_iso()),
        )


def _seed_intent_with_valid_acquiring_option(storage: Storage, public_id: str, mk_user_id: str) -> None:
    """Same as _seed_intent, but also pre-seeds an already-valid, non-
    expired acquiring option — so a successful card-token call hits the
    existing-option fast path (web_app_server.py client_payment_card_
    token, "renewed": False branch) instead of trying to create a real
    one via payment_intent_create_acquiring_option, which needs real
    bePaid credentials this test environment deliberately never has."""
    _seed_intent(storage, public_id, mk_user_id)
    intent = storage.get_payment_intent(public_id)
    with storage._connect() as conn:
        conn.execute(
            """INSERT INTO payment_intent_options
               (payment_intent_id, intent_public_id, channel, shop_type, status,
                payment_url, checkout_token, expires_at, created_at, updated_at)
               VALUES (?, ?, 'acquiring', 'acq', 'created', ?, ?, '2099-01-01T00:00:00Z', ?, ?)""",
            (intent["id"], public_id, f"https://pay.example/{public_id}", f"tok_{public_id}", now_iso(), now_iso()),
        )


class Base(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)


# ── proves the vulnerability existed / now proves it's closed ─────────────
class TestOwnershipEnforced(Base):
    def test_own_payment_allowed(self):
        _cl_link(self.storage, "50001", "Kid A", PARENT_A_UID)
        _seed_intent_with_valid_acquiring_option(self.storage, "pi_A1", "50001")
        r = self.ctx.client_payment_card_token(_auth(PARENT_A_UID), "pi_A1")
        self.assertTrue(r["ok"], r)
        self.assertIn("payment_url", r)
        self.assertIn("checkout_token", r)

    def test_foreign_payment_denied_idor(self):
        # THE core regression this file exists to lock in: client A must
        # never be able to fetch client B's checkout token/url.
        _cl_link(self.storage, "50002", "Kid A", PARENT_A_UID)
        _cl_link(self.storage, "50003", "Kid B", PARENT_B_UID)
        _seed_intent(self.storage, "pi_B1", "50003")
        r = self.ctx.client_payment_card_token(_auth(PARENT_A_UID), "pi_B1")
        self.assertFalse(r["ok"], r)
        self.assertNotIn("payment_url", r)
        self.assertNotIn("checkout_token", r)

    def test_denial_message_indistinguishable_from_nonexistent(self):
        # non-enumeration: "exists but not yours" and "does not exist at
        # all" must produce byte-identical responses.
        _cl_link(self.storage, "50004", "Kid A", PARENT_A_UID)
        _cl_link(self.storage, "50005", "Kid B", PARENT_B_UID)
        _seed_intent(self.storage, "pi_B2", "50005")
        foreign = self.ctx.client_payment_card_token(_auth(PARENT_A_UID), "pi_B2")
        missing = self.ctx.client_payment_card_token(_auth(PARENT_A_UID), "pi_does_not_exist")
        self.assertEqual(foreign, missing)

    def test_nonexistent_public_id(self):
        _cl_link(self.storage, "50006", "Kid A", PARENT_A_UID)
        r = self.ctx.client_payment_card_token(_auth(PARENT_A_UID), "pi_totally_made_up")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "Счёт не найден.")

    def test_invalid_public_id_format(self):
        _cl_link(self.storage, "50007", "Kid A", PARENT_A_UID)
        for bogus in ("", "  ", "'; DROP TABLE payment_intents; --", "../../etc/passwd"):
            r = self.ctx.client_payment_card_token(_auth(PARENT_A_UID), bogus)
            self.assertFalse(r["ok"], (bogus, r))

    def test_parent_with_zero_linked_children_denied(self):
        # a parent role with literally no client_parent_child_links row at
        # all (e.g. a food-only client, or a brand new /me caller) must be
        # denied exactly like anyone else with no ownership match.
        _cl_link(self.storage, "50008", "Kid B", PARENT_B_UID)
        _seed_intent(self.storage, "pi_B3", "50008")
        r = self.ctx.client_payment_card_token(_auth(PARENT_A_UID), "pi_B3")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "Счёт не найден.")


class TestClientKinds(Base):
    def test_regular_client_own_payment(self):
        _cl_link(self.storage, "50010", "Kid", PARENT_A_UID)
        _seed_intent_with_valid_acquiring_option(self.storage, "pi_reg1", "50010")
        r = self.ctx.client_payment_card_token(_auth(PARENT_A_UID), "pi_reg1")
        self.assertTrue(r["ok"], r)

    def test_combined_client_own_payment_still_works(self):
        # combined = has BOTH a client link and a food link; the food link
        # must be irrelevant to payment ownership either way.
        _cl_link(self.storage, "50011", "Kid", PARENT_A_UID)
        _food_link(self.storage, "food-50011", "Kid", PARENT_A_UID)
        _seed_intent_with_valid_acquiring_option(self.storage, "pi_comb1", "50011")
        r = self.ctx.client_payment_card_token(_auth(PARENT_A_UID), "pi_comb1")
        self.assertTrue(r["ok"], r)

    def test_food_only_client_has_no_payment_access(self):
        # a food-only parent (food link only, no client_parent_child_links
        # row at all) never had legitimate card-token access before this
        # fix either — confirms the fix does not newly grant anything, it
        # only closes the leak for OTHER real parents' payments.
        _food_link(self.storage, "food-50012", "Kid", PARENT_A_UID)
        _cl_link(self.storage, "50012b", "Other Kid", PARENT_B_UID)
        _seed_intent(self.storage, "pi_food1", "50012b")
        r = self.ctx.client_payment_card_token(_auth(PARENT_A_UID), "pi_food1")
        self.assertFalse(r["ok"])


class TestRolesAndAuth(Base):
    def test_client_manager_denied(self):
        # client_payment_card_token is parent-only by design (role != "parent"
        # is rejected before any ownership logic even runs) — client_manager
        # has no legitimate reason to fetch a live checkout token this way.
        _cl_link(self.storage, "50020", "Kid", PARENT_A_UID)
        _seed_intent(self.storage, "pi_cm1", "50020")
        r = self.ctx.client_payment_card_token(_auth(CLIENT_MANAGER_UID), "pi_cm1")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "Доступ только для родителей.")

    def test_owner_denied_outside_test_mode(self):
        _cl_link(self.storage, "50021", "Kid", PARENT_A_UID)
        _seed_intent(self.storage, "pi_own1", "50021")
        r = self.ctx.client_payment_card_token(_auth(OWNER_UID), "pi_own1")
        self.assertFalse(r["ok"])

    def test_admin_denied(self):
        _cl_link(self.storage, "50022", "Kid", PARENT_A_UID)
        _seed_intent(self.storage, "pi_adm1", "50022")
        r = self.ctx.client_payment_card_token(_auth(ADMIN_UID), "pi_adm1")
        self.assertFalse(r["ok"])

    def test_teacher_denied(self):
        _cl_link(self.storage, "50023", "Kid", PARENT_A_UID)
        _seed_intent(self.storage, "pi_teach1", "50023")
        r = self.ctx.client_payment_card_token(_auth(TEACHER_UID), "pi_teach1")
        self.assertFalse(r["ok"])

    def test_unauthenticated_like_caller_with_no_role(self):
        # a Telegram id with no staff role, no linked children, and below
        # the parent-role numeric convention used by this test harness —
        # resolves to a role _require_availability_access-style checks
        # would reject outright.
        unknown_uid = 1
        ctx2 = _make_ctx(self.storage)
        ctx2._role_for_user = lambda uid: ""
        _seed_intent(self.storage, "pi_anon1", "99999")
        r = ctx2.client_payment_card_token(_auth(unknown_uid), "pi_anon1")
        self.assertFalse(r["ok"])


class TestExistingBusinessRulesUnaffected(Base):
    """These detail checks (published/withdrawn/paid/confirmed) already
    existed and must still fire correctly for the OWNER of the intent —
    confirms the new ownership gate doesn't interfere with them."""

    def test_own_unpublished_payment_still_rejected(self):
        _cl_link(self.storage, "50030", "Kid", PARENT_A_UID)
        _seed_intent(self.storage, "pi_unpub1", "50030", client_visibility="draft")
        r = self.ctx.client_payment_card_token(_auth(PARENT_A_UID), "pi_unpub1")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "Счёт недоступен.")

    def test_own_already_paid_payment_still_rejected(self):
        _cl_link(self.storage, "50031", "Kid", PARENT_A_UID)
        _seed_intent(self.storage, "pi_paid1", "50031", status="paid")
        r = self.ctx.client_payment_card_token(_auth(PARENT_A_UID), "pi_paid1")
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"], "Счёт уже оплачен.")


class TestRegressionClientPaymentsList(Base):
    def test_client_payments_list_unaffected(self):
        _cl_link(self.storage, "50040", "Kid", PARENT_A_UID)
        _seed_intent(self.storage, "pi_list1", "50040")
        r = self.ctx.client_payments_list(_auth(PARENT_A_UID))
        self.assertTrue(r["ok"])
        self.assertEqual(len(r["payments"]), 1)
        self.assertEqual(r["payments"][0]["public_id"], "pi_list1")


class TestNoMoyKlassInFix(unittest.TestCase):
    def test_no_moyklass_reference_in_changed_function(self):
        # "posted_to_moyklass" is a pre-existing, legitimate PAYMENT STATUS
        # value checked here (set by a completely different code path once
        # a payment has already been posted) — not a MoyKlass API call.
        # Checking for a real call/client reference specifically avoids
        # that false positive.
        src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        start = src.index("def client_payment_card_token(")
        end = src.index("\n    # ── v7.0.93.1/93.2", start)
        body = src[start:end]
        self.assertNotIn("self.moyklass", body)
        self.assertNotIn("moyklass_client", body.lower())
        self.assertNotIn("post_to_moyklass(", body.lower())


if __name__ == "__main__":
    unittest.main()

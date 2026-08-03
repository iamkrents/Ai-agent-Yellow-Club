"""Tests for v7.1.15 — launch-readiness idempotency/duplicate-protection
audit ahead of connecting 464 real clients.

These tests do NOT change any linking logic (link_client_child,
activate_onboarding_invite, link_parent_to_child are all untouched this
release — see the impact map in the final report) — they exist to PROVE the
already-existing atomic-claim/already_linked-replay design actually holds,
since that is exactly the property launch readiness depends on.

Covers (Registration 1-10, Food 11-14):
  1.  CL-code creates a regular link.
  2.  Invite creates a regular link.
  3.  Repeat CL-code submission does not create a duplicate.
  4.  Repeat invite activation does not create a duplicate.
  5.  Concurrent (racing) requests for the same code never create two links.
  6.  An already-linked child opens the existing cabinet (client_kind).
  7.  A parent with two children keeps both links.
  8.  Someone else's invite/code is rejected (fails closed, no leak).
  9.  An invalid code is rejected with a stable reason_code.
  10. "Lost the HTTP response, retry" is safe (repeat call after success).
  11. Food-only stays on the old flow (parent_child_links only).
  12. Food-only never gets an automatic regular link.
  13. Combined keeps both regular + food links.
  14. The food linking function itself is untouched/still idempotent.

Run:
    python -m unittest tests.test_client_onboarding_idempotency_v7115 -v
"""
from __future__ import annotations

import sys
import tempfile
import threading
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import unittest  # noqa: E402

from storage import Storage  # noqa: E402


def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")


def _make_code(st: Storage, mk_user_id: str, name: str = "Child") -> str:
    result = st.create_client_link_code(mk_user_id, name, created_by="9001")
    assert result["ok"], result
    return result["code"]


def _make_campaign_recipient_invite(st: Storage, mk_user_id: str, name: str = "Child"):
    camp = st.create_onboarding_campaign(name="Launch", academic_year="2026-2027", created_by="9001")["campaign"]
    # Campaigns are created in 'draft'; invites require 'active'.
    with st._connect() as conn:
        conn.execute("UPDATE client_onboarding_campaigns SET status='active' WHERE id=?", (camp["id"],))
    imp = st.import_onboarding_campaign_recipients(camp["id"], [{"mk_user_id": mk_user_id, "child_display_name": name}], added_by="9001")
    assert imp["ok"], imp
    with st._connect() as conn:
        row = conn.execute(
            "SELECT id FROM client_onboarding_recipients WHERE campaign_id=? AND mk_user_id=?",
            (camp["id"], mk_user_id),
        ).fetchone()
    recipient_id = row["id"]
    invite = st.create_onboarding_invite(camp["id"], recipient_id, "9001", "test_signing_secret")
    assert invite["ok"], invite
    return camp["id"], recipient_id, invite["invite_id"], invite["signature"]


class TestClCodeIdempotency(unittest.TestCase):
    def setUp(self):
        self.st = _tmp_storage()

    def test_1_cl_code_creates_regular_link(self):
        code = _make_code(self.st, "S1001")
        result = self.st.link_client_child("700001", code, _now())
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(self.st.get_client_kind_for_parent("700001"), "regular")

    def test_3_repeat_cl_code_no_duplicate(self):
        code = _make_code(self.st, "S1002")
        first = self.st.link_client_child("700002", code, _now())
        self.assertTrue(first.get("ok"))
        second = self.st.link_client_child("700002", code, _now())
        self.assertTrue(second.get("ok"))
        self.assertTrue(second.get("already_linked"))
        with self.st._connect() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM client_parent_child_links WHERE mk_user_id='S1002' AND status='active'"
            ).fetchone()[0]
        self.assertEqual(n, 1)

    def test_5_concurrent_requests_same_code_no_duplicate(self):
        code = _make_code(self.st, "S1003")
        results = []
        lock = threading.Lock()

        def attempt(parent_id):
            r = self.st.link_client_child(parent_id, code, _now())
            with lock:
                results.append(r)

        threads = [threading.Thread(target=attempt, args=(f"70000{i}",)) for i in range(3, 5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        ok_results = [r for r in results if r.get("ok") and not r.get("already_linked")]
        self.assertEqual(len(ok_results), 1, f"exactly one racer should win a brand-new link: {results}")
        with self.st._connect() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM client_parent_child_links WHERE mk_user_id='S1003' AND status='active'"
            ).fetchone()[0]
        self.assertEqual(n, 1)

    def test_6_already_linked_child_resolves_regular_kind(self):
        code = _make_code(self.st, "S1004")
        self.st.link_client_child("700006", code, _now())
        # Re-deriving client_kind (what /api/me does) must show the cabinet
        # is already open — no second registration needed.
        self.assertEqual(self.st.get_client_kind_for_parent("700006"), "regular")

    def test_7_parent_with_two_children_keeps_both_links(self):
        code_a = _make_code(self.st, "S1005A")
        code_b = _make_code(self.st, "S1005B")
        r1 = self.st.link_client_child("700007", code_a, _now())
        r2 = self.st.link_client_child("700007", code_b, _now())
        self.assertTrue(r1.get("ok") and r2.get("ok"))
        with self.st._connect() as conn:
            rows = conn.execute(
                "SELECT mk_user_id FROM client_parent_child_links WHERE parent_telegram_user_id='700007' AND status='active'"
            ).fetchall()
        self.assertEqual({r["mk_user_id"] for r in rows}, {"S1005A", "S1005B"})

    def test_8_someone_elses_code_use_is_rejected(self):
        code = _make_code(self.st, "S1006")
        first = self.st.link_client_child("700008", code, _now())
        self.assertTrue(first.get("ok"))
        second = self.st.link_client_child("700009", code, _now())
        self.assertFalse(second.get("ok"))
        self.assertEqual(second.get("reason_code"), "code_already_used")

    def test_9_invalid_code_rejected_with_stable_reason_code(self):
        result = self.st.link_client_child("700010", "CL-ZZZZZZZZ", _now())
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("reason_code"), "code_not_found")

    def test_10_lost_response_then_retry_is_safe(self):
        code = _make_code(self.st, "S1007")
        first = self.st.link_client_child("700011", code, _now())
        self.assertTrue(first.get("ok"))
        # Simulate the client never seeing the first response and retrying.
        retry = self.st.link_client_child("700011", code, _now())
        self.assertTrue(retry.get("ok"))
        self.assertTrue(retry.get("already_linked"))
        with self.st._connect() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM client_parent_child_links WHERE mk_user_id='S1007'"
            ).fetchone()[0]
        self.assertEqual(n, 1)


class TestInviteIdempotency(unittest.TestCase):
    def setUp(self):
        self.st = _tmp_storage()

    def test_2_invite_creates_regular_link(self):
        _cid, _rid, invite_id, signature = _make_campaign_recipient_invite(self.st, "S2001")
        result = self.st.activate_onboarding_invite(invite_id, signature, "800001", "test_signing_secret")
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(self.st.get_client_kind_for_parent("800001"), "regular")

    def test_4_repeat_invite_activation_no_duplicate(self):
        _cid, _rid, invite_id, signature = _make_campaign_recipient_invite(self.st, "S2002")
        first = self.st.activate_onboarding_invite(invite_id, signature, "800002", "test_signing_secret")
        self.assertTrue(first.get("ok"))
        second = self.st.activate_onboarding_invite(invite_id, signature, "800002", "test_signing_secret")
        self.assertTrue(second.get("ok"))
        self.assertTrue(second.get("already_linked"))
        with self.st._connect() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM client_parent_child_links WHERE mk_user_id='S2002' AND status='active'"
            ).fetchone()[0]
        self.assertEqual(n, 1)

    def test_8b_other_users_invite_activation_rejected(self):
        _cid, _rid, invite_id, signature = _make_campaign_recipient_invite(self.st, "S2003")
        first = self.st.activate_onboarding_invite(invite_id, signature, "800003", "test_signing_secret")
        self.assertTrue(first.get("ok"))
        second = self.st.activate_onboarding_invite(invite_id, signature, "800004", "test_signing_secret")
        self.assertFalse(second.get("ok"))
        self.assertEqual(second.get("reason_code"), "invite_already_used")

    def test_9b_bad_signature_rejected(self):
        _cid, _rid, invite_id, _sig = _make_campaign_recipient_invite(self.st, "S2004")
        result = self.st.activate_onboarding_invite(invite_id, "forged-signature", "800005", "test_signing_secret")
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("reason_code"), "invite_not_found")


class TestFoodOnlyIsolationIdempotency(unittest.TestCase):
    def setUp(self):
        self.st = _tmp_storage()
        self.code = self.st.get_or_create_link_code_for_student("F3001")

    def test_11_food_only_stays_in_old_flow(self):
        result = self.st.link_parent_to_child("900001", self.code)
        self.assertTrue(result.get("ok"), result)
        with self.st._connect() as conn:
            has_food = conn.execute(
                "SELECT 1 FROM parent_child_links WHERE parent_telegram_id='900001' AND mk_student_id='F3001' AND active=1"
            ).fetchone()
        self.assertIsNotNone(has_food)

    def test_12_food_only_never_gets_automatic_regular_link(self):
        self.st.link_parent_to_child("900002", self.code)
        with self.st._connect() as conn:
            has_client = conn.execute(
                "SELECT 1 FROM client_parent_child_links WHERE parent_telegram_user_id='900002'"
            ).fetchone()
        self.assertIsNone(has_client)
        self.assertEqual(self.st.get_client_kind_for_parent("900002"), "food_only")

    def test_13_combined_keeps_both_regular_and_food(self):
        self.st.link_parent_to_child("900003", self.code)
        code = _make_code(self.st, "S3002")
        self.st.link_client_child("900003", code, _now())
        self.assertEqual(self.st.get_client_kind_for_parent("900003"), "combined")
        with self.st._connect() as conn:
            has_food = conn.execute("SELECT 1 FROM parent_child_links WHERE parent_telegram_id='900003'").fetchone()
            has_client = conn.execute("SELECT 1 FROM client_parent_child_links WHERE parent_telegram_user_id='900003'").fetchone()
        self.assertIsNotNone(has_food)
        self.assertIsNotNone(has_client)

    def test_14_food_link_function_itself_still_idempotent(self):
        first = self.st.link_parent_to_child("900004", self.code)
        second = self.st.link_parent_to_child("900004", self.code)
        self.assertTrue(first.get("ok") and second.get("ok"))
        self.assertTrue(second.get("already_linked"))


if __name__ == "__main__":
    unittest.main()

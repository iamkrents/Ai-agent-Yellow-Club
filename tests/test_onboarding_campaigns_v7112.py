"""Tests for v7.1.12 — mass client-onboarding campaigns and continuation tracking.

Second, parallel path alongside the existing point-in-time CL-code flow and
the v7.1.11 staff link-and-enroll endpoint — neither is replaced or modified.
A successful campaign invite activation writes into the SAME
client_parent_child_links table via the same safety invariants as
link_client_child() (atomic claim, idempotent same-parent reuse, fail-closed
on a different parent), and reuses ensure_payment_pilot_from_client_link()
unchanged for pilot enrollment.

Covers (grouped to match the v7.1.12 spec's test checklist):
  Campaign lifecycle: create, duplicate submit (idempotency_key), start,
    close, archive, invalid transitions, permission matrix, default 30-day
    expiration, custom expiration.
  Recipient import: import selected candidates, duplicate idempotent,
    filters, missing MK data tolerated, no mass automatic pilot creation.
  Continuation status: manual update, bulk update, audit/history (old/new/
    actor/comment), invalid status, permissions, parent survey response,
    manager override producing a second history entry (never silently lost).
  Invite lifecycle: secure token, hash-only storage, create, revoke,
    regenerate, expiration, closed campaign blocks new invites, one-time use,
    duplicate active invite requires explicit force, wrong Telegram user,
    already-linked-same-user idempotency, linked-to-another-user fail-closed,
    no raw token anywhere in the audit log.
  Activation: parent-child link created, pilot created (review/enabled),
    existing review/auto/observe/disabled preserved untouched, no Payment
    Intent/bePaid/publish/MK-posting side effects, survey_enabled path,
    survey_enabled=false path.
  Regression: v7.1.11 staff link-and-enroll and parent CL-flow completely
    unchanged and produce zero campaign-table rows.

Run:
    python -m unittest tests.test_onboarding_campaigns_v7112 -v
"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage, CONTINUATION_STATUSES, ONBOARDING_CAMPAIGN_TRANSITIONS
from web_app_server import MiniAppContext, CLIENT_ONBOARDING_CAMPAIGN_ROLES, PAYMENT_ONBOARDING_STAFF_ROLES


def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


SECRET = "test-bot-token-secret"  # v7.1.12.1 — matches ctx.settings.telegram_bot_token below


def _make_ctx(storage: Storage) -> MiniAppContext:
    ctx = object.__new__(MiniAppContext)
    ctx.storage = storage
    ctx.settings = types.SimpleNamespace(bot_username="yellowclubagent_bot", telegram_bot_token=SECRET)
    ctx._role_store: dict[int, str] = {}

    def _role_for_user(uid: int) -> str:
        return ctx._role_store.get(int(uid), "other")

    ctx._role_for_user = _role_for_user
    ctx.moyklass = types.SimpleNamespace(request=lambda *a, **k: types.SimpleNamespace(ok=False, data=None))
    return ctx


def _auth(uid: int, role: str, ctx: MiniAppContext) -> dict:
    ctx._role_store[uid] = role
    return {"user_id": uid}


class OnboardingTestBase(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)
        self.owner = _auth(1, "owner", self.ctx)

    def _make_campaign(self, **kw):
        kw.setdefault("name", "Продолжающие 2026/2027")
        kw.setdefault("academic_year", "2026/2027")
        r = self.ctx.onboarding_campaign_create(self.owner, kw)
        self.assertTrue(r.get("ok"), r)
        return r["campaign"]

    def _started_campaign(self, **kw):
        c = self._make_campaign(**kw)
        r = self.ctx.onboarding_campaign_start(self.owner, str(c["id"]))
        self.assertTrue(r.get("ok"), r)
        return r["campaign"]

    def _warm_verified_cache(self, mk_user_ids):
        """v7.1.12.1 hotfix #2 — onboarding_campaign_import_recipients now
        only ever imports server-verified data (cache / trusted local record
        / live MoyKlass check), never whatever a request body claims. Tests
        that exercise import() directly (rather than through a real search/
        bulk-fetch call first) simulate "this candidate was already
        server-verified this session" the same way the real UI produces it —
        by populating the same cache search/bulk-fetch populate."""
        import time as _time
        cache = self.ctx._onboarding_candidates_cache_dict()
        now = _time.time()
        for mk in mk_user_ids:
            mk = str(mk)
            cache[mk] = (now, {
                "mk_user_id": mk, "child_display_name": f"Child {mk}", "branch_name": "", "course_name": "",
            })

    def _import(self, campaign_id, mk_user_ids):
        self._warm_verified_cache(mk_user_ids)
        recs = [{"mk_user_id": mk, "child_display_name": f"Child {mk}"} for mk in mk_user_ids]
        r = self.ctx.onboarding_campaign_import_recipients(self.owner, str(campaign_id), {"recipients": recs})
        self.assertTrue(r.get("ok"), r)
        return r

    def _activate(self, combined_token: str, parent_tid: str):
        """v7.1.12.1 — combined_token is "<invite_id>_<signature>", the exact
        payload shape parsed out of an invite_link's start= query param."""
        invite_id_str, _sep, signature = combined_token.partition("_")
        return self.storage.activate_onboarding_invite(int(invite_id_str), signature, parent_tid, SECRET)


# ─────────────────────────────────────────────────────────────────────────────
# Campaign lifecycle
# ─────────────────────────────────────────────────────────────────────────────

class TestCampaignLifecycle(OnboardingTestBase):
    def test_create(self):
        c = self._make_campaign()
        self.assertEqual(c["status"], "draft")
        self.assertEqual(c["invite_ttl_days"], 30)
        self.assertEqual(c["default_payment_mode"], "review")
        self.assertTrue(c["auto_enroll_payments"])
        self.assertFalse(c["survey_enabled"])

    def test_default_30_day_expiration(self):
        c = self._make_campaign()
        self.assertEqual(c["invite_ttl_days"], 30)

    def test_custom_expiration_presets(self):
        for days in (7, 14, 20, 30):
            c = self._make_campaign(name=f"C{days}", invite_ttl_days=days)
            self.assertEqual(c["invite_ttl_days"], days)

    def test_custom_expiration_out_of_bounds_rejected(self):
        r = self.ctx.onboarding_campaign_create(self.owner, {"name": "Bad", "academic_year": "x", "invite_ttl_days": 999})
        self.assertFalse(r.get("ok"))

    def test_duplicate_submit_idempotent(self):
        key = "idem-key-1"
        r1 = self.ctx.onboarding_campaign_create(self.owner, {"name": "A", "academic_year": "y", "idempotency_key": key})
        r2 = self.ctx.onboarding_campaign_create(self.owner, {"name": "A duplicate click", "academic_year": "y", "idempotency_key": key})
        self.assertTrue(r1.get("ok") and r2.get("ok"))
        self.assertEqual(r1["campaign"]["id"], r2["campaign"]["id"])
        campaigns = self.storage.list_onboarding_campaigns()
        self.assertEqual(len(campaigns), 1)

    def test_no_idempotency_key_allows_two_campaigns(self):
        self.ctx.onboarding_campaign_create(self.owner, {"name": "A", "academic_year": "y"})
        self.ctx.onboarding_campaign_create(self.owner, {"name": "B", "academic_year": "y"})
        self.assertEqual(len(self.storage.list_onboarding_campaigns()), 2)

    def test_start_close_archive_happy_path(self):
        c = self._make_campaign()
        s = self.ctx.onboarding_campaign_start(self.owner, str(c["id"]))
        self.assertTrue(s["ok"]); self.assertEqual(s["campaign"]["status"], "active")
        self.assertIsNotNone(s["campaign"]["started_at"])
        cl = self.ctx.onboarding_campaign_close(self.owner, str(c["id"]))
        self.assertTrue(cl["ok"]); self.assertEqual(cl["campaign"]["status"], "completed")
        ar = self.ctx.onboarding_campaign_archive(self.owner, str(c["id"]))
        self.assertTrue(ar["ok"]); self.assertEqual(ar["campaign"]["status"], "archived")

    def test_invalid_transitions_blocked(self):
        c = self._make_campaign()
        # draft -> completed directly is invalid
        r = self.ctx.onboarding_campaign_close(self.owner, str(c["id"]))
        self.assertFalse(r["ok"]); self.assertEqual(r.get("reason_code"), "invalid_transition")
        # archived -> anything is invalid
        self.ctx.onboarding_campaign_archive(self.owner, str(c["id"]))
        r2 = self.ctx.onboarding_campaign_start(self.owner, str(c["id"]))
        self.assertFalse(r2["ok"]); self.assertEqual(r2.get("reason_code"), "invalid_transition")

    def test_transition_table_matches_spec_states(self):
        self.assertEqual(set(ONBOARDING_CAMPAIGN_TRANSITIONS.keys()), {"draft", "active", "completed", "archived"})
        self.assertEqual(ONBOARDING_CAMPAIGN_TRANSITIONS["archived"], set())

    def test_permission_matrix_create(self):
        for role in ("owner", "admin", "client_manager"):
            auth = _auth(100 + hash(role) % 1000, role, self.ctx)
            r = self.ctx.onboarding_campaign_create(auth, {"name": f"by-{role}", "academic_year": "y"})
            self.assertTrue(r.get("ok"), f"{role} should be allowed: {r}")
        for role in ("operations", "teacher", "methodist", "intern", "kitchen", "restaurant", "parent"):
            auth = _auth(200 + hash(role) % 1000, role, self.ctx)
            r = self.ctx.onboarding_campaign_create(auth, {"name": f"by-{role}", "academic_year": "y"})
            self.assertFalse(r.get("ok"), f"{role} should be denied")

    def test_permission_matrix_matches_backend_constant(self):
        self.assertEqual(CLIENT_ONBOARDING_CAMPAIGN_ROLES, {"owner", "admin", "client_manager"})
        # v7.1.12 deliberately mirrors the same set as v7.1.11 staff onboarding.
        self.assertEqual(CLIENT_ONBOARDING_CAMPAIGN_ROLES, PAYMENT_ONBOARDING_STAFF_ROLES)

    def test_list_and_get(self):
        c = self._make_campaign()
        lst = self.ctx.onboarding_campaigns_list(self.owner, {})
        self.assertTrue(lst["ok"]); self.assertEqual(len(lst["campaigns"]), 1)
        self.assertIn("stats", lst["campaigns"][0])
        got = self.ctx.onboarding_campaign_get(self.owner, str(c["id"]), {})
        self.assertTrue(got["ok"]); self.assertEqual(got["campaign"]["id"], c["id"])

    def test_get_nonexistent_campaign(self):
        r = self.ctx.onboarding_campaign_get(self.owner, "999999", {})
        self.assertFalse(r["ok"])


# ─────────────────────────────────────────────────────────────────────────────
# Recipient import
# ─────────────────────────────────────────────────────────────────────────────

class TestRecipientImport(OnboardingTestBase):
    def test_import_selected(self):
        c = self._make_campaign()
        r = self._import(c["id"], ["1001", "1002"])
        self.assertEqual(r["added"], 2)
        self.assertEqual(r["already_present"], 0)

    def test_duplicate_recipient_idempotent(self):
        c = self._make_campaign()
        self._import(c["id"], ["1001"])
        r2 = self.ctx.onboarding_campaign_import_recipients(self.owner, str(c["id"]), {
            "recipients": [{"mk_user_id": "1001", "child_display_name": "Dup"}]
        })
        self.assertTrue(r2["ok"]); self.assertEqual(r2["added"], 0); self.assertEqual(r2["already_present"], 1)
        recipients = self.storage.list_onboarding_campaign_recipients(c["id"])
        self.assertEqual(len(recipients), 1)

    def test_filters(self):
        c = self._started_campaign()
        self._import(c["id"], ["2001", "2002"])
        recs = self.storage.list_onboarding_campaign_recipients(c["id"])
        self.storage.update_recipient_continuation_status(recs[0]["id"], "continues", "1", "owner")
        got_continues = self.ctx.onboarding_campaign_get(self.owner, str(c["id"]), {"continuation_status": "continues"})
        self.assertEqual(len(got_continues["recipients"]), 1)
        got_all = self.ctx.onboarding_campaign_get(self.owner, str(c["id"]), {})
        self.assertEqual(len(got_all["recipients"]), 2)

    def test_already_linked_client_shown_not_blocked(self):
        from utils import now_iso
        c = self._started_campaign()
        # Link 3001 via the existing generic parent CL flow first.
        code = self.storage.create_client_link_code("3001", "Kid", "1")["code"]
        self.storage.link_client_child("777", code, now_iso())
        self._import(c["id"], ["3001"])
        recs = self.storage.list_onboarding_campaign_recipients(c["id"])
        self.assertTrue(recs[0]["telegram_connected"])

    def test_existing_pilot_shown_not_blocked(self):
        c = self._started_campaign()
        self.storage.upsert_pilot_client("4001", mode="auto")
        self._import(c["id"], ["4001"])
        recs = self.storage.list_onboarding_campaign_recipients(c["id"])
        self.assertTrue(recs[0]["in_pilot"])
        self.assertEqual(recs[0]["pilot_mode"], "auto")

    def test_missing_mk_data_tolerated(self):
        c = self._started_campaign()
        self._warm_verified_cache(["5001"])
        r = self.ctx.onboarding_campaign_import_recipients(self.owner, str(c["id"]), {
            "recipients": [{"mk_user_id": "5001"}]  # no display name, no branch
        })
        self.assertTrue(r["ok"]); self.assertEqual(r["added"], 1)

    def test_no_mass_automatic_pilot_creation_during_import(self):
        c = self._started_campaign()
        self._import(c["id"], ["6001", "6002"])
        self.assertEqual(len(self.storage.list_pilot_clients()), 0)

    def test_import_requires_draft_or_active_campaign(self):
        c = self._started_campaign()
        self.ctx.onboarding_campaign_close(self.owner, str(c["id"]))
        r = self.ctx.onboarding_campaign_import_recipients(self.owner, str(c["id"]), {
            "recipients": [{"mk_user_id": "7001"}]
        })
        self.assertFalse(r["ok"])

    def test_import_permission_denied_for_operations(self):
        c = self._make_campaign()
        ops = _auth(50, "operations", self.ctx)
        r = self.ctx.onboarding_campaign_import_recipients(ops, str(c["id"]), {"recipients": [{"mk_user_id": "1"}]})
        self.assertFalse(r["ok"])


# ─────────────────────────────────────────────────────────────────────────────
# Continuation status
# ─────────────────────────────────────────────────────────────────────────────

class TestContinuationStatus(OnboardingTestBase):
    def setUp(self):
        super().setUp()
        self.campaign = self._started_campaign()
        self._import(self.campaign["id"], ["8001", "8002", "8003"])
        self.recipients = self.storage.list_onboarding_campaign_recipients(self.campaign["id"])

    def test_manual_update(self):
        rid = self.recipients[0]["id"]
        r = self.ctx.onboarding_campaign_continuation_status(self.owner, str(self.campaign["id"]), {
            "recipient_id": rid, "status": "continues", "comment": "звонили родителю",
        })
        self.assertTrue(r["ok"]); self.assertEqual(r["new_status"], "continues")
        self.assertEqual(r["old_status"], "unknown")

    def test_bulk_update(self):
        ids = [r["id"] for r in self.recipients]
        r = self.ctx.onboarding_campaign_continuation_status(self.owner, str(self.campaign["id"]), {
            "recipient_ids": ids, "status": "needs_consultation",
        })
        self.assertTrue(r["ok"]); self.assertEqual(r["updated"], 3)
        for rec in self.storage.list_onboarding_campaign_recipients(self.campaign["id"]):
            self.assertEqual(rec["continuation_status"], "needs_consultation")

    def test_audit_history_fields(self):
        rid = self.recipients[0]["id"]
        self.ctx.onboarding_campaign_continuation_status(self.owner, str(self.campaign["id"]), {
            "recipient_id": rid, "status": "not_continuing", "comment": "переезд",
        })
        events = self.storage.list_onboarding_audit_events(self.campaign["id"])
        ev = next(e for e in events if e["event_type"] == "continuation_status_changed")
        self.assertEqual(ev["old_status"], "unknown")
        self.assertEqual(ev["new_status"], "not_continuing")
        self.assertEqual(ev["actor_telegram_user_id"], "1")
        self.assertIn("переезд", ev["note"])

    def test_invalid_status_rejected(self):
        rid = self.recipients[0]["id"]
        r = self.ctx.onboarding_campaign_continuation_status(self.owner, str(self.campaign["id"]), {
            "recipient_id": rid, "status": "bogus_status",
        })
        self.assertFalse(r["ok"])
        self.assertEqual(set(CONTINUATION_STATUSES), {"unknown", "continues", "undecided", "needs_consultation", "not_continuing"})

    def test_permissions(self):
        rid = self.recipients[0]["id"]
        ops = _auth(60, "operations", self.ctx)
        r = self.ctx.onboarding_campaign_continuation_status(ops, str(self.campaign["id"]), {
            "recipient_id": rid, "status": "continues",
        })
        self.assertFalse(r["ok"])

    def test_cross_campaign_recipient_rejected(self):
        other = self._make_campaign(name="Other")
        rid = self.recipients[0]["id"]
        r = self.ctx.onboarding_campaign_continuation_status(self.owner, str(other["id"]), {
            "recipient_id": rid, "status": "continues",
        })
        self.assertFalse(r["ok"])

    def test_parent_survey_response(self):
        rid = self.recipients[0]["id"]
        result = self.storage.submit_continuation_response(self.campaign["id"], rid, "999", "continues", comment="ок")
        self.assertTrue(result["ok"])
        self.assertEqual(result["recipient"]["continuation_status"], "continues")
        responses = None
        with self.storage._connect() as conn:
            responses = conn.execute("SELECT * FROM client_continuation_responses WHERE recipient_id=?", (rid,)).fetchall()
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0]["source"], "parent_app")

    def test_manager_override_after_survey_produces_second_history_entry(self):
        rid = self.recipients[0]["id"]
        self.storage.submit_continuation_response(self.campaign["id"], rid, "999", "undecided")
        self.ctx.onboarding_campaign_continuation_status(self.owner, str(self.campaign["id"]), {
            "recipient_id": rid, "status": "continues", "comment": "перезвонили, подтвердили",
        })
        events = [e for e in self.storage.list_onboarding_audit_events(self.campaign["id"])
                  if e["event_type"] == "continuation_status_changed" and e["recipient_id"] == rid]
        self.assertEqual(len(events), 2)  # neither change silently overwrote the other — both are on record
        rec = self.storage.get_onboarding_recipient(rid)
        self.assertEqual(rec["continuation_status"], "continues")  # manager's change is authoritative-current


# ─────────────────────────────────────────────────────────────────────────────
# Invite lifecycle
# ─────────────────────────────────────────────────────────────────────────────

class TestInviteLifecycle(OnboardingTestBase):
    def setUp(self):
        super().setUp()
        self.campaign = self._started_campaign()
        self._import(self.campaign["id"], ["9001"])
        self.recipient = self.storage.list_onboarding_campaign_recipients(self.campaign["id"])[0]

    def test_create_invite_secure_token(self):
        r = self.ctx.onboarding_campaign_create_invite(self.owner, str(self.campaign["id"]), {"recipient_id": self.recipient["id"]})
        self.assertTrue(r["ok"])
        self.assertTrue(r["invite_link"].startswith("https://t.me/yellowclubagent_bot?start=c_"))
        token_part = r["invite_link"].split("start=c_")[1]
        self.assertGreaterEqual(len(token_part), 32)  # high-entropy, not a short guessable code

    def test_hash_only_storage(self):
        """v7.1.12.1: no random secret token is generated at all any more —
        the link is a reproducible HMAC signature derived from stable data
        (id, campaign_id, mk_user_id) + the app secret, so there is nothing
        resembling a bearer secret stored in the row (token_hash exists only
        as an inert, always-NULL legacy column — see the table's DDL comment)."""
        self.ctx.onboarding_campaign_create_invite(self.owner, str(self.campaign["id"]), {"recipient_id": self.recipient["id"]})
        with self.storage._connect() as conn:
            row = conn.execute("SELECT * FROM client_onboarding_invites WHERE recipient_id=?", (self.recipient["id"],)).fetchone()
        cols = set(row.keys())
        self.assertIn("token_hash", cols)
        self.assertIsNone(row["token_hash"])
        self.assertNotIn("token", cols)
        self.assertNotIn("token_plaintext", cols)
        self.assertNotIn("signature", cols)

    def test_revoke(self):
        r = self.ctx.onboarding_campaign_create_invite(self.owner, str(self.campaign["id"]), {"recipient_id": self.recipient["id"]})
        rv = self.ctx.onboarding_invite_revoke(self.owner, str(r["invite_id"]))
        self.assertTrue(rv["ok"])
        rv2 = self.ctx.onboarding_invite_revoke(self.owner, str(r["invite_id"]))
        self.assertFalse(rv2["ok"])  # already revoked, not idempotent-success — explicit reason instead

    def test_regenerate(self):
        r = self.ctx.onboarding_campaign_create_invite(self.owner, str(self.campaign["id"]), {"recipient_id": self.recipient["id"]})
        rg = self.ctx.onboarding_invite_regenerate(self.owner, str(r["invite_id"]))
        self.assertTrue(rg["ok"])
        self.assertNotEqual(rg["invite_link"], r["invite_link"])

    def test_expiration(self):
        r = self.ctx.onboarding_campaign_create_invite(self.owner, str(self.campaign["id"]), {"recipient_id": self.recipient["id"]})
        token = r["invite_link"].split("start=c_")[1]
        # Force the invite to look expired.
        with self.storage._connect() as conn:
            conn.execute("UPDATE client_onboarding_invites SET expires_at='2000-01-01T00:00:00' WHERE recipient_id=?", (self.recipient["id"],))
        act = self._activate(token, "1234")
        self.assertFalse(act["ok"])
        self.assertEqual(act["reason_code"], "invite_expired")

    def test_closed_campaign_blocks_new_invite(self):
        self.ctx.onboarding_campaign_close(self.owner, str(self.campaign["id"]))
        r = self.ctx.onboarding_campaign_create_invite(self.owner, str(self.campaign["id"]), {"recipient_id": self.recipient["id"]})
        self.assertFalse(r["ok"]); self.assertEqual(r.get("reason_code"), "campaign_not_active")

    def test_one_time_use(self):
        r = self.ctx.onboarding_campaign_create_invite(self.owner, str(self.campaign["id"]), {"recipient_id": self.recipient["id"]})
        token = r["invite_link"].split("start=c_")[1]
        first = self._activate(token, "1234")
        self.assertTrue(first["ok"])
        # different user retries the SAME token after it's used -> rejected
        second = self._activate(token, "5678")
        self.assertFalse(second["ok"]); self.assertEqual(second["reason_code"], "invite_already_used")

    def test_double_request_without_force_blocked(self):
        self.ctx.onboarding_campaign_create_invite(self.owner, str(self.campaign["id"]), {"recipient_id": self.recipient["id"]})
        r2 = self.ctx.onboarding_campaign_create_invite(self.owner, str(self.campaign["id"]), {"recipient_id": self.recipient["id"]})
        self.assertFalse(r2["ok"]); self.assertEqual(r2.get("reason_code"), "active_invite_exists")

    def test_concurrent_use_only_one_wins(self):
        # Simulated concurrency: sequential calls against the same atomic
        # claim (UPDATE ... WHERE status='active') — the second call always
        # loses the race deterministically, matching the real concurrent case.
        r = self.ctx.onboarding_campaign_create_invite(self.owner, str(self.campaign["id"]), {"recipient_id": self.recipient["id"]})
        token = r["invite_link"].split("start=c_")[1]
        first = self._activate(token, "111")
        second = self._activate(token, "222")
        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        links = self.storage.list_client_children_for_parent("111")
        self.assertEqual(len(links), 1)
        self.assertEqual(len(self.storage.list_client_children_for_parent("222")), 0)

    def test_wrong_telegram_user_after_use(self):
        r = self.ctx.onboarding_campaign_create_invite(self.owner, str(self.campaign["id"]), {"recipient_id": self.recipient["id"]})
        token = r["invite_link"].split("start=c_")[1]
        self._activate(token, "111")
        wrong = self._activate(token, "999")
        self.assertFalse(wrong["ok"])
        self.assertEqual(wrong["reason_code"], "invite_already_used")

    def test_already_linked_same_user_idempotent(self):
        r = self.ctx.onboarding_campaign_create_invite(self.owner, str(self.campaign["id"]), {"recipient_id": self.recipient["id"]})
        token = r["invite_link"].split("start=c_")[1]
        first = self._activate(token, "111")
        second = self._activate(token, "111")
        self.assertTrue(first["ok"]); self.assertFalse(first["already_linked"])
        self.assertTrue(second["ok"]); self.assertTrue(second["already_linked"])

    def test_linked_to_another_user_fails_closed(self):
        # mk 9001 already linked to parent 555 via the generic CL flow.
        code = self.storage.create_client_link_code("9001", "Someone", "1")
        # 9001 is already a recipient of self.campaign with its own invite;
        # link it directly via storage to simulate a pre-existing different-parent link.
        from utils import now_iso
        self.storage.link_client_child("555", code["code"], now_iso())
        r = self.ctx.onboarding_campaign_create_invite(self.owner, str(self.campaign["id"]), {"recipient_id": self.recipient["id"], "force_regenerate": True})
        token = r["invite_link"].split("start=c_")[1]
        act = self._activate(token, "666")
        self.assertFalse(act["ok"])
        self.assertEqual(act["reason_code"], "linked_to_another_user")
        # never leaks the other user's telegram id anywhere in the result
        self.assertNotIn("555", str(act))

    def test_no_raw_token_in_audit_log(self):
        r = self.ctx.onboarding_campaign_create_invite(self.owner, str(self.campaign["id"]), {"recipient_id": self.recipient["id"]})
        token = r["invite_link"].split("start=c_")[1]
        self._activate(token, "111")
        events = self.storage.list_onboarding_audit_events(self.campaign["id"])
        combined = str(events)
        self.assertNotIn(token, combined)

    def test_invite_permission_denied_for_operations(self):
        ops = _auth(70, "operations", self.ctx)
        r = self.ctx.onboarding_campaign_create_invite(ops, str(self.campaign["id"]), {"recipient_id": self.recipient["id"]})
        self.assertFalse(r["ok"])


# ─────────────────────────────────────────────────────────────────────────────
# Activation
# ─────────────────────────────────────────────────────────────────────────────

class TestActivation(OnboardingTestBase):
    def _invite_token(self, campaign, mk_user_id):
        self._import(campaign["id"], [mk_user_id])
        rec = [r for r in self.storage.list_onboarding_campaign_recipients(campaign["id"]) if r["mk_user_id"] == mk_user_id][0]
        r = self.ctx.onboarding_campaign_create_invite(self.owner, str(campaign["id"]), {"recipient_id": rec["id"]})
        self.assertTrue(r["ok"], r)
        return r["invite_link"].split("start=c_")[1], rec

    def test_parent_child_link_created(self):
        c = self._started_campaign()
        token, rec = self._invite_token(c, "1101")
        act = self._activate(token, "5001")
        self.assertTrue(act["ok"])
        children = self.storage.list_client_children_for_parent("5001")
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]["mk_user_id"], "1101")

    def test_pilot_created_review_enabled(self):
        c = self._started_campaign(auto_enroll_payments=True)
        token, rec = self._invite_token(c, "1102")
        act = self._activate(token, "5002")
        self.assertTrue(act["pilot_created"])
        pilot = self.storage.get_pilot_client("1102")
        self.assertEqual(pilot["mode"], "review")
        self.assertTrue(bool(pilot["enabled"]))

    def test_auto_enroll_false_skips_pilot(self):
        c = self._started_campaign(auto_enroll_payments=False)
        token, rec = self._invite_token(c, "1103")
        act = self._activate(token, "5003")
        self.assertFalse(act["pilot_created"])
        self.assertIsNone(self.storage.get_pilot_client("1103"))

    def test_existing_review_preserved(self):
        self._assert_existing_mode_preserved("review", "1201")

    def test_existing_auto_preserved(self):
        self._assert_existing_mode_preserved("auto", "1202")

    def test_existing_observe_preserved(self):
        self._assert_existing_mode_preserved("observe", "1203")

    def test_existing_disabled_preserved(self):
        self._assert_existing_mode_preserved("disabled", "1204")

    def _assert_existing_mode_preserved(self, mode, mk_user_id):
        self.storage.upsert_pilot_client(mk_user_id, mode=mode)
        c = self._started_campaign(name=f"preserve-{mode}")
        token, rec = self._invite_token(c, mk_user_id)
        act = self._activate(token, "6001")
        self.assertTrue(act["ok"]); self.assertFalse(act["pilot_created"])
        self.assertEqual(self.storage.get_pilot_client(mk_user_id)["mode"], mode)

    def test_no_payment_intent_created(self):
        c = self._started_campaign()
        token, rec = self._invite_token(c, "1301")
        self._activate(token, "7001")
        with self.storage._connect() as conn:
            n = conn.execute("SELECT COUNT(*) c FROM payment_intents").fetchone()["c"]
        self.assertEqual(n, 0)

    def test_no_bepaid_no_publish_no_mk_post_side_effects(self):
        c = self._started_campaign()
        token, rec = self._invite_token(c, "1302")
        act = self._activate(token, "7002")
        self.assertTrue(act["ok"])
        # No mk_payment_id / posting fields ever get set for a campaign
        # activation — nothing to check beyond "no payment_intents row",
        # already covered above; this test asserts the audit event set
        # produced contains only onboarding/pilot events, nothing MK-posting-shaped.
        events = self.storage.list_onboarding_audit_events(c["id"])
        event_types = {e["event_type"] for e in events}
        self.assertTrue(event_types.issubset({
            "onboarding_campaign_created", "onboarding_campaign_started", "onboarding_recipient_added",
            "onboarding_invite_created", "onboarding_invite_used", "payment_pilot_created_from_campaign",
        }))

    def test_survey_enabled_flag_returned(self):
        c = self._started_campaign(survey_enabled=True)
        token, rec = self._invite_token(c, "1401")
        act = self._activate(token, "8001")
        self.assertTrue(act["survey_enabled"])
        self.assertEqual(act["recipient_id"], rec["id"])

    def test_survey_disabled_flag_returned(self):
        c = self._started_campaign(survey_enabled=False)
        token, rec = self._invite_token(c, "1402")
        act = self._activate(token, "8002")
        self.assertFalse(act["survey_enabled"])


# ─────────────────────────────────────────────────────────────────────────────
# Regression — v7.1.11 flows completely unaffected
# ─────────────────────────────────────────────────────────────────────────────

class TestRegressionV7111Unaffected(OnboardingTestBase):
    def test_staff_link_and_enroll_unchanged_no_campaign_rows(self):
        code = self.storage.create_client_link_code("2001", "Kid", "1")["code"]
        result = self.ctx.client_admin_link_and_enroll(self.owner, {"code": code, "parent_telegram_user_id": "3001"})
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(len(self.storage.list_onboarding_campaigns()), 0)

    def test_parent_cl_flow_unchanged(self):
        code = self.storage.create_client_link_code("2002", "Kid", "1")["code"]
        parent = _auth(4001, "parent", self.ctx)
        result = self.ctx.client_link_child(parent, {"code": code})
        self.assertTrue(result.get("ok"), result)
        self.assertNotIn("payment_automation", result)
        self.assertEqual(len(self.storage.list_onboarding_campaigns()), 0)

    def test_payment_onboarding_staff_roles_unchanged(self):
        self.assertEqual(PAYMENT_ONBOARDING_STAFF_ROLES, {"owner", "admin", "client_manager"})


if __name__ == "__main__":
    unittest.main()

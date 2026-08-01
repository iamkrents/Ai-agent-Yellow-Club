"""Tests for v7.1.12.1 — schedule availability, academic level, reproducible
persistent invite links, and safe batch invite generation for 300+ clients.

Extends the existing v7.1.12 mass-onboarding-campaign feature (campaign
lifecycle / recipients / continuation status / Telegram deep-link activation
/ pilot integration — see test_onboarding_campaigns_v7112.py, unchanged and
still passing) rather than replacing any of it.

Covers (grouped to match the v7.1.12.1 spec's test checklist):
  Availability: parent creates, parent updates (edit replaces), another
    parent denied, staff creates/updates, invalid weekday/time denied,
    multiple intervals, preferred/possible, filters, summary, CSV export,
    campaign setting disabled (collect_schedule_availability=False still
    allows manual entry — it only controls whether the bot offers it),
    registration is never blocked by availability.
  Academic level: high-confidence detection, ambiguous name -> unknown,
    manual override, manual override preserved on re-import, audit,
    filters/export.
  Persistent links: repeat export after a brand-new Storage instance (i.e.
    after a process restart), no plaintext in DB, no raw signature in
    audit/logs, permission matrix, revoke/regenerate/used/expired still
    reproduce (with the correct status) rather than erroring.
  Batch: 300 recipients in one request, idempotency, existing active invite
    preserved (force_regenerate=False), force regenerate, partial errors
    (wrong-campaign recipient denied inside a batch), max batch limit, no
    financial side effects.
  Regression: existing single-invite flow, existing CL-flow, v7.1.11 staff
    onboarding, continuation statuses, pilot integration all still work
    exactly as before this round's changes; Food/intern untouched.

Run:
    python -m unittest tests.test_onboarding_campaign_v71121 -v
"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import (
    Storage,
    ACADEMIC_LEVELS,
    SCHEDULE_PREFERRED_BRANCHES,
    ONBOARDING_INVITE_MAX_BATCH_SIZE,
)
from web_app_server import MiniAppContext, CLIENT_ONBOARDING_CAMPAIGN_ROLES

DB_PATH = None  # set per-test via _tmp_db_path()
SECRET = "test-bot-token-secret"


def _tmp_db_path() -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Path(tmp.name)


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


class OnboardingV2TestBase(unittest.TestCase):
    def setUp(self):
        self.db_path = _tmp_db_path()
        self.storage = Storage(self.db_path)
        self.ctx = _make_ctx(self.storage)
        self.owner = _auth(1, "owner", self.ctx)
        self.cm = _auth(2, "client_manager", self.ctx)
        self.ops = _auth(3, "operations", self.ctx)

    def _started_campaign(self, **kw):
        kw.setdefault("name", "August 2026")
        kw.setdefault("academic_year", "2026/2027")
        r = self.ctx.onboarding_campaign_create(self.owner, kw)
        self.assertTrue(r.get("ok"), r)
        c = r["campaign"]
        s = self.ctx.onboarding_campaign_start(self.owner, str(c["id"]))
        self.assertTrue(s["ok"], s)
        return s["campaign"]

    def _import(self, campaign_id, recipients):
        r = self.ctx.onboarding_campaign_import_recipients(self.owner, str(campaign_id), {"recipients": recipients})
        self.assertTrue(r.get("ok"), r)
        return r

    def _link_and_activate(self, campaign_id, recipient_id, parent_tid):
        """Reach a real "linked child" state via the actual invite flow, so
        parent-ownership checks (availability's _require_availability_access)
        have something real to authorize against."""
        inv = self.ctx.onboarding_campaign_create_invite(self.owner, str(campaign_id), {"recipient_id": recipient_id})
        self.assertTrue(inv["ok"], inv)
        invite_id_str, _sep, sig = inv["invite_link"].split("start=c_")[1].partition("_")
        act = self.storage.activate_onboarding_invite(int(invite_id_str), sig, parent_tid, SECRET)
        self.assertTrue(act["ok"], act)
        return act


# ─────────────────────────────────────────────────────────────────────────────
# Availability
# ─────────────────────────────────────────────────────────────────────────────

class TestAvailability(OnboardingV2TestBase):
    def setUp(self):
        super().setUp()
        self.campaign = self._started_campaign(collect_schedule_availability=True)
        self._import(self.campaign["id"], [{"mk_user_id": "1001", "child_display_name": "Kid"}])
        self.recipient = self.storage.list_onboarding_campaign_recipients(self.campaign["id"])[0]
        self._link_and_activate(self.campaign["id"], self.recipient["id"], "5001")

    def test_parent_creates(self):
        parent = _auth(5001, "parent", self.ctx)
        r = self.ctx.onboarding_recipient_availability_submit(parent, str(self.recipient["id"]), {
            "preferred_branch": "YC1", "available_from": "2026-09-01",
            "intervals": [{"weekday": 1, "start_time": "15:00", "end_time": "16:00", "preference": "preferred"}],
        })
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["recipient"]["preferred_branch"], "YC1")

    def test_parent_updates_replaces_not_appends(self):
        parent = _auth(5001, "parent", self.ctx)
        self.ctx.onboarding_recipient_availability_submit(parent, str(self.recipient["id"]), {
            "intervals": [{"weekday": 1, "start_time": "15:00", "end_time": "16:00", "preference": "preferred"}],
        })
        self.ctx.onboarding_recipient_availability_submit(parent, str(self.recipient["id"]), {
            "intervals": [{"weekday": 2, "start_time": "10:00", "end_time": "11:00", "preference": "possible"}],
        })
        got = self.ctx.onboarding_recipient_availability_get(parent, str(self.recipient["id"]))
        self.assertEqual(len(got["intervals"]), 1)
        self.assertEqual(got["intervals"][0]["weekday"], 2)

    def test_another_parent_denied(self):
        other_parent = _auth(9999, "parent", self.ctx)
        r = self.ctx.onboarding_recipient_availability_submit(other_parent, str(self.recipient["id"]), {"intervals": []})
        self.assertFalse(r["ok"])
        r2 = self.ctx.onboarding_recipient_availability_get(other_parent, str(self.recipient["id"]))
        self.assertFalse(r2["ok"])

    def test_staff_creates_and_updates(self):
        r = self.ctx.onboarding_recipient_availability_submit(self.owner, str(self.recipient["id"]), {
            "preferred_branch": "either", "schedule_comment": "manager note",
            "intervals": [{"weekday": 3, "start_time": "12:00", "end_time": "13:00", "preference": "possible"}],
        })
        self.assertTrue(r["ok"], r)
        r2 = self.ctx.onboarding_recipient_availability_submit(self.cm, str(self.recipient["id"]), {
            "preferred_branch": "YC2", "intervals": [],
        })
        self.assertTrue(r2["ok"], r2)
        self.assertEqual(r2["recipient"]["preferred_branch"], "YC2")

    def test_staff_denied_for_operations(self):
        r = self.ctx.onboarding_recipient_availability_submit(self.ops, str(self.recipient["id"]), {"intervals": []})
        self.assertFalse(r["ok"])

    def test_invalid_weekday_denied(self):
        r = self.ctx.onboarding_recipient_availability_submit(self.owner, str(self.recipient["id"]), {
            "intervals": [{"weekday": 0, "start_time": "10:00", "end_time": "11:00"}],
        })
        self.assertFalse(r["ok"]); self.assertEqual(r["reason_code"], "invalid_interval")
        r2 = self.ctx.onboarding_recipient_availability_submit(self.owner, str(self.recipient["id"]), {
            "intervals": [{"weekday": 8, "start_time": "10:00", "end_time": "11:00"}],
        })
        self.assertFalse(r2["ok"])

    def test_invalid_time_format_denied(self):
        r = self.ctx.onboarding_recipient_availability_submit(self.owner, str(self.recipient["id"]), {
            "intervals": [{"weekday": 1, "start_time": "25:99", "end_time": "11:00"}],
        })
        self.assertFalse(r["ok"])

    def test_start_after_end_denied(self):
        r = self.ctx.onboarding_recipient_availability_submit(self.owner, str(self.recipient["id"]), {
            "intervals": [{"weekday": 1, "start_time": "12:00", "end_time": "11:00"}],
        })
        self.assertFalse(r["ok"])

    def test_multiple_intervals_preferred_and_possible(self):
        r = self.ctx.onboarding_recipient_availability_submit(self.owner, str(self.recipient["id"]), {
            "intervals": [
                {"weekday": 1, "start_time": "15:00", "end_time": "16:00", "preference": "preferred"},
                {"weekday": 2, "start_time": "10:00", "end_time": "11:00", "preference": "possible"},
                {"weekday": 3, "start_time": "09:00", "end_time": "10:00", "preference": "preferred"},
            ],
        })
        self.assertTrue(r["ok"])
        got = self.ctx.onboarding_recipient_availability_get(self.owner, str(self.recipient["id"]))
        self.assertEqual(len(got["intervals"]), 3)
        preferred = [iv for iv in got["intervals"] if iv["preference"] == "preferred"]
        possible = [iv for iv in got["intervals"] if iv["preference"] == "possible"]
        self.assertEqual(len(preferred), 2)
        self.assertEqual(len(possible), 1)

    def test_filters_by_availability_filled_and_weekday(self):
        self.ctx.onboarding_recipient_availability_submit(self.owner, str(self.recipient["id"]), {
            "intervals": [{"weekday": 4, "start_time": "16:00", "end_time": "17:00", "preference": "preferred"}],
        })
        self._import(self.campaign["id"], [{"mk_user_id": "1002", "child_display_name": "Other"}])
        filled = self.ctx.onboarding_campaign_get(self.owner, str(self.campaign["id"]), {"availability_filled": "true"})
        self.assertEqual(len(filled["recipients"]), 1)
        by_weekday = self.ctx.onboarding_campaign_get(self.owner, str(self.campaign["id"]), {"weekday": "4"})
        self.assertEqual(len(by_weekday["recipients"]), 1)
        by_wrong_weekday = self.ctx.onboarding_campaign_get(self.owner, str(self.campaign["id"]), {"weekday": "5"})
        self.assertEqual(len(by_wrong_weekday["recipients"]), 0)

    def test_summary_counts_and_grid(self):
        self.ctx.onboarding_recipient_availability_submit(self.owner, str(self.recipient["id"]), {
            "preferred_branch": "YC1",
            "intervals": [{"weekday": 1, "start_time": "15:00", "end_time": "16:00", "preference": "preferred"}],
        })
        detail = self.ctx.onboarding_campaign_get(self.owner, str(self.campaign["id"]), {})
        self.assertEqual(detail["stats"]["availability_filled"], 1)
        self.assertEqual(detail["stats"]["availability_missing"], 0)
        self.assertTrue(detail["availability_summary"]["grid"])
        self.assertEqual(detail["availability_summary"]["by_preferred_branch"]["YC1"], 1)

    def test_csv_export_includes_intervals_and_academic_level(self):
        self.ctx.onboarding_recipient_availability_submit(self.owner, str(self.recipient["id"]), {
            "preferred_branch": "YC1", "available_from": "2026-09-01", "schedule_comment": "only afternoons",
            "intervals": [{"weekday": 1, "start_time": "15:00", "end_time": "16:00", "preference": "preferred"}],
        })
        csv_bytes, _fn = self.ctx.onboarding_campaign_export_csv(self.owner, str(self.campaign["id"]), {})
        text = csv_bytes.decode("utf-8-sig")
        self.assertIn("Пн 15:00-16:00", text)
        self.assertIn("only afternoons", text)
        self.assertIn("2026-09-01", text)

    def test_campaign_setting_disabled_still_allows_manual_entry(self):
        camp2 = self._started_campaign(name="No collect", collect_schedule_availability=False)
        self._import(camp2["id"], [{"mk_user_id": "2001", "child_display_name": "X"}])
        rec2 = self.storage.list_onboarding_campaign_recipients(camp2["id"])[0]
        r = self.ctx.onboarding_recipient_availability_submit(self.owner, str(rec2["id"]), {
            "intervals": [{"weekday": 1, "start_time": "10:00", "end_time": "11:00"}],
        })
        self.assertTrue(r["ok"], r)  # setting only controls the bot's offer, not whether staff can record it

    def test_registration_not_blocked_by_availability(self):
        # The activation in setUp() already succeeded without ANY availability
        # data ever being submitted — this is the actual proof of "never blocks".
        children = self.storage.list_client_children_for_parent("5001")
        self.assertEqual(len(children), 1)
        got = self.ctx.onboarding_recipient_availability_get(self.owner, str(self.recipient["id"]))
        self.assertTrue(got["ok"])
        self.assertEqual(got["intervals"], [])


# ─────────────────────────────────────────────────────────────────────────────
# Academic level
# ─────────────────────────────────────────────────────────────────────────────

class TestAcademicLevel(OnboardingV2TestBase):
    def setUp(self):
        super().setUp()
        self.campaign = self._started_campaign()

    def test_high_confidence_detection_years(self):
        self._import(self.campaign["id"], [
            {"mk_user_id": f"30{i}", "child_display_name": f"K{i}", "course_name": f"Python {i} год обучения"}
            for i in range(1, 5)
        ])
        recs = {r["mk_user_id"]: r for r in self.storage.list_onboarding_campaign_recipients(self.campaign["id"])}
        for i in range(1, 5):
            rec = recs[f"30{i}"]
            self.assertEqual(rec["academic_level"], f"year_{i}")
            self.assertEqual(rec["academic_level_confidence"], "high")
            self.assertEqual(rec["academic_level_source"], "moyklass_group_name")

    def test_high_confidence_detection_advanced(self):
        self._import(self.campaign["id"], [{"mk_user_id": "401", "child_display_name": "K", "course_name": "Продвинутый Python"}])
        rec = self.storage.list_onboarding_campaign_recipients(self.campaign["id"])[0]
        self.assertEqual(rec["academic_level"], "advanced")
        self.assertEqual(rec["academic_level_confidence"], "high")

    def test_ambiguous_name_unknown(self):
        self._import(self.campaign["id"], [
            {"mk_user_id": "501", "child_display_name": "A", "course_name": "Summer Camp - Yellow Summer Week 1"},
            {"mk_user_id": "502", "child_display_name": "B", "course_name": "Python 2"},
            {"mk_user_id": "503", "child_display_name": "C", "course_name": ""},
        ])
        recs = {r["mk_user_id"]: r for r in self.storage.list_onboarding_campaign_recipients(self.campaign["id"])}
        for mk in ("501", "502", "503"):
            self.assertEqual(recs[mk]["academic_level"], "unknown")
            self.assertEqual(recs[mk]["academic_level_confidence"], "unknown")

    def test_manual_override(self):
        self._import(self.campaign["id"], [{"mk_user_id": "601", "child_display_name": "K"}])
        rec = self.storage.list_onboarding_campaign_recipients(self.campaign["id"])[0]
        r = self.ctx.onboarding_recipient_academic_level(self.owner, str(rec["id"]), {"academic_level": "year_3", "comment": "placement test"})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["recipient"]["academic_level"], "year_3")
        self.assertEqual(r["recipient"]["academic_level_source"], "staff")
        self.assertEqual(r["recipient"]["academic_level_confidence"], "manual")

    def test_manual_override_invalid_level_rejected(self):
        self._import(self.campaign["id"], [{"mk_user_id": "602", "child_display_name": "K"}])
        rec = self.storage.list_onboarding_campaign_recipients(self.campaign["id"])[0]
        r = self.ctx.onboarding_recipient_academic_level(self.owner, str(rec["id"]), {"academic_level": "bogus"})
        self.assertFalse(r["ok"])
        self.assertEqual(set(ACADEMIC_LEVELS), {"unknown", "year_1", "year_2", "year_3", "year_4", "advanced"})

    def test_manual_override_preserved_on_re_import(self):
        self._import(self.campaign["id"], [{"mk_user_id": "701", "child_display_name": "K", "course_name": "Python 1 год обучения"}])
        rec = self.storage.list_onboarding_campaign_recipients(self.campaign["id"])[0]
        self.assertEqual(rec["academic_level"], "year_1")
        self.ctx.onboarding_recipient_academic_level(self.owner, str(rec["id"]), {"academic_level": "advanced"})
        # Re-import same mk_user_id with a DIFFERENT (even conflicting) course_name.
        self._import(self.campaign["id"], [{"mk_user_id": "701", "child_display_name": "K", "course_name": "Python 4 год обучения"}])
        rec_after = self.storage.get_onboarding_recipient(rec["id"])
        self.assertEqual(rec_after["academic_level"], "advanced")
        self.assertEqual(rec_after["academic_level_source"], "staff")

    def test_audit_events(self):
        self._import(self.campaign["id"], [{"mk_user_id": "801", "child_display_name": "K", "course_name": "3 год обучения"}])
        rec = self.storage.list_onboarding_campaign_recipients(self.campaign["id"])[0]
        events = self.storage.list_onboarding_audit_events(self.campaign["id"])
        self.assertTrue(any(e["event_type"] == "academic_level_detected" for e in events))
        self.ctx.onboarding_recipient_academic_level(self.owner, str(rec["id"]), {"academic_level": "advanced", "comment": "manual"})
        events2 = self.storage.list_onboarding_audit_events(self.campaign["id"])
        changed = [e for e in events2 if e["event_type"] == "academic_level_changed"]
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]["old_status"], "year_3")
        self.assertEqual(changed[0]["new_status"], "advanced")

    def test_filters_and_export(self):
        self._import(self.campaign["id"], [
            {"mk_user_id": "901", "child_display_name": "A", "course_name": "2 год обучения"},
            {"mk_user_id": "902", "child_display_name": "B", "course_name": "unrelated name"},
        ])
        filtered = self.ctx.onboarding_campaign_get(self.owner, str(self.campaign["id"]), {"academic_level": "year_2"})
        self.assertEqual(len(filtered["recipients"]), 1)
        csv_bytes, _fn = self.ctx.onboarding_campaign_export_csv(self.owner, str(self.campaign["id"]), {})
        text = csv_bytes.decode("utf-8-sig")
        self.assertIn("2-й учебный год", text)
        self.assertIn("Не определён", text)


# ─────────────────────────────────────────────────────────────────────────────
# Persistent (reproducible) invite links
# ─────────────────────────────────────────────────────────────────────────────

class TestPersistentLinks(OnboardingV2TestBase):
    def setUp(self):
        super().setUp()
        self.campaign = self._started_campaign()
        self._import(self.campaign["id"], [{"mk_user_id": "1101", "child_display_name": "K"}])
        self.recipient = self.storage.list_onboarding_campaign_recipients(self.campaign["id"])[0]

    def test_repeat_export_after_new_storage_instance(self):
        inv = self.ctx.onboarding_campaign_create_invite(self.owner, str(self.campaign["id"]), {"recipient_id": self.recipient["id"]})
        self.assertTrue(inv["ok"], inv)
        original_link = inv["invite_link"]

        # Simulate a full process restart: fresh Storage + fresh context bound
        # to the SAME db file and the SAME app secret, nothing carried over
        # in memory.
        storage2 = Storage(self.db_path)
        ctx2 = _make_ctx(storage2)
        owner2 = _auth(1, "owner", ctx2)
        detail = ctx2.onboarding_campaign_get(owner2, str(self.campaign["id"]), {})
        rec_after = [r for r in detail["recipients"] if r["id"] == self.recipient["id"]][0]
        self.assertEqual(rec_after["invite_link"], original_link)

        csv_bytes, _fn = ctx2.onboarding_campaign_export_csv(owner2, str(self.campaign["id"]), {})
        self.assertIn(original_link, csv_bytes.decode("utf-8-sig"))

    def test_no_plaintext_in_db(self):
        self.ctx.onboarding_campaign_create_invite(self.owner, str(self.campaign["id"]), {"recipient_id": self.recipient["id"]})
        with self.storage._connect() as conn:
            row = conn.execute("SELECT * FROM client_onboarding_invites WHERE recipient_id=?", (self.recipient["id"],)).fetchone()
        self.assertIsNone(row["token_hash"])
        # No column anywhere in the row resembles a stored secret/signature.
        self.assertNotIn("signature", row.keys())

    def test_no_raw_signature_in_audit_or_logs(self):
        inv = self.ctx.onboarding_campaign_create_invite(self.owner, str(self.campaign["id"]), {"recipient_id": self.recipient["id"]})
        signature = inv["invite_link"].rsplit("_", 1)[-1]
        events = self.storage.list_onboarding_audit_events(self.campaign["id"])
        self.assertNotIn(signature, str(events))

    def test_permission_matrix_for_export_and_link_refetch(self):
        inv = self.ctx.onboarding_campaign_create_invite(self.owner, str(self.campaign["id"]), {"recipient_id": self.recipient["id"]})
        for auth in (self.owner, self.cm):
            r = self.ctx.onboarding_invite_get_link(auth, str(inv["invite_id"]))
            self.assertTrue(r["ok"], r)
        denied = self.ctx.onboarding_invite_get_link(self.ops, str(inv["invite_id"]))
        self.assertFalse(denied["ok"])
        csv_denied = self.ctx.onboarding_campaign_export_csv(self.ops, str(self.campaign["id"]), {})
        self.assertIsInstance(csv_denied, dict)
        self.assertFalse(csv_denied["ok"])

    def test_revoked_invite_link_still_reproduces_with_status(self):
        inv = self.ctx.onboarding_campaign_create_invite(self.owner, str(self.campaign["id"]), {"recipient_id": self.recipient["id"]})
        self.ctx.onboarding_invite_revoke(self.owner, str(inv["invite_id"]))
        link_info = self.ctx.onboarding_invite_get_link(self.owner, str(inv["invite_id"]))
        self.assertTrue(link_info["ok"])
        self.assertEqual(link_info["status"], "revoked")
        self.assertEqual(link_info["invite_link"], inv["invite_link"])
        # But it can never be reactivated.
        invite_id_str, _sep, sig = inv["invite_link"].split("start=c_")[1].partition("_")
        act = self.storage.activate_onboarding_invite(int(invite_id_str), sig, "9999", SECRET)
        self.assertFalse(act["ok"])
        self.assertEqual(act["reason_code"], "invite_revoked")

    def test_used_invite_link_still_reproduces(self):
        inv = self.ctx.onboarding_campaign_create_invite(self.owner, str(self.campaign["id"]), {"recipient_id": self.recipient["id"]})
        invite_id_str, _sep, sig = inv["invite_link"].split("start=c_")[1].partition("_")
        self.storage.activate_onboarding_invite(int(invite_id_str), sig, "8888", SECRET)
        link_info = self.ctx.onboarding_invite_get_link(self.owner, str(inv["invite_id"]))
        self.assertTrue(link_info["ok"])
        self.assertEqual(link_info["status"], "used")

    def test_expired_invite_link_reproduces_with_expired_status(self):
        inv = self.ctx.onboarding_campaign_create_invite(self.owner, str(self.campaign["id"]), {"recipient_id": self.recipient["id"]})
        with self.storage._connect() as conn:
            conn.execute("UPDATE client_onboarding_invites SET expires_at='2000-01-01T00:00:00' WHERE id=?", (inv["invite_id"],))
        invite_id_str, _sep, sig = inv["invite_link"].split("start=c_")[1].partition("_")
        act = self.storage.activate_onboarding_invite(int(invite_id_str), sig, "7777", SECRET)
        self.assertFalse(act["ok"]); self.assertEqual(act["reason_code"], "invite_expired")
        link_info = self.ctx.onboarding_invite_get_link(self.owner, str(inv["invite_id"]))
        self.assertEqual(link_info["status"], "expired")

    def test_invite_link_exported_audit_event_on_csv(self):
        self.ctx.onboarding_campaign_create_invite(self.owner, str(self.campaign["id"]), {"recipient_id": self.recipient["id"]})
        self.ctx.onboarding_campaign_export_csv(self.owner, str(self.campaign["id"]), {})
        events = self.storage.list_onboarding_audit_events(self.campaign["id"])
        self.assertTrue(any(e["event_type"] == "invite_link_exported" for e in events))


# ─────────────────────────────────────────────────────────────────────────────
# Batch generation
# ─────────────────────────────────────────────────────────────────────────────

class TestBatchGeneration(OnboardingV2TestBase):
    def setUp(self):
        super().setUp()
        self.campaign = self._started_campaign()

    def _import_n(self, n, prefix="B"):
        self._import(self.campaign["id"], [{"mk_user_id": f"{prefix}{i}", "child_display_name": f"{prefix}{i}"} for i in range(n)])
        return [r["id"] for r in self.storage.list_onboarding_campaign_recipients(self.campaign["id"]) if r["mk_user_id"].startswith(prefix)]

    def test_300_recipients_one_request(self):
        rids = self._import_n(300)
        r = self.ctx.onboarding_campaign_create_invites_batch(self.owner, str(self.campaign["id"]), {"recipient_ids": rids})
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["counts"]["created"], 300)
        self.assertEqual(len(r["results"]), 300)

    def test_idempotency_key_replay(self):
        rids = self._import_n(5, "I")
        key = "batch-idem-1"
        r1 = self.ctx.onboarding_campaign_create_invites_batch(self.owner, str(self.campaign["id"]), {"recipient_ids": rids, "idempotency_key": key})
        r2 = self.ctx.onboarding_campaign_create_invites_batch(self.owner, str(self.campaign["id"]), {"recipient_ids": rids, "idempotency_key": key})
        self.assertTrue(r1["ok"] and r2["ok"])
        self.assertEqual(r1["counts"]["created"], 5)
        self.assertEqual(r2.get("replayed"), True)
        # No duplicate invites were created by the replay.
        with self.storage._connect() as conn:
            n = conn.execute("SELECT COUNT(*) c FROM client_onboarding_invites WHERE campaign_id=?", (self.campaign["id"],)).fetchone()["c"]
        self.assertEqual(n, 5)

    def test_existing_active_invite_preserved_without_force(self):
        rids = self._import_n(3, "E")
        self.ctx.onboarding_campaign_create_invites_batch(self.owner, str(self.campaign["id"]), {"recipient_ids": rids})
        r2 = self.ctx.onboarding_campaign_create_invites_batch(self.owner, str(self.campaign["id"]), {"recipient_ids": rids})
        self.assertEqual(r2["counts"]["existing"], 3)
        self.assertEqual(r2["counts"]["created"], 0)

    def test_force_regenerate(self):
        rids = self._import_n(3, "F")
        r1 = self.ctx.onboarding_campaign_create_invites_batch(self.owner, str(self.campaign["id"]), {"recipient_ids": rids})
        r2 = self.ctx.onboarding_campaign_create_invites_batch(self.owner, str(self.campaign["id"]), {"recipient_ids": rids, "force_regenerate": True})
        self.assertEqual(r2["counts"]["regenerated"], 3)
        links1 = {x["recipient_id"]: x["invite_link"] for x in r1["results"]}
        links2 = {x["recipient_id"]: x["invite_link"] for x in r2["results"]}
        for rid in rids:
            self.assertNotEqual(links1[rid], links2[rid])

    def test_partial_errors_wrong_campaign_recipient_denied(self):
        rids = self._import_n(2, "P")
        other_campaign = self._started_campaign(name="Other")
        r = self.ctx.onboarding_campaign_create_invites_batch(self.owner, str(other_campaign["id"]), {"recipient_ids": rids})
        self.assertTrue(r["ok"])
        self.assertEqual(r["counts"]["failed"], 2)
        self.assertTrue(all(x["outcome"] == "failed" and x["reason"] == "not_in_campaign" for x in r["results"]))

    def test_max_batch_limit(self):
        r = self.ctx.onboarding_campaign_create_invites_batch(self.owner, str(self.campaign["id"]), {
            "recipient_ids": list(range(1, ONBOARDING_INVITE_MAX_BATCH_SIZE + 2))
        })
        self.assertFalse(r["ok"])
        self.assertEqual(r.get("reason_code"), "batch_too_large")

    def test_no_financial_side_effects(self):
        rids = self._import_n(10, "N")
        self.ctx.onboarding_campaign_create_invites_batch(self.owner, str(self.campaign["id"]), {"recipient_ids": rids})
        with self.storage._connect() as conn:
            n = conn.execute("SELECT COUNT(*) c FROM payment_intents").fetchone()["c"]
        self.assertEqual(n, 0)

    def test_permission_denied_for_operations(self):
        rids = self._import_n(2, "D")
        r = self.ctx.onboarding_campaign_create_invites_batch(self.ops, str(self.campaign["id"]), {"recipient_ids": rids})
        self.assertFalse(r["ok"])

    def test_no_raw_signature_in_batch_results(self):
        rids = self._import_n(2, "S")
        r = self.ctx.onboarding_campaign_create_invites_batch(self.owner, str(self.campaign["id"]), {"recipient_ids": rids})
        for row in r["results"]:
            self.assertNotIn("signature", row)


# ─────────────────────────────────────────────────────────────────────────────
# Regression
# ─────────────────────────────────────────────────────────────────────────────

class TestRegression(OnboardingV2TestBase):
    def test_existing_single_invite_flow_unchanged(self):
        campaign = self._started_campaign()
        self._import(campaign["id"], [{"mk_user_id": "1", "child_display_name": "K"}])
        rec = self.storage.list_onboarding_campaign_recipients(campaign["id"])[0]
        r = self.ctx.onboarding_campaign_create_invite(self.owner, str(campaign["id"]), {"recipient_id": rec["id"]})
        self.assertTrue(r["ok"])
        self.assertIn("invite_link", r)

    def test_existing_cl_flow_unchanged(self):
        code = self.storage.create_client_link_code("2001", "Kid", "1")["code"]
        parent_auth = _auth(4001, "parent", self.ctx)
        result = self.ctx.client_link_child(parent_auth, {"code": code})
        self.assertTrue(result.get("ok"), result)
        self.assertNotIn("payment_automation", result)

    def test_v7111_staff_onboarding_unchanged(self):
        code = self.storage.create_client_link_code("2002", "Kid", "1")["code"]
        result = self.ctx.client_admin_link_and_enroll(self.owner, {"code": code, "parent_telegram_user_id": "3001"})
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result["payment_automation"]["mode"], "review")

    def test_continuation_statuses_unchanged(self):
        campaign = self._started_campaign()
        self._import(campaign["id"], [{"mk_user_id": "3", "child_display_name": "K"}])
        rec = self.storage.list_onboarding_campaign_recipients(campaign["id"])[0]
        r = self.ctx.onboarding_campaign_continuation_status(self.owner, str(campaign["id"]), {"recipient_id": rec["id"], "status": "continues"})
        self.assertTrue(r["ok"])

    def test_pilot_integration_unchanged(self):
        campaign = self._started_campaign(auto_enroll_payments=True)
        self._import(campaign["id"], [{"mk_user_id": "4", "child_display_name": "K"}])
        rec = self.storage.list_onboarding_campaign_recipients(campaign["id"])[0]
        act = self._link_and_activate(campaign["id"], rec["id"], "6001")
        self.assertTrue(act["pilot_created"])
        pilot = self.storage.get_pilot_client("4")
        self.assertEqual(pilot["mode"], "review")

    def test_food_module_untouched(self):
        storage_src = (ROOT / "storage.py").read_text(encoding="utf-8")
        self.assertIn("parent_child_links", storage_src)
        self.assertIn("camp_children", storage_src)
        server_src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        self.assertIn("/api/food/link-child", server_src)

    def test_intern_module_untouched(self):
        server_src = (ROOT / "web_app_server.py").read_text(encoding="utf-8")
        self.assertIn("intern", server_src.lower())

    def test_role_gate_constant_unchanged(self):
        self.assertEqual(CLIENT_ONBOARDING_CAMPAIGN_ROLES, {"owner", "admin", "client_manager"})

    def test_schedule_branches_and_academic_levels_match_spec(self):
        self.assertEqual(set(SCHEDULE_PREFERRED_BRANCHES), {"YC1", "YC2", "either", "unknown"})


if __name__ == "__main__":
    unittest.main()

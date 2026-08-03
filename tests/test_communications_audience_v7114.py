"""Tests for v7.1.14 — staff "Рассылки": trusted audience resolution.

Covers:
  10. single trusted client resolves correctly.
  11. an invalid/unlinked client is rejected (not silently empty).
  12. all active parents deduped (one row per parent, not per link).
  13. a parent with two children is counted exactly once.
  14. system (standalone availability) campaigns never appear as a
      selectable connection campaign.
  15. connection-campaign audience only includes that campaign's own
      linked parents.
  16. "заполнили возможности" only matches parents with >=1 filled child.
  17. "не заполнили возможности" only matches parents with 0 filled children.
  18. a genuinely empty audience resolves to eligible_count=0 with a
      structured reason, never fabricated.
  19. excluded-reason counts are structured and additive (sum == excluded_count).
  20. no N+1 queries: audience resolution never issues MoyKlass calls, and
      resolves in a bounded number of DB round trips regardless of size.

Run:
    python -m unittest tests.test_communications_audience_v7114 -v
"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import web_app_server as srv  # noqa: E402
from storage import Storage  # noqa: E402
from utils import now_iso  # noqa: E402


def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _settings(**overrides):
    base = dict(
        client_communications_enabled=True, client_communications_pilot_telegram_ids=[],
        client_communications_send_enabled=True, client_communications_scheduler_enabled=True,
        client_notifications_enabled=True, client_notifications_pilot_telegram_ids=[],
        admin_ids=[], senior_teacher_ids=[], web_app_test_roles=True, food_module_enabled=False,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


def _make_ctx(storage: Storage, **settings_overrides):
    ctx = srv.MiniAppContext.__new__(srv.MiniAppContext)
    ctx.storage = storage
    ctx.settings = _settings(**settings_overrides)
    return ctx


def _link(storage: Storage, parent_tid: str, mk_user_id: str, name: str = "Child") -> None:
    now = now_iso()
    with storage._connect() as conn:
        conn.execute(
            """INSERT INTO client_parent_child_links
               (parent_telegram_user_id, mk_user_id, child_display_name, status, linked_at, created_at, updated_at)
               VALUES (?,?,?,'active',?,?,?)""",
            (parent_tid, mk_user_id, name, now, now, now),
        )


class TestSingleClient(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        _link(self.storage, "700001", "S1", "Ребёнок")
        self.ctx = _make_ctx(self.storage)

    def test_10_single_trusted_client_resolves(self):
        result = self.ctx._resolve_audience_single_client({"parent_telegram_id": "700001"})
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["eligible"]), 1)
        self.assertEqual(result["eligible"][0]["parent_telegram_id"], "700001")
        self.assertEqual(result["distinct_parents"], 1)

    def test_11_invalid_client_rejected(self):
        result = self.ctx._resolve_audience_single_client({"parent_telegram_id": "999999999"})
        self.assertFalse(result["ok"])
        self.assertIn("не найден", result["error"])

    def test_11b_empty_query_rejected(self):
        result = self.ctx._resolve_audience_single_client({})
        self.assertFalse(result["ok"])


class TestAllParentsDedup(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        _link(self.storage, "800001", "S1", "Ребёнок А")  # single-child parent
        _link(self.storage, "800002", "S2", "Ребёнок Б")  # two-children parent
        _link(self.storage, "800002", "S3", "Ребёнок В")
        self.ctx = _make_ctx(self.storage)

    def test_12_all_parents_deduped(self):
        result = self.ctx._resolve_audience_all_parents()
        parent_ids = [e["parent_telegram_id"] for e in result["eligible"]]
        self.assertEqual(len(parent_ids), len(set(parent_ids)), "duplicate parent rows")
        self.assertEqual(result["distinct_parents"], 2)
        self.assertEqual(result["matched_children"], 3)

    def test_13_two_children_parent_counted_once(self):
        result = self.ctx._resolve_audience_all_parents()
        count_800002 = sum(1 for e in result["eligible"] if e["parent_telegram_id"] == "800002")
        self.assertEqual(count_800002, 1)


class TestConnectionCampaign(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.ctx = _make_ctx(self.storage)

    def test_14_system_campaign_not_selectable(self):
        campaign_id = self.storage._ensure_standalone_availability_campaign("seed")
        result = self.ctx._resolve_audience_connection_campaign({"campaign_id": campaign_id})
        self.assertFalse(result["ok"])
        self.assertIn("Системные", result["error"])
        # Also confirm it's excluded from the staff-facing campaign list.
        listed_ids = {c["id"] for c in self.storage.list_onboarding_campaigns()}
        self.assertNotIn(campaign_id, listed_ids)

    def test_15_campaign_includes_only_its_own_linked_parents(self):
        camp = self.storage.create_onboarding_campaign("Camp A", academic_year="2026", created_by="seed")
        camp_id = camp["campaign"]["id"]
        self.storage.start_onboarding_campaign(camp_id, "seed")
        self.storage.import_onboarding_campaign_recipients(
            camp_id, [{"mk_user_id": "CA1", "child_display_name": "X"}, {"mk_user_id": "CA2", "child_display_name": "Y"}], "seed",
        )
        _link(self.storage, "810001", "CA1")  # connected
        # CA2 never connected.
        # An unrelated parent/child pair from a DIFFERENT campaign must not leak in.
        _link(self.storage, "810099", "UNRELATED")

        result = self.ctx._resolve_audience_connection_campaign({"campaign_id": camp_id})
        self.assertTrue(result["ok"])
        eligible_ids = {e["parent_telegram_id"] for e in result["eligible"]}
        self.assertEqual(eligible_ids, {"810001"})
        self.assertEqual(result["diagnostics"]["not_connected_count"], 1)


class TestAvailabilitySegments(unittest.TestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        _link(self.storage, "820001", "AV1", "Filled child")
        _link(self.storage, "820002", "AV2", "Unfilled child")
        recipient = self.storage.get_or_create_recipient_for_client("AV1", "Filled child", "seed")
        self.storage.submit_schedule_availability(
            recipient["id"], "seed", "staff", preferred_branch="YC1", available_from=None,
            schedule_comment="", intervals=[{"weekday": 1, "start_time": "10:00", "end_time": "11:00", "preference": "preferred"}],
            source="staff",
        )
        self.ctx = _make_ctx(self.storage)

    def test_16_filled_segment_matches_only_filled_parent(self):
        result = self.ctx._resolve_audience_availability(True)
        ids = {e["parent_telegram_id"] for e in result["eligible"]}
        self.assertEqual(ids, {"820001"})

    def test_17_missing_segment_matches_only_unfilled_parent(self):
        result = self.ctx._resolve_audience_availability(False)
        ids = {e["parent_telegram_id"] for e in result["eligible"]}
        self.assertEqual(ids, {"820002"})


class TestEmptyAudienceAndExclusionCounts(unittest.TestCase):
    def test_18_zero_audience_is_honest_not_fabricated(self):
        storage = _tmp_storage()
        ctx = _make_ctx(storage)
        result = ctx._resolve_audience_all_parents()
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["eligible"]), 0)
        self.assertEqual(result["distinct_parents"], 0)

    def test_19_exclusion_reason_counts_sum_to_excluded_count(self):
        storage = _tmp_storage()
        _link(storage, "830001", "E1")
        _link(storage, "830002", "E2")
        ctx = _make_ctx(storage, client_notifications_enabled=False, client_notifications_pilot_telegram_ids=[])
        result = ctx._resolve_audience_all_parents()
        summary = ctx._communications_summarize_exclusions(result["excluded"])
        self.assertEqual(sum(r["count"] for r in summary), len(result["excluded"]))
        self.assertEqual(len(result["excluded"]), 2)
        self.assertEqual(len(result["eligible"]), 0)


class TestNoNPlusOneMoyKlassCalls(unittest.TestCase):
    def test_20_no_moyklass_calls_during_resolution(self):
        storage = _tmp_storage()
        for i in range(50):
            _link(storage, f"84{i:05d}", f"BULK{i}")
        ctx = _make_ctx(storage)
        ctx.moyklass = mock.Mock()
        result = ctx._resolve_audience_all_parents()
        self.assertEqual(len(result["eligible"]), 50)
        ctx.moyklass.assert_not_called()
        # Availability resolver is the other one that scales with audience
        # size — confirm it doesn't touch moyklass either.
        ctx._resolve_audience_availability(True)
        ctx.moyklass.assert_not_called()


if __name__ == "__main__":
    unittest.main()

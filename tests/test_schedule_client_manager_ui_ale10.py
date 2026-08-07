"""Tests for ALE-10 — first read-only client_manager-facing "Дети" screen
in the schedule module, built on top of the already-existing ALE-8
draft-preview and the already-existing drafts-list read endpoints. No new
backend route is introduced by this step — these tests cover:

  - permission regression for the two existing endpoints this new screen
    newly relies on (schedule_draft_preview / schedule_drafts_list),
    specifically confirming client_manager CAN read them and an
    unauthorized caller CANNOT;
  - static checks on the new app.js UI code: required Russian copy,
    the exact endpoints it calls (no new/undocumented route), no MoyKlass
    write/publish affordance anywhere, and that the backend "decision"
    field is displayed as-is (never recomputed/overridden from
    baseline_outcome/continuation_status/availability_match on the
    frontend).

Run offline:
    python -m unittest tests.test_schedule_client_manager_ui_ale10 -v
"""
from __future__ import annotations

import re
import sys
import tempfile
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage
from web_app_server import MiniAppContext

APP_JS = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")


def _fn_body(name: str) -> str:
    m = re.search(r"(?:async )?function " + re.escape(name) + r"\([^)]*\) \{(.*?)\n\}\n", APP_JS, re.S)
    assert m, f"function {name} not found"
    return m.group(1)


def _make_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


def _make_ctx(storage: Storage, owner_id: int = 900001) -> MiniAppContext:
    # object.__new__ bypasses MiniAppContext.__init__ — same pattern as
    # tests.test_client_rollout_gates_v7113_round2._make_ctx and every
    # other schedule test file's _make_ctx.
    ctx = object.__new__(MiniAppContext)
    ctx.storage = storage
    ctx.settings = types.SimpleNamespace(
        admin_ids=[owner_id], senior_teacher_ids=[], web_app_test_roles=False,
        schedule_foundation_enabled=True, schedule_foundation_pilot_telegram_ids=[owner_id],
        schedule_draft_mutations_enabled=False,
        food_location_yc1="Кульман 1/1", food_location_yc2="Мстиславца 6",
    )
    return ctx


class TestClientManagerCanReadExistingEndpoints(unittest.TestCase):
    """This screen introduces no new backend route — it must reuse the
    same server-side role gate every other schedule endpoint already has.
    client_manager is one of the three roles that gate allows through."""

    def test_client_manager_can_read_draft_preview(self):
        storage = _make_storage()
        cm_id = 900101
        storage.set_staff_role(cm_id, "client_manager")
        ctx = _make_ctx(storage, owner_id=900001)
        # schedule_foundation_pilot_telegram_ids only lists the owner in
        # this fixture; make the module enabled globally instead so the
        # client_manager's real (non-owner) role is what's under test.
        ctx.settings.schedule_foundation_enabled = True

        result = ctx.schedule_draft_preview({"user_id": cm_id}, {})
        self.assertTrue(result.get("ok"), result)
        self.assertIn("summary", result)

    def test_client_manager_can_read_drafts_list(self):
        storage = _make_storage()
        cm_id = 900102
        storage.set_staff_role(cm_id, "client_manager")
        ctx = _make_ctx(storage, owner_id=900001)
        ctx.settings.schedule_foundation_enabled = True

        result = ctx.schedule_drafts_list({"user_id": cm_id}, {})
        self.assertTrue(result.get("ok"), result)
        self.assertIn("drafts", result)


class TestUnauthorizedRoleCannotReadEndpoints(unittest.TestCase):
    """A regular client/teacher (or any caller not in SCHEDULE_MODULE_
    ROLES) must never be able to read this data, matching the existing
    schedule-module contract — not something new introduced here, but
    newly exercised end-to-end by this screen's two API calls."""

    def test_unauthorized_role_cannot_read_draft_preview(self):
        storage = _make_storage()
        teacher_id = 900201
        storage.set_staff_role(teacher_id, "teacher")
        ctx = _make_ctx(storage, owner_id=900001)
        ctx.settings.schedule_foundation_enabled = True

        result = ctx.schedule_draft_preview({"user_id": teacher_id}, {})
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("reason_code"), "forbidden")

    def test_unauthorized_role_cannot_read_drafts_list(self):
        storage = _make_storage()
        teacher_id = 900202
        storage.set_staff_role(teacher_id, "teacher")
        ctx = _make_ctx(storage, owner_id=900001)
        ctx.settings.schedule_foundation_enabled = True

        result = ctx.schedule_drafts_list({"user_id": teacher_id}, {})
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("reason_code"), "forbidden")

    def test_unrecognized_caller_with_module_disabled_gets_feature_disabled(self):
        # module enabled but no pilot access at all for a random id — the
        # existing feature_disabled path, unaffected by this screen.
        storage = _make_storage()
        random_id = 900301
        ctx = _make_ctx(storage, owner_id=900001)
        ctx.settings.schedule_foundation_enabled = False
        ctx.settings.schedule_foundation_pilot_telegram_ids = []

        result = ctx.schedule_draft_preview({"user_id": random_id}, {})
        self.assertFalse(result.get("ok"))
        self.assertIn(result.get("reason_code"), ("forbidden", "feature_disabled"))


class TestNewTabRegistration(unittest.TestCase):
    def test_children_tab_registered_in_skeleton(self):
        body = _fn_body("_schedRenderSkeleton")
        self.assertIn('{ id: "children", label: "Дети" }', body)

    def test_children_tab_dispatched_in_current_tab_router(self):
        body = _fn_body("_schedRenderCurrentTab")
        self.assertIn('_schedState.tab === "children"', body)
        self.assertIn("_schedRenderChildrenPlan(root)", body)
        self.assertIn("_schedLoadChildrenPlan()", body)
        self.assertIn("_schedLoadChildrenDrafts()", body)

    def test_preexisting_tabs_still_present(self):
        # regression: adding the new tab must not have disturbed the
        # existing owner/admin technical tabs.
        body = _fn_body("_schedRenderSkeleton")
        for tab_id in ("overview", "groups", "foundation", "needs-review", "no-availability", "unconfirmed", "drafts"):
            self.assertIn(f'id: "{tab_id}"', body)


class TestHumanReadableCopy(unittest.TestCase):
    def test_screen_title_present(self):
        body = _fn_body("_schedRenderChildrenPlan")
        self.assertIn("Расписание нового учебного года", body)

    def test_summary_cards_use_exact_required_labels(self):
        for label in (
            "Всего детей", "Можно сохранить прошлую группу", "Ждут ответа",
            "Нужно распределить", "Требуют проверки", "Не продолжают",
        ):
            self.assertIn(label, APP_JS.split("SCHED_PREVIEW_SUMMARY_CARDS")[1][:600])

    def test_filters_use_exact_required_labels(self):
        block = APP_JS.split("SCHED_PREVIEW_FILTERS")[1][:700]
        for label in ("Все", "Ждут ответа", "Сохранить прошлую группу", "Нужно распределить", "Проверить вручную", "Не продолжают"):
            self.assertIn(label, block)

    def test_decision_labels_match_exact_required_russian_phrases(self):
        block = APP_JS.split("SCHED_PREVIEW_DECISION_LABELS = {")[1][:700]
        self.assertIn("Можно сохранить прошлую группу", block)
        self.assertIn("Ждём ответа", block)
        self.assertIn("Нужно подобрать время", block)
        self.assertIn("Нужно проверить", block)
        self.assertIn("Не продолжает", block)

    def test_no_raw_technical_decision_values_shown_as_labels(self):
        # the raw enum values themselves (keep_historical_slot etc.) must
        # only ever appear as object KEYS (mapping FROM them), never as
        # user-facing text on their own outside that map.
        row_body = _fn_body("_schedChildDecisionRowHtml")
        self.assertNotIn(">keep_historical_slot<", row_body)
        self.assertNotIn(">pending_confirmation<", row_body)
        self.assertNotIn(">manual_review<", row_body)

    def test_no_historical_baseline_text(self):
        body = _fn_body("_schedChildBaselineText")
        self.assertIn("Основная группа не определена", body)

    def test_ambiguous_baseline_text(self):
        body = _fn_body("_schedChildBaselineText")
        self.assertIn("Несколько возможных групп", body)

    def test_empty_drafts_state_uses_exact_required_copy(self):
        body = _fn_body("_schedRenderChildrenDrafts")
        self.assertIn(
            "Черновики появятся после того, как родители подтвердят продолжение обучения и укажут доступное время.",
            body,
        )


class TestReusesExistingEndpointsOnly(unittest.TestCase):
    def test_uses_existing_draft_preview_endpoint(self):
        body = _fn_body("_schedLoadChildrenPlan")
        self.assertIn("/api/schedule/draft-preview", body)

    def test_uses_existing_drafts_list_endpoint(self):
        body = _fn_body("_schedLoadChildrenDrafts")
        self.assertIn("/api/schedule/drafts", body)

    def test_no_new_undocumented_endpoint_introduced(self):
        new_section_start = APP_JS.index("// ── ALE-10")
        new_section_end = APP_JS.index("// ── Status + overview")
        new_section = APP_JS[new_section_start:new_section_end]
        endpoint_calls = re.findall(r'apiGet\(`(/api/[a-zA-Z0-9/_-]+)', new_section)
        for call in endpoint_calls:
            self.assertIn(call, ("/api/schedule/draft-preview", "/api/schedule/drafts"))


class TestNoMoyKlassWriteAffordance(unittest.TestCase):
    def test_no_moyklass_publish_or_sync_button_in_rendered_html(self):
        # scoped to the actual HTML-producing function bodies (rendered,
        # user-facing UI), not the surrounding explanatory code comments —
        # those legitimately mention "MoyKlass" to document why nothing is
        # written there.
        rendered = "".join(
            _fn_body(name) for name in (
                "_schedRenderChildrenPlan", "_schedRenderChildrenSummary", "_schedRenderChildrenFilters",
                "_schedChildDecisionRowHtml", "_schedRenderChildrenList", "_schedRenderChildrenDrafts",
                "_schedChildBaselineText",
            )
        )
        for forbidden in ("публиковать", "Опубликовать", "МойКласс", "MoyKlass", "moyklass", "sync-to-moyklass"):
            self.assertNotIn(forbidden, rendered)

    def test_new_section_never_calls_apiPost(self):
        # purely read-only screen — no mutation call of any kind.
        new_section_start = APP_JS.index("// ── ALE-10")
        new_section_end = APP_JS.index("// ── Status + overview")
        new_section = APP_JS[new_section_start:new_section_end]
        self.assertNotIn("apiPost(", new_section)


class TestDecisionNeverRecomputedOnFrontend(unittest.TestCase):
    """ALE-10 explicit instruction: 169 baseline=none / 6 ambiguous
    children stay in whatever the backend decided (pending_confirmation
    today) — the frontend must show c.decision as-is, never derive its
    own decision from baseline_outcome/continuation_status/
    availability_match."""

    def test_row_renderer_reads_decision_directly_from_the_record(self):
        body = _fn_body("_schedChildDecisionRowHtml")
        self.assertIn("SCHED_PREVIEW_DECISION_LABELS[c.decision]", body)

    def test_baseline_text_helper_never_returns_a_decision_value(self):
        # the baseline helper is display-only context text, not a
        # decision — must never emit any of the actual decision strings.
        body = _fn_body("_schedChildBaselineText")
        for decision in ("keep_historical_slot", "pending_confirmation", "needs_reassignment", "manual_review", "stopped"):
            self.assertNotIn(decision, body)

    def test_row_renderer_never_reassigns_c_decision(self):
        body = _fn_body("_schedChildDecisionRowHtml")
        self.assertNotIn("c.decision =", body)
        self.assertNotIn("c.decision=", body)


if __name__ == "__main__":
    unittest.main()

"""Tests for v7.1.17 — "Расписание" schedule module: draft editor UI.

Static text/AST-style checks against app.js (this repo's existing frontend
test convention). Covers spec section 23 UI checks 63, 64, 72, 73, 74, 75:
the draft editor screen itself, conflict badges, Telegram BackButton
handling (including the unsaved-changes guard), the request-token race
guard (a stale response must never overwrite a newer one), polling
stopping when the user leaves the tab, and the feature flag hiding the
whole module.

Run:
    python -m unittest tests.test_schedule_draft_editor_ui_v7117 -v
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

APP_JS = (ROOT / "miniapp" / "app.js").read_text(encoding="utf-8")


def _fn_body(name: str) -> str:
    m = re.search(r"(?:async )?function " + re.escape(name) + r"\([^)]*\) \{(.*?)\n\}\n", APP_JS, re.S)
    assert m, f"function {name} not found"
    return m.group(1)


class TestDraftEditorScreen(unittest.TestCase):
    def test_63_editor_has_all_required_fields(self):
        body = _fn_body("_schedRenderDraftEditor")
        for field in ("name", "branch_name", "teacher_name", "weekday", "start_time", "duration_minutes", "planned_start_date"):
            self.assertIn(f'"{field}"', body if field in ("weekday",) else body, f"field {field} missing")
        for label in ("Название", "Филиал", "Преподаватель", "День недели", "Время начала", "Длительность", "Предполагаемая дата начала"):
            self.assertIn(label, body)

    def test_63b_editor_disabled_when_archived_or_mutations_off(self):
        body = _fn_body("_schedRenderDraftEditor")
        self.assertIn("archived || !mutationsEnabled", body)

    def test_63c_status_transition_buttons_present(self):
        body = _fn_body("_schedRenderDraftEditor")
        self.assertIn("_schedSetDraftStatus('needs_review')", body)
        self.assertIn("_schedSetDraftStatus('ready')", body)
        self.assertIn("_schedConfirmArchiveDraft()", body)

    def test_63d_save_button_disabled_until_dirty(self):
        body = _fn_body("_schedRenderDraftEditor")
        self.assertIn("!_schedState.editorDirty", body)

    def test_63e_double_submit_blocked_while_saving(self):
        save_fn = _fn_body("_schedSaveDraftFields")
        self.assertIn("if (!dr || _schedState.editorSaving) return;", save_fn)
        self.assertIn("_schedState.editorSaving = true;", save_fn)
        render = _fn_body("_schedRenderDraftEditor")
        self.assertIn("_schedState.editorSaving", render)

    def test_version_conflict_shows_message_and_blocks_silent_overwrite(self):
        save_fn = _fn_body("_schedSaveDraftFields")
        self.assertIn('d.reason_code === "version_conflict"', save_fn)
        self.assertIn("_schedState.editorVersionConflict = true", save_fn)
        render = _fn_body("_schedRenderDraftEditor")
        self.assertIn("editorVersionConflict", render)
        self.assertIn("изменён другим сотрудником", render)


class TestConflictBadges(unittest.TestCase):
    def test_64_conflict_banner_shows_child_and_teacher_conflicts(self):
        body = _fn_body("_schedRenderDraftEditor")
        self.assertIn("conflicts.child_conflicts", body)
        self.assertIn("conflicts.teacher_conflict_drafts", body)
        self.assertIn("sched-conflict-banner", body)

    def test_64b_member_badges_show_continuation_and_availability(self):
        body = _fn_body("_schedRenderDraftEditor")
        self.assertIn("SCHED_CONTINUATION_LABELS[m.continuation_status]", body)
        self.assertIn("SCHED_MATCH_LABELS[m.availability_match]", body)


class TestBackButtonAndUnsavedChanges(unittest.TestCase):
    def test_72_backbutton_scoped_to_section(self):
        set_bb = _fn_body("_schedSetBackButton")
        self.assertIn('_schedState.view === "overview"', set_bb)
        self.assertIn("_appSetBackButton(null)", set_bb)
        self.assertIn("_appSetBackButton(_schedBackButtonHandler)", set_bb)

    def test_72b_unsaved_changes_warns_before_leaving_editor(self):
        handler = _fn_body("_schedBackButtonHandler")
        self.assertIn('_schedState.view === "draft-editor" && _schedState.editorDirty', handler)
        self.assertIn("uiConfirmSheet(", handler)
        self.assertIn("Есть несохранённые изменения", handler)


class TestRaceGuardAndPolling(unittest.TestCase):
    def test_73_stale_response_never_overwrites_newer_one(self):
        # _schedLoadStatus is the single top-level "refresh everything"
        # entry point (tab activation, refresh button, returning from a
        # sub-view) — same role _wsPeriodChanged plays for Payments
        # Workspace — so it alone is allowed to bump the shared token.
        for loader in ("_schedLoadStats", "_schedLoadGroups", "_schedLoadGroupDetail",
                       "_schedLoadDrafts", "_schedLoadMembers", "_schedLoadDraftDetail"):
            body = _fn_body(loader)
            self.assertIn("myToken", body, f"{loader} must capture the request token")
            self.assertIn("if (myToken !== _schedState.reqToken) return;", body, f"{loader} must fence out stale responses")
            self.assertNotIn("_schedState.reqToken++", body)
            self.assertNotIn("++_schedState.reqToken", body)

        status_loader = _fn_body("_schedLoadStatus")
        self.assertIn("const myToken = ++_schedState.reqToken;", status_loader)
        self.assertIn("if (myToken !== _schedState.reqToken) return;", status_loader)

    def test_74_polling_stops_on_tab_leave(self):
        self.assertIn('if (name === "schedule-foundation") loadScheduleFoundation();', APP_JS)
        self.assertIn("else _schedStopPolling();", APP_JS)
        stop_fn = _fn_body("_schedStopPolling")
        self.assertIn("clearInterval(_schedState.pollTimer)", stop_fn)
        self.assertIn("_schedState.pollTimer = null", stop_fn)

    def test_74b_polling_also_stops_when_sync_finishes(self):
        poll_fn = _fn_body("_schedPollProgress")
        self.assertIn("_schedStopPolling();", poll_fn)


class TestFeatureFlagHidesModule(unittest.TestCase):
    def test_75_tab_hidden_by_capability_flag(self):
        self.assertIn(
            'document.querySelectorAll(".schedule-foundation-only").forEach(el => el.classList.toggle("hidden", !roleCaps().canUseScheduleFoundation));',
            APP_JS,
        )

    def test_75b_no_external_calls_when_flag_off(self):
        # the tab button itself carries "hidden" by default in the markup
        # (setupRoleUi only ever REMOVES hidden when the capability is on),
        # and loadScheduleFoundation is only ever invoked from tab
        # activation — never from a top-level/auto-run call.
        index_html = (ROOT / "miniapp" / "index.html").read_text(encoding="utf-8")
        self.assertIn('class="tab schedule-foundation-only hidden"', index_html)
        # exactly 3 legitimate call sites: the function definition itself,
        # the tab-activation dispatch, and the manual refresh button —
        # never an unconditional top-level call that would run with the
        # feature flag off.
        self.assertIn('if (name === "schedule-foundation") loadScheduleFoundation();', APP_JS)
        self.assertIn('onclick="loadScheduleFoundation()"', APP_JS)
        self.assertEqual(APP_JS.count("loadScheduleFoundation()"), 3)


if __name__ == "__main__":
    unittest.main()

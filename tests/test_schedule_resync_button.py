"""Tests for ALE-31 — minimal "Синхронизировать заново" control for the
healthy-active-snapshot state of the schedule module overview.

Before this fix, the schedule workspace had no UI trigger to start a new
manual sync once a completed active snapshot already existed: the
pre-existing "Начать синхронизацию" button only rendered with no snapshot
at all, and "Повторить" only rendered for a failed/partial latest attempt.
An owner with a healthy snapshot had no way to trigger another sync short
of calling the API directly.

Static text/AST-style checks against app.js, matching this repo's existing
frontend test convention for this module (see
tests/test_schedule_scoped_light_theme_v71171.py) — no JS runtime/DOM
available in this test suite.

Run:
    python -m unittest tests.test_schedule_resync_button -v
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


class TestResyncButtonRendersWhenHealthy(unittest.TestCase):
    """completed active snapshot + syncEnabled=true + syncRunning=false => button exists."""

    def test_sync_status_line_computes_can_resync_from_completed_status_and_sync_enabled(self):
        body = _fn_body("_schedRenderSyncStatusLine")
        self.assertIn("canResync", body)
        self.assertIn('active.status === "completed"', body)
        self.assertIn("s.syncEnabled", body)

    def test_button_text_and_handler_present_in_healthy_branch(self):
        body = _fn_body("_schedRenderSyncStatusLine")
        self.assertIn("Синхронизировать заново", body)
        self.assertIn("_schedTriggerResync()", body)

    def test_button_only_renders_inside_the_active_snapshot_branch(self):
        # the button markup must be part of the `else if (active)` branch,
        # not a separate top-level element always present.
        body = _fn_body("_schedRenderSyncStatusLine")
        active_branch = body.split("} else if (active) {", 1)[1].split("} else {", 1)[0]
        self.assertIn("canResync", active_branch)
        self.assertIn("Синхронизировать заново", active_branch)

    def test_button_reuses_existing_visual_pattern_no_new_css_class(self):
        # must reuse the same "sched-linklike" pattern as the existing
        # "Повторить" retry button (app.js's retry-banner button) rather
        # than introducing a new visual pattern.
        body = _fn_body("_schedRenderSyncStatusLine")
        self.assertIn('class="sched-linklike"', body)


class TestResyncButtonCallsFreshSyncNoResume(unittest.TestCase):
    """Button must call the existing fresh-sync path with no resume_snapshot_id."""

    def test_button_onclick_calls_trigger_resync_with_no_arguments(self):
        body = _fn_body("_schedRenderSyncStatusLine")
        self.assertIn("onclick=\"_schedTriggerResync()\"", body)

    def test_trigger_resync_calls_start_sync_with_no_resume_id(self):
        body = _fn_body("_schedTriggerResync")
        self.assertIn("_schedStartSync()", body)
        self.assertNotIn("_schedStartSync(_schedState", body)
        # must never pass any snapshot id through this new control
        self.assertNotRegex(body, r"_schedStartSync\([^)]+\)")

    def test_start_sync_with_no_args_creates_new_snapshot_not_resume(self):
        # _schedStartSync's own body must still branch on a falsy
        # resumeSnapshotId to send an empty body — i.e. reuse of the
        # exact same "new sync" contract as the first-sync button.
        body = _fn_body("_schedStartSync")
        self.assertIn("resumeSnapshotId ? { resume_snapshot_id: resumeSnapshotId } : {}", body)


class TestResyncUnavailableWhileRunning(unittest.TestCase):
    """syncRunning=true => repeated launch unavailable."""

    def test_trigger_resync_guards_against_running_snapshot(self):
        body = _fn_body("_schedTriggerResync")
        self.assertIn("_schedState.status.runningSnapshot", body)
        self.assertIn("return;", body)

    def test_trigger_resync_guards_against_reentrant_click(self):
        body = _fn_body("_schedTriggerResync")
        self.assertIn("if (_schedState.resyncTriggering) return;", body)

    def test_button_disabled_while_triggering(self):
        body = _fn_body("_schedRenderSyncStatusLine")
        self.assertIn('_schedState.resyncTriggering ? "disabled"', body)

    def test_running_branch_never_renders_the_resync_button(self):
        # the `if (s.runningSnapshot)` branch renders only the running
        # badge — the resync button lives exclusively in the mutually
        # exclusive `else if (active)` branch.
        body = _fn_body("_schedRenderSyncStatusLine")
        running_branch = body.split("if (s.runningSnapshot) {", 1)[1].split("} else if (active) {", 1)[0]
        self.assertNotIn("Синхронизировать заново", running_branch)
        self.assertNotIn("_schedTriggerResync", running_branch)

    def test_underlying_start_sync_still_has_its_own_double_submit_guard(self):
        # defense in depth, unchanged from the pre-existing implementation.
        body = _fn_body("_schedStartSync")
        self.assertIn("if (_schedState.status && _schedState.status.runningSnapshot) return;", body)


class TestResyncButtonHiddenWhenSyncDisabled(unittest.TestCase):
    """syncEnabled=false => button absent, following the existing
    canSync/first-sync pattern (omit entirely, not disabled-attribute)."""

    def test_can_resync_requires_sync_enabled_flag(self):
        body = _fn_body("_schedRenderSyncStatusLine")
        # canResync must be a boolean AND of all conditions, not any one
        # alone, so a disabled flag always suppresses the button even with
        # a completed active snapshot.
        self.assertRegex(body, r'canResync\s*=\s*active\.status === "completed" && s\.syncEnabled && !hasUnresolvedFailedAttempt')

    def test_button_omitted_entirely_when_can_resync_false(self):
        body = _fn_body("_schedRenderSyncStatusLine")
        self.assertIn("(canResync", body)
        # ternary must fall back to empty string, matching the pre-existing
        # `canSync ? ... : ""` idiom used by the first-sync button.
        self.assertIn(': "")', body)


class TestFailedPartialCoexistence(unittest.TestCase):
    """Pre-merge review finding #2: a newer failed/partial snapshot must
    suppress the fresh-resync control entirely — that state is owned
    exclusively by the existing "Повторить" retry banner in
    _schedRenderOverview, never both controls shown at once."""

    def test_active_branch_computes_unresolved_failed_attempt_from_latest(self):
        body = _fn_body("_schedRenderSyncStatusLine")
        active_branch = body.split("} else if (active) {", 1)[1].split("} else {", 1)[0]
        self.assertIn("hasUnresolvedFailedAttempt", active_branch)
        self.assertIn("s.recentSnapshots", active_branch)

    def test_unresolved_failed_attempt_uses_same_condition_as_retry_banner(self):
        # must match _schedRenderOverview's own latest/failed-partial check
        # verbatim, not a reimplementation that could silently drift.
        body = _fn_body("_schedRenderSyncStatusLine")
        self.assertIn('latest.id !== active.id && (latest.status === "failed" || latest.status === "partial")', body)

    def test_can_resync_excludes_unresolved_failed_attempt(self):
        body = _fn_body("_schedRenderSyncStatusLine")
        self.assertRegex(
            body,
            r'canResync\s*=\s*active\.status === "completed" && s\.syncEnabled && !hasUnresolvedFailedAttempt',
        )

    def test_overview_retry_banner_condition_is_unchanged(self):
        # the flow that must exclusively own the failed/partial recovery
        # state — must not have been touched by this fix.
        overview = _fn_body("_schedRenderOverview")
        self.assertIn(
            'if (latest && latest.id !== active.id && (latest.status === "failed" || latest.status === "partial")) {',
            overview,
        )


class TestResyncTriggeringLifecycleRobustness(unittest.TestCase):
    """Pre-merge review finding #1: resyncTriggering must reset to false on
    every path, including a thrown exception from the first render call
    itself — not only from _schedStartSync (which never rejects, since it
    already catches its own errors internally)."""

    def test_flag_set_true_only_right_before_the_try_that_also_wraps_the_first_render(self):
        body = _fn_body("_schedTriggerResync")
        self.assertRegex(
            body,
            r'_schedState\.resyncTriggering = true;\s*try \{\s*_schedRenderSyncStatusLine\(\);\s*await _schedStartSync\(\);\s*\} finally \{',
        )

    def test_finally_always_resets_flag_and_rerenders(self):
        body = _fn_body("_schedTriggerResync")
        finally_block = body.split("} finally {", 1)[1]
        self.assertIn("_schedState.resyncTriggering = false;", finally_block)
        self.assertIn("_schedRenderSyncStatusLine();", finally_block)

    def test_start_sync_never_rethrows_so_finally_always_runs(self):
        # _schedStartSync must keep catching its own errors internally —
        # if that guarantee were ever removed, _schedTriggerResync's own
        # try/finally would still reset the flag, but this pins the
        # invariant this fix's safety argument depends on.
        body = _fn_body("_schedStartSync")
        self.assertIn("} catch (e) {", body)
        self.assertIn("setNotice(", body)


class TestExistingFlowsUnbroken(unittest.TestCase):
    """First-sync and failed/partial retry flows must be untouched."""

    def test_first_sync_button_still_gated_on_no_snapshot_at_all(self):
        overview = _fn_body("_schedRenderOverview")
        self.assertIn("!s.activeSnapshot && (!s.recentSnapshots || !s.recentSnapshots.length)", overview)
        self.assertIn('onclick="_schedStartSync()">Начать синхронизацию</button>', overview)

    def test_failed_partial_retry_error_state_still_present(self):
        overview = _fn_body("_schedRenderOverview")
        self.assertIn('latest.status === "failed" || latest.status === "partial"', overview)
        self.assertIn("_schedStartSync(${latest.id})", overview)

    def test_retry_banner_for_stale_failed_latest_still_present(self):
        overview = _fn_body("_schedRenderOverview")
        self.assertIn("Повторить", overview)
        self.assertIn('class="sched-linklike" onclick="_schedStartSync(${latest.id})"', overview)

    def test_overview_render_function_does_not_itself_render_the_new_button(self):
        # the new control lives in the always-visible header status line,
        # not duplicated inside the tab-specific overview body.
        overview = _fn_body("_schedRenderOverview")
        self.assertNotIn("_schedTriggerResync", overview)


if __name__ == "__main__":
    unittest.main()

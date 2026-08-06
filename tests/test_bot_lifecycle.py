"""Tests for graceful shutdown of BotHandlers._auto_schedule_watch_loop (ALE-7 / fix/bot-graceful-shutdown).

Before this fix, the auto-schedule watcher task created in post_init() was never
cancelled on bot shutdown, which produced "Task was destroyed but it is pending!"
warnings when the event loop closed. This file covers the lifecycle wiring only
(start, cancel+await on shutdown, restart, no double-start) — the watch loop's
own business logic (intervals, schedule checks, notifications) is untouched by
this fix and is not re-tested here; see handlers.py::_auto_schedule_watch_loop.

Run:
    python -m unittest tests.test_bot_lifecycle -v
"""
from __future__ import annotations

import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from handlers import BotHandlers

BOT_PY = (ROOT / "bot.py").read_text(encoding="utf-8")
HANDLERS_PY = (ROOT / "handlers.py").read_text(encoding="utf-8")


def _make_handlers(*, mk_auto_watch_enabled: bool = True, moyklass_enabled: bool = True) -> BotHandlers:
    """Build a BotHandlers instance without touching Storage/AgentCore/AdminPanel —
    only the fields the lifecycle code path (post_init/post_shutdown) actually reads.
    Mirrors the object.__new__(BotHandlers) pattern already used in
    tests/test_onboarding_bot_v7112.py."""
    h = object.__new__(BotHandlers)
    h.settings = types.SimpleNamespace(
        mk_auto_watch_enabled=mk_auto_watch_enabled,
        moyklass_enabled=moyklass_enabled,
        mk_watch_interval_minutes=15,
        mk_watch_days=30,
        web_app_url="",
    )
    h._schedule_watcher_task = None
    h.bot_username = ""
    h._setup_miniapp_menu_button = AsyncMock()
    return h


class _FakeApp:
    """Stand-in for telegram.ext.Application — post_init only awaits app.bot.get_me()."""

    def __init__(self) -> None:
        self.bot = types.SimpleNamespace(
            get_me=AsyncMock(return_value=types.SimpleNamespace(username="testbot"))
        )


async def _forever_loop_like_watcher(_app) -> None:
    """Mimics the real _auto_schedule_watch_loop's cancellation shape (sleeps forever,
    re-raises CancelledError instead of swallowing it — see handlers.py:802-803)
    without any of its MoyKlass/storage business logic."""
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        raise


class TestGracefulShutdown(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.h = _make_handlers()
        self.h._auto_schedule_watch_loop = _forever_loop_like_watcher
        self.app = _FakeApp()

    async def test_start_then_shutdown_leaves_no_pending_task(self):
        await self.h.post_init(self.app)
        task = self.h._schedule_watcher_task
        self.assertIsNotNone(task)
        self.assertFalse(task.done())

        await self.h.post_shutdown(self.app)

        self.assertTrue(task.done())
        self.assertTrue(task.cancelled())
        self.assertIsNone(self.h._schedule_watcher_task)

    async def test_shutdown_does_not_propagate_cancelled_error(self):
        await self.h.post_init(self.app)
        # _auto_schedule_watch_loop re-raises CancelledError after catching it;
        # post_shutdown must swallow that, not let it escape to the caller.
        await self.h.post_shutdown(self.app)  # would raise CancelledError if unhandled

    async def test_shutdown_is_a_noop_when_no_task_was_started(self):
        h = _make_handlers(mk_auto_watch_enabled=False)
        await h.post_shutdown(self.app)  # no post_init call at all
        self.assertIsNone(h._schedule_watcher_task)

    async def test_restart_after_shutdown_creates_a_new_task(self):
        await self.h.post_init(self.app)
        first_task = self.h._schedule_watcher_task

        await self.h.post_shutdown(self.app)
        self.assertIsNone(self.h._schedule_watcher_task)

        await self.h.post_init(self.app)
        second_task = self.h._schedule_watcher_task
        self.assertIsNotNone(second_task)
        self.assertIsNot(second_task, first_task)
        self.assertFalse(second_task.done())

        await self.h.post_shutdown(self.app)  # cleanup, avoid leaking a pending task

    async def test_post_init_does_not_double_start_a_live_watcher(self):
        await self.h.post_init(self.app)
        first_task = self.h._schedule_watcher_task

        await self.h.post_init(self.app)  # called again while watcher is still alive

        self.assertIs(self.h._schedule_watcher_task, first_task)

        await self.h.post_shutdown(self.app)  # cleanup

    async def test_watcher_not_started_when_flags_disabled(self):
        h = _make_handlers(mk_auto_watch_enabled=False, moyklass_enabled=True)
        h._auto_schedule_watch_loop = _forever_loop_like_watcher

        await h.post_init(self.app)

        self.assertIsNone(h._schedule_watcher_task)


class TestShutdownHookWiring(unittest.TestCase):
    """Static checks that the shutdown hook is actually registered, mirroring the
    project convention of asserting wiring via source text (see
    tests/test_onboarding_bot_v7112.py::TestStartPayloadBranching)."""

    def test_bot_py_registers_post_shutdown_hook(self):
        self.assertIn(".post_shutdown(handlers.post_shutdown)", BOT_PY)

    def test_handlers_post_shutdown_cancels_and_nulls_task(self):
        idx = HANDLERS_PY.find("async def post_shutdown")
        self.assertNotEqual(idx, -1, "post_shutdown method not found in handlers.py")
        segment = HANDLERS_PY[idx:idx + 1000]
        self.assertIn("task.cancel()", segment)
        self.assertIn("await task", segment)
        self.assertIn("self._schedule_watcher_task = None", segment)

    def test_post_init_guards_against_duplicate_start(self):
        idx = HANDLERS_PY.find("async def post_init")
        self.assertNotEqual(idx, -1)
        segment = HANDLERS_PY[idx:idx + 700]
        self.assertIn("not self._schedule_watcher_task.done()", segment)


if __name__ == "__main__":
    unittest.main()

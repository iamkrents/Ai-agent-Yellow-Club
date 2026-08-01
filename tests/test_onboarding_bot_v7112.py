"""Tests for v7.1.12 — bot deep-link activation + continuation survey flow.

Entirely bot-conversation-driven (no Mini App screen involved) — the bot
talks directly to the same Storage instance (handlers.py already does this
elsewhere, e.g. self.storage.get_staff_test_mode), so this flow has no HTTP
endpoints of its own; see storage.get_onboarding_invite_preview /
activate_onboarding_invite / submit_continuation_response for the actual
logic, already covered end-to-end in test_onboarding_campaigns_v7112.py.
This file covers the bot-side wiring: /start payload branching, the
confirm/cancel callback, and the survey callback — plus the regression
requirement that a bare /start (no payload) is completely unchanged.

Run:
    python -m unittest tests.test_onboarding_bot_v7112 -v
"""
from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage import Storage, ONBOARDING_INVITE_TOKEN_PREFIX
from handlers import BotHandlers

HANDLERS_PY = (ROOT / "handlers.py").read_text(encoding="utf-8")
BOT_PY = (ROOT / "bot.py").read_text(encoding="utf-8")


def _tmp_storage() -> Storage:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    return Storage(Path(tmp.name))


SECRET = "test-bot-token-secret"  # v7.1.12.1 — matches h.settings.telegram_bot_token below


def _make_handlers(storage: Storage) -> BotHandlers:
    h = object.__new__(BotHandlers)
    h.storage = storage
    h.settings = types.SimpleNamespace(web_app_url="", telegram_max_message_chars=3900, telegram_bot_token=SECRET)
    h.pending_onboarding_invites = {}
    h.pending_onboarding_surveys = {}
    return h


def _combined_payload(invite: dict) -> str:
    """v7.1.12.1 — "<invite_id>_<signature>", the exact start-payload shape
    (minus the c_ prefix) that create_onboarding_invite's result implies."""
    return f"{invite['invite_id']}_{invite['signature']}"


def _msg() -> MagicMock:
    m = MagicMock()
    m.reply_text = AsyncMock()
    return m


def _query() -> MagicMock:
    q = MagicMock()
    q.edit_message_text = AsyncMock()
    q.message = MagicMock()
    q.message.reply_text = AsyncMock()
    return q


class OnboardingBotTestBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.storage = _tmp_storage()
        self.h = _make_handlers(self.storage)
        campaign = self.storage.create_onboarding_campaign(
            "Test campaign", "2026/2027", "1", survey_enabled=True
        )["campaign"]
        self.storage.start_onboarding_campaign(campaign["id"], "1")
        self.storage.import_onboarding_campaign_recipients(
            campaign["id"], [{"mk_user_id": "9001", "child_display_name": "Vasya"}], "1"
        )
        rec = self.storage.list_onboarding_campaign_recipients(campaign["id"])[0]
        invite = self.storage.create_onboarding_invite(campaign["id"], rec["id"], "1", SECRET)
        self.token = _combined_payload(invite)
        self.campaign = campaign
        self.recipient = rec

    def _no_survey_campaign_invite(self):
        campaign = self.storage.create_onboarding_campaign(
            "No survey", "2026/2027", "1", survey_enabled=False
        )["campaign"]
        self.storage.start_onboarding_campaign(campaign["id"], "1")
        self.storage.import_onboarding_campaign_recipients(
            campaign["id"], [{"mk_user_id": "9002", "child_display_name": "Petya"}], "1"
        )
        rec = self.storage.list_onboarding_campaign_recipients(campaign["id"])[0]
        invite = self.storage.create_onboarding_invite(campaign["id"], rec["id"], "1", SECRET)
        return _combined_payload(invite), rec


class TestStartPayloadBranching(unittest.TestCase):
    def test_start_branch_checks_token_prefix(self):
        idx = HANDLERS_PY.find('if cmd in {"start", "register"}:')
        self.assertNotEqual(idx, -1)
        segment = HANDLERS_PY[idx:idx + 600]
        self.assertIn("ONBOARDING_INVITE_TOKEN_PREFIX", segment)
        self.assertIn("_handle_onboarding_invite_start", segment)

    def test_bare_start_falls_through_unchanged(self):
        # Regression: the existing has_any_role / food_module_enabled /
        # ask_registration branches must still be reachable and untouched.
        idx = HANDLERS_PY.find('if cmd in {"start", "register"}:')
        end = HANDLERS_PY.find('if cmd in {"menu", "admin"}:', idx)
        segment = HANDLERS_PY[idx:end]
        self.assertIn("self.admin.has_any_role(user.id)", segment)
        self.assertIn("self._send_parent_welcome(msg, user.id)", segment)
        self.assertIn("self.admin.ask_registration(msg)", segment)

    def test_callback_handler_registered_in_bot_py(self):
        self.assertIn("CallbackQueryHandler(handlers.handle_callback_query)", BOT_PY)

    def test_token_prefix_matches_storage_constant(self):
        self.assertEqual(ONBOARDING_INVITE_TOKEN_PREFIX, "c_")


class TestInvitePreview(OnboardingBotTestBase):
    async def test_bad_token_shows_friendly_error_no_pending_state(self):
        msg = _msg()
        await self.h._handle_onboarding_invite_start(msg, 555, "c_totallybogus")
        self.assertNotIn(555, self.h.pending_onboarding_invites)
        text = msg.reply_text.call_args.args[0]
        self.assertIn("недействительна", text)

    async def test_good_token_shows_confirmation_with_child_name(self):
        msg = _msg()
        await self.h._handle_onboarding_invite_start(msg, 999, f"c_{self.token}")
        invite_id_str, _sep, signature = self.token.partition("_")
        self.assertEqual(self.h.pending_onboarding_invites[999], {"invite_id": int(invite_id_str), "signature": signature})
        text = msg.reply_text.call_args.args[0]
        self.assertIn("Vasya", text)
        self.assertIn("Подключить ребёнка", text)
        markup = msg.reply_text.call_args.kwargs.get("reply_markup")
        self.assertIsNotNone(markup)

    async def test_token_never_placed_in_callback_data(self):
        msg = _msg()
        await self.h._handle_onboarding_invite_start(msg, 999, f"c_{self.token}")
        markup = msg.reply_text.call_args.kwargs.get("reply_markup")
        for row in markup.inline_keyboard:
            for btn in row:
                self.assertNotIn(self.token, btn.callback_data or "")


class TestConfirmCallback(OnboardingBotTestBase):
    async def test_confirm_activates_and_creates_link(self):
        msg = _msg()
        await self.h._handle_onboarding_invite_start(msg, 999, f"c_{self.token}")
        query = _query()
        await self.h._handle_onboarding_confirm_callback(query, 999)
        self.assertNotIn(999, self.h.pending_onboarding_invites)
        self.assertIn("подключён", query.edit_message_text.call_args.args[0])
        children = self.storage.list_client_children_for_parent("999")
        self.assertEqual(len(children), 1)
        self.assertEqual(children[0]["mk_user_id"], "9001")

    async def test_confirm_with_survey_enabled_prompts_survey(self):
        msg = _msg()
        await self.h._handle_onboarding_invite_start(msg, 999, f"c_{self.token}")
        query = _query()
        await self.h._handle_onboarding_confirm_callback(query, 999)
        self.assertIn(999, self.h.pending_onboarding_surveys)
        self.assertEqual(self.h.pending_onboarding_surveys[999]["campaign_id"], self.campaign["id"])
        survey_text = query.message.reply_text.call_args.args[0]
        self.assertIn("продолжать обучение", survey_text)

    async def test_confirm_with_survey_disabled_no_survey_prompt(self):
        token, rec = self._no_survey_campaign_invite()
        msg = _msg()
        await self.h._handle_onboarding_invite_start(msg, 888, f"c_{token}")
        query = _query()
        await self.h._handle_onboarding_confirm_callback(query, 888)
        self.assertNotIn(888, self.h.pending_onboarding_surveys)

    async def test_stale_confirm_without_pending_state(self):
        query = _query()
        await self.h._handle_onboarding_confirm_callback(query, 12345)
        self.assertIn("устарела", query.edit_message_text.call_args.args[0])

    async def test_cancel_clears_pending_state(self):
        msg = _msg()
        await self.h._handle_onboarding_invite_start(msg, 999, f"c_{self.token}")
        self.assertIn(999, self.h.pending_onboarding_invites)
        query = _query()
        query.data = "oc_cancel"
        query.from_user = MagicMock(id=999)
        query.answer = AsyncMock()
        await self.h.handle_callback_query(types.SimpleNamespace(callback_query=query), None)
        self.assertNotIn(999, self.h.pending_onboarding_invites)
        self.assertIn("Отменено", query.edit_message_text.call_args.args[0])

    async def test_linked_to_another_user_shows_exact_spec_text(self):
        from utils import now_iso
        code = self.storage.create_client_link_code("9001", "Vasya", "1")["code"]
        self.storage.link_client_child("222", code, now_iso())
        # regenerate invite since the original wasn't consumed by this
        invite2 = self.storage.create_onboarding_invite(self.campaign["id"], self.recipient["id"], "1", SECRET, force_regenerate=True)
        msg = _msg()
        await self.h._handle_onboarding_invite_start(msg, 333, f"c_{_combined_payload(invite2)}")
        query = _query()
        await self.h._handle_onboarding_confirm_callback(query, 333)
        text = query.edit_message_text.call_args.args[0]
        self.assertEqual(text, "Клиент уже подключён к другому аккаунту. Обратитесь к администратору.")
        self.assertNotIn("222", text)


class TestSurveyCallback(OnboardingBotTestBase):
    async def _activate(self, user_id=999):
        msg = _msg()
        await self.h._handle_onboarding_invite_start(msg, user_id, f"c_{self.token}")
        query = _query()
        await self.h._handle_onboarding_confirm_callback(query, user_id)
        return query

    async def test_survey_answer_updates_continuation_status(self):
        await self._activate()
        query2 = _query()
        await self.h._handle_onboarding_survey_callback(query2, 999, "continues")
        self.assertNotIn(999, self.h.pending_onboarding_surveys)
        self.assertIn("Спасибо", query2.edit_message_text.call_args.args[0])
        rec = self.storage.get_onboarding_recipient(self.recipient["id"])
        self.assertEqual(rec["continuation_status"], "continues")

    async def test_survey_answer_recorded_as_parent_app_response(self):
        await self._activate()
        query2 = _query()
        await self.h._handle_onboarding_survey_callback(query2, 999, "not_continuing")
        with self.storage._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM client_continuation_responses WHERE recipient_id=?", (self.recipient["id"],)
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "parent_app")
        self.assertEqual(rows[0]["response_status"], "not_continuing")

    async def test_stale_survey_answer(self):
        query = _query()
        await self.h._handle_onboarding_survey_callback(query, 55555, "continues")
        self.assertIn("завершён", query.edit_message_text.call_args.args[0])


if __name__ == "__main__":
    unittest.main()

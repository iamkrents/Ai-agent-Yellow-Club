from __future__ import annotations

import asyncio
import json
import hashlib
import hmac
import logging
import re
import urllib.parse
from datetime import date, datetime, time, timedelta
from typing import Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update, WebAppInfo, MenuButtonWebApp
from telegram.ext import ContextTypes

from admin_panel import AdminPanel
from agent_core import AgentCore, AnswerContext
from config import Settings
from rules import is_question_like, should_smart_reply
from storage import Storage
from trial_manager import extract_trial_data, format_trial_for_admin, has_trial_details, should_start_or_update_trial
from report_manager import is_parent_report_request, report_need_details_message
from utils import chunk_telegram, clean_text, extract_command
from keyboards import REPORT_START_BUTTONS, REPORT_ACTION_BUTTONS, MOYKLASS_BUTTONS, TEACHER_DAY_BUTTONS, report_actions_keyboard, report_waiting_keyboard, staff_keyboard, moyklass_keyboard, selected_lesson_keyboard, lesson_list_keyboard, teacher_day_prepare_keyboard, teacher_day_prepare_lesson_keyboard, teacher_day_lesson_keyboard, teacher_day_close_keyboard, after_lesson_absents_keyboard, after_lesson_next_keyboard, after_lesson_topic_keyboard, after_lesson_done_keyboard, manager_waiting_keyboard, manager_answer_keyboard, manager_trials_keyboard, class_list_keyboard, selected_class_keyboard, selected_user_keyboard, parent_keyboard
from moyklass_client import (
    MoyKlassClient,
    filter_lessons_by_teacher,
    format_lessons_result,
    format_users_result,
    format_lesson_preview,
    format_lesson_technical,
    format_teachers_result,
    extract_lesson_topic_from_result,
    extract_lesson_id_from_result,
    lesson_has_teacher,
    extract_items,
    lesson_open_button_label,
    _pick,
    _lesson_date_value,
    _lesson_time_value,
    _lesson_group_value,
    _lesson_teacher_value,
    preview_json,
    save_moyklass_manual_name,
    delete_moyklass_manual_name,
    format_moyklass_manual_names,
    format_classes_result,
    format_class_preview,
    format_class_users_result,
    class_open_button_label,
    format_month_analytics_result,
    format_month_analytics_debug,
    format_month_analytics_probe,
    format_month_issue_lessons_result,
    format_month_lesson_breakdown_result,
    format_lesson_records_result,
    format_payments_result,
    format_active_clients_result,
    format_moyklass_work_tasks_result,
    format_absences_result,
    format_low_attendance_result,
    format_unpaid_active_clients_result,
    format_teacher_month_report_result,
    format_daily_tasks_result,
    format_user_card_result,
    format_user_lessons_result,
    format_user_payments_result,
    format_user_absences_result,
    format_user_joins_result,
)

log = logging.getLogger("yellow_club_agent.handlers")


VALID_ROLES = {"owner", "manager", "teacher", "methodist", "operations", "smm", "accountant", "other"}


class BotHandlers:
    def __init__(self, settings: Settings, storage: Storage, core: AgentCore, admin: AdminPanel):
        self.settings = settings
        self.storage = storage
        self.core = core
        self.admin = admin
        self.moyklass = MoyKlassClient(settings.moyklass_api_url, settings.moyklass_api_key, settings.moyklass_timeout)
        self.bot_username: str = ""
        # Private chat states for role-based cabinet flows.
        # Currently used for teacher/owner parent report generation.
        self.private_states: dict[int, dict[str, str]] = {}
        # Pending MoyKlass write operations. Nothing is sent to MoyKlass until /mk_confirm.
        self.pending_mk_writes: dict[int, dict] = {}
        self._schedule_watcher_task: asyncio.Task | None = None

    async def post_init(self, app):
        me = await app.bot.get_me()
        self.bot_username = me.username or ""
        log.info("Bot started as @%s", self.bot_username)
        await self._setup_miniapp_menu_button(app.bot)
        if self.settings.mk_auto_watch_enabled and self.settings.moyklass_enabled:
            self._schedule_watcher_task = asyncio.create_task(self._auto_schedule_watch_loop(app))
            log.info(
                "MoyKlass auto watcher enabled: interval=%s min, days=%s",
                self.settings.mk_watch_interval_minutes,
                self.settings.mk_watch_days,
            )

    async def _setup_miniapp_menu_button(self, bot) -> None:
        """Set the persistent bottom-left Telegram chat menu button to open the Mini App.

        This creates the button like the `Open` / `Play` button in many Telegram apps.
        The Mini App still also has an inline signed-link fallback from the normal keyboard.
        """
        url = (self.settings.web_app_url or "").strip()
        if not url:
            return
        if not url.startswith("https://"):
            log.warning("Mini App menu button skipped: WEB_APP_URL must start with https://, got %r", url)
            return
        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="Open", web_app=WebAppInfo(url=url))
            )
            log.info("Mini App menu button configured: Open -> %s", url)
        except Exception as e:
            log.warning("Could not configure Mini App menu button: %s", e)

    def _thread_id(self, update: Update) -> Optional[int]:
        msg = update.effective_message
        return getattr(msg, "message_thread_id", None) if msg else None

    def _save_incoming(self, update: Update) -> None:
        msg = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        if not msg or not chat:
            return
        text = msg.text or msg.caption or ""
        if not text:
            return
        self.storage.save_message(
            chat_id=chat.id,
            thread_id=self._thread_id(update),
            chat_type=chat.type,
            user_id=user.id if user else None,
            username=(user.username if user else "") or "",
            full_name=(user.full_name if user else "") or "",
            text=text,
            is_bot=bool(user.is_bot if user else False),
        )
        log.info("MAIN UPDATE DEBUG | chat_id=%s | type=%s | thread_id=%s | user_id=%s | text=%r", chat.id, chat.type, self._thread_id(update), user.id if user else None, text[:200])

    def _is_reply_to_bot(self, update: Update) -> bool:
        msg = update.effective_message
        if not msg or not msg.reply_to_message or not msg.reply_to_message.from_user:
            return False
        return (msg.reply_to_message.from_user.username or '').lower() == (self.bot_username or '').lower()

    def _is_rewrite_request(self, text: str) -> bool:
        n = clean_text(text).lower().replace('ё', 'е')
        markers = [
            'не нравится', 'переделай', 'перепиши', 'сгенерируй лучше', 'сделай лучше',
            'ответ плохой', 'плохой ответ', 'не так', 'не подходит', 'переделать',
            'короче', 'слишком длин', 'мягче', 'официальнее', 'подробнее', 'другой вариант',
        ]
        return any(m in n for m in markers)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        self._save_incoming(update)
        msg = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        if not msg or not chat or not user:
            return
        text = clean_text(msg.text or msg.caption or "")
        if not text:
            return

        # ADMIN_IDS already have owner access from .env.
        # Register them into staff_users as well, so /users and /set_role work normally.
        if chat.type == "private":
            self.admin.ensure_admin_registered(user)

        # Role-based private cabinet flows work before common buttons.
        if chat.type == "private" and await self._handle_private_flow(msg, text):
            return

        # Reply keyboard buttons and role registration work only in private chat.
        if chat.type == "private" and await self.admin.handle_button(msg, text):
            return

        cmd, args = extract_command(text)
        if cmd:
            await self.handle_command(update, context, cmd, args)
            return

        # In private chat, first interaction should register the employee role.
        if chat.type == "private" and not self.admin.has_any_role(user.id):
            if self.settings.food_module_enabled:
                await self._send_parent_welcome(msg, user.id)
            else:
                await self.admin.ask_registration(msg)
            return

        # Ignore groups that are not allowed.
        if chat.type != "private" and not self.settings.is_group_allowed(chat.id):
            return

        # If a user replies to the agent's message with a request to improve/rewrite it,
        # regenerate the answer based on the replied message.
        if self._is_reply_to_bot(update) and self._is_rewrite_request(text):
            original = msg.reply_to_message.text or msg.reply_to_message.caption or ''
            answer = self.core.rewrite_bot_answer(original_answer=original, instruction=text, chat_id=chat.id, thread_id=self._thread_id(update))
            await self._reply(msg, answer)
            return

        if self.settings.enable_trial_manager:
            open_trial = self.storage.get_open_trial(chat.id, self._thread_id(update))
            should_start = should_start_or_update_trial(text)
            should_continue = bool(open_trial and has_trial_details(text))
            if should_start or should_continue:
                data = extract_trial_data(text)
                if data:
                    self.storage.create_or_update_trial(chat.id, self._thread_id(update), user.id, data)

        if not self._should_reply(update, text):
            return

        answer = self.core.build_answer(AnswerContext(chat_id=chat.id, thread_id=self._thread_id(update), user_text=text))
        await self._reply(msg, answer)

    def _looks_like_parent_report(self, text: str) -> bool:
        n = (text or "").strip().lower().replace("ё", "е")
        return n.startswith("здравствуйте, уважаемые родители") and "сегодня на занятии" in n

    def _report_action_instruction(self, text: str) -> str:
        mapping = {
            "🔁 Переделать": "Сделай другой вариант отчёта: сохрани смысл, но переформулируй естественнее.",
            "✂️ Короче": "Сократи отчёт. Оставь только самые важные пункты и простой итог для родителей.",
            "📌 Подробнее": "Сделай отчёт немного подробнее и понятнее для родителей, но не добавляй выдуманные действия.",
            "🤝 Мягче": "Сделай формулировки мягче, теплее и спокойнее для родительского чата.",
        }
        return mapping.get(text, text)

    def _is_manager_role(self, user_id: int) -> bool:
        role = self.admin.get_role(user_id) or "other"
        return bool(role in {"manager", "owner", "operations"} or self.admin.is_admin(user_id))

    def _manager_prompt_text(self, mode: str) -> str:
        if mode == "manager_parent_reply_waiting":
            return (
                "Опишите ситуацию с родителем или вставьте его сообщение.\n\n"
                "Пример:\n"
                "Родитель спрашивает Roblox для ребёнка 8 лет, офлайн, что ответить?"
            )
        if mode == "manager_direction_waiting":
            return (
                "Опишите возраст ребёнка, интересы, опыт и пожелания родителя.\n\n"
                "Пример:\n"
                "8 лет, любит Roblox и рисовать, опыта в программировании нет."
            )
        if mode == "manager_makeup_waiting":
            return (
                "Опишите ситуацию по пропуску или отработке.\n\n"
                "Пример:\n"
                "Иван пропустил GDevelop 6 тему, группа вторник 17:30. Что написать родителю?"
            )
        if mode == "manager_responsible_waiting":
            return (
                "Опишите ситуацию, и я подскажу, к кому внутри Yellow Club обратиться.\n\n"
                "Пример:\n"
                "Родитель недоволен занятием, преподаватель говорит, что ребёнок сильно отстаёт."
            )
        return "Опишите ситуацию текстом."

    def _manager_build_prompt(self, mode: str, text: str) -> str:
        base = (text or "").strip()
        if mode == "manager_parent_reply_waiting":
            return (
                "Ты помощник менеджера Yellow Club. Подготовь ответ родителю на русском языке.\n"
                "Ответ должен быть готовым к отправке, спокойным, понятным и без лишней воды.\n"
                "Учитывай актуальные факты: пробное занятие бесплатное, 4 занятия - 239 BYN, адреса Кульман 1/1 и Мстиславца 6, группы до 6 учеников, возраст 7-17.\n"
                "Если данных не хватает, сначала кратко перечисли, что менеджеру нужно уточнить, а затем дай готовый вариант сообщения.\n\n"
                f"Ситуация от менеджера: {base}"
            )
        if mode == "manager_direction_waiting":
            return (
                "Ты помощник менеджера Yellow Club. Помоги подобрать направление обучения ребёнку.\n"
                "Нужно дать менеджеру: какие направления подходят, какие уточняющие вопросы задать родителю и как мягко предложить бесплатное пробное занятие.\n"
                "Не обещай конкретную группу и расписание без проверки МойКласс.\n\n"
                f"Данные от менеджера: {base}"
            )
        if mode == "manager_makeup_waiting":
            return (
                "Ты помощник менеджера Yellow Club. Помоги с отработкой пропущенного занятия.\n"
                "Опирайся на регламент: отработка закрывает пропущенные знания, желательно до следующей темы; в МойКласс в комментарии пишем 'Имя - отработка'.\n"
                "Дай менеджеру короткий план действий и готовый текст для родителя, если он нужен.\n\n"
                f"Ситуация: {base}"
            )
        if mode == "manager_responsible_waiting":
            return (
                "Ты внутренний помощник Yellow Club. По ситуации сотрудника определи, к кому обратиться: операционный менеджер, клиентский менеджер, методист, бухгалтер, SMM/маркетинг или владелец.\n"
                "Ответь коротко: кто ответственный, почему, и что написать/передать.\n\n"
                f"Ситуация: {base}"
            )
        return base

    def _courses_and_prices_text(self) -> str:
        return (
            "📚 Курсы и цены Yellow Club\n\n"
            "Актуальная стоимость: 4 занятия - 239 BYN.\n"
            "Пробное занятие - бесплатное.\n"
            "Возраст учеников: 7-17 лет.\n"
            "Максимум в группе: 6 учеников.\n"
            "Адреса: Кульман 1/1 и Мстиславца 6. Есть онлайн-формат.\n\n"
            "Основные направления:\n"
            "- Photoshop\n"
            "- WordPress\n"
            "- HTML/CSS/JavaScript\n"
            "- GDevelop 5\n"
            "- Roblox\n"
            "- Blender\n"
            "- Figma\n"
            "- Python\n"
            "- Telegram-боты на Python\n"
            "- Blockbench / Roblox 3D\n\n"
            "Для подбора направления нажмите 🧭 Подбор направления или опишите возраст, интересы и опыт ребёнка."
        )

    def _manager_trials_text(self) -> str:
        trials = self.storage.list_trials("open", 20)
        if not trials:
            return (
                "👥 Открытых заявок на пробное сейчас нет.\n\n"
                "Если родитель пишет в чат, агент может помочь собрать заявку.\n"
                "Нужно уточнить: возраст ребёнка, направление, онлайн/офлайн, удобный адрес, дни/время и контакт."
            )
        return "👥 Открытые заявки на пробное:\n\n" + "\n\n---\n\n".join(format_trial_for_admin(t) for t in trials)

    def _moyklass_menu_text(self, user_id: int) -> str:
        role = self.admin.get_role(user_id) or "other"
        lines = [
            "МойКласс API.",
            "",
            f"Статус ключа: {'указан' if self.settings.moyklass_enabled else 'не указан'}",
            "Выберите действие кнопкой ниже.",
        ]
        if role == "owner":
            lines.extend([
                "",
                "Изменения в МойКласс доступны только через подготовку и подтверждение:",
                "1. Подготовить изменение.",
                "2. Проверить данные.",
                "3. Нажать ✅ Отправить изменение.",
            ])
        elif role in {"teacher", "methodist"}:
            teacher_id = self._mk_teacher_id_for_user(user_id)
            lines.extend([
                "",
                f"Ваша привязка teacherId: {teacher_id or 'не настроена'}",
                "Если teacherId привязан, бот покажет только ваши занятия.",
                "Комментарий и тему можно менять только в своих занятиях и только через подтверждение.",
            ])
        elif role == "manager":
            lines.extend([
                "",
                "Менеджеру доступны рабочие функции МойКласс:",
                "- просмотр расписания и карточек занятий;",
                "- поиск учеников без полного вывода контактов;",
                "- подготовка комментария, темы и времени занятия через подтверждение;",
                "- работа с заявками на пробное, ответами родителям и отработками.",
                "",
                "Добавление/удаление учеников из групп добавим отдельным этапом после проверки нужных endpoint API.",
            ])
        else:
            lines.append("\nДля вашей роли доступны только безопасные действия чтения.")
        return "\n".join(lines)

    async def _show_moyklass_menu(self, msg) -> None:
        user_id = msg.from_user.id if msg.from_user else 0
        role = self.admin.get_role(user_id) or "other"
        await msg.reply_text(self._moyklass_menu_text(user_id), reply_markup=moyklass_keyboard(role))

    async def _show_mk_pending(self, msg, user_id: int) -> None:
        pending = self.pending_mk_writes.get(user_id)
        if not pending:
            await self._reply(msg, "Нет подготовленного изменения.", reply_markup=moyklass_keyboard(self.admin.get_role(user_id) or "other"))
            return
        await self._reply(msg, "Подготовленное изменение:\n" + "\n".join([
            f"Действие: {pending.get('action')}",
            f"Объект: {pending.get('object_type')} {pending.get('object_id')}",
            f"Запрос: {pending.get('method')} {pending.get('path')}",
            "Тело:",
            self._compact_payload(pending.get('payload') or {}),
            "",
            "Отправить: ✅ Отправить изменение или /mk_confirm",
            "Отменить: ❌ Отменить изменение или /mk_cancel",
        ]), reply_markup=moyklass_keyboard(self.admin.get_role(user_id) or "other"))

    def _mk_teacher_id_for_user(self, user_id: int | None) -> str:
        if user_id and self.admin.is_real_admin(user_id):
            test = self.storage.get_staff_test_mode(user_id)
            if test.get("enabled") and test.get("mk_teacher_id"):
                return str(test.get("mk_teacher_id") or "").strip()
        return self.storage.get_staff_mk_teacher_id(user_id)

    def _test_mode_summary(self, user_id: int) -> str:
        test = self.storage.get_staff_test_mode(user_id)
        if not test.get("enabled"):
            return "Тестовый режим выключен."
        return (
            "Тестовый режим включён.\n"
            f"Роль: {self.admin.role_label(test.get('role') or 'other')}\n"
            f"Тестовый teacherId: {test.get('mk_teacher_id') or '-'}\n\n"
            "Выключить: /test_as_off"
        )

    def _moyklass_binding_text(self, user_id: int) -> str:
        role = self.admin.get_role(user_id) or "other"
        teacher_id = self._mk_teacher_id_for_user(user_id)
        if teacher_id:
            return (
                f"Ваша роль: {self.admin.role_label(role)}\n"
                f"Привязка МойКласс: teacherId {teacher_id}\n\n"
                "По этой привязке бот показывает ваши занятия и разрешает безопасно менять комментарий/тему только в ваших занятиях."
            )
        return (
            f"Ваша роль: {self.admin.role_label(role)}\n"
            "Привязка к МойКласс пока не настроена.\n\n"
            "Попросите владельца выполнить команду:\n"
            "/mk_link_teacher ВАШ_TELEGRAM_ID MK_TEACHER_ID\n\n"
            "teacherId можно взять из карточки занятия МойКласс: строка teacherIds."
        )

    def _filter_own_lessons(self, user_id: int, result):
        teacher_id = self._mk_teacher_id_for_user(user_id)
        return filter_lessons_by_teacher(result, teacher_id)

    def _lesson_result_belongs_to_user(self, user_id: int, result) -> bool:
        if self.admin.is_admin(user_id) or self.admin.get_role(user_id) in {"owner", "operations"}:
            return True
        teacher_id = self._mk_teacher_id_for_user(user_id)
        if not teacher_id or not result.ok:
            return False
        item = self.moyklass._extract_lesson_item(result.data)
        return bool(item and lesson_has_teacher(item, teacher_id))

    async def _reply_need_mk_teacher_link(self, msg, user_id: int, role: str) -> None:
        await self._reply(msg, self._moyklass_binding_text(user_id), reply_markup=moyklass_keyboard(role))

    async def _show_lessons_with_open_buttons(self, msg, user_id: int, result, *, title: str, limit: int = 40) -> None:
        role = self.admin.get_role(user_id) or "other"
        items = [x for x in extract_items(result.data) if isinstance(x, dict)] if result and result.ok else []
        buttons: list[str] = []
        mapping: dict[str, str] = {}
        for idx, item in enumerate(items[:limit], start=1):
            lesson_id = extract_lesson_id_from_result(type(result)(True, data=item, status=result.status, endpoint=result.endpoint))
            if not lesson_id:
                continue
            label = lesson_open_button_label(item, idx)
            # Avoid duplicate button labels if two lessons look similar.
            if label in mapping:
                label = f"{label} #{lesson_id}"
            buttons.append(label)
            mapping[label] = lesson_id

        text = format_lessons_result(result, limit=limit, title=title, include_open_hint=False)
        if buttons and msg.chat.type == "private":
            selected = self._selected_lesson_id(user_id)
            state = {"mode": "mk_lesson_list_waiting", "lesson_buttons": mapping}
            if selected:
                state["selected_lesson_id"] = selected
            self.private_states[user_id] = state
            await self._reply(msg, text + "\n\nВыберите занятие кнопкой ниже.", reply_markup=lesson_list_keyboard(buttons, role))
        else:
            await self._reply(msg, text, reply_markup=moyklass_keyboard(role))

    def _lesson_is_from_now(self, item: dict) -> bool:
        """Keep lessons from current moment forward in Yellow Club timezone (UTC+3)."""
        if not isinstance(item, dict):
            return False
        raw_date = (_lesson_date_value(item) or "")[:10]
        raw_time = _lesson_time_value(item) or ""
        now = datetime.utcnow() + timedelta(hours=3)
        try:
            lesson_date = date.fromisoformat(raw_date) if raw_date else None
        except Exception:
            lesson_date = None
        if lesson_date is None:
            return True
        if lesson_date > now.date():
            return True
        if lesson_date < now.date():
            return False
        start_t, end_t = self._parse_lesson_time_range(raw_time)
        if end_t:
            return now <= datetime.combine(now.date(), end_t)
        if start_t:
            return now <= datetime.combine(now.date(), start_t)
        # If time is missing but date is today, keep it visible.
        return True

    def _filter_lessons_from_now(self, result):
        """Return a MoyKlassResult-like object with past lessons removed."""
        if not result or not result.ok:
            return result
        try:
            items = [x for x in extract_items(result.data) if isinstance(x, dict)]
            items = [x for x in items if self._lesson_is_from_now(x)]
            data = result.data
            if isinstance(data, list):
                new_data = items
            elif isinstance(data, dict):
                new_data = dict(data)
                replaced = False
                for key in ("items", "lessons", "data", "result", "records"):
                    if isinstance(new_data.get(key), list):
                        new_data[key] = items
                        replaced = True
                        break
                if not replaced:
                    new_data["items"] = items
            else:
                new_data = items
            return type(result)(True, data=new_data, status=result.status, endpoint=result.endpoint, error=getattr(result, "error", ""))
        except Exception:
            return result

    async def _show_my_lessons(self, msg, user_id: int, *, days: int = 7) -> None:
        role = self.admin.get_role(user_id) or "other"
        teacher_id = self._mk_teacher_id_for_user(user_id)
        if not teacher_id:
            await self._reply_need_mk_teacher_link(msg, user_id, role)
            return
        days = max(1, int(days or 7))
        result = self.moyklass.get_upcoming_lessons(days=days, limit=300)
        filtered = filter_lessons_by_teacher(result, teacher_id)
        filtered = self._filter_lessons_from_now(filtered)
        title = f"Мои занятия на ближайшие {days} дней" if days != 1 else "Мои занятия с текущего момента"
        await self._show_lessons_with_open_buttons(msg, user_id, filtered, title=title, limit=60)

    def _today_iso(self) -> str:
        return date.today().isoformat()

    def _teacher_control_user_id(self, user_id: int | None) -> int | None:
        if not user_id:
            return None
        role = self.admin.get_role(user_id) or ""
        if role in {"teacher", "methodist"}:
            return int(user_id)
        return None

    def _lesson_fields_from_item(self, item: dict, user_id: int | None = None) -> dict:
        """Small normalized snapshot for local teacher day control."""
        if not isinstance(item, dict):
            return {}
        teacher_ids = item.get("teacherIds")
        if isinstance(teacher_ids, list) and teacher_ids:
            mk_teacher_id = str(teacher_ids[0])
        else:
            mk_teacher_id = _pick(item, ("teacherId",))
        return {
            "lesson_date": (_lesson_date_value(item) or self._today_iso())[:10],
            "lesson_time": _lesson_time_value(item),
            "group_name": _lesson_group_value(item),
            "lesson_topic": _pick(item, ("topic", "name", "title", "subjectName", "courseName", "description")),
            "teacher_user_id": int(user_id) if user_id else None,
            "teacher_name": _lesson_teacher_value(item),
            "mk_teacher_id": mk_teacher_id,
        }


    def _teacher_profile_label(self, status: str) -> str:
        labels = {
            "trainee": "стажёр",
            "trial_allowed": "допущен к пробным",
            "regular_allowed": "допущен к регулярным",
            "active": "действующий преподаватель",
            "paused": "временно не работает",
            "substitute": "доступен на замены",
        }
        return labels.get((status or "").strip().lower(), status or "-")

    def _format_teacher_profiles_text(self, rows: list[dict]) -> str:
        if not rows:
            return (
                "👤 Профили преподавателей пока не созданы.\n\n"
                "Создать профили для текущих преподавателей: /teacher_profiles_init\n"
                "Назначить статус вручную: /set_teacher_status USER_ID active"
            )
        lines = ["👤 Профили преподавателей", ""]
        for r in rows[:80]:
            name = r.get("full_name") or r.get("username") or r.get("mk_teacher_name") or str(r.get("user_id"))
            status = self._teacher_profile_label(str(r.get("teacher_status") or ""))
            profile_type = r.get("profile_type") or "-"
            mk = r.get("mk_teacher_id") or "-"
            flags = []
            if int(r.get("can_trial") or 0):
                flags.append("пробные")
            if int(r.get("can_regular") or 0):
                flags.append("регулярные")
            if int(r.get("can_substitute") or 0):
                flags.append("замены")
            lines.append(f"{r.get('user_id')} | {name}")
            lines.append(f"  статус: {status} | тип: {profile_type} | teacherId: {mk}")
            lines.append(f"  допуски: {', '.join(flags) if flags else '-'}")
            if r.get("location"):
                lines.append(f"  локация: {r.get('location')}")
            if r.get("notes"):
                lines.append(f"  заметка: {str(r.get('notes'))[:120]}")
            lines.append("")
        lines.append("Команды:")
        lines.append("/set_teacher_status USER_ID active")
        lines.append("/set_teacher_status USER_ID trainee")
        lines.append("/set_teacher_status USER_ID substitute")
        return "\n".join(lines).strip()

    def _format_teacher_tasks_text(self, rows: list[dict], *, title: str = "📌 Задачи") -> str:
        if not rows:
            return f"{title}\n\nОткрытых задач нет."
        lines = [title, ""]
        for r in rows[:80]:
            pr = str(r.get("priority") or "normal")
            icon = "🔴" if pr in {"urgent", "high"} else "🟡"
            due = r.get("due_at") or "без срока"
            lines.append(f"{icon} #{r.get('id')} | {r.get('title')}")
            lines.append(f"Тип: {r.get('task_type')} | срок: {due}")
            if r.get("lesson_id"):
                lines.append(f"Занятие: #{r.get('lesson_id')}")
            if r.get("text"):
                lines.append(str(r.get("text"))[:700])
            lines.append(f"Закрыть задачу: /task_done {r.get('id')}")
            lines.append("")
        return "\n".join(lines).strip()

    def _teacher_ids_from_item(self, item: dict) -> list[str]:
        raw = item.get("teacherIds")
        ids: list[str] = []
        if isinstance(raw, list):
            ids.extend(str(x).strip() for x in raw if str(x).strip())
        else:
            one = _pick(item, ("teacherId", "teacher_id"))
            if one:
                ids.append(str(one).strip())
        # Some enriched lesson payloads can contain teacher objects.
        teachers = item.get("teachers") or item.get("teacher")
        if isinstance(teachers, list):
            for t in teachers:
                if isinstance(t, dict):
                    tid = _pick(t, ("id", "teacherId"))
                    if tid:
                        ids.append(str(tid).strip())
        elif isinstance(teachers, dict):
            tid = _pick(teachers, ("id", "teacherId"))
            if tid:
                ids.append(str(tid).strip())
        out: list[str] = []
        for tid in ids:
            if tid and tid not in out:
                out.append(tid)
        return out

    def _schedule_snapshot_from_item(self, item: dict) -> dict:
        teacher_ids = self._teacher_ids_from_item(item)
        teacher_name = _lesson_teacher_value(item) or ""
        payload = {
            "lesson_date": (_lesson_date_value(item) or "")[:10],
            "lesson_time": _lesson_time_value(item) or "",
            "group_name": _lesson_group_value(item) or "",
            "lesson_topic": _pick(item, ("topic", "name", "title", "subjectName", "courseName", "description")) or "",
            "teacher_ids": ",".join(teacher_ids),
            "teacher_names": teacher_name,
        }
        fingerprint_source = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        payload["fingerprint"] = hashlib.sha1(fingerprint_source.encode("utf-8")).hexdigest()
        payload["raw_preview"] = json.dumps(item, ensure_ascii=False)[:3000]
        return payload

    def _ops_lesson_summary(self, snapshot: dict) -> str:
        return "\n".join([
            f"Группа: {snapshot.get('group_name') or '-'}",
            f"Дата: {snapshot.get('lesson_date') or '-'}",
            f"Время: {snapshot.get('lesson_time') or '-'}",
            f"Тема: {snapshot.get('lesson_topic') or '-'}",
            f"teacherId: {snapshot.get('teacher_ids') or '-'}",
        ])

    def _prep_due_date(self, lesson_date: str) -> str:
        try:
            d = date.fromisoformat(str(lesson_date or "")[:10])
            return (d - timedelta(days=3)).isoformat()
        except Exception:
            return ""

    async def _send_ops_notification(self, context: ContextTypes.DEFAULT_TYPE, user_id: int | None, text: str, *, event_type: str, lesson_id: str = "", task_id: int | None = None) -> bool:
        if not user_id:
            self.storage.add_notification_log(event_type, text, None, lesson_id, task_id, False, "no_user_id")
            return False
        try:
            await context.bot.send_message(chat_id=int(user_id), text=text)
            self.storage.add_notification_log(event_type, text, int(user_id), lesson_id, task_id, True, "")
            return True
        except Exception as e:
            self.storage.add_notification_log(event_type, text, int(user_id), lesson_id, task_id, False, str(e)[:500])
            return False

    async def _mk_schedule_check(self, context: ContextTypes.DEFAULT_TYPE, *, days: int = 14, notify: bool = False) -> dict:
        result = self.moyklass.get_upcoming_lessons(days=max(1, min(int(days or 14), 120)), limit=1000)
        if not result.ok:
            return {"ok": False, "error": result.error or f"status={result.status}", "new": [], "changed": [], "unchanged": 0, "tasks": []}
        items = [x for x in extract_items(result.data) if isinstance(x, dict)]
        created_tasks: list[dict] = []
        new_events: list[dict] = []
        changed_events: list[dict] = []
        unchanged = 0
        for item in items:
            lesson_id = str(_pick(item, ("id", "lessonId")) or "").strip()
            if not lesson_id:
                continue
            snap = self._schedule_snapshot_from_item(item)
            event, previous, current = self.storage.upsert_lesson_snapshot(lesson_id, snap)
            teacher_ids = [x for x in str(current.get("teacher_ids") or "").split(",") if x]
            if event == "unchanged":
                unchanged += 1
                continue
            if event == "new":
                new_events.append(current)
            elif event == "changed":
                changed_events.append(current)
            for mk_teacher_id in teacher_ids or [""]:
                staff = self.storage.get_staff_by_mk_teacher_id(mk_teacher_id) if mk_teacher_id else None
                user_id = int(staff.get("user_id")) if staff and staff.get("user_id") else None
                title = "Новое занятие в МойКласс" if event == "new" else "Изменение занятия в МойКласс"
                task = self.storage.upsert_teacher_task(
                    task_key=f"{event}_lesson:{lesson_id}:{mk_teacher_id or 'unknown'}",
                    task_type="new_lesson" if event == "new" else "changed_lesson",
                    title=title,
                    text=self._ops_lesson_summary(current),
                    user_id=user_id,
                    mk_teacher_id=mk_teacher_id,
                    lesson_id=lesson_id,
                    due_at=str(current.get("lesson_date") or ""),
                    priority="high" if event == "changed" else "normal",
                    source="moyklass_schedule_check",
                )
                created_tasks.append(task)
                prep_task = self.storage.upsert_teacher_task(
                    task_key=f"prepare_lesson:{lesson_id}:{mk_teacher_id or 'unknown'}",
                    task_type="prepare_lesson",
                    title="Подготовиться к занятию",
                    text="Изучить материал в Notion, посмотреть видео, выполнить практику и при необходимости отправить результат старшему преподавателю.\n\n" + self._ops_lesson_summary(current),
                    user_id=user_id,
                    mk_teacher_id=mk_teacher_id,
                    lesson_id=lesson_id,
                    due_at=self._prep_due_date(str(current.get("lesson_date") or "")),
                    priority="normal",
                    source="moyklass_schedule_check",
                )
                created_tasks.append(prep_task)
                if notify and user_id:
                    notify_text = (
                        ("📌 Вам добавлено новое занятие" if event == "new" else "⚠️ Изменение в вашем занятии")
                        + "\n\n" + self._ops_lesson_summary(current)
                        + "\n\nПроверьте расписание и подготовку в кабинете преподавателя."
                    )
                    await self._send_ops_notification(context, user_id, notify_text, event_type=event, lesson_id=lesson_id, task_id=task.get("id"))
                if notify and self.settings.mk_watch_notify_admins and event in {"new", "changed"}:
                    admin_text = (
                        ("📌 Новое занятие в МойКласс" if event == "new" else "⚠️ Изменение занятия в МойКласс")
                        + "\n\n" + self._ops_lesson_summary(current)
                    )
                    for admin_id in self.settings.admin_ids:
                        if user_id and int(admin_id) == int(user_id):
                            continue
                        await self._send_ops_notification(context, int(admin_id), admin_text, event_type=f"admin_{event}", lesson_id=lesson_id, task_id=task.get("id"))
        return {"ok": True, "total": len(items), "new": new_events, "changed": changed_events, "unchanged": unchanged, "tasks": created_tasks}

    async def _auto_schedule_watch_loop(self, app) -> None:
        """Periodically compare MoyKlass schedule and notify about new/changed lessons."""
        try:
            await asyncio.sleep(int(self.settings.mk_watch_initial_delay_seconds))
            has_baseline = bool(self.storage.list_lesson_snapshots(limit=1))
            if not has_baseline:
                data = await self._mk_schedule_check(app, days=self.settings.mk_watch_days, notify=False)
                log.info(
                    "MoyKlass watcher baseline created: ok=%s total=%s new=%s changed=%s",
                    data.get("ok"), data.get("total"), len(data.get("new") or []), len(data.get("changed") or []),
                )
            while True:
                await asyncio.sleep(int(self.settings.mk_watch_interval_minutes) * 60)
                data = await self._mk_schedule_check(app, days=self.settings.mk_watch_days, notify=True)
                log.info(
                    "MoyKlass watcher check: ok=%s total=%s new=%s changed=%s tasks=%s",
                    data.get("ok"), data.get("total"), len(data.get("new") or []), len(data.get("changed") or []), len(data.get("tasks") or []),
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("MoyKlass auto watcher crashed")

    def _format_schedule_check_text(self, data: dict) -> str:
        if not data.get("ok"):
            return f"⚠️ Проверка расписания не выполнена\n\n{data.get('error') or 'unknown error'}"
        lines = ["📡 Проверка расписания МойКласс", ""]
        lines.append(f"Занятий проверено: {data.get('total', 0)}")
        lines.append(f"Новых: {len(data.get('new') or [])}")
        lines.append(f"Изменённых: {len(data.get('changed') or [])}")
        lines.append(f"Без изменений: {data.get('unchanged', 0)}")
        lines.append(f"Задач создано/обновлено: {len(data.get('tasks') or [])}")
        if data.get("new"):
            lines.append("\n📌 Новые занятия:")
            for s in (data.get("new") or [])[:10]:
                lines.append(f"- {s.get('lesson_date')} {s.get('lesson_time')} | {s.get('group_name')} | {s.get('lesson_topic') or 'тема не указана'}")
        if data.get("changed"):
            lines.append("\n⚠️ Изменённые занятия:")
            for s in (data.get("changed") or [])[:10]:
                lines.append(f"- {s.get('lesson_date')} {s.get('lesson_time')} | {s.get('group_name')} | {s.get('lesson_topic') or 'тема не указана'}")
        return "\n".join(lines).strip()

    def _parse_lesson_time_range(self, value: str | None) -> tuple[time | None, time | None]:
        text = str(value or "").strip()
        m = re.search(r"(\d{1,2})[:.](\d{2})(?:\s*[-–—]\s*(\d{1,2})[:.](\d{2}))?", text)
        if not m:
            return None, None
        try:
            start = time(int(m.group(1)), int(m.group(2)))
            end = time(int(m.group(3)), int(m.group(4))) if m.group(3) else None
            return start, end
        except Exception:
            return None, None

    def _lesson_time_gate(self, control: dict | None) -> dict:
        """Return availability flags for preparation/closing buttons.

        The bot should not show closing actions before the lesson actually ends.
        If date/time cannot be parsed, closing stays blocked to avoid accidental false confirmations.
        """
        c = control or {}
        raw_date = str(c.get("lesson_date") or "").strip()[:10]
        raw_time = str(c.get("lesson_time") or "").strip()
        start_t, end_t = self._parse_lesson_time_range(raw_time)
        # Yellow Club works in Belarus/Moscow time (UTC+3).
        now = datetime.utcnow() + timedelta(hours=3)
        today = now.date()
        lesson_date = None
        try:
            if raw_date:
                lesson_date = date.fromisoformat(raw_date)
        except Exception:
            lesson_date = None

        if lesson_date is None:
            return {
                "known": False,
                "phase": "unknown",
                "can_prepare": True,
                "can_close": False,
                "note": "Не удалось определить дату занятия. Закрытие недоступно, пока дата/время не будут понятны.",
            }
        if lesson_date > today:
            return {
                "known": True,
                "phase": "future",
                "can_prepare": True,
                "can_close": False,
                "note": "Занятие ещё не прошло. Сейчас доступны только подготовка и материал занятия.",
            }
        if lesson_date < today:
            return {
                "known": True,
                "phase": "past",
                "can_prepare": True,
                "can_close": True,
                "note": "Занятие уже прошло. Доступно закрытие занятия.",
            }
        # Today. If end time exists, closing is available only after lesson end.
        if end_t:
            end_dt = datetime.combine(today, end_t)
            if now >= end_dt:
                return {"known": True, "phase": "ended", "can_prepare": True, "can_close": True, "note": "Занятие уже закончилось. Доступно закрытие."}
            if start_t and now >= datetime.combine(today, start_t):
                return {"known": True, "phase": "in_progress", "can_prepare": True, "can_close": False, "note": "Занятие сейчас идёт. Закрытие появится после окончания занятия."}
            return {"known": True, "phase": "today_before", "can_prepare": True, "can_close": False, "note": "Занятие сегодня, но ещё не закончилось. Сейчас доступны подготовка и материал."}
        if start_t:
            start_dt = datetime.combine(today, start_t)
            if now < start_dt:
                return {"known": True, "phase": "today_before", "can_prepare": True, "can_close": False, "note": "Занятие сегодня, но ещё не началось. Сейчас доступны подготовка и материал."}
            # No end time - allow closing one hour after start as a fallback.
            return {"known": True, "phase": "in_progress_unknown_end", "can_prepare": True, "can_close": False, "note": "Не вижу время окончания занятия, поэтому закрытие заблокировано. Уточните время в МойКласс."}
        return {
            "known": True,
            "phase": "unknown_time",
            "can_prepare": True,
            "can_close": False,
            "note": "Не удалось определить время занятия. Закрытие недоступно, пока время не будет указано.",
        }

    def _teacher_day_next_close_step(self, control: dict | None) -> tuple[str, str]:
        c = control or {}
        if str(c.get("parent_report_status") or "") != "done":
            return "parent_report", "Сначала сформируйте и отправьте отчёт родителям."
        if str(c.get("myclass_status") or "") != "done":
            return "myclass", "Далее проверьте и подтвердите заполнение МойКласс."
        if str(c.get("works_status") or "") != "done":
            return "works", "Далее подтвердите, что работы учеников сохранены."
        if str(c.get("classroom_status") or "") != "done":
            return "classroom", "Далее подтвердите, что кабинет и рабочие места в порядке."
        return "close", "Все обязательные пункты отмечены. Можно закрыть занятие."

    def _teacher_status_icons(self, control: dict | None) -> tuple[str, str, str, str]:
        c = control or {}
        prep = str(c.get("preparation_status") or "not_started")
        lesson = str(c.get("lesson_status") or "not_started")
        problem = str(c.get("problem_status") or "none")
        prep_icon = "🟢" if prep == "ready" else ("🔴" if prep in {"needs_help", "not_ready"} else "🟡")
        close_icon = "✅" if lesson == "closed" else ("🔴" if problem == "problem" or lesson == "problem" else "🟡")
        problem_icon = "🔴" if problem == "problem" else "⚪"
        if lesson == "closed":
            status = "закрыто"
        elif problem == "problem":
            status = "есть проблема"
        elif prep == "ready":
            status = "готово к проведению"
        else:
            status = "требует контроля"
        return prep_icon, close_icon, problem_icon, status

    async def _get_teacher_period_items(self, user_id: int, *, days: int = 7, allow_owner_all: bool = False) -> tuple[object, list[dict]]:
        role = self.admin.get_role(user_id) or "other"
        days = max(1, int(days or 7))
        result = self.moyklass.get_upcoming_lessons(days=days, limit=300)
        if role in {"teacher", "methodist"} or (self.admin.is_real_admin(user_id) and self.storage.get_staff_test_mode(user_id).get("enabled")):
            teacher_id = self._mk_teacher_id_for_user(user_id)
            if teacher_id:
                result = filter_lessons_by_teacher(result, teacher_id)
        elif not (allow_owner_all and (self.admin.is_admin(user_id) or role in {"owner", "operations"})):
            teacher_id = self._mk_teacher_id_for_user(user_id)
            if teacher_id:
                result = filter_lessons_by_teacher(result, teacher_id)
        result = self._filter_lessons_from_now(result)
        items = [x for x in extract_items(result.data) if isinstance(x, dict)] if result and result.ok else []
        return result, items

    async def _get_teacher_today_items(self, user_id: int, *, allow_owner_all: bool = False) -> tuple[object, list[dict]]:
        return await self._get_teacher_period_items(user_id, days=1, allow_owner_all=allow_owner_all)

    def _remember_day_lessons(self, user_id: int, items: list[dict]) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for idx, item in enumerate(items, start=1):
            lesson_id = str(_pick(item, ("id", "lessonId"))).strip()
            if not lesson_id:
                continue
            fields = self._lesson_fields_from_item(item, user_id=self._teacher_control_user_id(user_id))
            self.storage.upsert_teacher_lesson_control(lesson_id, **fields)
            label = lesson_open_button_label(item, idx)
            if label in mapping:
                label = f"{label} #{lesson_id}"
            mapping[label] = lesson_id
        return mapping

    def _lesson_material_query_from_item(self, item: dict | None) -> str:
        if not isinstance(item, dict):
            return ""
        values: list[str] = []
        # Prefer the human topic/title fields, then course/group context. This reduces
        # wrong Notion matches when different courses have the same topic number.
        for key in (
            "topic", "topicName", "lessonTopic", "subjectName", "courseName",
            "name", "title", "description", "className", "groupName", "group",
        ):
            value = _pick(item, (key,))
            if value and str(value).strip() not in values:
                values.append(str(value).strip())
        return " ".join(values).strip()

    def _teacher_quick_help_text(self) -> str:
        return (
            "❓ Как пользоваться кабинетом преподавателя\n\n"
            "Главная кнопка - 📅 Мои занятия. Она показывает ваши занятия на ближайшие 7 дней с текущего момента.\n\n"
            "1. Нажмите 📅 Мои занятия.\n"
            "2. Выберите конкретное занятие из списка.\n"
            "3. Если занятие ещё не прошло, будут доступны только подготовка:\n"
            "   - 📘 Материал выбранного\n"
            "   - ✅ Тема изучена, я готов\n"
            "   - ⚠️ Нужна помощь с темой\n\n"
            "4. Когда занятие уже прошло, появится закрытие занятия:\n"
            "   - отчёт родителям\n"
            "   - МойКласс\n"
            "   - работы учеников\n"
            "   - кабинет\n\n"
            "📌 Мои задачи - показывает уведомления и задачи, которые агент создал по МойКласс.\n"
            "🌙 Закрыть рабочий день - используется в конце смены для занятий текущего дня.\n\n"
            "Если материал открылся не тот, напишите руководителю: возможно, в МойКласс тема указана неточно или в Notion у похожих тем одинаковые названия."
        )

    def _teacher_day_status_text(self, user_id: int, items: list[dict], *, title: str = "Мои занятия на ближайшие 7 дней") -> str:
        if not items:
            return f"📅 {title}\n\nЗанятий на ближайшую неделю с текущего момента не найдено."
        lines = [f"📅 {title}", ""]
        closed = 0
        ready = 0
        problems = 0
        for idx, item in enumerate(items, start=1):
            lesson_id = str(_pick(item, ("id", "lessonId"))).strip()
            fields = self._lesson_fields_from_item(item, user_id=self._teacher_control_user_id(user_id))
            control = self.storage.upsert_teacher_lesson_control(lesson_id, **fields) if lesson_id else {}
            prep_icon, close_icon, problem_icon, status = self._teacher_status_icons(control)
            if str(control.get("lesson_status") or "") == "closed":
                closed += 1
            if str(control.get("preparation_status") or "") == "ready":
                ready += 1
            if str(control.get("problem_status") or "") == "problem":
                problems += 1
            time_value = _lesson_time_value(item) or "время не указано"
            group = _lesson_group_value(item) or "группа не указана"
            topic = _pick(item, ("topic", "name", "title", "subjectName", "courseName", "description")) or "тема не указана"
            lines.extend([
                f"{idx}. {time_value} - {group}",
                f"   🏷 {topic}",
                f"   {prep_icon} подготовка | {close_icon} закрытие | {problem_icon} проблема",
                f"   Статус: {status}",
                "",
            ])
        lines.extend([
            "Итог дня:",
            f"- подготовка подтверждена: {ready}/{len(items)}",
            f"- занятия закрыты: {closed}/{len(items)}",
            f"- проблемы: {problems}",
            "",
            "Что делать дальше:",
            "1. Нажмите на конкретное занятие кнопкой ниже.",
            "2. До занятия откройте материал и подтвердите подготовку.",
            "3. После занятия появятся шаги закрытия.",
        ])
        return "\n".join(lines).strip()

    async def _show_teacher_day_overview(self, msg, user_id: int) -> None:
        role = self.admin.get_role(user_id) or "other"
        if role not in {"teacher", "methodist", "owner", "operations"} and not self.admin.is_admin(user_id):
            await self._reply(msg, "Мои занятия доступны преподавателю, методисту, операционному менеджеру и владельцу.", reply_markup=staff_keyboard(role))
            return
        if role in {"teacher", "methodist"} and not self._mk_teacher_id_for_user(user_id):
            await self._reply_need_mk_teacher_link(msg, user_id, role)
            return
        result, items = await self._get_teacher_period_items(user_id, days=7, allow_owner_all=True)
        if result and not result.ok:
            await self._reply(msg, f"Не удалось получить занятия из МойКласс. status={result.status}\n{result.error}", reply_markup=staff_keyboard(role))
            return
        mapping = self._remember_day_lessons(user_id, items)
        text = self._teacher_day_status_text(user_id, items, title="Мои занятия на ближайшие 7 дней")
        self.private_states[user_id] = {"mode": "teacher_day_overview", "lesson_buttons": mapping}
        await self._reply(msg, text, reply_markup=teacher_day_lesson_keyboard(list(mapping.keys()), show_finish_day=False) if mapping else staff_keyboard(role))

    async def _show_teacher_today_materials(self, msg, user_id: int) -> None:
        role = self.admin.get_role(user_id) or "other"
        result, items = await self._get_teacher_period_items(user_id, days=7, allow_owner_all=True)
        if result and not result.ok:
            await self._reply(msg, f"Не удалось получить занятия из МойКласс. status={result.status}\n{result.error}", reply_markup=staff_keyboard(role))
            return
        if not items:
            await self._reply(msg, "На ближайшую неделю занятий не найдено.", reply_markup=staff_keyboard(role))
            return
        lines = ["📘 Материалы на неделю", ""]
        for idx, item in enumerate(items, start=1):
            topic = _pick(item, ("topic", "name", "title", "subjectName", "courseName", "description"))
            material_query = self._lesson_material_query_from_item(item) or (topic or "")
            time_value = _lesson_time_value(item) or "время не указано"
            group = _lesson_group_value(item) or "группа не указана"
            lines.append(f"{idx}. {time_value} - {group}")
            if topic:
                match = self.core.kb.find_lesson_material(material_query, max_chars=1200)
                if match:
                    lines.append(f"   🏷 {topic}")
                    goal = ""
                    try:
                        sections = self.core._extract_lesson_sections(self.core._prepare_lesson_text_for_summary(match.text))
                        goal = self.core._short_section(sections.get("goal") or "", max_lines=1, max_chars=180)
                    except Exception:
                        goal = ""
                    if goal:
                        lines.append(f"   🎯 {goal}")
                    if match.notion_url:
                        lines.append(f"   🔗 {match.notion_url}")
                else:
                    lines.append(f"   🏷 {topic}")
                    lines.append("   ⚠️ Точный материал в Notion не найден. Уточните название темы.")
            else:
                lines.append("   ⚠️ В МойКласс не указана тема занятия.")
            lines.append("")
        lines.append("Для полной карточки выберите занятие в 📅 Мои занятия и нажмите 📘 Материал выбранного.")
        await self._reply(msg, "\n".join(lines).strip(), reply_markup=staff_keyboard(role))

    async def _start_teacher_day_prepare(self, msg, user_id: int) -> None:
        role = self.admin.get_role(user_id) or "other"
        result, items = await self._get_teacher_period_items(user_id, days=7, allow_owner_all=True)
        if not result.ok:
            await self._reply(msg, f"Не удалось получить занятия из МойКласс. status={result.status}\n{result.error}", reply_markup=staff_keyboard(role))
            return
        mapping = self._remember_day_lessons(user_id, items)
        text = self._teacher_day_status_text(user_id, items, title="Подготовка ко дню")
        text += "\n\nПеред подтверждением проверьте:\n1. Материалы и видео по темам открыты и понятны.\n2. Практические работы понятны.\n3. Понятно, что должно получиться у учеников.\n4. Понятно, что писать родителям вечером."
        self.private_states[user_id] = {"mode": "teacher_day_prepare", "lesson_buttons": mapping}
        await self._reply(msg, text, reply_markup=teacher_day_prepare_keyboard())

    async def _start_teacher_day_close(self, msg, user_id: int) -> None:
        role = self.admin.get_role(user_id) or "other"
        result, items = await self._get_teacher_today_items(user_id, allow_owner_all=True)
        if not result.ok:
            await self._reply(msg, f"Не удалось получить занятия из МойКласс. status={result.status}\n{result.error}", reply_markup=staff_keyboard(role))
            return
        mapping = self._remember_day_lessons(user_id, items)
        self.private_states[user_id] = {"mode": "teacher_day_close_list", "lesson_buttons": mapping}
        text = self._teacher_day_status_text(user_id, items, title="Закрытие рабочего дня")
        text += "\n\nВыберите занятие кнопкой ниже и закройте его по чек-листу. После всех занятий нажмите ✅ Завершить рабочий день."
        await self._reply(msg, text, reply_markup=teacher_day_lesson_keyboard(list(mapping.keys())) if mapping else staff_keyboard(role))

    def _teacher_day_lesson_status_text(self, lesson_id: str) -> str:
        control = self.storage.get_teacher_lesson_control(lesson_id) or {}
        prep_icon, close_icon, problem_icon, status = self._teacher_status_icons(control)
        gate = self._lesson_time_gate(control)
        step_key, step_text = self._teacher_day_next_close_step(control)
        header = "🌙 Закрытие занятия" if gate.get("can_close") else "📘 Подготовка к занятию"
        lines = [
            f"{header} #{lesson_id}",
            "",
            f"Группа: {control.get('group_name') or '-'}",
            f"Дата: {control.get('lesson_date') or '-'}",
            f"Время: {control.get('lesson_time') or '-'}",
            f"Тема: {control.get('lesson_topic') or '-'}",
            "",
            f"{prep_icon} Подготовка: {control.get('preparation_status') or 'not_started'}",
            f"{close_icon} Закрытие: {control.get('lesson_status') or 'not_started'}",
            f"📝 Отчёт родителям: {control.get('parent_report_status') or 'not_checked'}",
            f"🗂 МойКласс: {control.get('myclass_status') or 'not_checked'}",
            f"💾 Работы: {control.get('works_status') or 'not_checked'}",
            f"🏫 Кабинет: {control.get('classroom_status') or 'not_checked'}",
            f"{problem_icon} Проблемы: {control.get('problem_status') or 'none'}",
            "",
            str(gate.get("note") or ""),
        ]
        if gate.get("can_close"):
            lines.extend([
                "",
                f"Следующий шаг: {step_text}",
                "",
                "Бот показывает только следующий нужный шаг, чтобы не было лишних кнопок.",
            ])
        else:
            lines.extend([
                "",
                "До окончания занятия можно открыть материал, подтвердить подготовку или сообщить, что нужна помощь.",
            ])
        if control.get("problem_comment"):
            lines.extend(["", f"Комментарий/проблема: {control.get('problem_comment')}"])
        return "\n".join([x for x in lines if x is not None]).strip()

    async def _open_day_close_lesson(self, msg, user_id: int, lesson_id: str) -> None:
        role = self.admin.get_role(user_id) or "other"
        result = self.moyklass.get_lesson_by_id(lesson_id)
        if not self._lesson_result_belongs_to_user(user_id, result):
            await self._reply(msg, "Это занятие не относится к вашему доступу. Доступ закрыт.", reply_markup=staff_keyboard(role))
            return
        if result.ok:
            item = self.moyklass._extract_lesson_item(result.data)
            if item:
                self.storage.upsert_teacher_lesson_control(lesson_id, **self._lesson_fields_from_item(item, user_id=self._teacher_control_user_id(user_id)))
        control = self.storage.get_teacher_lesson_control(lesson_id) or {}
        gate = self._lesson_time_gate(control)
        if gate.get("can_close"):
            self.private_states[user_id] = {"mode": "teacher_day_close_lesson", "selected_lesson_id": lesson_id}
            await self._reply(msg, self._teacher_day_lesson_status_text(lesson_id), reply_markup=teacher_day_close_keyboard(control))
        else:
            self.private_states[user_id] = {"mode": "teacher_day_prepare_lesson", "selected_lesson_id": lesson_id}
            await self._reply(msg, self._teacher_day_lesson_status_text(lesson_id), reply_markup=teacher_day_prepare_lesson_keyboard())

    async def _finish_teacher_day(self, msg, user_id: int) -> None:
        role = self.admin.get_role(user_id) or "other"
        result, items = await self._get_teacher_today_items(user_id, allow_owner_all=False)
        if result and not result.ok:
            await self._reply(msg, f"Не удалось получить занятия из МойКласс. status={result.status}\n{result.error}", reply_markup=staff_keyboard(role))
            return
        self._remember_day_lessons(user_id, items)
        lesson_ids = [str(_pick(item, ("id", "lessonId"))).strip() for item in items if str(_pick(item, ("id", "lessonId"))).strip()]
        rows = [self.storage.get_teacher_lesson_control(x) or {} for x in lesson_ids]
        total = len(rows)
        closed = sum(1 for r in rows if str(r.get("lesson_status") or "") == "closed")
        ready = sum(1 for r in rows if str(r.get("preparation_status") or "") == "ready")
        reports = sum(1 for r in rows if str(r.get("parent_report_status") or "") == "done")
        myclass = sum(1 for r in rows if str(r.get("myclass_status") or "") == "done")
        works = sum(1 for r in rows if str(r.get("works_status") or "") == "done")
        classroom = sum(1 for r in rows if str(r.get("classroom_status") or "") == "done")
        problems = [r for r in rows if str(r.get("problem_status") or "") == "problem"]
        lines = ["🌙 Итог рабочего дня", ""]
        if total == 0:
            lines.append("На ближайшую неделю занятий не найдено.")
        else:
            ok = closed == total and reports == total and myclass == total and works == total and classroom == total
            lines.append("✅ День закрыт полностью." if ok else "⚠️ День закрыт не полностью.")
            lines.extend([
                "",
                f"Занятий: {total}",
                f"Подготовка: {ready}/{total}",
                f"Закрыто занятий: {closed}/{total}",
                f"Отчёты родителям: {reports}/{total}",
                f"МойКласс: {myclass}/{total}",
                f"Работы сохранены: {works}/{total}",
                f"Кабинет: {classroom}/{total}",
                f"Проблемы: {len(problems)}",
            ])
            missing = [r for r in rows if str(r.get("lesson_status") or "") != "closed"]
            if missing:
                lines.extend(["", "Что осталось закрыть:"])
                for r in missing[:10]:
                    lines.append(f"- {r.get('lesson_time') or '-'} | {r.get('group_name') or '-'} | {r.get('lesson_topic') or '-'}")
        self.private_states.pop(user_id, None)
        await self._reply(msg, "\n".join(lines).strip(), reply_markup=staff_keyboard(role))

    async def _show_teacher_day_control(self, msg, user_id: int) -> None:
        role = self.admin.get_role(user_id) or "other"
        if role not in {"owner", "methodist", "operations"} and not self.admin.is_admin(user_id):
            await self._reply(msg, "Контроль преподавателей доступен владельцу, методисту и операционному менеджеру.", reply_markup=staff_keyboard(role))
            return
        today = self._today_iso()
        rows = self.storage.list_teacher_lesson_control(lesson_date=today, limit=500)
        if not rows:
            await self._reply(msg, "📊 Контроль преподавателей\n\nНа ближайшую неделю локальных статусов пока нет. Откройте 📅 Мои занятия или попросите преподавателей открыть кабинет.", reply_markup=staff_keyboard(role))
            return
        total = len(rows)
        ready = sum(1 for r in rows if str(r.get("preparation_status") or "") == "ready")
        closed = sum(1 for r in rows if str(r.get("lesson_status") or "") == "closed")
        problems = [r for r in rows if str(r.get("problem_status") or "") == "problem"]
        lines = [
            "📊 Контроль преподавателей",
            "",
            f"Занятий в контроле: {total}",
            f"Подготовка подтверждена: {ready}/{total}",
            f"Закрыто занятий: {closed}/{total}",
            f"Проблемы: {len(problems)}",
            "",
            "Не закрыто:",
        ]
        not_closed = [r for r in rows if str(r.get("lesson_status") or "") != "closed"]
        if not_closed:
            for r in not_closed[:20]:
                lines.append(f"- {r.get('lesson_time') or '-'} | {r.get('group_name') or '-'} | {r.get('teacher_name') or '-'}")
                lines.append(f"  Тема: {r.get('lesson_topic') or '-'}")
                lines.append(f"  Подготовка: {r.get('preparation_status') or 'not_started'} | МК: {r.get('myclass_status') or 'not_checked'} | отчёт: {r.get('parent_report_status') or 'not_checked'}")
        else:
            lines.append("- нет")
        if problems:
            lines.extend(["", "Проблемы:"])
            for r in problems[:10]:
                lines.append(f"- {r.get('lesson_time') or '-'} | {r.get('group_name') or '-'}: {r.get('problem_comment') or 'без комментария'}")
        await self._reply(msg, "\n".join(lines).strip(), reply_markup=staff_keyboard(role))

    def _selected_lesson_id(self, user_id: int) -> str:
        state = self.private_states.get(user_id) or {}
        lesson_id = str(state.get("selected_lesson_id") or "").strip()
        return lesson_id if lesson_id.isdigit() else ""

    def _selected_lesson_help(self) -> str:
        return (
            "Сначала откройте карточку занятия.\n\n"
            "Как это сделать:\n"
            "1. Нажмите 📅 Мои занятия или 📆 Мои занятия на неделю.\n"
            "2. Нажмите кнопку нужного занятия под списком.\n\n"
            "После этого будут доступны кнопки: 🧾 Отчёт по выбранному, ✅ Чеклист после занятия, ✏️ Комментарий, 🏷 Тема."
        )

    def _after_lesson_checklist_text(self, lesson_text: str = "") -> str:
        lines = []
        if lesson_text:
            lines.append(lesson_text)
            lines.append("")
        lines.extend([
            "✅ Чеклист после занятия",
            "",
            "1. Отметить занятие в МойКласс как проведённое.",
            "2. Отметить присутствующих и отсутствующих учеников.",
            "3. В комментарии указать отсутствующих в формате:",
            "   Иван - отработка",
            "   Маша, Даша - отработка",
            "4. Поставить тему следующего занятия на неделю вперёд.",
            "   Формат: Курс - номер темы - название темы.",
            "5. Если тему не успели пройти до конца - добавить, где остановились.",
            "6. Сформировать и отправить отчёт в родительский чат.",
            "7. Сохранить работы учеников на Яндекс.Диск.",
            "8. Сделать/прикрепить фото учебного процесса для отчёта.",
            "9. Очистить рабочие столы на компьютерах и привести кабинет в порядок.",
            "",
            "Быстрые действия:",
            "- 🧾 Отчёт по выбранному",
            "- ✏️ Комментарий к занятию",
            "- 🏷 Тема занятия",
        ])
        return "\n".join(lines)

    def _after_lesson_start_text(self, lesson_text: str = "") -> str:
        lines = []
        if lesson_text:
            lines.append(lesson_text)
            lines.append("")
        lines.extend([
            "✅ Завершение занятия",
            "",
            "Шаг 1 из 3 - отсутствующие и отработки",
            "",
            "Напишите, кто отсутствовал, одним сообщением. Например:",
            "Иван, Маша",
            "",
            "Бот подготовит комментарий для МойКласс в формате:",
            "Иван - отработка",
            "Маша - отработка",
            "",
            "Если все были на занятии - нажмите ✅ Все присутствовали.",
        ])
        return "\n".join(lines)

    def _after_lesson_topic_text(self) -> str:
        return (
            "Шаг 2 из 3 - тема следующего занятия\n\n"
            "Напишите тему, которую нужно поставить в МойКласс.\n\n"
            "Формат по регламенту:\n"
            "Название курса - номер темы - название темы\n\n"
            "Пример:\n"
            "Python - 5 тема - Условия\n\n"
            "Если тему сейчас не нужно менять - нажмите ⏭ Не менять тему."
        )

    def _after_lesson_finish_text(self, lesson_id: str) -> str:
        return (
            "Шаг 3 из 3 - финальные действия\n\n"
            f"Занятие #{lesson_id} обработано. Проверьте, что выполнены оставшиеся пункты регламента:\n\n"
            "- занятие отмечено как проведённое в МойКласс;\n"
            "- присутствующие и отсутствующие отмечены;\n"
            "- работы учеников сохранены на Яндекс.Диск;\n"
            "- файлы названы уникально и подписаны именами учеников;\n"
            "- рабочие столы очищены;\n"
            "- кабинет приведён в порядок;\n"
            "- отчёт отправлен в родительский чат.\n\n"
            "Можно сразу сформировать отчёт по выбранному занятию кнопкой ниже."
        )

    def _normalize_absence_comment(self, text: str) -> str:
        raw = (text or "").strip()
        if not raw:
            return ""
        # If the teacher already wrote a ready comment, keep it almost as is.
        if "отработка" in raw.lower():
            return raw
        names = []
        for part in re.split(r"[,;\n]+", raw):
            name = part.strip(" -—–\t")
            if name:
                names.append(name)
        if not names:
            return ""
        return "\n".join(f"{name} - отработка" for name in names)

    def _lesson_current_comment(self, lesson_result) -> str:
        try:
            item = self.moyklass._extract_lesson_item(lesson_result.data) if lesson_result and lesson_result.ok else {}
            return str((item or {}).get("comment") or "").strip()
        except Exception:
            return ""

    def _merge_lesson_comment(self, current: str, addition: str) -> str:
        current = (current or "").strip()
        addition = (addition or "").strip()
        if not addition:
            return current
        if not current:
            return addition
        if addition.lower() in current.lower():
            return current
        return current + "\n" + addition

    async def _show_selected_lesson_material(self, msg, user_id: int) -> None:
        role = self.admin.get_role(user_id) or "other"
        lesson_id = self._selected_lesson_id(user_id)
        if not lesson_id:
            await self._reply(msg, self._selected_lesson_help(), reply_markup=staff_keyboard(role))
            return
        result = self.moyklass.get_lesson_by_id(lesson_id)
        if not self._lesson_result_belongs_to_user(user_id, result):
            await self._reply(msg, "Это занятие не относится к вашему доступу. Материал по нему открыть нельзя.", reply_markup=staff_keyboard(role))
            return
        topic = extract_lesson_topic_from_result(result)
        if not topic:
            await self._reply(msg, "В карточке занятия нет темы. Напишите запрос вручную: /lesson_material курс + номер темы.", reply_markup=staff_keyboard(role))
            return
        answer = self.core.build_lesson_material_answer(topic)
        await self._reply(msg, answer, reply_markup=staff_keyboard(role))

    async def _show_selected_lesson_report(self, msg, user_id: int) -> None:
        role = self.admin.get_role(user_id) or "other"
        lesson_id = self._selected_lesson_id(user_id)
        if not lesson_id:
            await self._reply(msg, self._selected_lesson_help(), reply_markup=moyklass_keyboard(role))
            return
        result = self.moyklass.get_lesson_by_id(lesson_id)
        if not self._lesson_result_belongs_to_user(user_id, result):
            await self._reply(msg, "Это занятие не относится к вашему доступу. Отчёт по нему сформировать нельзя.", reply_markup=moyklass_keyboard(role))
            return
        topic = extract_lesson_topic_from_result(result)
        if not topic:
            self.private_states[user_id] = {"mode": "parent_report_waiting", "selected_lesson_id": lesson_id}
            await self._reply(
                msg,
                "В карточке занятия нет темы. Напишите курс и тему вручную.\n\nНапример:\nPython 4 тема\nGDevelop 9 занятие",
                reply_markup=report_waiting_keyboard(),
            )
            return
        answer = self.core.build_parent_report("/parent_report " + topic)
        self.private_states[user_id] = {
            "mode": "parent_report_ready",
            "last_report": answer,
            "source_request": topic,
            "selected_lesson_id": lesson_id,
        }
        await self._reply(msg, answer, reply_markup=report_actions_keyboard())

    async def _show_selected_after_lesson_checklist(self, msg, user_id: int) -> None:
        role = self.admin.get_role(user_id) or "other"
        lesson_id = self._selected_lesson_id(user_id)
        if not lesson_id:
            self.private_states[user_id] = {"mode": "mk_checklist_waiting"}
            await self._reply(
                msg,
                "Введите ID занятия, по которому нужен чеклист.\nНапример: 16620686",
                reply_markup=moyklass_keyboard(role),
            )
            return
        result = self.moyklass.get_lesson_by_id(lesson_id)
        if not self._lesson_result_belongs_to_user(user_id, result):
            await self._reply(msg, "Это занятие не относится к вашему доступу. Чеклист по нему открыть нельзя.", reply_markup=moyklass_keyboard(role))
            return
        card = format_lesson_preview(result, title=f"Выбранное занятие #{lesson_id}")
        self.private_states[user_id] = {"mode": "mk_after_absents_waiting", "selected_lesson_id": lesson_id}
        await self._reply(msg, self._after_lesson_start_text(card), reply_markup=after_lesson_absents_keyboard())

    def _can_prepare_lesson_update(self, user_id: int, lesson_result, field: str) -> tuple[bool, str]:
        role = self.admin.get_role(user_id) or "other"
        if self.admin.is_admin(user_id) or role in {"owner", "manager", "operations"}:
            return True, role or "admin"
        if field not in {"comment", "topic"}:
            return False, "Преподаватель может менять только комментарий и тему своего занятия."
        if role not in {"teacher", "methodist"}:
            return False, "Изменение комментария и темы доступно владельцу, менеджеру, операционному менеджеру, преподавателю и методисту."
        teacher_id = self._mk_teacher_id_for_user(user_id)
        if not teacher_id:
            return False, "У вас не настроена привязка к teacherId МойКласс."
        if not lesson_result.ok:
            return False, "Не удалось получить занятие из МойКласс."
        item = self.moyklass._extract_lesson_item(lesson_result.data)
        if not item or not lesson_has_teacher(item, teacher_id):
            return False, "Это занятие не относится к вашему teacherId. Доступ закрыт."
        return True, "own_teacher_lesson"


    async def _handle_moyklass_button(self, msg, text: str) -> bool:
        user = msg.from_user
        if not user:
            return True
        user_id = user.id
        role = self.admin.get_role(user_id) or "other"

        if text == "⬅️ В меню":
            self.private_states.pop(user_id, None)
            await self.admin.show(msg)
            return True

        if text == "🗂 МойКласс":
            self.private_states.pop(user_id, None)
            await self._show_moyklass_menu(msg)
            return True

        if text == "🔗 Моя привязка МК":
            await self._reply(msg, self._moyklass_binding_text(user_id), reply_markup=moyklass_keyboard(role))
            return True

        if text in {"🧾 Отчёт по выбранному", "📝 Отчёт по выбранному"}:
            if not self.admin.can_make_parent_reports(user_id):
                await self._reply(msg, "Отчёты родителям доступны преподавателям, методистам и владельцу.", reply_markup=moyklass_keyboard(role))
                return True
            await self._show_selected_lesson_report(msg, user_id)
            return True

        if text == "📘 Материал выбранного":
            await self._show_selected_lesson_material(msg, user_id)
            return True

        if text == "✅ Чеклист после занятия":
            await self._show_selected_after_lesson_checklist(msg, user_id)
            return True

        if text == "⬅️ МойКласс":
            await self._show_moyklass_menu(msg)
            return True

        if text in {"📅 Мои занятия", "📅 Мои занятия сегодня"}:
            if role not in {"teacher", "methodist", "owner"} and not self.admin.is_admin(user_id):
                await self._reply(msg, "Мои занятия доступны преподавателю, методисту и владельцу.", reply_markup=moyklass_keyboard(role))
                return True
            if role == "owner" or self.admin.is_admin(user_id):
                result = self.moyklass.get_upcoming_lessons(days=7, limit=120)
                result = self._filter_lessons_from_now(result)
                await self._show_lessons_with_open_buttons(msg, user_id, result, title="Занятия на ближайшие 7 дней", limit=40)
                return True
            await self._show_my_lessons(msg, user_id, days=7)
            return True

        if text == "📆 Мои занятия на неделю":
            if role not in {"teacher", "methodist", "owner"} and not self.admin.is_admin(user_id):
                await self._reply(msg, "Мои занятия доступны преподавателю, методисту и владельцу.", reply_markup=moyklass_keyboard(role))
                return True
            if role == "owner" or self.admin.is_admin(user_id):
                result = self.moyklass.get_upcoming_lessons(days=7, limit=120)
                result = self._filter_lessons_from_now(result)
                await self._show_lessons_with_open_buttons(msg, user_id, result, title="Занятия на ближайшие 7 дней", limit=40)
                return True
            await self._show_my_lessons(msg, user_id, days=7)
            return True

        if text == "🧪 Проверить МК":
            if not self.admin.is_admin(user_id):
                await self._reply(msg, "Проверка подключения доступна только владельцу.", reply_markup=moyklass_keyboard(role))
                return True
            result = self.moyklass.test()
            if result.ok:
                await self._reply(msg, "МойКласс: подключение проверено.\n" + preview_json(result.data, 1500), reply_markup=moyklass_keyboard(role))
            else:
                await self._reply(msg, f"МойКласс: ошибка подключения.\nstatus={result.status}\n{result.error}", reply_markup=moyklass_keyboard(role))
            return True

        if text == "📅 Занятия сегодня":
            if not self._can_read_moyklass_schedule(user_id):
                await self._reply(msg, "Расписание МойКласс доступно владельцу, менеджеру, методисту и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
                return True
            result = self.moyklass.get_upcoming_lessons(days=1, limit=80)
            await self._show_lessons_with_open_buttons(msg, user_id, result, title="Занятия на сегодня", limit=30)
            return True

        if text == "📆 Занятия на неделю":
            if not self._can_read_moyklass_schedule(user_id):
                await self._reply(msg, "Расписание МойКласс доступно владельцу, менеджеру, методисту и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
                return True
            result = self.moyklass.get_upcoming_lessons(days=7, limit=120)
            await self._show_lessons_with_open_buttons(msg, user_id, result, title="Занятия на ближайшие 7 дней", limit=40)
            return True

        if text == "📊 Отчёт МК":
            if not (self.admin.is_admin(user_id) or role in {"owner", "manager", "operations"}):
                await self._reply(msg, "Отчёт МойКласс доступен владельцу, менеджеру и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
                return True
            result = self.moyklass.get_month_analytics(None)
            await self._reply(msg, format_month_analytics_result(result), reply_markup=moyklass_keyboard(role))
            return True

        if text == "🧾 Посещения":
            if not (self.admin.is_admin(user_id) or role in {"owner", "manager", "operations"}):
                await self._reply(msg, "Посещения доступны владельцу, менеджеру и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
                return True
            result = self.moyklass.get_month_lesson_records(None)
            await self._reply(msg, format_lesson_records_result(result), reply_markup=moyklass_keyboard(role))
            return True

        if text == "💳 Оплаты":
            if not (self.admin.is_admin(user_id) or role in {"owner", "manager", "operations"}):
                await self._reply(msg, "Оплаты доступны владельцу, менеджеру и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
                return True
            result = self.moyklass.get_month_payments(None)
            await self._reply(msg, format_payments_result(result), reply_markup=moyklass_keyboard(role))
            return True

        if text == "🧑‍🎓 Активные клиенты":
            if not (self.admin.is_admin(user_id) or role in {"owner", "manager", "operations"}):
                await self._reply(msg, "Активные клиенты доступны владельцу, менеджеру и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
                return True
            result = self.moyklass.get_month_lesson_records(None)
            await self._reply(msg, format_active_clients_result(result), reply_markup=moyklass_keyboard(role))
            return True

        if text == "📋 Задачи МК":
            if not (self.admin.is_admin(user_id) or role in {"owner", "manager", "operations"}):
                await self._reply(msg, "Задачи МойКласс доступны владельцу, менеджеру и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
                return True
            result = self.moyklass.get_month_analytics(None)
            await self._reply(msg, format_moyklass_work_tasks_result(result), reply_markup=moyklass_keyboard(role))
            return True

        if text == "🔁 Пропуски":
            if not (self.admin.is_admin(user_id) or role in {"owner", "manager", "operations"}):
                await self._reply(msg, "Пропуски доступны владельцу, менеджеру и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
                return True
            result = self.moyklass.get_month_absences(None)
            await self._reply(msg, format_absences_result(result), reply_markup=moyklass_keyboard(role))
            return True

        if text == "💳 Без оплат":
            if not (self.admin.is_admin(user_id) or role in {"owner", "manager", "operations"}):
                await self._reply(msg, "Активные без оплат доступны владельцу, менеджеру и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
                return True
            result = self.moyklass.get_unpaid_active_clients(None)
            await self._reply(msg, format_unpaid_active_clients_result(result), reply_markup=moyklass_keyboard(role))
            return True

        if text == "📉 Низкая посещаемость":
            if not (self.admin.is_admin(user_id) or role in {"owner", "manager", "operations"}):
                await self._reply(msg, "Низкая посещаемость доступна владельцу, менеджеру и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
                return True
            result = self.moyklass.get_month_low_attendance(None, threshold=60.0)
            await self._reply(msg, format_low_attendance_result(result), reply_markup=moyklass_keyboard(role))
            return True

        if text == "👨‍🏫 Месяц преподавателей":
            if not (self.admin.is_admin(user_id) or role in {"owner", "manager", "operations", "methodist"}):
                await self._reply(msg, "Отчёт по преподавателям доступен владельцу, менеджеру, методисту и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
                return True
            result = self.moyklass.get_teacher_month_report(None)
            await self._reply(msg, format_teacher_month_report_result(result), reply_markup=moyklass_keyboard(role))
            return True

        if text == "📅 Задачи на сегодня":
            if not (self.admin.is_admin(user_id) or role in {"owner", "manager", "operations", "methodist"}):
                await self._reply(msg, "Задачи на сегодня доступны владельцу, менеджеру, методисту и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
                return True
            result = self.moyklass.get_daily_tasks(None)
            await self._reply(msg, format_daily_tasks_result(result), reply_markup=moyklass_keyboard(role))
            return True

        if text == "🧾 Записи занятий":
            if not (self.admin.is_admin(user_id) or role in {"owner", "manager", "operations"}):
                await self._reply(msg, "Записи занятий доступны владельцу, менеджеру и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
                return True
            result = self.moyklass.get_month_lesson_records(None)
            await self._reply(msg, format_lesson_records_result(result, limit=50), reply_markup=moyklass_keyboard(role))
            return True

        if text == "🏷 Без темы":
            if not (self.admin.is_admin(user_id) or role in {"owner", "manager", "operations"}):
                await self._reply(msg, "Список задач МойКласс доступен владельцу, менеджеру и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
                return True
            result = self.moyklass.get_month_issue_lessons(None, "missing_topic")
            await self._show_lessons_with_open_buttons(msg, user_id, result, title="Занятия без темы", limit=50)
            return True

        if text == "💬 Без комментария":
            if not (self.admin.is_admin(user_id) or role in {"owner", "manager", "operations"}):
                await self._reply(msg, "Список задач МойКласс доступен владельцу, менеджеру и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
                return True
            result = self.moyklass.get_month_issue_lessons(None, "missing_comment")
            await self._show_lessons_with_open_buttons(msg, user_id, result, title="Занятия без комментария", limit=50)
            return True

        if text == "👨‍🏫 По преподавателям":
            result = self.moyklass.get_month_lesson_breakdown(None, "teacher")
            await self._reply(msg, format_month_lesson_breakdown_result(result), reply_markup=moyklass_keyboard(role))
            return True

        if text == "🏫 По кабинетам":
            result = self.moyklass.get_month_lesson_breakdown(None, "room")
            await self._reply(msg, format_month_lesson_breakdown_result(result), reply_markup=moyklass_keyboard(role))
            return True

        if text == "👥 По группам":
            result = self.moyklass.get_month_lesson_breakdown(None, "group")
            await self._reply(msg, format_month_lesson_breakdown_result(result), reply_markup=moyklass_keyboard(role))
            return True

        if text == "🔎 Занятие по ID":
            if not self._can_read_moyklass_schedule(user_id) and role not in {"teacher", "methodist"}:
                await self._reply(msg, "Чтение занятия МойКласс доступно владельцу, методисту, операционному менеджеру или преподавателю только для своих занятий.", reply_markup=moyklass_keyboard(role))
                return True
            if role in {"teacher", "methodist"} and not self._mk_teacher_id_for_user(user_id):
                await self._reply_need_mk_teacher_link(msg, user_id, role)
                return True
            self.private_states[user_id] = {"mode": "mk_lesson_waiting"}
            await msg.reply_text("Введите ID занятия.\nНапример: 16620686", reply_markup=moyklass_keyboard(role))
            return True

        if text == "👥 Группы":
            if not self._can_read_moyklass_classes(user_id):
                await self._reply(msg, "Просмотр групп МойКласс доступен владельцу, менеджеру, методисту и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
                return True
            result = self.moyklass.get_classes("limit=80")
            await self._show_classes_with_open_buttons(msg, user_id, result, title="Группы МойКласс", limit=40)
            return True

        if text == "📘 Группа по ID":
            if not self._can_read_moyklass_classes(user_id):
                await self._reply(msg, "Просмотр группы доступен владельцу, менеджеру, методисту и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
                return True
            self.private_states[user_id] = {"mode": "mk_class_waiting"}
            await msg.reply_text("Введите ID группы / classId.\nНапример: 709199", reply_markup=moyklass_keyboard(role))
            return True

        if text == "👥 Ученики группы":
            if not self._can_read_moyklass_classes(user_id):
                await self._reply(msg, "Ученики группы доступны владельцу, менеджеру и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
                return True
            await self._show_selected_class_users(msg, user_id)
            return True

        if text == "📚 Занятия группы":
            if not self._can_read_moyklass_classes(user_id):
                await self._reply(msg, "Занятия группы доступны владельцу, менеджеру, методисту и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
                return True
            await self._show_selected_class_lessons(msg, user_id)
            return True

        if text == "👤 Найти ученика":
            if not self._can_read_moyklass_users(user_id):
                await self._reply(msg, "Поиск учеников доступен владельцу, менеджеру и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
                return True
            self.private_states[user_id] = {"mode": "mk_users_waiting"}
            await msg.reply_text("Введите имя или фамилию ученика.\nНапример: Иван", reply_markup=moyklass_keyboard(role))
            return True

        if text == "👤 Карточка ученика":
            if not self._can_read_moyklass_users(user_id):
                await self._reply(msg, "Карточка ученика доступна владельцу, менеджеру и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
                return True
            self.private_states[user_id] = {"mode": "mk_user_card_waiting"}
            await msg.reply_text("Введите ID ученика и месяц.\nНапример: 6222670 2026-06", reply_markup=moyklass_keyboard(role))
            return True

        if text == "📚 Занятия ученика":
            if not self._can_read_moyklass_users(user_id):
                await self._reply(msg, "Занятия ученика доступны владельцу, менеджеру и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
                return True
            await self._show_selected_user_lessons(msg, user_id)
            return True

        if text == "💳 Оплаты ученика":
            if not self._can_read_moyklass_users(user_id):
                await self._reply(msg, "Оплаты ученика доступны владельцу, менеджеру и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
                return True
            await self._show_selected_user_payments(msg, user_id)
            return True

        if text == "🔁 Пропуски ученика":
            if not self._can_read_moyklass_users(user_id):
                await self._reply(msg, "Пропуски ученика доступны владельцу, менеджеру и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
                return True
            await self._show_selected_user_absences(msg, user_id)
            return True

        if text == "🧾 Записи ученика":
            if not self._can_read_moyklass_users(user_id):
                await self._reply(msg, "Записи ученика доступны владельцу, менеджеру и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
                return True
            await self._show_selected_user_lessons(msg, user_id)
            return True

        if text == "👥 Группы ученика":
            if not self._can_read_moyklass_users(user_id):
                await self._reply(msg, "Группы ученика доступны владельцу, менеджеру и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
                return True
            await self._show_selected_user_joins(msg, user_id)
            return True

        if text == "📝 Комментарий ученику":
            if not self._can_write_moyklass(user_id):
                await self._reply(msg, "Комментарий в карточку ученика доступен владельцу, менеджеру и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
                return True
            selected_user = self._selected_user_id(user_id)
            if not selected_user:
                self.private_states[user_id] = {"mode": "mk_user_comment_id_waiting"}
                await msg.reply_text("Введите ID ученика и текст комментария.\nНапример:\n6222670 Связаться по оплате", reply_markup=moyklass_keyboard(role))
                return True
            self.private_states[user_id] = {"mode": "mk_user_comment_waiting", "selected_user_id": selected_user}
            await msg.reply_text(f"Выбран ученик #{selected_user}. Напишите комментарий для МойКласс.", reply_markup=selected_user_keyboard(role))
            return True

        if text == "✏️ Комментарий к занятию":
            selected = self._selected_lesson_id(user_id)
            if selected:
                current = self.moyklass.get_lesson_by_id(selected)
                allowed, reason = self._can_prepare_lesson_update(user_id, current, "comment")
                if not allowed:
                    await self._reply(msg, reason, reply_markup=moyklass_keyboard(role))
                    return True
                self.private_states[user_id] = {"mode": "mk_comment_waiting_selected", "selected_lesson_id": selected}
                await msg.reply_text(
                    f"Выбрано занятие #{selected}.\nНапишите комментарий для МойКласс.\n\nНапример:\nИван - отработка",
                    reply_markup=moyklass_keyboard(role),
                )
                return True
            if role in {"teacher", "methodist"} and not self._mk_teacher_id_for_user(user_id):
                await self._reply_need_mk_teacher_link(msg, user_id, role)
                return True
            if not (self._can_write_moyklass(user_id) or role in {"teacher", "methodist"}):
                await self._reply(msg, "Комментарий к занятию доступен владельцу, преподавателю и методисту.", reply_markup=moyklass_keyboard(role))
                return True
            self.private_states[user_id] = {"mode": "mk_comment_waiting"}
            await msg.reply_text("Введите ID занятия и комментарий.\nНапример:\n16620686 Иван - отработка", reply_markup=moyklass_keyboard(role))
            return True

        if text == "🏷 Тема занятия":
            selected = self._selected_lesson_id(user_id)
            if selected:
                current = self.moyklass.get_lesson_by_id(selected)
                allowed, reason = self._can_prepare_lesson_update(user_id, current, "topic")
                if not allowed:
                    await self._reply(msg, reason, reply_markup=moyklass_keyboard(role))
                    return True
                self.private_states[user_id] = {"mode": "mk_topic_waiting_selected", "selected_lesson_id": selected}
                await msg.reply_text(
                    f"Выбрано занятие #{selected}.\nНапишите тему для МойКласс.\n\nНапример:\nPython - 5 тема - Условия",
                    reply_markup=moyklass_keyboard(role),
                )
                return True
            if role in {"teacher", "methodist"} and not self._mk_teacher_id_for_user(user_id):
                await self._reply_need_mk_teacher_link(msg, user_id, role)
                return True
            if not (self._can_write_moyklass(user_id) or role in {"teacher", "methodist"}):
                await self._reply(msg, "Тема занятия доступна владельцу, преподавателю и методисту.", reply_markup=moyklass_keyboard(role))
                return True
            self.private_states[user_id] = {"mode": "mk_topic_waiting"}
            await msg.reply_text("Введите ID занятия и тему.\nНапример:\n16620686 Python - 5 тема - Условия", reply_markup=moyklass_keyboard(role))
            return True

        if text == "⏰ Время занятия":
            if not self._can_write_moyklass(user_id):
                await self._reply(msg, "Изменения времени в МойКласс доступны владельцу, менеджеру и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
                return True
            self.private_states[user_id] = {"mode": "mk_time_waiting"}
            await msg.reply_text("Введите ID, дату, начало и конец.\nНапример:\n16620686 2026-06-13 16:00 17:30", reply_markup=moyklass_keyboard(role))
            return True

        if text == "⚙️ Техническая информация":
            selected = self._selected_lesson_id(user_id)
            if not selected:
                await self._reply(msg, self._selected_lesson_help(), reply_markup=moyklass_keyboard(role))
                return True
            if not (self.admin.is_admin(user_id) or role in {"owner", "operations"}):
                await self._reply(msg, "Техническая информация доступна только владельцу и операционному менеджеру.", reply_markup=selected_lesson_keyboard(role))
                return True
            result = self.moyklass.get_lesson_by_id(selected)
            await self._reply(msg, format_lesson_technical(result, title=f"Техническая информация занятия #{selected}"), reply_markup=selected_lesson_keyboard(role))
            return True

        if text == "📌 Подготовленное изменение":
            await self._show_mk_pending(msg, user_id)
            return True

        if text == "✅ Отправить изменение":
            await self._execute_mk_pending(msg, user_id)
            state = self.private_states.get(user_id) or {}
            if state.get("mode") in {"mk_after_comment_confirm", "mk_after_topic_confirm"}:
                await self._reply(msg, "Если изменение отправлено успешно, нажмите ➡️ Далее. Если была ошибка - исправьте данные или отмените изменение.", reply_markup=after_lesson_next_keyboard())
                return True
            await self._show_moyklass_menu(msg)
            return True

        if text == "❌ Отменить изменение":
            if user_id in self.pending_mk_writes:
                self.pending_mk_writes.pop(user_id, None)
                await self._reply(msg, "Подготовленное изменение отменено.", reply_markup=moyklass_keyboard(role))
            else:
                await self._reply(msg, "Нет подготовленного изменения.", reply_markup=moyklass_keyboard(role))
            return True

        if text == "📜 Журнал изменений":
            if not self.admin.is_admin(user_id):
                await self._reply(msg, "Журнал изменений МойКласс доступен только владельцу.", reply_markup=moyklass_keyboard(role))
                return True
            rows = self.storage.list_mk_audit(30)
            if not rows:
                await self._reply(msg, "Журнал изменений МойКласс пока пуст.", reply_markup=moyklass_keyboard(role))
            else:
                lines = []
                for r in rows:
                    ok = "ok" if r.get("ok") else "error"
                    lines.append(f"#{r['id']} | {r['created_at']} | {ok} | user:{r.get('user_id')} | {r.get('method')} {r.get('path')} | status:{r.get('status')}")
                await self._reply(msg, "\n".join(lines), reply_markup=moyklass_keyboard(role))
            return True

        return False

    async def _handle_moyklass_state_text(self, msg, text: str, state: dict[str, str]) -> bool:
        user = msg.from_user
        if not user:
            return True
        user_id = user.id
        role = self.admin.get_role(user_id) or "other"
        mode = state.get("mode")

        if mode == "mk_lesson_list_waiting":
            mapping = state.get("lesson_buttons") or {}
            lesson_id = str(mapping.get(text.strip()) or "").strip()
            if not lesson_id:
                await self._reply(msg, "Выберите занятие кнопкой из списка или вернитесь в меню МойКласс.", reply_markup=lesson_list_keyboard(list(mapping.keys()), role))
                return True
            result = self.moyklass.get_lesson_by_id(lesson_id)
            if not self._lesson_result_belongs_to_user(user_id, result):
                await self._reply(msg, "Это занятие не относится к вашему доступу. Доступ закрыт.", reply_markup=moyklass_keyboard(role))
                return True
            self.private_states[user_id] = {"mode": "mk_lesson_selected", "selected_lesson_id": lesson_id}
            await self._reply(msg, format_lesson_preview(result), reply_markup=selected_lesson_keyboard(role))
            return True

        if mode == "mk_class_list_waiting":
            mapping = state.get("class_buttons") or {}
            class_id = str(mapping.get(text.strip()) or "").strip()
            if not class_id:
                await self._reply(msg, "Выберите группу кнопкой из списка или вернитесь в меню МойКласс.", reply_markup=class_list_keyboard(list(mapping.keys()), role))
                return True
            await self._show_class_card(msg, user_id, class_id)
            return True

        if mode == "mk_class_waiting":
            class_id = text.strip().split()[0] if text.strip() else ""
            if not class_id.isdigit():
                await self._reply(msg, "Нужен числовой ID группы / classId.", reply_markup=moyklass_keyboard(role))
                return True
            await self._show_class_card(msg, user_id, class_id)
            return True

        if mode == "mk_class_users_waiting":
            class_id = text.strip().split()[0] if text.strip() else ""
            if not class_id.isdigit():
                await self._reply(msg, "Нужен числовой ID группы / classId.", reply_markup=moyklass_keyboard(role))
                return True
            self.private_states[user_id] = {"mode": "mk_class_selected", "selected_class_id": class_id}
            result = self.moyklass.get_class_users(class_id, limit=80)
            include_contacts = bool(self.admin.is_admin(user_id))
            await self._reply(msg, format_class_users_result(result, limit=60, include_contacts=include_contacts), reply_markup=selected_class_keyboard(role))
            return True

        if mode == "mk_class_lessons_waiting":
            class_id = text.strip().split()[0] if text.strip() else ""
            if not class_id.isdigit():
                await self._reply(msg, "Нужен числовой ID группы / classId.", reply_markup=moyklass_keyboard(role))
                return True
            self.private_states[user_id] = {"mode": "mk_class_selected", "selected_class_id": class_id}
            result = self.moyklass.get_class_lessons(class_id, days=30, limit=120)
            await self._show_lessons_with_open_buttons(msg, user_id, result, title=f"Ближайшие занятия группы {class_id}", limit=40)
            return True

        if mode == "mk_lesson_waiting":
            lesson_id = text.strip().split()[0] if text.strip() else ""
            if not lesson_id.isdigit():
                await self._reply(msg, "Нужен числовой ID занятия.", reply_markup=moyklass_keyboard(role))
                return True
            result = self.moyklass.get_lesson_by_id(lesson_id)
            if not self._can_read_moyklass_schedule(user_id):
                if role in {"teacher", "methodist"}:
                    if not self._mk_teacher_id_for_user(user_id):
                        await self._reply_need_mk_teacher_link(msg, user_id, role)
                        return True
                    if not self._lesson_result_belongs_to_user(user_id, result):
                        await self._reply(msg, "Это занятие не относится к вашему teacherId. Доступ закрыт.", reply_markup=moyklass_keyboard(role))
                        return True
                else:
                    await self._reply(msg, "Доступ к занятию по ID закрыт для вашей роли.", reply_markup=moyklass_keyboard(role))
                    return True
            self.private_states[user_id] = {"mode": "mk_lesson_selected", "selected_lesson_id": lesson_id}
            await self._reply(msg, format_lesson_preview(result), reply_markup=selected_lesson_keyboard(role))
            return True

        if mode == "mk_checklist_waiting":
            lesson_id = text.strip().split()[0] if text.strip() else ""
            if not lesson_id.isdigit():
                await self._reply(msg, "Нужен числовой ID занятия.", reply_markup=moyklass_keyboard(role))
                return True
            result = self.moyklass.get_lesson_by_id(lesson_id)
            if not self._lesson_result_belongs_to_user(user_id, result):
                await self._reply(msg, "Это занятие не относится к вашему доступу. Чеклист по нему открыть нельзя.", reply_markup=moyklass_keyboard(role))
                return True
            self.private_states[user_id] = {"mode": "mk_after_absents_waiting", "selected_lesson_id": lesson_id}
            card = format_lesson_preview(result, title=f"Выбранное занятие #{lesson_id}")
            await self._reply(msg, self._after_lesson_start_text(card), reply_markup=after_lesson_absents_keyboard())
            return True

        if mode == "mk_after_absents_waiting":
            lesson_id = str(state.get("selected_lesson_id") or "").strip()
            if not lesson_id.isdigit():
                self.private_states.pop(user_id, None)
                await self._reply(msg, self._selected_lesson_help(), reply_markup=moyklass_keyboard(role))
                return True
            normalized = text.strip()
            if normalized in {"✅ Все присутствовали", "⏭ Пропустить", "⏭ Пропустить отсутствующих"}:
                self.private_states[user_id] = {"mode": "mk_after_topic_waiting", "selected_lesson_id": lesson_id}
                await self._reply(msg, self._after_lesson_topic_text(), reply_markup=after_lesson_topic_keyboard())
                return True
            addition = self._normalize_absence_comment(text)
            if not addition:
                await self._reply(msg, "Напишите имена отсутствующих или нажмите ✅ Все присутствовали.", reply_markup=after_lesson_absents_keyboard())
                return True
            current = self.moyklass.get_lesson_by_id(lesson_id)
            allowed, reason = self._can_prepare_lesson_update(user_id, current, "comment")
            if not allowed:
                await self._reply(msg, reason, reply_markup=moyklass_keyboard(role))
                return True
            current_comment = self._lesson_current_comment(current)
            new_comment = self._merge_lesson_comment(current_comment, addition)
            current_text = format_lesson_preview(current, title=f"Текущее занятие #{lesson_id}") if current.ok else f"Текущее занятие получить не удалось: status={current.status} {current.error}"
            self.private_states[user_id] = {"mode": "mk_after_comment_confirm", "selected_lesson_id": lesson_id}
            await self._prepare_mk_write(
                msg,
                user_id,
                action="update_lesson_fields",
                object_type="lesson",
                object_id=lesson_id,
                method="POST",
                path=f"/v1/company/lessons/{lesson_id}",
                payload={"comment": new_comment},
                current_text=current_text,
                allowed_user_id=user_id,
                reply_markup=after_lesson_next_keyboard(),
            )
            await self._reply(msg, "После отправки изменения нажмите ➡️ Далее, чтобы перейти к теме следующего занятия.", reply_markup=after_lesson_next_keyboard())
            return True

        if mode == "mk_after_comment_confirm":
            lesson_id = str(state.get("selected_lesson_id") or "").strip()
            if text.strip() not in {"➡️ Далее", "Далее", "далее"}:
                await self._reply(msg, "Сначала отправьте подготовленное изменение кнопкой ✅ Отправить изменение или командой /mk_confirm. Потом нажмите ➡️ Далее.", reply_markup=after_lesson_next_keyboard())
                return True
            self.private_states[user_id] = {"mode": "mk_after_topic_waiting", "selected_lesson_id": lesson_id}
            await self._reply(msg, self._after_lesson_topic_text(), reply_markup=after_lesson_topic_keyboard())
            return True

        if mode == "mk_after_topic_waiting":
            lesson_id = str(state.get("selected_lesson_id") or "").strip()
            if not lesson_id.isdigit():
                self.private_states.pop(user_id, None)
                await self._reply(msg, self._selected_lesson_help(), reply_markup=moyklass_keyboard(role))
                return True
            if text.strip() in {"⏭ Не менять тему", "⏭ Пропустить", "пропустить", "не менять"}:
                self.private_states[user_id] = {"mode": "mk_lesson_selected", "selected_lesson_id": lesson_id}
                await self._reply(msg, self._after_lesson_finish_text(lesson_id), reply_markup=after_lesson_done_keyboard())
                return True
            topic = text.strip()
            if not topic:
                await self._reply(msg, self._after_lesson_topic_text(), reply_markup=after_lesson_topic_keyboard())
                return True
            current = self.moyklass.get_lesson_by_id(lesson_id)
            allowed, reason = self._can_prepare_lesson_update(user_id, current, "topic")
            if not allowed:
                await self._reply(msg, reason, reply_markup=moyklass_keyboard(role))
                return True
            current_text = format_lesson_preview(current, title=f"Текущее занятие #{lesson_id}") if current.ok else f"Текущее занятие получить не удалось: status={current.status} {current.error}"
            self.private_states[user_id] = {"mode": "mk_after_topic_confirm", "selected_lesson_id": lesson_id}
            await self._prepare_mk_write(
                msg,
                user_id,
                action="update_lesson_fields",
                object_type="lesson",
                object_id=lesson_id,
                method="POST",
                path=f"/v1/company/lessons/{lesson_id}",
                payload={"topic": topic},
                current_text=current_text,
                allowed_user_id=user_id,
                reply_markup=after_lesson_next_keyboard(),
            )
            await self._reply(msg, "После отправки изменения нажмите ➡️ Далее, чтобы завершить чеклист.", reply_markup=after_lesson_next_keyboard())
            return True

        if mode == "mk_after_topic_confirm":
            lesson_id = str(state.get("selected_lesson_id") or "").strip()
            if text.strip() not in {"➡️ Далее", "Далее", "далее"}:
                await self._reply(msg, "Сначала отправьте подготовленное изменение кнопкой ✅ Отправить изменение или командой /mk_confirm. Потом нажмите ➡️ Далее.", reply_markup=after_lesson_next_keyboard())
                return True
            self.private_states[user_id] = {"mode": "mk_lesson_selected", "selected_lesson_id": lesson_id}
            await self._reply(msg, self._after_lesson_finish_text(lesson_id), reply_markup=after_lesson_done_keyboard())
            return True

        if mode == "mk_users_waiting":
            self.private_states.pop(user_id, None)
            result = self.moyklass.search_users(text, limit=10) if text.strip() else self.moyklass.get_users("limit=10")
            await self._reply(msg, format_users_result(result, limit=10, include_contacts=False), reply_markup=moyklass_keyboard(role))
            return True

        if mode == "mk_user_card_waiting":
            parts = text.strip().split()
            mk_user_id = parts[0].replace("#", "") if parts else ""
            month = parts[1] if len(parts) > 1 else None
            if not mk_user_id.isdigit():
                await self._reply(msg, "Нужен ID ученика. Например: 6222670 2026-06", reply_markup=moyklass_keyboard(role))
                return True
            await self._show_user_card(msg, user_id, mk_user_id, month)
            return True

        if mode in {"mk_user_lessons_waiting", "mk_user_payments_waiting", "mk_user_absences_waiting"}:
            parts = text.strip().split()
            mk_user_id = parts[0].replace("#", "") if parts else ""
            month = parts[1] if len(parts) > 1 else None
            if not mk_user_id.isdigit():
                await self._reply(msg, "Нужен ID ученика и месяц. Например: 6222670 2026-06", reply_markup=moyklass_keyboard(role))
                return True
            self.private_states[user_id] = {"mode": "mk_user_selected", "selected_user_id": mk_user_id}
            if month:
                self.private_states[user_id]["selected_user_month"] = month
            if mode == "mk_user_lessons_waiting":
                result = self.moyklass.get_user_lessons_month(mk_user_id, month)
                await self._reply(msg, format_user_lessons_result(result), reply_markup=selected_user_keyboard(role))
            elif mode == "mk_user_payments_waiting":
                result = self.moyklass.get_user_payments_month(mk_user_id, month)
                await self._reply(msg, format_user_payments_result(result), reply_markup=selected_user_keyboard(role))
            else:
                result = self.moyklass.get_user_absences_month(mk_user_id, month)
                await self._reply(msg, format_user_absences_result(result), reply_markup=selected_user_keyboard(role))
            return True

        if mode == "mk_user_joins_waiting":
            mk_user_id = text.strip().split()[0].replace("#", "") if text.strip() else ""
            if not mk_user_id.isdigit():
                await self._reply(msg, "Нужен ID ученика. Например: 6222670", reply_markup=moyklass_keyboard(role))
                return True
            self.private_states[user_id] = {"mode": "mk_user_selected", "selected_user_id": mk_user_id}
            result = self.moyklass.get_user_joins(mk_user_id, limit=100)
            await self._reply(msg, format_user_joins_result(result), reply_markup=selected_user_keyboard(role))
            return True

        if mode == "mk_user_comment_id_waiting":
            parts = text.strip().split(maxsplit=1)
            if len(parts) != 2 or not parts[0].replace("#", "").isdigit():
                await self._reply(msg, "Формат: ID ученика и комментарий.\nНапример:\n6222670 Связаться по оплате", reply_markup=moyklass_keyboard(role))
                return True
            mk_user_id = parts[0].replace("#", "")
            comment = parts[1].strip()
            self.private_states[user_id] = {"mode": "mk_user_selected", "selected_user_id": mk_user_id}
            current = self.moyklass.get_user_card(mk_user_id, None)
            current_text = format_user_card_result(current) if current.ok else f"Карточку получить не удалось: status={current.status} {current.error}"
            await self._prepare_mk_write(
                msg,
                user_id,
                action="create_user_comment",
                object_type="user",
                object_id=mk_user_id,
                method="POST",
                path="/v1/company/userComments",
                payload={"userId": int(mk_user_id), "comment": comment, "showToUser": False},
                current_text=current_text,
                allowed_user_id=user_id,
                reply_markup=selected_user_keyboard(role),
            )
            return True

        if mode == "mk_user_comment_waiting":
            mk_user_id = self._selected_user_id(user_id)
            comment = text.strip()
            if not mk_user_id or not comment:
                await self._reply(msg, "Напишите комментарий текстом или сначала откройте карточку ученика.", reply_markup=moyklass_keyboard(role))
                return True
            current = self.moyklass.get_user_card(mk_user_id, None)
            current_text = format_user_card_result(current) if current.ok else f"Карточку получить не удалось: status={current.status} {current.error}"
            self.private_states[user_id] = {"mode": "mk_user_selected", "selected_user_id": mk_user_id}
            await self._prepare_mk_write(
                msg,
                user_id,
                action="create_user_comment",
                object_type="user",
                object_id=mk_user_id,
                method="POST",
                path="/v1/company/userComments",
                payload={"userId": int(mk_user_id), "comment": comment, "showToUser": False},
                current_text=current_text,
                allowed_user_id=user_id,
                reply_markup=selected_user_keyboard(role),
            )
            return True

        if mode == "mk_comment_waiting_selected":
            lesson_id = str(state.get("selected_lesson_id") or "").strip()
            comment = text.strip()
            if not lesson_id.isdigit() or not comment:
                await self._reply(msg, "Напишите комментарий текстом. Например: Иван - отработка", reply_markup=moyklass_keyboard(role))
                return True
            current = self.moyklass.get_lesson_by_id(lesson_id)
            allowed, reason = self._can_prepare_lesson_update(user_id, current, "comment")
            if not allowed:
                await self._reply(msg, reason, reply_markup=moyklass_keyboard(role))
                return True
            current_text = format_lesson_preview(current, title=f"Текущее занятие #{lesson_id}") if current.ok else f"Текущее занятие получить не удалось: status={current.status} {current.error}"
            self.private_states[user_id] = {"mode": "mk_lesson_selected", "selected_lesson_id": lesson_id}
            await self._prepare_mk_write(
                msg,
                user_id,
                action="update_lesson_fields",
                object_type="lesson",
                object_id=lesson_id,
                method="POST",
                path=f"/v1/company/lessons/{lesson_id}",
                payload={"comment": comment},
                current_text=current_text,
                allowed_user_id=user_id,
            )
            return True

        if mode == "mk_comment_waiting":
            parts = text.strip().split(maxsplit=1)
            if len(parts) != 2 or not parts[0].isdigit():
                await self._reply(msg, "Формат: ID занятия и комментарий.\nНапример:\n16620686 Иван - отработка", reply_markup=moyklass_keyboard(role))
                return True
            self.private_states.pop(user_id, None)
            lesson_id, comment = parts[0], parts[1].strip()
            current = self.moyklass.get_lesson_by_id(lesson_id)
            allowed, reason = self._can_prepare_lesson_update(user_id, current, "comment")
            if not allowed:
                await self._reply(msg, reason, reply_markup=moyklass_keyboard(role))
                return True
            current_text = format_lesson_preview(current, title=f"Текущее занятие #{lesson_id}") if current.ok else f"Текущее занятие получить не удалось: status={current.status} {current.error}"
            await self._prepare_mk_write(
                msg,
                user_id,
                action="update_lesson_fields",
                object_type="lesson",
                object_id=lesson_id,
                method="POST",
                path=f"/v1/company/lessons/{lesson_id}",
                payload={"comment": comment},
                current_text=current_text,
                allowed_user_id=user_id,
            )
            return True

        if mode == "mk_topic_waiting_selected":
            lesson_id = str(state.get("selected_lesson_id") or "").strip()
            topic = text.strip()
            if not lesson_id.isdigit() or not topic:
                await self._reply(msg, "Напишите тему текстом. Например: Python - 5 тема - Условия", reply_markup=moyklass_keyboard(role))
                return True
            current = self.moyklass.get_lesson_by_id(lesson_id)
            allowed, reason = self._can_prepare_lesson_update(user_id, current, "topic")
            if not allowed:
                await self._reply(msg, reason, reply_markup=moyklass_keyboard(role))
                return True
            current_text = format_lesson_preview(current, title=f"Текущее занятие #{lesson_id}") if current.ok else f"Текущее занятие получить не удалось: status={current.status} {current.error}"
            self.private_states[user_id] = {"mode": "mk_lesson_selected", "selected_lesson_id": lesson_id}
            await self._prepare_mk_write(
                msg,
                user_id,
                action="update_lesson_fields",
                object_type="lesson",
                object_id=lesson_id,
                method="POST",
                path=f"/v1/company/lessons/{lesson_id}",
                payload={"topic": topic},
                current_text=current_text,
                allowed_user_id=user_id,
            )
            return True

        if mode == "mk_topic_waiting":
            parts = text.strip().split(maxsplit=1)
            if len(parts) != 2 or not parts[0].isdigit():
                await self._reply(msg, "Формат: ID занятия и тема.\nНапример:\n16620686 Python - 5 тема - Условия", reply_markup=moyklass_keyboard(role))
                return True
            self.private_states.pop(user_id, None)
            lesson_id, topic = parts[0], parts[1].strip()
            current = self.moyklass.get_lesson_by_id(lesson_id)
            allowed, reason = self._can_prepare_lesson_update(user_id, current, "topic")
            if not allowed:
                await self._reply(msg, reason, reply_markup=moyklass_keyboard(role))
                return True
            current_text = format_lesson_preview(current, title=f"Текущее занятие #{lesson_id}") if current.ok else f"Текущее занятие получить не удалось: status={current.status} {current.error}"
            await self._prepare_mk_write(
                msg,
                user_id,
                action="update_lesson_fields",
                object_type="lesson",
                object_id=lesson_id,
                method="POST",
                path=f"/v1/company/lessons/{lesson_id}",
                payload={"topic": topic},
                current_text=current_text,
                allowed_user_id=user_id,
            )
            return True

        if mode == "mk_time_waiting":
            if not self._can_write_moyklass(user_id):
                self.private_states.pop(user_id, None)
                await self._reply(msg, "Изменения времени в МойКласс пока доступны только владельцу.", reply_markup=moyklass_keyboard(role))
                return True
            parts = text.strip().split(maxsplit=3)
            if len(parts) < 4 or not parts[0].isdigit():
                await self._reply(msg, "Формат: ID YYYY-MM-DD HH:MM HH:MM\nНапример:\n16620686 2026-06-13 16:00 17:30", reply_markup=moyklass_keyboard(role))
                return True
            self.private_states.pop(user_id, None)
            lesson_id, day, begin, end = parts[0], parts[1], parts[2], parts[3]
            current = self.moyklass.get_lesson_by_id(lesson_id)
            current_text = format_lesson_preview(current, title=f"Текущее занятие #{lesson_id}") if current.ok else f"Текущее занятие получить не удалось: status={current.status} {current.error}"
            await self._prepare_mk_write(
                msg,
                user_id,
                action="update_lesson_fields",
                object_type="lesson",
                object_id=lesson_id,
                method="POST",
                path=f"/v1/company/lessons/{lesson_id}",
                payload={"date": day, "beginTime": begin, "endTime": end},
                current_text=current_text,
            )
            return True

        return False

    def _signed_miniapp_url(self, user_id: int) -> str:
        """Build a user-bound Mini App URL fallback.

        Telegram should pass signed initData when the app is opened via WebAppInfo.
        Some desktop/proxy combinations open the page as a regular in-app browser and initData is empty.
        This signed URL lets our own backend identify the user without exposing bot tokens.
        """
        base = (self.settings.web_app_url or "").strip()
        if not base:
            return ""
        ts = str(int(datetime.now().timestamp()))
        payload = f"{int(user_id)}:{ts}"
        sig = hmac.new(self.settings.telegram_bot_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
        sep = "&" if "?" in base else "?"
        return base + sep + urllib.parse.urlencode({"yc_user_id": str(int(user_id)), "yc_ts": ts, "yc_sig": sig})

    async def _send_parent_welcome(self, msg, user_id: int) -> None:
        if self.settings.web_app_url:
            await msg.reply_text(
                "Добро пожаловать в Yellow Club!\n\n"
                "Нажмите кнопку ниже, чтобы открыть кабинет родителя.\n"
                "В кабинете вы сможете привязать ребёнка по коду YC-XXXX и следить за питанием в программе.",
                reply_markup=parent_keyboard(),
            )
        else:
            await msg.reply_text(
                "Добро пожаловать в Yellow Club!\n\n"
                "Кабинет родителя временно недоступен. Обратитесь к администратору."
            )

    async def _handle_private_flow(self, msg, text: str) -> bool:
        user = msg.from_user
        if not user:
            return False
        user_id = user.id

        if text == "📋 Меню":
            self.private_states.pop(user_id, None)
            await self.admin.show(msg)
            return True

        if text == "❌ Отмена":
            self.private_states.pop(user_id, None)
            role = self.admin.get_role(user_id)
            await msg.reply_text("Действие отменено.", reply_markup=staff_keyboard(role or "other"))
            return True

        if text == "📲 Открыть кабинет":
            role = self.admin.get_role(user_id) or "other"
            if self.settings.web_app_url:
                signed_url = self._signed_miniapp_url(user_id)
                await msg.reply_text(
                    "📲 Откройте кабинет через кнопку ниже.\n\n"
                    "Кнопка содержит безопасную ссылку именно для вашего Telegram ID.\n"
                    "Если приложение уже было открыто старой кнопкой, закройте его и нажмите эту новую кнопку.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📲 Открыть кабинет", web_app=WebAppInfo(url=signed_url or self.settings.web_app_url))]]),
                )
            else:
                await msg.reply_text(
                    "📲 Mini App пока не включён.\n\n"
                    "Чтобы включить кабинет-приложение:\n"
                    "1. Запустите start_miniapp_windows.bat.\n"
                    "2. Дайте серверу HTTPS-ссылку через Cloudflare Tunnel / ngrok / домен.\n"
                    "3. Добавьте в .env:\n"
                    "WEB_APP_URL=https://ваша-ссылка\n"
                    "4. Перезапустите бота и нажмите /menu.",
                    reply_markup=staff_keyboard(role),
                )
            return True

        if text == "📌 Мои задачи":
            rows = self.storage.list_teacher_tasks(user_id=user_id, status="open", limit=30)
            await msg.reply_text(self._format_teacher_tasks_text(rows, title="📌 Мои открытые задачи"), reply_markup=staff_keyboard(self.admin.get_role(user_id) or "other"))
            return True

        if text == "📊 OPS контроль":
            role = self.admin.get_role(user_id) or "other"
            if role not in {"owner", "operations", "methodist"} and not self.admin.is_admin(user_id):
                await msg.reply_text("OPS контроль доступен владельцу, методисту и операционному менеджеру.")
                return True
            rows = self.storage.list_teacher_tasks(status="open", limit=80)
            await msg.reply_text(self._format_teacher_tasks_text(rows, title="📊 OPS контроль: открытые задачи"), reply_markup=staff_keyboard(role))
            return True

        # Manager cabinet quick actions. They run before the generic admin button handler,
        # so manager gets a real working flow instead of only a hint message.
        if text in {"💬 Ответ родителю", "💬 Ответ клиенту"}:
            if not self._is_manager_role(user_id):
                await msg.reply_text("Ответы родителям доступны менеджеру, операционному менеджеру и владельцу.")
                return True
            self.private_states[user_id] = {"mode": "manager_parent_reply_waiting"}
            await msg.reply_text(self._manager_prompt_text("manager_parent_reply_waiting"), reply_markup=manager_waiting_keyboard())
            return True

        if text == "🧭 Подбор направления":
            if not self._is_manager_role(user_id):
                await msg.reply_text("Подбор направления доступен менеджеру, операционному менеджеру и владельцу.")
                return True
            self.private_states[user_id] = {"mode": "manager_direction_waiting"}
            await msg.reply_text(self._manager_prompt_text("manager_direction_waiting"), reply_markup=manager_waiting_keyboard())
            return True

        if text in {"🔁 Отработка", "🔁 Отработки"}:
            if not self._is_manager_role(user_id) and self.admin.get_role(user_id) not in {"teacher", "methodist"}:
                await msg.reply_text("Помощь с отработками доступна менеджеру, преподавателю, методисту, операционному менеджеру и владельцу.")
                return True
            self.private_states[user_id] = {"mode": "manager_makeup_waiting"}
            await msg.reply_text(self._manager_prompt_text("manager_makeup_waiting"), reply_markup=manager_waiting_keyboard())
            return True

        if text == "📌 К кому обратиться":
            self.private_states[user_id] = {"mode": "manager_responsible_waiting"}
            await msg.reply_text(self._manager_prompt_text("manager_responsible_waiting"), reply_markup=manager_waiting_keyboard())
            return True

        if text == "📚 Курсы и цены":
            await msg.reply_text(self._courses_and_prices_text(), reply_markup=staff_keyboard(self.admin.get_role(user_id) or "other"))
            return True

        if text in {"❓ Как пользоваться", "❔ Как пользоваться", "ℹ️ Как пользоваться"}:
            await msg.reply_text(self._teacher_quick_help_text(), reply_markup=staff_keyboard(self.admin.get_role(user_id) or "other"))
            return True

        if text in {"📅 Мои занятия", "📅 Мои занятия сегодня"}:
            await self._show_teacher_day_overview(msg, user_id)
            return True

        if text in {"📘 Материалы на неделю", "📘 Материалы на сегодня"}:
            await self._show_teacher_today_materials(msg, user_id)
            return True

        if text in {"✅ Подготовка ко дню", "✅ Подготовка к занятию"}:
            await self._start_teacher_day_prepare(msg, user_id)
            return True

        if text in {"🌙 Закрыть рабочий день", "📋 Закрыть занятие"}:
            await self._start_teacher_day_close(msg, user_id)
            return True

        if text == "📊 Контроль преподавателей":
            await self._show_teacher_day_control(msg, user_id)
            return True

        if text in {"👥 Заявки на пробное", "📝 Заявки"}:
            if not self.admin.can_manage_trials(user_id):
                await msg.reply_text("Заявки на пробное доступны менеджеру, операционному менеджеру и владельцу.")
                return True
            await msg.reply_text(self._manager_trials_text(), reply_markup=manager_trials_keyboard())
            return True

        if text == "🔎 Найти ученика":
            if not self._can_read_moyklass_users(user_id):
                await msg.reply_text("Поиск учеников доступен владельцу, менеджеру и операционному менеджеру.")
                return True
            self.private_states[user_id] = {"mode": "mk_users_waiting"}
            await msg.reply_text("Введите имя, фамилию или часть имени ученика.", reply_markup=moyklass_keyboard(self.admin.get_role(user_id) or "other"))
            return True

        if text == "🗂 МойКласс" or text in MOYKLASS_BUTTONS:
            return await self._handle_moyklass_button(msg, text)

        state = self.private_states.get(user_id) or {}
        mode = state.get("mode") or ""

        if mode in {"teacher_day_overview", "teacher_day_close_list"}:
            mapping = state.get("lesson_buttons") or {}
            if text == "✅ Завершить рабочий день":
                await self._finish_teacher_day(msg, user_id)
                return True
            lesson_id = str(mapping.get(text.strip()) or "").strip()
            if not lesson_id:
                await self._reply(msg, "Выберите занятие кнопкой из списка или нажмите 📋 Меню.", reply_markup=teacher_day_lesson_keyboard(list(mapping.keys())))
                return True
            await self._open_day_close_lesson(msg, user_id, lesson_id)
            return True

        if mode == "teacher_day_prepare":
            mapping = state.get("lesson_buttons") or {}
            if text == "✅ Готов ко всем занятиям":
                for lesson_id in mapping.values():
                    self.storage.mark_teacher_preparation(lesson_id, user_id, "ready")
                self.private_states.pop(user_id, None)
                await self._reply(msg, f"✅ Подготовка ко дню подтверждена.\nЗанятий отмечено: {len(mapping)}", reply_markup=staff_keyboard(self.admin.get_role(user_id) or "other"))
                return True
            if text == "⚠️ Нужна помощь по дню":
                self.private_states[user_id] = {"mode": "teacher_day_prepare_problem", "lesson_buttons": mapping}
                await self._reply(msg, "Опишите, по какой теме или занятию нужна помощь. Сообщение попадёт в контроль преподавателей.", reply_markup=teacher_day_prepare_keyboard())
                return True
            if text in {"📘 Материалы на неделю", "📘 Материалы на сегодня"}:
                await self._show_teacher_today_materials(msg, user_id)
                return True

        if mode == "teacher_day_prepare_problem":
            mapping = state.get("lesson_buttons") or {}
            for lesson_id in mapping.values():
                self.storage.mark_teacher_preparation(lesson_id, user_id, "needs_help", comment=text)
            self.private_states.pop(user_id, None)
            await self._reply(msg, "⚠️ Зафиксировал, что нужна помощь по подготовке ко дню.", reply_markup=staff_keyboard(self.admin.get_role(user_id) or "other"))
            return True

        if mode == "teacher_day_prepare_lesson":
            lesson_id = str(state.get("selected_lesson_id") or "").strip()
            if not lesson_id:
                await self._show_teacher_day_overview(msg, user_id)
                return True
            if text == "⬅️ К занятиям дня":
                await self._show_teacher_day_overview(msg, user_id)
                return True
            if text == "📘 Материал выбранного":
                result = self.moyklass.get_lesson_by_id(lesson_id)
                item = self.moyklass._extract_lesson_item(result.data) if result and result.ok else None
                topic = self._lesson_material_query_from_item(item) or extract_lesson_topic_from_result(result)
                if topic:
                    answer = self.core.build_lesson_material_answer(topic)
                    self.private_states[user_id] = {"mode": "teacher_day_prepare_lesson", "selected_lesson_id": lesson_id}
                    await self._reply(msg, answer, reply_markup=teacher_day_prepare_lesson_keyboard())
                else:
                    await self._reply(msg, "В карточке занятия нет темы. Материал не найден.", reply_markup=teacher_day_prepare_lesson_keyboard())
                return True
            if text == "✅ Тема изучена, я готов":
                self.storage.mark_teacher_preparation(lesson_id, user_id, "ready")
                self.private_states[user_id] = {"mode": "teacher_day_prepare_lesson", "selected_lesson_id": lesson_id}
                await self._reply(msg, self._teacher_day_lesson_status_text(lesson_id), reply_markup=teacher_day_prepare_lesson_keyboard())
                return True
            if text == "⚠️ Нужна помощь с темой":
                self.private_states[user_id] = {"mode": "teacher_day_prepare_lesson_problem", "selected_lesson_id": lesson_id}
                await self._reply(msg, "Опишите, что непонятно по теме или подготовке к этому занятию.", reply_markup=teacher_day_prepare_lesson_keyboard())
                return True

        if mode == "teacher_day_prepare_lesson_problem":
            lesson_id = str(state.get("selected_lesson_id") or "").strip()
            if lesson_id:
                self.storage.mark_teacher_preparation(lesson_id, user_id, "needs_help", comment=text)
                self.private_states[user_id] = {"mode": "teacher_day_prepare_lesson", "selected_lesson_id": lesson_id}
                await self._reply(msg, self._teacher_day_lesson_status_text(lesson_id), reply_markup=teacher_day_prepare_lesson_keyboard())
            else:
                await self._reply(msg, "Не вижу выбранное занятие. Откройте 📅 Мои занятия ещё раз.", reply_markup=staff_keyboard(self.admin.get_role(user_id) or "other"))
            return True

        if mode == "teacher_day_close_lesson":
            lesson_id = str(state.get("selected_lesson_id") or "").strip()
            if not lesson_id:
                await self._start_teacher_day_close(msg, user_id)
                return True
            if text == "⬅️ К занятиям дня":
                await self._start_teacher_day_close(msg, user_id)
                return True
            control = self.storage.get_teacher_lesson_control(lesson_id) or {}
            gate = self._lesson_time_gate(control)
            if not gate.get("can_close"):
                self.private_states[user_id] = {"mode": "teacher_day_prepare_lesson", "selected_lesson_id": lesson_id}
                await self._reply(msg, self._teacher_day_lesson_status_text(lesson_id), reply_markup=teacher_day_prepare_lesson_keyboard())
                return True
            if text == "📘 Материал выбранного":
                result = self.moyklass.get_lesson_by_id(lesson_id)
                item = self.moyklass._extract_lesson_item(result.data) if result and result.ok else None
                topic = self._lesson_material_query_from_item(item) or extract_lesson_topic_from_result(result)
                if topic:
                    answer = self.core.build_lesson_material_answer(topic)
                    self.private_states[user_id] = {"mode": "teacher_day_close_lesson", "selected_lesson_id": lesson_id}
                    await self._reply(msg, answer, reply_markup=teacher_day_close_keyboard(self.storage.get_teacher_lesson_control(lesson_id) or {}))
                else:
                    await self._reply(msg, "В карточке занятия нет темы. Материал не найден.", reply_markup=teacher_day_close_keyboard(self.storage.get_teacher_lesson_control(lesson_id) or {}))
                return True
            if text == "📝 Отчёт по выбранному":
                self.private_states[user_id] = {"mode": "teacher_day_close_lesson", "selected_lesson_id": lesson_id}
                await self._show_selected_lesson_report(msg, user_id)
                return True
            step_map = {
                "✅ МойКласс заполнен": ("myclass", "done"),
                "📝 Отчёт отправлен": ("parent_report", "done"),
                "💾 Работы сохранены": ("works", "done"),
                "🏫 Кабинет в порядке": ("classroom", "done"),
            }
            if text in step_map:
                step, status = step_map[text]
                self.storage.mark_teacher_lesson_step(lesson_id, step, status, user_id=user_id)
                self.private_states[user_id] = {"mode": "teacher_day_close_lesson", "selected_lesson_id": lesson_id}
                await self._reply(msg, self._teacher_day_lesson_status_text(lesson_id), reply_markup=teacher_day_close_keyboard(self.storage.get_teacher_lesson_control(lesson_id) or {}))
                return True
            if text == "⚠️ Есть проблема":
                self.private_states[user_id] = {"mode": "teacher_day_problem_waiting", "selected_lesson_id": lesson_id}
                await self._reply(msg, "Опишите проблему по занятию: техника, ученик, тема, дисциплина, кабинет, материалы или другое.", reply_markup=teacher_day_close_keyboard(self.storage.get_teacher_lesson_control(lesson_id) or {}))
                return True
            if text in {"✅ Закрыть выбранное занятие", "✅ Закрыть занятие полностью"}:
                c = self.storage.get_teacher_lesson_control(lesson_id) or {}
                missing = []
                if str(c.get("myclass_status") or "") != "done":
                    missing.append("МойКласс")
                if str(c.get("parent_report_status") or "") != "done":
                    missing.append("отчёт родителям")
                if str(c.get("works_status") or "") != "done":
                    missing.append("работы учеников")
                if str(c.get("classroom_status") or "") != "done":
                    missing.append("кабинет")
                if missing:
                    await self._reply(msg, "Занятие пока нельзя закрыть полностью. Не отмечено: " + ", ".join(missing) + ".\n\nЕсли был форс-мажор, нажмите ⚠️ Есть проблема и опишите ситуацию.", reply_markup=teacher_day_close_keyboard(c))
                    return True
                self.storage.close_teacher_lesson(lesson_id, user_id)
                await self._reply(msg, "✅ Занятие закрыто. Можно выбрать следующее занятие для закрытия дня.", reply_markup=staff_keyboard(self.admin.get_role(user_id) or "other"))
                await self._start_teacher_day_close(msg, user_id)
                return True

        if mode == "teacher_day_problem_waiting":
            lesson_id = str(state.get("selected_lesson_id") or "").strip()
            if lesson_id:
                self.storage.mark_teacher_lesson_problem(lesson_id, user_id, text)
                self.private_states[user_id] = {"mode": "teacher_day_close_lesson", "selected_lesson_id": lesson_id}
                await self._reply(msg, self._teacher_day_lesson_status_text(lesson_id), reply_markup=teacher_day_close_keyboard(self.storage.get_teacher_lesson_control(lesson_id) or {}))
            else:
                await self._reply(msg, "Не вижу выбранное занятие. Откройте 🌙 Закрыть рабочий день ещё раз.", reply_markup=staff_keyboard(self.admin.get_role(user_id) or "other"))
            return True

        state = self.private_states.get(user_id) or {}
        if (state.get("mode") or "").startswith("mk_"):
            return await self._handle_moyklass_state_text(msg, text, state)

        if text in REPORT_START_BUTTONS:
            if not self.admin.can_make_parent_reports(user_id):
                await msg.reply_text("Формирование отчётов доступно преподавателям, методистам и владельцу.")
                return True
            self.private_states[user_id] = {"mode": "parent_report_waiting"}
            await msg.reply_text(report_need_details_message(), reply_markup=report_waiting_keyboard())
            return True

        state = self.private_states.get(user_id) or {}
        mode = state.get("mode")

        if text in REPORT_ACTION_BUTTONS and mode == "parent_report_ready":
            if text == "✅ Хорошо":
                report = state.get("last_report") or ""
                role = self.admin.get_role(user_id)
                lesson_id = str(state.get("selected_lesson_id") or "").strip()
                if lesson_id:
                    self.storage.mark_teacher_lesson_step(lesson_id, "parent_report", "done", user_id=user_id)
                    self.private_states[user_id] = {"mode": "teacher_day_close_lesson", "selected_lesson_id": lesson_id}
                    await msg.reply_text("✅ Отчёт отмечен как отправленный. Следующий шаг появится на клавиатуре.", reply_markup=teacher_day_close_keyboard(self.storage.get_teacher_lesson_control(lesson_id) or {}))
                else:
                    self.private_states.pop(user_id, None)
                    await msg.reply_text("Хорошо. Можно скопировать отчёт и отправить в родительский чат.", reply_markup=staff_keyboard(role or "other"))
                return True
            last_report = state.get("last_report") or ""
            if not last_report:
                self.private_states[user_id] = {"mode": "parent_report_waiting"}
                await msg.reply_text(report_need_details_message(), reply_markup=report_waiting_keyboard())
                return True
            instruction = self._report_action_instruction(text)
            source_request = state.get("source_request") or ""
            answer = self.core.rewrite_parent_report(
                original_report=last_report,
                instruction=instruction,
                source_request=source_request,
            )
            next_state = {
                "mode": "parent_report_ready",
                "last_report": answer,
                "source_request": source_request,
            }
            if state.get("selected_lesson_id"):
                next_state["selected_lesson_id"] = state.get("selected_lesson_id")
            self.private_states[user_id] = next_state
            await self._reply(msg, answer, reply_markup=report_actions_keyboard())
            return True

        if mode == "parent_report_waiting":
            if not self.admin.can_make_parent_reports(user_id):
                self.private_states.pop(user_id, None)
                await msg.reply_text("Формирование отчётов доступно преподавателям, методистам и владельцу.")
                return True
            answer = self.core.build_parent_report("/parent_report " + text)
            if self._looks_like_parent_report(answer):
                self.private_states[user_id] = {
                    "mode": "parent_report_ready",
                    "last_report": answer,
                    "source_request": text,
                }
                await self._reply(msg, answer, reply_markup=report_actions_keyboard())
            else:
                self.private_states[user_id] = {"mode": "parent_report_waiting"}
                await self._reply(msg, answer, reply_markup=report_waiting_keyboard())
            return True

        if mode in {"manager_parent_reply_waiting", "manager_direction_waiting", "manager_makeup_waiting", "manager_responsible_waiting"}:
            if mode != "manager_makeup_waiting" and not self._is_manager_role(user_id):
                self.private_states.pop(user_id, None)
                await msg.reply_text("Эта функция доступна менеджеру, операционному менеджеру и владельцу.")
                return True
            prompt = self._manager_build_prompt(mode, text)
            answer = self.core.build_answer(AnswerContext(chat_id=msg.chat.id, thread_id=None, user_text=prompt))
            self.private_states[user_id] = {
                "mode": "manager_answer_ready",
                "last_answer": answer,
                "source_request": text,
                "source_mode": mode,
            }
            await self._reply(msg, answer, reply_markup=manager_answer_keyboard())
            return True

        if mode == "manager_answer_ready" and text in {"🔁 Переделать", "✂️ Короче", "🤝 Мягче"}:
            last_answer = state.get("last_answer") or ""
            source_request = state.get("source_request") or ""
            source_mode = state.get("source_mode") or "manager_parent_reply_waiting"
            instruction_map = {
                "🔁 Переделать": "Переделай ответ менеджеру: сделай его более точным, естественным и готовым к использованию.",
                "✂️ Короче": "Сократи ответ, оставь только самое важное и готовую формулировку.",
                "🤝 Мягче": "Сделай ответ мягче и спокойнее для общения с родителем.",
            }
            instruction = instruction_map.get(text, text)
            answer = self.core.rewrite_bot_answer(
                original_answer=last_answer,
                instruction=instruction + "\n\nИсходная ситуация: " + source_request,
                chat_id=msg.chat.id,
                thread_id=None,
            )
            self.private_states[user_id] = {
                "mode": "manager_answer_ready",
                "last_answer": answer,
                "source_request": source_request,
                "source_mode": source_mode,
            }
            await self._reply(msg, answer, reply_markup=manager_answer_keyboard())
            return True

        return False

    def _mentioned_usernames(self, text: str) -> list[str]:
        return [m.lower() for m in re.findall(r"@([A-Za-z0-9_]{4,32})", text or "")]

    def _mentions_someone_else_without_bot(self, text: str) -> bool:
        mentions = self._mentioned_usernames(text)
        if not mentions:
            return False
        bot_username = (self.bot_username or "").lower()
        return bot_username not in mentions

    def _should_reply(self, update: Update, text: str) -> bool:
        chat = update.effective_chat
        msg = update.effective_message
        if not chat or not msg:
            return False
        if chat.type == "private":
            return True
        mode = self.settings.response_mode
        if mode == "silent":
            return False
        if mode == "commands":
            return False
        if msg.reply_to_message and msg.reply_to_message.from_user and msg.reply_to_message.from_user.username == self.bot_username:
            return True
        if self.bot_username and f"@{self.bot_username.lower()}" in text.lower():
            return True
        if self._mentions_someone_else_without_bot(text):
            log.info("SMART SKIP | message mentions another user, not the bot | text=%r", text[:200])
            return False
        if is_parent_report_request(text):
            return True
        if mode == "mentions":
            return False
        if mode == "smart":
            return should_smart_reply(text)
        return is_question_like(text)

    def _can_read_moyklass_schedule(self, user_id: int | None) -> bool:
        return bool(self.admin.is_admin(user_id) or self.admin.get_role(user_id) in {"owner", "operations", "methodist", "manager"})

    def _can_read_moyklass_users(self, user_id: int | None) -> bool:
        return bool(self.admin.is_admin(user_id) or self.admin.get_role(user_id) in {"owner", "operations", "manager"})

    def _can_read_moyklass_classes(self, user_id: int | None) -> bool:
        return bool(self.admin.is_admin(user_id) or self.admin.get_role(user_id) in {"owner", "operations", "manager", "methodist"})

    def _can_write_moyklass(self, user_id: int | None) -> bool:
        # Managers and operations work with CRM daily, so they can prepare lesson changes.
        # Every write still requires explicit confirmation and is logged in mk_audit.
        return bool(self.admin.is_admin(user_id) or self.admin.get_role(user_id) in {"owner", "manager", "operations"})

    def _compact_payload(self, payload: dict) -> str:
        try:
            return json.dumps(payload or {}, ensure_ascii=False, indent=2)
        except Exception:
            return str(payload)



    def _selected_user_id(self, user_id: int) -> str:
        state = self.private_states.get(user_id) or {}
        raw = str(state.get("selected_user_id") or "").strip().replace("#", "")
        m = re.search(r"\d+", raw)
        return m.group(0) if m else ""

    async def _show_user_card(self, msg, user_id: int, mk_user_id: str, month: str | None = None) -> None:
        role = self.admin.get_role(user_id) or "other"
        if not self._can_read_moyklass_users(user_id):
            await self._reply(msg, "Карточка ученика доступна владельцу, менеджеру и операционному менеджеру.", reply_markup=moyklass_keyboard(role))
            return
        mk_user_id = str(mk_user_id or "").strip().replace("#", "")
        if not mk_user_id.isdigit():
            await self._reply(msg, "Нужен числовой ID ученика. Например: 6222670", reply_markup=moyklass_keyboard(role))
            return
        result = self.moyklass.get_user_card(mk_user_id, month)
        if result.ok:
            self.private_states[user_id] = {"mode": "mk_user_selected", "selected_user_id": mk_user_id}
            if month:
                self.private_states[user_id]["selected_user_month"] = str(month)
            await self._reply(
                msg,
                format_user_card_result(result, role=role, show_contacts=(self.admin.is_admin(user_id) or role in {"owner", "manager"})),
                reply_markup=selected_user_keyboard(role),
            )
        else:
            await self._reply(
                msg,
                format_user_card_result(result, role=role, show_contacts=(self.admin.is_admin(user_id) or role in {"owner", "manager"})),
                reply_markup=moyklass_keyboard(role),
            )

    async def _show_selected_user_lessons(self, msg, user_id: int) -> None:
        role = self.admin.get_role(user_id) or "other"
        mk_user_id = self._selected_user_id(user_id)
        month = str((self.private_states.get(user_id) or {}).get("selected_user_month") or "").strip() or None
        if not mk_user_id:
            self.private_states[user_id] = {"mode": "mk_user_lessons_waiting"}
            await self._reply(msg, "Введите ID ученика и месяц. Например:\n6222670 2026-06", reply_markup=moyklass_keyboard(role))
            return
        result = self.moyklass.get_user_lessons_month(mk_user_id, month)
        await self._reply(msg, format_user_lessons_result(result), reply_markup=selected_user_keyboard(role))

    async def _show_selected_user_payments(self, msg, user_id: int) -> None:
        role = self.admin.get_role(user_id) or "other"
        mk_user_id = self._selected_user_id(user_id)
        month = str((self.private_states.get(user_id) or {}).get("selected_user_month") or "").strip() or None
        if not mk_user_id:
            self.private_states[user_id] = {"mode": "mk_user_payments_waiting"}
            await self._reply(msg, "Введите ID ученика и месяц. Например:\n6222670 2026-06", reply_markup=moyklass_keyboard(role))
            return
        result = self.moyklass.get_user_payments_month(mk_user_id, month)
        await self._reply(msg, format_user_payments_result(result), reply_markup=selected_user_keyboard(role))

    async def _show_selected_user_absences(self, msg, user_id: int) -> None:
        role = self.admin.get_role(user_id) or "other"
        mk_user_id = self._selected_user_id(user_id)
        month = str((self.private_states.get(user_id) or {}).get("selected_user_month") or "").strip() or None
        if not mk_user_id:
            self.private_states[user_id] = {"mode": "mk_user_absences_waiting"}
            await self._reply(msg, "Введите ID ученика и месяц. Например:\n6222670 2026-06", reply_markup=moyklass_keyboard(role))
            return
        result = self.moyklass.get_user_absences_month(mk_user_id, month)
        await self._reply(msg, format_user_absences_result(result), reply_markup=selected_user_keyboard(role))

    async def _show_selected_user_joins(self, msg, user_id: int) -> None:
        role = self.admin.get_role(user_id) or "other"
        mk_user_id = self._selected_user_id(user_id)
        if not mk_user_id:
            self.private_states[user_id] = {"mode": "mk_user_joins_waiting"}
            await self._reply(msg, "Введите ID ученика. Например:\n6222670", reply_markup=moyklass_keyboard(role))
            return
        result = self.moyklass.get_user_joins(mk_user_id, limit=100)
        await self._reply(msg, format_user_joins_result(result), reply_markup=selected_user_keyboard(role))

    def _selected_class_id(self, user_id: int) -> str:
        state = self.private_states.get(user_id) or {}
        return str(state.get("selected_class_id") or "").strip()

    async def _show_classes_with_open_buttons(self, msg, user_id: int, result, title: str = "Группы МойКласс", limit: int = 30) -> None:
        text = format_classes_result(result, limit=limit, title=title)
        items = [x for x in extract_items(result.data) if isinstance(x, dict)] if result.ok else []
        mapping: dict[str, str] = {}
        buttons: list[str] = []
        for idx, item in enumerate(items[:limit], start=1):
            class_id = str(item.get("id") or item.get("classId") or item.get("groupId") or item.get("courseClassId") or "").strip()
            if not class_id:
                continue
            label = class_open_button_label(item, idx)
            # Ensure unique labels.
            while label in mapping:
                label += " "
            mapping[label] = class_id
            buttons.append(label)
        if buttons:
            self.private_states[user_id] = {"mode": "mk_class_list_waiting", "class_buttons": mapping}
            await self._reply(msg, text + "\n\nВыберите группу кнопкой ниже.", reply_markup=class_list_keyboard(buttons, self.admin.get_role(user_id) or "other"))
        else:
            await self._reply(msg, text, reply_markup=moyklass_keyboard(self.admin.get_role(user_id) or "other"))

    async def _show_class_card(self, msg, user_id: int, class_id: str) -> None:
        role = self.admin.get_role(user_id) or "other"
        result = self.moyklass.get_class_by_id(class_id)
        if not result.ok:
            await self._reply(msg, format_class_preview(result), reply_markup=moyklass_keyboard(role))
            return
        self.private_states[user_id] = {"mode": "mk_class_selected", "selected_class_id": str(class_id)}
        await self._reply(msg, format_class_preview(result), reply_markup=selected_class_keyboard(role))

    async def _show_selected_class_users(self, msg, user_id: int) -> None:
        role = self.admin.get_role(user_id) or "other"
        class_id = self._selected_class_id(user_id)
        if not class_id:
            self.private_states[user_id] = {"mode": "mk_class_users_waiting"}
            await self._reply(msg, "Введите ID группы, чтобы показать учеников.\nНапример: 709199", reply_markup=moyklass_keyboard(role))
            return
        result = self.moyklass.get_class_users(class_id, limit=80)
        include_contacts = bool(self.admin.is_admin(user_id))
        await self._reply(msg, format_class_users_result(result, limit=60, include_contacts=include_contacts), reply_markup=selected_class_keyboard(role))

    async def _show_selected_class_lessons(self, msg, user_id: int) -> None:
        role = self.admin.get_role(user_id) or "other"
        class_id = self._selected_class_id(user_id)
        if not class_id:
            self.private_states[user_id] = {"mode": "mk_class_lessons_waiting"}
            await self._reply(msg, "Введите ID группы, чтобы показать ближайшие занятия.\nНапример: 709199", reply_markup=moyklass_keyboard(role))
            return
        result = self.moyklass.get_class_lessons(class_id, days=30, limit=120)
        await self._show_lessons_with_open_buttons(msg, user_id, result, title=f"Ближайшие занятия группы {class_id}", limit=40)

    async def _prepare_mk_write(self, msg, user_id: int, *, action: str, object_type: str, object_id: str, method: str, path: str, payload: dict, current_text: str = "", allowed_user_id: int | None = None, reply_markup=None) -> None:
        pending = {
            "action": action,
            "object_type": object_type,
            "object_id": str(object_id),
            "method": method.upper(),
            "path": path,
            "payload": payload or {},
            "current_text": current_text or "",
            "allowed_user_id": int(allowed_user_id or user_id),
            "prepared_by": int(user_id),
        }
        self.pending_mk_writes[user_id] = pending
        body = [
            "Подготовлено изменение в МойКласс. Данные ещё не отправлены.",
            "",
        ]
        if current_text:
            body.append(current_text)
            body.append("")
        body.extend([
            f"Действие: {action}",
            f"Запрос: {pending['method']} {path}",
            "Тело запроса:",
            self._compact_payload(payload),
        ])
        body.extend([
            "",
            "Чтобы отправить изменение в МойКласс, напишите:",
            "/mk_confirm",
            "",
            "Чтобы отменить:",
            "/mk_cancel",
        ])
        await self._reply(msg, "\n".join(body), reply_markup=reply_markup or moyklass_keyboard(self.admin.get_role(user_id) or "other"))

    async def _execute_mk_pending(self, msg, user_id: int) -> None:
        pending = self.pending_mk_writes.get(user_id)
        if not pending:
            await self._reply(msg, "Нет подготовленного изменения. Сначала используйте /mk_prepare_comment, /mk_prepare_topic или /mk_write_raw.")
            return
        allowed_user_id = pending.get("allowed_user_id")
        if not (self.admin.is_admin(user_id) or self.admin.get_role(user_id) == "owner" or int(allowed_user_id or 0) == int(user_id)):
            await self._reply(msg, "Это изменение подготовлено не для вашего пользователя. Отправка запрещена.")
            return
        if pending.get("action") == "update_lesson_fields":
            result = self.moyklass.update_lesson_fields(
                pending.get("object_id") or "",
                pending.get("payload") or {},
                method=pending.get("method") or "POST",
            )
        else:
            result = self.moyklass.raw_write(pending["method"], pending["path"], pending["payload"])

        audit_id = self.storage.add_mk_audit(
            user_id=user_id,
            action=pending.get("action") or "write",
            object_type=pending.get("object_type") or "",
            object_id=pending.get("object_id") or "",
            method=pending.get("method") or "",
            path=pending.get("path") or "",
            payload=self._compact_payload(pending.get("payload") or {}),
            status=result.status,
            ok=result.ok,
            response=preview_json(result.data, 6000) if result.ok else preview_json(result.data, 6000),
            error=result.error if not result.ok else "",
        )
        if result.ok:
            self.pending_mk_writes.pop(user_id, None)
            await self._reply(msg, f"Изменение отправлено и проверено в МойКласс.\nstatus={result.status}\naudit_id=#{audit_id}\n\nОтвет:\n" + preview_json(result.data, 3000))
        else:
            await self._reply(msg, f"МойКласс не принял изменение.\nstatus={result.status}\naudit_id=#{audit_id}\n\nОшибка:\n{result.error}\n\nОтвет:\n" + preview_json(result.data, 3000) + "\n\nПодготовленное изменение оставлено. Можно отменить /mk_cancel или изменить командой заново.")



    async def handle_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE, cmd: str, args: str) -> None:
        msg = update.effective_message
        chat = update.effective_chat
        user = update.effective_user
        if not msg or not chat or not user:
            return

        if cmd in {"test_as", "test_role"}:
            if chat.type != "private":
                await self._reply(msg, "Тестовый режим включается только в личке с ботом.")
                return
            if not self.admin.is_real_admin(user.id):
                await self._reply(msg, "Тестировать роли может только владелец из ADMIN_IDS.")
                return
            role = (args.strip().split()[0].lower() if args.strip() else "")
            if role not in VALID_ROLES:
                await self._reply(msg, "Формат: /test_as role\nРоли: owner, manager, teacher, methodist, operations, smm, accountant, other")
                return
            self.storage.set_staff_test_mode(user.id, role)
            await self._reply(
                msg,
                "Тестовый режим включён.\n"
                f"Теперь бот показывает кабинет как для роли: {self.admin.role_label(role)}.\n\n"
                "Вернуться в обычный режим: /test_as_off",
                reply_markup=staff_keyboard(role),
            )
            return

        if cmd in {"test_teacher_id", "test_mk_teacher_id"}:
            if chat.type != "private":
                await self._reply(msg, "Тестовый teacherId задаётся только в личке с ботом.")
                return
            if not self.admin.is_real_admin(user.id):
                await self._reply(msg, "Тестовый teacherId может задавать только владелец из ADMIN_IDS.")
                return
            teacher_id = args.strip().split()[0] if args.strip() else ""
            if not teacher_id.isdigit():
                await self._reply(msg, "Формат: /test_teacher_id MK_TEACHER_ID\nНапример: /test_teacher_id 71190")
                return
            self.storage.set_staff_test_teacher_id(user.id, teacher_id)
            test = self.storage.get_staff_test_mode(user.id)
            role = test.get("role") or "teacher"
            await self._reply(
                msg,
                f"Тестовый teacherId сохранён: {teacher_id}\n"
                f"Текущая тестовая роль: {self.admin.role_label(role)}\n\n"
                "Теперь можно проверить: /menu -> 🗂 МойКласс -> 📅 Мои занятия",
                reply_markup=staff_keyboard(role),
            )
            return

        if cmd in {"test_as_off", "test_off"}:
            if chat.type != "private":
                await self._reply(msg, "Тестовый режим выключается только в личке с ботом.")
                return
            if not self.admin.is_real_admin(user.id):
                await self._reply(msg, "Тестовый режим может выключать только владелец из ADMIN_IDS.")
                return
            self.storage.clear_staff_test_mode(user.id)
            await self._reply(msg, "Тестовый режим выключен. Вернулся ваш обычный кабинет владельца.", reply_markup=staff_keyboard(self.admin.get_role(user.id) or "owner"))
            return

        if cmd in {"test_mode", "test_status"}:
            if not self.admin.is_real_admin(user.id):
                await self._reply(msg, "Тестовый режим доступен только владельцу из ADMIN_IDS.")
                return
            await self._reply(msg, self._test_mode_summary(user.id), reply_markup=staff_keyboard(self.admin.get_role(user.id) or "owner"))
            return

        if cmd in {"teacher_profiles_init"}:
            if not (self.admin.is_admin(user.id) or self.admin.get_role(user.id) in {"owner", "operations", "methodist"}):
                await self._reply(msg, "Инициализация профилей доступна владельцу, методисту и операционному менеджеру.")
                return
            created = self.storage.ensure_teacher_profiles_from_staff()
            await self._reply(msg, f"✅ Профили текущих преподавателей проверены. Создано новых профилей: {created}")
            return

        if cmd in {"teacher_profiles", "teachers_ops"}:
            if not (self.admin.is_admin(user.id) or self.admin.get_role(user.id) in {"owner", "operations", "methodist"}):
                await self._reply(msg, "Профили преподавателей доступны владельцу, методисту и операционному менеджеру.")
                return
            rows = self.storage.list_teacher_profiles(200)
            await self._reply(msg, self._format_teacher_profiles_text(rows))
            return

        if cmd in {"set_teacher_status", "teacher_status"}:
            if not (self.admin.is_admin(user.id) or self.admin.get_role(user.id) in {"owner", "operations", "methodist"}):
                await self._reply(msg, "Статус преподавателя может менять владелец, методист или операционный менеджер.")
                return
            parts = args.strip().split(maxsplit=2)
            if len(parts) < 2 or not parts[0].isdigit():
                await self._reply(msg, "Формат: /set_teacher_status USER_ID STATUS [заметка]\nСтатусы: trainee, trial_allowed, regular_allowed, active, paused, substitute")
                return
            target_id = int(parts[0])
            status = parts[1].strip().lower()
            notes = parts[2].strip() if len(parts) > 2 else ""
            if status not in self.storage.VALID_TEACHER_STATUSES:
                await self._reply(msg, "Неизвестный статус. Статусы: trainee, trial_allowed, regular_allowed, active, paused, substitute")
                return
            profile_type = "new" if status == "trainee" else "current"
            can_trial = status in {"trial_allowed", "regular_allowed", "active"}
            can_regular = status in {"regular_allowed", "active"}
            can_sub = status in {"substitute", "active"}
            row = self.storage.upsert_teacher_profile(target_id, teacher_status=status, profile_type=profile_type, can_trial=can_trial, can_regular=can_regular, can_substitute=can_sub, notes=notes)
            await self._reply(msg, "✅ Статус преподавателя обновлён.\n" + self._format_teacher_profiles_text([row]))
            return

        if cmd in {"my_tasks", "tasks"}:
            rows = self.storage.list_teacher_tasks(user_id=user.id, status="open", limit=50)
            await self._reply(msg, self._format_teacher_tasks_text(rows, title="📌 Мои открытые задачи"), reply_markup=staff_keyboard(self.admin.get_role(user.id) or "other"))
            return

        if cmd in {"task_done", "done_task"}:
            task_id = args.strip().split()[0] if args.strip() else ""
            if not task_id.isdigit():
                await self._reply(msg, "Формат: /task_done ID_задачи")
                return
            ok = self.storage.complete_teacher_task(task_id, completed_by=user.id)
            await self._reply(msg, "✅ Задача закрыта." if ok else "Не удалось закрыть задачу. Проверьте ID.")
            return

        if cmd in {"ops_control", "teacher_ops_control"}:
            if not (self.admin.is_admin(user.id) or self.admin.get_role(user.id) in {"owner", "operations", "methodist"}):
                await self._reply(msg, "OPS контроль доступен владельцу, методисту и операционному менеджеру.")
                return
            rows = self.storage.list_teacher_tasks(status="open", limit=100)
            await self._reply(msg, self._format_teacher_tasks_text(rows, title="📊 OPS контроль: открытые задачи"))
            return

        if cmd in {"mk_schedule_snapshot", "mk_schedule_check"}:
            if not (self.admin.is_admin(user.id) or self.admin.get_role(user.id) in {"owner", "operations", "methodist"}):
                await self._reply(msg, "Проверка расписания доступна владельцу, методисту и операционному менеджеру.")
                return
            parts = args.strip().split()
            days = 14
            notify = False
            for part in parts:
                if part.isdigit():
                    days = int(part)
                if part.lower() in {"notify", "send", "уведомить", "yes"}:
                    notify = True
            data = await self._mk_schedule_check(context, days=days, notify=notify)
            await self._reply(msg, self._format_schedule_check_text(data))
            return

        if cmd in {"start", "register"}:
            if chat.type != "private":
                await self._reply(msg, "Регистрация проходит в личке с ботом. Откройте бота и напишите /start.")
                return
            if self.admin.has_any_role(user.id):
                await self.admin.show(msg)
            elif self.settings.food_module_enabled:
                await self._send_parent_welcome(msg, user.id)
            else:
                await self.admin.ask_registration(msg)
            return

        if cmd in {"menu", "admin"}:
            if chat.type != "private":
                await self._reply(msg, "Меню открывается в личке с ботом.")
                return
            await self.admin.show(msg)
            return

        if cmd == "role":
            role = self.admin.get_role(user.id)
            if role:
                extra = ""
                if self.admin.is_real_admin(user.id):
                    test = self.storage.get_staff_test_mode(user.id)
                    if test.get("enabled"):
                        extra = "\n\n" + self._test_mode_summary(user.id)
                await self._reply(msg, f"Ваша роль: {self.admin.role_label(role)}" + extra)
            else:
                await self._reply(msg, "Вы ещё не зарегистрированы. В личке с ботом напишите /start и выберите роль.")
            return

        if cmd in {"start", "help"}:
            await self._reply(
                msg,
                "Yellow Club Agent работает. Можно писать вопрос обычным текстом. "
                "Команды: /menu, /role, /ping, /chatid, /ask, /parent_report, /remember, /facts, /search_history, /trials, /decision, /decisions, /feedback, /mk_test, /mk_today, /mk_week, /mk_users, /mk_groups, /mk_class, /mk_lesson, /mk_prepare_comment, /mk_prepare_topic, /mk_confirm, /mk_report, /mk_attendance, /mk_payments, /mk_active_clients, /mk_tasks_mk, /mk_absences, /mk_unpaid, /mk_low_attendance, /mk_teacher_month, /mk_daily_tasks, /mk_user, /mk_user_lessons, /mk_user_payments, /mk_user_absences, /mk_user_joins. Для владельца: /test_as, /test_teacher_id, /test_as_off."
            )
            return

        if cmd == "ping":
            status = "разрешён" if (chat.type == "private" or self.settings.is_group_allowed(chat.id)) else "не разрешён"
            role = self.admin.get_role(user.id) or "not_registered"
            await self._reply(msg, f"pong\nchat_id={chat.id}\ntype={chat.type}\nstatus={status}\nmode={self.settings.response_mode}\nrole={role}")
            return
        if cmd == "chatid":
            status = "разрешён" if (chat.type == "private" or self.settings.is_group_allowed(chat.id)) else "не разрешён"
            await self._reply(msg, f"chat_id={chat.id}\nthread_id={self._thread_id(update)}\ntype={chat.type}\nstatus={status}")
            return
        if cmd == "ask":
            if not args:
                await self._reply(msg, "Напиши вопрос после /ask.")
                return
            answer = self.core.build_answer(AnswerContext(chat_id=chat.id, thread_id=self._thread_id(update), user_text=args))
            await self._reply(msg, answer)
            return
        if cmd in {"parent_report_debug", "report_debug"}:
            # Debug diagnostics use the same access rule as knowledge-base/memory management.
            # AdminPanel in the stable branch does not have is_owner/can_manage_knowledge methods.
            if not self.admin.can_manage_memory(user.id):
                await self._reply(msg, "Диагностика отчётов доступна владельцу, методисту и администратору.")
                return
            answer = self.core.build_parent_report_debug(args if args else text)
            await self._reply(msg, answer)
            return

        if cmd in {"parent_report", "report_parent"}:
            if not self.admin.can_make_parent_reports(user.id):
                await self._reply(msg, "Отчёты родителям доступны преподавателям, методистам и владельцу. В личке с ботом напишите /start и выберите роль.")
                return
            report_text = args if args else text
            answer = self.core.build_parent_report(report_text)
            if chat.type == "private" and self._looks_like_parent_report(answer):
                self.private_states[user.id] = {
                    "mode": "parent_report_ready",
                    "last_report": answer,
                    "source_request": report_text,
                }
                await self._reply(msg, answer, reply_markup=report_actions_keyboard())
            else:
                await self._reply(msg, answer)
            return
        if cmd == "remember":
            if not self.admin.can_manage_memory(user.id):
                await self._reply(msg, "Сохранять факты может владелец или методист.")
                return
            if not args:
                await self._reply(msg, "Напиши факт после /remember.")
                return
            fact_id = self.storage.add_fact(args, user.id)
            await self._reply(msg, f"Факт сохранён: #{fact_id}")
            return
        if cmd == "facts":
            facts = self.storage.list_facts(30)
            if not facts:
                await self._reply(msg, "Фактов пока нет.")
            else:
                await self._reply(msg, "\n".join(f"#{f['id']}: {f['text']}" for f in facts))
            return
        if cmd == "forget_fact":
            if not self.admin.can_manage_memory(user.id):
                await self._reply(msg, "Удалять факты может владелец или методист.")
                return
            try:
                fact_id = int(args.strip())
            except Exception:
                await self._reply(msg, "Укажи ID факта: /forget_fact 1")
                return
            ok = self.storage.forget_fact(fact_id)
            await self._reply(msg, "Факт удалён." if ok else "Факт не найден.")
            return
        if cmd == "decision":
            if not self.admin.can_manage_memory(user.id):
                await self._reply(msg, "Сохранять рабочие решения может владелец или методист.")
                return
            if not args:
                await self._reply(msg, "Напиши решение после /decision. Например: /decision По группе вс 17:00 отработать 7 и 9 тему.")
                return
            decision_id = self.storage.add_decision(args, chat.id, self._thread_id(update), user.id)
            await self._reply(msg, f"Решение сохранено: #{decision_id}")
            return
        if cmd == "decisions":
            decisions = self.storage.list_decisions(chat_id=chat.id, thread_id=self._thread_id(update), limit=20)
            if not decisions and self._thread_id(update) is not None:
                decisions = self.storage.list_decisions(chat_id=chat.id, limit=20)
            if not decisions:
                await self._reply(msg, "Сохранённых решений пока нет.")
            else:
                await self._reply(msg, "\n\n".join(f"#{d['id']} | {d['created_at']}\n{d['text']}" for d in decisions))
            return
        if cmd == "forget_decision":
            if not self.admin.can_manage_memory(user.id):
                await self._reply(msg, "Удалять решения может владелец или методист.")
                return
            try:
                decision_id = int(args.strip())
            except Exception:
                await self._reply(msg, "Укажи ID решения: /forget_decision 1")
                return
            ok = self.storage.forget_decision(decision_id)
            await self._reply(msg, "Решение удалено." if ok else "Решение не найдено.")
            return
        if cmd == "feedback":
            if not args:
                await self._reply(msg, "Формат: /feedback хорошо текст или /feedback плохо текст")
                return
            raw = args.strip()
            first, _, rest = raw.partition(" ")
            rating_map = {
                "хорошо": "good", "good": "good", "+": "good", "ок": "good",
                "плохо": "bad", "bad": "bad", "-": "bad", "ошибка": "bad",
            }
            rating = rating_map.get(first.lower(), "note")
            feedback_text = rest if rating != "note" else raw
            feedback_id = self.storage.add_feedback(rating, feedback_text, chat.id, self._thread_id(update), user.id, user.username or "")
            await self._reply(msg, f"Обратная связь сохранена: #{feedback_id}")
            return
        if cmd == "feedbacks":
            if not self.admin.is_admin(user.id):
                await self._reply(msg, "Просмотр обратной связи доступен только владельцу.")
                return
            rows = self.storage.list_feedback(30)
            if not rows:
                await self._reply(msg, "Обратной связи пока нет.")
            else:
                await self._reply(msg, "\n\n".join(f"#{r['id']} | {r['rating']} | @{r.get('username') or ''}\n{r.get('text') or ''}" for r in rows))
            return
        if cmd == "search_history":
            if not args:
                await self._reply(msg, "Напиши запрос после /search_history.")
                return
            rows = self.storage.search_messages(args, chat_id=chat.id, thread_id=self._thread_id(update), limit=10)
            if not rows and self._thread_id(update) is not None:
                rows = self.storage.search_messages(args, chat_id=chat.id, limit=10)
            if not rows:
                await self._reply(msg, "В истории ничего не найдено.")
            else:
                lines = []
                for r in rows:
                    name = r.get("full_name") or r.get("username") or r.get("user_id")
                    lines.append(f"{r.get('created_at')} | {name}: {r.get('text')}")
                await self._reply(msg, "\n\n".join(lines))
            return
        if cmd == "trials":
            if not self.admin.can_manage_trials(user.id):
                await self._reply(msg, "Просмотр заявок доступен менеджеру, операционному менеджеру и владельцу. В личке с ботом напишите /start и выберите роль.")
                return
            trials = self.storage.list_trials("open", 20)
            if not trials:
                await self._reply(msg, "Открытых заявок на пробное нет.")
            else:
                await self._reply(msg, "\n\n---\n\n".join(format_trial_for_admin(t) for t in trials))
            return
        if cmd == "trial_start":
            if not self.admin.can_manage_trials(user.id):
                await self._reply(msg, "Создание заявок доступно менеджеру, операционному менеджеру и владельцу.")
                return
            data = extract_trial_data(args)
            trial_id = self.storage.create_or_update_trial(chat.id, self._thread_id(update), user.id, data)
            await self._reply(msg, f"Заявка #{trial_id} подготовлена. Посмотреть: /trials")
            return
        if cmd == "trial_info":
            if not self.admin.can_manage_trials(user.id):
                await self._reply(msg, "Изменение заявок доступно менеджеру, операционному менеджеру и владельцу.")
                return
            data = extract_trial_data(args)
            trial_id = self.storage.create_or_update_trial(chat.id, self._thread_id(update), user.id, data)
            await self._reply(msg, f"Данные добавлены к заявке #{trial_id}.")
            return
        if cmd == "trial_done":
            if not self.admin.can_manage_trials(user.id):
                await self._reply(msg, "Закрытие заявок доступно менеджеру, операционному менеджеру и владельцу.")
                return
            try:
                trial_id = int(args.strip())
            except Exception:
                await self._reply(msg, "Укажи ID: /trial_done 1")
                return
            ok = self.storage.close_trial(trial_id)
            await self._reply(msg, "Заявка закрыта." if ok else "Заявка не найдена.")
            return
        if cmd == "trial_cancel":
            if not self.admin.can_manage_trials(user.id):
                await self._reply(msg, "Отмена заявок доступна менеджеру, операционному менеджеру и владельцу.")
                return
            try:
                trial_id = int(args.strip())
            except Exception:
                await self._reply(msg, "Укажи ID: /trial_cancel 1")
                return
            ok = self.storage.cancel_trial(trial_id)
            await self._reply(msg, "Заявка отменена." if ok else "Заявка не найдена.")
            return
        if cmd == "mk_test":
            if not self.admin.is_admin(user.id):
                await self._reply(msg, "Проверка МойКласс доступна только владельцу.")
                return
            result = self.moyklass.test()
            if result.ok:
                await self._reply(msg, "МойКласс: подключение проверено.\n" + preview_json(result.data, 1500))
            else:
                await self._reply(msg, f"МойКласс: ошибка подключения.\nstatus={result.status}\n{result.error}")
            return

        if cmd in {"mk_teachers", "mk_teacher_list"}:
            if not self.admin.is_admin(user.id):
                await self._reply(msg, "Список преподавателей МойКласс доступен только владельцу.")
                return
            result = self.moyklass.get_teachers(args or "limit=50")
            await self._reply(msg, format_teachers_result(result, limit=50))
            return

        if cmd in {"mk_link_teacher", "mk_bind_teacher"}:
            if not self.admin.is_admin(user.id):
                await self._reply(msg, "Привязку Telegram к teacherId может делать только владелец.")
                return
            parts = args.strip().split(maxsplit=2)
            if len(parts) < 2 or not parts[0].isdigit() or not parts[1].isdigit():
                await self._reply(msg, "Формат: /mk_link_teacher TELEGRAM_ID MK_TEACHER_ID [имя]\nНапример: /mk_link_teacher 123456789 71190 Иван")
                return
            tg_id = int(parts[0])
            teacher_id = parts[1]
            teacher_name = parts[2] if len(parts) > 2 else ""
            self.storage.set_staff_mk_teacher(tg_id, teacher_id, teacher_name)
            await self._reply(msg, f"Привязка сохранена.\nTelegram ID: {tg_id}\nMoyKlass teacherId: {teacher_id}\nИмя: {teacher_name or '-'}")
            return

        if cmd in {"mk_unlink_teacher", "mk_unbind_teacher"}:
            if not self.admin.is_admin(user.id):
                await self._reply(msg, "Удалять привязку может только владелец.")
                return
            raw = args.strip().split()[0] if args.strip() else ""
            if not raw.isdigit():
                await self._reply(msg, "Формат: /mk_unlink_teacher TELEGRAM_ID")
                return
            ok = self.storage.clear_staff_mk_teacher(int(raw))
            await self._reply(msg, "Привязка удалена." if ok else "Пользователь не найден.")
            return

        if cmd in {"mk_my_teacher", "mk_teacher_me"}:
            await self._reply(msg, self._moyklass_binding_text(user.id))
            return

        if cmd in {"mk_names", "mk_name_list"}:
            if not self.admin.is_admin(user.id):
                await self._reply(msg, "Названия МойКласс может смотреть только владелец.")
                return
            await self._reply(msg, format_moyklass_manual_names())
            return

        if cmd in {"mk_name_class", "mk_map_class", "mk_name_group", "mk_map_group"}:
            if not self.admin.is_admin(user.id):
                await self._reply(msg, "Названия групп МойКласс может задавать только владелец.")
                return
            parts = args.strip().split(maxsplit=1)
            if len(parts) != 2 or not parts[0].isdigit():
                await self._reply(msg, "Формат: /mk_name_class CLASS_ID Название\nНапример: /mk_name_class 709199 Вторник 17:00")
                return
            ok = save_moyklass_manual_name("class", parts[0], parts[1])
            self.moyklass.clear_lookup_cache()
            await self._reply(msg, "Название группы сохранено." if ok else "Не удалось сохранить название группы.")
            return

        if cmd in {"mk_name_room", "mk_map_room"}:
            if not self.admin.is_admin(user.id):
                await self._reply(msg, "Названия кабинетов МойКласс может задавать только владелец.")
                return
            parts = args.strip().split(maxsplit=1)
            if len(parts) != 2 or not parts[0].isdigit():
                await self._reply(msg, "Формат: /mk_name_room ROOM_ID Название\nНапример: /mk_name_room 42756 Учебный класс YC1")
                return
            ok = save_moyklass_manual_name("room", parts[0], parts[1])
            self.moyklass.clear_lookup_cache()
            await self._reply(msg, "Название кабинета сохранено." if ok else "Не удалось сохранить название кабинета.")
            return

        if cmd in {"mk_name_teacher", "mk_map_teacher"}:
            if not self.admin.is_admin(user.id):
                await self._reply(msg, "Имена преподавателей МойКласс может задавать только владелец.")
                return
            parts = args.strip().split(maxsplit=1)
            if len(parts) != 2 or not parts[0].isdigit():
                await self._reply(msg, "Формат: /mk_name_teacher TEACHER_ID Имя Фамилия\nНапример: /mk_name_teacher 220305 Иван Иванов")
                return
            ok = save_moyklass_manual_name("teacher", parts[0], parts[1])
            self.moyklass.clear_lookup_cache()
            await self._reply(msg, "Имя преподавателя сохранено." if ok else "Не удалось сохранить имя преподавателя.")
            return

        if cmd in {"mk_name_delete", "mk_unname"}:
            if not self.admin.is_admin(user.id):
                await self._reply(msg, "Удалять названия МойКласс может только владелец.")
                return
            parts = args.strip().split(maxsplit=1)
            if len(parts) != 2:
                await self._reply(msg, "Формат: /mk_name_delete class|room|teacher ID")
                return
            ok = delete_moyklass_manual_name(parts[0], parts[1])
            self.moyklass.clear_lookup_cache()
            await self._reply(msg, "Название удалено." if ok else "Название не найдено.")
            return

        if cmd == "mk_lessons":
            if not self._can_read_moyklass_schedule(user.id):
                await self._reply(msg, "Чтение расписания МойКласс доступно владельцу, методисту и операционному менеджеру.")
                return
            result = self.moyklass.get_lessons(args)
            await self._reply(msg, format_lessons_result(result, limit=20))
            return

        if cmd in {"mk_today", "mk_lessons_today", "mk_my_today", "teacher_today"}:
            role = self.admin.get_role(user.id) or "other"
            if role in {"teacher", "methodist"} and not (self.admin.is_admin(user.id) or role == "owner"):
                await self._show_my_lessons(msg, user.id, days=1)
                return
            if not self._can_read_moyklass_schedule(user.id):
                await self._reply(msg, "Расписание МойКласс доступно владельцу, методисту и операционному менеджеру. Преподавателю доступно только своё расписание после привязки teacherId.")
                return
            result = self.moyklass.get_upcoming_lessons(days=1, limit=80)
            if chat.type == "private":
                await self._show_lessons_with_open_buttons(msg, user.id, result, title="Занятия на сегодня", limit=30)
            else:
                await self._reply(msg, format_lessons_result(result, limit=30, title="Занятия на сегодня"))
            return

        if cmd in {"mk_tomorrow", "mk_lessons_tomorrow"}:
            if not self._can_read_moyklass_schedule(user.id):
                await self._reply(msg, "Расписание МойКласс доступно владельцу, методисту и операционному менеджеру.")
                return
            from datetime import date, timedelta
            tomorrow = date.today() + timedelta(days=1)
            result = self.moyklass.get_lessons_on_date(tomorrow)
            if chat.type == "private":
                await self._show_lessons_with_open_buttons(msg, user.id, result, title="Занятия на завтра", limit=30)
            else:
                await self._reply(msg, format_lessons_result(result, limit=30, title="Занятия на завтра"))
            return

        if cmd in {"mk_week", "mk_lessons_week", "mk_my_week", "teacher_week"}:
            role = self.admin.get_role(user.id) or "other"
            if role in {"teacher", "methodist"} and not (self.admin.is_admin(user.id) or role == "owner"):
                await self._show_my_lessons(msg, user.id, days=7)
                return
            if not self._can_read_moyklass_schedule(user.id):
                await self._reply(msg, "Расписание МойКласс доступно владельцу, методисту и операционному менеджеру. Преподавателю доступно только своё расписание после привязки teacherId.")
                return
            result = self.moyklass.get_upcoming_lessons(days=7, limit=120)
            if chat.type == "private":
                await self._show_lessons_with_open_buttons(msg, user.id, result, title="Занятия на ближайшие 7 дней", limit=40)
            else:
                await self._reply(msg, format_lessons_result(result, limit=40, title="Занятия на ближайшие 7 дней"))
            return

        if cmd in {"mk_date", "mk_lessons_date"}:
            if not self._can_read_moyklass_schedule(user.id):
                await self._reply(msg, "Расписание МойКласс доступно владельцу, методисту и операционному менеджеру.")
                return
            if not args.strip():
                await self._reply(msg, "Формат: /mk_date 2026-06-13")
                return
            result = self.moyklass.get_lessons_on_date(args.strip().split()[0])
            if chat.type == "private":
                await self._show_lessons_with_open_buttons(msg, user.id, result, title=f"Занятия на {args.strip().split()[0]}", limit=40)
            else:
                await self._reply(msg, format_lessons_result(result, limit=40, title=f"Занятия на {args.strip().split()[0]}"))
            return

        if cmd in {"mk_range", "mk_lessons_range"}:
            if not self._can_read_moyklass_schedule(user.id):
                await self._reply(msg, "Расписание МойКласс доступно владельцу, методисту и операционному менеджеру.")
                return
            parts = args.split()
            if len(parts) < 2:
                await self._reply(msg, "Формат: /mk_range 2026-06-13 2026-06-20")
                return
            result = self.moyklass.get_lessons_between(parts[0], parts[1], limit=150)
            if chat.type == "private":
                await self._show_lessons_with_open_buttons(msg, user.id, result, title=f"Занятия с {parts[0]} по {parts[1]}", limit=50)
            else:
                await self._reply(msg, format_lessons_result(result, limit=50, title=f"Занятия с {parts[0]} по {parts[1]}"))
            return

        if cmd in {"mk_report", "mk_month_report", "mk_analytics", "mk_stats"}:
            if not (self.admin.is_admin(user.id) or self.admin.get_role(user.id) in {"owner", "manager", "operations"}):
                await self._reply(msg, "Отчёт МойКласс доступен владельцу, менеджеру и операционному менеджеру.")
                return
            month = args.strip().split()[0] if args.strip() else ""
            result = self.moyklass.get_month_analytics(month or None)
            await self._reply(msg, format_month_analytics_result(result), reply_markup=moyklass_keyboard(self.admin.get_role(user.id) or "other"))
            return

        if cmd in {"mk_attendance", "mk_visits", "mk_records"}:
            if not (self.admin.is_admin(user.id) or self.admin.get_role(user.id) in {"owner", "manager", "operations"}):
                await self._reply(msg, "Посещения и записи занятий доступны владельцу, менеджеру и операционному менеджеру.")
                return
            parts = args.strip().split()
            month = parts[0] if parts else ""
            result = self.moyklass.get_month_lesson_records(month or None)
            await self._reply(msg, format_lesson_records_result(result), reply_markup=moyklass_keyboard(self.admin.get_role(user.id) or "other"))
            return

        if cmd in {"mk_payments", "mk_finance", "mk_income"}:
            if not (self.admin.is_admin(user.id) or self.admin.get_role(user.id) in {"owner", "manager", "operations"}):
                await self._reply(msg, "Оплаты доступны владельцу, менеджеру и операционному менеджеру.")
                return
            parts = args.strip().split()
            month = parts[0] if parts else ""
            result = self.moyklass.get_month_payments(month or None)
            await self._reply(msg, format_payments_result(result), reply_markup=moyklass_keyboard(self.admin.get_role(user.id) or "other"))
            return

        if cmd in {"mk_active_clients", "mk_clients_active", "mk_clients_month"}:
            if not (self.admin.is_admin(user.id) or self.admin.get_role(user.id) in {"owner", "manager", "operations"}):
                await self._reply(msg, "Активные клиенты доступны владельцу, менеджеру и операционному менеджеру.")
                return
            parts = args.strip().split()
            month = parts[0] if parts else ""
            result = self.moyklass.get_month_lesson_records(month or None)
            await self._reply(msg, format_active_clients_result(result), reply_markup=moyklass_keyboard(self.admin.get_role(user.id) or "other"))
            return

        if cmd in {"mk_tasks_mk", "mk_work_tasks", "mk_manager_tasks"}:
            if not (self.admin.is_admin(user.id) or self.admin.get_role(user.id) in {"owner", "manager", "operations"}):
                await self._reply(msg, "Задачи МойКласс доступны владельцу, менеджеру и операционному менеджеру.")
                return
            parts = args.strip().split()
            month = parts[0] if parts else ""
            result = self.moyklass.get_month_analytics(month or None)
            await self._reply(msg, format_moyklass_work_tasks_result(result), reply_markup=moyklass_keyboard(self.admin.get_role(user.id) or "other"))
            return

        if cmd in {"mk_absences", "mk_missed", "mk_workoffs"}:
            if not (self.admin.is_admin(user.id) or self.admin.get_role(user.id) in {"owner", "manager", "operations"}):
                await self._reply(msg, "Пропуски и отработки доступны владельцу, менеджеру и операционному менеджеру.")
                return
            month = args.strip().split()[0] if args.strip() else ""
            result = self.moyklass.get_month_absences(month or None)
            await self._reply(msg, format_absences_result(result), reply_markup=moyklass_keyboard(self.admin.get_role(user.id) or "other"))
            return

        if cmd in {"mk_unpaid", "mk_no_payments", "mk_unpaid_active"}:
            if not (self.admin.is_admin(user.id) or self.admin.get_role(user.id) in {"owner", "manager", "operations"}):
                await self._reply(msg, "Список активных без оплат доступен владельцу, менеджеру и операционному менеджеру.")
                return
            month = args.strip().split()[0] if args.strip() else ""
            result = self.moyklass.get_unpaid_active_clients(month or None)
            await self._reply(msg, format_unpaid_active_clients_result(result), reply_markup=moyklass_keyboard(self.admin.get_role(user.id) or "other"))
            return

        if cmd in {"mk_low_attendance", "mk_bad_attendance"}:
            if not (self.admin.is_admin(user.id) or self.admin.get_role(user.id) in {"owner", "manager", "operations"}):
                await self._reply(msg, "Отчёт по низкой посещаемости доступен владельцу, менеджеру и операционному менеджеру.")
                return
            parts = args.strip().split()
            month = parts[0] if parts else ""
            threshold = 60.0
            if len(parts) > 1:
                try:
                    threshold = float(parts[1].replace(",", "."))
                except Exception:
                    threshold = 60.0
            result = self.moyklass.get_month_low_attendance(month or None, threshold=threshold)
            await self._reply(msg, format_low_attendance_result(result), reply_markup=moyklass_keyboard(self.admin.get_role(user.id) or "other"))
            return

        if cmd in {"mk_teacher_month", "mk_teachers_month", "mk_teacher_analytics"}:
            if not (self.admin.is_admin(user.id) or self.admin.get_role(user.id) in {"owner", "manager", "operations", "methodist"}):
                await self._reply(msg, "Отчёт по преподавателям доступен владельцу, менеджеру, методисту и операционному менеджеру.")
                return
            month = args.strip().split()[0] if args.strip() else ""
            result = self.moyklass.get_teacher_month_report(month or None)
            await self._reply(msg, format_teacher_month_report_result(result), reply_markup=moyklass_keyboard(self.admin.get_role(user.id) or "other"))
            return

        if cmd in {"mk_daily_tasks", "mk_today_tasks", "mk_day_tasks"}:
            if not (self.admin.is_admin(user.id) or self.admin.get_role(user.id) in {"owner", "manager", "operations", "methodist"}):
                await self._reply(msg, "Ежедневные задачи МойКласс доступны владельцу, менеджеру, методисту и операционному менеджеру.")
                return
            day = args.strip().split()[0] if args.strip() else ""
            result = self.moyklass.get_daily_tasks(day or None)
            await self._reply(msg, format_daily_tasks_result(result), reply_markup=moyklass_keyboard(self.admin.get_role(user.id) or "other"))
            return

        if cmd in {"mk_lesson_records", "mk_lesson_attendance"}:
            if not (self.admin.is_admin(user.id) or self.admin.get_role(user.id) in {"owner", "manager", "operations", "teacher", "methodist"}):
                await self._reply(msg, "Записи занятия доступны сотрудникам с доступом к МойКласс.")
                return
            lesson_id = args.strip().split()[0] if args.strip() else self._selected_lesson_id(user.id)
            if not str(lesson_id).isdigit():
                await self._reply(msg, "Формат: /mk_lesson_records LESSON_ID")
                return
            lesson_result = self.moyklass.get_lesson_by_id(lesson_id)
            if self.admin.get_role(user.id) in {"teacher", "methodist"} and not self._lesson_result_belongs_to_user(user.id, lesson_result):
                await self._reply(msg, "Это занятие не относится к вашему teacherId. Доступ закрыт.")
                return
            result = self.moyklass.get_lesson_records(lesson_id)
            await self._reply(msg, format_lesson_records_result(result, limit=50), reply_markup=moyklass_keyboard(self.admin.get_role(user.id) or "other"))
            return

        if cmd in {"mk_class_records", "mk_group_records"}:
            if not self._can_read_moyklass_classes(user.id):
                await self._reply(msg, "Записи группы доступны владельцу, менеджеру, методисту и операционному менеджеру.")
                return
            parts = args.strip().split()
            if not parts or not parts[0].isdigit():
                await self._reply(msg, "Формат: /mk_class_records CLASS_ID 2026-06")
                return
            class_id = parts[0]
            month = parts[1] if len(parts) > 1 else ""
            result = self.moyklass.get_class_records_month(class_id, month or None)
            await self._reply(msg, format_lesson_records_result(result, limit=60), reply_markup=moyklass_keyboard(self.admin.get_role(user.id) or "other"))
            return

        if cmd in {"mk_missing_topics", "mk_no_topic"}:
            if not (self.admin.is_admin(user.id) or self.admin.get_role(user.id) in {"owner", "manager", "operations"}):
                await self._reply(msg, "Список занятий без темы доступен владельцу, менеджеру и операционному менеджеру.")
                return
            month = args.strip().split()[0] if args.strip() else ""
            result = self.moyklass.get_month_issue_lessons(month or None, "missing_topic")
            if chat.type == "private":
                await self._show_lessons_with_open_buttons(msg, user.id, result, title="Занятия без темы", limit=50)
            else:
                await self._reply(msg, format_month_issue_lessons_result(result, limit=30))
            return

        if cmd in {"mk_missing_comments", "mk_no_comment"}:
            if not (self.admin.is_admin(user.id) or self.admin.get_role(user.id) in {"owner", "manager", "operations"}):
                await self._reply(msg, "Список занятий без комментария доступен владельцу, менеджеру и операционному менеджеру.")
                return
            month = args.strip().split()[0] if args.strip() else ""
            result = self.moyklass.get_month_issue_lessons(month or None, "missing_comment")
            if chat.type == "private":
                await self._show_lessons_with_open_buttons(msg, user.id, result, title="Занятия без комментария", limit=50)
            else:
                await self._reply(msg, format_month_issue_lessons_result(result, limit=30))
            return

        if cmd in {"mk_no_teacher", "mk_missing_teacher"}:
            if not (self.admin.is_admin(user.id) or self.admin.get_role(user.id) in {"owner", "manager", "operations"}):
                await self._reply(msg, "Список занятий без преподавателя доступен владельцу, менеджеру и операционному менеджеру.")
                return
            month = args.strip().split()[0] if args.strip() else ""
            result = self.moyklass.get_month_issue_lessons(month or None, "no_teacher")
            if chat.type == "private":
                await self._show_lessons_with_open_buttons(msg, user.id, result, title="Занятия без преподавателя", limit=50)
            else:
                await self._reply(msg, format_month_issue_lessons_result(result, limit=30))
            return

        if cmd in {"mk_no_room", "mk_missing_room"}:
            if not (self.admin.is_admin(user.id) or self.admin.get_role(user.id) in {"owner", "manager", "operations"}):
                await self._reply(msg, "Список занятий без кабинета доступен владельцу, менеджеру и операционному менеджеру.")
                return
            month = args.strip().split()[0] if args.strip() else ""
            result = self.moyklass.get_month_issue_lessons(month or None, "no_room")
            if chat.type == "private":
                await self._show_lessons_with_open_buttons(msg, user.id, result, title="Занятия без кабинета", limit=50)
            else:
                await self._reply(msg, format_month_issue_lessons_result(result, limit=30))
            return

        if cmd in {"mk_teacher_report", "mk_teachers_report"}:
            if not (self.admin.is_admin(user.id) or self.admin.get_role(user.id) in {"owner", "manager", "operations"}):
                await self._reply(msg, "Сводка по преподавателям доступна владельцу, менеджеру и операционному менеджеру.")
                return
            month = args.strip().split()[0] if args.strip() else ""
            result = self.moyklass.get_month_lesson_breakdown(month or None, "teacher")
            await self._reply(msg, format_month_lesson_breakdown_result(result), reply_markup=moyklass_keyboard(self.admin.get_role(user.id) or "other"))
            return

        if cmd in {"mk_group_report", "mk_groups_report"}:
            if not (self.admin.is_admin(user.id) or self.admin.get_role(user.id) in {"owner", "manager", "operations"}):
                await self._reply(msg, "Сводка по группам доступна владельцу, менеджеру и операционному менеджеру.")
                return
            month = args.strip().split()[0] if args.strip() else ""
            result = self.moyklass.get_month_lesson_breakdown(month or None, "group")
            await self._reply(msg, format_month_lesson_breakdown_result(result), reply_markup=moyklass_keyboard(self.admin.get_role(user.id) or "other"))
            return

        if cmd in {"mk_room_report", "mk_rooms_report"}:
            if not (self.admin.is_admin(user.id) or self.admin.get_role(user.id) in {"owner", "manager", "operations"}):
                await self._reply(msg, "Сводка по кабинетам доступна владельцу, менеджеру и операционному менеджеру.")
                return
            month = args.strip().split()[0] if args.strip() else ""
            result = self.moyklass.get_month_lesson_breakdown(month or None, "room")
            await self._reply(msg, format_month_lesson_breakdown_result(result), reply_markup=moyklass_keyboard(self.admin.get_role(user.id) or "other"))
            return

        if cmd in {"mk_report_debug", "mk_analytics_debug"}:
            if not self.admin.is_admin(user.id):
                await self._reply(msg, "Диагностика отчёта МойКласс доступна только владельцу.")
                return
            month = args.strip().split()[0] if args.strip() else ""
            result = self.moyklass.get_month_analytics(month or None)
            await self._reply(msg, format_month_analytics_debug(result), reply_markup=moyklass_keyboard(self.admin.get_role(user.id) or "other"))
            return

        if cmd in {"mk_report_probe", "mk_analytics_probe", "mk_find_analytics"}:
            if not self.admin.is_admin(user.id):
                await self._reply(msg, "Поиск endpoint аналитики МойКласс доступен только владельцу.")
                return
            month = args.strip().split()[0] if args.strip() else ""
            result = self.moyklass.probe_analytics_sources(month or None)
            await self._reply(msg, format_month_analytics_probe(result), reply_markup=moyklass_keyboard(self.admin.get_role(user.id) or "other"))
            return

        if cmd in {"mk_groups", "mk_classes"}:
            if not self._can_read_moyklass_classes(user.id):
                await self._reply(msg, "Просмотр групп МойКласс доступен владельцу, менеджеру, методисту и операционному менеджеру.")
                return
            result = self.moyklass.search_classes(args, limit=40) if args.strip() else self.moyklass.get_classes("limit=80")
            if chat.type == "private":
                await self._show_classes_with_open_buttons(msg, user.id, result, title="Группы МойКласс", limit=40)
            else:
                await self._reply(msg, format_classes_result(result, limit=40, title="Группы МойКласс"))
            return

        if cmd in {"mk_class", "mk_group"}:
            if not self._can_read_moyklass_classes(user.id):
                await self._reply(msg, "Просмотр группы доступен владельцу, менеджеру, методисту и операционному менеджеру.")
                return
            class_id = args.strip().split()[0] if args.strip() else ""
            if not class_id.isdigit():
                await self._reply(msg, "Формат: /mk_class CLASS_ID")
                return
            if chat.type == "private":
                await self._show_class_card(msg, user.id, class_id)
            else:
                result = self.moyklass.get_class_by_id(class_id)
                await self._reply(msg, format_class_preview(result))
            return

        if cmd in {"mk_class_users", "mk_group_users", "mk_class_students", "mk_group_students"}:
            if not self._can_read_moyklass_classes(user.id):
                await self._reply(msg, "Ученики группы доступны владельцу, менеджеру и операционному менеджеру.")
                return
            class_id = args.strip().split()[0] if args.strip() else ""
            if not class_id.isdigit():
                await self._reply(msg, "Формат: /mk_class_users CLASS_ID")
                return
            result = self.moyklass.get_class_users(class_id, limit=80)
            await self._reply(msg, format_class_users_result(result, limit=60, include_contacts=bool(self.admin.is_admin(user.id))))
            return

        if cmd in {"mk_class_lessons", "mk_group_lessons"}:
            if not self._can_read_moyklass_classes(user.id):
                await self._reply(msg, "Занятия группы доступны владельцу, менеджеру, методисту и операционному менеджеру.")
                return
            class_id = args.strip().split()[0] if args.strip() else ""
            if not class_id.isdigit():
                await self._reply(msg, "Формат: /mk_class_lessons CLASS_ID")
                return
            result = self.moyklass.get_class_lessons(class_id, days=30, limit=120)
            if chat.type == "private":
                await self._show_lessons_with_open_buttons(msg, user.id, result, title=f"Ближайшие занятия группы {class_id}", limit=40)
            else:
                await self._reply(msg, format_lessons_result(result, limit=40, title=f"Ближайшие занятия группы {class_id}"))
            return

        if cmd in {"mk_users", "mk_students"}:
            if not self._can_read_moyklass_users(user.id):
                await self._reply(msg, "Чтение учеников МойКласс доступно владельцу, менеджеру и операционному менеджеру.")
                return
            result = self.moyklass.search_users(args, limit=10) if args.strip() else self.moyklass.get_users("limit=10")
            await self._reply(msg, format_users_result(result, limit=10, include_contacts=False))
            return

        if cmd in {"mk_users_full", "mk_students_full"}:
            if not self.admin.is_admin(user.id):
                await self._reply(msg, "Полный вывод контактов доступен только владельцу.")
                return
            result = self.moyklass.search_users(args, limit=10) if args.strip() else self.moyklass.get_users("limit=10")
            await self._reply(msg, format_users_result(result, limit=10, include_contacts=True))
            return


        if cmd in {"mk_user", "mk_student", "mk_client"}:
            if not self._can_read_moyklass_users(user.id):
                await self._reply(msg, "Карточка ученика доступна владельцу, менеджеру и операционному менеджеру.")
                return
            parts = args.strip().split()
            if not parts or not parts[0].replace("#", "").isdigit():
                await self._reply(msg, "Формат: /mk_user USER_ID 2026-06\nНапример: /mk_user 6222670 2026-06")
                return
            mk_user_id = parts[0].replace("#", "")
            month = parts[1] if len(parts) > 1 else None
            if chat.type == "private":
                await self._show_user_card(msg, user.id, mk_user_id, month)
            else:
                result = self.moyklass.get_user_card(mk_user_id, month)
                await self._reply(msg, format_user_card_result(result))
            return

        if cmd in {"mk_user_lessons", "mk_student_lessons", "mk_user_records"}:
            if not self._can_read_moyklass_users(user.id):
                await self._reply(msg, "Занятия ученика доступны владельцу, менеджеру и операционному менеджеру.")
                return
            parts = args.strip().split()
            if not parts or not parts[0].replace("#", "").isdigit():
                await self._reply(msg, "Формат: /mk_user_lessons USER_ID 2026-06")
                return
            mk_user_id = parts[0].replace("#", "")
            month = parts[1] if len(parts) > 1 else None
            result = self.moyklass.get_user_lessons_month(mk_user_id, month)
            if chat.type == "private" and result.ok:
                self.private_states[user.id] = {"mode": "mk_user_selected", "selected_user_id": mk_user_id}
                if month:
                    self.private_states[user.id]["selected_user_month"] = month
                await self._reply(msg, format_user_lessons_result(result), reply_markup=selected_user_keyboard(self.admin.get_role(user.id) or "other"))
            else:
                await self._reply(msg, format_user_lessons_result(result))
            return

        if cmd in {"mk_user_payments", "mk_student_payments"}:
            if not self._can_read_moyklass_users(user.id):
                await self._reply(msg, "Оплаты ученика доступны владельцу, менеджеру и операционному менеджеру.")
                return
            parts = args.strip().split()
            if not parts or not parts[0].replace("#", "").isdigit():
                await self._reply(msg, "Формат: /mk_user_payments USER_ID 2026-06")
                return
            mk_user_id = parts[0].replace("#", "")
            month = parts[1] if len(parts) > 1 else None
            result = self.moyklass.get_user_payments_month(mk_user_id, month)
            if chat.type == "private" and result.ok:
                self.private_states[user.id] = {"mode": "mk_user_selected", "selected_user_id": mk_user_id}
                if month:
                    self.private_states[user.id]["selected_user_month"] = month
                await self._reply(msg, format_user_payments_result(result), reply_markup=selected_user_keyboard(self.admin.get_role(user.id) or "other"))
            else:
                await self._reply(msg, format_user_payments_result(result))
            return

        if cmd in {"mk_user_absences", "mk_student_absences"}:
            if not self._can_read_moyklass_users(user.id):
                await self._reply(msg, "Пропуски ученика доступны владельцу, менеджеру и операционному менеджеру.")
                return
            parts = args.strip().split()
            if not parts or not parts[0].replace("#", "").isdigit():
                await self._reply(msg, "Формат: /mk_user_absences USER_ID 2026-06")
                return
            mk_user_id = parts[0].replace("#", "")
            month = parts[1] if len(parts) > 1 else None
            result = self.moyklass.get_user_absences_month(mk_user_id, month)
            if chat.type == "private" and result.ok:
                self.private_states[user.id] = {"mode": "mk_user_selected", "selected_user_id": mk_user_id}
                if month:
                    self.private_states[user.id]["selected_user_month"] = month
                await self._reply(msg, format_user_absences_result(result), reply_markup=selected_user_keyboard(self.admin.get_role(user.id) or "other"))
            else:
                await self._reply(msg, format_user_absences_result(result))
            return

        if cmd in {"mk_user_joins", "mk_student_joins", "mk_user_groups"}:
            if not self._can_read_moyklass_users(user.id):
                await self._reply(msg, "Группы ученика доступны владельцу, менеджеру и операционному менеджеру.")
                return
            mk_user_id = args.strip().split()[0].replace("#", "") if args.strip() else ""
            if not mk_user_id.isdigit():
                await self._reply(msg, "Формат: /mk_user_joins USER_ID")
                return
            result = self.moyklass.get_user_joins(mk_user_id, limit=100)
            if chat.type == "private" and result.ok:
                self.private_states[user.id] = {"mode": "mk_user_selected", "selected_user_id": mk_user_id}
                await self._reply(msg, format_user_joins_result(result), reply_markup=selected_user_keyboard(self.admin.get_role(user.id) or "other"))
            else:
                await self._reply(msg, format_user_joins_result(result))
            return

        if cmd in {"mk_prepare_user_comment", "mk_user_comment"}:
            if not self._can_write_moyklass(user.id):
                await self._reply(msg, "Комментарий в карточку ученика доступен владельцу, менеджеру и операционному менеджеру.")
                return
            parts = args.strip().split(maxsplit=1)
            if len(parts) != 2 or not parts[0].replace("#", "").isdigit():
                await self._reply(msg, "Формат: /mk_prepare_user_comment USER_ID текст")
                return
            mk_user_id = parts[0].replace("#", "")
            comment = parts[1].strip()
            current = self.moyklass.get_user_card(mk_user_id, None)
            current_text = format_user_card_result(current) if current.ok else f"Карточку получить не удалось: status={current.status} {current.error}"
            await self._prepare_mk_write(
                msg,
                user.id,
                action="create_user_comment",
                object_type="user",
                object_id=mk_user_id,
                method="POST",
                path="/v1/company/userComments",
                payload={"userId": int(mk_user_id), "comment": comment, "showToUser": False},
                current_text=current_text,
                allowed_user_id=user.id,
            )
            return

        if cmd in {"mk_get"}:
            if not self.admin.is_admin(user.id):
                await self._reply(msg, "Сырой GET к МойКласс доступен только владельцу.")
                return
            raw = args.strip()
            if not raw:
                await self._reply(msg, "Формат: /mk_get /v1/company/lessons dateFrom=2026-06-13 dateTo=2026-06-14 limit=20")
                return
            path, _, param_text = raw.partition(" ")
            params = {}
            for part in param_text.split():
                if "=" in part:
                    k, v = part.split("=", 1)
                    params[k.strip()] = v.strip()
            result = self.moyklass.request("GET", path, params=params)
            if result.ok:
                await self._reply(msg, f"GET {path}\nstatus={result.status}\n\n" + preview_json(result.data, 3200))
            else:
                await self._reply(msg, f"GET {path}\nstatus={result.status}\n{result.error}")
            return

        if cmd in {"mk_lesson", "mk_get_lesson"}:
            lesson_id = args.strip().split()[0] if args.strip() else ""
            if not lesson_id.isdigit():
                await self._reply(msg, "Формат: /mk_lesson LESSON_ID")
                return
            result = self.moyklass.get_lesson_by_id(lesson_id)
            role = self.admin.get_role(user.id) or "other"
            if self._can_read_moyklass_schedule(user.id):
                if chat.type == "private" and result.ok:
                    self.private_states[user.id] = {"mode": "mk_lesson_selected", "selected_lesson_id": lesson_id}
                    await self._reply(msg, format_lesson_preview(result), reply_markup=selected_lesson_keyboard(role))
                else:
                    await self._reply(msg, format_lesson_preview(result))
                return
            if role in {"teacher", "methodist"}:
                if not self._mk_teacher_id_for_user(user.id):
                    await self._reply(msg, self._moyklass_binding_text(user.id))
                    return
                if not self._lesson_result_belongs_to_user(user.id, result):
                    await self._reply(msg, "Это занятие не относится к вашему teacherId. Доступ закрыт.")
                    return
                if chat.type == "private" and result.ok:
                    self.private_states[user.id] = {"mode": "mk_lesson_selected", "selected_lesson_id": lesson_id}
                    await self._reply(msg, format_lesson_preview(result), reply_markup=selected_lesson_keyboard(role))
                else:
                    await self._reply(msg, format_lesson_preview(result))
                return
            await self._reply(msg, "Чтение занятия МойКласс доступно владельцу, методисту, операционному менеджеру или преподавателю только для своих занятий.")
            return

        if cmd in {"mk_prepare_comment", "mk_lesson_comment"}:
            parts = args.strip().split(maxsplit=1)
            if len(parts) != 2 or not parts[0].isdigit():
                await self._reply(msg, "Формат: /mk_prepare_comment LESSON_ID комментарий\nНапример: /mk_prepare_comment 16330521 Иван - отработка")
                return
            lesson_id, comment = parts[0], parts[1].strip()
            current = self.moyklass.get_lesson_by_id(lesson_id)
            allowed, reason = self._can_prepare_lesson_update(user.id, current, "comment")
            if not allowed:
                await self._reply(msg, reason)
                return
            current_text = format_lesson_preview(current, title=f"Текущее занятие #{lesson_id}") if current.ok else f"Текущее занятие получить не удалось: status={current.status} {current.error}"
            await self._prepare_mk_write(
                msg,
                user.id,
                action="update_lesson_fields",
                object_type="lesson",
                object_id=lesson_id,
                method="POST",
                path=f"/v1/company/lessons/{lesson_id}",
                payload={"comment": comment},
                current_text=current_text,
                allowed_user_id=user.id,
            )
            return

        if cmd in {"mk_prepare_topic", "mk_lesson_topic"}:
            parts = args.strip().split(maxsplit=1)
            if len(parts) != 2 or not parts[0].isdigit():
                await self._reply(msg, "Формат: /mk_prepare_topic LESSON_ID тема\nНапример: /mk_prepare_topic 16330521 Python - 5 тема - Условия")
                return
            lesson_id, topic = parts[0], parts[1].strip()
            current = self.moyklass.get_lesson_by_id(lesson_id)
            allowed, reason = self._can_prepare_lesson_update(user.id, current, "topic")
            if not allowed:
                await self._reply(msg, reason)
                return
            current_text = format_lesson_preview(current, title=f"Текущее занятие #{lesson_id}") if current.ok else f"Текущее занятие получить не удалось: status={current.status} {current.error}"
            await self._prepare_mk_write(
                msg,
                user.id,
                action="update_lesson_fields",
                object_type="lesson",
                object_id=lesson_id,
                method="POST",
                path=f"/v1/company/lessons/{lesson_id}",
                payload={"topic": topic},
                current_text=current_text,
                allowed_user_id=user.id,
            )
            return

        if cmd in {"mk_prepare_time", "mk_lesson_time"}:
            if not self._can_write_moyklass(user.id):
                await self._reply(msg, "Изменения времени в МойКласс пока доступны только владельцу.")
                return
            parts = args.strip().split(maxsplit=4)
            if len(parts) < 4 or not parts[0].isdigit():
                await self._reply(msg, "Формат: /mk_prepare_time LESSON_ID YYYY-MM-DD HH:MM HH:MM\nНапример: /mk_prepare_time 16330521 2026-06-13 16:00 17:30")
                return
            lesson_id, day, begin, end = parts[0], parts[1], parts[2], parts[3]
            current = self.moyklass.get_lesson_by_id(lesson_id)
            current_text = format_lesson_preview(current, title=f"Текущее занятие #{lesson_id}") if current.ok else f"Текущее занятие получить не удалось: status={current.status} {current.error}"
            await self._prepare_mk_write(
                msg,
                user.id,
                action="update_lesson_fields",
                object_type="lesson",
                object_id=lesson_id,
                method="POST",
                path=f"/v1/company/lessons/{lesson_id}",
                payload={"date": day, "beginTime": begin, "endTime": end},
                current_text=current_text,
            )
            return

        if cmd == "mk_write_raw":
            if not self.admin.is_admin(user.id):
                await self._reply(msg, "Сырой write-запрос к МойКласс доступен только владельцу.")
                return
            raw = args.strip()
            if not raw:
                await self._reply(msg, "Формат: /mk_write_raw PATCH /v1/company/lessons/ID {\"comment\":\"текст\"}\nКоманда только готовит изменение. Отправка - через /mk_confirm.")
                return
            parts = raw.split(maxsplit=2)
            if len(parts) != 3:
                await self._reply(msg, "Формат: /mk_write_raw METHOD PATH JSON")
                return
            method, path, payload_text = parts[0].upper(), parts[1], parts[2]
            try:
                payload = json.loads(payload_text)
                if not isinstance(payload, dict):
                    raise ValueError("JSON должен быть объектом")
            except Exception as e:
                await self._reply(msg, f"Не удалось разобрать JSON: {e}")
                return
            await self._prepare_mk_write(
                msg,
                user.id,
                action="raw_write",
                object_type="raw",
                object_id=path,
                method=method,
                path=path,
                payload=payload,
            )
            return

        if cmd == "mk_pending":
            pending = self.pending_mk_writes.get(user.id)
            if not pending:
                await self._reply(msg, "Нет подготовленного изменения.")
            else:
                await self._reply(msg, "Подготовленное изменение:\n" + "\n".join([
                    f"Действие: {pending.get('action')}",
                    f"Объект: {pending.get('object_type')} {pending.get('object_id')}",
                    f"Запрос: {pending.get('method')} {pending.get('path')}",
                    "Тело:",
                    self._compact_payload(pending.get('payload') or {}),
                    "",
                    "Отправить: /mk_confirm",
                    "Отменить: /mk_cancel",
                ]))
            return

        if cmd == "mk_confirm":
            await self._execute_mk_pending(msg, user.id)
            return

        if cmd == "mk_cancel":
            if user.id in self.pending_mk_writes:
                self.pending_mk_writes.pop(user.id, None)
                await self._reply(msg, "Подготовленное изменение отменено.")
            else:
                await self._reply(msg, "Нет подготовленного изменения.")
            return

        if cmd == "mk_audit":
            if not self.admin.is_admin(user.id):
                await self._reply(msg, "Журнал изменений МойКласс доступен только владельцу.")
                return
            rows = self.storage.list_mk_audit(30)
            if not rows:
                await self._reply(msg, "Журнал изменений МойКласс пока пуст.")
            else:
                lines = []
                for r in rows:
                    ok = "ok" if r.get("ok") else "error"
                    lines.append(f"#{r['id']} | {r['created_at']} | {ok} | user:{r.get('user_id')} | {r.get('method')} {r.get('path')} | status:{r.get('status')}")
                await self._reply(msg, "\n".join(lines))
            return

        if cmd in {"users", "staff"}:
            if not self.admin.is_admin(user.id):
                await self._reply(msg, "Список сотрудников доступен только владельцу.")
                return
            users = self.storage.list_staff_users(100)
            if not users:
                await self._reply(msg, "Сотрудники ещё не зарегистрированы.")
            else:
                await self._reply(msg, "\n".join(
                    f"{u['user_id']} | {self.admin.role_label(u.get('role'))} | {u.get('full_name') or u.get('username') or ''} | {u.get('status')} | teacherId: {u.get('mk_teacher_id') or '-'}"
                    for u in users
                ))
            return
        if cmd == "set_role":
            if not self.admin.is_admin(user.id):
                await self._reply(msg, "Менять роли может только владелец.")
                return
            parts = args.split(maxsplit=1)
            if len(parts) != 2:
                await self._reply(msg, "Формат: /set_role USER_ID role\nРоли: owner, manager, teacher, methodist, operations, smm, accountant, other")
                return
            try:
                target_id = int(parts[0])
            except Exception:
                await self._reply(msg, "USER_ID должен быть числом.")
                return
            role = parts[1].strip().lower()
            if role not in VALID_ROLES:
                await self._reply(msg, "Неизвестная роль. Роли: owner, manager, teacher, methodist, operations, smm, accountant, other")
                return
            ok = self.storage.set_staff_role(target_id, role)
            await self._reply(msg, f"Роль обновлена: {target_id} -> {self.admin.role_label(role)}" if ok else "Не удалось обновить роль.")
            return

        await self._reply(msg, "Команда не распознана. Напиши /help.")

    async def _reply(self, msg, text: str, reply_markup=None) -> None:
        parts = chunk_telegram(text, self.settings.telegram_max_message_chars)
        for idx, part in enumerate(parts):
            await msg.reply_text(part, reply_markup=reply_markup if idx == len(parts) - 1 else None)

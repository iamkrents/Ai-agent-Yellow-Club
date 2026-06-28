from __future__ import annotations

from config import Settings
from knowledge_base import KnowledgeBase
from llm import OllamaClient
from storage import Storage
from keyboards import (
    ROLE_BY_BUTTON,
    ROLE_LABELS,
    registration_keyboard,
    remove_keyboard,
    staff_keyboard,
    moyklass_keyboard,
)
from trial_manager import format_trial_for_admin
from report_manager import report_need_details_message


ROLE_NAMES = {
    "owner": "Владелец / админ",
    "manager": "Менеджер",
    "teacher": "Преподаватель",
    "methodist": "Методист",
    "operations": "Операционный менеджер",
    "smm": "Маркетинг / SMM",
    "accountant": "Бухгалтер",
    "other": "Другое",
}


class AdminPanel:
    def __init__(self, settings: Settings, kb: KnowledgeBase, storage: Storage, llm: OllamaClient):
        self.settings = settings
        self.kb = kb
        self.storage = storage
        self.llm = llm

    def is_real_admin(self, user_id: int | None) -> bool:
        return bool(user_id and user_id in self.settings.admin_ids)

    def _test_mode(self, user_id: int | None) -> dict:
        if not self.is_real_admin(user_id):
            return {"enabled": False, "role": "", "mk_teacher_id": ""}
        return self.storage.get_staff_test_mode(user_id)

    def is_admin(self, user_id: int | None) -> bool:
        test = self._test_mode(user_id)
        if test.get("enabled"):
            return test.get("role") == "owner"
        return self.is_real_admin(user_id)

    def get_role(self, user_id: int | None) -> str:
        test = self._test_mode(user_id)
        if test.get("enabled") and test.get("role"):
            return str(test.get("role") or "")
        return self.storage.get_staff_role(user_id, self.settings.admin_ids)

    def has_any_role(self, user_id: int | None) -> bool:
        return bool(self.get_role(user_id))

    def can_manage_trials(self, user_id: int | None) -> bool:
        return self.get_role(user_id) in {"owner", "manager", "operations"}

    def can_make_parent_reports(self, user_id: int | None) -> bool:
        return self.get_role(user_id) in {"owner", "teacher", "methodist"}

    def can_manage_memory(self, user_id: int | None) -> bool:
        return self.get_role(user_id) in {"owner", "methodist"}

    def role_label(self, role: str) -> str:
        return ROLE_NAMES.get(role or "", "Не зарегистрирован")

    def ensure_admin_registered(self, user) -> None:
        """ADMIN_IDS always have owner access, but also need to be present in staff_users for /users and /set_role."""
        if not user or not self.is_real_admin(user.id):
            return
        existing = self.storage.get_staff_user(user.id)
        if existing and existing.get("status") == "active":
            # Keep name/username fresh, but preserve owner role for ADMIN_IDS.
            self.storage.register_staff_user(
                user_id=user.id,
                username=user.username or "",
                full_name=user.full_name or "",
                role="owner",
            )
            return
        self.storage.register_staff_user(
            user_id=user.id,
            username=user.username or "",
            full_name=user.full_name or "",
            role="owner",
        )

    async def ask_registration(self, message):
        is_admin = self.is_real_admin(message.from_user.id if message.from_user else None)
        await message.reply_text(
            "Для личного кабинета нужно выбрать роль.\n\n"
            "Выберите вашу должность в Yellow Club:",
            reply_markup=registration_keyboard(is_admin=is_admin),
        )

    async def show(self, message):
        user = message.from_user
        if not user:
            return
        role = self.get_role(user.id)
        if not role:
            await self.ask_registration(message)
            return
        await message.reply_text(
            f"Личный кабинет Yellow Club Agent\nРоль: {self.role_label(role)}",
            reply_markup=staff_keyboard(role),
        )

    async def register_role_from_button(self, message, text: str) -> bool:
        if text not in ROLE_BY_BUTTON:
            return False
        user = message.from_user
        if not user:
            return True
        role = ROLE_BY_BUTTON[text]
        # Do not allow self-registering as owner unless user is in ADMIN_IDS.
        if role == "owner" and not self.is_real_admin(user.id):
            await message.reply_text("Роль владельца можно назначить только через ADMIN_IDS или владельцем агента.")
            return True
        self.storage.register_staff_user(
            user_id=user.id,
            username=user.username or "",
            full_name=user.full_name or "",
            role=role,
        )
        await message.reply_text(
            f"Роль сохранена: {self.role_label(role)}.\n"
            "Теперь доступны функции по вашей должности.",
            reply_markup=staff_keyboard(role),
        )
        return True

    async def handle_button(self, message, text: str) -> bool:
        user_id = message.from_user.id if message.from_user else None

        # Registration buttons are available to all in private chat.
        if await self.register_role_from_button(message, text):
            return True

        if text == "❌ Скрыть":
            await message.reply_text("Кнопки скрыты.", reply_markup=remove_keyboard())
            return True

        if text == "👤 Моя роль":
            role = self.get_role(user_id)
            if role:
                await message.reply_text(f"Ваша роль: {self.role_label(role)}")
            else:
                await self.ask_registration(message)
            return True

        if text == "❓ Задать вопрос":
            await message.reply_text("Напишите вопрос обычным сообщением. Я отвечу на основе базы Yellow Club, фактов и истории чата.")
            return True

        if text in {"🧾 Отчёт родителям", "🧾 Сформировать отчёт"}:
            if not self.can_make_parent_reports(user_id):
                await message.reply_text("Формирование отчётов доступно преподавателям, методистам и владельцу.")
                return True
            await message.reply_text(report_need_details_message())
            return True

        if text in {"📝 Заявки", "👥 Заявки на пробное"}:
            if not self.can_manage_trials(user_id):
                await message.reply_text("Заявки на пробное доступны менеджеру, операционному менеджеру и владельцу.")
                return True
            trials = self.storage.list_trials("open", 10)
            if not trials:
                await message.reply_text("Открытых заявок на пробное нет.")
            else:
                await message.reply_text("\n\n---\n\n".join(format_trial_for_admin(t) for t in trials))
            return True

        if text == "🗂 МойКласс":
            role = self.get_role(user_id)
            if role in {"owner", "operations", "methodist", "teacher"} or self.is_admin(user_id):
                await message.reply_text(
                    "МойКласс API.\n\n"
                    f"Статус ключа: {'указан' if self.settings.moyklass_enabled else 'не указан'}\n"
                    "Выберите действие кнопкой ниже.\n\n"
                    "Для преподавателя показываются только занятия по привязанному teacherId. Комментарий и тема доступны только для своих занятий через подтверждение.",
                    reply_markup=moyklass_keyboard(role or "owner"),
                )
                return True
            await message.reply_text(
                "По МойКласс можно спросить: что отметить после занятия, как указать отсутствующих, как записать тему следующего занятия."
            )
            return True

        # Quick prompt buttons by role.
        quick_prompts = {
            "💬 Ответ клиенту": "Напишите ситуацию с клиентом. Например: Родитель хочет Roblox для ребёнка 8 лет.",
            "📚 Курсы и цены": "Можно спросить: какая актуальная стоимость, какие направления есть, что входит в учебный трек.",
            "🧭 Подбор направления": "Опишите возраст ребёнка, опыт, интересы и запрос родителя. Я помогу подобрать направление.",
            "📌 К кому обратиться": "Опишите ситуацию, и я подскажу, к кому внутри компании обратиться.",
            "🎓 Методика занятия": "Опишите вопрос по занятию, группе или теме. Я отвечу по регламентам и методике Yellow Club.",
            "📚 Программа обучения": "Напишите курс, возраст или год обучения. Например: 2 год 11-17, Python 4 тема, GDevelop 9 тема.",
            "🔁 Отработка": "Опишите пропуск, группу, ученика и что нужно отработать. Я подскажу по регламенту отработок.",
            "👤 Обратная связь по ученику": "Опишите, как ученик работал на занятии. Я помогу сформулировать обратную связь для родителя или МойКласс.",
            "🟡 Регламент занятия": "Можно спросить: что должен сделать преподаватель до занятия, во время занятия и после занятия? Например: что делать, если пришёл один ученик?",
            "🟢 Регламент пробного": "Можно спросить: как провести пробное, что спросить у ребёнка, что сказать родителям и что отметить в МойКласс.",
            "🗂 МойКласс": "Открою раздел МойКласс. Для преподавателя нужны привязка Telegram ID к teacherId МойКласс и кнопки Мои занятия сегодня / на неделю.",
            "📅 Мои занятия сегодня": "Откройте 🗂 МойКласс и нажмите 📅 Мои занятия сегодня. Если бот попросит привязку, владелец должен выполнить /mk_link_teacher TELEGRAM_ID MK_TEACHER_ID.",
            "📆 Мои занятия на неделю": "Откройте 🗂 МойКласс и нажмите 📆 Мои занятия на неделю. Будут показаны только занятия по вашему teacherId.",
            "🧹 Порядок в классе": "Можно спросить: что проверить в кабинете, на компьютерах, по расходникам и после занятия.",
        }
        if text in quick_prompts:
            await message.reply_text(quick_prompts[text])
            return True

        if text == "🧪 Тест роли":
            if not self.is_real_admin(user_id):
                return False
            await message.reply_text(
                "Режим тестирования роли нужен, чтобы владелец мог проверить кабинет сотрудника со своего Telegram.\n\n"
                "Команды:\n"
                "/test_as teacher - открыть кабинет как преподаватель\n"
                "/test_as manager - открыть кабинет как менеджер\n"
                "/test_as operations - открыть кабинет как операционный менеджер\n"
                "/test_teacher_id 71190 - привязать тестовый teacherId для проверки занятий преподавателя\n"
                "/test_mode - посмотреть текущий тестовый режим\n"
                "/test_as_off - выключить тестовый режим"
            )
            return True

        # Owner-only buttons.
        if not self.is_admin(user_id):
            return False

        if text == "📊 Статус":
            await message.reply_text(
                "Статус агента:\n"
                f"Модель: {self.settings.ollama_model}\n"
                f"Режим: {self.settings.response_mode}\n"
                f"Файлов базы: {len(self.kb.file_list())}\n"
                f"Фрагментов базы: {len(self.kb.chunks)}\n"
                f"Сообщений в истории: {self.storage.count_messages()}\n"
                f"Сотрудников в базе: {len(self.storage.list_staff_users(500))}\n"
                f"Разрешённые группы: {'все' if self.settings.allow_all_groups else ', '.join(map(str, self.settings.allowed_group_ids))}\n"
                f"МойКласс API: {'ключ указан' if self.settings.moyklass_enabled else 'ключ не указан'}\n"
                f"Notion: {'настроен' if self.settings.notion_enabled else 'не настроен'}"
            )
            return True
        if text == "📚 База знаний":
            files = self.kb.file_list()
            if files:
                await message.reply_text(
                    "Файлы базы:\n"
                    + "\n".join(files[:40])
                    + ("\n\nПоказаны первые 40 файлов." if len(files) > 40 else "")
                    + "\n\nПерезагрузить базу: /kb_reload"
                )
            else:
                await message.reply_text("База знаний пуста. Notion можно синхронизировать командой /notion_sync.")
            return True
        if text == "🤖 Ollama":
            res = self.llm.ping()
            await message.reply_text("Ollama: работает" if res.ok else f"Ollama недоступна: {res.error}")
            return True
        if text == "💬 Режим":
            await message.reply_text("Текущий режим задаётся в .env: RESPONSE_MODE=smart/mentions/commands/silent. Сейчас: " + self.settings.response_mode)
            return True
        if text == "🧠 Память":
            facts = self.storage.list_facts(10)
            if facts:
                body = "\n".join(f"#{f['id']}: {f['text']}" for f in facts)
            else:
                body = "Фактов пока нет. Добавить: /remember текст факта"
            await message.reply_text(body)
            return True
        if text == "📌 Решения":
            decisions = self.storage.list_decisions(limit=10)
            if not decisions:
                await message.reply_text("Сохранённых решений пока нет. Добавить: /decision текст решения")
            else:
                await message.reply_text("\n\n".join(f"#{d['id']} | {d['created_at']}\n{d['text']}" for d in decisions))
            return True
        if text == "🗣️ Feedback":
            rows = self.storage.list_feedback(10)
            if not rows:
                await message.reply_text("Обратной связи пока нет. Добавить: /feedback хорошо текст или /feedback плохо текст")
            else:
                await message.reply_text("\n\n".join(f"#{r['id']} | {r['rating']} | @{r.get('username') or ''}\n{r.get('text') or ''}" for r in rows))
            return True
        if text == "👥 Сотрудники":
            users = self.storage.list_staff_users(100)
            if not users:
                await message.reply_text("Сотрудники ещё не зарегистрированы.")
            else:
                await message.reply_text("\n".join(
                    f"{u['user_id']} | {self.role_label(u.get('role'))} | {u.get('full_name') or u.get('username') or ''} | {u.get('status')} | teacherId: {u.get('mk_teacher_id') or '-'}"
                    for u in users
                ))
            return True
        if text == "⚙️ Настройки":
            await message.reply_text("Настройки меняются в .env. После изменения нужно перезапустить start_windows.bat. Роли можно менять командой /set_role ID role.")
            return True
        return False

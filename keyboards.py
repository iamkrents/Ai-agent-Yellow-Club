from __future__ import annotations

import os

from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove


ROLE_LABELS = {
    "owner": "👑 Владелец / админ",
    "manager": "💬 Менеджер",
    "teacher": "🎓 Преподаватель",
    "methodist": "🧭 Методист",
    "operations": "⚙️ Операционный менеджер",
    "smm": "🦄 Маркетинг / SMM",
    "accountant": "📂 Бухгалтер",
    "other": "👤 Другое",
    "kitchen": "🍳 Кухня",
    "restaurant": "🍽 Ресторан",
    "intern": "🎓 Стажёр",
    "client_manager": "💼 Клиент-менеджер",
}

ROLE_BY_BUTTON = {v: k for k, v in ROLE_LABELS.items()}


def _web_app_row() -> list:
    """Return a plain launcher button for the Mini App.

    Important: this must be a normal text button, not KeyboardButton(web_app=...).
    The bot has to receive the click first and then send a per-user signed URL.
    Otherwise Telegram may open WEB_APP_URL directly without initData / yc_sig,
    and the Mini App will not know which employee opened it.
    """
    return ["📲 Открыть кабинет"]


REPORT_START_BUTTONS = {"🧾 Отчёт родителям", "🧾 Сформировать отчёт", "📝 Отчёт родителям"}
REPORT_ACTION_BUTTONS = {
    "🔁 Переделать",
    "✂️ Короче",
    "📌 Подробнее",
    "🤝 Мягче",
    "✅ Хорошо",
    "📋 Меню",
    "❌ Отмена",
}

NOTION_BUTTONS = {
    "📄 Notion",
    "⚡ Быстрый sync Notion",
    "🔄 Синхронизировать Notion",
    "🔗 Синхронизировать страницу",
    "🧹 Полная пересинхронизация",
    "📋 Файлы Notion",
    "🧾 Notion manifest",
    "🔁 Перезагрузить базу",
    "⬅️ В меню",
}


def registration_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    rows = [
        ["💬 Менеджер", "🎓 Преподаватель"],
        ["🧭 Методист", "⚙️ Операционный менеджер"],
        ["🦄 Маркетинг / SMM", "📂 Бухгалтер"],
        ["👤 Другое"],
    ]
    # Owner/admin role is not offered for self-registration, except for ADMIN_IDS.
    if is_admin:
        rows.insert(0, ["👑 Владелец / админ"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=True)


def owner_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            _web_app_row(),
            ["📊 Статус", "👥 Сотрудники"],
            ["📊 Контроль преподавателей", "📊 OPS контроль"],
            ["📌 Мои задачи", "🧪 Тест ролей"],
            ["📅 Мои занятия", "🌙 Закрыть рабочий день"],
            ["🗂 МойКласс", "📄 Notion"],
            ["📘 Материал занятия", "📝 Отчёт родителям"],
            ["✅ Чек-лист преподавателя", "❓ Вопрос по регламенту"],
            ["🧠 Умный ответ", "🔎 Диагностика базы"],
            ["⚙️ Настройки", "❌ Скрыть"],
        ],
        resize_keyboard=True,
    )


def manager_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["👥 Заявки на пробное", "🔎 Найти ученика"],
            ["💬 Ответ родителю", "🧭 Подбор направления"],
            ["🔁 Отработка", "📚 Курсы и цены"],
            ["📌 К кому обратиться", "🗂 МойКласс"],
            ["❓ Задать вопрос", "🧠 Умный ответ"],
            ["👤 Моя роль"],
            ["❌ Скрыть"],
        ],
        resize_keyboard=True,
    )


def teacher_keyboard() -> ReplyKeyboardMarkup:
    # Keep the teacher cabinet intentionally simple. Detailed actions appear
    # only after the teacher selects a concrete lesson.
    return ReplyKeyboardMarkup(
        [
            _web_app_row(),
            ["📅 Мои занятия", "📌 Мои задачи"],
            ["🌙 Закрыть рабочий день"],
            ["❓ Как пользоваться", "❓ Вопрос по регламенту"],
            ["👤 Моя роль", "❌ Скрыть"],
        ],
        resize_keyboard=True,
    )


def methodist_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            _web_app_row(),
            ["📅 Мои занятия", "📌 Мои задачи"],
            ["🌙 Закрыть рабочий день", "📊 OPS контроль"],
            ["❓ Как пользоваться", "❓ Вопрос по регламенту"],
            ["📊 Контроль преподавателей", "🗂 МойКласс"],
            ["🔎 Поиск в базе", "🔎 Диагностика базы"],
            ["👤 Моя роль", "❌ Скрыть"],
        ],
        resize_keyboard=True,
    )


def operations_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["🗂 МойКласс", "📊 OPS контроль"],
            ["📌 К кому обратиться", "📌 Мои задачи"],
            ["📊 Контроль преподавателей", "🌙 Закрыть рабочий день"],
            ["💬 Ответ родителю", "🔁 Отработка"],
            ["✅ Чек-лист преподавателя", "❓ Задать вопрос"],
            ["👤 Моя роль", "❌ Скрыть"],
        ],
        resize_keyboard=True,
    )


def staff_keyboard(role: str) -> ReplyKeyboardMarkup:
    role = (role or "").lower()
    if role == "owner":
        return owner_keyboard()
    if role == "manager":
        return manager_keyboard()
    if role == "teacher":
        return teacher_keyboard()
    if role == "methodist":
        return methodist_keyboard()
    if role == "operations":
        return operations_keyboard()
    # Generic staff panel.
    return ReplyKeyboardMarkup(
        [
            ["❓ Задать вопрос", "🧠 Умный ответ"],
            ["📚 Курсы и цены"],
            ["📌 К кому обратиться", "👤 Моя роль"],
            ["❌ Скрыть"],
        ],
        resize_keyboard=True,
    )



TEACHER_CHECKLIST_BUTTONS = {
    "✅ Чек-лист преподавателя",
    "🟢 Перед занятием",
    "🔵 Проведение занятия",
    "🟣 После занятия",
    "🟡 Пробное / отработка",
    "🔴 Нестандартная ситуация",
    "⬅️ Кабинет преподавателя",
}


def teacher_checklist_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["🟢 Перед занятием", "🔵 Проведение занятия"],
            ["🟣 После занятия", "🟡 Пробное / отработка"],
            ["🔴 Нестандартная ситуация"],
            ["⬅️ Кабинет преподавателя", "📋 Меню"],
        ],
        resize_keyboard=True,
    )



TEACHER_CONTROL_BUTTONS = {
    "✅ Подготовка к занятию",
    "📋 Закрыть занятие",
    "📊 Контроль преподавателей",
    "✅ МойКласс заполнен",
    "📝 Отчёт отправлен",
    "💾 Работы сохранены",
    "🏫 Кабинет в порядке",
    "⚠️ Есть проблема",
    "✅ Закрыть занятие полностью",
    "✅ Закрыть выбранное занятие",
    "📝 Сформировать отчёт",
    "📘 Материал выбранного",
    "📝 Отчёт по выбранному",
    "⚠️ Нужна помощь",
    "❌ Не успеваю",
    "⬅️ Кабинет преподавателя",
}


TEACHER_OPS_BUTTONS = {
    "📌 Мои задачи",
    "📊 OPS контроль",
}


TEACHER_DAY_BUTTONS = {
    "📲 Открыть кабинет",
    "📅 Мои занятия",
    "📅 Мои занятия сегодня",
    "📘 Материалы на неделю",
    "📘 Материалы на сегодня",
    "✅ Подготовка ко дню",
    "🌙 Закрыть рабочий день",
    "✅ Готов ко всем занятиям",
    "✅ Тема изучена, я готов",
    "⚠️ Нужна помощь по дню",
    "⚠️ Нужна помощь с темой",
    "✅ МойКласс заполнен",
    "📝 Отчёт отправлен",
    "💾 Работы сохранены",
    "🏫 Кабинет в порядке",
    "⚠️ Есть проблема",
    "✅ Закрыть выбранное занятие",
    "✅ Завершить рабочий день",
    "📘 Материал выбранного",
    "📝 Отчёт по выбранному",
    "⬅️ К занятиям дня",
}


def teacher_day_prepare_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["✅ Готов ко всем занятиям"],
            ["⚠️ Нужна помощь по дню"],
            ["📘 Материалы на неделю", "📅 Мои занятия"],
            ["📋 Меню"],
        ],
        resize_keyboard=True,
    )


def teacher_day_lesson_keyboard(open_buttons: list[str], show_finish_day: bool = True) -> ReplyKeyboardMarkup:
    rows = [[button] for button in open_buttons[:30]]
    if show_finish_day:
        rows.append(["✅ Завершить рабочий день"])
    rows.append(["❓ Как пользоваться", "📋 Меню"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def teacher_day_prepare_lesson_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["📘 Материал выбранного"],
            ["✅ Тема изучена, я готов"],
            ["⚠️ Нужна помощь с темой"],
            ["⬅️ К занятиям дня", "📋 Меню"],
        ],
        resize_keyboard=True,
    )


def teacher_day_close_keyboard(control: dict | None = None) -> ReplyKeyboardMarkup:
    rows: list[list[str]] = []
    c = control or {}
    parent_done = str(c.get("parent_report_status") or "") == "done"
    myclass_done = str(c.get("myclass_status") or "") == "done"
    works_done = str(c.get("works_status") or "") == "done"
    classroom_done = str(c.get("classroom_status") or "") == "done"

    # Step-by-step closing: show only the next main action, not all actions at once.
    if not parent_done:
        rows.append(["📝 Отчёт по выбранному"])
        rows.append(["📝 Отчёт отправлен"])
    elif not myclass_done:
        rows.append(["✅ МойКласс заполнен"])
    elif not works_done:
        rows.append(["💾 Работы сохранены"])
    elif not classroom_done:
        rows.append(["🏫 Кабинет в порядке"])
    else:
        rows.append(["✅ Закрыть выбранное занятие"])

    rows.append(["📘 Материал выбранного"])
    rows.append(["⚠️ Есть проблема"])
    rows.append(["⬅️ К занятиям дня", "📋 Меню"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def teacher_prepare_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["📘 Материал выбранного"],
            ["⚠️ Нужна помощь", "❌ Не успеваю"],
            ["⬅️ Кабинет преподавателя", "📋 Меню"],
        ],
        resize_keyboard=True,
    )


def teacher_close_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["✅ МойКласс заполнен", "📝 Отчёт отправлен"],
            ["💾 Работы сохранены", "🏫 Кабинет в порядке"],
            ["📝 Сформировать отчёт", "⚠️ Есть проблема"],
            ["✅ Закрыть занятие полностью"],
            ["⬅️ Кабинет преподавателя", "📋 Меню"],
        ],
        resize_keyboard=True,
    )



TEST_ROLE_BUTTONS = {
    "🧪 Тест ролей",
    "🎓 Тест: преподаватель",
    "💬 Тест: менеджер",
    "🧭 Тест: методист",
    "⚙️ Тест: операционный",
    "🦄 Тест: SMM",
    "📂 Тест: бухгалтер",
    "👤 Тест: другое",
    "👑 Обычный владелец",
    "🆔 Тестовый teacherId",
    "⬅️ В меню",
}


def test_roles_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["🎓 Тест: преподаватель", "💬 Тест: менеджер"],
            ["🧭 Тест: методист", "⚙️ Тест: операционный"],
            ["🦄 Тест: SMM", "📂 Тест: бухгалтер"],
            ["👤 Тест: другое", "🆔 Тестовый teacherId"],
            ["👑 Обычный владелец", "⬅️ В меню"],
        ],
        resize_keyboard=True,
    )


def notion_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["⚡ Быстрый sync Notion"],
            ["🔗 Синхронизировать страницу"],
            ["🔄 Синхронизировать Notion"],
            ["🧹 Полная пересинхронизация"],
            ["📋 Файлы Notion", "🧾 Notion manifest"],
            ["🔁 Перезагрузить базу", "⬅️ В меню"],
        ],
        resize_keyboard=True,
    )


def report_waiting_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["❌ Отмена", "📋 Меню"]],
        resize_keyboard=True,
    )


def report_actions_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["🔁 Переделать", "✂️ Короче"],
            ["📌 Подробнее", "🤝 Мягче"],
            ["✅ Хорошо", "📋 Меню"],
        ],
        resize_keyboard=True,
    )


def after_lesson_absents_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["✅ Все присутствовали"],
            ["⏭ Пропустить", "❌ Отмена"],
            ["⬅️ МойКласс"],
        ],
        resize_keyboard=True,
    )


def after_lesson_next_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["✅ Отправить изменение", "➡️ Далее"],
            ["📌 Подготовленное изменение", "❌ Отмена"],
            ["⬅️ МойКласс"],
        ],
        resize_keyboard=True,
    )


def after_lesson_topic_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["⏭ Не менять тему"],
            ["❌ Отмена", "⬅️ МойКласс"],
        ],
        resize_keyboard=True,
    )


def after_lesson_done_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["🧾 Отчёт по выбранному"],
            ["✏️ Комментарий к занятию", "🏷 Тема занятия"],
            ["⬅️ МойКласс"],
        ],
        resize_keyboard=True,
    )




def manager_waiting_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["❌ Отмена", "📋 Меню"],
        ],
        resize_keyboard=True,
    )


def manager_answer_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["🔁 Переделать", "✂️ Короче"],
            ["🤝 Мягче", "📋 Меню"],
        ],
        resize_keyboard=True,
    )


def manager_trials_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["💬 Ответ родителю", "🔎 Найти ученика"],
            ["🧭 Подбор направления", "📋 Меню"],
        ],
        resize_keyboard=True,
    )

MOYKLASS_BUTTONS = {
    "📅 Мои занятия",
    "📅 Мои занятия сегодня",
    "📆 Мои занятия на неделю",
    "🔗 Моя привязка МК",
    "🧾 Отчёт по выбранному",
    "✅ Чеклист после занятия",
    "⬅️ МойКласс",
    "🧪 Проверить МК",
    "📅 Занятия сегодня",
    "📆 Занятия на неделю",
    "📊 Отчёт МК",
    "🧾 Посещения",
    "💳 Оплаты",
    "🧑‍🎓 Активные клиенты",
    "📋 Задачи МК",
    "🔁 Пропуски",
    "💳 Без оплат",
    "📉 Низкая посещаемость",
    "👨‍🏫 Месяц преподавателей",
    "📅 Задачи на сегодня",
    "🧾 Записи занятий",
    "🏷 Без темы",
    "💬 Без комментария",
    "👨‍🏫 По преподавателям",
    "🏫 По кабинетам",
    "👥 По группам",
    "🔎 Занятие по ID",
    "👤 Найти ученика",
    "👤 Карточка ученика",
    "📚 Занятия ученика",
    "💳 Оплаты ученика",
    "🔁 Пропуски ученика",
    "🧾 Записи ученика",
    "👥 Группы ученика",
    "📝 Комментарий ученику",
    "👥 Группы",
    "📘 Группа по ID",
    "👥 Ученики группы",
    "📚 Занятия группы",
    "✏️ Комментарий к занятию",
    "🏷 Тема занятия",
    "⏰ Время занятия",
    "📌 Подготовленное изменение",
    "✅ Отправить изменение",
    "❌ Отменить изменение",
    "📜 Журнал изменений",
    "⚙️ Техническая информация",
    "⬅️ В меню",
}



def selected_lesson_keyboard(role: str) -> ReplyKeyboardMarkup:
    """Keyboard shown after opening a concrete MoyKlass lesson card."""
    role = (role or "").lower()

    if role in {"teacher", "methodist"}:
        rows = [
            ["📘 Материал выбранного", "📝 Отчёт по выбранному"],
            ["✅ Чеклист после занятия", "🌙 Закрыть рабочий день"],
            ["✏️ Комментарий к занятию", "🏷 Тема занятия"],
        ]
    elif role == "manager":
        rows = [
            ["✏️ Комментарий к занятию", "🏷 Тема занятия"],
            ["⏰ Время занятия", "📌 Подготовленное изменение"],
        ]
    elif role == "operations":
        rows = [
            ["✏️ Комментарий к занятию", "🏷 Тема занятия"],
            ["⏰ Время занятия", "⚙️ Техническая информация"],
            ["📌 Подготовленное изменение"],
        ]
    else:
        rows = [
            ["✅ Подготовка к занятию", "📋 Закрыть занятие"],
            ["🧾 Отчёт по выбранному", "✅ Чеклист после занятия"],
            ["✏️ Комментарий к занятию", "🏷 Тема занятия"],
            ["⏰ Время занятия", "⚙️ Техническая информация"],
            ["📜 Журнал изменений", "📌 Подготовленное изменение"],
        ]

    rows.append(["⬅️ МойКласс"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def lesson_list_keyboard(open_buttons: list[str], role: str) -> ReplyKeyboardMarkup:
    """Keyboard under a lessons list: open selected lesson without typing /mk_lesson ID."""
    rows = []
    for button in open_buttons[:30]:
        rows.append([button])
    rows.append(["⬅️ МойКласс", "⬅️ В меню"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def moyklass_keyboard(role: str) -> ReplyKeyboardMarkup:
    role = (role or "").lower()
    rows = []

    if role in {"teacher", "methodist"}:
        rows.extend([
            ["📅 Мои занятия", "📆 Мои занятия на неделю"],
            ["📘 Материалы на неделю", "✅ Подготовка ко дню"],
            ["🌙 Закрыть рабочий день", "🔗 Моя привязка МК"],
            ["🔎 Занятие по ID", "🧾 Отчёт по выбранному"],
            ["✅ Чеклист после занятия"],
            ["✏️ Комментарий к занятию", "🏷 Тема занятия"],
            ["📌 Подготовленное изменение", "✅ Отправить изменение"],
            ["❌ Отменить изменение"],
        ])
    elif role == "manager":
        rows.extend([
            ["📅 Занятия сегодня", "📆 Занятия на неделю"],
            ["📊 Отчёт МК", "👥 Группы"],
            ["🧾 Посещения", "💳 Оплаты"],
            ["🧑‍🎓 Активные клиенты", "📋 Задачи МК"],
            ["🔁 Пропуски", "💳 Без оплат"],
            ["📉 Низкая посещаемость", "📅 Задачи на сегодня"],
            ["👨‍🏫 Месяц преподавателей", "🧾 Записи занятий"],
            ["🏷 Без темы", "💬 Без комментария"],
            ["👨‍🏫 По преподавателям", "🏫 По кабинетам"],
            ["👥 По группам"],
            ["📘 Группа по ID", "👥 Ученики группы"],
            ["📚 Занятия группы"],
            ["🔎 Занятие по ID", "👤 Найти ученика"],
            ["👤 Карточка ученика", "📚 Занятия ученика"],
            ["✏️ Комментарий к занятию", "🏷 Тема занятия"],
            ["⏰ Время занятия", "📌 Подготовленное изменение"],
            ["✅ Отправить изменение", "❌ Отменить изменение"],
            ["👥 Заявки на пробное", "💬 Ответ родителю"],
            ["🧭 Подбор направления", "🔁 Отработка"],
        ])
    else:
        rows.extend([
            ["🧪 Проверить МК", "📅 Занятия сегодня"],
            ["📆 Занятия на неделю", "🔎 Занятие по ID"],
            ["📊 Отчёт МК", "👥 Группы"],
            ["🧾 Посещения", "💳 Оплаты"],
            ["🧑‍🎓 Активные клиенты", "📋 Задачи МК"],
            ["🔁 Пропуски", "💳 Без оплат"],
            ["📉 Низкая посещаемость", "📅 Задачи на сегодня"],
            ["👨‍🏫 Месяц преподавателей", "🧾 Записи занятий"],
            ["🏷 Без темы", "💬 Без комментария"],
            ["👨‍🏫 По преподавателям", "🏫 По кабинетам"],
            ["👥 По группам"],
            ["📘 Группа по ID", "👥 Ученики группы"],
            ["📚 Занятия группы"],
            ["👤 Найти ученика", "👤 Карточка ученика"],
        ])

    # Owner keeps full control. Other roles receive only role-safe actions above.
    if role == "owner":
        rows.extend([
            ["🧾 Отчёт по выбранному", "✅ Чеклист после занятия"],
            ["✏️ Комментарий к занятию", "🏷 Тема занятия"],
            ["⏰ Время занятия", "📌 Подготовленное изменение"],
            ["✅ Отправить изменение", "❌ Отменить изменение"],
            ["📜 Журнал изменений", "🔎 Диагностика отчёта"],
        ])

    rows.append(["⬅️ В меню"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def class_list_keyboard(open_buttons: list[str], role: str) -> ReplyKeyboardMarkup:
    """Keyboard under a groups list: open selected group without typing classId."""
    rows = []
    for button in open_buttons[:30]:
        rows.append([button])
    rows.append(["⬅️ МойКласс", "⬅️ В меню"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def selected_class_keyboard(role: str) -> ReplyKeyboardMarkup:
    """Keyboard shown after opening a concrete MoyKlass group card."""
    role = (role or "").lower()
    rows = [
        ["👥 Ученики группы", "📚 Занятия группы"],
        ["👤 Найти ученика", "📆 Занятия на неделю"],
    ]
    if role in {"owner", "manager", "operations"}:
        rows.append(["📘 Группа по ID", "👥 Группы"])
    rows.append(["⬅️ МойКласс"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)




def user_list_keyboard(open_buttons: list[str], role: str) -> ReplyKeyboardMarkup:
    """Keyboard under a student search result: open selected student without copying ID."""
    rows = []
    for button in open_buttons[:30]:
        rows.append([button])
    rows.append(["⬅️ МойКласс", "⬅️ В меню"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)

def selected_user_keyboard(role: str) -> ReplyKeyboardMarkup:
    """Keyboard shown after opening a concrete MoyKlass student card."""
    role = (role or "").lower()
    rows = [
        ["📚 Занятия ученика", "💳 Оплаты ученика"],
        ["🔁 Пропуски ученика", "🧾 Записи ученика"],
        ["👥 Группы ученика"],
    ]
    if role in {"owner", "manager", "operations"}:
        rows.append(["📝 Комментарий ученику"])
    rows.append(["⬅️ МойКласс"])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


def parent_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["📲 Открыть кабинет"]],
        resize_keyboard=True,
    )


# Backward compatible alias: old code imports admin_keyboard().
def admin_keyboard() -> ReplyKeyboardMarkup:
    return owner_keyboard()


def remove_keyboard() -> ReplyKeyboardRemove:
    return ReplyKeyboardRemove()

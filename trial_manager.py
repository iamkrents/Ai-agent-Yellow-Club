from __future__ import annotations

import re
from typing import Optional

from rules import CURRENT_ADDRESSES, is_roblox_interest, is_trial_intent
from utils import normalize

REQUIRED_FIELDS = [
    ("age", "возраст ребёнка"),
    ("direction", "интересующее направление"),
    ("format", "формат: офлайн или онлайн"),
    ("location", "адрес для офлайна: Кульман 1/1 или Мстиславца 6"),
    ("preferred_time", "удобный день и время"),
    ("contact", "контакт для связи"),
]


def extract_trial_data(text: str) -> dict[str, str]:
    n = normalize(text)
    data: dict[str, str] = {}

    if "roblox" in n or "роблокс" in n:
        data["direction"] = "Roblox"
    elif "python" in n or "пайтон" in n:
        data["direction"] = "Python"
    elif "figma" in n or "фигма" in n:
        data["direction"] = "Figma"
    elif "blender" in n or "блендер" in n:
        data["direction"] = "Blender"
    elif "gdevelop" in n or "игр" in n:
        data["direction"] = "создание игр"

    age_match = re.search(r"(?:реб[её]нку\s*)?(\d{1,2})\s*(?:лет|года|год|л\b)?", text, flags=re.I)
    if age_match:
        age = int(age_match.group(1))
        if 4 <= age <= 18:
            data["age"] = f"{age} лет"

    if "онлайн" in n or "zoom" in n or "зум" in n:
        data["format"] = "онлайн"
    elif "офлайн" in n or "очно" in n or "кульман" in n or "мстислав" in n:
        data["format"] = "офлайн"

    if "кульман" in n:
        data["location"] = "Кульман 1/1"
    elif "мстислав" in n:
        data["location"] = "Мстиславца 6"

    if "telegram" in n or "телеграм" in n or "тг" in n:
        data["contact"] = "Telegram"
    elif "viber" in n or "вайбер" in n:
        data["contact"] = "Viber"

    phone = re.search(r"(?:\+?375|80)?[\s\-()]*(?:29|33|44|25)[\s\-()]*\d{3}[\s\-()]*\d{2}[\s\-()]*\d{2}", text)
    if phone:
        data["contact"] = phone.group(0)

    if any(w in n for w in ["сегодня", "завтра", "понедельник", "вторник", "сред", "четвер", "пятниц", "суббот", "воскрес", "будни", "выходн"]):
        data["preferred_time"] = text.strip()
    time_match = re.search(r"\b\d{1,2}[:.]\d{2}\b", text)
    if time_match and "preferred_time" not in data:
        data["preferred_time"] = text.strip()

    name_match = re.search(r"\b([А-ЯЁ][а-яё]+\s+[А-ЯЁ][а-яё]+)\b", text)
    if name_match:
        # In staff chat this is often parent/client name; keep as parent_name unless later we add explicit child fields.
        data["parent_name"] = name_match.group(1)

    if text.strip():
        data["notes"] = text.strip()
    return data


def has_trial_details(text: str) -> bool:
    data = extract_trial_data(text)
    return any(k in data for k in ["age", "direction", "format", "location", "contact", "preferred_time", "parent_name"])


def is_advice_or_process_question(text: str) -> bool:
    n = normalize(text)
    advice_markers = [
        "что ответить", "как ответить", "вариант ответа", "скрипт", "текст для клиента",
        "родитель спрашивает", "клиент спрашивает", "родитель говорит", "клиент говорит",
        "как можно записаться", "как записаться", "как проходит запись", "как записать",
        "что нужно для записи", "какие данные нужны",
    ]
    return any(marker in n for marker in advice_markers)


def is_real_trial_lead(text: str) -> bool:
    n = normalize(text)
    lead_markers = [
        "хочу изучать", "хочу на", "хочу записаться", "хотим записаться", "запишите",
        "записать нас", "записать ребенка", "записать ребёнка", "можем записать",
        "клиент хочет записаться", "родитель хочет записаться", "нужна запись",
        "на пробное в", "пробное сегодня", "пробное завтра",
    ]
    return any(marker in n for marker in lead_markers)


def should_start_or_update_trial(text: str) -> bool:
    if is_advice_or_process_question(text):
        return False
    return is_real_trial_lead(text)


def missing_trial_fields(trial: dict) -> list[str]:
    missing: list[str] = []
    for key, label in REQUIRED_FIELDS:
        if key == "location" and (trial.get("format") or "").lower() == "онлайн":
            continue
        if not trial.get(key):
            missing.append(label)
    return missing


def make_trial_public_reply(text: str) -> Optional[str]:
    n = normalize(text)
    if is_roblox_interest(text):
        return (
            "Можно предложить бесплатное пробное занятие по Roblox. На пробном преподаватель посмотрит возраст ребёнка, его опыт и интерес именно к Roblox Studio, а после этого менеджер сможет подобрать подходящую группу.\n\n"
            "У родителя лучше уточнить:\n"
            "1. возраст ребёнка;\n"
            "2. был ли уже опыт в Roblox Studio или только интерес к Roblox как игре;\n"
            "3. формат - офлайн или онлайн;\n"
            "4. если офлайн - какой адрес удобнее: Кульман 1/1 или Мстиславца 6;\n"
            "5. удобные дни и время для пробного;\n"
            "6. контакт для связи.\n\n"
            "Важно не вести клиента только по слову Roblox автоматически: если интерес осознанный, Roblox хорошо подходит как точка входа; если запрос общий, на пробном лучше оценить уровень и подобрать направление."
        )
    if is_trial_intent(text):
        return (
            "Для записи на бесплатное пробное занятие нужно уточнить: возраст ребёнка, интересующее направление, формат офлайн/онлайн, удобный адрес для офлайна, подходящие дни и время, контакт для связи.\n\n"
            "Пробное проходит в формате обычного занятия: ребёнок знакомится с преподавателем, пробует одно из направлений и делает первую практическую работу. После пробного менеджер сможет предложить подходящую группу и дальнейший формат обучения."
        )
    return None


def format_trial_for_admin(trial: dict) -> str:
    fields = [
        ("ID", trial.get("id")),
        ("Статус", trial.get("status")),
        ("Направление", trial.get("direction")),
        ("Ребёнок", trial.get("child_name")),
        ("Родитель", trial.get("parent_name")),
        ("Возраст", trial.get("age")),
        ("Формат", trial.get("format")),
        ("Адрес", trial.get("location")),
        ("Контакт", trial.get("contact")),
        ("Время", trial.get("preferred_time")),
        ("Заметки", trial.get("notes")),
    ]
    lines = [f"Заявка на пробное #{trial.get('id')}"]
    for name, value in fields[1:]:
        if value:
            lines.append(f"{name}: {value}")
    missing = missing_trial_fields(trial)
    if missing:
        lines.append("Не хватает: " + ", ".join(missing))
    else:
        lines.append("Карточка заполнена. Менеджеру осталось проверить расписание и подтвердить запись.")
    return "\n".join(lines)

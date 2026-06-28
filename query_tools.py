from __future__ import annotations

import re
from dataclasses import dataclass

from utils import normalize

# Единая нормализация запросов сотрудников. Нужна, потому что в реальной работе
# люди пишут вперемешку: "GDevelop 9 tema", "гдевелоп 9 урок",
# "отчет родителям по девелопу" и т.д.
LATIN_WORD_REPLACEMENTS: dict[str, str] = {
    r"\btema\b": "тема",
    r"\btemy\b": "темы",
    r"\btopic\b": "тема",
    r"\btopics\b": "темы",
    r"\burok\b": "урок",
    r"\buroki\b": "уроки",
    r"\bzanyatie\b": "занятие",
    r"\bzaniatie\b": "занятие",
    r"\blesson\b": "урок",
    r"\blessons\b": "уроки",
    r"\bmaterial\b": "материал",
    r"\bmaterials\b": "материалы",
    r"\botchet\b": "отчет",
    r"\botch[ée]t\b": "отчет",
    r"\breport\b": "отчет",
    r"\breports\b": "отчеты",
    r"\bparent\b": "родитель",
    r"\bparents\b": "родители",
    r"\broditel[eyiamu]*\b": "родителям",
    r"\breglament\b": "регламент",
    r"\bchecklist\b": "чеклист",
    r"\bcheck-list\b": "чеклист",
    r"\bmyclass\b": "мойкласс",
    r"\bmoyklass\b": "мойкласс",
    r"\bmoy klass\b": "мойкласс",
    r"\bg develop\b": "gdevelop",
    r"\bgdevelop 5\b": "gdevelop",
    r"\bgame dev\b": "gamedev",
}

ORDINAL_TO_NUMBER: dict[str, str] = {
    "нулевая": "0", "нулевой": "0", "вводная": "0",
    "первая": "1", "первое": "1", "первый": "1",
    "вторая": "2", "второе": "2", "второй": "2",
    "третья": "3", "третье": "3", "третий": "3",
    "четвертая": "4", "четвертое": "4", "четвертый": "4", "четвертая": "4",
    "пятая": "5", "пятое": "5", "пятый": "5",
    "шестая": "6", "шестое": "6", "шестой": "6",
    "седьмая": "7", "седьмое": "7", "седьмой": "7",
    "восьмая": "8", "восьмое": "8", "восьмой": "8",
    "девятая": "9", "девятое": "9", "девятый": "9",
    "десятая": "10", "десятое": "10", "десятый": "10",
    "одиннадцатая": "11", "одиннадцатое": "11", "одиннадцатый": "11",
    "двенадцатая": "12", "двенадцатое": "12", "двенадцатый": "12",
}

COURSE_ALIASES: dict[str, tuple[str, ...]] = {
    "photoshop": ("photoshop", "adobe photoshop", "фотошоп", "адоб фотошоп", "ps", "фш"),
    "python": ("python", "pethon", "paython", "phyton", "pithon", "пайтон", "питон", "петхон", "py"),
    "gdevelop": (
        "gdevelop", "gdevelop5", "джидевелоп", "гдевелоп", "девелоп",
        "разработка мобильных игр", "мобильных игр в gdevelop", "gamedev", "game dev",
    ),
    "roblox": ("roblox", "roblox studio", "роблокс", "роблокс студио"),
    "blender": ("blender", "blender 3d", "блендер", "блендер 3д", "3d моделирование"),
    "wordpress": ("wordpress", "вордпресс", "wp"),
    "html": ("html", "html css", "хтмл", "аштиэмэл", "программирование html"),
    "css": ("css", "цсс", "программирование css"),
    "javascript": ("javascript", "java script", "js", "джаваскрипт"),
    "figma": ("figma", "фигма"),
    "blockbench": ("blockbench", "блокбенч"),
    "telegram_bot": ("telegram bot", "telegram-бот", "телеграм бот", "тг бот", "бот python"),
    "kotlin": ("kotlin", "котлин"),
    "cpp": ("c++", "cpp", "си плюс плюс"),
    "c": ("c", "язык си"),
}

COURSE_DISPLAY: dict[str, str] = {
    "photoshop": "Photoshop",
    "python": "Python",
    "gdevelop": "GDevelop",
    "roblox": "Roblox Studio",
    "blender": "Blender 3D",
    "wordpress": "WordPress",
    "html": "HTML",
    "css": "CSS",
    "javascript": "JavaScript",
    "figma": "Figma",
    "blockbench": "Blockbench",
    "telegram_bot": "Telegram-бот",
    "kotlin": "Kotlin",
    "cpp": "C++",
    "c": "C",
}

LESSON_WORDS = ("тема", "урок", "занятие", "материал", "конспект", "план занятия", "план урока")
REPORT_WORDS = ("отчет", "отчёт", "родител", "родительский чат", "сообщение в чат", "сообщение родителям")
REGULATION_WORDS = ("регламент", "что делать", "как поступить", "если", "правило", "чеклист", "чек-лист")
MANAGER_WORDS = ("как ответить", "ответ родителю", "ответ клиенту", "клиент спрашивает", "родитель спрашивает", "возражение", "скрипт")


def canonicalize_user_text(text: str) -> str:
    q = normalize(text or "")
    # Unify Russian spelling before latin replacements.
    q = q.replace("отчёт", "отчет")
    # Phrase replacements first.
    for pattern, repl in LATIN_WORD_REPLACEMENTS.items():
        q = re.sub(pattern, repl, q, flags=re.IGNORECASE)
    for word, number in ORDINAL_TO_NUMBER.items():
        q = re.sub(rf"\b{re.escape(word)}\b", number, q)
    q = re.sub(r"\bмой\s+класс\b", "мойкласс", q)
    q = re.sub(r"\bg\s*develop\b", "gdevelop", q)
    q = re.sub(r"\bg\s*dev\b", "gamedev", q)
    q = re.sub(r"\s+", " ", q).strip()
    return q


def _phrase_in_text(phrase: str, text: str) -> bool:
    phrase = canonicalize_user_text(phrase)
    if not phrase:
        return False
    # Short aliases like "ps" must be matched as words. Long phrases can be substring matches.
    if len(phrase) <= 3 or re.fullmatch(r"[a-z0-9+#]+", phrase):
        return bool(re.search(rf"(?<![a-zа-я0-9]){re.escape(phrase)}(?![a-zа-я0-9])", text))
    return phrase in text


def extract_course_keys(text: str) -> list[str]:
    q = canonicalize_user_text(text)
    found: list[str] = []
    for key, aliases in COURSE_ALIASES.items():
        for alias in aliases:
            if _phrase_in_text(alias, q):
                found.append(key)
                break
    return found


def extract_course_terms(text: str) -> list[str]:
    keys = extract_course_keys(text)
    terms: list[str] = []
    for key in keys:
        if key not in terms:
            terms.append(key)
        for alias in COURSE_ALIASES.get(key, ()):  # type: ignore[arg-type]
            a = canonicalize_user_text(alias)
            if a and a not in terms:
                terms.append(a)
    return terms


def extract_lesson_number(text: str) -> str:
    q = canonicalize_user_text(text)
    patterns = [
        r"(?:тема|занятие|урок)\s*№?\s*(\d{1,2})",
        r"(\d{1,2})\s*(?:тема|темы|занятие|занятия|урок|урока)",
        r"№\s*(\d{1,2})",
        r"#\s*(\d{1,2})",
    ]
    for pat in patterns:
        m = re.search(pat, q)
        if m:
            return m.group(1)
    # If a course is specified, a lone small number is usually the topic number.
    if extract_course_keys(q):
        m = re.search(r"(?<!\d)(\d{1,2})(?!\d)", q)
        if m:
            return m.group(1)
    return ""


def query_has_report_intent(text: str) -> bool:
    q = canonicalize_user_text(text)
    return any(w in q for w in REPORT_WORDS) or q.startswith("/parent_report") or q.startswith("/report")


def query_has_lesson_intent(text: str) -> bool:
    q = canonicalize_user_text(text)
    return any(w in q for w in LESSON_WORDS) or bool(extract_course_keys(q) and extract_lesson_number(q))


def query_has_regulation_intent(text: str) -> bool:
    q = canonicalize_user_text(text)
    return any(w in q for w in REGULATION_WORDS)


def query_has_manager_reply_intent(text: str) -> bool:
    q = canonicalize_user_text(text)
    return any(w in q for w in MANAGER_WORDS)


def normalize_for_search_tokens(text: str) -> list[str]:
    q = canonicalize_user_text(text)
    return re.findall(r"[a-zа-я0-9+.#-]+", q)


@dataclass(frozen=True)
class QueryProfile:
    original: str
    normalized: str
    course_keys: tuple[str, ...]
    course_terms: tuple[str, ...]
    lesson_number: str
    has_lesson_intent: bool
    has_report_intent: bool
    has_regulation_intent: bool
    has_manager_reply_intent: bool


def build_query_profile(text: str) -> QueryProfile:
    n = canonicalize_user_text(text)
    return QueryProfile(
        original=text or "",
        normalized=n,
        course_keys=tuple(extract_course_keys(n)),
        course_terms=tuple(extract_course_terms(n)),
        lesson_number=extract_lesson_number(n),
        has_lesson_intent=query_has_lesson_intent(n),
        has_report_intent=query_has_report_intent(n),
        has_regulation_intent=query_has_regulation_intent(n),
        has_manager_reply_intent=query_has_manager_reply_intent(n),
    )

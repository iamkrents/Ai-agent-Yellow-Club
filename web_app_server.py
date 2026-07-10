from __future__ import annotations

import hashlib
import hmac
import json
import logging
import mimetypes
import os
import re
import threading
import time
import urllib.parse
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from config import BASE_DIR, load_settings
from knowledge_base import KnowledgeBase
from moyklass_client import (
    MoyKlassClient,
    extract_items,
    extract_lesson_topic_from_result,
    filter_lessons_by_teacher,
    lesson_has_teacher,
    _class_id_value,
    _class_name_value,
    _lesson_date_value,
    _lesson_group_value,
    _lesson_room_value,
    _lesson_status_value,
    _lesson_teacher_value,
    _lesson_time_value,
    _lesson_title_value,
    _pick,
    _v3991_fetch_user_objects,
    _v3991_user_name_from_user_obj,
    _v3991_user_id_from_any,
)
from food_menu_ocr import ocr_image_to_text
from storage import Storage
from llm import OllamaClient
from agent_core import AgentCore, AnswerContext
from query_tools import build_query_profile

log = logging.getLogger("yellow_club_miniapp")
WEB_DIR = BASE_DIR / "miniapp"

ROLE_LABELS = {
    "owner": "Владелец",
    "admin": "Администратор",
    "teacher": "Преподаватель",
    "methodist": "Старший преподаватель",
    "intern": "Стажер",
    "client_manager": "Клиент-менеджер",
    "director": "Директор",
    "operations": "Операционный менеджер",
    "other": "Сотрудник",
    "parent": "Родитель",
    "kitchen": "Кухня",
    "restaurant": "Кухня",
}
TEST_ROLE_OPTIONS = [
    {"value": "owner", "label": "Админ (owner)", "needsTeacher": False},
    {"value": "teacher", "label": "Преподаватель", "needsTeacher": True},
    {"value": "methodist", "label": "Старший преподаватель (методист)", "needsTeacher": False},
    {"value": "intern", "label": "Стажер", "needsTeacher": True},
    {"value": "client_manager", "label": "Клиент-менеджер", "needsTeacher": False},
    {"value": "director", "label": "Директор", "needsTeacher": False},
    {"value": "kitchen", "label": "Кухня", "needsTeacher": False},
]
LESSON_ROLES = {"owner", "teacher", "methodist", "operations", "intern"}
SCHEDULE_ROLES = {"owner", "teacher", "methodist", "operations"}
INTERN_ROLES = {"intern"}
OPEN_SLOTS_ROLES = {"client_manager"}
REPORT_ROLES = {"client_manager", "director", "owner", "operations", "methodist", "admin"}
CHILDREN_REPORT_ROLES = {"client_manager", "director", "owner", "operations", "admin"}
CLIENT_TASK_ROLES = {"client_manager", "owner", "operations"}
KPI_ROLES = {"client_manager", "owner", "operations", "methodist"}
TEACHER_LIKE_ROLES = {"teacher", "methodist", "intern"}
ADMIN_ROLES = {"owner", "admin", "methodist", "operations"}
FULL_ADMIN_ROLES = {"owner", "admin", "operations"}
KITCHEN_SUMMARY_ROLES = {"kitchen", "restaurant", "owner", "admin", "methodist", "operations"}
FOOD_PRICE_ROLES = {"kitchen", "restaurant", "owner", "admin", "methodist", "operations"}
FOOD_MENU_EDIT_ROLES = {"kitchen", "restaurant", "owner", "admin", "methodist", "operations"}
FOOD_MENU_DELETE_ROLES = {"kitchen", "restaurant", "owner", "admin", "operations"}
FOOD_ADMIN_EDIT_ROLES = {"owner", "admin", "operations"}
ADMIN_TABS_BY_ROLE = {
    "owner": ["overview", "lesson-control", "teachers", "work-schedule", "prep-results", "tasks", "users", "notion", "notifications", "kpi", "interns"],
    "admin": ["overview", "lesson-control", "teachers", "work-schedule", "prep-results", "tasks", "users", "notion", "notifications", "kpi", "interns"],
    "operations": ["overview", "lesson-control", "teachers", "work-schedule", "prep-results", "tasks", "users", "notion", "notifications", "kpi", "interns"],
    "methodist": ["overview", "lesson-control", "teachers", "work-schedule", "prep-results", "tasks", "notifications", "kpi", "interns"],
}


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def _parse_query(path: str) -> tuple[str, dict[str, str]]:
    parsed = urllib.parse.urlparse(path)
    params = {k: v[-1] if v else "" for k, v in urllib.parse.parse_qs(parsed.query).items()}
    return parsed.path, params


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _safe_filename(name: str) -> str:
    name = (name or "result.bin").strip().replace("\\", "_").replace("/", "_")
    name = re.sub(r"[^\w\-.() а-яА-ЯёЁ]+", "_", name, flags=re.UNICODE)
    name = re.sub(r"_+", "_", name).strip("._ ")
    return (name or "result.bin")[:160]


def _parse_multipart_form(content_type: str, body: bytes) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """Very small multipart/form-data parser for Mini App uploads."""
    fields: dict[str, str] = {}
    files: dict[str, dict[str, Any]] = {}
    m = re.search(r"boundary=(?:\"([^\"]+)\"|([^;]+))", content_type or "", re.I)
    if not m:
        return fields, files
    boundary = (m.group(1) or m.group(2) or "").encode("utf-8", errors="ignore")
    if not boundary:
        return fields, files
    marker = b"--" + boundary
    for raw_part in body.split(marker):
        part = raw_part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        if part.endswith(b"--"):
            part = part[:-2].strip(b"\r\n")
        if b"\r\n\r\n" not in part:
            continue
        head, data = part.split(b"\r\n\r\n", 1)
        header_text = head.decode("utf-8", errors="replace")
        disp = ""
        ctype = "application/octet-stream"
        for line in header_text.split("\r\n"):
            if line.lower().startswith("content-disposition:"):
                disp = line
            elif line.lower().startswith("content-type:"):
                ctype = line.split(":", 1)[1].strip()
        name_m = re.search(r'name="([^"]+)"', disp)
        if not name_m:
            continue
        name = name_m.group(1)
        fn_m = re.search(r'filename="([^"]*)"', disp)
        if fn_m:
            filename = fn_m.group(1) or "result.bin"
            files[name] = {"filename": filename, "content": data, "content_type": ctype}
        else:
            fields[name] = data.decode("utf-8", errors="replace")
    return fields, files


def _lesson_id(item: dict[str, Any]) -> str:
    return _pick(item, ("id", "lessonId"))


def _lesson_topic(item: dict[str, Any]) -> str:
    """Return only the real lesson topic/material title from MoyKlass.

    Do not fall back to group name or schedule title here. Earlier versions used
    groupName/name/title as fallback, so a lesson without a topic could open
    a random Notion page such as "16 лет". If the topic is empty, return empty.
    """
    # Most MoyKlass installations store the real lesson topic in one of these fields.
    direct = _pick(item, (
        "topic", "topicName", "lessonTopic", "theme", "themeName",
        "lessonTheme", "subjectName", "courseTopic", "homeworkTopic",
    ))
    if direct:
        return direct.strip()

    # Some exports place the topic in description/comment. Use it only when it
    # clearly looks like a lesson topic, not as a generic note.
    for key in ("description", "comment", "note"):
        value = str(item.get(key) or "").strip()
        if value and re.search(r"(?i)(?:^|\b)(?:\d{1,2}\s*(?:тема|урок)|(?:тема|урок)\s*\d{1,2}|python|gdevelop|photoshop|blender|roblox|html|css|figma|wordpress|язык\s*c|c\+\+)", value):
            return value
    return ""


def _clean_mk_text(value: Any) -> str:
    """Clean MoyKlass text/comment fields for display in Mini App."""
    if value is None:
        return ""
    if isinstance(value, list):
        parts: list[str] = []
        for x in value:
            if isinstance(x, dict):
                parts.append(_clean_mk_text(x.get("text") or x.get("comment") or x.get("description") or x.get("value") or ""))
            else:
                parts.append(_clean_mk_text(x))
        return "\n".join([x for x in parts if x]).strip()
    if isinstance(value, dict):
        for key in (
            "text", "comment", "message", "body", "content", "description",
            "value", "note", "html", "plainText", "commentText",
        ):
            if value.get(key):
                return _clean_mk_text(value.get(key))
        return ""
    text = str(value or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p\s*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = urllib.parse.unquote(text)
    text = text.replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:1200]




_MONTH_NAME_TO_NUMBER = {
    "январь": 1, "января": 1, "январе": 1,
    "февраль": 2, "февраля": 2, "феврале": 2,
    "март": 3, "марта": 3, "марте": 3,
    "апрель": 4, "апреля": 4, "апреле": 4,
    "май": 5, "мая": 5, "мае": 5,
    "июнь": 6, "июня": 6, "июне": 6,
    "июль": 7, "июля": 7, "июле": 7,
    "август": 8, "августа": 8, "августе": 8,
    "сентябрь": 9, "сентября": 9, "сентябре": 9,
    "октябрь": 10, "октября": 10, "октябре": 10,
    "ноябрь": 11, "ноября": 11, "ноябре": 11,
    "декабрь": 12, "декабря": 12, "декабре": 12,
}


def _month_from_staff_question(text: str) -> str:
    """Return YYYY-MM from a staff chat question when a month is mentioned."""
    raw = str(text or "").lower().replace("ё", "е")
    numeric = re.search(r"\b(20\d{2})[-./](0?[1-9]|1[0-2])\b(?![-./]\d{1,2})", raw)
    if numeric:
        return f"{numeric.group(1)}-{int(numeric.group(2)):02d}"
    reverse = re.search(r"(?<!\d[-./])\b(0?[1-9]|1[0-2])[-./](20\d{2})\b", raw)
    if reverse:
        return f"{reverse.group(2)}-{int(reverse.group(1)):02d}"

    now = datetime.now()
    explicit_year_match = re.search(r"\b(20\d{2})\b", raw)
    explicit_year = int(explicit_year_match.group(1)) if explicit_year_match else None
    for name, month in _MONTH_NAME_TO_NUMBER.items():
        if re.search(rf"\b{name}\b", raw):
            year = explicit_year or now.year
            # If the user says a month that has not happened yet this year, usually they mean the previous one.
            if not explicit_year and month > now.month:
                year -= 1
            return f"{year}-{month:02d}"
    return ""


def _looks_like_mk_month_analytics_question(text: str) -> bool:
    raw = str(text or "").lower().replace("ё", "е")
    if not _month_from_staff_question(raw):
        return False
    # Не путать операционные задачи записи/отработок с месячной аналитикой.
    # Пример: "подбери окно для отработки до 2026-06-19" содержит дату и слово "ученик",
    # но это не вопрос по отчёту за месяц.
    if re.search(r"\b(подбер\w*|найд\w*|окн[оа]?|свободн\w*|отработ\w*|пробн\w*|запис\w*|постав\w*)\b", raw):
        return False
    has_month_phrase = bool(re.search(r"\b(за|в|по|месяц|месячн\w*)\s+(январ|феврал|март|апрел|ма[йяе]|июн|июл|август|сентябр|октябр|ноябр|декабр|20\d{2}[-./]\d{1,2}|\d{1,2}[-./]20\d{2})", raw))
    has_counter = bool(re.search(r"\b(сколько|количество|число|отчет|статистика|посещени|обучал|занимал|ходил|ученик|клиент|заняти|оплат|выручк|сумм)\w*\b", raw))
    has_mk_metric = bool(re.search(r"\b(ученик|клиент|ребен|дет|посещени|визит|заняти|урок|групп|оплат|платеж|выручк|мои\s*класс|мойкласс|мк)\w*\b", raw))
    return has_month_phrase and has_counter and has_mk_metric


def _mk_month_student_answer_text(result: Any, month_label: str, question: str) -> str:
    if not getattr(result, "ok", False):
        return f"Не удалось получить данные МойКласс за {month_label}. Ошибка: {getattr(result, 'error', '') or getattr(result, 'status', '')}."
    data = result.data or {}
    month = data.get("month") or month_label
    groups = data.get("groups") or {}
    lessons = data.get("lessons") or {}
    visits = data.get("visits") or {}
    clients = data.get("clients") or {}
    payments = data.get("payments") or {}

    raw = str(question or "").lower().replace("ё", "е")
    wants_students = bool(re.search(r"\b(ученик|клиент|ребен|дет|обучал|занимал|ходил)\w*\b", raw))
    wants_lessons = bool(re.search(r"\b(заняти|урок)\w*\b", raw)) and not wants_students
    wants_payments = bool(re.search(r"\b(оплат|платеж|выручк|сумм|деньг)\w*\b", raw))

    lines: list[str] = []
    source = str(clients.get("source") or "")
    visits_available = bool(visits.get("available", visits.get("ok")))
    payments_available = bool(payments.get("available", payments.get("ok")))

    if wants_students or not (wants_lessons or wants_payments):
        if source == "lessonRecords":
            active = int(clients.get("uniqueByRecords") or 0)
            attended = int(clients.get("uniqueByVisits") or 0)
            lines.append(f"По данным МойКласс за {month}: активных учеников/клиентов - {active}.")
            lines.append("Это посчитано по уникальным ученикам в записях на занятия за месяц.")
            if visits_available:
                lines.append(f"Дополнительно: уникальных учеников с фактическим посещением - {attended}, записей на занятия - {visits.get('recordsTotal', 0)}, посещений - {visits.get('count', 0)}, пропусков - {visits.get('missed', 0)}.")
        elif source == "users":
            lines.append(f"Точное количество учеников, которые именно обучались в {month}, через текущие данные МойКласс определить нельзя.")
            lines.append(f"МойКласс отдал только новых учеников в базе за месяц - {clients.get('usersInMonth', 0)}. Это не равно активным ученикам месяца.")
        else:
            lines.append(f"Точное количество учеников за {month} сейчас недоступно через API МойКласс.")
            lines.append("Нужны данные записей/посещений занятий, а текущий API-ответ их не отдал.")
        if lessons.get("ok"):
            lines.append(f"Занятий в месяце: {lessons.get('count', 0)}.")
        if groups.get("ok"):
            lines.append(f"Групп всего в базе: {groups.get('count', 0)}.")
        if clients.get("usersInMonth") is not None:
            lines.append(f"Новых учеников в базе за месяц: {clients.get('usersInMonth', 0)}.")
        return "\n".join(lines).strip()

    if wants_lessons:
        return f"По данным МойКласс за {month}: занятий в месяце - {lessons.get('count', 0) if lessons.get('ok') else 'не удалось получить'}."

    if wants_payments:
        if payments_available:
            return f"По данным МойКласс за {month}: оплат за месяц - {payments.get('count', 0)}, сумма оплат - {payments.get('sum', 0)}."
        return f"Оплаты за {month} сейчас недоступны через API МойКласс. Нужен доступ к endpoint оплат или права API."

    return ""

def _lesson_mk_comment(item: dict[str, Any]) -> str:
    """Return MoyKlass lesson comment/note for the card header, if present.

    We intentionally keep this separate from topic matching. This is for staff
    context: where the group stopped, what to know before replacement, special
    notes, etc. If the field only duplicates the topic, it is hidden.
    """
    topic = _lesson_topic(item).strip().lower()
    keys = (
        "_mkFetchedCommonComment", "commonComment", "commonComments",
        "generalComment", "generalComments", "lessonCommonComment",
        "lessonCommonComments", "lessonComment", "lessonComments",
        "teacherComment", "teacherComments", "mkComment", "mkComments",
        "comment", "comments", "commentItems", "discussion", "discussions",
        "description", "note", "notes", "details", "adminComment", "managerComment",
        "groupComment", "homeworkComment", "clientComment",
    )
    for key in keys:
        text = _clean_mk_text(item.get(key))
        if not text:
            continue
        norm = re.sub(r"\s+", " ", text).strip().lower()
        if topic and norm == topic:
            continue
        # Avoid showing pure IDs/status words as a comment.
        if len(norm) < 3 or norm in {"none", "null", "нет", "-"}:
            continue
        return text[:700]
    return ""




def _comment_kind(value: dict[str, Any]) -> str:
    """Return a lowercase marker describing a MoyKlass comment kind/tab."""
    if not isinstance(value, dict):
        return ""
    parts: list[str] = []
    for key in (
        "type", "kind", "category", "tab", "section", "name", "title",
        "commentType", "comment_type", "entityType", "entity_type",
    ):
        raw = value.get(key)
        if raw is not None:
            parts.append(str(raw))
    return " ".join(parts).lower()


def _looks_like_common_comment(value: dict[str, Any], source_key: str = "") -> bool:
    """Prefer the MoyKlass '+ Общ Комм' / common comment tab when metadata exists."""
    marker = (source_key + " " + _comment_kind(value)).lower()
    return any(x in marker for x in (
        "общ", "common", "general", "lesson_common", "commoncomment", "common_comment",
        "generalcomment", "general_comment",
    ))


def _comment_author_prefix(value: dict[str, Any]) -> str:
    if not isinstance(value, dict):
        return ""
    name = ""
    for key in ("author", "authorName", "userName", "managerName", "teacherName", "createdByName", "creatorName", "name"):
        raw = value.get(key)
        if isinstance(raw, dict):
            name = _clean_mk_text(raw.get("name") or raw.get("fullName") or raw.get("title") or "")
        elif raw:
            name = _clean_mk_text(raw)
        if name:
            break
    created = ""
    for key in ("createdAt", "date", "created", "time", "datetime"):
        if value.get(key):
            created = _clean_mk_text(value.get(key))[:16]
            break
    prefix_parts = [x for x in (name, created) if x]
    return " · ".join(prefix_parts)


def _extract_common_comments_from_api_data(data: Any) -> list[str]:
    """Extract lesson common comments from a MoyKlass response.

    MoyKlass accounts/API builds can expose '+ Общ Комм' differently: as a
    direct field on a lesson, as `comments`, or through a nested comments
    endpoint. This extractor is intentionally tolerant, but it only returns
    short human text and skips service-like values.
    """
    found: list[str] = []
    seen: set[str] = set()

    def add(text: str, prefix: str = "") -> None:
        text = _clean_mk_text(text)
        if not text:
            return
        norm = re.sub(r"\s+", " ", text).strip().lower()
        if len(norm) < 2 or norm in {"none", "null", "нет", "-"}:
            return
        # Skip obvious status-only service strings.
        if norm in {"open", "closed", "done", "true", "false", "0", "1"}:
            return
        text = text[:700]
        full = f"{prefix}: {text}" if prefix else text
        key = re.sub(r"\s+", " ", full).strip().lower()
        if key not in seen:
            seen.add(key)
            found.append(full)

    def walk(value: Any, source_key: str = "", depth: int = 0) -> None:
        if depth > 5 or value is None:
            return
        if isinstance(value, list):
            for item in value[:30]:
                walk(item, source_key, depth + 1)
            return
        if isinstance(value, dict):
            # A dict that itself looks like a comment object.
            text = ""
            for key in ("text", "comment", "message", "body", "content", "value", "note", "description", "commentText"):
                if value.get(key):
                    text = _clean_mk_text(value.get(key))
                    break
            if text and (_looks_like_common_comment(value, source_key) or source_key.lower() in {
                "comments", "commentitems", "commoncomments", "generalcomments", "lessoncomments",
                "commoncomment", "generalcomment", "lessoncommoncomments",
            }):
                add(text, _comment_author_prefix(value))

            # Direct fields whose name strongly indicates the common lesson comment.
            for key, child in value.items():
                key_l = str(key).lower()
                if child is None:
                    continue
                strong = any(x in key_l for x in (
                    "commoncomment", "common_comment", "generalcomment", "general_comment",
                    "lessoncommon", "общ", "obsch", "commoncomments", "generalcomments",
                ))
                ordinary_comment_container = key_l in {"comments", "commentitems", "lessoncomments"}
                if strong:
                    if isinstance(child, (str, int, float)):
                        add(str(child))
                    else:
                        walk(child, key_l, depth + 1)
                elif ordinary_comment_container:
                    walk(child, key_l, depth + 1)
                elif isinstance(child, (dict, list)) and depth < 3:
                    walk(child, key_l, depth + 1)
            return
        if isinstance(value, str) and any(x in source_key.lower() for x in ("common", "general", "общ", "comment")):
            add(value)

    walk(data)
    return found[:5]



def _extract_comment_texts_from_api_data(data: Any, force: bool = False) -> list[str]:
    """Extract human comment texts from unknown MoyKlass comment/visit responses.

    This is broader than `_extract_common_comments_from_api_data`: when we call an
    endpoint that is already named `comments`, `notes`, `attendance`, or `visits`,
    MoyKlass can return generic records under `items`/`data` without a field that
    literally says `commonComment`. In that case accepting common text fields is
    safer than returning nothing. We still skip short/service-like values.
    """
    found: list[str] = []
    seen: set[str] = set()
    text_keys = (
        "text", "comment", "message", "body", "content", "value", "note", "notes",
        "description", "commentText", "commonComment", "generalComment", "lessonComment",
        "teacherComment", "adminComment", "managerComment", "memo", "remark", "remarks",
    )

    def add(text: Any, prefix: str = "") -> None:
        cleaned = _clean_mk_text(text)
        if not cleaned:
            return
        norm = re.sub(r"\s+", " ", cleaned).strip().lower()
        if len(norm) < 2 or norm in {"none", "null", "нет", "-", "open", "closed", "done", "true", "false", "0", "1"}:
            return
        # Skip JSON-looking service payloads and very long encoded blobs.
        if (norm.startswith("{") and norm.endswith("}")) or "%22" in norm[:80]:
            return
        full = f"{prefix}: {cleaned}" if prefix else cleaned
        key = re.sub(r"\s+", " ", full).strip().lower()
        if key not in seen:
            seen.add(key)
            found.append(full[:700])

    def walk(value: Any, source_key: str = "", depth: int = 0) -> None:
        if depth > 7 or value is None:
            return
        if isinstance(value, list):
            for child in value[:80]:
                walk(child, source_key, depth + 1)
            return
        if isinstance(value, dict):
            source_l = source_key.lower()
            should_read_here = force or any(x in source_l for x in (
                "comment", "comments", "note", "notes", "общ", "common", "general", "visit", "attendance", "record"
            ))
            prefix = _comment_author_prefix(value)
            # Direct comment object fields.
            for key in text_keys:
                if key in value and value.get(key) not in (None, ""):
                    key_l = key.lower()
                    if should_read_here or any(x in key_l for x in ("comment", "note", "text", "message", "remark", "memo")):
                        add(value.get(key), prefix)
                        # Do not return; one object can have text + note variants.
            for key, child in value.items():
                key_l = str(key).lower()
                # Strong direct field name.
                if any(x in key_l for x in (
                    "commoncomment", "common_comment", "generalcomment", "general_comment", "lessoncomment",
                    "lesson_comment", "общ", "comment", "note", "memo", "remark"
                )) and isinstance(child, (str, int, float)):
                    add(child, prefix)
                elif isinstance(child, (dict, list)):
                    walk(child, key_l, depth + 1)
            return
        if isinstance(value, str) and (force or any(x in source_key.lower() for x in ("comment", "note", "общ", "common", "general"))):
            add(value)

    walk(data)
    return found[:8]

def _lesson_material_query(item: dict[str, Any]) -> str:
    """Build a Notion-material query only when MoyKlass has an actual topic.

    If topic is empty, we intentionally return an empty string and do not use
    groupName/name/title as a fallback, because that creates false Notion links.
    """
    topic = _lesson_topic(item).strip()
    if not topic:
        return ""
    group = _lesson_group_value(item)
    query = topic
    # If the topic contains only a number/title without a course, group can help,
    # but it must never replace a missing topic.
    if query and not _course_has_explicit_key(query) and group:
        query = f"{query} {group}".strip()
    return query


def _lesson_to_card(item: dict[str, Any], control: dict[str, Any] | None = None) -> dict[str, Any]:
    control = control or {}
    return {
        "id": _lesson_id(item),
        "date": _lesson_date_value(item),
        "time": _lesson_time_value(item),
        "group": _lesson_group_value(item),
        "room": _lesson_room_value(item),
        "teacher": _lesson_teacher_value(item),
        "topic": _lesson_topic(item),
        "status": _lesson_status_value(item),
        "preparationStatus": control.get("preparation_status") or "not_started",
        "lessonStatus": control.get("lesson_status") or "not_started",
        "myclassStatus": control.get("myclass_status") or "not_started",
        "parentReportStatus": control.get("parent_report_status") or "not_started",
        "worksStatus": control.get("works_status") or "not_started",
        "classroomStatus": control.get("classroom_status") or "not_started",
        "problemStatus": control.get("problem_status") or "",
        "problemComment": control.get("problem_comment") or "",
        "preparationComment": control.get("preparation_comment") or "",
        "prepMaterialStatus": control.get("prep_material_status") or "not_checked",
        "prepVideoStatus": control.get("prep_video_status") or "not_checked",
        "prepPracticeStatus": control.get("prep_practice_status") or "not_checked",
        "prepResultStatus": control.get("prep_result_status") or "not_checked",
        "prepResultFileId": control.get("prep_result_file_id") or "",
        "lessonComment": control.get("lesson_comment") or "",
        "mkComment": _lesson_mk_comment(item),
    }


def _lesson_fields_for_control(item: dict[str, Any], user_id: int, mk_teacher_id: str) -> dict[str, Any]:
    return {
        "lesson_date": _lesson_date_value(item),
        "lesson_time": _lesson_time_value(item),
        "group_name": _lesson_group_value(item),
        "lesson_topic": _lesson_topic(item),
        "teacher_user_id": user_id,
        "teacher_name": _lesson_teacher_value(item),
        "mk_teacher_id": mk_teacher_id,
    }


def _telegram_send_with_webapp(
    bot_token: str,
    user_id: int | None,
    text: str,
    web_app_url: str = "",
) -> tuple[bool, str]:
    """Send a Telegram message, optionally with an inline WebApp button."""
    if not bot_token or not user_id:
        return False, "no_token_or_user_id"
    try:
        import requests as _req
        payload: dict = {"chat_id": int(user_id), "text": text}
        if web_app_url:
            payload["reply_markup"] = {
                "inline_keyboard": [[{"text": "Открыть кабинет Yellow Club", "web_app": {"url": web_app_url}}]]
            }
        resp = _req.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json=payload,
            timeout=10,
        )
        if resp.status_code >= 400:
            return False, resp.text[:500]
        return True, ""
    except Exception as exc:
        return False, str(exc)[:500]


def _telegram_send(bot_token: str, user_id: int | None, text: str) -> tuple[bool, str]:
    if not bot_token or not user_id:
        return False, "no_token_or_user_id"
    try:
        import requests
        resp = requests.post(
            f"https://api.telegram.org/bot{bot_token}/sendMessage",
            json={"chat_id": int(user_id), "text": text},
            timeout=10,
        )
        if resp.status_code >= 400:
            return False, resp.text[:500]
        return True, ""
    except Exception as exc:
        return False, str(exc)[:500]


def _telegram_send_document(bot_token: str, user_id: int | None, file_path: Path, file_name: str, caption: str, mime_type: str = "") -> tuple[bool, str]:
    """Send a saved preparation-result file to a reviewer in Telegram."""
    if not bot_token or not user_id:
        return False, "no_token_or_user_id"
    if not file_path.exists() or not file_path.is_file():
        return False, "file_not_found"
    try:
        import requests
        with file_path.open("rb") as fh:
            files = {"document": (file_name or file_path.name, fh, mime_type or "application/octet-stream")}
            data = {"chat_id": int(user_id), "caption": caption[:1024]}
            resp = requests.post(
                f"https://api.telegram.org/bot{bot_token}/sendDocument",
                data=data,
                files=files,
                timeout=60,
            )
        if resp.status_code >= 400:
            return False, resp.text[:500]
        return True, ""
    except Exception as exc:
        return False, str(exc)[:500]


def _course_has_explicit_key(text: str) -> bool:
    try:
        return bool(build_query_profile(text or "").course_keys)
    except Exception:
        return False


def _all_prep_steps_done(control: dict[str, Any]) -> bool:
    return all(str(control.get(key) or "") == "done" for key in (
        "prep_material_status",
        "prep_video_status",
        "prep_practice_status",
        "prep_result_status",
    ))


def _missing_close_steps(control: dict[str, Any]) -> list[str]:
    checks = [
        ("parent_report_status", "отчёт родителям"),
        ("myclass_status", "МойКласс"),
        ("works_status", "работы учеников"),
        ("classroom_status", "кабинет"),
    ]
    return [label for key, label in checks if str(control.get(key) or "") != "done"]


# ---- Camp week detection helpers ----

def _parse_camp_week_from_name(name: str) -> dict[str, Any] | None:
    """Parse week key and date range from groupName like 'Yellow Summer Week 1(29.06-03.07),YC1'."""
    if not name:
        return None
    m = re.search(
        r'(Yellow Summer Week\s+(\d+))\s*\((\d{2})\.(\d{2})-(\d{2})\.(\d{2})\)',
        name, re.IGNORECASE
    )
    if not m:
        return None
    week_key = m.group(1).strip()
    week_num = int(m.group(2))
    sd, sm, ed, em = m.group(3), m.group(4), m.group(5), m.group(6)
    g = re.search(r',\s*(YC\d+)\s*$', name.strip(), re.IGNORECASE)
    group = g.group(1).upper() if g else ""
    return {
        "key": week_key,
        "week_num": week_num,
        "raw_start": f"{sd}.{sm}",
        "raw_end": f"{ed}.{em}",
        "group": group,
    }


def _resolve_week_dates(parsed: dict[str, Any], lesson_year: int) -> tuple[str, str] | None:
    """Convert DD.MM to YYYY-MM-DD using the lesson's year."""
    try:
        sd, sm = parsed["raw_start"].split(".")
        ed, em = parsed["raw_end"].split(".")
        start = date(lesson_year, int(sm), int(sd))
        end = date(lesson_year, int(em), int(ed))
        if end < start:
            end = date(lesson_year + 1, int(em), int(ed))
        return start.isoformat(), end.isoformat()
    except Exception:
        return None


def _week_range_from_lesson_date(lesson_date_str: str) -> tuple[str, str] | None:
    """Fallback: compute Mon-Fri week range from a lesson date."""
    try:
        d = date.fromisoformat(lesson_date_str[:10])
        monday = d - timedelta(days=d.weekday())
        friday = monday + timedelta(days=4)
        return monday.isoformat(), friday.isoformat()
    except Exception:
        return None


def _detect_camp_weeks(
    matching: list[tuple[dict[str, Any], list[dict[str, Any]]]],
) -> list[dict[str, Any]]:
    """Group matching lessons by camp week. Returns sorted list of week dicts."""
    weeks: dict[str, dict[str, Any]] = {}
    for lesson, _ in matching:
        lesson_date_str = _lesson_date_value(lesson) or ""
        lesson_year = int(lesson_date_str[:4]) if len(lesson_date_str) >= 4 else date.today().year
        lesson_group = _lesson_group_value(lesson) or ""

        parsed = _parse_camp_week_from_name(lesson_group)
        if parsed:
            key = parsed["key"]
            resolved = _resolve_week_dates(parsed, lesson_year)
            start_date, end_date = resolved if resolved else ("", "")
            group = parsed["group"]
            week_num = parsed["week_num"]
        else:
            # Fallback: group by calendar week of lesson date
            wr = _week_range_from_lesson_date(lesson_date_str) if lesson_date_str else None
            if not wr:
                continue
            start_date, end_date = wr
            key = f"Week {start_date}"
            group = ""
            week_num = 0

        if key not in weeks:
            weeks[key] = {
                "key": key,
                "label": lesson_group or key,
                "startDate": start_date,
                "endDate": end_date,
                "lessonsCount": 0,
                "groups": [],
                "week_num": week_num,
            }
        weeks[key]["lessonsCount"] += 1
        if group and group not in weeks[key]["groups"]:
            weeks[key]["groups"].append(group)

    result = sorted(weeks.values(), key=lambda w: (w["week_num"] or 99, w["startDate"]))
    return result


def _select_active_camp_week(
    camp_weeks: list[dict[str, Any]],
    today: date,
    mode: str,
    manual_start: str,
    manual_end: str,
) -> dict[str, Any]:
    """Choose which camp week to treat as active."""
    if mode == "manual" and manual_start and manual_end:
        # Find matching week in detected list for label/key, or create minimal dict
        for w in camp_weeks:
            if w["startDate"] == manual_start and w["endDate"] == manual_end:
                return {**w, "mode": "manual", "reason": "manual_override"}
        return {
            "key": "manual",
            "label": f"{manual_start} — {manual_end}",
            "startDate": manual_start,
            "endDate": manual_end,
            "lessonsCount": 0,
            "groups": [],
            "mode": "manual",
            "reason": "manual_override",
        }

    if not camp_weeks:
        return {"mode": mode, "reason": "no_weeks_found", "startDate": "", "endDate": ""}

    today_str = today.isoformat()
    # 1. Current week
    for w in camp_weeks:
        if w["startDate"] <= today_str <= w["endDate"]:
            return {**w, "mode": mode, "reason": "current_week"}
    # 2. Nearest future week
    future = [w for w in camp_weeks if w["startDate"] > today_str]
    if future:
        return {**future[0], "mode": mode, "reason": "nearest_future"}
    # 3. Last past week
    return {**camp_weeks[-1], "mode": mode, "reason": "last_past"}


_FOOD_AUTO_REMINDER_STATUS: dict[str, Any] = {
    "lastRunAt": None,
    "lastResult": None,
    "runCount": 0,
}


class FoodAutoReminderWatcher:
    def __init__(self, ctx: "MiniAppContext") -> None:
        self._ctx = ctx
        self._thread = threading.Thread(
            target=self._loop, name="food-auto-reminder", daemon=True
        )

    def start(self) -> None:
        self._thread.start()

    def _loop(self) -> None:
        import time as _time
        _time.sleep(60)
        interval = max(1, getattr(self._ctx.settings, "food_auto_reminder_check_interval_minutes", 15)) * 60
        while True:
            try:
                result = self._run_once()
                from datetime import datetime, timezone as _tz
                _FOOD_AUTO_REMINDER_STATUS["lastRunAt"] = datetime.now(_tz.utc).isoformat()
                _FOOD_AUTO_REMINDER_STATUS["lastResult"] = result
                _FOOD_AUTO_REMINDER_STATUS["runCount"] = _FOOD_AUTO_REMINDER_STATUS.get("runCount", 0) + 1
            except Exception:
                log.exception("FoodAutoReminderWatcher: unhandled error")
            _time.sleep(interval)

    def _run_once(self) -> dict[str, Any]:
        minutes = getattr(self._ctx.settings, "food_auto_reminder_minutes_before_deadline", 120)
        menus = self._ctx.storage.get_published_menus_needing_auto_reminder(int(minutes))
        menus_with_missing = 0
        sent_total = 0
        errors: list[str] = []
        for menu in menus:
            try:
                res = self._ctx._send_food_missing_reminders_core(
                    int(menu["id"]), dict(menu), triggered_by="auto"
                )
                if res.get("sentCount", 0) > 0 or res.get("alreadyRemindedCount", 0) > 0:
                    menus_with_missing += 1
                sent_total += res.get("sentCount", 0)
            except Exception as exc:
                log.exception("FoodAutoReminderWatcher: error for menu %s", menu.get("id"))
                errors.append(f"menu {menu.get('id')}: {exc}")
        return {
            "menusChecked": len(menus),
            "menusWithMissing": menus_with_missing,
            "sentCount": sent_total,
            "errors": errors,
        }


class MiniAppContext:
    def __init__(self) -> None:
        self.settings = load_settings()
        self.storage = Storage(self.settings.db_path)
        self.kb = KnowledgeBase(self.settings.data_dir, use_raw_excel=self.settings.use_raw_excel)
        self.moyklass = MoyKlassClient(self.settings.moyklass_api_url, self.settings.moyklass_api_key, self.settings.moyklass_timeout)
        self.llm = OllamaClient(self.settings.ollama_url, self.settings.ollama_model, self.settings.ollama_timeout)
        self.core = AgentCore(self.kb, self.storage, self.llm, self.settings.kb_top_k, self.settings.kb_max_context_chars)
        self._material_cache: dict[str, tuple[float, dict[str, Any]]] = {}
        self._mk_comment_cache: dict[str, tuple[float, str]] = {}
        self._mk_student_name_cache: dict[str, tuple[float, str]] = {}
        self._client_tasks_sync_cache: dict[str, Any] = {"ts": 0.0, "result": {}}

    def validate_init_data(self, init_data: str, dev_user_id: str = "", unsafe_user_id: str = "", yc_user_id: str = "", yc_ts: str = "", yc_sig: str = "") -> dict[str, Any]:
        if self.settings.web_app_dev_mode and dev_user_id:
            uid = _safe_int(dev_user_id)
            if uid:
                user = self.storage.get_staff_user(uid) or {}
                return {
                    "ok": True,
                    "user_id": uid,
                    "user": {
                        "id": uid,
                        "first_name": user.get("full_name") or "DEV",
                        "username": user.get("username") or "",
                    },
                    "dev": True,
                }
        if not init_data:
            # Primary fallback for YC MVP: the bot opens the Mini App with a short signed launch URL.
            # This covers Telegram clients/proxy combinations where Telegram.WebApp.initData is empty.
            uid = _safe_int(yc_user_id)
            ts = _safe_int(yc_ts)
            if uid and ts and yc_sig:
                age = abs(int(time.time()) - ts)
                payload = f"{uid}:{ts}"
                expected = hmac.new(self.settings.telegram_bot_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
                if age <= 60 * 60 * 24 and hmac.compare_digest(expected, str(yc_sig)):
                    user = self.storage.get_staff_user(uid) or {}
                    return {
                        "ok": True,
                        "user_id": uid,
                        "user": {"id": uid, "first_name": user.get("full_name") or "Telegram", "username": user.get("username") or ""},
                        "dev": False,
                        "signedFallback": True,
                    }
            # Optional less-safe fallback: Telegram.WebApp.initDataUnsafe.user.id.
            if self.settings.web_app_allow_unsafe_fallback and unsafe_user_id:
                uid = _safe_int(unsafe_user_id)
                if uid:
                    user = self.storage.get_staff_user(uid) or {}
                    return {
                        "ok": True,
                        "user_id": uid,
                        "user": {"id": uid, "first_name": user.get("full_name") or "Telegram", "username": user.get("username") or ""},
                        "dev": False,
                        "unsafeFallback": True,
                    }
            return {"ok": False, "error": "Mini App открыт без Telegram initData. Нажмите в боте /menu -> 📲 Открыть кабинет -> новую inline-кнопку."}
        parsed = urllib.parse.parse_qs(init_data, keep_blank_values=True)
        data = {k: v[-1] for k, v in parsed.items()}
        received_hash = data.pop("hash", "")
        if not received_hash:
            return {"ok": False, "error": "В initData нет hash."}
        check_string = "\n".join(f"{k}={data[k]}" for k in sorted(data))
        secret_key = hmac.new(b"WebAppData", self.settings.telegram_bot_token.encode("utf-8"), hashlib.sha256).digest()
        calculated = hmac.new(secret_key, check_string.encode("utf-8"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(calculated, received_hash):
            return {"ok": False, "error": "Telegram initData не прошёл проверку подписи."}
        auth_date = _safe_int(data.get("auth_date"))
        if auth_date and time.time() - auth_date > 60 * 60 * 24:
            return {"ok": False, "error": "Telegram initData устарел. Закройте и откройте кабинет заново."}
        try:
            user = json.loads(data.get("user", "{}"))
        except Exception:
            user = {}
        user_id = _safe_int(user.get("id"))
        if not user_id:
            return {"ok": False, "error": "Не удалось определить пользователя Telegram."}
        return {"ok": True, "user_id": user_id, "user": user, "dev": False}

    def _base_role_for_user(self, user_id: int) -> str:
        """Real role from storage/env, without test-role override."""
        role = self.storage.get_staff_role(user_id, self.settings.admin_ids)
        if int(user_id or 0) in set(int(x) for x in (self.settings.senior_teacher_ids or []) if x):
            if role not in {"owner", "operations"}:
                return "methodist"
        if role:
            return role
        # Non-staff user: if food module is enabled, treat as parent
        if getattr(self.settings, "food_module_enabled", False):
            return "parent"
        return ""

    def _can_use_role_test(self, user_id: int) -> bool:
        if not bool(getattr(self.settings, "web_app_test_roles", False)):
            return False
        real_role = self._base_role_for_user(user_id)
        return int(user_id or 0) in set(int(x) for x in (self.settings.admin_ids or []) if x) or real_role in FULL_ADMIN_ROLES

    def _test_teacher_options(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        try:
            for user in self.storage.list_staff_users(limit=500):
                teacher_id = str(user.get("mk_teacher_id") or "").strip()
                if not teacher_id or teacher_id in seen:
                    continue
                role = str(user.get("role") or "").strip().lower()
                if role not in {"teacher", "methodist", "intern", "owner", "operations"}:
                    continue
                seen.add(teacher_id)
                name = user.get("mk_teacher_name") or user.get("full_name") or user.get("username") or f"teacherId {teacher_id}"
                items.append({"id": teacher_id, "name": str(name), "userId": user.get("user_id")})
        except Exception:
            pass
        return items

    def _default_test_teacher_id(self, user_id: int) -> str:
        test = self.storage.get_staff_test_mode(user_id)
        if test.get("mk_teacher_id"):
            return str(test.get("mk_teacher_id") or "").strip()
        real = self.storage.get_staff_mk_teacher_id(user_id)
        if real:
            return real
        options = self._test_teacher_options()
        return str(options[0].get("id") or "").strip() if options else ""

    def _mk_teacher_id_for_user(self, user_id: int) -> str:
        test = self.storage.get_staff_test_mode(user_id)
        if test.get("enabled") and test.get("mk_teacher_id"):
            return str(test.get("mk_teacher_id") or "").strip()
        return self.storage.get_staff_mk_teacher_id(user_id)

    def _resolve_teacher_mk_id(self, user_id: int) -> tuple[str, str]:
        """Resolve mk_teacher_id for user, with auto-match fallback by name.
        Returns (mk_teacher_id, method) where method is 'linked', 'name_match', or ''.
        If a unique name match is found, persists the link to avoid repeated lookups."""
        existing = self._mk_teacher_id_for_user(user_id)
        if existing:
            return existing, "linked"
        staff = self.storage.get_staff_user(user_id)
        full_name = str(staff.get("full_name") or "") if staff else ""
        if not full_name:
            return "", ""
        candidates = self.storage.find_teacher_candidates_by_name(full_name)
        if len(candidates) == 1:
            mk_id = candidates[0]["mk_teacher_id"]
            teacher_name = candidates[0]["teacher_name"]
            log.info(
                "food: auto-matched user %s (%s) → teacherId %s by name",
                user_id, full_name, mk_id,
            )
            self.storage.set_staff_mk_teacher(user_id, mk_id, teacher_name)
            return mk_id, "name_match"
        if len(candidates) > 1:
            log.warning(
                "food: ambiguous teacher name match for user %s (%s): %d candidates, no auto-link",
                user_id, full_name, len(candidates),
            )
        return "", ""

    def _role_for_user(self, user_id: int) -> str:
        test = self.storage.get_staff_test_mode(user_id)
        if self._can_use_role_test(user_id) and test.get("enabled") and test.get("role"):
            return str(test.get("role") or "other")
        return self._base_role_for_user(user_id)

    def _admin_tabs_for_role(self, role: str) -> list[str]:
        return list(ADMIN_TABS_BY_ROLE.get(role or "", []))

    def _capabilities_for_user(self, user_id: int) -> dict[str, Any]:
        role = self._role_for_user(user_id)
        admin_tabs = self._admin_tabs_for_role(role)
        if getattr(self.settings, "food_module_enabled", False) and role in ADMIN_ROLES:
            if "food-debug" not in admin_tabs:
                admin_tabs = list(admin_tabs) + ["food-debug"]
            if "food-children" not in admin_tabs:
                admin_tabs = list(admin_tabs) + ["food-children"]
            if "food-menu" not in admin_tabs:
                admin_tabs = list(admin_tabs) + ["food-menu"]
            if "food-report" not in admin_tabs:
                admin_tabs = list(admin_tabs) + ["food-report"]
        _staff_food_roles = {"owner", "methodist", "operations", "teacher", "intern", "client_manager", "other"}
        can_order_staff_lunch = bool(getattr(self.settings, "food_module_enabled", False)) and role in _staff_food_roles
        if can_order_staff_lunch and "food-lunch" not in admin_tabs:
            admin_tabs = list(admin_tabs) + ["food-lunch"]
        food_enabled = bool(getattr(self.settings, "food_module_enabled", False))
        return {
            "canUseLessons": role in LESSON_ROLES,
            "canUseSchedule": role in SCHEDULE_ROLES,
            "canUseOpenSlots": role in OPEN_SLOTS_ROLES,
            "canUseReports": role in REPORT_ROLES,
            "canUseClientTasks": role in CLIENT_TASK_ROLES,
            "canUseAdmin": bool(admin_tabs),
            "canUseInternship": role in INTERN_ROLES,
            "canReviewPrepResults": role in ADMIN_ROLES,
            "canRunScheduleCheck": role in ADMIN_ROLES,
            "canManageUsers": role in FULL_ADMIN_ROLES,
            "canUseNotionDiagnostics": role in FULL_ADMIN_ROLES,
            "canUseTestRoles": self._can_use_role_test(user_id),
            "canAskAgent": role not in ("parent", "kitchen", "restaurant"),
            "isParent": role == "parent",
            "adminTabs": admin_tabs,
            "canOrderStaffLunch": can_order_staff_lunch,
            "canUseFoodKitchenSummary": food_enabled and role in KITCHEN_SUMMARY_ROLES,
            "canSeeFoodPrices": food_enabled and role in FOOD_PRICE_ROLES,
            "canSeeFoodCostReport": food_enabled and role in FOOD_PRICE_ROLES,
            "canCreateFoodMenu": food_enabled and role in FOOD_MENU_EDIT_ROLES,
            "canEditFoodMenuDraft": food_enabled and role in FOOD_MENU_EDIT_ROLES,
            "canPublishFoodMenu": food_enabled and role in FOOD_MENU_EDIT_ROLES,
            "canEditFoodDeadline": food_enabled and role in ADMIN_ROLES,
            "canDeleteFoodMenu": food_enabled and role in FOOD_MENU_DELETE_ROLES,
            "foodMenuOcrEnabled": bool(getattr(self.settings, "food_menu_ocr_enabled", False)) and role in FOOD_MENU_EDIT_ROLES,
            "canAdminFoodOrders": food_enabled and role in FOOD_ADMIN_EDIT_ROLES,
        }

    def me(self, auth: dict[str, Any]) -> dict[str, Any]:
        user_id = int(auth["user_id"])
        staff = self.storage.get_staff_user(user_id) or {}
        # Auto-save Telegram display name when staff_users.full_name is empty
        if staff:
            tg_user = auth.get("user") or {}
            tg_first = str(tg_user.get("first_name") or "").strip()
            tg_last = str(tg_user.get("last_name") or "").strip()
            tg_full = f"{tg_first} {tg_last}".strip() if (tg_first or tg_last) else ""
            tg_username = str(tg_user.get("username") or "").strip()
            if tg_full or tg_username:
                self.storage.update_staff_display_name(user_id, tg_full, tg_username)
                if tg_full and not staff.get("full_name"):
                    staff["full_name"] = tg_full
                if tg_username and not staff.get("username"):
                    staff["username"] = tg_username
        profile = self.storage.get_teacher_profile(user_id) or {}
        role = self._role_for_user(user_id)
        real_role = self._base_role_for_user(user_id)
        can_test = self._can_use_role_test(user_id)
        test_mode = self.storage.get_staff_test_mode(user_id) if can_test else {"enabled": False, "role": "", "mk_teacher_id": ""}
        capabilities = self._capabilities_for_user(user_id)
        _mk_name = str(staff.get("mk_teacher_name") or "").strip()
        _full_name = str(staff.get("full_name") or "").strip()
        _tg_name = str((auth.get("user") or {}).get("first_name") or "").strip()
        _username = str(staff.get("username") or (auth.get("user") or {}).get("username") or "").strip()
        if _mk_name:
            _resolved_display_name = _mk_name
        elif _full_name:
            _resolved_display_name = _full_name
        elif _tg_name:
            _resolved_display_name = _tg_name
        elif _username:
            _resolved_display_name = _username
        else:
            _resolved_display_name = f"Сотрудник #{user_id}"
        data = {
            "userId": user_id,
            "telegramUser": auth.get("user") or {},
            "role": role,
            "roleLabel": ROLE_LABELS.get(role, role or "роль"),
            "realRole": real_role,
            "realRoleLabel": ROLE_LABELS.get(real_role, real_role or "роль"),
            "fullName": _full_name or _tg_name or "Сотрудник",
            "username": _username,
            "resolvedDisplayName": _resolved_display_name,
            "mkTeacherId": self._mk_teacher_id_for_user(user_id),
            "mkTeacherName": _mk_name,
            "profile": profile,
            "isSeniorTeacher": role == "methodist" or user_id in set(int(x) for x in (self.settings.senior_teacher_ids or []) if x),
            "devMode": bool(auth.get("dev")),
            "capabilities": capabilities,
            "testMode": test_mode,
            "internTrialMaterialUrl": str(getattr(self.settings, "intern_trial_material_url", "") or ""),
            "mvpReleaseMode": bool(getattr(self.settings, "mvp_release_mode", False)),
            "foodModuleEnabled": bool(getattr(self.settings, "food_module_enabled", False)),
            "foodMenuOcrEnabled": capabilities.get("foodMenuOcrEnabled", False),
            "campClassNameFilter": (
                str(getattr(self.settings, "camp_lesson_name_filter", "") or "").strip()
                or str(getattr(self.settings, "camp_class_name_filter", "Summer Camp") or "Summer Camp").strip()
            ),
            "campActiveWeekMode": str(getattr(self.settings, "camp_active_week_mode", "auto") or "auto").strip(),
            "campActiveStartDate": str(getattr(self.settings, "camp_active_start_date", "") or "").strip(),
            "campActiveEndDate": str(getattr(self.settings, "camp_active_end_date", "") or "").strip(),
        }
        if can_test:
            mvp_mode = bool(getattr(self.settings, "mvp_release_mode", False))
            _mvp_role_values = {"owner", "admin", "teacher", "methodist", "intern", "director", "client_manager", "kitchen", "operations"}
            role_opts = [o for o in TEST_ROLE_OPTIONS if o["value"] in _mvp_role_values] if mvp_mode else TEST_ROLE_OPTIONS
            data["roleOptions"] = role_opts
            data["testTeachers"] = self._test_teacher_options()
        return data

    def set_test_role(self, auth: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        user_id = int(auth["user_id"])
        if not self._can_use_role_test(user_id):
            return {"ok": False, "error": "Переключение тестовых ролей доступно только владельцу/админу."}
        enabled = bool(payload.get("enabled", True))
        if not enabled or str(payload.get("role") or "").strip().lower() in {"", "off", "real"}:
            self.storage.clear_staff_test_mode(user_id)
            return {"ok": True, "me": self.me(auth)}
        role = str(payload.get("role") or "").strip().lower()
        valid_roles = {str(x.get("value")) for x in TEST_ROLE_OPTIONS}
        mvp_mode = bool(getattr(self.settings, "mvp_release_mode", False))
        if mvp_mode:
            valid_roles = valid_roles & {"owner", "admin", "teacher", "methodist", "intern", "director", "client_manager", "kitchen", "operations"}
        if role not in valid_roles:
            return {"ok": False, "error": "Неизвестная тестовая роль."}
        mk_teacher_id = str(payload.get("mkTeacherId") or payload.get("mk_teacher_id") or "").strip()
        if role in {"teacher", "intern"} and not mk_teacher_id:
            mk_teacher_id = self._default_test_teacher_id(user_id)
        self.storage.set_staff_test_mode(user_id, role, mk_teacher_id)
        return {"ok": True, "me": self.me(auth)}

    def lessons(self, auth: dict[str, Any], days: int = 7) -> dict[str, Any]:
        user_id = int(auth["user_id"])
        role = self._role_for_user(user_id)
        mk_teacher_id = self._mk_teacher_id_for_user(user_id)
        if role not in LESSON_ROLES:
            return {"ok": False, "error": "Кабинет занятий доступен преподавателю, стажеру, методисту и владельцу."}
        if not self.settings.moyklass_enabled:
            return {"ok": False, "error": "МойКласс не настроен: проверьте MOYKLASS_API_KEY."}
        is_admin = self._is_admin_role(auth)
        if not is_admin and role in TEACHER_LIKE_ROLES and not mk_teacher_id:
            return {"ok": False, "error": "У вас не настроена привязка к teacherId МойКласс."}
        today = date.today()
        end = today + timedelta(days=max(1, min(int(days or 7), 31)))
        result = self.moyklass.get_lessons_between(today, end, limit=400)
        if not is_admin and role in TEACHER_LIKE_ROLES and mk_teacher_id:
            result = filter_lessons_by_teacher(result, mk_teacher_id)
            _total_before = len(extract_items(result.data)) if result.ok else 0
            log.info(
                "[teacher-access] uid=%s role=%s mk_teacher_id=%s lessons_found=%s ok=%s",
                user_id, role, mk_teacher_id, _total_before, result.ok,
            )
        if not result.ok:
            return {"ok": False, "error": f"МойКласс: status={result.status} {result.error}".strip()}
        items = [x for x in extract_items(result.data) if isinstance(x, dict)]
        cards: list[dict[str, Any]] = []
        for item in items:
            lesson_id = _lesson_id(item)
            if not lesson_id:
                continue
            # Store/update local control row for the real teacher view.
            # Admin/senior views must not overwrite teacher_user_id with the reviewer ID.
            if not is_admin and role in TEACHER_LIKE_ROLES:
                self.storage.upsert_teacher_lesson_control(lesson_id, **_lesson_fields_for_control(item, user_id, mk_teacher_id))
            control = self.storage.get_teacher_lesson_control(lesson_id) or {}
            cards.append(_lesson_to_card(item, control))
        cards.sort(key=lambda x: (str(x.get("date") or ""), str(x.get("time") or ""), str(x.get("group") or "")))
        return {"ok": True, "days": days, "items": cards}

    def _fetch_mk_common_comment(self, lesson_id: str, item: dict[str, Any] | None = None, deep_probe: bool = False) -> str:
        """Read '+ Общ Комм' for a lesson from MoyKlass.

        Fast mode is used by the regular lesson card. It extracts comments only
        from the already loaded lesson object and from a short in-memory cache.
        The older endpoint-by-endpoint probing can make every card opening slow,
        so it is reserved for the admin diagnostic endpoint via deep_probe=True.
        """
        cache_key = str(lesson_id or "").strip()
        now = time.time()
        cached = self._mk_comment_cache.get(cache_key) if cache_key else None
        if cached:
            cached_at, cached_text = cached
            ttl = 60 * 60 * 6 if cached_text else 60 * 10
            if now - cached_at < ttl:
                return cached_text

        collected: list[str] = []
        seen: set[str] = set()

        def add_many(values: list[str], source: str = "") -> None:
            for text in values:
                cleaned = _clean_mk_text(text)
                norm = re.sub(r"\s+", " ", cleaned).strip().lower()
                if not norm or norm in seen:
                    continue
                seen.add(norm)
                collected.append(cleaned)
            # Stable mode: do not spam Mini App console with probe diagnostics.

        if item:
            add_many(_extract_common_comments_from_api_data(item), "lesson_object_strict")
            # Broad fallback for fields that the public API may name simply `comments` or `notes`.
            if not collected:
                add_many(_extract_comment_texts_from_api_data(item, force=False), "lesson_object_broad")

        if collected:
            text = "\n\n".join(collected[:3]).strip()[:1200]
            if cache_key:
                self._mk_comment_cache[cache_key] = (now, text)
            return text

        if not deep_probe:
            if cache_key:
                self._mk_comment_cache[cache_key] = (now, "")
            return ""

        endpoint_attempts = [
            # Lesson detail with possible include/expand switches.
            (f"/v1/company/lessons/{lesson_id}", {"include": "comments,notes,visits,attendance,records"}, False),
            (f"/v1/company/lessons/{lesson_id}", {"expand": "comments,notes,visits,attendance,records"}, False),
            (f"/v1/company/lessons/{lesson_id}", {"includeComments": "1", "withComments": "1", "comments": "1"}, False),

            # Likely lesson comment endpoints.
            (f"/v1/company/lessons/{lesson_id}/comments", None, True),
            (f"/v1/company/lessons/{lesson_id}/commonComments", None, True),
            (f"/v1/company/lessons/{lesson_id}/common-comments", None, True),
            (f"/v1/company/lessons/{lesson_id}/notes", None, True),
            (f"/v1/company/lesson/{lesson_id}/comments", None, True),
            (f"/v1/company/lesson/{lesson_id}/notes", None, True),

            # Collection endpoints filtered by lesson id.
            (f"/v1/company/lessonComments", {"lessonId": lesson_id, "limit": "100"}, True),
            (f"/v1/company/lesson-comments", {"lessonId": lesson_id, "limit": "100"}, True),
            (f"/v1/company/comments", {"lessonId": lesson_id, "limit": "100"}, True),
            (f"/v1/company/comments", {"entityId": lesson_id, "entityType": "lesson", "limit": "100"}, True),
            (f"/v1/company/notes", {"lessonId": lesson_id, "limit": "100"}, True),

            # Some installations attach lesson comments to visits/attendance records.
            (f"/v1/company/lessonVisits", {"lessonId": lesson_id, "limit": "100"}, True),
            (f"/v1/company/lesson-visits", {"lessonId": lesson_id, "limit": "100"}, True),
            (f"/v1/company/visits", {"lessonId": lesson_id, "limit": "100"}, True),
            (f"/v1/company/attendance", {"lessonId": lesson_id, "limit": "100"}, True),
            (f"/v1/company/lessonRecords", {"lessonId": lesson_id, "limit": "100"}, True),
            (f"/v1/company/lesson-records", {"lessonId": lesson_id, "limit": "100"}, True),
            (f"/v1/company/userLessons", {"lessonId": lesson_id, "limit": "100"}, True),
            (f"/v1/company/studentLessons", {"lessonId": lesson_id, "limit": "100"}, True),
        ]
        diagnostics: list[str] = []  # kept for optional debug only
        for path, params, broad in endpoint_attempts:
            try:
                result = self.moyklass.request("GET", path, params=params)
            except Exception as exc:
                diagnostics.append(f"{path} exception={str(exc)[:120]}")
                continue
            if result.ok:
                try:
                    count = len(extract_items(result.data))
                except Exception:
                    count = 0
                diagnostics.append(f"{path} status={result.status} ok=True count={count}")
            else:
                diagnostics.append(f"{path} status={result.status} ok=False")
                continue
            strict_values = _extract_common_comments_from_api_data(result.data)
            broad_values = _extract_comment_texts_from_api_data(result.data, force=broad)
            add_many(strict_values, path + ":strict")
            if not collected:
                add_many(broad_values, path + ":broad")
            if collected:
                break
        if not collected:
            # Keep a short diagnostic line in logs; this is what we need if the
            # public API hides '+ Общ Комм' completely.
            log.debug("MK common comment not found lesson_id=%s diagnostics=%s", lesson_id, " | ".join(diagnostics[-6:]))
        text = "\n\n".join(collected[:3]).strip()[:1200]
        if cache_key:
            self._mk_comment_cache[cache_key] = (now, text)
        return text

    def admin_mk_comment_probe(self, auth: dict[str, Any], lesson_id: str) -> dict[str, Any]:
        denied = self._require_admin(auth)
        if denied:
            return denied
        lesson_id = str(lesson_id or "").strip()
        if not lesson_id:
            return {"ok": False, "error": "lesson id is empty"}
        result = self.moyklass.get_lesson_by_id(lesson_id)
        item: dict[str, Any] = {}
        if result.ok:
            items = [x for x in extract_items(result.data) if isinstance(x, dict)]
            item = items[0] if items else (result.data if isinstance(result.data, dict) else {})
        comment = self._fetch_mk_common_comment(lesson_id, item, deep_probe=True)
        # Return safe diagnostics: top-level keys only, not full personal data.
        return {
            "ok": True,
            "lessonId": lesson_id,
            "foundComment": comment,
            "lessonEndpointOk": result.ok,
            "lessonStatus": result.status,
            "lessonTopKeys": list(item.keys())[:80] if isinstance(item, dict) else [],
            "lessonPreview": json.dumps(item, ensure_ascii=False)[:1200] if isinstance(item, dict) else "",
        }

    def lesson_detail(self, auth: dict[str, Any], lesson_id: str) -> dict[str, Any]:
        user_id = int(auth["user_id"])
        role = self._role_for_user(user_id)
        mk_teacher_id = self._mk_teacher_id_for_user(user_id)
        result = self.moyklass.get_lesson_by_id(lesson_id)
        if not result.ok:
            return {"ok": False, "error": f"МойКласс: status={result.status} {result.error}".strip()}
        items = [x for x in extract_items(result.data) if isinstance(x, dict)]
        item = items[0] if items else (result.data if isinstance(result.data, dict) else {})
        is_admin = self._is_admin_role(auth)
        if not is_admin and role in TEACHER_LIKE_ROLES and mk_teacher_id and not lesson_has_teacher(item, mk_teacher_id):
            return {"ok": False, "error": "Это занятие не относится к вашему teacherId."}
        if not is_admin and role in TEACHER_LIKE_ROLES:
            self.storage.upsert_teacher_lesson_control(lesson_id, **_lesson_fields_for_control(item, user_id, mk_teacher_id))
        control = self.storage.get_teacher_lesson_control(lesson_id) or {}
        mk_common_comment = self._fetch_mk_common_comment(lesson_id, item)
        if mk_common_comment:
            item["_mkFetchedCommonComment"] = mk_common_comment
        card = _lesson_to_card(item, control)
        material = self.material_for_lesson_item(item)
        prep_files = self.storage.list_teacher_prep_result_files(lesson_id=lesson_id, limit=20)
        return {"ok": True, "lesson": card, "material": material, "prepFiles": prep_files}

    def material_for_lesson_item(self, item: dict[str, Any]) -> dict[str, Any]:
        query = _lesson_material_query(item)
        if not query:
            return {
                "found": False,
                "query": "",
                "reason": "no_topic",
                "error": "В МойКласс не указана тема занятия. Материал Notion не подбирается, чтобы не открыть случайную похожую страницу.",
            }
        cache_key = query.strip().lower()
        cached = self._material_cache.get(cache_key)
        if cached and time.time() - cached[0] < 60 * 60 * 6:
            return dict(cached[1])

        match = self.kb.find_lesson_material(query, max_chars=5500)
        if not match:
            data = {
                "found": False,
                "query": query,
                "error": "Точный материал в Notion не найден. Лучше не открывать случайную похожую тему.",
            }
            self._material_cache[cache_key] = (time.time(), data)
            return dict(data)
        data = {
            "found": True,
            "query": query,
            "title": match.title,
            "source": match.source,
            "notionUrl": match.notion_url,
            "notionPath": match.notion_path,
            "preview": match.text[:2500],
        }
        self._material_cache[cache_key] = (time.time(), data)
        return dict(data)

    def parent_report(self, auth: dict[str, Any], lesson_id: str, variant: str = "normal", details: str = "") -> dict[str, Any]:
        detail = self.lesson_detail(auth, lesson_id)
        if not detail.get("ok"):
            return detail
        lesson = detail.get("lesson") or {}
        material = detail.get("material") or {}
        base_query = " ".join(str(x or "") for x in [lesson.get("topic"), lesson.get("group")]).strip()
        fact_details = details.strip()
        if fact_details and base_query:
            query = f"{base_query}. Фактически на занятии сделали: {fact_details}"
        else:
            query = fact_details or base_query
        mk_comment = str(lesson.get("mkComment") or "").strip()
        if mk_comment:
            query = (query + f". Общий комментарий МойКласс: {mk_comment}").strip()
        if not query:
            return {"ok": False, "error": "Не удалось определить тему для отчёта."}
        report = self.core.build_parent_report("/parent_report " + query, variant=variant or "normal")
        return {"ok": True, "report": report, "variant": variant or "normal", "sourceQuery": query, "material": material}

    # --- Food module ---

    def food_debug_sync_camp_children(self, auth: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        denied = self._require_admin(auth)
        if denied:
            return denied
        food_enabled = bool(getattr(self.settings, "food_module_enabled", False))
        if not food_enabled:
            return {"ok": False, "error": "food_module_disabled", "hint": "Установите FOOD_MODULE_ENABLED=true в .env для использования модуля питания."}

        primary_filter = (
            str(getattr(self.settings, "camp_lesson_name_filter", "") or "").strip()
            or str(getattr(self.settings, "camp_class_name_filter", "Summer Camp") or "Summer Camp").strip()
        )
        alt_raw = str(getattr(self.settings, "camp_lesson_alt_filters", "") or "").strip()
        alt_filters = [f.strip() for f in alt_raw.split(",") if f.strip()] if alt_raw else []
        active_filters = [primary_filter] + [f for f in alt_filters if f.lower() != primary_filter.lower()]

        save_to_db = bool(payload.get("save", False))
        direct_lesson_id = str(payload.get("lessonId") or "").strip()

        if not self.moyklass.is_configured:
            return {"ok": False, "error": "МойКласс не настроен (MOYKLASS_API_KEY не указан)."}

        active_start = str(getattr(self.settings, "camp_active_start_date", "") or "").strip()
        active_end = str(getattr(self.settings, "camp_active_end_date", "") or "").strip()

        today = date.today()
        d1 = today - timedelta(days=7)
        d2 = today + timedelta(days=90)

        lessons_result = self.moyklass.get_lessons_between(d1, d2, limit=500)
        all_lessons: list[dict[str, Any]] = []
        fetch_error = ""
        if lessons_result.ok:
            all_lessons = [l for l in extract_items(lessons_result.data) if isinstance(l, dict)]
        else:
            fetch_error = lessons_result.error[:400]

        # ---- helpers ----
        # Content fields: used for camp matching (topic/name/group). Room fields must NOT trigger match.
        _CONTENT_FIELDS = ("topic", "name", "title", "description", "className", "groupName",
                           "courseName", "subjectName", "_prettyClassName",
                           "comment", "groupTitle", "classTitle")
        _ROOM_FIELDS = frozenset(("_prettyRoomName", "roomName", "classroomName", "roomTitle", "classroomTitle"))
        _ALL_KNOWN_FIELDS = _CONTENT_FIELDS + tuple(_ROOM_FIELDS)

        def _alltext(lesson: dict[str, Any]) -> str:
            parts: list[str] = []
            for k in _ALL_KNOWN_FIELDS:
                v = lesson.get(k)
                if v and isinstance(v, str):
                    parts.append(v)
            for k, v in lesson.items():
                if isinstance(v, str) and v and k not in _ALL_KNOWN_FIELDS:
                    parts.append(v)
            return " ".join(parts)

        def _content_text(lesson: dict[str, Any]) -> str:
            parts: list[str] = []
            for k in _CONTENT_FIELDS:
                v = lesson.get(k)
                if v and isinstance(v, str):
                    parts.append(v)
            for k, v in lesson.items():
                if isinstance(v, str) and v and k not in _ALL_KNOWN_FIELDS:
                    parts.append(v)
            return " ".join(parts)

        def _matched_by(lesson: dict[str, Any], filters: list[str]) -> list[dict[str, Any]]:
            hits: list[dict[str, Any]] = []
            for flt in filters:
                q = flt.lower()
                for field in _CONTENT_FIELDS:
                    v = lesson.get(field)
                    if isinstance(v, str) and q in v.lower():
                        hits.append({"filter": flt, "field": field, "value": v[:120]})
                if not any(h["filter"] == flt for h in hits):
                    for k, v in lesson.items():
                        if isinstance(v, str) and q in v.lower() and k not in _ALL_KNOWN_FIELDS:
                            hits.append({"filter": flt, "field": k, "value": v[:120]})
            return hits

        def _room_only_filters(lesson: dict[str, Any], filters: list[str]) -> list[str]:
            """Return filters that match only in room fields (not in content fields)."""
            room_only: list[str] = []
            ct = _content_text(lesson).lower()
            for flt in filters:
                q = flt.lower()
                if q not in ct:
                    for rf in _ROOM_FIELDS:
                        v = lesson.get(rf)
                        if isinstance(v, str) and q in v.lower():
                            room_only.append(flt)
                            break
            return room_only

        def _user_sample(raw_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for rec in raw_records[:5]:
                s: dict[str, Any] = {"rawKeys": list(rec.keys())}
                for field in ("userId", "user_id", "id", "studentId", "clientId"):
                    if field in rec:
                        s[field] = rec[field]
                user_obj = rec.get("user") or rec.get("student") or rec.get("client") or {}
                if isinstance(user_obj, dict) and user_obj:
                    s["_userKeys"] = list(user_obj.keys())
                    for f in ("id", "name", "fullName", "fio", "firstName", "first_name", "lastName", "last_name"):
                        if f in user_obj:
                            s[f"user_{f}"] = user_obj[f]
                out.append(s)
            return out

        # ---- title samples (first 20 lessons for diagnosis) ----
        lesson_title_samples: list[dict[str, Any]] = []
        for lesson in all_lessons[:20]:
            lesson_title_samples.append({
                "id": str(_pick(lesson, ("id", "lessonId")) or ""),
                "rawKeys": list(lesson.keys()),
                "topic": lesson.get("topic"),
                "name": lesson.get("name"),
                "title": lesson.get("title"),
                "date": _lesson_date_value(lesson),
                "beginTime": lesson.get("beginTime") or lesson.get("startTime"),
                "endTime": lesson.get("endTime") or lesson.get("finishTime"),
                "classId": lesson.get("classId") or lesson.get("groupId"),
                "_prettyClassName": lesson.get("_prettyClassName"),
                "_prettyRoomName": lesson.get("_prettyRoomName"),
                "allTextPreview": _alltext(lesson)[:300],
            })

        # ---- filter matching (content fields only — room fields excluded) ----
        q_lower = [f.lower() for f in active_filters]
        matching: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
        warnings: list[str] = []
        for lesson in all_lessons:
            ct = _content_text(lesson).lower()
            if any(q in ct for q in q_lower):
                matched_by = _matched_by(lesson, active_filters)
                matching.append((lesson, matched_by))
            else:
                # Check if any filter would have matched only via room fields
                ro = _room_only_filters(lesson, active_filters)
                if ro:
                    lesson_label = _lesson_title_value(lesson) or str(_pick(lesson, ("id", "lessonId")) or "?")
                    warnings.append(
                        f"Фильтр {ro!r} совпал только в поле комнаты для занятия «{lesson_label}» — пропущено"
                    )

        # ---- detect camp weeks + select active week ----
        week_mode = str(getattr(self.settings, "camp_active_week_mode", "auto") or "auto").strip().lower()
        camp_weeks = _detect_camp_weeks(matching)
        active_week = _select_active_camp_week(camp_weeks, today, week_mode, active_start, active_end)
        active_week_start = active_week.get("startDate", "")
        active_week_end = active_week.get("endDate", "")

        if active_week.get("reason") == "last_past":
            warnings.append("Все найденные недели смены уже прошли. Выбрана последняя найденная неделя.")
        elif active_week.get("reason") == "no_weeks_found":
            warnings.append("Не удалось определить недели смены из названий занятий — фильтрация по неделе отключена.")

        # Filter matching to active week only
        if active_week_start and active_week_end:
            week_filtered: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
            for lesson, matched_by in matching:
                lesson_date_str = _lesson_date_value(lesson) or ""
                if lesson_date_str >= active_week_start and lesson_date_str <= active_week_end:
                    week_filtered.append((lesson, matched_by))
                else:
                    lesson_label = _lesson_title_value(lesson) or lesson_date_str or "?"
                    warnings.append(
                        f"Занятие «{lesson_label}» ({lesson_date_str}) вне активной недели {active_week_start}–{active_week_end} — пропущено"
                    )
            matching = week_filtered

        # ---- direct lessonId check ----
        direct_data: dict[str, Any] = {}
        if direct_lesson_id:
            try:
                dr = self.moyklass.get_lesson_records(direct_lesson_id, limit=200)
                dr_raw = [r for r in extract_items(dr.data) if isinstance(r, dict)] if dr.ok else []
                direct_data = {
                    "directLessonId": direct_lesson_id,
                    "directLessonRecordsOk": dr.ok,
                    "directLessonRecordsCount": len(dr_raw),
                    "directLessonRecordsSample": _user_sample(dr_raw),
                    "directLessonRecordsError": dr.error[:300] if not dr.ok else "",
                }
            except Exception as exc:
                direct_data = {
                    "directLessonId": direct_lesson_id,
                    "directLessonRecordsOk": False,
                    "directLessonRecordsCount": 0,
                    "directLessonRecordsSample": [],
                    "directLessonRecordsError": str(exc)[:300],
                }

        # ---- phase 1: pre-fetch all lessonRecords ----
        prefetched: list[tuple[dict[str, Any], list[dict[str, Any]], Any, list[dict[str, Any]], str]] = []
        for lesson, matched_by in matching:
            lesson_id_pre = str(_pick(lesson, ("id", "lessonId")) or "").strip()
            _pre_result = None
            _pre_recs: list[dict[str, Any]] = []
            _pre_err = ""
            if lesson_id_pre:
                try:
                    _pre_result = self.moyklass.get_lesson_records(lesson_id_pre, limit=200)
                    _pre_recs = [r for r in extract_items(_pre_result.data) if isinstance(r, dict)] if _pre_result.ok else []
                    if not _pre_result.ok:
                        _pre_err = _pre_result.error[:300]
                except Exception as exc:
                    _pre_err = str(exc)[:300]
            prefetched.append((lesson, matched_by, _pre_result, _pre_recs, _pre_err))

        # ---- phase 2: batch-resolve user objects (one request per unique userId) ----
        seen_uids: set[str] = set()
        all_uids: list[str] = []
        for _, _, _, _recs, _ in prefetched:
            for _rec in _recs:
                _uid = str(_rec.get("userId") or _rec.get("user_id") or _rec.get("studentId") or _rec.get("clientId") or "").strip()
                if _uid and _uid not in seen_uids:
                    seen_uids.add(_uid)
                    all_uids.append(_uid)

        user_cache: dict[str, dict[str, Any]] = {}
        user_resolve_errors: list[str] = []
        if all_uids:
            try:
                user_cache = _v3991_fetch_user_objects(self.moyklass, all_uids)
            except Exception as exc:
                user_resolve_errors.append(str(exc)[:300])

        # ---- phase 3: userResolve diagnostic ----
        resolved_count = sum(1 for uid in all_uids if uid in user_cache)
        user_resolve_sample: list[dict[str, Any]] = []
        for uid in all_uids[:10]:
            u = user_cache.get(uid, {})
            fn = str(_pick(u, ("firstName", "first_name")) or "").strip() if u else ""
            ln = str(_pick(u, ("lastName", "last_name", "surname")) or "").strip() if u else ""
            user_resolve_sample.append({
                "userId": uid,
                "rawKeys": list(u.keys()) if u else [],
                "fullName": _v3991_user_name_from_user_obj(u) if u else "",
                "firstName": fn,
                "lastName": ln,
            })
        user_resolve: dict[str, Any] = {
            "uniqueUserIds": len(all_uids),
            "resolvedCount": resolved_count,
            "unresolvedCount": len(all_uids) - resolved_count,
            "sample": user_resolve_sample,
            "errors": user_resolve_errors[:10],
        }

        def _enriched_user_sample(raw_recs: list[dict[str, Any]]) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for rec in raw_recs[:5]:
                uid = str(rec.get("userId") or rec.get("user_id") or rec.get("studentId") or rec.get("clientId") or "").strip()
                resolved = user_cache.get(uid, {})
                full_name = _v3991_user_name_from_user_obj(resolved) if resolved else ""
                first = str(_pick(resolved, ("firstName", "first_name")) or "").strip() if resolved else ""
                last = str(_pick(resolved, ("lastName", "last_name", "surname")) or "").strip() if resolved else ""
                out.append({
                    "userId": uid,
                    "recordId": str(rec.get("id") or "").strip(),
                    "rawKeys": list(rec.keys()),
                    "resolvedFullName": full_name,
                    "firstName": first,
                    "lastName": last,
                    "userRawKeys": list(resolved.keys()) if resolved else [],
                    "resolveError": "" if uid in user_cache else ("userId отсутствует" if not uid else "не удалось получить"),
                })
            return out

        # ---- phase 4: build lessons_info + optional save ----
        errors: list[str] = []
        lessons_info: list[dict[str, Any]] = []
        saved_count = 0
        skipped_count = 0
        seen_student_ids_saved: set[str] = set()
        warned_duplicate_ids: set[str] = set()

        for lesson, matched_by, records_result, raw_records, users_endpoint_error in prefetched:
            lesson_id = str(_pick(lesson, ("id", "lessonId")) or "").strip()
            lesson_name = _lesson_title_value(lesson)
            lesson_date = _lesson_date_value(lesson)
            lesson_time = _lesson_time_value(lesson)
            lesson_group = _lesson_group_value(lesson)
            lesson_room = _lesson_room_value(lesson)
            class_id = str(_pick(lesson, ("classId", "groupId")) or "").strip()

            lessons_info.append({
                "lessonId": lesson_id,
                "lessonName": lesson_name,
                "date": lesson_date,
                "time": lesson_time,
                "groupName": lesson_group,
                "classroom": lesson_room,
                "classId": class_id,
                "rawKeys": list(lesson.keys()),
                "matchedBy": matched_by,
                "usersCount": len(raw_records),
                "usersEndpointOk": records_result.ok if records_result else False,
                "usersEndpointError": users_endpoint_error,
                "usersSample": _enriched_user_sample(raw_records),
            })

            if save_to_db:
                lesson_ct = _content_text(lesson).lower()
                if primary_filter and primary_filter.lower() not in lesson_ct:
                    skipped_count += len(raw_records)
                    warnings.append(
                        f"Занятие «{lesson_name}» (id={lesson_id}) пропущено при сохранении: "
                        f"основной фильтр «{primary_filter}» отсутствует в контентных полях"
                    )
                    continue
                for rec in raw_records:
                    if not isinstance(rec, dict):
                        continue
                    mk_student_id = str(
                        rec.get("userId") or rec.get("user_id") or rec.get("studentId") or rec.get("clientId") or ""
                    ).strip()
                    if not mk_student_id:
                        skipped_count += 1
                        continue
                    resolved = user_cache.get(mk_student_id, {})
                    full_name = _v3991_user_name_from_user_obj(resolved) if resolved else ""
                    first_name = str(_pick(resolved, ("firstName", "first_name")) or "").strip() if resolved else ""
                    last_name = str(_pick(resolved, ("lastName", "last_name", "surname")) or "").strip() if resolved else ""
                    if not full_name:
                        skipped_count += 1
                        continue
                    if mk_student_id in seen_student_ids_saved:
                        skipped_count += 1
                        if mk_student_id not in warned_duplicate_ids:
                            warned_duplicate_ids.add(mk_student_id)
                            warnings.append(
                                f"Ребёнок userId={mk_student_id} ({full_name}) встречается в нескольких занятиях/группах — первая запись сохранена"
                            )
                        continue
                    seen_student_ids_saved.add(mk_student_id)
                    try:
                        self.storage.upsert_camp_child({
                            "mk_student_id": mk_student_id,
                            "full_name": full_name,
                            "first_name": first_name or None,
                            "last_name": last_name or None,
                            "group_name": lesson_group or None,
                            "mk_class_id": class_id or None,
                            "mk_class_name": lesson_group or None,
                            "camp_lesson_id": lesson_id or None,
                            "camp_lesson_date": lesson_date or None,
                            "classroom": lesson_room or None,
                            "raw_json": {**rec, "_resolvedUser": resolved},
                        })
                        saved_count += 1
                    except Exception as exc:
                        skipped_count += 1
                        errors.append(f"save error ({mk_student_id}): {str(exc)[:200]}")

        if save_to_db and all_uids and resolved_count == 0:
            warnings.append("Найдены записи учеников, но не удалось получить имена по userId. Сохранение пропущено.")

        if fetch_error:
            errors.insert(0, f"fetch error: {fetch_error}")

        # Annotate active week with unique children count from this sync
        active_week = {**active_week, "childrenUniqueCount": len(seen_student_ids_saved)}

        result: dict[str, Any] = {
            "ok": True,
            "filterType": "lesson",
            "filter": primary_filter,
            "activeFilters": active_filters,
            "foodModuleEnabled": food_enabled,
            "savedToDB": save_to_db,
            "dateRange": {"from": d1.isoformat(), "to": d2.isoformat()},
            "campActiveWeekMode": week_mode,
            "campWeeks": camp_weeks,
            "activeCampWeek": active_week,
            "campActiveStartDate": active_week_start,
            "campActiveEndDate": active_week_end,
            "totalLessonsFetched": len(all_lessons),
            "lessonTitleSamples": lesson_title_samples,
            "lessonsFoundCount": len(lessons_info),
            "classesFoundCount": len(lessons_info),
            "lessons": lessons_info,
            "classes": lessons_info,
            "savedCount": saved_count,
            "skippedCount": skipped_count,
            "userResolve": user_resolve,
            "warnings": warnings[:20],
            "errors": errors[:20],
        }
        if direct_data:
            result.update(direct_data)
        return result

    def food_debug_clear_camp_children(self, auth: dict[str, Any]) -> dict[str, Any]:
        denied = self._require_admin(auth)
        if denied:
            return denied
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "Food module disabled (FOOD_MODULE_ENABLED=false)."}
        affected = self.storage.deactivate_all_camp_children()
        return {"ok": True, "affectedCount": affected}

    def food_debug_cleanup_duplicates(self, auth: dict[str, Any]) -> dict[str, Any]:
        denied = self._require_admin(auth)
        if denied:
            return denied
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        result = self.storage.cleanup_duplicate_camp_children()
        return {"ok": True, **result}

    def food_list_camp_children(self, auth: dict[str, Any]) -> dict[str, Any]:
        denied = self._require_admin(auth)
        if denied:
            return denied
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        children = self.storage.list_camp_children_with_links(active_only=True)
        with_code = sum(1 for c in children if c.get("link_code"))
        linked = sum(1 for c in children if c.get("parent_telegram_id"))
        return {"ok": True, "children": children, "count": len(children), "withCode": with_code, "linked": linked}

    def food_generate_codes_all(self, auth: dict[str, Any]) -> dict[str, Any]:
        denied = self._require_admin(auth)
        if denied:
            return denied
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        children = self.storage.list_camp_children_with_links(active_only=True)
        generated: list[dict[str, Any]] = []
        errors: list[str] = []
        for child in children:
            if child.get("link_code"):
                continue
            mk_id = str(child.get("mk_student_id") or "").strip()
            if not mk_id:
                continue
            try:
                code = self.storage.generate_child_link_code(mk_id)
                generated.append({"mk_student_id": mk_id, "full_name": child.get("full_name"), "link_code": code})
            except Exception as exc:
                errors.append(f"{mk_id}: {str(exc)[:200]}")
        return {"ok": True, "generatedCount": len(generated), "generated": generated, "errors": errors[:20]}

    def food_generate_code_for_child(self, auth: dict[str, Any], mk_student_id: str) -> dict[str, Any]:
        denied = self._require_admin(auth)
        if denied:
            return denied
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        mk_student_id = str(mk_student_id or "").strip()
        if not mk_student_id:
            return {"ok": False, "error": "mk_student_id required"}
        child = self.storage.get_camp_child_by_mk_student_id(mk_student_id)
        if not child:
            return {"ok": False, "error": "Ребёнок не найден в базе"}
        try:
            code = self.storage.get_or_create_link_code_for_student(mk_student_id)
            return {"ok": True, "child": child, "link_code": code}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:300]}

    def food_relink_child(self, auth: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_admin(auth)
        if denied:
            return denied
        mk_student_id = str(payload.get("mk_student_id") or "").strip()
        if not mk_student_id:
            return {"ok": False, "error": "mk_student_id обязателен"}
        child = self.storage.get_camp_child_by_mk_student_id(mk_student_id)
        if not child:
            return {"ok": False, "error": "Ребёнок не найден"}
        result = self.storage.relink_child(mk_student_id)
        if not result.get("ok"):
            return result
        child_name = child.get("full_name") or child.get("first_name") or mk_student_id
        old_parent = result.get("old_parent_telegram_id")
        log.info("food_relink_child mk_student_id=%s old_parent=%r new_code=%r by=%s",
                 mk_student_id, old_parent, result.get("new_code"), auth.get("user_id"))
        return {
            "ok": True,
            "child_name": child_name,
            "mk_student_id": mk_student_id,
            "new_code": result["new_code"],
            "old_parent_telegram_id": old_parent,
        }

    def food_link_child(self, auth: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        user_id = int(auth["user_id"])
        parent_telegram_id = str(user_id)
        code = str(payload.get("code") or "").strip().upper()
        if not code:
            return {"ok": False, "error": "invalid_code", "message": "Введите код ребёнка"}
        result = self.storage.link_parent_to_child(parent_telegram_id, code)
        if not result.get("ok"):
            error = result.get("error", "")
            if "не найден" in error or "использован" in error:
                return {"ok": False, "error": "invalid_code", "message": "Код не найден. Проверьте правильность или обратитесь к администратору."}
            if "другому" in error:
                return {"ok": False, "error": "code_already_used", "message": "Этот код уже использован другим пользователем."}
            return {"ok": False, "error": error, "message": error}
        mk_student_id = str(result.get("mk_student_id") or "").strip()
        child = self.storage.get_camp_child_by_mk_student_id(mk_student_id) if mk_student_id else {}
        return {
            "ok": True,
            "already_linked": bool(result.get("already_linked")),
            "child": {
                "mk_student_id": child.get("mk_student_id") or mk_student_id,
                "full_name": child.get("full_name", ""),
                "group_name": child.get("group_name") or child.get("mk_class_name", ""),
                "classroom": child.get("classroom", ""),
            },
        }

    def food_my_children(self, auth: dict[str, Any]) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        user_id = int(auth["user_id"])
        parent_telegram_id = str(user_id)
        children = self.storage.list_children_for_parent(parent_telegram_id)
        return {"ok": True, "children": children}

    # --- Food module: menus (admin) ---

    def food_list_menus(self, auth: dict[str, Any]) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_food_menu_edit(auth)
        if denied:
            return denied
        menus = self.storage.list_food_menus()
        return {"ok": True, "menus": menus, "count": len(menus)}

    def food_get_menu(self, auth: dict[str, Any], menu_id: str) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_food_menu_edit(auth)
        if denied:
            return denied
        try:
            mid = int(menu_id)
        except (ValueError, TypeError):
            return {"ok": False, "error": "Неверный menu_id"}
        menu = self.storage.get_food_menu(mid)
        if not menu:
            return {"ok": False, "error": "Меню не найдено"}
        # Add order_count per item so UI knows which items have existing orders
        order_counts = self.storage.get_food_items_order_counts(mid)
        for item in menu.get("items") or []:
            item["order_count"] = order_counts.get(int(item["id"]), 0)
        return {"ok": True, "menu": menu}

    def food_create_menu(self, auth: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_food_menu_edit(auth)
        if denied:
            return denied
        menu_date = str(payload.get("menu_date") or "").strip()
        if not menu_date:
            return {"ok": False, "error": "menu_date обязателен"}
        user_id = int(auth["user_id"])
        role = self._role_for_user(user_id)
        if role in {"kitchen", "restaurant"}:
            from datetime import date as _date
            try:
                if _date.fromisoformat(menu_date) < _date.today():
                    return {"ok": False, "error": "Нельзя создать меню на прошедшую дату."}
            except ValueError:
                return {"ok": False, "error": "Неверный формат даты"}
        title = str(payload.get("title") or "").strip() or None
        deadline_at = str(payload.get("deadline_at") or "").strip() or None
        location_code = str(payload.get("location_code") or "").strip().upper() or None
        created_by = int(auth["user_id"])
        menu = self.storage.create_food_menu(menu_date, title, deadline_at, created_by, location_code)
        return {"ok": True, "menu": menu}

    def food_update_menu(self, auth: dict[str, Any], menu_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_admin(auth)
        if denied:
            return denied
        try:
            mid = int(menu_id)
        except (ValueError, TypeError):
            return {"ok": False, "error": "Неверный menu_id"}
        data = {k: payload[k] for k in ("menu_date", "title", "deadline_at", "notes") if k in payload}
        menu = self.storage.update_food_menu(mid, data)
        return {"ok": True, "menu": menu}

    def food_publish_menu(self, auth: dict[str, Any], menu_id: str) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_food_menu_edit(auth)
        if denied:
            return denied
        try:
            mid = int(menu_id)
        except (ValueError, TypeError):
            return {"ok": False, "error": "Неверный menu_id"}
        menu = self.storage.set_food_menu_status(mid, "published")
        if not menu:
            return {"ok": False, "error": "Меню не найдено"}
        return {"ok": True, "menu": menu}

    def food_close_menu(self, auth: dict[str, Any], menu_id: str) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_admin(auth)
        if denied:
            return denied
        try:
            mid = int(menu_id)
        except (ValueError, TypeError):
            return {"ok": False, "error": "Неверный menu_id"}
        menu = self.storage.set_food_menu_status(mid, "closed")
        if not menu:
            return {"ok": False, "error": "Меню не найдено"}
        return {"ok": True, "menu": menu}

    def food_delete_menu(self, auth: dict[str, Any], menu_id: str) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        user_id = int(auth["user_id"])
        role = self._role_for_user(user_id)
        if role not in FOOD_MENU_DELETE_ROLES:
            return {"ok": False, "error": "Нет прав для удаления меню."}
        try:
            mid = int(menu_id)
        except (ValueError, TypeError):
            return {"ok": False, "error": "Неверный menu_id"}
        menu = self.storage.get_food_menu(mid)
        if not menu:
            return {"ok": False, "error": "Меню не найдено или уже удалено."}
        if menu.get("status") == "deleted":
            return {"ok": False, "error": "Меню уже удалено."}
        counts = self.storage.count_food_menu_orders(mid)
        if counts["total"] > 0:
            log.warning(
                "food_menu_delete_blocked user_id=%s role=%s menu_id=%s menu_date=%s child=%s staff=%s",
                user_id, role, mid, menu.get("menu_date"), counts["child"], counts["staff"],
            )
            return {
                "ok": False,
                "error": "has_orders",
                "message": (
                    f"Нельзя удалить меню: по нему уже есть заказы — "
                    f"детей: {counts['child']}, сотрудников: {counts['staff']}. "
                    "Удалите заказы вручную или обратитесь к администратору."
                ),
                "child_orders": counts["child"],
                "staff_orders": counts["staff"],
            }
        status_before = menu.get("status", "draft")
        items_count = len(menu.get("items") or [])
        self.storage.soft_delete_food_menu(mid, user_id)
        log.info(
            "food_menu_deleted admin_user_id=%s role=%s menu_id=%s menu_date=%s status_before=%s items=%s deleted_mode=soft",
            user_id, role, mid, menu.get("menu_date"), status_before, items_count,
        )
        return {
            "ok": True,
            "deleted_menu_id": mid,
            "menu_date": menu.get("menu_date"),
            "deleted_mode": "soft",
        }

    def food_update_deadline(self, auth: dict[str, Any], menu_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_admin(auth)
        if denied:
            return denied
        try:
            mid = int(menu_id)
        except (ValueError, TypeError):
            return {"ok": False, "error": "Неверный menu_id"}
        deadline_at = str(payload.get("deadline_at") or "").strip()
        if not deadline_at:
            dl_date = str(payload.get("deadline_date") or "").strip()
            dl_time = str(payload.get("deadline_time") or "").strip()
            if dl_date and dl_time:
                deadline_at = f"{dl_date}T{dl_time}" + (":00" if len(dl_time) == 5 else "")
            elif dl_date:
                deadline_at = f"{dl_date}T23:59:00"
        if not deadline_at:
            return {"ok": False, "error": "Дедлайн не указан"}
        from datetime import datetime as _dt
        try:
            dl = _dt.fromisoformat(deadline_at.replace("Z", ""))
            if dl <= _dt.now():
                return {"ok": False, "error": "Дедлайн должен быть в будущем"}
        except (ValueError, TypeError):
            return {"ok": False, "error": "Неверный формат дедлайна"}
        old_menu = self.storage.get_food_menu(mid)
        if not old_menu:
            return {"ok": False, "error": "Меню не найдено"}
        old_deadline = old_menu.get("deadline_at") or ""
        changed_by = int(auth["user_id"])
        menu = self.storage.update_food_menu(mid, {"deadline_at": deadline_at})
        if not menu:
            return {"ok": False, "error": "Меню не найдено"}
        log.info("food_deadline_updated menu_id=%s old=%r new=%r by=%s", mid, old_deadline, deadline_at, changed_by)
        return {"ok": True, "menu": menu}

    def _log_menu_item_audit_if_published(
        self, actor_user_id: int, role: str, item: dict, action: str,
        old_data: Any = None, new_data: Any = None
    ) -> None:
        """Write audit log when a menu item is changed on a published menu."""
        try:
            menu_id = int(item.get("menu_id") or 0)
            if not menu_id:
                return
            menu = self.storage.get_food_menu(menu_id)
            if not menu or menu.get("status") not in ("published", "closed"):
                return
            self.storage.log_food_order_audit(
                menu_id=menu_id,
                order_id=int(item.get("id") or 0),
                order_type="menu_item",
                action=action,
                actor_user_id=actor_user_id,
                actor_role=role,
                old_data=old_data,
                new_data=new_data,
                comment=f"item_name={item.get('name', '')}",
            )
        except Exception:
            log.exception("menu_item_audit failed (non-fatal)")

    def food_add_item(self, auth: dict[str, Any], menu_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_food_menu_edit(auth)
        if denied:
            return denied
        try:
            mid = int(menu_id)
        except (ValueError, TypeError):
            return {"ok": False, "error": "Неверный menu_id"}
        name = str(payload.get("name") or "").strip()
        if not name:
            return {"ok": False, "error": "name обязателен"}
        category = str(payload.get("category") or "Другое").strip()
        weight = str(payload.get("weight") or "").strip() or None
        try:
            price = float(payload.get("price") or 0)
        except (ValueError, TypeError):
            price = 0.0
        try:
            sort_order = int(payload.get("sort_order") or 0)
        except (ValueError, TypeError):
            sort_order = 0
        item = self.storage.add_food_item(mid, category, name, weight, price, sort_order)
        user_id = int(auth["user_id"])
        role = self._role_for_user(user_id)
        self._log_menu_item_audit_if_published(user_id, role, item, "menu_item_create_after_publish",
                                               old_data=None, new_data=item)
        return {"ok": True, "item": item}

    def food_update_item(self, auth: dict[str, Any], item_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_food_menu_edit(auth)
        if denied:
            return denied
        try:
            iid = int(item_id)
        except (ValueError, TypeError):
            return {"ok": False, "error": "Неверный item_id"}
        old_item = self.storage.update_food_item(iid, {})  # fetch current state
        data = {k: payload[k] for k in ("category", "name", "weight", "price", "sort_order", "is_available", "description") if k in payload}
        item = self.storage.update_food_item(iid, data)
        if not item:
            return {"ok": False, "error": "Блюдо не найдено"}
        user_id = int(auth["user_id"])
        role = self._role_for_user(user_id)
        self._log_menu_item_audit_if_published(user_id, role, item, "menu_item_update_after_publish",
                                               old_data=old_item, new_data=item)
        return {"ok": True, "item": item}

    def food_hide_item(self, auth: dict[str, Any], item_id: str) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_food_menu_edit(auth)
        if denied:
            return denied
        try:
            iid = int(item_id)
        except (ValueError, TypeError):
            return {"ok": False, "error": "Неверный item_id"}
        item = self.storage.set_food_item_available(iid, False)
        if not item:
            return {"ok": False, "error": "Блюдо не найдено"}
        user_id = int(auth["user_id"])
        role = self._role_for_user(user_id)
        self._log_menu_item_audit_if_published(user_id, role, item, "menu_item_hide_after_publish",
                                               old_data={"is_available": True}, new_data={"is_available": False})
        return {"ok": True, "item": item}

    def food_restore_item(self, auth: dict[str, Any], item_id: str) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_food_menu_edit(auth)
        if denied:
            return denied
        try:
            iid = int(item_id)
        except (ValueError, TypeError):
            return {"ok": False, "error": "Неверный item_id"}
        item = self.storage.set_food_item_available(iid, True)
        if not item:
            return {"ok": False, "error": "Блюдо не найдено"}
        user_id = int(auth["user_id"])
        role = self._role_for_user(user_id)
        self._log_menu_item_audit_if_published(user_id, role, item, "menu_item_show_after_publish",
                                               old_data={"is_available": False}, new_data={"is_available": True})
        return {"ok": True, "item": item}

    # --- Food module: parent orders ---

    def food_my_orders(self, auth: dict[str, Any]) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        user_id = int(auth["user_id"])
        orders = self.storage.list_food_orders_for_parent(str(user_id))
        return {"ok": True, "orders": orders}

    def _check_order_preconditions(
        self, auth: dict[str, Any], payload: dict[str, Any]
    ) -> tuple[str | None, int | None, str | None, dict | None]:
        """Returns (error_str, menu_id, mk_student_id, menu) or (None, ...) on success."""
        user_id = int(auth["user_id"])
        parent_telegram_id = str(user_id)
        try:
            menu_id = int(payload.get("menu_id") or 0)
        except (ValueError, TypeError):
            return "Неверный menu_id", None, None, None
        mk_student_id = str(payload.get("mk_student_id") or "").strip()
        if not menu_id:
            return "menu_id обязателен", None, None, None
        if not mk_student_id:
            return "mk_student_id обязателен", None, None, None
        children = self.storage.list_children_for_parent(parent_telegram_id)
        if not any(c["mk_student_id"] == mk_student_id for c in children):
            return "forbidden_child", None, None, None
        menu = self.storage.get_food_menu(menu_id)
        if not menu or menu.get("status") != "published":
            return "menu_not_available", None, None, None
        deadline_at = menu.get("deadline_at")
        if deadline_at:
            try:
                dl = datetime.fromisoformat(deadline_at)
                if dl.tzinfo is None:
                    from datetime import timezone
                    dl = dl.replace(tzinfo=timezone.utc)
                from datetime import timezone
                if datetime.now(timezone.utc) > dl:
                    return "deadline_passed", None, None, None
            except Exception:
                pass
        return None, menu_id, mk_student_id, menu

    def food_submit_order(self, auth: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        err, menu_id, mk_student_id, menu = self._check_order_preconditions(auth, payload)
        if err:
            return {"ok": False, "error": err}
        user_id = int(auth["user_id"])
        available_items = [it for it in menu.get("items", []) if it.get("is_available")]
        menu_item_ids = {it["id"] for it in available_items}
        # Accept {items: [{id, quantity}]} or legacy {item_ids: [...]}
        item_quantities: dict[int, int] = {}
        raw_items = payload.get("items")
        if isinstance(raw_items, list):
            for entry in raw_items:
                try:
                    iid = int(entry["id"])
                    qty = min(99, max(1, int(entry.get("quantity", 1) or 1)))
                    if iid in menu_item_ids and qty > 0:
                        item_quantities[iid] = qty
                except (KeyError, TypeError, ValueError):
                    pass
        else:
            # Legacy: item_ids list
            for iid in (payload.get("item_ids") or []):
                try:
                    i = int(iid)
                    if i in menu_item_ids:
                        item_quantities[i] = 1
                except (TypeError, ValueError):
                    pass
        order = self.storage.upsert_food_order(str(user_id), mk_student_id, menu_id, item_quantities, "submitted")
        return {"ok": True, "order": order}

    def food_skip_order(self, auth: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        err, menu_id, mk_student_id, menu = self._check_order_preconditions(auth, payload)
        if err:
            return {"ok": False, "error": err}
        user_id = int(auth["user_id"])
        order = self.storage.upsert_food_order(str(user_id), mk_student_id, menu_id, {}, "skipped")
        return {"ok": True, "order": order}

    # --- Food module: admin summary ---

    def food_menu_summary(self, auth: dict[str, Any], menu_id: str) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_admin(auth)
        if denied:
            return denied
        try:
            mid = int(menu_id)
        except (ValueError, TypeError):
            return {"ok": False, "error": "Неверный menu_id"}
        summary = self.storage.get_food_menu_summary(mid)
        if not summary:
            return {"ok": False, "error": "Меню не найдено"}
        location_map: dict[str, str] = {
            "YC1": getattr(self.settings, "food_location_yc1", "") or "Кульман 1/1",
            "YC2": getattr(self.settings, "food_location_yc2", "") or "Мстиславца 6",
            "YC3": getattr(self.settings, "food_location_yc3", "") or "Адрес не указан",
            "unknown": "Адрес не определён",
        }
        groups: dict[str, list[Any]] = {}
        code_order = {"YC1": 0, "YC2": 1, "YC3": 2, "YC4": 3, "YC5": 4, "unknown": 99}
        for ch in summary.get("byChildren", []):
            code = ch.get("groupCode", "unknown")
            groups.setdefault(code, []).append(ch)
        by_locations = []
        menu_loc_code = str((summary.get("menu") or {}).get("location_code") or "").strip().upper()
        full_by_staff = summary.get("byStaff", [])
        # Ensure staff-only locations appear even if no children in that location
        for s in full_by_staff:
            staff_loc = (s.get("locationCode") or "").upper()
            if staff_loc and staff_loc not in groups:
                groups[staff_loc] = []
        # Build a meta map from global byItems (includes staff items) for fallback lookup
        global_item_meta: dict[int, dict[str, Any]] = {
            int(bi["item_id"]): bi for bi in summary.get("byItems", [])
        }
        sorted_codes = sorted(groups.keys(), key=lambda c: code_order.get(c, 50))
        for idx, code in enumerate(sorted_codes):
            ch_list = groups[code]
            loc = location_map.get(code, f"Адрес не определён ({code})")
            loc_submitted = sum(1 for c in ch_list if c["status"] == "submitted")
            loc_skipped = sum(1 for c in ch_list if c["status"] == "skipped")
            loc_missing = sum(1 for c in ch_list if c["status"] == "missing")
            # Determine this location's staff orders; use staff's own locationCode if set
            loc_staff = []
            for s in full_by_staff:
                staff_loc = (s.get("locationCode") or "").upper()
                if staff_loc:
                    if staff_loc == code:
                        loc_staff.append(s)
                elif menu_loc_code:
                    if code == menu_loc_code:
                        loc_staff.append(s)
                elif idx == 0:
                    loc_staff.append(s)
            loc_item_counts: dict[int, int] = {}
            loc_item_children: dict[int, list[str]] = {}
            loc_item_meta: dict[int, dict[str, Any]] = {}
            # Count children's items
            for ch in ch_list:
                if ch["status"] == "submitted":
                    for it in ch.get("itemDetails", []):
                        iid = int(it["item_id"])
                        qty = int(it.get("quantity", 1) or 1)
                        loc_item_counts[iid] = loc_item_counts.get(iid, 0) + qty
                        if ch["childName"] not in loc_item_children.get(iid, []):
                            loc_item_children.setdefault(iid, []).append(ch["childName"])
                        loc_item_meta[iid] = it
            # Add staff items to the same per-location counts
            for s in loc_staff:
                if s.get("status") == "submitted":
                    for it in s.get("itemDetails", []):
                        iid = int(it["item_id"])
                        qty = int(it.get("quantity", 1) or 1)
                        loc_item_counts[iid] = loc_item_counts.get(iid, 0) + qty
                        if iid not in loc_item_meta:
                            loc_item_meta[iid] = global_item_meta.get(iid, it)
            loc_by_items = [
                {"item_id": iid, "category": meta.get("category"), "name": meta.get("name"), "weight": meta.get("weight"), "count": loc_item_counts[iid], "children": loc_item_children.get(iid, [])}
                for iid, meta in loc_item_meta.items()
            ]
            # Utensils: 1 set per person with a submitted food order
            loc_utensils = loc_submitted + sum(1 for s in loc_staff if s.get("status") == "submitted")
            by_locations.append({
                "groupCode": code,
                "location": loc,
                "totalChildren": len(ch_list),
                "submittedOrders": loc_submitted,
                "skippedOrders": loc_skipped,
                "missingOrders": loc_missing,
                "byItems": loc_by_items,
                "byChildren": ch_list,
                "byStaff": loc_staff,
                "missingChildren": [c["childName"] for c in ch_list if c["status"] == "missing"],
                "utensils": loc_utensils,
            })
        total_utensils = sum(loc.get("utensils", 0) for loc in by_locations)
        return {"ok": True, **summary, "byLocations": by_locations, "totalUtensils": total_utensils}

    # --- Food module: OCR preview ---

    def food_ocr_preview(self, auth: dict[str, Any], menu_id: str, files: dict[str, Any]) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_food_menu_edit(auth)
        if denied:
            return denied
        if not getattr(self.settings, "food_menu_ocr_enabled", False):
            return {"ok": False, "error": "food_menu_ocr_disabled", "message": "Распознавание по фото отключено на сервере."}
        img_file = files.get("image")
        if not img_file:
            return {"ok": False, "error": "no_image", "message": "Файл изображения не передан."}
        content = img_file.get("content", b"")
        if len(content) > 5 * 1024 * 1024:
            return {"ok": False, "error": "file_too_large", "message": "Файл слишком большой (максимум 5 МБ)."}
        ctype = (img_file.get("content_type") or "").lower()
        if not ctype.startswith("image/"):
            return {"ok": False, "error": "invalid_mime", "message": "Поддерживаются только изображения."}
        import pathlib
        import tempfile
        ocr_lang = str(getattr(self.settings, "food_menu_ocr_lang", "rus+eng") or "rus+eng")
        ocr_psm = int(getattr(self.settings, "food_menu_ocr_psm", 6))
        suffix = pathlib.Path(img_file.get("filename", "img.jpg")).suffix or ".jpg"
        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            result = ocr_image_to_text(tmp_path, lang=ocr_lang, psm=ocr_psm)
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass
        if not result.get("ok"):
            err = result.get("error", "ocr_error")
            if err == "ocr_dependency_missing":
                msg = "OCR библиотеки не установлены (pytesseract, Pillow)."
            elif err == "tesseract_not_installed":
                msg = "Tesseract не установлен на сервере."
            elif err == "ocr_language_missing":
                msg = result.get("message", "Язык OCR не установлен.")
            else:
                msg = f"Ошибка OCR: {err}"
            return {
                "ok": False,
                "error": err,
                "message": msg,
                "availableLanguages": result.get("availableLanguages", []),
            }
        return {
            "ok": True,
            "rawText": result.get("text", ""),
            "warnings": result.get("warnings", []),
            "availableLanguages": result.get("availableLanguages", []),
        }

    # --- Food module: active menus (parent) ---

    def food_active_menus(self, auth: dict[str, Any]) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        user_id = int(auth["user_id"])
        children = self.storage.list_children_for_parent(str(user_id))
        if not children:
            return {"ok": True, "childrenRequired": True, "children": [], "menus": []}
        menus = self.storage.list_published_food_menus_with_items()
        for menu in menus:
            for cat_items in (menu.get("itemsByCategory") or {}).values():
                for item in cat_items:
                    item.pop("price", None)
        return {"ok": True, "childrenRequired": False, "children": children, "menus": menus}

    def food_staff_active_menus(self, auth: dict[str, Any]) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        if not self._is_staff_food_role(auth):
            return {"ok": False, "error": "forbidden"}
        user_id = int(auth["user_id"])
        role = self._role_for_user(user_id)
        try:
            import zoneinfo
            minsk_now = datetime.now(zoneinfo.ZoneInfo("Europe/Minsk"))
        except Exception:
            minsk_now = datetime.utcnow()
        tomorrow_str = (minsk_now.date() + timedelta(days=1)).isoformat()
        today_str = minsk_now.date().isoformat()
        has_tomorrow_lesson = True
        teacher_not_linked = False
        teacher_location_codes: list[str] = []
        lesson_contexts: list[dict[str, Any]] = []
        mk_teacher_id_resolved = ""
        mk_resolve_method = ""
        teacher_display_name = ""
        location_map: dict[str, str] = {
            "YC1": getattr(self.settings, "food_location_yc1", "") or "Кульман 1/1",
            "YC2": getattr(self.settings, "food_location_yc2", "") or "Мстиславца 6",
            "YC3": getattr(self.settings, "food_location_yc3", "") or "Адрес не указан",
        }
        staff = self.storage.get_staff_user(user_id)
        staff_full_name = str(staff.get("full_name") or "") if staff else ""
        mk_teacher_id_resolved, mk_resolve_method = self._resolve_teacher_mk_id(user_id)
        has_online_lessons = False
        if mk_teacher_id_resolved:
            # Get per-lesson contexts for the menu date — ground truth for location
            lesson_contexts = self.storage.get_teacher_lesson_contexts(mk_teacher_id_resolved, tomorrow_str)
            # Split into food-eligible (offline) and online-only contexts
            food_eligible = [c for c in lesson_contexts if c.get("is_food_eligible")]
            online_contexts = [c for c in lesson_contexts if c.get("is_online")]
            has_online_lessons = bool(online_contexts)
            if food_eligible:
                has_tomorrow_lesson = True
                # Unique food-eligible location codes in lesson order
                seen_locs: list[str] = []
                for ctx in food_eligible:
                    lc = ctx.get("location_code") or ""
                    if lc and lc not in seen_locs:
                        seen_locs.append(lc)
                teacher_location_codes = seen_locs
            elif lesson_contexts:
                # Lessons exist but all online — no food access
                has_tomorrow_lesson = False
            else:
                has_tomorrow_lesson = self.storage.teacher_has_lesson_on_date(mk_teacher_id_resolved, tomorrow_str)
            # Add location_name to each context
            for ctx in lesson_contexts:
                lc = ctx.get("location_code") or ""
                if ctx.get("is_online"):
                    ctx["location_name"] = "Онлайн"
                else:
                    ctx["location_name"] = location_map.get(lc, lc)
            teacher_display_name = (
                str(staff.get("mk_teacher_name") or "") if staff else ""
            ) or staff_full_name
            log.info(
                "[teacher-lunch-location] user=%s role=%s mk=%s method=%s date=%s "
                "lesson_contexts=%s food_eligible=%s online=%s locs=%s",
                user_id, role, mk_teacher_id_resolved, mk_resolve_method,
                tomorrow_str, len(lesson_contexts), len(food_eligible), len(online_contexts), teacher_location_codes,
            )
            for ctx in lesson_contexts:
                log.info(
                    "[teacher-lunch-location] lesson_id=%s group=%r filial=%r loc=%s is_online=%s is_food_eligible=%s src=%s",
                    ctx.get("lesson_id"), ctx.get("group_name"), ctx.get("raw_filial_name"),
                    ctx.get("location_code"), ctx.get("is_online"), ctx.get("is_food_eligible"),
                    ctx.get("location_source"),
                )
        elif role in {"teacher", "intern"}:
            has_tomorrow_lesson = False
            teacher_not_linked = True
            log.warning(
                "[teacher-lunch-location] user=%s role=%s name=%r — no mk_teacher_id; use /mk_link_teacher",
                user_id, role, staff_full_name,
            )
        else:
            log.info(
                "[teacher-lunch-location] user=%s role=%s name=%r — no teacherId; staff fallback",
                user_id, role, staff_full_name,
            )
        menus = self.storage.list_published_food_menus_with_items()
        tomorrow_menus = [m for m in menus if (m.get("menu_date") or "") == tomorrow_str]
        if teacher_location_codes:
            located = [m for m in tomorrow_menus if (m.get("location_code") or "").upper() in teacher_location_codes]
            if located:
                tomorrow_menus = located
        for menu in tomorrow_menus:
            for cat_items in (menu.get("itemsByCategory") or {}).values():
                for item in cat_items:
                    item.pop("price", None)
        requires_location_choice = len(teacher_location_codes) > 1
        resolved_location_code = teacher_location_codes[0] if len(teacher_location_codes) == 1 else ""
        return {
            "ok": True,
            "hasTomorrowLesson": has_tomorrow_lesson,
            "hasOnlineLessons": has_online_lessons,
            "teacherNotLinked": teacher_not_linked,
            "tomorrowDate": tomorrow_str,
            "todayDate": today_str,
            "teacherLocationCodes": teacher_location_codes,
            "lessonContexts": lesson_contexts,
            "requiresLocationChoice": requires_location_choice,
            "resolvedLocationCode": resolved_location_code,
            "mkResolveMethod": mk_resolve_method,
            "teacherDisplayName": teacher_display_name,
            "isTeacherBranch": bool(mk_teacher_id_resolved),
            "menus": tomorrow_menus,
        }

    def food_staff_tomorrow_teachers(self, auth: dict[str, Any]) -> dict[str, Any]:
        """Admin diagnostic: list teachers with lessons tomorrow."""
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_admin(auth)
        if denied:
            return denied
        try:
            import zoneinfo
            minsk_now = datetime.now(zoneinfo.ZoneInfo("Europe/Minsk"))
        except Exception:
            minsk_now = datetime.utcnow()
        tomorrow_str = (minsk_now.date() + timedelta(days=1)).isoformat()
        from storage import normalize_food_location as _nfl
        rows = self.storage.list_teachers_with_lesson_on_date(tomorrow_str)
        teachers = []
        for t in rows:
            mk_id = str(t.get("mk_teacher_id") or "").strip()
            has_user = bool(t.get("user_id"))
            group_names_str = str(t.get("group_names") or "")
            loc_codes = list({_nfl(g) for g in group_names_str.split(",") if _nfl(g)})
            teachers.append({
                "mkTeacherId": mk_id,
                "teacherName": t.get("teacher_name") or t.get("full_name") or mk_id,
                "userId": t.get("user_id"),
                "username": t.get("username"),
                "groupNames": group_names_str,
                "locationCodes": loc_codes,
                "hasStaffUser": has_user,
                "status": "access_ok" if has_user else "no_telegram_link",
            })
        return {"ok": True, "tomorrowDate": tomorrow_str, "teachers": teachers}

    def food_teacher_class_orders(self, auth: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        """Teacher endpoint: show children's orders for the teacher's class/location."""
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        user_id = int(auth["user_id"])
        role = self._role_for_user(user_id)
        allowed_roles = {"teacher", "methodist", "intern", "owner", "admin", "operations"}
        if role not in allowed_roles:
            return {"ok": False, "error": "forbidden"}
        mk_teacher_id, _ = self._resolve_teacher_mk_id(user_id)
        try:
            import zoneinfo
            minsk_now = datetime.now(zoneinfo.ZoneInfo("Europe/Minsk"))
        except Exception:
            minsk_now = datetime.utcnow()
        tomorrow_str = (minsk_now.date() + timedelta(days=1)).isoformat()
        target_date = str(params.get("date") or "").strip() or tomorrow_str
        teacher_location_codes: list[str] = []
        if mk_teacher_id:
            teacher_location_codes = self.storage.get_teacher_lesson_locations(mk_teacher_id, target_date)
        location_filter = str(params.get("location_code") or "").strip().upper() or None
        if location_filter:
            if teacher_location_codes and location_filter not in teacher_location_codes:
                if role not in {"owner", "admin", "operations"}:
                    return {"ok": False, "error": "Этот филиал не в вашем расписании на эту дату."}
            teacher_location_codes = [location_filter]
        elif not teacher_location_codes and role not in {"owner", "admin", "operations"}:
            return {
                "ok": False,
                "error": "no_lesson",
                "message": "Нет занятий в этот день. Заказы детей недоступны.",
            }
        location_map: dict[str, str] = {
            "YC1": getattr(self.settings, "food_location_yc1", "") or "Кульман 1/1",
            "YC2": getattr(self.settings, "food_location_yc2", "") or "Мстиславца 6",
            "YC3": getattr(self.settings, "food_location_yc3", "") or "Адрес не указан",
            "unknown": "Адрес не определён",
        }
        menu_id_param = params.get("menu_id")
        if menu_id_param:
            try:
                menu_obj = self.storage.get_food_menu(int(menu_id_param))
                menus: list[dict] = [menu_obj] if menu_obj else []
            except (ValueError, TypeError):
                return {"ok": False, "error": "Неверный menu_id"}
        else:
            all_menus = self.storage.list_food_menus()
            menus = [
                m for m in all_menus
                if m.get("menu_date") == target_date
                and m.get("status") not in ("draft", "deleted", "archived")
                and not m.get("deleted_at")
            ]
        locations_out: list[dict] = []
        seen_keys: set[str] = set()
        for menu in menus:
            summary = self.storage.get_food_menu_summary(menu["id"])
            if not summary:
                continue
            by_children = summary.get("byChildren", [])
            groups: dict[str, list] = {}
            for ch in by_children:
                code = ch.get("groupCode", "unknown")
                groups.setdefault(code, []).append(ch)
            target_codes = teacher_location_codes if teacher_location_codes else sorted(groups.keys())
            for code in target_codes:
                key = f"{code}_{menu['id']}"
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                children_list = groups.get(code, [])
                loc_name = location_map.get(code, f"Адрес ({code})")
                children_out = []
                for ch in sorted(children_list, key=lambda c: c.get("childName") or ""):
                    status = ch.get("status", "missing")
                    teacher_status = "ordered" if status == "submitted" else ("no_food" if status == "skipped" else "missing")
                    items_out: list[dict] = []
                    if status == "submitted":
                        for it in (ch.get("itemDetails") or []):
                            items_out.append({
                                "name": it.get("name", ""),
                                "quantity": int(it.get("quantity") or 1),
                                "weight": it.get("weight") or "",
                            })
                    children_out.append({
                        "child_name": ch.get("childName", ""),
                        "status": teacher_status,
                        "items": items_out,
                    })
                locations_out.append({
                    "location_code": code,
                    "location_name": loc_name,
                    "menu_id": menu["id"],
                    "menu_date": menu.get("menu_date"),
                    "deadline_at": str(menu.get("deadline_at") or ""),
                    "children": children_out,
                })
        for loc_out in locations_out:
            ordered_n = sum(1 for c in loc_out.get("children", []) if c.get("status") == "ordered")
            missing_n = sum(1 for c in loc_out.get("children", []) if c.get("status") == "missing")
            log.info(
                "[teacher-class-orders] user_id=%s mk=%s loc=%s menu_id=%s menu_date=%s dl=%s children=%s ordered=%s missing=%s",
                user_id, mk_teacher_id or "", loc_out.get("location_code"), loc_out.get("menu_id"),
                loc_out.get("menu_date"), (loc_out.get("deadline_at") or "")[:16],
                len(loc_out.get("children", [])), ordered_n, missing_n,
            )
        if not locations_out:
            log.info(
                "[teacher-class-orders] user_id=%s mk=%s date=%s locs=%s menus_found=%s — no locations in output",
                user_id, mk_teacher_id or "", target_date, teacher_location_codes, len(menus),
            )
        return {
            "ok": True,
            "date": target_date,
            "teacher_location_codes": teacher_location_codes,
            "locations": locations_out,
        }

    def food_notify_published(self, auth: dict[str, Any], menu_id_str: str) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_admin(auth)
        if denied:
            return denied
        try:
            mid = int(menu_id_str)
        except (ValueError, TypeError):
            return {"ok": False, "error": "Неверный menu_id"}
        summary = self.storage.get_food_menu_summary(mid)
        if not summary:
            return {"ok": False, "error": "Меню не найдено"}
        menu = summary.get("menu", {})
        if menu.get("status") not in ("published",):
            return {"ok": False, "error": "Меню не опубликовано."}

        from datetime import datetime, timezone as _tz
        deadline_str = menu.get("deadline_at") or ""
        deadline_display = ""
        if deadline_str:
            try:
                dl = datetime.fromisoformat(deadline_str.replace("Z", "+00:00"))
                if dl.tzinfo is None:
                    dl = dl.replace(tzinfo=_tz.utc)
                if datetime.now(_tz.utc) > dl:
                    return {"ok": False, "error": "deadline_passed", "message": "Дедлайн прошёл. Уведомления не отправляются."}
                deadline_display = dl.strftime("%d.%m в %H:%M")
            except Exception:
                deadline_display = deadline_str

        menu_date = menu.get("menu_date") or ""
        menu_title = menu.get("title") or menu_date
        date_parts = menu_date.split("-")
        date_display = f"{date_parts[2]}.{date_parts[1]}.{date_parts[0]}" if len(date_parts) == 3 else menu_date

        all_children = self.storage.get_all_children_with_parents()
        if not all_children:
            return {"ok": True, "sentCount": 0, "parentsCount": 0, "childrenCount": 0,
                    "noParentCount": 0, "alreadyNotifiedCount": 0, "failedCount": 0,
                    "noParentChildren": [], "failed": [], "message": "Нет детей в смене."}

        by_parent: dict[str, list[dict]] = {}
        no_parent_children: list[dict] = []
        for ch in all_children:
            ptid = ch.get("parent_telegram_id")
            if ptid:
                by_parent.setdefault(str(ptid), []).append(ch)
            else:
                no_parent_children.append({"childName": ch["full_name"], "groupCode": ch["groupCode"]})

        bot_token = getattr(self.settings, "telegram_bot_token", "") or ""
        web_app_url = getattr(self.settings, "web_app_url", "") or ""
        sent_count = 0
        already_count = 0
        failed: list[dict] = []

        for ptid, children in by_parent.items():
            if self.storage.check_food_notification_sent(mid, ptid):
                already_count += 1
                continue
            child_names = [c["full_name"] for c in children]
            if len(child_names) == 1:
                body = (
                    f"Здравствуйте! Меню питания на {date_display} опубликовано.\n\n"
                    f"Ребёнок:\n{child_names[0]}\n\n"
                    f"Меню: {menu_title}"
                )
            else:
                names_block = "\n".join(f"• {n}" for n in child_names)
                body = (
                    f"Здравствуйте! Меню питания на {date_display} опубликовано.\n\n"
                    f"Дети:\n{names_block}\n\n"
                    f"Меню: {menu_title}"
                )
            if deadline_display:
                body += f"\nВыбор доступен до: {deadline_display}"
            body += "\n\nОткройте кабинет Yellow Club и выберите блюда."

            ok, err = _telegram_send_with_webapp(bot_token, int(ptid), body, web_app_url)
            status = "sent" if ok else "failed"
            self.storage.log_food_notification(mid, ptid, child_names, status, err)
            if ok:
                sent_count += 1
            else:
                log.warning("food_notify_published: failed send to %s: %s", ptid, err)
                failed.append({"parentTelegramId": ptid, "error": err})

        return {
            "ok": True,
            "sentCount": sent_count,
            "parentsCount": len(by_parent),
            "childrenCount": len(all_children) - len(no_parent_children),
            "noParentCount": len(no_parent_children),
            "alreadyNotifiedCount": already_count,
            "failedCount": len(failed),
            "noParentChildren": no_parent_children,
            "failed": failed,
        }

    def _send_food_missing_reminders_core(
        self, mid: int, menu: dict[str, Any], triggered_by: str = "manual"
    ) -> dict[str, Any]:
        """Shared logic for manual remind-missing and auto-reminder watcher."""
        from datetime import datetime, timezone as _tz
        deadline_str = menu.get("deadline_at") or ""
        if deadline_str:
            try:
                dl = datetime.fromisoformat(deadline_str.replace("Z", "+00:00"))
                if dl.tzinfo is None:
                    dl = dl.replace(tzinfo=_tz.utc)
                if datetime.now(_tz.utc) > dl:
                    return {"ok": False, "error": "deadline_passed",
                            "message": "Дедлайн прошёл. Напоминания не отправляются."}
            except Exception:
                pass

        missing_children = self.storage.get_missing_children_with_parents(mid)
        if not missing_children:
            return {"ok": True, "sentCount": 0, "parentsCount": 0, "childrenCount": 0,
                    "noParentCount": 0, "alreadyRemindedCount": 0, "failedCount": 0,
                    "noParentChildren": [], "failed": [], "message": "Все дети уже выбрали питание."}

        menu_date = menu.get("menu_date") or ""
        menu_title = menu.get("title") or menu_date
        date_parts = menu_date.split("-")
        date_display = f"{date_parts[2]}.{date_parts[1]}.{date_parts[0]}" if len(date_parts) == 3 else menu_date
        deadline_display = ""
        if deadline_str:
            try:
                dl_local = datetime.fromisoformat(deadline_str.replace("Z", "+00:00"))
                deadline_display = dl_local.strftime("%d.%m в %H:%M")
            except Exception:
                deadline_display = deadline_str

        by_parent: dict[str, list[dict]] = {}
        no_parent_children: list[dict] = []
        for ch in missing_children:
            ptid = ch.get("parent_telegram_id")
            if ptid:
                by_parent.setdefault(str(ptid), []).append(ch)
            else:
                no_parent_children.append({"childName": ch["full_name"], "groupCode": ch["groupCode"]})

        bot_token = getattr(self.settings, "telegram_bot_token", "") or ""
        web_app_url = getattr(self.settings, "web_app_url", "") or ""
        sent_count = 0
        already_count = 0
        failed: list[dict] = []

        for ptid, children in by_parent.items():
            if self.storage.check_food_reminder_cooldown(mid, ptid, hours=2):
                already_count += 1
                continue
            child_names = [c["full_name"] for c in children]
            if len(child_names) == 1:
                body = (
                    f"Здравствуйте! Напоминаем выбрать питание для ребёнка на {date_display}.\n\n"
                    f"Ребёнок:\n{child_names[0]}\n\n"
                    f"Меню: {menu_title}"
                )
            else:
                names_block = "\n".join(f"• {n}" for n in child_names)
                body = (
                    f"Здравствуйте! Напоминаем выбрать питание на {date_display}.\n\n"
                    f"Дети:\n{names_block}\n\n"
                    f"Меню: {menu_title}"
                )
            if deadline_display:
                body += f"\nВыбор доступен до: {deadline_display}"
            body += "\n\nОткройте кабинет Yellow Club и выберите блюда."

            ok, err = _telegram_send_with_webapp(bot_token, int(ptid), body, web_app_url)
            status = "sent" if ok else "failed"
            self.storage.log_food_reminder(mid, ptid, child_names, status, err, triggered_by=triggered_by)
            if ok:
                sent_count += 1
            else:
                log.warning("food_remind [%s]: failed send to %s: %s", triggered_by, ptid, err)
                failed.append({"parentTelegramId": ptid, "error": err})

        return {
            "ok": True,
            "sentCount": sent_count,
            "parentsCount": len(by_parent),
            "childrenCount": len(missing_children) - len(no_parent_children),
            "noParentCount": len(no_parent_children),
            "alreadyRemindedCount": already_count,
            "failedCount": len(failed),
            "noParentChildren": no_parent_children,
            "failed": failed,
        }

    def food_remind_missing(self, auth: dict[str, Any], menu_id_str: str) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_admin(auth)
        if denied:
            return denied
        try:
            mid = int(menu_id_str)
        except (ValueError, TypeError):
            return {"ok": False, "error": "Неверный menu_id"}
        summary = self.storage.get_food_menu_summary(mid)
        if not summary:
            return {"ok": False, "error": "Меню не найдено"}
        menu = summary.get("menu", {})
        if menu.get("status") not in ("published", "closed"):
            return {"ok": False, "error": "Меню не опубликовано — напоминания не отправляются."}
        return self._send_food_missing_reminders_core(mid, menu, triggered_by="manual")

    def food_debug_data_status(self, auth: dict[str, Any]) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_admin(auth)
        if denied:
            return denied
        return {"ok": True, **self.storage.get_food_data_status()}

    def food_auto_reminder_status(self, auth: dict[str, Any]) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_admin(auth)
        if denied:
            return denied
        return {
            "ok": True,
            "enabled": getattr(self.settings, "food_auto_reminders_enabled", False),
            "minutesBeforeDeadline": getattr(self.settings, "food_auto_reminder_minutes_before_deadline", 120),
            "checkIntervalMinutes": getattr(self.settings, "food_auto_reminder_check_interval_minutes", 15),
            "lastRunAt": _FOOD_AUTO_REMINDER_STATUS.get("lastRunAt"),
            "lastResult": _FOOD_AUTO_REMINDER_STATUS.get("lastResult"),
            "runCount": _FOOD_AUTO_REMINDER_STATUS.get("runCount", 0),
        }

    # --- Food module: staff lunch orders ---

    def _is_staff_food_role(self, auth: dict[str, Any]) -> bool:
        role = self._role_for_user(int(auth["user_id"]))
        return role not in ("", "parent", "kitchen", "restaurant") and bool(getattr(self.settings, "food_module_enabled", False))

    def food_staff_my_order(self, auth: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        if not self._is_staff_food_role(auth):
            return {"ok": False, "error": "forbidden"}
        try:
            menu_id = int(params.get("menu_id") or 0)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Неверный menu_id"}
        if not menu_id:
            return {"ok": False, "error": "menu_id обязателен"}
        user_id = int(auth["user_id"])
        order = self.storage.get_food_staff_order(user_id, menu_id)
        return {"ok": True, "order": order}

    def _resolve_staff_order_location(
        self, user_id: int, menu: dict[str, Any], payload: dict[str, Any]
    ) -> str | dict[str, Any]:
        """Determine the location_code for a staff order.

        Priority (teacher branch takes precedence over role/menu):
        1. mk_teacher_id resolution (via link, auto-match by name in tlc + snapshots)
        2. If found → get lesson locations on menu_date from teacher_lesson_control + lesson_snapshots
           - 1 location: use automatically
           - >1 locations: require payload['location_code'] or return error
           - 0 locations: fallback to menu.location_code with warning log
        3. If no teacherId found → fallback to menu.location_code
        4. If menu has no location_code → empty string
        """
        menu_date = str(menu.get("menu_date") or "")
        menu_loc = str(menu.get("location_code") or "").strip().upper()
        staff = self.storage.get_staff_user(user_id)
        role = self._role_for_user(user_id)
        full_name = str(staff.get("full_name") or "") if staff else ""

        mk_teacher_id, mk_method = self._resolve_teacher_mk_id(user_id)
        log.info(
            "food_order_loc_start: user=%s role=%s name=%r mk=%s method=%s menu_date=%s menu_loc=%s",
            user_id, role, full_name, mk_teacher_id or "(none)", mk_method or "not_found",
            menu_date, menu_loc,
        )

        if mk_teacher_id and menu_date:
            loc_codes = self.storage.get_teacher_lesson_locations(mk_teacher_id, menu_date)
            log.info(
                "[teacher-lunch-location] save: user=%s mk=%s date=%s lesson_locs=%s menu_loc=%s payload_loc=%s",
                user_id, mk_teacher_id, menu_date, loc_codes, menu_loc,
                str(payload.get("location_code") or ""),
            )
            if len(loc_codes) == 1:
                log.info(
                    "[teacher-lunch-location] result: user=%s → single_lesson_loc=%s",
                    user_id, loc_codes[0],
                )
                return loc_codes[0]
            if len(loc_codes) > 1:
                # Priority 1: explicit choice from frontend
                preferred = str(payload.get("location_code") or "").strip().upper()
                if preferred and preferred in loc_codes:
                    log.info(
                        "[teacher-lunch-location] result: user=%s → multi_loc payload_chosen=%s",
                        user_id, preferred,
                    )
                    return preferred
                # Priority 2: menu itself is location-specific and matches a teacher lesson
                if menu_loc and menu_loc in loc_codes:
                    log.info(
                        "[teacher-lunch-location] result: user=%s → multi_loc menu_loc=%s",
                        user_id, menu_loc,
                    )
                    return menu_loc
                # Need explicit choice from user
                log.info(
                    "[teacher-lunch-location] result: user=%s → multiple_locations=%s (no choice made)",
                    user_id, loc_codes,
                )
                return {
                    "ok": False,
                    "error": "multiple_locations",
                    "availableLocations": loc_codes,
                    "message": "У вас занятия в нескольких учебных классах на эту дату. Выберите занятие для обеда.",
                }
            # teacherId found but no lesson on this date in DB
            log.warning(
                "[teacher-lunch-location] no_lesson: user=%s mk=%s date=%s "
                "— not in tlc or snapshots; fallback to menu_loc=%s",
                user_id, mk_teacher_id, menu_date, menu_loc,
            )
        elif not mk_teacher_id:
            log.info(
                "[teacher-lunch-location] no_teacher: user=%s role=%s name=%r → menu_loc=%s",
                user_id, role, full_name, menu_loc,
            )

        log.info("[teacher-lunch-location] result: user=%s → staff_fallback loc=%s", user_id, menu_loc)
        return menu_loc

    def food_staff_submit_order(self, auth: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        if not self._is_staff_food_role(auth):
            return {"ok": False, "error": "forbidden"}
        try:
            menu_id = int(payload.get("menu_id") or 0)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Неверный menu_id"}
        if not menu_id:
            return {"ok": False, "error": "menu_id обязателен"}
        menu = self.storage.get_food_menu(menu_id)
        if not menu or menu.get("status") != "published":
            return {"ok": False, "error": "menu_not_available"}
        deadline_at = menu.get("deadline_at")
        if deadline_at:
            try:
                dl = datetime.fromisoformat(deadline_at)
                if dl.tzinfo is None:
                    from datetime import timezone
                    dl = dl.replace(tzinfo=timezone.utc)
                from datetime import timezone
                if datetime.now(timezone.utc) > dl:
                    return {"ok": False, "error": "deadline_passed"}
            except Exception:
                pass
        available_items = [it for it in menu.get("items", []) if it.get("is_available")]
        menu_item_ids = {it["id"] for it in available_items}
        item_quantities: dict[int, int] = {}
        raw_items = payload.get("items")
        if isinstance(raw_items, list):
            for entry in raw_items:
                try:
                    iid = int(entry["id"])
                    qty = min(99, max(1, int(entry.get("quantity", 1) or 1)))
                    if iid in menu_item_ids and qty > 0:
                        item_quantities[iid] = qty
                except (KeyError, TypeError, ValueError):
                    pass
        else:
            for iid in (payload.get("item_ids") or []):
                try:
                    i = int(iid)
                    if i in menu_item_ids:
                        item_quantities[i] = 1
                except (TypeError, ValueError):
                    pass
        user_id = int(auth["user_id"])
        order_location_code = self._resolve_staff_order_location(user_id, menu, payload)
        if isinstance(order_location_code, dict):
            return order_location_code  # error response (multiple_locations)
        order = self.storage.upsert_food_staff_order(user_id, menu_id, item_quantities, "submitted", location_code=order_location_code)
        return {"ok": True, "order": order}

    def food_staff_skip_order(self, auth: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        if not self._is_staff_food_role(auth):
            return {"ok": False, "error": "forbidden"}
        try:
            menu_id = int(payload.get("menu_id") or 0)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Неверный menu_id"}
        if not menu_id:
            return {"ok": False, "error": "menu_id обязателен"}
        menu = self.storage.get_food_menu(menu_id)
        if not menu or menu.get("status") != "published":
            return {"ok": False, "error": "menu_not_available"}
        user_id = int(auth["user_id"])
        order_location_code = self._resolve_staff_order_location(user_id, menu, payload)
        if isinstance(order_location_code, dict):
            return order_location_code  # error response (multiple_locations)
        order = self.storage.upsert_food_staff_order(user_id, menu_id, {}, "skipped", location_code=order_location_code)
        return {"ok": True, "order": order}

    _FOOD_DELETE_ROLES = {"owner", "admin", "operations"}

    def _require_food_delete_access(self, auth: dict[str, Any]) -> dict[str, Any] | None:
        role = self._role_for_user(int(auth["user_id"]))
        if role not in self._FOOD_DELETE_ROLES:
            return {"ok": False, "error": "Удалять заказы может только owner, admin или operations."}
        return None

    def food_delete_child_order(self, auth: dict[str, Any], order_id_str: str) -> dict[str, Any]:
        """Admin: delete a child food order by its order id."""
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_food_delete_access(auth)
        if denied:
            return denied
        try:
            order_id = int(order_id_str or 0)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Неверный order_id"}
        if not order_id:
            return {"ok": False, "error": "order_id обязателен"}
        deleted = self.storage.delete_food_child_order(order_id)
        if not deleted:
            return {"ok": False, "error": "Заказ не найден"}
        admin_uid = int(auth["user_id"])
        admin_role = self._role_for_user(admin_uid)
        log.info(
            "food_order_deleted: admin=%s role=%s order_id=%s type=child menu_id=%s student=%s status=%s",
            admin_uid, admin_role, order_id, deleted.get("menu_id"), deleted.get("mk_student_id"), deleted.get("status"),
        )
        return {
            "ok": True,
            "deleted_order_id": order_id,
            "order_type": "child",
            "menu_id": deleted.get("menu_id"),
            "mk_student_id": deleted.get("mk_student_id"),
        }

    def food_delete_staff_order_by_id(self, auth: dict[str, Any], order_id_str: str) -> dict[str, Any]:
        """Admin: delete a staff/teacher food order by its order id."""
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_food_delete_access(auth)
        if denied:
            return denied
        try:
            order_id = int(order_id_str or 0)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Неверный order_id"}
        if not order_id:
            return {"ok": False, "error": "order_id обязателен"}
        deleted = self.storage.delete_food_staff_order(order_id)
        if not deleted:
            return {"ok": False, "error": "Заказ не найден"}
        admin_uid = int(auth["user_id"])
        admin_role = self._role_for_user(admin_uid)
        mk_name = str(deleted.get("staff_mk_teacher_name") or "").strip()
        full_name = str(deleted.get("staff_name") or "").strip()
        username = str(deleted.get("staff_username") or "").strip()
        display_name = mk_name or full_name or username or f"Сотрудник #{deleted.get('staff_user_id')}"
        log.info(
            "food_order_deleted: admin=%s role=%s order_id=%s type=staff menu_id=%s user=%s name=%r loc=%s status=%s",
            admin_uid, admin_role, order_id, deleted.get("menu_id"), deleted.get("staff_user_id"),
            display_name, deleted.get("location_code"), deleted.get("status"),
        )
        return {
            "ok": True,
            "deleted_order_id": order_id,
            "order_type": "staff",
            "display_name": display_name,
            "menu_id": deleted.get("menu_id"),
            "staff_user_id": deleted.get("staff_user_id"),
        }

    # --- Food module: kitchen / restaurant read-only summary ---

    def _require_kitchen_access(self, auth: dict[str, Any]) -> dict[str, Any] | None:
        user_id = int(auth["user_id"])
        role = self._role_for_user(user_id)
        if role not in KITCHEN_SUMMARY_ROLES:
            return {"ok": False, "error": "Доступ только для кухни и администраторов."}
        return None

    def food_kitchen_menus(self, auth: dict[str, Any]) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_kitchen_access(auth)
        if denied:
            return denied
        menus = self.storage.list_food_menus()
        published = [m for m in menus if m.get("status") == "published"]
        return {"ok": True, "menus": published}

    def food_kitchen_summary(self, auth: dict[str, Any], menu_id: str) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_kitchen_access(auth)
        if denied:
            return denied
        user_id = int(auth["user_id"])
        role = self._role_for_user(user_id)
        show_prices = role in FOOD_PRICE_ROLES
        try:
            mid = int(menu_id)
        except (ValueError, TypeError):
            return {"ok": False, "error": "Неверный menu_id"}
        summary = self.storage.get_food_menu_summary(mid)
        if not summary:
            return {"ok": False, "error": "Меню не найдено"}
        item_prices: dict[int, float] = self.storage.get_food_item_prices(mid) if show_prices else {}
        location_map: dict[str, str] = {
            "YC1": getattr(self.settings, "food_location_yc1", "") or "Кульман 1/1",
            "YC2": getattr(self.settings, "food_location_yc2", "") or "Мстиславца 6",
            "YC3": getattr(self.settings, "food_location_yc3", "") or "Адрес не указан",
            "unknown": "Адрес не определён",
        }
        code_order = {"YC1": 0, "YC2": 1, "YC3": 2, "YC4": 3, "YC5": 4, "unknown": 99}
        groups: dict[str, list[Any]] = {}
        for ch in summary.get("byChildren", []):
            groups.setdefault(ch.get("groupCode", "unknown"), []).append(ch)
        menu_loc_code = str((summary.get("menu") or {}).get("location_code") or "").strip().upper()
        full_by_staff = summary.get("byStaff", [])
        # Ensure staff-only locations appear even if no children in that location
        for s in full_by_staff:
            staff_loc = (s.get("locationCode") or "").upper()
            if staff_loc and staff_loc not in groups:
                groups[staff_loc] = []
        global_item_meta: dict[int, dict[str, Any]] = {int(bi["item_id"]): bi for bi in summary.get("byItems", [])}
        sorted_codes = sorted(groups.keys(), key=lambda c: code_order.get(c, 50))
        by_locations = []
        overall_total = 0.0
        for idx, code in enumerate(sorted_codes):
            ch_list = groups[code]
            loc = location_map.get(code, f"Адрес не определён ({code})")
            # Match staff to this location: use staff's own locationCode if set, else menu/first-block fallback
            loc_staff = []
            for s in full_by_staff:
                staff_loc = (s.get("locationCode") or "").upper()
                if staff_loc:
                    if staff_loc == code:
                        loc_staff.append(s)
                elif menu_loc_code:
                    if code == menu_loc_code:
                        loc_staff.append(s)
                elif idx == 0:
                    loc_staff.append(s)
            loc_item_counts: dict[int, int] = {}
            loc_item_meta: dict[int, dict[str, Any]] = {}
            # Build children entries
            children_orders = []
            for ch in ch_list:
                if ch["status"] == "submitted":
                    child_total = 0.0
                    child_items = []
                    for it in ch.get("itemDetails", []):
                        iid = int(it["item_id"])
                        qty = int(it.get("quantity", 1) or 1)
                        loc_item_counts[iid] = loc_item_counts.get(iid, 0) + qty
                        loc_item_meta[iid] = it
                        ie: dict[str, Any] = {"name": it["name"], "quantity": qty}
                        if show_prices:
                            p = item_prices.get(iid, 0.0)
                            ie["price"] = p
                            ie["total"] = round(p * qty, 2)
                            child_total += p * qty
                        child_items.append(ie)
                    ce: dict[str, Any] = {"name": ch["childName"], "status": "submitted", "items": child_items}
                    if show_prices:
                        ce["total"] = round(child_total, 2)
                    children_orders.append(ce)
                elif ch["status"] == "skipped":
                    children_orders.append({"name": ch["childName"], "status": "skipped", "items": []})
            # Build staff entries
            staff_orders = []
            for s in loc_staff:
                if s.get("status") == "submitted":
                    staff_total = 0.0
                    staff_items = []
                    for it in s.get("itemDetails", []):
                        iid = int(it["item_id"])
                        qty = int(it.get("quantity", 1) or 1)
                        loc_item_counts[iid] = loc_item_counts.get(iid, 0) + qty
                        if iid not in loc_item_meta:
                            loc_item_meta[iid] = global_item_meta.get(iid, it)
                        ie = {"name": it["name"], "quantity": qty}
                        if show_prices:
                            p = item_prices.get(iid, 0.0)
                            ie["price"] = p
                            ie["total"] = round(p * qty, 2)
                            staff_total += p * qty
                        staff_items.append(ie)
                    se: dict[str, Any] = {"name": s["staffName"], "status": "submitted", "items": staff_items}
                    if show_prices:
                        se["total"] = round(staff_total, 2)
                    staff_orders.append(se)
                elif s.get("status") == "skipped":
                    staff_orders.append({"name": s["staffName"], "status": "skipped", "items": []})
            # Build items summary for this location
            loc_by_items = []
            loc_total = 0.0
            for iid, meta in loc_item_meta.items():
                count = loc_item_counts.get(iid, 0)
                ie = {"name": meta.get("name"), "count": count}
                if show_prices:
                    p = item_prices.get(iid, 0.0)
                    ie["price"] = p
                    ie["total"] = round(p * count, 2)
                    loc_total += p * count
                loc_by_items.append(ie)
            if show_prices:
                overall_total += loc_total
            loc_submitted_count = sum(1 for c in ch_list if c["status"] == "submitted")
            loc_staff_submitted = sum(1 for s in loc_staff if s.get("status") == "submitted")
            loc_utensils = loc_submitted_count + loc_staff_submitted
            loc_entry: dict[str, Any] = {
                "groupCode": code,
                "location": loc,
                "submittedOrders": loc_submitted_count,
                "skippedOrders": sum(1 for c in ch_list if c["status"] == "skipped"),
                "missingOrders": sum(1 for c in ch_list if c["status"] == "missing"),
                "byItems": loc_by_items,
                "byChildren": children_orders,
                "byStaff": staff_orders,
                "missingChildren": [c["childName"] for c in ch_list if c["status"] == "missing"],
                "noFoodChildren": [c["childName"] for c in ch_list if c["status"] == "skipped"],
                "utensils": loc_utensils,
            }
            if show_prices:
                loc_entry["locationTotal"] = round(loc_total, 2)
            by_locations.append(loc_entry)
        total_utensils_kitchen = sum(loc.get("utensils", 0) for loc in by_locations)
        result: dict[str, Any] = {
            "ok": True,
            "menu": {
                "id": summary["menu"]["id"],
                "menu_date": summary["menu"].get("menu_date"),
                "title": summary["menu"].get("title"),
                "status": summary["menu"].get("status"),
                "deadline_at": summary["menu"].get("deadline_at"),
                "location_code": summary["menu"].get("location_code"),
            },
            "byLocations": by_locations,
            "showPrices": show_prices,
            "totalUtensils": total_utensils_kitchen,
        }
        if show_prices:
            result["overallTotal"] = round(overall_total, 2)
        return result

    def food_summary_audit(self, auth: dict[str, Any], menu_id: str) -> dict[str, Any]:
        """Read-only audit: recompute totals from raw DB data and run consistency checks."""
        from storage import _get_food_group_info as _gfgi
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_food_menu_edit(auth)
        if denied:
            return denied
        user_id = int(auth["user_id"])
        role = self._role_for_user(user_id)
        try:
            mid = int(menu_id)
        except (ValueError, TypeError):
            return {"ok": False, "error": "Неверный menu_id"}
        raw = self.storage.get_food_menu_audit_raw(mid)
        if not raw:
            return {"ok": False, "error": "Меню не найдено"}
        menu = raw["menu"]
        if menu.get("status") in ("deleted", "archived"):
            return {"ok": False, "error": "Меню удалено или недоступно."}
        child_orders = raw["childOrders"]
        staff_orders = raw["staffOrders"]
        food_items = raw["foodItems"]
        item_price_map: dict[int, float] = {int(fi["id"]): float(fi.get("price") or 0) for fi in food_items}
        item_name_map: dict[int, str] = {int(fi["id"]): fi.get("name") or "" for fi in food_items}
        known_item_ids: set[int] = set(item_price_map.keys())
        loc_names: dict[str, str] = {
            "YC1": getattr(self.settings, "food_location_yc1", "") or "Кульман 1/1",
            "YC2": getattr(self.settings, "food_location_yc2", "") or "Мстиславца 6",
            "YC3": getattr(self.settings, "food_location_yc3", "") or "Адрес не указан",
            "unknown": "Без адреса / требует проверки",
        }
        # Always include YC1 and YC2 in results even if empty
        known_codes = ["YC1", "YC2"]
        checks: list[dict[str, Any]] = []
        warnings: list[str] = []
        errors: list[str] = []

        def _add_check(code: str, status: str, message: str) -> None:
            checks.append({"code": code, "status": status, "message": message})
            if status == "error":
                errors.append(message)
            elif status == "warning":
                warnings.append(message)

        # --- CHECK: menu items exist ---
        if not food_items:
            _add_check("MENU_ITEMS_EXIST", "warning", "В меню нет блюд.")
        else:
            _add_check("MENU_ITEMS_EXIST", "passed", f"Блюд в меню: {len(food_items)}")

        # --- Partition orders ---
        active_child = [o for o in child_orders if o.get("status") == "submitted"]
        skipped_child = [o for o in child_orders if o.get("status") == "skipped"]
        other_child = [o for o in child_orders if o.get("status") not in ("submitted", "skipped")]
        active_staff = [o for o in staff_orders if o.get("status") == "submitted"]
        skipped_staff = [o for o in staff_orders if o.get("status") == "skipped"]

        total_deleted_child = len(other_child)
        total_people = len(active_child) + len(active_staff)
        total_no_food = len(skipped_child) + len(skipped_staff)

        # --- CHECK: no cancelled/other orders in active set ---
        if total_deleted_child > 0:
            _add_check("NO_DELETED_ORDERS_IN_TOTALS", "passed",
                       f"Удалённых/отменённых детских заказов: {total_deleted_child} — в итог не включены.")
        else:
            _add_check("NO_DELETED_ORDERS_IN_TOTALS", "passed", "Удалённых заказов нет — итог чистый.")

        # --- CHECK: no-food excluded from item totals ---
        _add_check("NO_FOOD_EXCLUDED_FROM_ITEMS", "passed",
                   f"'Без питания' в итог по блюдам не включено. Детей: {len(skipped_child)}, сотр.: {len(skipped_staff)}")

        # --- CHECK: unique child orders ---
        child_student_ids = [o.get("mk_student_id") for o in active_child]
        dup_child = len(child_student_ids) - len(set(child_student_ids))
        if dup_child > 0:
            _add_check("CHILD_ORDERS_UNIQUE", "error", f"Найдено дублей детских заказов: {dup_child}. Проверьте данные.")
        else:
            _add_check("CHILD_ORDERS_UNIQUE", "passed", "Дублей детских заказов нет.")

        # --- CHECK: unique staff orders ---
        staff_user_ids = [o.get("staff_user_id") for o in active_staff]
        dup_staff = len(staff_user_ids) - len(set(staff_user_ids))
        if dup_staff > 0:
            _add_check("STAFF_ORDERS_UNIQUE", "error", f"Найдено дублей заказов сотрудников: {dup_staff}. Проверьте данные.")
        else:
            _add_check("STAFF_ORDERS_UNIQUE", "passed", "Дублей заказов сотрудников нет.")

        # --- Compute global item totals and validate ---
        raw_item_qty: dict[int, int] = {}
        unknown_menu_items: list[str] = []
        qty_invalid = 0
        prices_missing: list[str] = []
        for o in active_child + active_staff:
            for it in o.get("items") or []:
                iid = int(it.get("item_id") or 0)
                qty = int(it.get("quantity") or 1)
                if qty < 1:
                    qty_invalid += 1
                raw_item_qty[iid] = raw_item_qty.get(iid, 0) + qty
                if iid not in known_item_ids:
                    unknown_menu_items.append(it.get("name") or f"id={iid}")
        for iid in raw_item_qty:
            if iid in known_item_ids and item_price_map.get(iid, 0.0) == 0.0:
                prices_missing.append(item_name_map.get(iid) or f"id={iid}")

        # --- CHECK: quantity valid ---
        if qty_invalid > 0:
            _add_check("QUANTITY_VALID", "error", f"Найдено позиций с quantity < 1: {qty_invalid}.")
        else:
            _add_check("QUANTITY_VALID", "passed", "Все quantity корректны (>= 1).")

        # --- CHECK: prices ---
        if prices_missing:
            msg = "Нет цены у блюд: " + ", ".join(f'"{n}"' for n in prices_missing[:5])
            if len(prices_missing) > 5:
                msg += f" и ещё {len(prices_missing) - 5}"
            _add_check("PRICES_EXIST_OR_WARN", "warning", msg)
        else:
            _add_check("PRICES_EXIST_OR_WARN", "passed", "Цены есть у всех блюд в заказах.")

        if unknown_menu_items:
            _add_check("MENU_ITEMS_EXIST", "warning",
                       f"Заказы содержат блюда вне меню: {', '.join(set(unknown_menu_items[:3]))}")

        # --- Global totals ---
        total_amount = round(sum(item_price_map.get(iid, 0.0) * qty for iid, qty in raw_item_qty.items()), 2)
        total_items_qty = sum(raw_item_qty.values())

        # --- Location data structure ---
        menu_loc = str(menu.get("location_code") or "").strip().upper()

        loc_data: dict[str, dict[str, Any]] = {}

        def _ensure_loc(code: str) -> dict[str, Any]:
            if code not in loc_data:
                loc_data[code] = {
                    "locationCode": code,
                    "locationName": loc_names.get(code, f"Адрес не определён ({code})"),
                    "childOrders": 0,
                    "staffOrders": 0,
                    "noFoodChild": 0,
                    "noFoodStaff": 0,
                    "itemQty": {},
                    "amount": 0.0,
                }
            return loc_data[code]

        # Initialise always-present codes
        for c in known_codes:
            _ensure_loc(c)

        # --- Assign children to locations using the same _get_food_group_info logic as regular summary ---
        child_no_loc: list[str] = []
        for o in active_child:
            group_code = _gfgi(o).get("groupCode", "unknown")
            if not group_code or group_code == "unknown":
                child_no_loc.append(str(o.get("child_name") or o.get("mk_student_id") or "неизвестный ребёнок"))
                group_code = "unknown"
            entry = _ensure_loc(group_code)
            entry["childOrders"] += 1
            for it in o.get("items") or []:
                iid = int(it.get("item_id") or 0)
                qty = int(it.get("quantity") or 1)
                entry["itemQty"][iid] = entry["itemQty"].get(iid, 0) + qty
                entry["amount"] += item_price_map.get(iid, 0.0) * qty

        for o in skipped_child:
            group_code = _gfgi(o).get("groupCode", "unknown")
            _ensure_loc(group_code)["noFoodChild"] += 1

        # --- Assign staff/teachers to locations ---
        orders_missing_loc: list[str] = []
        for o in active_staff:
            loc = str(o.get("location_code") or "").strip().upper() or menu_loc
            if not loc:
                loc = "unknown"
                orders_missing_loc.append(str(o.get("staff_name") or f"staff_user_id={o.get('staff_user_id')}"))
            entry = _ensure_loc(loc)
            entry["staffOrders"] += 1
            for it in o.get("items") or []:
                iid = int(it.get("item_id") or 0)
                qty = int(it.get("quantity") or 1)
                entry["itemQty"][iid] = entry["itemQty"].get(iid, 0) + qty
                entry["amount"] += item_price_map.get(iid, 0.0) * qty

        for o in skipped_staff:
            loc = str(o.get("location_code") or "").strip().upper() or menu_loc or "unknown"
            _ensure_loc(loc)["noFoodStaff"] += 1

        # --- CHECK: orders have location ---
        if child_no_loc:
            msg = f"Дети без определённого адреса: {', '.join(child_no_loc[:3])}"
            if len(child_no_loc) > 3:
                msg += f" и ещё {len(child_no_loc) - 3}"
            _add_check("ORDERS_HAVE_LOCATION", "warning", msg)
        elif orders_missing_loc:
            msg = f"Сотрудники без указания филиала: {', '.join(orders_missing_loc[:3])}"
            if len(orders_missing_loc) > 3:
                msg += f" и ещё {len(orders_missing_loc) - 3}"
            _add_check("ORDERS_HAVE_LOCATION", "warning", msg)
        else:
            _add_check("ORDERS_HAVE_LOCATION", "passed", "У всех заказов определён адрес.")

        # --- CHECK: teacher branch resolved ---
        teachers_no_branch = []
        for o in active_staff:
            is_teacher = bool(str(o.get("staff_mk_teacher_id") or "").strip()) or \
                         str(o.get("staff_role") or "") in ("teacher", "intern", "methodist")
            if is_teacher and not str(o.get("location_code") or "").strip():
                teachers_no_branch.append(str(o.get("staff_name") or f"id={o.get('staff_user_id')}"))
        if teachers_no_branch:
            _add_check("TEACHER_BRANCH_RESOLVED", "warning",
                       f"Преподаватели без определённого филиала: {', '.join(teachers_no_branch[:3])}")
        else:
            _add_check("TEACHER_BRANCH_RESOLVED", "passed", "Все преподаватели с определённым филиалом.")

        # --- CHECK: item totals match orders ---
        combined_qty: dict[int, int] = {}
        for entry in loc_data.values():
            for iid, qty in entry["itemQty"].items():
                combined_qty[iid] = combined_qty.get(iid, 0) + qty
        if combined_qty == raw_item_qty:
            _add_check("ITEM_TOTALS_MATCH_ORDERS", "passed", "Итог по блюдам совпадает с заказами по всем адресам.")
        else:
            _add_check("ITEM_TOTALS_MATCH_ORDERS", "error", "Итог по блюдам не совпадает с суммой заказов.")

        # --- CHECK: location totals match global ---
        loc_qty_sum = sum(sum(e["itemQty"].values()) for e in loc_data.values())
        loc_amount_sum = round(sum(e["amount"] for e in loc_data.values()), 2)
        if loc_qty_sum == total_items_qty and abs(loc_amount_sum - total_amount) < 0.01:
            _add_check("LOCATION_TOTALS_MATCH_GLOBAL_TOTAL", "passed",
                       f"Сумма по адресам {loc_amount_sum:.2f} BYN = общей сумме {total_amount:.2f} BYN.")
        else:
            _add_check("LOCATION_TOTALS_MATCH_GLOBAL_TOTAL", "error",
                       f"Сумма по адресам {loc_amount_sum:.2f} BYN ≠ общей сумме {total_amount:.2f} BYN.")

        # --- CHECK: money totals ---
        _add_check("MONEY_TOTALS_MATCH", "passed",
                   f"Пересчитанная сумма: {total_amount:.2f} BYN ({total_items_qty} порций).")

        # --- CHECK: utensils count ---
        utensils_by_loc: dict[str, int] = {}
        for code, entry in loc_data.items():
            utensils_by_loc[code] = entry["childOrders"] + entry["staffOrders"]
        total_utensils_audit = sum(utensils_by_loc.values())
        loc_utensils_str = ", ".join(
            f"{code}: {cnt}" for code, cnt in sorted(utensils_by_loc.items()) if cnt > 0
        )
        _add_check(
            "UTENSILS_COUNT", "passed",
            f"Столовые приборы: {total_utensils_audit} комплект(ов)"
            + (f" ({loc_utensils_str})" if loc_utensils_str else "") + ".",
        )

        # --- Build global items summary ---
        items_summary = []
        for iid, qty in sorted(raw_item_qty.items(), key=lambda x: -x[1]):
            name = item_name_map.get(iid) or f"item_id={iid}"
            price = item_price_map.get(iid, 0.0)
            items_summary.append({"itemId": iid, "itemName": name, "qty": qty, "price": price,
                                   "amount": round(price * qty, 2)})

        # --- Build per-location results (YC1 first, then YC2, then others, unknown last) ---
        code_order = {c: i for i, c in enumerate(known_codes)}
        sorted_codes = sorted(loc_data.keys(),
                              key=lambda c: (0 if c in code_order else (1 if c != "unknown" else 2), code_order.get(c, 0), c))
        loc_results = []
        for code in sorted_codes:
            entry = loc_data[code]
            loc_items = []
            for iid, qty in sorted(entry["itemQty"].items(), key=lambda x: -x[1]):
                name = item_name_map.get(iid) or f"item_id={iid}"
                price = item_price_map.get(iid, 0.0)
                loc_items.append({"itemName": name, "qty": qty, "price": price, "amount": round(price * qty, 2)})
            loc_results.append({
                "locationCode": entry["locationCode"],
                "locationName": entry["locationName"],
                "childOrders": entry["childOrders"],
                "staffOrders": entry["staffOrders"],
                "noFood": entry["noFoodChild"] + entry["noFoodStaff"],
                "totalItemsQty": sum(entry["itemQty"].values()),
                "totalAmount": round(entry["amount"], 2),
                "items": loc_items,
            })

        overall_status = "failed" if errors else ("warning" if warnings else "passed")
        # Per-location stats for logging
        yc1 = loc_data.get("YC1", {})
        yc2 = loc_data.get("YC2", {})
        unk = loc_data.get("unknown", {})
        result: dict[str, Any] = {
            "ok": True,
            "menuId": mid,
            "menuDate": menu.get("menu_date"),
            "menuTitle": menu.get("title"),
            "menuStatus": menu.get("status"),
            "auditStatus": overall_status,
            "summary": {
                "totalPeople": total_people,
                "childOrders": len(active_child),
                "staffOrders": len(active_staff),
                "noFood": total_no_food,
                "totalItemsQty": total_items_qty,
                "totalAmount": total_amount,
                "deletedChildOrders": total_deleted_child,
            },
            "locations": loc_results,
            "items": items_summary,
            "checks": checks,
            "warnings": warnings,
            "errors": errors,
        }
        log.info(
            "food_summary_audit_run user_id=%s role=%s menu_id=%s menu_date=%s status=%s "
            "errors=%s warnings=%s "
            "yc1_people=%s yc1_amount=%.2f yc2_people=%s yc2_amount=%.2f "
            "unknown_people=%s unknown_amount=%.2f total_people=%s total_amount=%.2f",
            user_id, role, mid, menu.get("menu_date"), overall_status,
            len(errors), len(warnings),
            yc1.get("childOrders", 0) + yc1.get("staffOrders", 0), yc1.get("amount", 0.0),
            yc2.get("childOrders", 0) + yc2.get("staffOrders", 0), yc2.get("amount", 0.0),
            unk.get("childOrders", 0) + unk.get("staffOrders", 0), unk.get("amount", 0.0),
            total_people, total_amount,
        )
        return result

    def food_shift_report(self, auth: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_admin(auth)
        if denied:
            return denied
        start_date = (params.get("start_date") or "").strip() or None
        end_date = (params.get("end_date") or "").strip() or None
        report = self.storage.get_food_shift_report(start_date, end_date)
        location_map = {
            "YC1": getattr(self.settings, "food_location_yc1", "Кульман 1/1") or "Кульман 1/1",
            "YC2": getattr(self.settings, "food_location_yc2", "Мстиславца 6") or "Мстиславца 6",
            "YC3": getattr(self.settings, "food_location_yc3", "Адрес не указан") or "Адрес не указан",
        }
        for loc in report.get("byLocations", []):
            gc = loc.get("groupCode", "")
            loc["locationName"] = location_map.get(gc, gc)
        for child in report.get("byChildren", []):
            gc = child.get("groupCode", "")
            child["locationName"] = location_map.get(gc, gc)
        return {"ok": True, **report}

    def _is_admin_role(self, auth: dict[str, Any]) -> bool:
        user_id = int(auth["user_id"])
        return self._role_for_user(user_id) in ADMIN_ROLES

    def _require_admin(self, auth: dict[str, Any]) -> dict[str, Any] | None:
        if not self._is_admin_role(auth):
            return {"ok": False, "error": "Админ-панель доступна владельцу, старшему преподавателю, методисту и операционному менеджеру."}
        return None

    def _require_food_menu_edit(self, auth: dict[str, Any]) -> dict[str, Any] | None:
        user_id = int(auth["user_id"])
        role = self._role_for_user(user_id)
        if role not in FOOD_MENU_EDIT_ROLES:
            return {"ok": False, "error": "Создание и редактирование меню доступно кухне и администраторам."}
        return None

    def _require_food_admin_edit(self, auth: dict[str, Any]) -> dict[str, Any] | None:
        role = self._role_for_user(int(auth["user_id"]))
        if role not in FOOD_ADMIN_EDIT_ROLES:
            return {"ok": False, "error": "Ручное добавление и редактирование заказов питания доступно только owner, admin, operations."}
        return None

    def food_admin_persons_for_menu(self, auth: dict[str, Any], menu_id: str) -> dict[str, Any]:
        """GET: return list of children + staff for admin order form."""
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_food_admin_edit(auth)
        if denied:
            return denied
        try:
            mid = int(menu_id)
        except (ValueError, TypeError):
            return {"ok": False, "error": "Неверный menu_id"}
        menu = self.storage.get_food_menu(mid)
        if not menu:
            return {"ok": False, "error": "Меню не найдено"}
        children = self.storage.list_camp_children(active_only=True)
        staff_list = self.storage.list_staff_users(limit=200)
        staff_food_roles = {"owner", "admin", "teacher", "methodist", "intern", "client_manager", "operations", "other"}
        staff_out = [
            {"userId": s["user_id"], "displayName": str(s.get("mk_teacher_name") or s.get("full_name") or s.get("username") or f"Сотрудник #{s['user_id']}"), "role": s.get("role", ""), "mkTeacherId": s.get("mk_teacher_id", "")}
            for s in staff_list if s.get("role", "") in staff_food_roles
        ]
        children_out = [
            {"mkStudentId": c["mk_student_id"], "fullName": c["full_name"], "groupName": c.get("group_name") or c.get("mk_class_name") or ""}
            for c in children
        ]
        items_out = [
            {"id": it["id"], "name": it["name"], "category": it.get("category") or "", "weight": it.get("weight") or "", "price": float(it.get("price") or 0)}
            for it in (menu.get("items") or []) if it.get("is_available")
        ]
        return {"ok": True, "children": children_out, "staff": staff_out, "menuItems": items_out, "menu": {"id": menu["id"], "title": menu.get("title") or "", "menuDate": menu.get("menu_date") or "", "locationCode": menu.get("location_code") or ""}}

    def food_admin_manual_child_order(self, auth: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        """POST: admin creates a manual child order after deadline."""
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_food_admin_edit(auth)
        if denied:
            return denied
        admin_uid = int(auth["user_id"])
        admin_role = self._role_for_user(admin_uid)
        try:
            menu_id = int(payload.get("menu_id") or 0)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Неверный menu_id"}
        if not menu_id:
            return {"ok": False, "error": "menu_id обязателен"}
        mk_student_id = str(payload.get("mk_student_id") or "").strip()
        if not mk_student_id:
            return {"ok": False, "error": "mk_student_id обязателен"}
        menu = self.storage.get_food_menu(menu_id)
        if not menu:
            return {"ok": False, "error": "Меню не найдено"}
        if menu.get("deleted_at"):
            return {"ok": False, "error": "Меню удалено"}
        available_items = [it for it in (menu.get("items") or []) if it.get("is_available")]
        menu_item_ids = {int(it["id"]) for it in available_items}
        raw_items = payload.get("items") or []
        item_quantities: dict[int, int] = {}
        for entry in raw_items:
            try:
                iid = int(entry["id"])
                qty = min(99, max(1, int(entry.get("quantity", 1) or 1)))
                if iid in menu_item_ids and qty > 0:
                    item_quantities[iid] = qty
            except (KeyError, TypeError, ValueError):
                pass
        if not item_quantities:
            return {"ok": False, "error": "Выберите хотя бы одно блюдо"}
        admin_comment = str(payload.get("comment") or "Добавлено администратором вручную").strip()
        try:
            order = self.storage.admin_create_food_child_order(menu_id, mk_student_id, item_quantities, admin_uid, admin_comment)
        except ValueError as ve:
            if "duplicate" in str(ve):
                return {"ok": False, "error": "У этого ребёнка уже есть заказ на это меню. Откройте существующий заказ для редактирования."}
            return {"ok": False, "error": str(ve)}
        self.storage.log_food_order_audit(menu_id, order["id"], "child", "manual_create", admin_uid, admin_role, None, {"mk_student_id": mk_student_id, "items": item_quantities}, admin_comment)
        log.info("food_order_manual_create: admin=%s role=%s menu_id=%s mk_student_id=%s items=%s", admin_uid, admin_role, menu_id, mk_student_id, item_quantities)
        return {"ok": True, "order": order}

    def food_admin_manual_staff_order(self, auth: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        """POST: admin creates a manual staff/teacher order after deadline."""
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_food_admin_edit(auth)
        if denied:
            return denied
        admin_uid = int(auth["user_id"])
        admin_role = self._role_for_user(admin_uid)
        try:
            menu_id = int(payload.get("menu_id") or 0)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Неверный menu_id"}
        if not menu_id:
            return {"ok": False, "error": "menu_id обязателен"}
        try:
            staff_user_id = int(payload.get("staff_user_id") or 0)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Неверный staff_user_id"}
        if not staff_user_id:
            return {"ok": False, "error": "staff_user_id обязателен"}
        location_code = str(payload.get("location_code") or "").strip().upper()
        if location_code not in ("YC1", "YC2", "YC3"):
            location_code = ""
        menu = self.storage.get_food_menu(menu_id)
        if not menu:
            return {"ok": False, "error": "Меню не найдено"}
        if menu.get("deleted_at"):
            return {"ok": False, "error": "Меню удалено"}
        available_items = [it for it in (menu.get("items") or []) if it.get("is_available")]
        menu_item_ids = {int(it["id"]) for it in available_items}
        raw_items = payload.get("items") or []
        item_quantities: dict[int, int] = {}
        for entry in raw_items:
            try:
                iid = int(entry["id"])
                qty = min(99, max(1, int(entry.get("quantity", 1) or 1)))
                if iid in menu_item_ids and qty > 0:
                    item_quantities[iid] = qty
            except (KeyError, TypeError, ValueError):
                pass
        if not item_quantities:
            return {"ok": False, "error": "Выберите хотя бы одно блюдо"}
        admin_comment = str(payload.get("comment") or "Добавлено администратором вручную").strip()
        try:
            order = self.storage.admin_create_food_staff_order(menu_id, staff_user_id, item_quantities, location_code, admin_uid, admin_comment)
        except ValueError as ve:
            if "duplicate" in str(ve):
                return {"ok": False, "error": "У этого сотрудника уже есть заказ на это меню. Откройте существующий заказ для редактирования."}
            return {"ok": False, "error": str(ve)}
        self.storage.log_food_order_audit(menu_id, order["id"], "staff", "manual_create", admin_uid, admin_role, None, {"staff_user_id": staff_user_id, "items": item_quantities, "location_code": location_code}, admin_comment)
        log.info("food_staff_order_manual_create: admin=%s role=%s menu_id=%s staff_user_id=%s loc=%s", admin_uid, admin_role, menu_id, staff_user_id, location_code)
        return {"ok": True, "order": order}

    def food_admin_edit_child_order(self, auth: dict[str, Any], order_id_str: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST: admin edits an existing child order items."""
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_food_admin_edit(auth)
        if denied:
            return denied
        admin_uid = int(auth["user_id"])
        admin_role = self._role_for_user(admin_uid)
        try:
            order_id = int(order_id_str or 0)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Неверный order_id"}
        existing = self.storage.get_food_child_order_by_id(order_id)
        if not existing:
            return {"ok": False, "error": "Заказ не найден"}
        menu = self.storage.get_food_menu(existing["menu_id"])
        if not menu:
            return {"ok": False, "error": "Меню не найдено"}
        available_items = [it for it in (menu.get("items") or []) if it.get("is_available")]
        menu_item_ids = {int(it["id"]) for it in available_items}
        raw_items = payload.get("items") or []
        item_quantities: dict[int, int] = {}
        for entry in raw_items:
            try:
                iid = int(entry["id"])
                qty = min(99, max(1, int(entry.get("quantity", 1) or 1)))
                if iid in menu_item_ids and qty > 0:
                    item_quantities[iid] = qty
            except (KeyError, TypeError, ValueError):
                pass
        if not item_quantities:
            return {"ok": False, "error": "Выберите хотя бы одно блюдо"}
        admin_comment = str(payload.get("comment") or "").strip() or "Изменено администратором"
        old_items = {int(it["item_id"]): int(it.get("quantity", 1)) for it in (existing.get("items") or [])}
        try:
            order = self.storage.admin_update_food_child_order(order_id, item_quantities, admin_uid, admin_comment)
        except ValueError as ve:
            return {"ok": False, "error": str(ve)}
        self.storage.log_food_order_audit(existing["menu_id"], order_id, "child", "admin_edit", admin_uid, admin_role, {"items": old_items}, {"items": item_quantities}, admin_comment)
        log.info("food_order_admin_edit: admin=%s role=%s order_id=%s new_items=%s", admin_uid, admin_role, order_id, item_quantities)
        return {"ok": True, "order": order}

    def food_admin_edit_staff_order(self, auth: dict[str, Any], order_id_str: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST: admin edits an existing staff order items/location."""
        if not getattr(self.settings, "food_module_enabled", False):
            return {"ok": False, "error": "food_module_disabled"}
        denied = self._require_food_admin_edit(auth)
        if denied:
            return denied
        admin_uid = int(auth["user_id"])
        admin_role = self._role_for_user(admin_uid)
        try:
            order_id = int(order_id_str or 0)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Неверный order_id"}
        existing = self.storage.get_food_staff_order_by_id(order_id)
        if not existing:
            return {"ok": False, "error": "Заказ не найден"}
        menu = self.storage.get_food_menu(existing["menu_id"])
        if not menu:
            return {"ok": False, "error": "Меню не найдено"}
        available_items = [it for it in (menu.get("items") or []) if it.get("is_available")]
        menu_item_ids = {int(it["id"]) for it in available_items}
        raw_items = payload.get("items") or []
        item_quantities: dict[int, int] = {}
        for entry in raw_items:
            try:
                iid = int(entry["id"])
                qty = min(99, max(1, int(entry.get("quantity", 1) or 1)))
                if iid in menu_item_ids and qty > 0:
                    item_quantities[iid] = qty
            except (KeyError, TypeError, ValueError):
                pass
        if not item_quantities:
            return {"ok": False, "error": "Выберите хотя бы одно блюдо"}
        location_code = str(payload.get("location_code") or "").strip().upper()
        if location_code not in ("YC1", "YC2", "YC3"):
            location_code = str(existing.get("location_code") or "").strip().upper()
        admin_comment = str(payload.get("comment") or "").strip() or "Изменено администратором"
        old_items = {int(it["item_id"]): int(it.get("quantity", 1)) for it in (existing.get("items") or [])}
        old_loc = str(existing.get("location_code") or "")
        try:
            order = self.storage.admin_update_food_staff_order(order_id, item_quantities, location_code, admin_uid, admin_comment)
        except ValueError as ve:
            return {"ok": False, "error": str(ve)}
        self.storage.log_food_order_audit(existing["menu_id"], order_id, "staff", "admin_edit", admin_uid, admin_role, {"items": old_items, "location_code": old_loc}, {"items": item_quantities, "location_code": location_code}, admin_comment)
        log.info("food_staff_order_admin_edit: admin=%s role=%s order_id=%s loc=%s new_items=%s", admin_uid, admin_role, order_id, location_code, item_quantities)
        return {"ok": True, "order": order}

    def _require_test_access(self, auth: dict[str, Any]) -> dict[str, Any] | None:
        user_id = int(auth["user_id"])
        real_role = self._base_role_for_user(user_id)
        if real_role not in ADMIN_ROLES:
            return {"ok": False, "error": "Доступ только для администраторов."}
        if not self._can_use_role_test(user_id):
            return {"ok": False, "error": "Тестовый режим выключен (WEB_APP_TEST_ROLES=false)."}
        return None

    def _require_admin_tab(self, auth: dict[str, Any], tab: str) -> dict[str, Any] | None:
        denied = self._require_admin(auth)
        if denied:
            return denied
        role = self._role_for_user(int(auth["user_id"]))
        if tab not in self._admin_tabs_for_role(role):
            return {"ok": False, "error": "Эта часть админки недоступна для выбранной роли."}
        return None

    def _prep_result_reviewer_ids(self) -> list[int]:
        """Return Telegram user IDs that should receive teachers' preparation files."""
        ids: list[int] = []
        configured = list(getattr(self.settings, "senior_teacher_ids", []) or [])
        if configured:
            ids.extend(int(x) for x in configured if x)
        else:
            try:
                for user in self.storage.list_staff_users(limit=500):
                    if str(user.get("status") or "active") != "active":
                        continue
                    if str(user.get("role") or "").lower() == "methodist" and user.get("user_id"):
                        ids.append(int(user["user_id"]))
            except Exception:
                pass
            ids.extend(int(x) for x in (self.settings.admin_ids or []) if x)

        seen: set[int] = set()
        unique: list[int] = []
        for uid in ids:
            if uid and uid not in seen:
                seen.add(uid)
                unique.append(uid)
        return unique

    def _intern_notify_admins(self, text: str) -> None:
        token = self.settings.telegram_bot_token
        if not token:
            return
        sent: set[int] = set()
        for uid in self._prep_result_reviewer_ids():
            if uid not in sent:
                sent.add(uid)
                _telegram_send(token, uid, text)

    def _intern_notify_user(self, intern_user_id: int, text: str) -> None:
        token = self.settings.telegram_bot_token
        if token and intern_user_id:
            _telegram_send(token, int(intern_user_id), text)

    def prep_result_download_info(self, auth: dict[str, Any], file_id: str | int) -> dict[str, Any]:
        row = self.storage.get_teacher_prep_result_file(file_id)
        if not row:
            return {"ok": False, "error": "Файл результата не найден."}

        user_id = int(auth["user_id"])
        is_owner = row.get("teacher_user_id") and int(row.get("teacher_user_id")) == user_id
        if not (self._is_admin_role(auth) or is_owner):
            return {"ok": False, "error": "Нет доступа к этому файлу."}

        raw_path = str(row.get("stored_path") or "").strip()
        if not raw_path:
            return {"ok": False, "error": "У файла не сохранён путь."}
        path = Path(raw_path)
        if not path.is_absolute():
            path = (BASE_DIR / path).resolve()
        else:
            path = path.resolve()

        base = (BASE_DIR / "data" / "prep_results").resolve()
        try:
            path.relative_to(base)
        except Exception:
            return {"ok": False, "error": "Недопустимый путь к файлу."}
        if not path.exists() or not path.is_file():
            return {"ok": False, "error": "Файл есть в базе, но не найден на диске."}

        filename = _safe_filename(str(row.get("file_name") or path.name))
        mime_type = str(row.get("mime_type") or "").strip() or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        return {"ok": True, "path": path, "filename": filename, "mimeType": mime_type, "row": row}

    def _control_is_past(self, row: dict[str, Any]) -> bool:
        lesson_date = str(row.get("lesson_date") or "")[:10]
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", lesson_date):
            return False
        lesson_time = str(row.get("lesson_time") or "")
        end_time = "23:59"
        match = re.search(r"(\d{1,2}:\d{2})\s*[-–—]\s*(\d{1,2}:\d{2})", lesson_time)
        if match:
            end_time = match.group(2)
        else:
            one = re.search(r"(\d{1,2}:\d{2})", lesson_time)
            if one:
                end_time = one.group(1)
        try:
            return datetime.now() > datetime.fromisoformat(f"{lesson_date}T{end_time}:00")
        except Exception:
            return False

    def _prep_missing_steps(self, row: dict[str, Any]) -> list[str]:
        checks = [
            ("prep_material_status", "материал Notion"),
            ("prep_video_status", "видео / инструкция"),
            ("prep_practice_status", "практика"),
            ("prep_result_status", "файл результата"),
        ]
        return [label for key, label in checks if str(row.get(key) or "not_checked") != "done"]

    def _lesson_control_dashboard_items(self, limit: int = 500) -> list[dict[str, Any]]:
        controls = self.storage.list_teacher_lesson_control(limit=limit)
        controls_by_lesson = {str(x.get("lesson_id") or ""): dict(x) for x in controls if x.get("lesson_id")}
        snapshots = self.storage.list_lesson_snapshots(limit=limit)
        staff_by_mk: dict[str, dict[str, Any]] = {}
        for user in self.storage.list_staff_users(limit=1000):
            mk_id = str(user.get("mk_teacher_id") or "").strip()
            if mk_id and mk_id not in staff_by_mk:
                staff_by_mk[mk_id] = user

        merged: dict[str, dict[str, Any]] = {}
        for snap in snapshots:
            lesson_id = str(snap.get("lesson_id") or "").strip()
            if not lesson_id:
                continue
            row = dict(controls_by_lesson.get(lesson_id) or {})
            row.setdefault("lesson_id", lesson_id)
            row["lesson_date"] = row.get("lesson_date") or snap.get("lesson_date") or ""
            row["lesson_time"] = row.get("lesson_time") or snap.get("lesson_time") or ""
            row["group_name"] = row.get("group_name") or snap.get("group_name") or ""
            row["lesson_topic"] = row.get("lesson_topic") or snap.get("lesson_topic") or ""
            row["teacher_name"] = row.get("teacher_name") or snap.get("teacher_names") or ""
            if not row.get("mk_teacher_id"):
                teacher_ids = [x.strip() for x in str(snap.get("teacher_ids") or "").split(",") if x.strip()]
                row["mk_teacher_id"] = teacher_ids[0] if teacher_ids else ""
            if not row.get("teacher_user_id") and row.get("mk_teacher_id"):
                staff = staff_by_mk.get(str(row.get("mk_teacher_id") or ""))
                if staff and staff.get("user_id"):
                    row["teacher_user_id"] = staff.get("user_id")
                    row["teacher_name"] = row.get("teacher_name") or staff.get("full_name") or staff.get("username") or ""
            merged[lesson_id] = row

        for lesson_id, control in controls_by_lesson.items():
            if lesson_id not in merged:
                merged[lesson_id] = dict(control)

        items: list[dict[str, Any]] = []
        for row in merged.values():
            row = dict(row)
            for key, default in (
                ("preparation_status", "not_started"),
                ("prep_material_status", "not_checked"),
                ("prep_video_status", "not_checked"),
                ("prep_practice_status", "not_checked"),
                ("prep_result_status", "not_checked"),
                ("lesson_status", "not_started"),
                ("parent_report_status", "not_checked"),
                ("myclass_status", "not_checked"),
                ("works_status", "not_checked"),
                ("classroom_status", "not_checked"),
                ("problem_status", "none"),
            ):
                row[key] = row.get(key) or default
            row["is_past"] = self._control_is_past(row)
            row["prep_missing"] = self._prep_missing_steps(row)
            row["close_missing"] = _missing_close_steps(row)
            if str(row.get("lesson_status") or "") == "closed":
                row["attention"] = "closed"
            elif str(row.get("problem_status") or "") == "problem" or str(row.get("preparation_status") or "") == "needs_help" or str(row.get("prep_result_status") or "") == "rejected":
                row["attention"] = "problem"
            elif str(row.get("prep_result_status") or "") == "submitted":
                row["attention"] = "review"
            elif row["is_past"]:
                row["attention"] = "overdue"
            elif str(row.get("preparation_status") or "") != "ready":
                row["attention"] = "prep"
            else:
                row["attention"] = "ok"
            items.append(row)

        order = {"problem": 0, "review": 1, "overdue": 2, "prep": 3, "ok": 4, "closed": 5}
        items.sort(key=lambda r: (order.get(str(r.get("attention") or ""), 9), str(r.get("lesson_date") or ""), str(r.get("lesson_time") or ""), str(r.get("teacher_name") or "")))
        return items[: int(limit or 500)]

    def admin_overview(self, auth: dict[str, Any]) -> dict[str, Any]:
        denied = self._require_admin(auth)
        if denied:
            return denied
        tasks = self.storage.list_teacher_tasks(user_id=None, status="open", limit=200)
        profiles = self.storage.list_teacher_profiles(limit=200)
        controls = self.storage.list_teacher_lesson_control(limit=200)
        problems = [x for x in controls if str(x.get("problem_status") or "").strip() == "problem"]
        open_by_priority: dict[str, int] = {}
        open_by_type: dict[str, int] = {}
        for t in tasks:
            open_by_priority[str(t.get("priority") or "normal")] = open_by_priority.get(str(t.get("priority") or "normal"), 0) + 1
            open_by_type[str(t.get("task_type") or "task")] = open_by_type.get(str(t.get("task_type") or "task"), 0) + 1
        dashboard_items = self._lesson_control_dashboard_items(limit=500)
        return {
            "ok": True,
            "stats": {
                "openTasks": len(tasks),
                "teachers": len(profiles),
                "lessonControls": len(dashboard_items) or len(controls),
                "problems": len(problems) + sum(1 for x in dashboard_items if str(x.get("attention") or "") == "problem"),
                "waitingReview": sum(1 for x in dashboard_items if str(x.get("attention") or "") == "review"),
                "notClosedPast": sum(1 for x in dashboard_items if bool(x.get("is_past")) and str(x.get("lesson_status") or "") != "closed"),
                "openByPriority": open_by_priority,
                "openByType": open_by_type,
            },
            "tasks": tasks[:50],
            "teachers": profiles[:50],
            "problems": problems[:50],
        }

    def admin_teachers(self, auth: dict[str, Any]) -> dict[str, Any]:
        denied = self._require_admin_tab(auth, "teachers")
        if denied:
            return denied
        return {"ok": True, "items": self.storage.list_teacher_profiles(limit=300)}

    def admin_users(self, auth: dict[str, Any]) -> dict[str, Any]:
        denied = self._require_admin_tab(auth, "users")
        if denied:
            return denied
        raw = self.storage.list_staff_users(limit=300)
        items: list[dict[str, Any]] = []
        for u in raw:
            mk_name = str(u.get("mk_teacher_name") or "").strip()
            full_name = str(u.get("full_name") or "").strip()
            username = str(u.get("username") or "").strip()
            mk_teacher_id = str(u.get("mk_teacher_id") or "").strip()
            role = str(u.get("role") or "")
            if mk_name:
                resolved = mk_name
                source = "moyklass"
            elif full_name:
                resolved = full_name
                source = "staff_user"
            elif username:
                resolved = username
                source = "telegram"
            else:
                resolved = f"Сотрудник #{u['user_id']}"
                source = "fallback"
            u["resolved_display_name"] = resolved
            u["display_name_source"] = source
            warnings: list[str] = []
            if mk_teacher_id and not mk_name:
                warnings.append("MK teacherId указан, но имя преподавателя не найдено в кэше МойКласс")
            if mk_teacher_id and role in ("kitchen", "restaurant"):
                warnings.append("Есть MK teacherId — этот пользователь будет считаться преподавателем в питании")
            if role == "teacher" and not mk_teacher_id:
                warnings.append("Роль Преподаватель, но MK teacherId не привязан")
            u["warnings"] = warnings
            items.append(u)
        return {"ok": True, "items": items}

    def teacher_diagnostics(self, auth: dict[str, Any], telegram_user_id: str) -> dict[str, Any]:
        """Diagnostic: check teacher access chain for a given Telegram user ID.
        Returns user record, lesson counts from both sources, locations, and root-cause reason.
        Only accessible to owner/admin/operations."""
        denied = self._require_admin(auth)
        if denied:
            return denied
        try:
            uid = int(telegram_user_id or 0)
        except (ValueError, TypeError):
            return {"ok": False, "error": "Неверный telegram_user_id"}
        if not uid:
            return {"ok": False, "error": "Неверный telegram_user_id"}
        try:
            from storage import normalize_food_location as _nfl
            try:
                import zoneinfo
                tz = zoneinfo.ZoneInfo("Europe/Minsk")
                today = datetime.now(tz).date()
            except Exception:
                today = date.today()
            user = self.storage.get_staff_user(uid)
            if not user:
                return {
                    "ok": True, "telegram_user_id": uid, "user_records": [],
                    "resolved_role": "", "resolved_mk_teacher_id": "",
                    "teacher_name": "", "status": "not_found",
                    "reason": "no_user_record",
                    "lessons": {"today": 0, "tomorrow": 0, "next_7_days": 0, "next_14_days": 0,
                                "from_teacher_lesson_control": 0, "from_lesson_snapshots": 0},
                    "locations": [], "food_access": False, "class_orders_access": False,
                    "sample_lessons": [],
                }
            role = str(user.get("role") or "")
            status = str(user.get("status") or "")
            mk_teacher_id_raw = str(user.get("mk_teacher_id") or "")
            mk_teacher_id = mk_teacher_id_raw.strip()
            user_record = {
                "source": "staff_users",
                "role": role,
                "is_active": status == "active",
                "mk_teacher_id": mk_teacher_id,
                "full_name": str(user.get("full_name") or ""),
                "mk_teacher_name": str(user.get("mk_teacher_name") or ""),
                "username": str(user.get("username") or ""),
                "created_at": str(user.get("created_at") or ""),
                "updated_at": str(user.get("updated_at") or ""),
            }
            if status != "active":
                reason = "inactive_user"
            elif not mk_teacher_id:
                reason = "no_mk_teacher_id"
            else:
                reason = "ok"
            date_from = today.isoformat()
            date_to_7 = (today + timedelta(days=7)).isoformat()
            date_to_14 = (today + timedelta(days=14)).isoformat()
            date_tomorrow = (today + timedelta(days=1)).isoformat()
            snap_today = snap_tomorrow = snap_7 = snap_14 = ctrl_count = 0
            locations: list[str] = []
            sample_lessons: list[dict] = []
            # Snapshot DB stats — track all IDs seen even without match
            snap_total_in_range = 0
            snap_with_teacher_ids = 0
            snap_unique_ids: list[str] = []  # sample of unique teacher IDs found in snapshots
            if mk_teacher_id:
                with self.storage._connect() as conn:
                    for row in conn.execute(
                        "SELECT lesson_id, lesson_date, lesson_time, group_name, lesson_topic, teacher_ids "
                        "FROM lesson_snapshots WHERE lesson_date>=? AND lesson_date<=? "
                        "ORDER BY lesson_date, lesson_time",
                        (date_from, date_to_14),
                    ).fetchall():
                        snap_total_in_range += 1
                        raw_ids_str = str(row["teacher_ids"] or "")
                        ids_in_snap = [x.strip() for x in raw_ids_str.split(",") if x.strip()]
                        if ids_in_snap:
                            snap_with_teacher_ids += 1
                            for sid in ids_in_snap:
                                if sid not in snap_unique_ids:
                                    snap_unique_ids.append(sid)
                        if mk_teacher_id not in ids_in_snap:
                            continue
                        d = str(row["lesson_date"] or "")
                        snap_14 += 1
                        if d <= date_to_7:
                            snap_7 += 1
                        if d == date_tomorrow:
                            snap_tomorrow += 1
                        if d == date_from:
                            snap_today += 1
                        loc = _nfl(str(row["group_name"] or ""))
                        if loc and loc not in locations:
                            locations.append(loc)
                        if len(sample_lessons) < 5:
                            sample_lessons.append({
                                "lesson_id": str(row["lesson_id"] or ""),
                                "date": d, "time": str(row["lesson_time"] or ""),
                                "title": str(row["lesson_topic"] or ""),
                                "teacher_id": mk_teacher_id,
                                "location_code": loc or "?",
                                "location_name": str(row["group_name"] or ""),
                                "source": "lesson_snapshots",
                            })
                    ctrl_row = conn.execute(
                        "SELECT COUNT(*) as cnt FROM teacher_lesson_control WHERE mk_teacher_id=?",
                        (mk_teacher_id,),
                    ).fetchone()
                    ctrl_count = (ctrl_row["cnt"] if ctrl_row else 0) or 0
                    for row in conn.execute(
                        "SELECT lesson_id, lesson_date, lesson_time, group_name, lesson_topic "
                        "FROM teacher_lesson_control WHERE mk_teacher_id=? AND lesson_date>=? AND lesson_date<=? "
                        "ORDER BY lesson_date LIMIT 3",
                        (mk_teacher_id, date_from, date_to_14),
                    ).fetchall():
                        loc = _nfl(str(row["group_name"] or ""))
                        if loc and loc not in locations:
                            locations.append(loc)
                        sample_lessons.append({
                            "lesson_id": str(row["lesson_id"] or ""),
                            "date": str(row["lesson_date"] or ""), "time": str(row["lesson_time"] or ""),
                            "title": str(row["lesson_topic"] or ""),
                            "teacher_id": mk_teacher_id,
                            "location_code": loc or "?",
                            "location_name": str(row["group_name"] or ""),
                            "source": "teacher_lesson_control",
                        })
            if reason == "ok" and mk_teacher_id:
                if snap_14 == 0 and ctrl_count == 0:
                    reason = "no_lessons_in_snapshots"
                elif snap_14 > 0 and not locations:
                    reason = "lessons_exist_without_location"
            food_enabled = bool(getattr(self.settings, "food_module_enabled", False))
            food_access = food_enabled and bool(mk_teacher_id) and snap_14 > 0 and bool(locations)
            class_orders_access = food_access and role in {"teacher", "methodist", "intern"}
            id_in_any_snapshot = mk_teacher_id in snap_unique_ids if mk_teacher_id else False
            log.info(
                "[teacher-diagnostics] uid=%s role=%s mk_teacher_id=%s status=%s "
                "snap_14=%s ctrl=%s locations=%s reason=%s snap_total=%s id_in_any_snap=%s",
                uid, role, mk_teacher_id, status, snap_14, ctrl_count,
                locations, reason, snap_total_in_range, id_in_any_snapshot,
            )
            return {
                "ok": True, "telegram_user_id": uid, "user_records": [user_record],
                "resolved_role": role, "resolved_mk_teacher_id": mk_teacher_id,
                "mk_teacher_id_raw": mk_teacher_id_raw,
                "mk_teacher_id_has_spaces": mk_teacher_id_raw != mk_teacher_id,
                "teacher_name": user_record["mk_teacher_name"] or user_record["full_name"],
                "status": status,
                "lessons": {
                    "today": snap_today, "tomorrow": snap_tomorrow,
                    "next_7_days": snap_7, "next_14_days": snap_14,
                    "from_teacher_lesson_control": ctrl_count,
                    "from_lesson_snapshots": snap_14,
                },
                "locations": locations, "food_access": food_access,
                "class_orders_access": class_orders_access,
                "reason": reason, "sample_lessons": sample_lessons,
                "snapshot_stats": {
                    "total_in_db_next14": snap_total_in_range,
                    "with_teacher_ids": snap_with_teacher_ids,
                    "unique_ids_sample": snap_unique_ids[:30],
                    "searched_id": mk_teacher_id,
                    "id_found_in_snapshots": id_in_any_snapshot,
                },
            }
        except Exception:
            log.exception("teacher_diagnostics error uid=%s", telegram_user_id)
            return {"ok": False, "error": "Ошибка диагностики (см. лог сервера)", "reason": "server_error"}

    def teacher_diagnostics_refresh(self, auth: dict[str, Any], telegram_user_id: str) -> dict[str, Any]:
        """Trigger safe, non-destructive MoyKlass schedule sync for a specific teacher.
        Budget-controlled: completes within ~20s and always returns a structured JSON response.
        Only accessible to owner/admin/operations."""
        denied = self._require_admin(auth)
        if denied:
            return denied
        try:
            uid = int(telegram_user_id or 0)
        except (ValueError, TypeError):
            return {"ok": False, "error_code": "invalid_uid", "error": "Неверный telegram_user_id"}
        user = self.storage.get_staff_user(uid)
        if not user:
            return {"ok": False, "error_code": "user_not_found", "error": "Пользователь не найден"}
        mk_teacher_id = str(user.get("mk_teacher_id") or "").strip()
        if not mk_teacher_id:
            return {
                "ok": False,
                "error_code": "no_teacher_id",
                "error": "MK teacherId не привязан — обновление невозможно. Сначала привяжите teacherId.",
            }
        today = date.today()
        date_to = today + timedelta(days=45)
        fetch: dict[str, Any] = {}
        try:
            log.info(
                "[moyklass-teacher-sync] start uid=%s mk_teacher_id=%s date_from=%s date_to=%s",
                uid, mk_teacher_id, today.isoformat(), date_to.isoformat(),
            )
            fetch = self.moyklass.get_teacher_lessons_paginated(
                teacher_id=mk_teacher_id,
                date_from=today,
                date_to=date_to,
                max_pages=8,
                per_req_timeout=8,
                total_timeout=18,
            )
        except Exception as exc:
            exc_type = type(exc).__name__
            exc_msg = str(exc)[:300]
            log.exception("[moyklass-teacher-sync] fetch_exception uid=%s mk_teacher_id=%s", uid, mk_teacher_id)
            error_code = "moyklass_timeout" if "timed out" in exc_msg.lower() else "moyklass_api_error"
            return {
                "ok": False,
                "error_code": error_code,
                "error": "МойКласс не ответил на запрос расписания.",
                "refresh": {
                    "ok": False,
                    "error_code": error_code,
                    "stage": "fetch",
                    "total_loaded": 0,
                    "pages_loaded": 0,
                    "exception_type": exc_type,
                    "exception_message": exc_msg,
                },
            }

        total_loaded = fetch.get("total_loaded", 0)
        pages_loaded = fetch.get("pages_loaded", 0)
        strategy_used = fetch.get("strategy_used", "none")
        matched_by_id = fetch.get("matched_by_id", 0)
        total_in_range = fetch.get("total_in_range", 0)
        field_stats = fetch.get("field_stats", {})
        reason_if_zero = fetch.get("reason_if_zero", "")
        timed_out = bool(fetch.get("timed_out", False))
        stage_reached = fetch.get("stage_reached", "unknown")
        elapsed_ms = fetch.get("elapsed_ms", 0)
        pagination_attempts = fetch.get("pagination_attempts", [])
        last_error = fetch.get("last_error", "")
        last_status = fetch.get("last_status", 0)
        teacher_lessons = fetch.get("matched", [])

        log.info(
            "[moyklass-teacher-sync] fetch done uid=%s mk_teacher_id=%s total=%s pages=%s "
            "in_range=%s by_id=%s strategy=%s timed_out=%s elapsed_ms=%s reason=%s",
            uid, mk_teacher_id, total_loaded, pages_loaded,
            total_in_range, matched_by_id, strategy_used, timed_out, elapsed_ms, reason_if_zero,
        )

        # If MoyKlass returned nothing at all — treat as API error
        if total_loaded == 0 and not timed_out:
            error_code = "moyklass_api_error" if last_status and last_status >= 400 else "no_lessons_from_api"
            log.warning(
                "[moyklass-teacher-sync] empty_response uid=%s last_status=%s last_error=%s",
                uid, last_status, last_error,
            )
            diag_partial = self.teacher_diagnostics(auth, str(uid))
            diag_partial["refresh"] = {
                "ok": False,
                "error_code": error_code,
                "synced_for_teacher": 0,
                "total_loaded": 0,
                "pages_loaded": 0,
                "stage": stage_reached,
                "strategy_used": strategy_used,
                "date_range": fetch.get("date_range", {}),
                "raw_teacher_field_stats": field_stats,
                "reason_if_zero": reason_if_zero,
                "last_status": last_status,
                "last_error": last_error,
                "pagination_attempts": pagination_attempts,
                "elapsed_ms": elapsed_ms,
            }
            return diag_partial

        # Name-based mismatch detection
        teacher_name_stored = str(user.get("mk_teacher_name") or "").strip().lower()
        name_matched_ids: list[str] = fetch.get("name_matched_ids") or []
        # Re-check against stored name (fetch returns all IDs; narrow by name match here)
        id_mismatch_warning = ""
        if teacher_name_stored and matched_by_id == 0 and total_in_range > 0:
            all_in_range = fetch.get("all_in_range", [])
            name_ids_from_range: list[str] = []
            for item in all_in_range:
                for field in ("teachers", "teacher", "staff"):
                    val = item.get(field)
                    objs = val if isinstance(val, list) else ([val] if isinstance(val, dict) else [])
                    for obj in objs:
                        if isinstance(obj, dict):
                            obj_name = str(
                                obj.get("name") or obj.get("fullName") or obj.get("fio") or ""
                            ).strip().lower()
                            if obj_name and (obj_name in teacher_name_stored or teacher_name_stored in obj_name):
                                obj_id = str(
                                    obj.get("id") or obj.get("userId") or obj.get("teacherId") or ""
                                ).strip()
                                if obj_id and obj_id not in name_ids_from_range:
                                    name_ids_from_range.append(obj_id)
            if name_ids_from_range:
                name_matched_ids = name_ids_from_range
                id_mismatch_warning = (
                    f"Имя '{user.get('mk_teacher_name')}' найдено в расписании МойКласс, "
                    f"но ID в занятиях: {', '.join(name_ids_from_range)}, "
                    f"а сохранённый teacherId={mk_teacher_id}. "
                    f"Возможно, нужно обновить teacherId."
                )
                log.warning(
                    "[moyklass-teacher-sync] id_mismatch uid=%s name=%s stored_id=%s found_ids=%s",
                    uid, teacher_name_stored, mk_teacher_id, name_ids_from_range,
                )

        # Persist results (save even partial/timeout results)
        snaps_written = 0
        try:
            for item in (fetch.get("all_in_range") or teacher_lessons):
                lesson_id = str(_pick(item, ("id", "lessonId")) or "").strip()
                if not lesson_id:
                    continue
                snap = self._schedule_snapshot_from_item(item)
                self.storage.upsert_lesson_snapshot(lesson_id, snap)
                snaps_written += 1
        except Exception:
            log.exception("[moyklass-teacher-sync] snapshot_write_error uid=%s", uid)

        synced = 0
        try:
            for item in teacher_lessons:
                lesson_id = str(_pick(item, ("id", "lessonId")) or "").strip()
                if not lesson_id:
                    continue
                snap = self._schedule_snapshot_from_item(item)
                synced += 1
                self.storage.upsert_teacher_lesson_control(
                    lesson_id,
                    lesson_date=snap.get("lesson_date") or "",
                    lesson_time=snap.get("lesson_time") or "",
                    group_name=snap.get("group_name") or "",
                    lesson_topic=snap.get("lesson_topic") or "",
                    teacher_name=snap.get("teacher_names") or "",
                    mk_teacher_id=mk_teacher_id,
                    teacher_user_id=uid,
                    filial_name=snap.get("filial_name") or "",
                    lesson_room_id=snap.get("lesson_room_id") or "",
                )
        except Exception:
            log.exception("[moyklass-teacher-sync] lesson_control_write_error uid=%s", uid)

        log.info(
            "[moyklass-teacher-sync] saved uid=%s mk_teacher_id=%s synced=%s snaps=%s timed_out=%s",
            uid, mk_teacher_id, synced, snaps_written, timed_out,
        )

        # Determine final error_code for refresh block
        if timed_out:
            refresh_error_code = "moyklass_timeout"
        elif matched_by_id == 0 and total_in_range == 0:
            refresh_error_code = "no_lessons_in_date_range"
        elif matched_by_id == 0:
            refresh_error_code = "teacher_not_found_in_lessons"
        else:
            refresh_error_code = ""

        try:
            diag = self.teacher_diagnostics(auth, str(uid))
        except Exception:
            log.exception("[moyklass-teacher-sync] post_diag_error uid=%s", uid)
            diag = {"ok": True, "telegram_user_id": uid, "reason": "diagnostics_unavailable"}

        diag["refresh"] = {
            "ok": synced > 0,
            "error_code": refresh_error_code,
            "synced_for_teacher": synced,
            "matched_by_id": matched_by_id,
            "total_loaded": total_loaded,
            "total_in_range": total_in_range,
            "pages_loaded": pages_loaded,
            "strategy_used": strategy_used,
            "stage": stage_reached,
            "date_range": fetch.get("date_range", {}),
            "raw_teacher_field_stats": field_stats,
            "name_matched_ids": name_matched_ids,
            "id_mismatch_warning": id_mismatch_warning,
            "reason_if_zero": reason_if_zero,
            "timed_out": timed_out,
            "elapsed_ms": elapsed_ms,
            "pagination_attempts": pagination_attempts,
            "last_error": last_error,
            "last_status": last_status,
        }
        return diag

    def admin_all_tasks(self, auth: dict[str, Any]) -> dict[str, Any]:
        denied = self._require_admin_tab(auth, "tasks")
        if denied:
            return denied
        return {"ok": True, "items": self.storage.list_teacher_tasks(user_id=None, status="open", limit=300)}

    def admin_notifications(self, auth: dict[str, Any]) -> dict[str, Any]:
        denied = self._require_admin_tab(auth, "notifications")
        if denied:
            return denied
        return {"ok": True, "items": self.storage.list_notification_log(limit=100)}

    def admin_prep_results(self, auth: dict[str, Any]) -> dict[str, Any]:
        denied = self._require_admin_tab(auth, "prep-results")
        if denied:
            return denied
        return {"ok": True, "items": self.storage.list_teacher_prep_result_files(status="all", limit=200)}

    # --- Intern endpoints ---

    def intern_track(self, auth: dict[str, Any]) -> dict[str, Any]:
        role = self._role_for_user(int(auth["user_id"]))
        if role not in INTERN_ROLES:
            return {"ok": False, "error": "Доступ только для стажёров."}
        track = self.storage.get_intern_track(int(auth["user_id"]))
        return {"ok": True, "track": track}

    def intern_sign_up_observation(self, auth: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        role = self._role_for_user(int(auth["user_id"]))
        if role not in INTERN_ROLES:
            return {"ok": False, "error": "Доступ только для стажёров."}
        uid = int(auth["user_id"])
        obs = self.storage.add_intern_observation(
            uid,
            mk_lesson_id=str(body.get("mk_lesson_id") or "").strip(),
            lesson_title=str(body.get("lesson_title") or "").strip(),
            lesson_date=str(body.get("lesson_date") or "").strip(),
            lesson_time=str(body.get("lesson_time") or "").strip(),
            location=str(body.get("location") or "").strip(),
        )
        if not obs:
            return {"ok": False, "error": "Не удалось записаться на наблюдение."}
        return {"ok": True, "observation": obs}

    def intern_submit_observation(self, auth: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        role = self._role_for_user(int(auth["user_id"]))
        if role not in INTERN_ROLES:
            return {"ok": False, "error": "Доступ только для стажёров."}
        uid = int(auth["user_id"])
        obs_id = str(body.get("obs_id") or "").strip()
        comment = str(body.get("comment") or "").strip()
        if not obs_id or not comment:
            return {"ok": False, "error": "Укажите ID наблюдения и комментарий."}
        obs = self.storage.get_intern_observation(obs_id)
        if not obs or int(obs.get("intern_user_id") or 0) != uid:
            return {"ok": False, "error": "Наблюдение не найдено."}
        updated = self.storage.set_intern_observation_comment(obs_id, comment)
        if not updated:
            return {"ok": False, "error": "Не удалось сохранить комментарий."}
        track = self.storage.get_intern_track(uid)
        return {"ok": True, "observation": updated, "track": track}

    def intern_submit_work(self, auth: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        role = self._role_for_user(int(auth["user_id"]))
        if role not in INTERN_ROLES:
            return {"ok": False, "error": "Доступ только для стажёров."}
        uid = int(auth["user_id"])
        track = self.storage.get_intern_track(uid)
        if track.get("current_step", 0) not in (2, 3):
            return {"ok": False, "error": "Загрузка работы недоступна на этом шаге."}
        file_name = str(body.get("file_name") or "").strip()
        description = str(body.get("description") or "").strip()
        if not file_name and not description:
            return {"ok": False, "error": "Укажите описание или название файла."}
        stored_ref = str(body.get("file_id") or "").strip() or "manual_submission"
        work = self.storage.add_intern_work(
            uid,
            file_name=file_name or description[:240],
            stored_path=stored_ref,
            mime_type=str(body.get("mime_type") or ""),
        )
        if not work:
            return {"ok": False, "error": "Не удалось отправить работу."}
        intern_user = self.storage.get_staff_user(uid) or {}
        intern_name = intern_user.get("full_name") or intern_user.get("username") or f"#{uid}"
        self._intern_notify_admins(f"📎 Стажёр {intern_name} отправил работу на проверку: {file_name or description[:100]}")
        return {"ok": True, "work": work, "track": self.storage.get_intern_track(uid)}

    def intern_book_demo(self, auth: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        role = self._role_for_user(int(auth["user_id"]))
        if role not in INTERN_ROLES:
            return {"ok": False, "error": "Доступ только для стажёров."}
        uid = int(auth["user_id"])
        track = self.storage.get_intern_track(uid)
        if track.get("current_step", 0) != 4:
            return {"ok": False, "error": "Запись на пробное недоступна на этом шаге."}
        booking = self.storage.add_intern_demo_booking(
            uid,
            demo_date=str(body.get("demo_date") or "").strip(),
            demo_time=str(body.get("demo_time") or "").strip(),
            location=str(body.get("location") or "").strip(),
            slot_ref=str(body.get("note") or "").strip()[:200],
        )
        if not booking:
            return {"ok": False, "error": "Не удалось записаться на пробное."}
        return {"ok": True, "booking": booking, "track": self.storage.get_intern_track(uid)}

    def intern_submit_demo_feedback(self, auth: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        role = self._role_for_user(int(auth["user_id"]))
        if role not in INTERN_ROLES:
            return {"ok": False, "error": "Доступ только для стажёров."}
        uid = int(auth["user_id"])
        track = self.storage.get_intern_track(uid)
        booking = track.get("latest_booking")
        if not booking:
            return {"ok": False, "error": "Запись на пробное занятие не найдена."}
        if int(booking.get("intern_user_id") or 0) != uid:
            return {"ok": False, "error": "Это не ваша запись на пробное занятие."}
        if str(booking.get("status") or "") in {"passed", "failed"}:
            return {"ok": False, "error": "Финальное решение уже принято. Самооценку нельзя изменить."}
        result = self.storage.save_intern_demo_feedback(
            booking.get("id"),
            how=str(body.get("how") or "").strip(),
            plus=str(body.get("plus") or "").strip(),
            minus=str(body.get("minus") or "").strip(),
            improve=str(body.get("improve") or "").strip(),
            comment=str(body.get("comment") or "").strip(),
        )
        if not result:
            return {"ok": False, "error": "Не удалось сохранить самооценку."}
        intern_user = self.storage.get_staff_user(uid) or {}
        intern_name = intern_user.get("full_name") or intern_user.get("username") or f"#{uid}"
        self._intern_notify_admins(f"📋 Стажёр {intern_name} отправил самооценку после пробного занятия.")
        return {"ok": True, "track": self.storage.get_intern_track(uid)}

    def intern_upcoming_lessons(self, auth: dict[str, Any]) -> dict[str, Any]:
        role = self._role_for_user(int(auth["user_id"]))
        if role not in INTERN_ROLES:
            return {"ok": False, "error": "Доступ только для стажёров."}
        today = date.today()
        cutoff = (today + timedelta(days=2)).isoformat()
        today_str = today.isoformat()
        all_snapshots = self.storage.list_lesson_snapshots(limit=200)
        upcoming = [
            s for s in all_snapshots
            if today_str <= (s.get("lesson_date") or "") <= cutoff
        ]
        upcoming.sort(key=lambda s: (s.get("lesson_date") or "", s.get("lesson_time") or ""))
        return {"ok": True, "lessons": upcoming}

    def upload_intern_work(self, auth: dict[str, Any], fields: dict[str, str], files: dict[str, dict[str, Any]]) -> dict[str, Any]:
        role = self._role_for_user(int(auth["user_id"]))
        if role not in INTERN_ROLES:
            return {"ok": False, "error": "Доступ только для стажёров."}
        uid = int(auth["user_id"])
        file = files.get("file")
        if not file or not file.get("content"):
            return {"ok": False, "error": "Файл не прикреплён."}
        content = file.get("content") or b""
        if len(content) > 40 * 1024 * 1024:
            return {"ok": False, "error": "Файл слишком большой. Максимум 40 МБ."}
        original_name = _safe_filename(str(file.get("filename") or "work.bin"))
        work_dir = BASE_DIR / "data" / "intern_works" / str(uid)
        work_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stored_name = f"{stamp}_{original_name}"
        stored_path = work_dir / stored_name
        stored_path.write_bytes(content)
        work = self.storage.add_intern_work(
            intern_user_id=uid,
            file_name=original_name,
            stored_path=str(stored_path),
            mime_type=str(file.get("content_type") or "application/octet-stream"),
            size_bytes=len(content),
        )
        if not work:
            return {"ok": False, "error": "Не удалось сохранить работу."}
        intern_user = self.storage.get_staff_user(uid) or {}
        intern_name = intern_user.get("full_name") or intern_user.get("username") or f"#{uid}"
        self._intern_notify_admins(f"📎 Стажёр {intern_name} отправил работу на проверку: {original_name}")
        return {"ok": True, "work": work, "track": self.storage.get_intern_track(uid)}

    def intern_work_download(self, auth: dict[str, Any], work_id: str) -> dict[str, Any]:
        denied = self._require_admin_tab(auth, "interns")
        if denied:
            return denied
        try:
            wid = int(work_id)
        except (ValueError, TypeError):
            return {"ok": False, "error": "Неверный ID работы."}
        work = self.storage.get_intern_work(wid)
        if not work:
            return {"ok": False, "error": "Работа не найдена."}
        stored = str(work.get("stored_path") or "")
        if not stored:
            return {"ok": False, "error": "Файл не сохранён на сервере."}
        path = Path(stored)
        if not path.is_file():
            return {"ok": False, "error": "Файл не найден на диске."}
        mime = str(work.get("mime_type") or "") or (mimetypes.guess_type(path.name)[0] or "application/octet-stream")
        return {"ok": True, "path": str(path), "filename": str(work.get("file_name") or path.name), "mimeType": mime}

    def intern_test_reset_action(self, auth: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        denied = self._require_test_access(auth)
        if denied:
            return denied
        intern_uid = int(body.get("intern_user_id") or auth["user_id"])
        self.storage.intern_test_reset(intern_uid)
        return {"ok": True, "track": self.storage.get_intern_track(intern_uid)}

    def intern_test_set_stage_action(self, auth: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        denied = self._require_test_access(auth)
        if denied:
            return denied
        intern_uid = int(body.get("intern_user_id") or auth["user_id"])
        stage = str(body.get("stage") or "start").strip().lower()
        try:
            self.storage.intern_test_set_stage(intern_uid, stage, reviewer_user_id=int(auth["user_id"]))
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "stage": stage, "track": self.storage.get_intern_track(intern_uid)}

    def admin_interns(self, auth: dict[str, Any]) -> dict[str, Any]:
        denied = self._require_admin_tab(auth, "interns")
        if denied:
            return denied
        caller_uid = int(auth["user_id"])
        test_mode_enabled = bool(getattr(self.settings, "web_app_test_roles", False))

        # Build a lookup of all staff users by user_id
        all_staff = self.storage.list_staff_users(limit=500)
        staff_by_uid: dict[int, dict] = {}
        for s in all_staff:
            uid = int(s.get("user_id") or 0)
            if uid:
                staff_by_uid[uid] = s

        # Source 1: staff_users with role=intern
        intern_role_uids: set[int] = {uid for uid, s in staff_by_uid.items() if s.get("role") == "intern"}

        # Source 2: any user_id that has records in intern activity tables
        activity_uids: set[int] = self.storage.list_all_intern_user_ids()

        # Source 3: current caller if test-mode is enabled and they're acting as intern
        caller_is_test_intern = False
        if test_mode_enabled and self._can_use_role_test(caller_uid):
            caller_test = self.storage.get_staff_test_mode(caller_uid)
            if caller_test.get("enabled") and str(caller_test.get("role") or "") == "intern":
                activity_uids.add(caller_uid)
                caller_is_test_intern = True

        all_intern_uids = intern_role_uids | activity_uids

        result: list[dict] = []
        for uid in sorted(all_intern_uids):
            s = staff_by_uid.get(uid) or {}
            real_role = str(s.get("role") or "")
            is_test = uid not in intern_role_uids and uid in activity_uids
            track = self.storage.get_intern_track(uid)
            result.append({
                "user_id": uid,
                "full_name": s.get("full_name") or s.get("username") or f"#{uid}",
                "username": s.get("username") or "",
                "mk_teacher_id": s.get("mk_teacher_id") or "",
                "mk_teacher_name": s.get("mk_teacher_name") or "",
                "real_role": real_role,
                "is_test_intern": is_test,
                "current_step": track.get("current_step", 1),
                "obs_count": track.get("obs_count", 0),
                "obs_needed": track.get("obs_needed", 2),
                "admitted": track.get("admitted", False),
                "observations": track.get("observations") or [],
                "works": track.get("works") or [],
                "latest_work": track.get("latest_work"),
                "latest_booking": track.get("latest_booking"),
                "latest_work_status": (track.get("latest_work") or {}).get("status") or "",
                "latest_booking_status": (track.get("latest_booking") or {}).get("status") or "",
            })

        pending_works = self.storage.list_intern_works(status="submitted", limit=50)
        all_demos = self.storage.list_intern_demo_bookings(status="all", limit=100)
        pending_demos = [b for b in all_demos if str(b.get("status") or "") in {"requested", "conducted", "approved"}]

        response: dict = {"ok": True, "items": result, "pending_works": pending_works, "pending_demos": pending_demos}
        if test_mode_enabled:
            response["debug"] = {
                "internStaffCount": len(intern_role_uids),
                "activityUserCount": len(activity_uids),
                "resolvedInternUsers": len(all_intern_uids),
                "callerIsTestIntern": caller_is_test_intern,
            }
        return response

    def admin_set_user_role(self, auth: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        caller_uid = int(auth["user_id"])
        caller_real = self._base_role_for_user(caller_uid)
        if caller_real not in FULL_ADMIN_ROLES:
            return {"ok": False, "error": "Смена роли доступна только владельцу и операционному менеджеру."}
        user_id = _safe_int(body.get("user_id") or 0)
        role = str(body.get("role") or "").strip().lower()
        # Normalize restaurant alias to kitchen
        if role == "restaurant":
            role = "kitchen"
        ALLOWED = {"teacher", "methodist", "intern", "client_manager", "director", "operations", "kitchen", "admin", "other"}
        # owner can only be granted by another owner
        if role == "owner":
            if caller_real != "owner":
                return {"ok": False, "error": "Назначить роль владельца может только другой владелец."}
            ALLOWED = ALLOWED | {"owner"}
        if not user_id or role not in ALLOWED:
            return {"ok": False, "error": f"Укажите user_id и допустимую роль. Получено: «{role}»"}
        if user_id == caller_uid:
            return {"ok": False, "error": "Нельзя изменить собственную роль через этот интерфейс."}
        target = self.storage.get_staff_user(user_id)
        if not target:
            return {"ok": False, "error": "Сотрудник не найден."}
        old_role = str(target.get("role") or "")
        if old_role == "owner" and caller_real != "owner":
            return {"ok": False, "error": "Нельзя изменить роль владельца без прав владельца."}
        self.storage.set_staff_role(user_id, role)
        log.info(
            "admin_staff_role_changed admin_user_id=%s target_telegram_user_id=%s old_role=%s new_role=%s",
            caller_uid, user_id, old_role, role,
        )
        return {"ok": True, "telegram_user_id": user_id, "old_role": old_role, "new_role": role}

    def admin_deactivate_staff(self, auth: dict[str, Any], target_uid_str: str) -> dict[str, Any]:
        caller_uid = int(auth["user_id"])
        caller_real = self._base_role_for_user(caller_uid)
        if caller_real not in FULL_ADMIN_ROLES:
            return {"ok": False, "error": "Управление доступом сотрудников доступно только владельцу и операционному менеджеру."}
        target_uid = _safe_int(target_uid_str)
        if not target_uid:
            return {"ok": False, "error": "Неверный user_id."}
        if target_uid == caller_uid:
            return {"ok": False, "error": "Нельзя отключить доступ самому себе."}
        target = self.storage.get_staff_user(target_uid)
        if not target:
            return {"ok": False, "error": "Сотрудник не найден."}
        target_role = str(target.get("role") or "")
        if target_role == "owner":
            if caller_real != "owner":
                return {"ok": False, "error": "Нельзя отключить владельца без прав владельца."}
            active_owners = [u for u in self.storage.list_staff_users(limit=500)
                             if u.get("role") == "owner" and u.get("status") == "active"]
            if len(active_owners) <= 1:
                return {"ok": False, "error": "Нельзя отключить последнего владельца."}
        old_status = str(target.get("status") or "active")
        self.storage.deactivate_staff_user(target_uid)
        log.info(
            "admin_staff_deactivated admin_user_id=%s target_telegram_user_id=%s old_role=%s old_status=%s new_status=inactive",
            caller_uid, target_uid, target_role, old_status,
        )
        return {"ok": True, "telegram_user_id": target_uid, "status": "inactive"}

    def admin_activate_staff(self, auth: dict[str, Any], target_uid_str: str) -> dict[str, Any]:
        caller_uid = int(auth["user_id"])
        caller_real = self._base_role_for_user(caller_uid)
        if caller_real not in FULL_ADMIN_ROLES:
            return {"ok": False, "error": "Управление доступом сотрудников доступно только владельцу и операционному менеджеру."}
        target_uid = _safe_int(target_uid_str)
        if not target_uid:
            return {"ok": False, "error": "Неверный user_id."}
        target = self.storage.get_staff_user(target_uid)
        if not target:
            return {"ok": False, "error": "Сотрудник не найден."}
        target_role = str(target.get("role") or "")
        self.storage.activate_staff_user(target_uid)
        log.info(
            "admin_staff_activated admin_user_id=%s target_telegram_user_id=%s role=%s",
            caller_uid, target_uid, target_role,
        )
        return {"ok": True, "telegram_user_id": target_uid, "status": "active"}

    def admin_sync_mk_name(self, auth: dict[str, Any], target_uid_str: str) -> dict[str, Any]:
        """Look up teacher name from MoyKlass lesson data by mk_teacher_id and persist it."""
        caller_uid = int(auth["user_id"])
        caller_real = self._base_role_for_user(caller_uid)
        if caller_real not in FULL_ADMIN_ROLES:
            return {"ok": False, "error": "Управление сотрудниками доступно только владельцу и операционному менеджеру."}
        target_uid = _safe_int(target_uid_str)
        if not target_uid:
            return {"ok": False, "error": "Неверный user_id."}
        target = self.storage.get_staff_user(target_uid)
        if not target:
            return {"ok": False, "error": "Сотрудник не найден."}
        mk_teacher_id = str(target.get("mk_teacher_id") or "").strip()
        if not mk_teacher_id:
            return {"ok": False, "error": "У сотрудника не привязан MK teacherId."}
        names = self.storage.get_teacher_name_by_mk_id(mk_teacher_id)
        if not names:
            return {"ok": False, "error": f"Не удалось найти преподавателя с MK teacherId={mk_teacher_id} в кэше МойКласс. Убедитесь, что синхронизация с МойКласс выполнялась."}
        if len(names) > 1:
            return {"ok": False, "error": f"Найдено несколько вариантов имён: {', '.join(names[:3])}. Уточните вручную.", "names": names}
        new_name = names[0]
        old_name = str(target.get("mk_teacher_name") or target.get("full_name") or "").strip()
        self.storage.update_staff_mk_teacher_name(target_uid, new_name)
        log.info(
            "admin_staff_mk_name_synced admin_user_id=%s target_telegram_user_id=%s mk_teacher_id=%s old_name=%s new_name=%s",
            caller_uid, target_uid, mk_teacher_id, old_name, new_name,
        )
        return {"ok": True, "telegram_user_id": target_uid, "mk_teacher_id": mk_teacher_id,
                "old_name": old_name, "new_name": new_name}

    def admin_moyklass_teachers(self, auth: dict[str, Any], q: str = "", include_with_no_lessons: bool = False) -> dict[str, Any]:
        """Return list of teachers found in MoyKlass (from lessons + direct endpoints).
        Admin-only. Marks which IDs are already linked to staff users."""
        denied = self._require_admin(auth)
        if denied:
            return denied
        try:
            today = date.today()
            fetch = self.moyklass.get_moyklass_teachers(
                date_from=today - timedelta(days=1),
                date_to=today + timedelta(days=45),
                per_req_timeout=8,
                total_timeout=20,
            )
            if not fetch.get("ok"):
                return {"ok": False, "error": "Не удалось получить данные из МойКласс.", "detail": str(fetch)}
            teachers = fetch.get("teachers") or []
            # Build lookup: mk_teacher_id → {user_id, role, display_name}
            linked: dict[str, dict] = {}
            for su in self.storage.list_staff_users(limit=500):
                mid = str(su.get("mk_teacher_id") or "").strip()
                if mid:
                    linked[mid] = {
                        "user_id": int(su.get("user_id") or 0),
                        "role": str(su.get("role") or ""),
                        "name": str(su.get("mk_teacher_name") or su.get("full_name") or su.get("username") or "").strip(),
                    }
            # Filter by name search
            q_low = (q or "").strip().lower()
            result_list = []
            for t in teachers:
                if q_low and q_low not in str(t.get("name") or "").lower():
                    continue
                if not include_with_no_lessons and t.get("lesson_count", 0) == 0 and t.get("source") != "direct_api":
                    continue
                tid = str(t.get("id") or "").strip()
                lnk = linked.get(tid)
                result_list.append({
                    **t,
                    "already_linked_to": lnk["user_id"] if lnk else None,
                    "already_linked_role": lnk["role"] if lnk else None,
                    "already_linked_name": lnk["name"] if lnk else None,
                })
            log.info(
                "[moyklass-teacher-picker] uid=%s q=%r total=%s returned=%s timed_out=%s",
                auth.get("user_id"), q, fetch.get("total"), len(result_list), fetch.get("timed_out"),
            )
            return {
                "ok": True,
                "teachers": result_list,
                "total": len(result_list),
                "date_range": fetch.get("date_range", {}),
                "source_used": fetch.get("source_used", ""),
                "timed_out": fetch.get("timed_out", False),
                "elapsed_ms": fetch.get("elapsed_ms", 0),
            }
        except Exception:
            log.exception("admin_moyklass_teachers error")
            return {"ok": False, "error": "Ошибка при получении списка преподавателей МойКласс."}

    def admin_link_moyklass_teacher(self, auth: dict[str, Any], target_uid_str: str, body: dict[str, Any]) -> dict[str, Any]:
        """Link a staff user to a MoyKlass teacher ID chosen from the picker.
        Only owner/admin/operations."""
        caller_uid = int(auth["user_id"])
        caller_real = self._base_role_for_user(caller_uid)
        if caller_real not in FULL_ADMIN_ROLES:
            return {"ok": False, "error": "Управление привязкой преподавателей доступно только владельцу и операционному менеджеру."}
        target_uid = _safe_int(target_uid_str)
        if not target_uid:
            return {"ok": False, "error": "Неверный telegram_user_id."}
        target = self.storage.get_staff_user(target_uid)
        if not target:
            return {"ok": False, "error": "Сотрудник не найден."}
        new_mk_id = str(body.get("mk_teacher_id") or "").strip()
        new_mk_name = str(body.get("mk_teacher_name") or "").strip()
        if not new_mk_id:
            return {"ok": False, "error": "mk_teacher_id не указан."}
        old_mk_id = str(target.get("mk_teacher_id") or "").strip()
        old_mk_name = str(target.get("mk_teacher_name") or "").strip()
        source = str(body.get("source") or "picker")
        self.storage.set_staff_mk_teacher(target_uid, new_mk_id, new_mk_name)
        log.info(
            "[moyklass-teacher-picker] linked caller=%s target=%s old_id=%s new_id=%s new_name=%s source=%s",
            caller_uid, target_uid, old_mk_id, new_mk_id, new_mk_name, source,
        )
        return {
            "ok": True,
            "telegram_user_id": target_uid,
            "old_mk_teacher_id": old_mk_id,
            "old_mk_teacher_name": old_mk_name,
            "new_mk_teacher_id": new_mk_id,
            "new_mk_teacher_name": new_mk_name,
            "source": source,
        }

    def admin_moyklass_staff_link(self, auth: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        """Reverse-direction binding: MoyKlass teacher → Telegram user_id + role.
        Creates or updates the staff_users record in one call.
        Endpoint: POST /api/admin/moyklass/staff-link"""
        caller_uid = int(auth["user_id"])
        caller_real = self._base_role_for_user(caller_uid)
        if caller_real not in FULL_ADMIN_ROLES:
            return {"ok": False, "error": "Управление привязкой доступно только владельцу, администратору и операционному менеджеру."}
        mk_teacher_id = str(body.get("mk_teacher_id") or "").strip()
        mk_teacher_name = str(body.get("mk_teacher_name") or "").strip()
        telegram_user_id = _safe_int(body.get("telegram_user_id") or 0)
        role = str(body.get("role") or "").strip().lower()
        force = bool(body.get("force"))
        if not mk_teacher_id:
            return {"ok": False, "error": "mk_teacher_id не указан."}
        if not telegram_user_id:
            return {"ok": False, "error": "Введите числовой Telegram user_id (не @username)."}
        ALLOWED: set[str] = {"teacher", "methodist", "intern", "client_manager", "director", "operations", "kitchen", "admin", "other"}
        if role == "owner":
            if caller_real != "owner":
                return {"ok": False, "error": "Назначить роль владельца может только другой владелец."}
            ALLOWED = ALLOWED | {"owner"}
        if not role or role not in ALLOWED:
            return {"ok": False, "error": f"Выберите допустимую роль. Получено: «{role}»"}
        # Conflict: this Telegram user_id already linked to a DIFFERENT MK teacher
        existing = self.storage.get_staff_user(telegram_user_id)
        old_mk_id = str(existing.get("mk_teacher_id") or "").strip() if existing else ""
        old_role = str(existing.get("role") or "") if existing else ""
        if old_mk_id and old_mk_id != mk_teacher_id and not force:
            old_name = str(existing.get("mk_teacher_name") or existing.get("full_name") or "").strip() if existing else ""
            return {
                "ok": False,
                "conflict": True,
                "conflict_type": "telegram_user_id",
                "error": f"Telegram user_id {telegram_user_id} уже привязан к МойКласс ID {old_mk_id} ({old_name}). Передайте force=true для перепривязки.",
                "existing_mk_teacher_id": old_mk_id,
                "existing_mk_teacher_name": old_name,
            }
        # Conflict: this MK teacher already linked to a DIFFERENT Telegram user
        for su in self.storage.list_staff_users(limit=500):
            existing_uid = int(su.get("user_id") or 0)
            if str(su.get("mk_teacher_id") or "").strip() == mk_teacher_id and existing_uid and existing_uid != telegram_user_id:
                if not force:
                    return {
                        "ok": False,
                        "conflict": True,
                        "conflict_type": "mk_teacher_id",
                        "error": f"МойКласс ID {mk_teacher_id} уже привязан к другому Telegram-пользователю (ID: {existing_uid}). Передайте force=true для перепривязки.",
                        "existing_telegram_user_id": existing_uid,
                    }
                break
        self.storage.set_staff_mk_teacher(telegram_user_id, mk_teacher_id, mk_teacher_name)
        self.storage.set_staff_role(telegram_user_id, role)
        log.info(
            "[moyklass-staff-link] caller=%s tg=%s mk_id=%s mk_name=%r role=%s old_mk_id=%s old_role=%s was_new=%s",
            caller_uid, telegram_user_id, mk_teacher_id, mk_teacher_name, role, old_mk_id, old_role, not bool(existing),
        )
        return {
            "ok": True,
            "telegram_user_id": telegram_user_id,
            "mk_teacher_id": mk_teacher_id,
            "mk_teacher_name": mk_teacher_name,
            "role": role,
            "was_new": not bool(existing),
        }

    def admin_unlink_teacher(self, auth: dict[str, Any], target_uid_str: str) -> dict[str, Any]:
        """Remove mk_teacher_id / mk_teacher_name link from a staff user."""
        caller_uid = int(auth["user_id"])
        caller_real = self._base_role_for_user(caller_uid)
        if caller_real not in FULL_ADMIN_ROLES:
            return {"ok": False, "error": "Управление сотрудниками доступно только владельцу и операционному менеджеру."}
        target_uid = _safe_int(target_uid_str)
        if not target_uid:
            return {"ok": False, "error": "Неверный user_id."}
        target = self.storage.get_staff_user(target_uid)
        if not target:
            return {"ok": False, "error": "Сотрудник не найден."}
        old_mk_teacher_id = str(target.get("mk_teacher_id") or "").strip()
        role = str(target.get("role") or "")
        if not old_mk_teacher_id:
            return {"ok": False, "error": "У сотрудника нет привязанного MK teacherId."}
        self.storage.clear_staff_mk_teacher(target_uid)
        log.info(
            "admin_staff_teacher_unlinked admin_user_id=%s target_telegram_user_id=%s old_mk_teacher_id=%s role=%s",
            caller_uid, target_uid, old_mk_teacher_id, role,
        )
        warnings: list[str] = []
        if role == "teacher":
            warnings.append("У пользователя роль Преподаватель, но MK teacherId отвязан. При необходимости смените роль.")
        return {"ok": True, "telegram_user_id": target_uid, "old_mk_teacher_id": old_mk_teacher_id, "warnings": warnings}

    def admin_intern_review_work(self, auth: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        denied = self._require_admin_tab(auth, "interns")
        if denied:
            return denied
        work_id = str(body.get("work_id") or "").strip()
        status = str(body.get("status") or "").strip().lower()
        comment = str(body.get("comment") or "").strip()
        if not work_id or status not in {"accepted", "rejected"}:
            return {"ok": False, "error": "Укажите work_id и статус (accepted/rejected)."}
        if status == "rejected" and not comment:
            return {"ok": False, "error": "Для отклонения нужен комментарий стажёру."}
        result = self.storage.review_intern_work(work_id, int(auth["user_id"]), status, comment)
        if not result:
            return {"ok": False, "error": "Работа не найдена."}
        intern_uid = int(result.get("intern_user_id") or 0)
        if intern_uid:
            if status == "accepted":
                self._intern_notify_user(intern_uid, "✅ Ваша подготовительная работа принята. Теперь можно записаться на пробное занятие.")
            else:
                msg = "❌ Работа отправлена на доработку."
                if comment:
                    msg += f"\n\nКомментарий: {comment}"
                self._intern_notify_user(intern_uid, msg)
        return {"ok": True, "work": result}

    def admin_intern_review_demo(self, auth: dict[str, Any], body: dict[str, Any]) -> dict[str, Any]:
        denied = self._require_admin_tab(auth, "interns")
        if denied:
            return denied
        booking_id = str(body.get("booking_id") or "").strip()
        outcome = str(body.get("outcome") or "").strip().lower()
        comment = str(body.get("comment") or "").strip()
        if not booking_id or outcome not in {"passed", "failed"}:
            return {"ok": False, "error": "Укажите booking_id и outcome (passed/failed)."}
        if outcome == "failed" and not comment:
            return {"ok": False, "error": "Для отклонения нужен комментарий стажёру."}
        result = self.storage.review_intern_demo_booking(booking_id, int(auth["user_id"]), outcome, comment)
        if not result:
            return {"ok": False, "error": "Запись на пробное не найдена."}
        intern_uid = int(result.get("intern_user_id") or 0)
        if intern_uid:
            if outcome == "passed":
                self._intern_notify_user(intern_uid, "🎉 Поздравляем! Вы допущены к проведению занятий.")
            else:
                msg = "Пока не допущены к занятиям."
                if comment:
                    msg += f"\n\nКомментарий: {comment}"
                self._intern_notify_user(intern_uid, msg)
        return {"ok": True, "booking": result}

    def admin_lesson_controls(self, auth: dict[str, Any]) -> dict[str, Any]:
        denied = self._require_admin_tab(auth, "lesson-control")
        if denied:
            return denied
        items = self._lesson_control_dashboard_items(limit=500)
        stats = {
            "total": len(items),
            "problems": sum(1 for x in items if str(x.get("attention") or "") == "problem"),
            "waitingReview": sum(1 for x in items if str(x.get("attention") or "") == "review"),
            "notClosedPast": sum(1 for x in items if bool(x.get("is_past")) and str(x.get("lesson_status") or "") != "closed"),
            "prepReady": sum(1 for x in items if str(x.get("preparation_status") or "") == "ready"),
            "closed": sum(1 for x in items if str(x.get("lesson_status") or "") == "closed"),
        }
        return {"ok": True, "stats": stats, "items": items}

    def notion_status(self, auth: dict[str, Any]) -> dict[str, Any]:
        denied = self._require_admin_tab(auth, "notion")
        if denied:
            return denied
        sync_dir = Path(getattr(self.settings, "notion_sync_dir", "") or (BASE_DIR / "data" / "notion"))
        manifest = BASE_DIR / "data" / "notion_manifest.json"
        md_files = list(sync_dir.glob("*.md")) if sync_dir.exists() else []
        manifest_count = 0
        if manifest.exists():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                manifest_count = len(data.get("pages") or data if isinstance(data, dict) else [])
            except Exception:
                manifest_count = 0
        return {"ok": True, "syncDir": str(sync_dir), "files": len(md_files), "manifest": str(manifest), "manifestCount": manifest_count, "kbFiles": len(self.kb.files), "kbChunks": len(self.kb.chunks)}

    def _teacher_ids_from_item(self, item: dict[str, Any]) -> list[str]:
        teachers = item.get("teachers") or item.get("teacher") or item.get("teacherIds") or item.get("teacherId") or []
        ids: list[str] = []
        if isinstance(teachers, list):
            for t in teachers:
                if isinstance(t, dict):
                    tid = _pick(t, ("id", "teacherId"))
                    if tid:
                        ids.append(str(tid).strip())
                elif t:
                    ids.append(str(t).strip())
        elif isinstance(teachers, dict):
            tid = _pick(teachers, ("id", "teacherId"))
            if tid:
                ids.append(str(tid).strip())
        elif teachers:
            ids.append(str(teachers).strip())
        out: list[str] = []
        for tid in ids:
            if tid and tid not in out:
                out.append(tid)
        return out

    def _schedule_snapshot_from_item(self, item: dict[str, Any]) -> dict[str, Any]:
        teacher_ids = self._teacher_ids_from_item(item)
        payload = {
            "lesson_date": (_lesson_date_value(item) or "")[:10],
            "lesson_time": _lesson_time_value(item) or "",
            "group_name": _lesson_group_value(item) or "",
            "lesson_topic": _pick(item, ("topic", "name", "title", "subjectName", "courseName", "description")) or "",
            "teacher_ids": ",".join(teacher_ids),
            "teacher_names": _lesson_teacher_value(item) or "",
        }
        fingerprint_source = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        payload["fingerprint"] = hashlib.sha1(fingerprint_source.encode("utf-8")).hexdigest()
        payload["raw_preview"] = json.dumps(item, ensure_ascii=False)[:3000]
        # Filial info for correct online/offline detection (priority over group_name parsing)
        filial_name = str(
            _pick(item, ("_prettyFilialName", "filialName", "branchName")) or ""
        ).strip()
        room_id_raw = _pick(item, ("roomId", "classroomId"))
        payload["filial_name"] = filial_name
        payload["lesson_room_id"] = str(room_id_raw if room_id_raw is not None else "")
        return payload

    def _ops_lesson_summary(self, snapshot: dict[str, Any]) -> str:
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

    def schedule_check(self, auth: dict[str, Any], days: int = 30, notify: bool = False) -> dict[str, Any]:
        denied = self._require_admin(auth)
        if denied:
            return denied
        result = self.moyklass.get_upcoming_lessons(days=max(1, min(int(days or 30), 120)), limit=1000)
        if not result.ok:
            return {"ok": False, "error": result.error or f"status={result.status}"}
        items = [x for x in extract_items(result.data) if isinstance(x, dict)]
        new_events: list[dict[str, Any]] = []
        changed_events: list[dict[str, Any]] = []
        unchanged = 0
        tasks: list[dict[str, Any]] = []
        sent = 0
        for item in items:
            lesson_id = str(_pick(item, ("id", "lessonId")) or "").strip()
            if not lesson_id:
                continue
            snap = self._schedule_snapshot_from_item(item)
            event, previous, current = self.storage.upsert_lesson_snapshot(lesson_id, snap)
            teacher_ids = [x for x in str(current.get("teacher_ids") or "").split(",") if x]
            first_teacher_id = teacher_ids[0] if teacher_ids else ""
            first_staff = self.storage.get_staff_by_mk_teacher_id(first_teacher_id) if first_teacher_id else None
            self.storage.upsert_teacher_lesson_control(
                lesson_id,
                lesson_date=current.get("lesson_date") or "",
                lesson_time=current.get("lesson_time") or "",
                group_name=current.get("group_name") or "",
                lesson_topic=current.get("lesson_topic") or "",
                teacher_name=current.get("teacher_names") or "",
                mk_teacher_id=first_teacher_id,
                teacher_user_id=int(first_staff.get("user_id")) if first_staff and first_staff.get("user_id") else None,
                filial_name=current.get("filial_name") or "",
                lesson_room_id=current.get("lesson_room_id") or "",
            )
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
                    source="miniapp_schedule_check",
                )
                tasks.append(task)
                prep_task = self.storage.upsert_teacher_task(
                    task_key=f"prepare_lesson:{lesson_id}:{mk_teacher_id or 'unknown'}",
                    task_type="prepare_lesson",
                    title="Подготовиться к занятию",
                    text="Изучить материал в Notion, посмотреть видео, выполнить практику.\n\n" + self._ops_lesson_summary(current),
                    user_id=user_id,
                    mk_teacher_id=mk_teacher_id,
                    lesson_id=lesson_id,
                    due_at=self._prep_due_date(str(current.get("lesson_date") or "")),
                    priority="normal",
                    source="miniapp_schedule_check",
                )
                tasks.append(prep_task)
                if notify and user_id:
                    notify_text = (("📌 Вам добавлено новое занятие" if event == "new" else "⚠️ Изменение в вашем занятии") + "\n\n" + self._ops_lesson_summary(current) + "\n\nОткройте кабинет преподавателя и проверьте подготовку.")
                    ok, err = _telegram_send(self.settings.telegram_bot_token, user_id, notify_text)
                    self.storage.add_notification_log(event, notify_text, user_id, lesson_id, task.get("id"), ok, err)
                    if ok:
                        sent += 1
        return {"ok": True, "total": len(items), "new": new_events, "changed": changed_events, "unchanged": unchanged, "tasks": tasks, "sent": sent}



    def _format_chat_history(self, history: Any) -> str:
        if not isinstance(history, list):
            return ""
        lines: list[str] = []
        for item in history[-8:]:
            if not isinstance(item, dict):
                continue
            role = "Сотрудник" if str(item.get("role") or "") == "user" else "Агент"
            text = _clean_mk_text(item.get("text") or "")
            if not text:
                continue
            lines.append(f"{role}: {text[:900]}")
        return "\n".join(lines).strip()[:5000]

    def _format_chat_work_context(self, auth: dict[str, Any], raw_context: Any) -> str:
        user_id = int(auth["user_id"])
        me = self.me(auth)
        lines: list[str] = []
        lines.append(f"Сотрудник: {me.get('fullName') or 'не указано'}")
        lines.append(f"Роль: {me.get('roleLabel') or me.get('role') or 'не указано'}")
        if me.get("mkTeacherId"):
            lines.append(f"teacherId МойКласс: {me.get('mkTeacherId')}")
        if me.get("mkTeacherName"):
            lines.append(f"Преподаватель МойКласс: {me.get('mkTeacherName')}")

        ctx = raw_context if isinstance(raw_context, dict) else {}
        lessons = ctx.get("lessons") if isinstance(ctx.get("lessons"), list) else []
        if lessons:
            lines.append("\nВидимые занятия в приложении:")
            for idx, lesson in enumerate(lessons[:8], start=1):
                if not isinstance(lesson, dict):
                    continue
                title = _clean_mk_text(lesson.get("title") or lesson.get("group") or "Занятие")
                date_s = _clean_mk_text(lesson.get("date") or "")
                time_s = _clean_mk_text(lesson.get("time") or "")
                topic = _clean_mk_text(lesson.get("topic") or "тема не указана")
                room = _clean_mk_text(lesson.get("room") or "")
                status_parts = []
                for key, label in (
                    ("prepStatus", "подготовка"),
                    ("lessonStatus", "занятие"),
                    ("reportStatus", "отчёт"),
                    ("moyklassStatus", "МойКласс"),
                    ("workStatus", "работы"),
                    ("roomStatus", "кабинет"),
                ):
                    value = _clean_mk_text(lesson.get(key) or "")
                    if value:
                        status_parts.append(f"{label}: {value}")
                meta = " · ".join(x for x in [date_s, time_s, topic, room] if x)
                line = f"{idx}. {title}"
                if meta:
                    line += f" - {meta}"
                if status_parts:
                    line += f" ({'; '.join(status_parts[:6])})"
                lines.append(line[:900])

        selected = ctx.get("selectedLesson") if isinstance(ctx.get("selectedLesson"), dict) else None
        if selected:
            lines.append("\nОткрытая карточка занятия:")
            title = _clean_mk_text(selected.get("title") or selected.get("group") or "Занятие")
            meta = " · ".join(_clean_mk_text(selected.get(k) or "") for k in ("date", "time", "topic", "room") if _clean_mk_text(selected.get(k) or ""))
            lines.append((title + (f" - {meta}" if meta else ""))[:900])

        work_schedule = ctx.get("workSchedule") if isinstance(ctx.get("workSchedule"), list) else []
        if work_schedule:
            day_names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
            lines.append("\nРабочие возможности сотрудника:")
            for slot in work_schedule[:30]:
                if not isinstance(slot, dict):
                    continue
                try:
                    day = max(0, min(6, int(slot.get("dayOfWeek") or 0)))
                except Exception:
                    day = 0
                start = _clean_mk_text(slot.get("startTime") or "")[:5]
                end = _clean_mk_text(slot.get("endTime") or "")[:5]
                location = _clean_mk_text(slot.get("location") or "")
                note = _clean_mk_text(slot.get("note") or "")
                line = f"{day_names[day]} {start}-{end}"
                if location:
                    line += f" · {location}"
                if note:
                    line += f" · {note[:140]}"
                lines.append(line[:500])

        open_slots = ctx.get("openSlots") if isinstance(ctx.get("openSlots"), list) else []
        if open_slots:
            lines.append("\nСвободные окна преподавателей для клиент-менеджера:")
            for slot in open_slots[:30]:
                if not isinstance(slot, dict):
                    continue
                teacher = _clean_mk_text(slot.get("teacherName") or slot.get("teacher_name") or "Преподаватель")
                day = _clean_mk_text(slot.get("day") or slot.get("dayName") or "")
                date_s = _clean_mk_text(slot.get("date") or slot.get("dateLabel") or "")
                start = _clean_mk_text(slot.get("startTime") or slot.get("start_time") or "")[:5]
                end = _clean_mk_text(slot.get("endTime") or slot.get("end_time") or "")[:5]
                location = _clean_mk_text(slot.get("location") or "")
                note = _clean_mk_text(slot.get("note") or "")
                line = f"{teacher}: {day} {date_s} {start}-{end}"
                if location:
                    line += f" · {location}"
                if note:
                    line += f" · {note[:140]}"
                lines.append(line[:600])

        tasks = ctx.get("tasks") if isinstance(ctx.get("tasks"), list) else []
        stored_tasks = self.storage.list_teacher_tasks(user_id=user_id, status="open", limit=8)
        all_tasks = list(tasks[:8]) + [t for t in stored_tasks if isinstance(t, dict)][:8]
        if all_tasks:
            lines.append("\nОткрытые задачи/напоминания:")
            seen: set[str] = set()
            pos = 1
            for task in all_tasks[:12]:
                if not isinstance(task, dict):
                    continue
                title = _clean_mk_text(task.get("title") or "Задача")
                text = _clean_mk_text(task.get("text") or "")
                due = _clean_mk_text(task.get("dueAt") or task.get("due_at") or "")
                key = (title + text + due).lower()
                if key in seen:
                    continue
                seen.add(key)
                line = f"{pos}. {title}"
                if due:
                    line += f" - дедлайн/дата: {due}"
                if text:
                    line += f". {text[:420]}"
                lines.append(line[:900])
                pos += 1

        # Intern onboarding track — gives the agent context to answer
        # "Что мне делать дальше?", "Почему шаг заблокирован?" etc.
        role = self._role_for_user(user_id)
        if role == "intern":
            try:
                track = self.storage.get_intern_track(user_id)
                step_labels = {
                    1: "Шаг 1 — Наблюдение занятий (нужно посетить и прокомментировать 2 занятия)",
                    2: "Шаг 2 — Отправить подготовительную работу",
                    3: "Шаг 3 — Ожидание проверки работы методистом",
                    4: "Шаг 4 — Запись на пробное занятие",
                    5: "Шаг 5 — Пробное занятие / ожидание решения методиста",
                }
                current_step = int(track.get("current_step") or 1)
                admitted = bool(track.get("admitted"))
                step_label = "Допущен к работе" if admitted else step_labels.get(current_step, f"Шаг {current_step}")
                lines.append("\nМаршрут стажёра:")
                lines.append(f"Текущий шаг: {step_label}")
                obs_count = int(track.get("obs_count") or 0)
                obs_needed = int(track.get("obs_needed") or 2)
                lines.append(f"Наблюдения: засчитано {obs_count} из {obs_needed} нужных")
                latest_work = track.get("latest_work")
                if latest_work:
                    ws = str(latest_work.get("status") or "")
                    ws_label = {"submitted": "на проверке", "accepted": "принята", "rejected": "отклонена"}.get(ws, ws or "нет")
                    lines.append(f"Подготовительная работа: {ws_label}")
                    if ws == "rejected":
                        comment = str(latest_work.get("reviewer_comment") or "").strip()
                        if comment:
                            lines.append(f"Комментарий проверяющего: {comment[:300]}")
                elif current_step >= 2:
                    lines.append("Подготовительная работа: не загружена")
                latest_booking = track.get("latest_booking")
                if latest_booking:
                    bs = str(latest_booking.get("status") or "")
                    bs_label = {"requested": "запись сделана / ожидается", "passed": "пройдено — допущен", "failed": "не пройдено"}.get(bs, bs or "нет")
                    lines.append(f"Пробное занятие: {bs_label}")
                    if bs == "failed":
                        rc = str(latest_booking.get("reviewer_comment") or "").strip()
                        if rc:
                            lines.append(f"Комментарий по пробному: {rc[:300]}")
                if admitted:
                    lines.append("Финальный статус: Допущен к работе ✓")
            except Exception:
                pass

        return "\n".join(lines).strip()[:9000]


    def _try_moyklass_month_analytics_answer(self, auth: dict[str, Any], question: str) -> str:
        if not _looks_like_mk_month_analytics_question(question):
            return ""
        user_id = int(auth["user_id"])
        role = self._role_for_user(user_id)
        if role not in REPORT_ROLES:
            return "Общие показатели МойКласс доступны клиент-менеджеру, директору, owner, операционному менеджеру или старшему преподавателю. Для преподавателя могу ответить по видимым занятиям и задачам в приложении."
        month_label = _month_from_staff_question(question)
        if not month_label:
            return "Уточните месяц в формате YYYY-MM или словами, например: май 2026."
        if not self.settings.moyklass_enabled:
            return "МойКласс сейчас отключён в настройках агента, поэтому я не могу посчитать месячные показатели."
        try:
            result = self.moyklass.get_month_analytics(month_label)
        except Exception as exc:
            log.exception("MoyKlass analytics in Mini App chat failed")
            return f"Не удалось получить данные МойКласс за {month_label}: {exc}"
        return _mk_month_student_answer_text(result, month_label, question)

    def _open_slot_time_minutes(self, value: Any) -> int | None:
        text = str(value or "").strip()
        m = re.search(r"(\d{1,2})(?::|\.)(\d{2})", text)
        if not m:
            m = re.search(r"\b(\d{1,2})\b", text)
            if not m:
                return None
            hour, minute = int(m.group(1)), 0
        else:
            hour, minute = int(m.group(1)), int(m.group(2))
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            return None
        return hour * 60 + minute

    def _parse_open_slot_dates_from_text(self, raw: str) -> tuple[set[str], list[str]]:
        """Parse explicit dates from a scheduling question. Past dates become notes, not hard filters."""
        today = date.today()
        dates: set[str] = set()
        notes: list[str] = []

        def add_date(dt: date, original: str) -> None:
            if dt < today:
                notes.append(f"Указанная дата {original} уже прошла, поэтому показываю ближайшие будущие окна.")
            else:
                dates.add(dt.isoformat())

        for m in re.finditer(r"\b(20\d{2})[-./](0?[1-9]|1[0-2])[-./]([0-3]?\d)\b", raw):
            try:
                add_date(date(int(m.group(1)), int(m.group(2)), int(m.group(3))), m.group(0))
            except Exception:
                pass

        for m in re.finditer(r"(?<!\d)([0-3]?\d)[./](0?[1-9]|1[0-2])(?:[./](20\d{2}))?(?!\d)", raw):
            try:
                day = int(m.group(1))
                month = int(m.group(2))
                year = int(m.group(3) or today.year)
                dt = date(year, month, day)
                if not m.group(3) and dt < today - timedelta(days=45):
                    dt = date(year + 1, month, day)
                add_date(dt, m.group(0))
            except Exception:
                pass
        return dates, notes

    def _looks_like_open_slots_question(self, question: str) -> bool:
        raw = str(question or "").lower().replace("ё", "е")
        if re.search(r"\b(окн[оа]?|свободн\w*|доступн\w*)\b", raw):
            return True
        has_action = bool(re.search(r"\b(подбер\w*|найд\w*|покаж\w*|кто\s+может|есть\s+ли|можно\s+поставить|запис\w*|постав\w*|использ\w*)\b", raw))
        has_work = bool(re.search(r"\b(пробн\w*|отработ\w*|замен\w*|регулярн\w*|заняти\w*|клиент\w*|преподавател\w*)\b", raw))
        return has_action and has_work

    def _open_slot_query_filters(self, question: str) -> dict[str, Any]:
        raw = str(question or "").lower().replace("ё", "е")
        filters: dict[str, Any] = {"weeks": ["current", "next"], "days": set(), "location": "all", "after": None, "before": None, "time_part": "all", "duration": None, "dates": set(), "date_notes": []}
        if re.search(r"\b(следующ\w*\s+недел|на\s+следующ\w*)\b", raw):
            filters["weeks"] = ["next"]
        elif re.search(r"\b(эт\w*\s+недел|на\s+эт\w*)\b", raw):
            filters["weeks"] = ["current"]

        today = date.today()
        relative_dates: set[str] = set()
        if re.search(r"\bсегодня\b", raw):
            relative_dates.add(today.isoformat())
        if re.search(r"\bзавтра\b", raw):
            relative_dates.add((today + timedelta(days=1)).isoformat())
        if re.search(r"\bпослезавтра\b", raw):
            relative_dates.add((today + timedelta(days=2)).isoformat())

        explicit_dates, date_notes = self._parse_open_slot_dates_from_text(raw)
        # In auto client-manager tasks the date may be a deadline: "до следующего занятия".
        # In that case, search from today up to the future deadline. If the deadline is past, do not hard-filter.
        if explicit_dates and re.search(r"\b(до\s+следующ|до\s+начала|в\s+течени[ея]|дедлайн|срок)\b", raw):
            filters["from_date"] = today.isoformat()
            filters["to_date"] = max(explicit_dates).isoformat()
        else:
            filters["dates"] = relative_dates | explicit_dates
        filters["date_notes"] = date_notes

        day_patterns = [
            (0, r"\b(понедельник|пн)\w*\b"),
            (1, r"\b(вторник|вт)\w*\b"),
            (2, r"\b(сред[ауеы]?|ср)\b"),
            (3, r"\b(четверг|чт)\w*\b"),
            (4, r"\b(пятниц[ауеы]?|пт)\b"),
            (5, r"\b(суббот[ауеы]?|сб)\b"),
            (6, r"\b(воскресень[ея]?|вс)\b"),
        ]
        for day, pat in day_patterns:
            if re.search(pat, raw):
                filters["days"].add(day)
        if re.search(r"\bвыходн\w*\b", raw):
            filters["days"].update({5, 6})
        if re.search(r"\bбудн\w*\b", raw):
            filters["days"].update({0, 1, 2, 3, 4})

        if "кульман" in raw:
            filters["location"] = "Кульман 1/1"
        elif "мстислав" in raw:
            filters["location"] = "Мстиславца 6"
        elif "онлайн" in raw or "online" in raw:
            filters["location"] = "Онлайн"

        after_m = re.search(r"\bпосле\s+(\d{1,2})(?::|\.)?(\d{2})?\b", raw)
        before_m = re.search(r"\bдо\s+(\d{1,2})(?::|\.)?(\d{2})?\b", raw)
        if after_m:
            filters["after"] = int(after_m.group(1)) * 60 + int(after_m.group(2) or 0)
        if before_m:
            filters["before"] = int(before_m.group(1)) * 60 + int(before_m.group(2) or 0)
        if re.search(r"\bвечер\w*\b", raw):
            filters["time_part"] = "evening"
        elif re.search(r"\bутр\w*\b", raw):
            filters["time_part"] = "morning"
        elif re.search(r"\b(днем|день|дневн\w*)\b", raw):
            filters["time_part"] = "day"

        dur_m = re.search(r"\b(\d{1,3})\s*(?:мин|минут)\b", raw)
        if dur_m:
            filters["duration"] = max(15, int(dur_m.group(1)))
        hour_m = re.search(r"\b(\d+(?:[,.]\d+)?)\s*(?:час|часа|часов)\b", raw)
        if hour_m:
            filters["duration"] = max(30, int(float(hour_m.group(1).replace(',', '.')) * 60))
        return filters

    def _slot_matches_open_query(self, slot: dict[str, Any], filters: dict[str, Any]) -> bool:
        try:
            day = int(slot.get("day_of_week") if slot.get("day_of_week") is not None else slot.get("dayOfWeek") or 0)
        except Exception:
            day = 0
        slot_date = str(slot.get("date") or "")
        dates = filters.get("dates") or set()
        if dates and slot_date not in dates:
            return False
        from_date = str(filters.get("from_date") or "")
        to_date = str(filters.get("to_date") or "")
        if from_date and slot_date and slot_date < from_date:
            return False
        if to_date and slot_date and slot_date > to_date:
            return False
        days = filters.get("days") or set()
        if days and day not in days:
            return False
        location_filter = str(filters.get("location") or "all")
        loc = str(slot.get("location") or "Любой формат").strip() or "Любой формат"
        if location_filter != "all" and loc != "Любой формат" and loc.lower() != location_filter.lower():
            return False
        start = self._open_slot_time_minutes(slot.get("start_time") or slot.get("startTime"))
        end = self._open_slot_time_minutes(slot.get("end_time") or slot.get("endTime"))
        if start is None or end is None:
            return True
        if end <= start:
            return False
        after = filters.get("after")
        before = filters.get("before")
        if after is not None and end <= int(after):
            return False
        if before is not None and start >= int(before):
            return False
        part = filters.get("time_part") or "all"
        if part == "morning" and start >= 12 * 60:
            return False
        if part == "day" and not (start < 17 * 60 and end > 12 * 60):
            return False
        if part == "evening" and end <= 17 * 60:
            return False
        duration = filters.get("duration")
        if duration and (end - start) < int(duration):
            return False
        return True

    def _format_open_slot_for_answer(self, slot: dict[str, Any], idx: int) -> str:
        teacher = _clean_mk_text(slot.get("teacher_name") or slot.get("teacherName") or "Преподаватель")
        day = _clean_mk_text(slot.get("day_name") or slot.get("dayName") or "")
        date_s = _clean_mk_text(slot.get("date_label") or slot.get("dateLabel") or slot.get("date") or "")
        start = _clean_mk_text(slot.get("start_time") or slot.get("startTime") or "")[:5]
        end = _clean_mk_text(slot.get("end_time") or slot.get("endTime") or "")[:5]
        loc = _clean_mk_text(slot.get("location") or "Любой формат")
        note = _clean_mk_text(slot.get("note") or "")
        line = f"{idx}. {teacher}\n{day}, {date_s}, {start}-{end}\nФормат/филиал: {loc}"
        if note:
            line += f"\nКомментарий: {note}"
        return line

    def _selected_open_slot_usage_answer(self, question: str) -> str:
        if not re.search(r"использ\w+\s+это\s+свободн\w+\s+окн", str(question or "").lower().replace("ё", "е")):
            return ""
        lines = [_clean_mk_text(x) for x in str(question or "").splitlines()]
        lines = [x for x in lines if x and not x.lower().startswith("помоги использовать") and "составь короткое" not in x.lower() and "чек-лист" not in x.lower()]
        slot_info = "\n".join(lines[:6]).strip()
        return (
            "Можно использовать это окно так.\n\n"
            "Сообщение клиенту:\n"
            "Здравствуйте! Есть вариант для занятия:\n"
            f"{slot_info or 'уточните преподавателя, дату, время и формат'}\n\n"
            "Подойдёт ли вам это время?\n\n"
            "Перед записью проверьте:\n"
            "1. В МойКласс нет занятия у преподавателя в это время.\n"
            "2. Формат/филиал подходит клиенту.\n"
            "3. Кабинет свободен, если занятие офлайн.\n"
            "4. В карточке клиента/группы корректно указаны дата, время и преподаватель.\n\n"
            "Преподаватели универсальные, поэтому курс и тип занятия отдельно подбирать не нужно."
        )

    def _try_open_slots_answer(self, auth: dict[str, Any], question: str, raw_context: Any = None) -> str:
        if not self._looks_like_open_slots_question(question):
            return ""
        user_id = int(auth["user_id"])
        role = self._role_for_user(user_id)
        if role not in OPEN_SLOTS_ROLES and role not in ADMIN_ROLES:
            return "Свободные окна преподавателей доступны клиент-менеджеру, методисту и админу. Преподаватель заполняет свои окна во вкладке «Время»."
        selected_answer = self._selected_open_slot_usage_answer(question)
        if selected_answer:
            return selected_answer
        filters = self._open_slot_query_filters(question)
        slots: list[dict[str, Any]] = []
        for week in filters.get("weeks") or ["current", "next"]:
            try:
                data = self.open_slots(auth, week=str(week), location="all")
                if data.get("ok"):
                    slots.extend([x for x in data.get("items") or [] if isinstance(x, dict)])
            except Exception:
                log.exception("Failed to load open slots for chat answer")
        # Deduplicate by DB id/date/time/teacher.
        dedup: list[dict[str, Any]] = []
        seen: set[str] = set()
        for slot in slots:
            key = "|".join(str(slot.get(k) or "") for k in ("id", "date", "start_time", "end_time", "user_id"))
            if key in seen:
                continue
            seen.add(key)
            if self._slot_matches_open_query(slot, filters):
                dedup.append(slot)
        dedup.sort(key=lambda x: (str(x.get("date") or ""), str(x.get("start_time") or ""), str(x.get("teacher_name") or "")))
        if not dedup:
            detail_parts: list[str] = []
            if filters.get("dates"):
                detail_parts.append("дата: " + ", ".join(sorted(str(x) for x in filters.get("dates") or [])))
            if filters.get("to_date"):
                detail_parts.append(f"до даты: {filters.get('to_date')}")
            if filters.get("location") != "all":
                detail_parts.append(f"филиал/формат: {filters.get('location')}")
            if filters.get("time_part") != "all":
                labels = {"morning": "утро", "day": "день", "evening": "вечер"}
                detail_parts.append(f"время дня: {labels.get(filters.get('time_part'), filters.get('time_part'))}")
            if filters.get("after") is not None:
                detail_parts.append(f"после {int(filters.get('after')) // 60:02d}:{int(filters.get('after')) % 60:02d}")
            if filters.get("before") is not None:
                detail_parts.append(f"до {int(filters.get('before')) // 60:02d}:{int(filters.get('before')) % 60:02d}")
            query_note = f" по условию ({'; '.join(detail_parts)})" if detail_parts else ""
            date_note = ""
            if filters.get("date_notes"):
                date_note = "\n" + "\n".join(str(x) for x in filters.get("date_notes") or []) + "\n"
            return (
                f"Свободных окон{query_note} не нашёл." + date_note + "\n"
                "Что можно сделать:\n"
                "1. Проверить другую неделю или более широкий диапазон времени.\n"
                "2. Попросить преподавателей заполнить вкладку «Время».\n"
                "3. Если нужно срочно, написать методисту или администратору для ручного согласования."
            )
        shown = dedup[:6]
        answer = []
        if filters.get("date_notes"):
            answer.extend(str(x) for x in filters.get("date_notes") or [])
        answer.append(f"Нашёл {len(dedup)} подходящ{('ее окно' if len(dedup) == 1 else 'их окон')}:")
        answer.extend(self._format_open_slot_for_answer(slot, i) for i, slot in enumerate(shown, start=1))
        if len(dedup) > len(shown):
            answer.append(f"Ещё {len(dedup) - len(shown)} окон не показано. Уточните день, время или филиал, чтобы сузить список.")
        answer.append(
            "Перед записью проверьте в МойКласс, что у преподавателя и кабинета нет занятия в выбранное время. "
            "Курс и тип занятия выбирать не нужно: преподаватели Yellow Club универсальные."
        )
        return "\n\n".join(answer).strip()

    def ask_agent(self, auth: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        user_id = int(auth["user_id"])
        question = str(payload.get("question") or "").strip()
        if not question:
            return {"ok": False, "error": "Напишите вопрос."}
        if len(question) > 3000:
            return {"ok": False, "error": "Вопрос слишком длинный. Сократите до 3000 символов."}
        me = self.me(auth)
        role_label = str(me.get("roleLabel") or me.get("role") or "сотрудник")
        # Operational scheduling requests must be handled before month analytics.
        open_slots_answer = self._try_open_slots_answer(auth, question, payload.get("workContext"))
        if open_slots_answer:
            return {"ok": True, "answer": open_slots_answer}
        mk_analytics_answer = self._try_moyklass_month_analytics_answer(auth, question)
        if mk_analytics_answer:
            return {"ok": True, "answer": mk_analytics_answer}
        work_context = self._format_chat_work_context(auth, payload.get("workContext"))
        chat_history = self._format_chat_history(payload.get("history"))
        answer = self.core.build_staff_chat_answer(
            AnswerContext(chat_id=user_id, thread_id=None, user_text=question),
            role_label=role_label,
            work_context=work_context,
            chat_history=chat_history,
        )
        answer = re.sub(r"<[^>]+>", "", str(answer or "")).strip()
        answer = re.sub(r"\n{4,}", "\n\n\n", answer)
        return {"ok": True, "answer": answer or "Не нашёл точного ответа в базе. Уточните вопрос или обратитесь к ответственному."}

    def tasks(self, auth: dict[str, Any]) -> dict[str, Any]:
        user_id = int(auth["user_id"])
        rows = self.storage.list_teacher_tasks(user_id=user_id, status="open", limit=50)
        return {"ok": True, "items": rows}


    # --- Automatic client-manager tasks from MoyKlass ---

    def _mk_truthy(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        text = str(value or "").strip().lower()
        return text in {"1", "true", "yes", "y", "да", "истина", "present", "visited", "attended"}

    def _deep_first_value(self, data: Any, keys: tuple[str, ...], max_depth: int = 4) -> Any:
        key_set = {k.lower() for k in keys}
        def walk(obj: Any, depth: int) -> Any:
            if depth > max_depth:
                return None
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if str(k).lower() in key_set and v not in (None, ""):
                        return v
                for v in obj.values():
                    got = walk(v, depth + 1)
                    if got not in (None, ""):
                        return got
            elif isinstance(obj, list):
                for x in obj[:30]:
                    got = walk(x, depth + 1)
                    if got not in (None, ""):
                        return got
            return None
        return walk(data, 0)

    def _mk_record_lesson_id(self, record: dict[str, Any]) -> str:
        value = self._deep_first_value(record, ("lessonId", "idLesson", "lesson_id", "lessonRecordLessonId"), max_depth=3)
        if value not in (None, ""):
            return str(value)
        lesson = record.get("lesson") if isinstance(record.get("lesson"), dict) else {}
        return str(_pick(lesson, ("id", "lessonId")) or "")

    def _mk_record_student_id(self, record: dict[str, Any]) -> str:
        value = self._deep_first_value(record, ("userId", "studentId", "clientId", "customerId", "idUser", "user_id", "student_id", "client_id"), max_depth=3)
        return str(value or "").strip()

    def _mk_user_id_from_obj(self, obj: dict[str, Any]) -> str:
        if not isinstance(obj, dict):
            return ""
        value = self._deep_first_value(
            obj,
            ("id", "userId", "studentId", "clientId", "customerId", "idUser", "user_id", "student_id", "client_id"),
            max_depth=2,
        )
        return str(value or "").strip()

    def _mk_user_display_name(self, obj: dict[str, Any]) -> str:
        if not isinstance(obj, dict):
            return ""

        def clean(value: Any) -> str:
            text = _clean_mk_text(value)
            text = re.sub(r"\s+", " ", text).strip()
            # A bare numeric id is not a human name.
            if re.fullmatch(r"\d{3,}", text):
                return ""
            return text

        direct = self._deep_first_value(
            obj,
            ("userName", "studentName", "clientName", "customerName", "fullName", "fio", "full_name"),
            max_depth=2,
        )
        direct_text = clean(direct)
        if direct_text:
            return direct_text[:160]

        first = clean(
            obj.get("firstName") or obj.get("firstname") or obj.get("first_name")
            or obj.get("givenName") or obj.get("given_name") or obj.get("name")
        )
        last = clean(
            obj.get("lastName") or obj.get("lastname") or obj.get("last_name")
            or obj.get("surname") or obj.get("familyName") or obj.get("family_name")
        )
        middle = clean(obj.get("middleName") or obj.get("middlename") or obj.get("patronymic") or obj.get("secondName"))
        full = " ".join(x for x in [first, last, middle] if x).strip()
        if full:
            return full[:160]

        generic = clean(obj.get("name") or obj.get("title"))
        return generic[:160] if generic else ""

    def _mk_student_names_by_ids(self, student_ids: set[str]) -> dict[str, str]:
        """Resolve MoyKlass student IDs to readable names for client-manager tasks.

        Lesson records often contain only userId/studentId. Without this extra
        lookup the task card shows `Ученик 8671858`, which is hard for managers
        to use. The method is intentionally read-only and cached for a few hours.
        """
        wanted = {str(x or "").strip() for x in (student_ids or set()) if str(x or "").strip()}
        if not wanted or not self.settings.moyklass_enabled:
            return {}

        now = time.time()
        resolved: dict[str, str] = {}
        missing: set[str] = set()
        for sid in wanted:
            cached = self._mk_student_name_cache.get(sid)
            if cached and now - cached[0] < 60 * 60 * 6:
                if cached[1]:
                    resolved[sid] = cached[1]
            else:
                missing.add(sid)
        if not missing:
            return resolved

        def remember(sid: str, name: str) -> None:
            sid = str(sid or "").strip()
            name = _clean_mk_text(name)
            name = re.sub(r"\s+", " ", name).strip()
            if not sid or not name or re.fullmatch(r"\d{3,}", name):
                return
            resolved[sid] = name[:160]
            self._mk_student_name_cache[sid] = (now, name[:160])

        # First try a broad users list. On most MoyKlass accounts this is enough
        # and avoids many per-student calls.
        try:
            users_result = self.moyklass.get_users("limit=10000")
            if users_result.ok:
                for item in extract_items(users_result.data):
                    if not isinstance(item, dict):
                        continue
                    uid = self._mk_user_id_from_obj(item)
                    if uid in missing:
                        name = self._mk_user_display_name(item)
                        if name:
                            remember(uid, name)
        except Exception:
            log.debug("MK student name broad lookup failed", exc_info=True)

        missing = {sid for sid in missing if sid not in resolved}
        if missing:
            # Fallback: try direct and filtered read endpoints for the remaining IDs.
            for sid in list(missing)[:80]:
                endpoint_attempts = [
                    (f"/v1/company/users/{sid}", None),
                    (f"/v1/company/user/{sid}", None),
                    (f"/v1/company/clients/{sid}", None),
                    (f"/v1/company/students/{sid}", None),
                    ("/v1/company/users", {"id": sid, "limit": "5"}),
                    ("/v1/company/users", {"userId": sid, "limit": "5"}),
                    ("/v1/company/users", {"clientId": sid, "limit": "5"}),
                    ("/v1/company/users", {"q": sid, "limit": "5"}),
                ]
                for path, params in endpoint_attempts:
                    try:
                        result = self.moyklass.request("GET", path, params=params)
                    except Exception:
                        continue
                    if not result.ok:
                        continue
                    candidates = [x for x in extract_items(result.data) if isinstance(x, dict)]
                    if isinstance(result.data, dict):
                        candidates.insert(0, result.data)
                    for item in candidates:
                        uid = self._mk_user_id_from_obj(item)
                        if uid and uid != sid:
                            continue
                        name = self._mk_user_display_name(item)
                        if name:
                            remember(sid, name)
                            break
                    if sid in resolved:
                        break

        for sid in wanted:
            if sid not in resolved:
                # Cache misses briefly so we do not spam MoyKlass with the same
                # failed lookup on every task refresh.
                self._mk_student_name_cache[sid] = (now, "")
        return resolved

    def _mk_record_student_name(self, record: dict[str, Any], name_map: dict[str, str] | None = None) -> str:
        direct = self._deep_first_value(record, ("userName", "studentName", "clientName", "customerName", "fullName", "fio", "full_name"), max_depth=3)
        if direct:
            text = _clean_mk_text(direct)
            text = re.sub(r"\s+", " ", text).strip()
            if text and not re.fullmatch(r"\d{3,}", text):
                return text[:160]
        for obj_key in ("user", "student", "client", "customer"):
            obj = record.get(obj_key)
            if isinstance(obj, dict):
                full = self._mk_user_display_name(obj)
                if full:
                    return full[:160]
        sid = self._mk_record_student_id(record)
        if sid and name_map and name_map.get(sid):
            return name_map[sid]
        cached = self._mk_student_name_cache.get(sid) if sid else None
        if cached and cached[1]:
            return cached[1]
        return f"Ученик {sid}" if sid else "Ученик"

    def _mk_record_absent(self, record: dict[str, Any]) -> bool:
        if self._mk_truthy(record.get("skip")) or self._mk_truthy(record.get("test")):
            return False
        if "visit" in record:
            return not self._mk_truthy(record.get("visit"))
        if "visited" in record:
            return not self._mk_truthy(record.get("visited"))
        if "attendance" in record:
            return not self._mk_truthy(record.get("attendance"))
        status = str(record.get("status") or record.get("attendanceStatus") or "").lower()
        return bool(re.search(r"absent|miss|не\s*приш|отсутств|пропуск", status))

    def _mk_record_balance_left(self, record: dict[str, Any]) -> float | None:
        # Try lesson/subscription balances first. We intentionally avoid guessing
        # from payment sums unless MoyKlass explicitly returns a remaining lessons field.
        balance_keys = (
            "lessonsLeft", "visitsLeft", "classesLeft", "remainingLessons", "remainingVisits",
            "availableLessons", "availableVisits", "countLeft", "leftCount", "balanceLessons",
            "balanceVisits", "subscriptionBalance", "abonementBalance", "balance",
        )
        def parse_number(value: Any) -> float | None:
            if value is None or value == "":
                return None
            if isinstance(value, bool):
                return None
            if isinstance(value, (int, float)):
                return float(value)
            m = re.search(r"-?\d+(?:[\.,]\d+)?", str(value))
            if not m:
                return None
            return float(m.group(0).replace(",", "."))
        # Direct/nested common keys.
        value = self._deep_first_value(record, balance_keys, max_depth=5)
        parsed = parse_number(value)
        if parsed is not None:
            return parsed
        return None

    def _lesson_date_obj(self, item: dict[str, Any]) -> date | None:
        text = _lesson_date_value(item)
        try:
            return date.fromisoformat(str(text)[:10])
        except Exception:
            return None

    def _lesson_class_key(self, item: dict[str, Any]) -> str:
        return str(_pick(item, ("classId", "groupId", "courseClassId", "idClass")) or _lesson_group_value(item) or "").strip().lower()

    def _next_lesson_for_same_group(self, lessons: list[dict[str, Any]], lesson: dict[str, Any]) -> dict[str, Any] | None:
        current_date = self._lesson_date_obj(lesson)
        current_time = _lesson_time_value(lesson)
        class_key = self._lesson_class_key(lesson)
        if not current_date or not class_key:
            return None
        candidates: list[dict[str, Any]] = []
        for item in lessons:
            if item is lesson:
                continue
            if self._lesson_class_key(item) != class_key:
                continue
            d = self._lesson_date_obj(item)
            if not d or d < current_date:
                continue
            if d == current_date and str(_lesson_time_value(item) or "") <= str(current_time or ""):
                continue
            candidates.append(item)
        candidates.sort(key=lambda x: (str(_lesson_date_value(x) or ""), str(_lesson_time_value(x) or "")))
        return candidates[0] if candidates else None

    def _auto_task_manager_user_id(self) -> int:
        users = self.storage.list_staff_users(limit=1000)
        for role in ("client_manager", "operations", "owner"):
            for user in users:
                if str(user.get("role") or "") == role and str(user.get("status") or "active") == "active":
                    try:
                        return int(user.get("user_id") or 0)
                    except Exception:
                        pass
        return 0

    def sync_client_tasks_from_moyklass(self, auth: dict[str, Any], force: bool = False) -> dict[str, Any]:
        denied = self._require_client_tasks_access(auth)
        if denied:
            return denied
        if not self.settings.moyklass_enabled:
            return {"ok": False, "error": "МойКласс не настроен."}
        now_ts = time.time()
        if not force and now_ts - float(self._client_tasks_sync_cache.get("ts") or 0) < 180:
            cached = self._client_tasks_sync_cache.get("result") or {}
            return {"ok": True, **cached, "cached": True}

        today = date.today()
        start = today - timedelta(days=14)
        end = today + timedelta(days=14)
        owner_id = self._auto_task_manager_user_id() or int(auth.get("user_id") or 0)
        created = 0
        updated = 0
        diagnostics: list[str] = []
        try:
            lessons_result = self.moyklass.get_lessons_between(start, end, limit=1200)
            lessons = [x for x in extract_items(lessons_result.data) if isinstance(x, dict)] if lessons_result.ok else []
            if not lessons_result.ok:
                diagnostics.append(f"lessons: {lessons_result.status} {lessons_result.error[:200]}")
        except Exception as exc:
            log.exception("Auto client task sync: lessons failed")
            return {"ok": False, "error": f"Не удалось получить занятия МойКласс: {exc}"}

        try:
            records_result = self.moyklass._scan_lesson_records_for_month(start, today + timedelta(days=1), limit=6000)
            records = [x for x in extract_items(records_result.data) if isinstance(x, dict)] if records_result.ok else []
            if not records_result.ok:
                diagnostics.append(f"lessonRecords: {records_result.status} {records_result.error[:200]}")
        except Exception as exc:
            log.exception("Auto client task sync: lesson records failed")
            records = []
            diagnostics.append(f"lessonRecords exception: {str(exc)[:200]}")

        lessons_by_id: dict[str, dict[str, Any]] = {}
        for item in lessons:
            lid = _lesson_id(item)
            if lid:
                lessons_by_id[str(lid)] = item

        student_ids = {self._mk_record_student_id(record) for record in records}
        student_name_map = self._mk_student_names_by_ids(student_ids)

        def count_insert_or_update(row: dict[str, Any]) -> None:
            nonlocal created, updated
            try:
                if str(row.get("status") or "").strip().lower() in {"done", "cancelled"}:
                    return
                if row.get("created_at") and row.get("updated_at") and row.get("created_at") == row.get("updated_at"):
                    created += 1
                else:
                    updated += 1
            except Exception:
                updated += 1

        # 1) Absent student -> makeup task.
        for record in records:
            if not self._mk_record_absent(record):
                continue
            lesson_id = self._mk_record_lesson_id(record)
            lesson = lessons_by_id.get(str(lesson_id), {})
            if not lesson:
                # Some lessonRecords include the lesson object directly.
                lesson_obj = record.get("lesson") if isinstance(record.get("lesson"), dict) else {}
                if lesson_obj:
                    lesson = lesson_obj
            lesson_date = str(_lesson_date_value(lesson) or record.get("date") or record.get("lessonDate") or "")[:10]
            if not lesson_date:
                continue
            try:
                if date.fromisoformat(lesson_date) > today:
                    continue
            except Exception:
                pass
            student_id = self._mk_record_student_id(record)
            student_name = self._mk_record_student_name(record, student_name_map)
            group = _lesson_group_value(lesson) or _clean_mk_text(record.get("className") or record.get("groupName") or "")
            topic = _lesson_topic(lesson) or _clean_mk_text(record.get("topic") or record.get("theme") or "") or "тема не указана"
            room = _lesson_room_value(lesson) or "Любой формат"
            next_lesson = self._next_lesson_for_same_group(lessons, lesson) if lesson else None
            next_date = str(_lesson_date_value(next_lesson or {}) or "")[:10]
            if not next_date:
                try:
                    next_date = (date.fromisoformat(lesson_date) + timedelta(days=7)).isoformat()
                except Exception:
                    next_date = ""
            source_key = f"auto:makeup:{lesson_id or lesson_date}:{student_id or student_name}"[:300]
            title_note = f"Автоматически из МойКласс: ученик отсутствовал на занятии {lesson_date}."
            comment = (
                f"{title_note}\n"
                f"Нужно поставить отработку по теме: {topic}.\n"
                f"Группа/класс: {group or 'не указан'}.\n"
                f"Желательно провести до следующего занятия: {next_date or 'в течение 7 дней'}."
            )
            before = self.storage.get_client_manager_task(source_key) if False else None
            row = self.storage.upsert_client_manager_task(
                created_by=owner_id,
                task_type="makeup",
                status="new",
                priority="high",
                client_name="",
                child_name=student_name,
                desired_date=next_date,
                desired_time="до следующего занятия",
                location=room,
                comment=comment,
                source_type="auto_mk_absence",
                source_key=source_key,
                lesson_id=str(lesson_id or ""),
                mk_student_id=student_id,
            )
            count_insert_or_update(row)

        # 2) Balance/remaining lessons <= 0 -> payment task.
        payment_seen: set[str] = set()
        for record in records:
            balance_left = self._mk_record_balance_left(record)
            if balance_left is None or balance_left > 0:
                continue
            student_id = self._mk_record_student_id(record)
            student_name = self._mk_record_student_name(record, student_name_map)
            payment_key_person = student_id or student_name
            if not payment_key_person or payment_key_person in payment_seen:
                continue
            payment_seen.add(payment_key_person)
            lesson_id = self._mk_record_lesson_id(record)
            lesson = lessons_by_id.get(str(lesson_id), {})
            next_lesson = self._next_lesson_for_same_group(lessons, lesson) if lesson else None
            next_date = str(_lesson_date_value(next_lesson or {}) or "")[:10]
            group = _lesson_group_value(lesson) or _clean_mk_text(record.get("className") or record.get("groupName") or "")
            source_key = f"auto:payment:{payment_key_person}"[:300]
            comment = (
                "Автоматически из МойКласс: по данным записи/абонемента остаток занятий или баланс <= 0.\n"
                f"Нужно попросить оплатить следующий блок до следующего занятия: {next_date or 'дата следующего занятия не найдена'}.\n"
                f"Группа/класс: {group or 'не указан'}.\n"
                "Стоимость актуального блока: 239 BYN за 4 занятия."
            )
            row = self.storage.upsert_client_manager_task(
                created_by=owner_id,
                task_type="payment",
                status="new",
                priority="urgent" if next_date else "high",
                client_name="",
                child_name=student_name,
                amount="239 BYN",
                payment_for="4 занятия",
                deadline=next_date,
                comment=comment,
                source_type="auto_mk_payment_balance",
                source_key=source_key,
                lesson_id=str(lesson_id or ""),
                mk_student_id=student_id,
            )
            count_insert_or_update(row)

        result = {
            "syncedAt": datetime.now().isoformat(timespec="seconds"),
            "createdOrUpdated": created + updated,
            "created": created,
            "updated": updated,
            "recordsChecked": len(records),
            "lessonsChecked": len(lessons),
            "diagnostics": diagnostics[:6],
        }
        self._client_tasks_sync_cache = {"ts": now_ts, "result": result}
        return {"ok": True, **result}


    # --- Client manager reports from MoyKlass ---

    def _require_reports_access(self, auth: dict[str, Any]) -> dict[str, Any] | None:
        role = self._role_for_user(int(auth["user_id"]))
        if role not in REPORT_ROLES:
            return {"ok": False, "error": "Отчёты МойКласс доступны клиент-менеджеру, директору, owner, операционному менеджеру и старшему преподавателю."}
        return None

    def _report_money(self, value: Any) -> str:
        try:
            amount = float(value or 0)
            if amount.is_integer():
                return f"{int(amount)} BYN"
            return f"{amount:.2f} BYN"
        except Exception:
            return f"{value or 0} BYN"

    def _build_month_report_payload(self, result: Any, month_label: str) -> dict[str, Any]:
        if not getattr(result, "ok", False):
            error = getattr(result, "error", "") or getattr(result, "status", "") or "неизвестная ошибка"
            return {
                "month": month_label,
                "keyMetrics": {},
                "sections": [{"title": "Ошибка", "rows": [{"label": "Не удалось получить отчёт", "value": error}]}],
                "text": f"Отчёт МойКласс за {month_label} не сформирован. Ошибка: {error}",
            }
        data = result.data or {}
        month = str(data.get("month") or month_label)
        groups = data.get("groups") or {}
        lessons = data.get("lessons") or {}
        visits = data.get("visits") or {}
        clients = data.get("clients") or {}
        payments = data.get("payments") or {}
        users = data.get("users") or {}
        lesson_control = data.get("lessonControl") or {}

        active_students = int(clients.get("uniqueByRecords") or 0)
        attended_students = int(clients.get("uniqueByVisits") or 0)
        new_students = int(clients.get("usersInMonth") or users.get("count") or 0)
        lessons_count = int(lessons.get("count") or 0)
        visits_count = int(visits.get("count") or 0)
        missed_count = int(visits.get("missed") or 0)
        records_total = int(visits.get("recordsTotal") or 0)
        trial_records = int(visits.get("test") or 0)
        paid_records = int(visits.get("paidRecords") or 0)
        payments_count = int(payments.get("count") or 0)
        payments_sum = payments.get("sum") or 0
        groups_count = int(groups.get("count") or 0)

        attendance_available = bool(visits.get("available") or visits.get("ok"))
        payments_available = bool(payments.get("available") or payments.get("ok"))

        active_task_counts = {"payment": 0, "makeup": 0, "trial": 0, "total": 0}
        try:
            active_tasks = self.storage.list_client_manager_tasks(status="active", task_type="all", limit=1000)
            for task in active_tasks:
                t = str(task.get("task_type") or "")
                if t in active_task_counts:
                    active_task_counts[t] += 1
                active_task_counts["total"] += 1
        except Exception:
            active_tasks = []

        key_metrics = {
            "activeStudents": active_students,
            "attendedStudents": attended_students,
            "newStudents": new_students,
            "lessons": lessons_count,
            "recordsTotal": records_total,
            "visits": visits_count,
            "missed": missed_count,
            "trialRecords": trial_records,
            "paidRecords": paid_records,
            "paymentsCount": payments_count if payments_available else "недоступно",
            "paymentsSum": self._report_money(payments_sum) if payments_available else "недоступно",
            "groups": groups_count,
            "paymentTasks": active_task_counts.get("payment", 0),
            "makeupTasks": active_task_counts.get("makeup", 0),
            "trialTasks": active_task_counts.get("trial", 0),
            "clientTasks": active_task_counts.get("total", 0),
        }

        detail_cards = [
            {
                "kind": "payments",
                "title": "Оплаты",
                "subtitle": "Кому нужно оплатить следующий блок занятий",
                "accent": "yellow",
                "metrics": [
                    {"label": "Оплат за месяц", "value": payments_count if payments_available else "недоступно"},
                    {"label": "Сумма оплат", "value": self._report_money(payments_sum) if payments_available else "недоступно"},
                    {"label": "Активных задач по оплатам", "value": active_task_counts.get("payment", 0)},
                    {"label": "Базовый абонемент", "value": "239 BYN / 4 занятия"},
                ],
                "actions": [
                    "Проверить учеников с нулевым остатком занятий",
                    "Создать или обновить задачи по оплатам",
                    "Написать клиентам до следующего занятия",
                ],
            },
            {
                "kind": "makeups",
                "title": "Отработки",
                "subtitle": "Пропуски и постановка отработок",
                "accent": "red" if missed_count else "green",
                "metrics": [
                    {"label": "Пропусков за месяц", "value": missed_count if attendance_available else "недоступно"},
                    {"label": "Активных задач по отработкам", "value": active_task_counts.get("makeup", 0)},
                    {"label": "Срок", "value": "до следующего занятия / в течение 7 дней"},
                ],
                "actions": [
                    "Проверить пропуски учеников",
                    "Создать задачи по отработкам",
                    "Подобрать свободные окна преподавателей",
                ],
            },
            {
                "kind": "trials",
                "title": "Пробные",
                "subtitle": "Пробные записи и дальнейшая работа с клиентами",
                "accent": "blue",
                "metrics": [
                    {"label": "Пробных записей", "value": trial_records if attendance_available else "недоступно"},
                    {"label": "Активных ручных задач по пробным", "value": active_task_counts.get("trial", 0)},
                    {"label": "Новых учеников в базе", "value": new_students},
                ],
                "actions": [
                    "Проверить, кто пришёл на пробное",
                    "Написать клиентам после пробного",
                    "Если клиент готов - поставить регулярное занятие",
                ],
            },
        ]

        sections = [
            {
                "title": "Общие показатели",
                "rows": [
                    {"label": "Активные ученики/клиенты", "value": active_students, "note": "уникальные ученики в записях на занятия" if active_students else "если 0, API не отдал записи или за месяц нет данных"},
                    {"label": "Учеников с фактическим посещением", "value": attended_students},
                    {"label": "Новых учеников в базе", "value": new_students},
                    {"label": "Групп в базе", "value": groups_count},
                ],
            },
            {
                "title": "Занятия и посещения",
                "rows": [
                    {"label": "Занятий в месяце", "value": lessons_count},
                    {"label": "Записей на занятия", "value": records_total if attendance_available else "недоступно"},
                    {"label": "Посещений", "value": visits_count if attendance_available else "недоступно"},
                    {"label": "Пропусков", "value": missed_count if attendance_available else "недоступно"},
                    {"label": "Пробных записей", "value": trial_records if attendance_available else "недоступно"},
                    {"label": "Оплаченных записей", "value": paid_records if attendance_available else "недоступно"},
                ],
            },
            {
                "title": "Оплаты",
                "rows": [
                    {"label": "Оплат за месяц", "value": payments_count if payments_available else "недоступно"},
                    {"label": "Сумма оплат", "value": self._report_money(payments_sum) if payments_available else "недоступно"},
                    {"label": "Стоимость абонемента", "value": "239 BYN за 4 занятия"},
                    {"label": "Активных задач по оплатам", "value": active_task_counts.get("payment", 0)},
                    {"label": "Что сделать", "value": "обновить задачи из МойКласс", "note": "если остаток занятий 0 или меньше, должна появиться задача оплаты"},
                ],
            },
            {
                "title": "Отработки",
                "rows": [
                    {"label": "Пропуски за месяц", "value": missed_count if attendance_available else "недоступно"},
                    {"label": "Активных задач по отработкам", "value": active_task_counts.get("makeup", 0)},
                    {"label": "Срок постановки", "value": "желательно до следующего занятия / в течение 1 недели"},
                    {"label": "Что сделать", "value": "подобрать свободное окно", "note": "агент использует возможности преподавателей и подсказывает варианты"},
                ],
            },
            {
                "title": "Пробные",
                "rows": [
                    {"label": "Пробных записей", "value": trial_records if attendance_available else "недоступно"},
                    {"label": "Активных задач по пробным", "value": active_task_counts.get("trial", 0)},
                    {"label": "Новые ученики в базе", "value": new_students},
                    {"label": "Что сделать", "value": "проверить конверсию пробных", "note": "кому написать после пробного и кого поставить на регулярное занятие"},
                ],
            },
        ]
        if isinstance(lesson_control, dict) and lesson_control:
            sections.append({
                "title": "Контроль занятий",
                "rows": [
                    {"label": "Занятий в контроле", "value": lesson_control.get("total", 0)},
                    {"label": "Закрыто", "value": lesson_control.get("closed", 0)},
                    {"label": "Не закрыто после занятия", "value": lesson_control.get("notClosedPast", 0)},
                    {"label": "Проблемы", "value": lesson_control.get("problems", 0)},
                ],
            })

        text_lines = [
            f"Отчёт МойКласс за {month}",
            "",
            "Общие показатели:",
            f"- Активные ученики/клиенты: {active_students}",
            f"- Ученики с фактическим посещением: {attended_students}",
            f"- Новые ученики в базе: {new_students}",
            f"- Групп в базе: {groups_count}",
            "",
            "Занятия и посещения:",
            f"- Занятий в месяце: {lessons_count}",
            f"- Записей на занятия: {records_total if attendance_available else 'недоступно'}",
            f"- Посещений: {visits_count if attendance_available else 'недоступно'}",
            f"- Пропусков: {missed_count if attendance_available else 'недоступно'}",
            f"- Пробных записей: {trial_records if attendance_available else 'недоступно'}",
            "",
            "Оплаты:",
            f"- Оплат за месяц: {payments_count if payments_available else 'недоступно'}",
            f"- Сумма оплат: {self._report_money(payments_sum) if payments_available else 'недоступно'}",
            "- Базовый абонемент: 239 BYN за 4 занятия",
            "",
            "Задачи клиент-менеджера:",
            f"- Активных задач по оплатам: {active_task_counts.get('payment', 0)}",
            f"- Активных задач по отработкам: {active_task_counts.get('makeup', 0)}",
            f"- Активных задач по пробным: {active_task_counts.get('trial', 0)}",
            "",
            "Действия:",
            "- По оплатам: проверить клиентов с нулевым остатком и написать до следующего занятия.",
            "- По отработкам: проверить пропуски и подобрать свободные окна преподавателей.",
            "- По пробным: проверить, кто пришёл/не пришёл, и кому нужно написать после пробного.",
            "- Перед финальным отчётом сверить спорные данные в МойКласс.",
        ]
        if not attendance_available:
            text_lines.append("- Посещения/пропуски недоступны через текущий API-ответ МойКласс.")
        if not payments_available:
            text_lines.append("- Оплаты недоступны через текущий API-ответ МойКласс.")

        return {"month": month, "keyMetrics": key_metrics, "detailCards": detail_cards, "sections": sections, "text": "\n".join(text_lines)}

    def reports_monthly(self, auth: dict[str, Any], month: str = "") -> dict[str, Any]:
        denied = self._require_reports_access(auth)
        if denied:
            return denied
        month_value = str(month or "").strip()
        if not re.fullmatch(r"20\d{2}-\d{2}", month_value):
            month_value = date.today().strftime("%Y-%m")
        if not self.settings.moyklass_enabled:
            return {"ok": False, "error": "МойКласс отключён в настройках агента."}
        try:
            result = self.moyklass.get_month_analytics(month_value)
        except Exception as exc:
            log.exception("MoyKlass monthly report failed")
            return {"ok": False, "error": f"Не удалось получить отчёт МойКласс: {exc}"}
        report = self._build_month_report_payload(result, month_value)
        return {"ok": True, "month": report.get("month") or month_value, "report": report}

    def reports_monthly_children(self, auth: dict[str, Any], month: str = "") -> dict[str, Any]:
        role = self._role_for_user(int(auth["user_id"]))
        if role not in CHILDREN_REPORT_ROLES:
            return {"ok": False, "error": "Отчёт по детям доступен owner, admin, director, client_manager и operations."}
        month_value = str(month or "").strip()
        if not re.fullmatch(r"20\d{2}-\d{2}", month_value):
            month_value = date.today().strftime("%Y-%m")
        if not self.settings.moyklass_enabled:
            return {"ok": False, "error": "МойКласс отключён в настройках агента."}
        import time as _t
        t0 = _t.monotonic()
        try:
            result = self.moyklass.get_monthly_children_report(month_value)
        except Exception as exc:
            log.exception("[monthly-children-report] failed user_id=%s month=%s", auth["user_id"], month_value)
            return {"ok": False, "error": f"Не удалось получить данные из МойКласс: {exc}"}
        elapsed_ms = int((_t.monotonic() - t0) * 1000)
        if not result.ok:
            return {"ok": False, "error": result.error or "Ошибка МойКласс"}
        data = result.data or {}
        log.info(
            "[monthly-children-report] role=%s user_id=%s month=%s "
            "records_total=%s records_attended=%s unique_children=%s elapsed_ms=%s",
            role, auth["user_id"], month_value,
            data.get("records_total", 0), data.get("records_attended", 0),
            data.get("total_unique_children", 0), elapsed_ms,
        )
        return data

    def _require_client_tasks_access(self, auth: dict[str, Any]) -> dict[str, Any] | None:
        role = self._role_for_user(int(auth["user_id"]))
        if role not in CLIENT_TASK_ROLES:
            return {"ok": False, "error": "Задачи клиент-менеджера доступны клиент-менеджеру, владельцу и операционному менеджеру."}
        return None

    def client_tasks(self, auth: dict[str, Any], status: str = "active", task_type: str = "all", sync: bool = True) -> dict[str, Any]:
        denied = self._require_client_tasks_access(auth)
        if denied:
            return denied
        sync_result: dict[str, Any] = {}
        if sync and (status or "active") in {"active", "all", "new", "in_work"}:
            try:
                sync_result = self.sync_client_tasks_from_moyklass(auth, force=False)
            except Exception as exc:
                log.exception("Client tasks auto sync failed")
                sync_result = {"ok": False, "error": str(exc)[:300]}
        items = self.storage.list_client_manager_tasks(status=status or "active", task_type=task_type or "all", limit=300)
        return {"ok": True, "items": items, "autoSync": sync_result}

    def save_client_task(self, auth: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        denied = self._require_client_tasks_access(auth)
        if denied:
            return denied
        user_id = int(auth["user_id"])
        task_type = str(payload.get("taskType") or payload.get("task_type") or "").strip().lower()
        if task_type not in {"makeup", "trial", "payment"}:
            return {"ok": False, "error": "Выберите тип задачи: отработка, пробное или оплата."}
        client_name = str(payload.get("clientName") or "").strip()
        child_name = str(payload.get("childName") or "").strip()
        if task_type in {"makeup", "trial"} and not (client_name or child_name):
            return {"ok": False, "error": "Укажите клиента или ученика."}
        if task_type == "payment" and not (client_name or child_name):
            return {"ok": False, "error": "Укажите клиента/ученика для задачи по оплате."}
        is_new = not str(payload.get("id") or "").strip()
        item = self.storage.upsert_client_manager_task(
            created_by=user_id,
            task_id=payload.get("id") or "",
            task_type=task_type,
            status=str(payload.get("status") or "new"),
            priority=str(payload.get("priority") or "normal"),
            client_name=client_name,
            child_name=child_name,
            contact=str(payload.get("contact") or ""),
            desired_date=str(payload.get("desiredDate") or ""),
            desired_time=str(payload.get("desiredTime") or ""),
            location=str(payload.get("location") or ""),
            amount=str(payload.get("amount") or ""),
            payment_for=str(payload.get("paymentFor") or ""),
            deadline=str(payload.get("deadline") or ""),
            comment=str(payload.get("comment") or ""),
            result_text=str(payload.get("resultText") or ""),
            source_type=str(payload.get("sourceType") or "manual"),
            source_key=str(payload.get("sourceKey") or ""),
            lesson_id=str(payload.get("lessonId") or ""),
            mk_student_id=str(payload.get("mkStudentId") or ""),
        )
        # Only write "created" event for genuinely new tasks. The upsert can also
        # return an existing task (found via source_key), in which case events
        # already exist — skip to avoid duplicates.
        if is_new and item.get("id"):
            existing_events = self.storage.list_client_task_events(item["id"], limit=1)
            if not existing_events:
                actor_n = str((auth.get("user") or {}).get("first_name") or "")
                ln = str((auth.get("user") or {}).get("last_name") or "").strip()
                if ln:
                    actor_n = (actor_n + " " + ln).strip()
                if not actor_n:
                    st = self.storage.get_staff_user(user_id) or {}
                    actor_n = str(st.get("full_name") or st.get("username") or "")
                self.storage.add_client_task_event(
                    task_id=item["id"],
                    event_type="created",
                    actor_user_id=user_id,
                    actor_name=actor_n,
                    new_status=str(item.get("status") or "new"),
                )
        return {"ok": True, "item": item, "items": self.storage.list_client_manager_tasks(status="active", limit=300)}

    def update_client_task_status(self, auth: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        denied = self._require_client_tasks_access(auth)
        if denied:
            return denied
        task_id = payload.get("id") or payload.get("taskId") or ""
        status = str(payload.get("status") or "").strip().lower()
        if status not in {"new", "in_work", "waiting_client", "waiting_confirm", "done", "cancelled"}:
            return {"ok": False, "error": "Некорректный статус задачи."}
        user_id = int(auth["user_id"])
        old_task = self.storage.get_client_manager_task(task_id) or {}
        old_status = str(old_task.get("status") or "")
        ok = self.storage.update_client_manager_task_status(task_id, status, user_id)
        if not ok:
            return {"ok": False, "error": "Не удалось обновить задачу."}
        # Only record an event if the status actually changed.
        if old_status != status:
            actor_name = str((auth.get("user") or {}).get("first_name") or "")
            last = str((auth.get("user") or {}).get("last_name") or "").strip()
            if last:
                actor_name = (actor_name + " " + last).strip()
            if not actor_name:
                staff = self.storage.get_staff_user(user_id) or {}
                actor_name = str(staff.get("full_name") or staff.get("username") or "")
            self.storage.add_client_task_event(
                task_id=task_id,
                event_type="status_change",
                actor_user_id=user_id,
                actor_name=actor_name,
                old_status=old_status,
                new_status=status,
            )
        return {"ok": True, "items": self.storage.list_client_manager_tasks(status="active", limit=300)}

    def client_tasks_kpi(self, auth: dict[str, Any], date_from: str = "", date_to: str = "") -> dict[str, Any]:
        role = self._role_for_user(int(auth["user_id"]))
        if role not in KPI_ROLES:
            return {"ok": False, "error": "KPI задач доступен клиент-менеджеру, владельцу, методисту и операционному менеджеру."}
        try:
            kpi = self.storage.get_client_tasks_kpi(date_from=date_from, date_to=date_to)
            return {"ok": True, "kpi": kpi}
        except Exception as exc:
            log.exception("client_tasks_kpi failed")
            return {"ok": False, "error": f"Не удалось получить KPI: {exc}"}

    def _work_week_start(self, week: str = "current") -> str:
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        if str(week or "current").strip().lower() in {"next", "next_week", "следующая"}:
            monday = monday + timedelta(days=7)
        return monday.isoformat()

    def _work_week_label(self, week_start: str) -> str:
        try:
            start = date.fromisoformat(str(week_start)[:10])
        except Exception:
            start = date.today() - timedelta(days=date.today().weekday())
        end = start + timedelta(days=6)
        return f"{start.strftime('%d.%m')} - {end.strftime('%d.%m')}"

    def _require_work_schedule_access(self, auth: dict[str, Any]) -> dict[str, Any] | None:
        user_id = int(auth["user_id"])
        role = self._role_for_user(user_id)
        if role not in SCHEDULE_ROLES:
            return {"ok": False, "error": "Для вашей роли расписание работы пока недоступно."}
        return None

    def work_schedule(self, auth: dict[str, Any], week: str = "current") -> dict[str, Any]:
        denied = self._require_work_schedule_access(auth)
        if denied:
            return denied
        user_id = int(auth["user_id"])
        week_start = self._work_week_start(week)
        items = self.storage.list_teacher_work_schedule(user_id=user_id, week_start=week_start, active_only=True, limit=100)
        return {"ok": True, "items": items, "meta": {"week": week, "weekStart": week_start, "weekLabel": self._work_week_label(week_start)}}

    def admin_work_schedule(self, auth: dict[str, Any], week: str = "current") -> dict[str, Any]:
        denied = self._require_admin_tab(auth, "work-schedule")
        if denied:
            return denied
        week_start = self._work_week_start(week)
        items = self.storage.list_teacher_work_schedule(user_id=None, week_start=week_start, active_only=True, limit=800)
        staff_by_user = {int(u.get("user_id")): u for u in self.storage.list_staff_users(limit=1000) if u.get("user_id") is not None}
        for item in items:
            staff = staff_by_user.get(int(item.get("user_id") or 0)) or {}
            item["username"] = staff.get("username") or ""
            item["full_name"] = staff.get("full_name") or ""
            item["role"] = staff.get("role") or ""
            item["mk_teacher_name"] = staff.get("mk_teacher_name") or ""
        return {"ok": True, "items": items, "meta": {"week": week, "weekStart": week_start, "weekLabel": self._work_week_label(week_start)}}

    @staticmethod
    def _time_to_minutes(value: str) -> int:
        m = re.match(r"^(\d{2}):(\d{2})$", str(value or "").strip())
        if not m:
            return -1
        return int(m.group(1)) * 60 + int(m.group(2))

    def _find_work_slot_overlap(self, user_id: int, week_start: str, day: int, start: str, end: str, slot_id: Any = "") -> dict[str, Any] | None:
        start_m = self._time_to_minutes(start)
        end_m = self._time_to_minutes(end)
        if start_m < 0 or end_m < 0:
            return None
        current_id = str(slot_id or "").strip()
        for item in self.storage.list_teacher_work_schedule(user_id=user_id, week_start=week_start, active_only=True, limit=200):
            if current_id and str(item.get("id") or "") == current_id:
                continue
            if int(item.get("day_of_week") or 0) != int(day):
                continue
            other_start = self._time_to_minutes(str(item.get("start_time") or ""))
            other_end = self._time_to_minutes(str(item.get("end_time") or ""))
            if other_start < 0 or other_end < 0:
                continue
            if start_m < other_end and end_m > other_start:
                return item
        return None

    def save_work_schedule_slot(self, auth: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        denied = self._require_work_schedule_access(auth)
        if denied:
            return denied
        user_id = int(auth["user_id"])
        day = _safe_int(payload.get("dayOfWeek"), -1)
        start = str(payload.get("startTime") or "").strip()[:5]
        end = str(payload.get("endTime") or "").strip()[:5]
        location = str(payload.get("location") or "").strip()
        # Преподаватели Yellow Club универсальные: тип занятия и курс не выбираем.
        # Окно означает свободную возможность для пробного, отработки, замены, регулярного или дополнительного занятия.
        work_type = "Любое"
        note = str(payload.get("note") or "").strip()
        slot_id = payload.get("id") or ""
        week = str(payload.get("week") or "current").strip()
        week_start = self._work_week_start(week)
        if day < 0 or day > 6:
            return {"ok": False, "error": "Выберите день недели."}
        if not re.match(r"^\d{2}:\d{2}$", start) or not re.match(r"^\d{2}:\d{2}$", end):
            return {"ok": False, "error": "Укажите время в формате HH:MM."}
        if start >= end:
            return {"ok": False, "error": "Время окончания должно быть позже времени начала."}
        overlap = self._find_work_slot_overlap(user_id=user_id, week_start=week_start, day=day, start=start, end=end, slot_id=slot_id)
        if overlap:
            return {"ok": False, "error": f"Это окно пересекается с уже добавленным временем: {str(overlap.get('start_time') or '')[:5]}-{str(overlap.get('end_time') or '')[:5]}."}
        row = self.storage.upsert_teacher_work_slot(
            user_id=user_id,
            mk_teacher_id=self._mk_teacher_id_for_user(user_id),
            day_of_week=day,
            start_time=start,
            end_time=end,
            location=location,
            work_type=work_type,
            note=note,
            week_start=week_start,
            slot_id=slot_id,
        )
        items = self.storage.list_teacher_work_schedule(user_id=user_id, week_start=week_start, active_only=True, limit=100)
        return {"ok": True, "item": row, "items": items, "meta": {"week": week, "weekStart": week_start, "weekLabel": self._work_week_label(week_start)}}

    def delete_work_schedule_slot(self, auth: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        denied = self._require_work_schedule_access(auth)
        if denied:
            return denied
        user_id = int(auth["user_id"])
        slot_id = payload.get("id") or ""
        week = str(payload.get("week") or "current").strip()
        week_start = self._work_week_start(week)
        ok = self.storage.delete_teacher_work_slot(user_id, slot_id)
        if not ok:
            return {"ok": False, "error": "Не удалось удалить интервал."}
        return {"ok": True, "items": self.storage.list_teacher_work_schedule(user_id=user_id, week_start=week_start, active_only=True, limit=100), "meta": {"week": week, "weekStart": week_start, "weekLabel": self._work_week_label(week_start)}}

    def _require_open_slots_access(self, auth: dict[str, Any]) -> dict[str, Any] | None:
        user_id = int(auth["user_id"])
        role = self._role_for_user(user_id)
        if role not in OPEN_SLOTS_ROLES and role not in ADMIN_ROLES:
            return {"ok": False, "error": "Просмотр свободных окон доступен клиент-менеджеру, методисту и админу."}
        return None

    def open_slots(self, auth: dict[str, Any], week: str = "current", location: str = "all") -> dict[str, Any]:
        denied = self._require_open_slots_access(auth)
        if denied:
            return denied
        week_start = self._work_week_start(week)
        items = self.storage.list_teacher_work_schedule(user_id=None, week_start=week_start, active_only=True, limit=1000)
        staff_by_user = {int(u.get("user_id")): u for u in self.storage.list_staff_users(limit=1000) if u.get("user_id") is not None}
        try:
            start_date = date.fromisoformat(str(week_start)[:10])
        except Exception:
            start_date = date.today() - timedelta(days=date.today().weekday())
        location_filter = str(location or "all").strip().lower()
        filtered: list[dict[str, Any]] = []
        for item in items:
            loc = str(item.get("location") or "Любой формат").strip() or "Любой формат"
            if location_filter not in {"", "all", "все"} and loc.lower() != location_filter:
                continue
            staff = staff_by_user.get(int(item.get("user_id") or 0)) or {}
            teacher_name = staff.get("mk_teacher_name") or staff.get("full_name") or staff.get("username") or item.get("mk_teacher_id") or f"ID {item.get('user_id') or ''}".strip()
            try:
                day = max(0, min(6, int(item.get("day_of_week") or 0)))
            except Exception:
                day = 0
            slot_date = start_date + timedelta(days=day)
            row = dict(item)
            row["teacher_name"] = teacher_name
            row["teacher_full_name"] = staff.get("full_name") or ""
            row["teacher_username"] = staff.get("username") or ""
            row["role"] = staff.get("role") or ""
            row["location"] = loc
            row["date"] = slot_date.isoformat()
            row["date_label"] = slot_date.strftime("%d.%m")
            row["day_name"] = ["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"][day]
            row["day_short"] = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"][day]
            filtered.append(row)
        filtered.sort(key=lambda x: (str(x.get("date") or ""), str(x.get("start_time") or ""), str(x.get("teacher_name") or "")))
        unique_teachers = len({str(x.get("user_id") or x.get("mk_teacher_id") or x.get("teacher_name")) for x in filtered})
        return {
            "ok": True,
            "items": filtered,
            "meta": {
                "week": week,
                "weekStart": week_start,
                "weekLabel": self._work_week_label(week_start),
                "total": len(filtered),
                "teachers": unique_teachers,
            },
        }

    def upload_prep_result(self, auth: dict[str, Any], fields: dict[str, str], files: dict[str, dict[str, Any]]) -> dict[str, Any]:
        user_id = int(auth["user_id"])
        lesson_id = str(fields.get("lessonId") or "").strip()
        if not lesson_id:
            return {"ok": False, "error": "Не указан lessonId."}
        detail = self.lesson_detail(auth, lesson_id)
        if not detail.get("ok"):
            return detail
        file = files.get("file")
        if not file or not file.get("content"):
            return {"ok": False, "error": "Файл не прикреплён."}
        content = file.get("content") or b""
        if len(content) > 40 * 1024 * 1024:
            return {"ok": False, "error": "Файл слишком большой. Максимум 40 МБ."}
        original_name = _safe_filename(str(file.get("filename") or "result.bin"))
        lesson_dir = BASE_DIR / "data" / "prep_results" / re.sub(r"[^0-9A-Za-zА-Яа-яёЁ_.-]+", "_", lesson_id)
        lesson_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        stored_name = f"{stamp}_{user_id}_{original_name}"
        stored_path = lesson_dir / stored_name
        stored_path.write_bytes(content)
        result_row = self.storage.add_teacher_prep_result_file(
            lesson_id=lesson_id,
            teacher_user_id=user_id,
            file_name=original_name,
            stored_path=str(stored_path),
            mime_type=str(file.get("content_type") or "application/octet-stream"),
            size_bytes=len(content),
        )
        # Notify senior teacher/reviewers and attach the actual file.
        lesson = detail.get("lesson") or {}
        caption = "\n".join([
            "📤 Результат подготовки на проверку",
            f"Преподаватель: {lesson.get('teacher') or user_id}",
            f"Группа: {lesson.get('group') or '-'}",
            f"Дата: {lesson.get('date') or '-'}",
            f"Время: {lesson.get('time') or '-'}",
            f"Тема: {lesson.get('topic') or '-'}",
            f"Файл: {original_name}",
            "Проверка: Mini App -> Админ -> Проверка работ.",
        ])
        recipients = self._prep_result_reviewer_ids()
        sent_documents = 0
        for reviewer_id in recipients:
            ok, err = _telegram_send_document(
                self.settings.telegram_bot_token,
                int(reviewer_id),
                stored_path,
                original_name,
                caption,
                str(file.get("content_type") or "application/octet-stream"),
            )
            self.storage.add_notification_log("prep_result_document_sent", caption, int(reviewer_id), lesson_id, result_row.get("id"), ok, err)
            if ok:
                sent_documents += 1
            else:
                fallback_text = caption + "\n\n⚠️ Файл сохранён в Mini App, но Telegram не смог отправить документ. Откройте его через Админ -> Проверка работ -> Скачать файл."
                text_ok, text_err = _telegram_send(self.settings.telegram_bot_token, int(reviewer_id), fallback_text)
                self.storage.add_notification_log("prep_result_document_fallback", fallback_text, int(reviewer_id), lesson_id, result_row.get("id"), text_ok, text_err or err)

        if not recipients:
            response_message = "Файл сохранён, но не настроен получатель проверки. Добавьте SENIOR_TEACHER_IDS в .env или назначьте сотруднику роль methodist."
        elif sent_documents:
            response_message = "Результат отправлен старшему преподавателю в Telegram. Статус: ожидает проверки."
        else:
            response_message = "Файл сохранён и поставлен на проверку, но Telegram не смог отправить документ. Старший преподаватель сможет скачать его в Mini App."
        return {"ok": True, "file": result_row, "message": response_message, "sentDocuments": sent_documents, "reviewers": recipients}

    def review_prep_result(self, auth: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        denied = self._require_admin(auth)
        if denied:
            return denied
        file_id = payload.get("fileId") or payload.get("id")
        decision = str(payload.get("decision") or "").strip().lower()
        comment = str(payload.get("comment") or "").strip()
        if decision not in {"approved", "rejected"}:
            return {"ok": False, "error": "Решение должно быть approved или rejected."}
        row = self.storage.review_teacher_prep_result_file(file_id, int(auth["user_id"]), decision, comment)
        if not row:
            return {"ok": False, "error": "Файл не найден."}
        lesson_id = str(row.get("lesson_id") or "")
        teacher_id = row.get("teacher_user_id")
        status_text = "подтверждён" if decision == "approved" else "отклонён"
        icon = "✅" if decision == "approved" else "❌"
        lesson_label = ""
        control = self.storage.get_teacher_lesson_control(lesson_id) or {}
        if control:
            lesson_label = f"\nЗанятие: {control.get('group_name') or '-'} · {control.get('lesson_date') or '-'} {control.get('lesson_time') or ''}"
        msg = f"{icon} Результат подготовки {status_text} старшим преподавателем.{lesson_label}"
        if comment:
            msg += f"\nКомментарий: {comment}"
        msg += "\n\nОбратная связь также отображается в карточке занятия."
        ok, err = _telegram_send(self.settings.telegram_bot_token, int(teacher_id) if teacher_id else None, msg)
        self.storage.add_notification_log("prep_result_reviewed", msg, int(teacher_id) if teacher_id else None, lesson_id, None, ok, err)
        return {"ok": True, "item": row}

    def action(self, auth: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        user_id = int(auth["user_id"])
        lesson_id = str(payload.get("lessonId") or "").strip()
        action = str(payload.get("action") or "").strip()
        comment = str(payload.get("comment") or "").strip()
        if not lesson_id:
            return {"ok": False, "error": "Не указан lessonId."}
        # Re-read lesson to verify ownership and fill control fields.
        detail = self.lesson_detail(auth, lesson_id)
        if not detail.get("ok"):
            return detail
        if action == "prepare_ready":
            row = self.storage.mark_teacher_preparation(lesson_id, user_id, "ready")
        elif action == "prepare_help":
            row = self.storage.mark_teacher_preparation(lesson_id, user_id, "needs_help", comment=comment)
        elif action == "prep_material_done":
            row = self.storage.mark_teacher_lesson_step(lesson_id, "prep_material", "done", user_id=user_id)
        elif action == "prep_material_not_checked":
            row = self.storage.mark_teacher_lesson_step(lesson_id, "prep_material", "not_checked", user_id=user_id)
        elif action == "prep_video_done":
            row = self.storage.mark_teacher_lesson_step(lesson_id, "prep_video", "done", user_id=user_id)
        elif action == "prep_video_not_checked":
            row = self.storage.mark_teacher_lesson_step(lesson_id, "prep_video", "not_checked", user_id=user_id)
        elif action == "prep_practice_done":
            row = self.storage.mark_teacher_lesson_step(lesson_id, "prep_practice", "done", user_id=user_id)
        elif action == "prep_practice_not_checked":
            row = self.storage.mark_teacher_lesson_step(lesson_id, "prep_practice", "not_checked", user_id=user_id)
        elif action == "prep_result_sent":
            return {"ok": False, "error": "Теперь результат подготовки нужно отправлять файлом через кнопку “Отправить результат”."}
        elif action in {"prep_study_done", "prep_all_done"}:
            row = self.storage.upsert_teacher_lesson_control(
                lesson_id,
                teacher_user_id=user_id,
                prep_material_status="done",
                prep_video_status="done",
                prep_practice_status="done",
            )
        elif action == "parent_report_done":
            row = self.storage.mark_teacher_lesson_step(lesson_id, "parent_report", "done", user_id=user_id)
        elif action == "parent_report_not_checked":
            row = self.storage.mark_teacher_lesson_step(lesson_id, "parent_report", "not_checked", user_id=user_id)
        elif action == "myclass_done":
            row = self.storage.mark_teacher_lesson_step(lesson_id, "myclass", "done", user_id=user_id)
        elif action == "myclass_not_checked":
            row = self.storage.mark_teacher_lesson_step(lesson_id, "myclass", "not_checked", user_id=user_id)
        elif action == "works_done":
            row = self.storage.mark_teacher_lesson_step(lesson_id, "works", "done", user_id=user_id)
        elif action == "works_not_checked":
            row = self.storage.mark_teacher_lesson_step(lesson_id, "works", "not_checked", user_id=user_id)
        elif action == "classroom_done":
            row = self.storage.mark_teacher_lesson_step(lesson_id, "classroom", "done", user_id=user_id)
        elif action == "classroom_not_checked":
            row = self.storage.mark_teacher_lesson_step(lesson_id, "classroom", "not_checked", user_id=user_id)
        elif action == "lesson_comment":
            if not comment.strip():
                return {"ok": False, "error": "Комментарий пустой."}
            row = self.storage.set_teacher_lesson_comment(lesson_id, user_id, comment)
        elif action == "problem":
            row = self.storage.mark_teacher_lesson_problem(lesson_id, user_id, comment or "Проблема без комментария")
        elif action == "close":
            current = self.storage.get_teacher_lesson_control(lesson_id) or {}
            missing = _missing_close_steps(current)
            if missing and str(current.get("problem_status") or "") != "problem":
                return {"ok": False, "error": "Нельзя закрыть занятие. Не отмечено: " + ", ".join(missing)}
            row = self.storage.close_teacher_lesson(lesson_id, user_id)
        else:
            return {"ok": False, "error": "Неизвестное действие."}
        row = row or self.storage.get_teacher_lesson_control(lesson_id) or {}
        if action.startswith("prep_"):
            if _all_prep_steps_done(row):
                row = self.storage.mark_teacher_preparation(lesson_id, user_id, "ready")
            elif action.endswith("_not_checked"):
                row = self.storage.mark_teacher_preparation(lesson_id, user_id, "not_started")
        return {"ok": True, "control": row}


CTX = MiniAppContext()


class MiniAppHandler(BaseHTTPRequestHandler):
    server_version = "YellowClubMiniApp/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        log.info("%s - %s", self.address_string(), fmt % args)

    def _send_json(self, data: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, default=_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(404, "Not found")
            return
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") or ctype in {"application/javascript"} else ""))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_download(self, path: Path, filename: str, mime_type: str = "") -> None:
        if not path.exists() or not path.is_file():
            self.send_error(404, "Not found")
            return
        ctype = mime_type or mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        body = path.read_bytes()
        safe_name = _safe_filename(filename or path.name)
        quoted = urllib.parse.quote(safe_name)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quoted}")
        self.send_header("Cache-Control", "private, no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self, params: dict[str, str]) -> dict[str, Any]:
        init_data = params.get("initData") or ""
        return CTX.validate_init_data(
            init_data,
            dev_user_id=params.get("dev_user_id", ""),
            unsafe_user_id=params.get("unsafe_user_id", ""),
            yc_user_id=params.get("yc_user_id", ""),
            yc_ts=params.get("yc_ts", ""),
            yc_sig=params.get("yc_sig", ""),
        )

    def _read_body_json(self) -> dict[str, Any]:
        length = _safe_int(self.headers.get("Content-Length"), 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def do_GET(self) -> None:  # noqa: N802
        path, params = _parse_query(self.path)
        try:
            if path == "/" or path == "/index.html":
                return self._send_file(WEB_DIR / "index.html")
            if path.startswith("/static/"):
                rel = path.removeprefix("/static/").lstrip("/")
                safe = Path(rel)
                if ".." in safe.parts:
                    self.send_error(403, "Forbidden")
                    return
                return self._send_file(WEB_DIR / safe)
            if path == "/health":
                return self._send_json({"ok": True, "service": "yellow-club-miniapp"})
            if path.startswith("/api/"):
                auth = self._auth(params)
                if not auth.get("ok"):
                    return self._send_json(auth, status=401)
                if path == "/api/me":
                    return self._send_json({"ok": True, "me": CTX.me(auth)})
                if path == "/api/lessons":
                    return self._send_json(CTX.lessons(auth, days=max(1, min(_safe_int(params.get("days"), 7), 31))))
                if path == "/api/tasks":
                    return self._send_json(CTX.tasks(auth))
                if path == "/api/client-tasks":
                    return self._send_json(CTX.client_tasks(auth, status=params.get("status", "active"), task_type=params.get("type", "all"), sync=params.get("sync", "1") != "0"))
                if path == "/api/work-schedule":
                    return self._send_json(CTX.work_schedule(auth, week=params.get("week", "current")))
                if path == "/api/open-slots":
                    return self._send_json(CTX.open_slots(auth, week=params.get("week", "current"), location=params.get("location", "all")))
                if path == "/api/reports/monthly":
                    return self._send_json(CTX.reports_monthly(auth, month=params.get("month", "")))
                if path == "/api/reports/monthly-children":
                    return self._send_json(CTX.reports_monthly_children(auth, month=params.get("month", "")))
                if path == "/api/lesson":
                    return self._send_json(CTX.lesson_detail(auth, params.get("id", "")))
                if path == "/api/report":
                    return self._send_json(CTX.parent_report(auth, params.get("lessonId", ""), variant=params.get("variant", "normal"), details=params.get("details", "")))
                if path == "/api/admin/overview":
                    return self._send_json(CTX.admin_overview(auth))
                if path == "/api/admin/teachers":
                    return self._send_json(CTX.admin_teachers(auth))
                if path == "/api/admin/moyklass/teachers":
                    _q = params.get("q", "")
                    _inc = str(params.get("include_with_no_lessons", "")).lower() in ("1", "true")
                    return self._send_json(CTX.admin_moyklass_teachers(auth, q=_q, include_with_no_lessons=_inc))
                if path == "/api/admin/users":
                    return self._send_json(CTX.admin_users(auth))
                if path.startswith("/api/admin/teacher-diagnostics/"):
                    _diag_uid = path[len("/api/admin/teacher-diagnostics/"):]
                    return self._send_json(CTX.teacher_diagnostics(auth, _diag_uid))
                if path == "/api/admin/tasks":
                    return self._send_json(CTX.admin_all_tasks(auth))
                if path == "/api/admin/notifications":
                    return self._send_json(CTX.admin_notifications(auth))
                if path == "/api/admin/prep-results":
                    return self._send_json(CTX.admin_prep_results(auth))
                if path == "/api/admin/lesson-controls":
                    return self._send_json(CTX.admin_lesson_controls(auth))
                if path == "/api/admin/work-schedule":
                    return self._send_json(CTX.admin_work_schedule(auth, week=params.get("week", "current")))
                if path == "/api/prep-result-download":
                    info = CTX.prep_result_download_info(auth, params.get("fileId", ""))
                    if not info.get("ok"):
                        return self._send_json(info, status=404 if "найден" in str(info.get("error") or "") else 403)
                    return self._send_download(info["path"], info["filename"], info.get("mimeType") or "")
                if path == "/api/client-tasks-kpi":
                    return self._send_json(CTX.client_tasks_kpi(auth, date_from=params.get("date_from", ""), date_to=params.get("date_to", "")))
                if path == "/api/intern/track":
                    return self._send_json(CTX.intern_track(auth))
                if path == "/api/intern/upcoming-lessons":
                    return self._send_json(CTX.intern_upcoming_lessons(auth))
                if path == "/api/intern/observations":
                    return self._send_json(CTX.intern_track(auth))
                if path == "/api/intern/work-download":
                    info = CTX.intern_work_download(auth, params.get("id", ""))
                    if not info.get("ok"):
                        return self._send_json(info, status=404 if "не найден" in str(info.get("error") or "").lower() else 403)
                    return self._send_download(info["path"], info["filename"], info.get("mimeType") or "")
                if path == "/api/admin/interns":
                    return self._send_json(CTX.admin_interns(auth))
                if path == "/api/admin/notion-status":
                    return self._send_json(CTX.notion_status(auth))
                if path == "/api/admin/mk-comment-probe":
                    return self._send_json(CTX.admin_mk_comment_probe(auth, params.get("id", "")))
                if path == "/api/food/my-children":
                    return self._send_json(CTX.food_my_children(auth))
                if path == "/api/food/my-orders":
                    return self._send_json(CTX.food_my_orders(auth))
                if path == "/api/food/staff/my-order":
                    return self._send_json(CTX.food_staff_my_order(auth, params))
                if path == "/api/food/menus":
                    return self._send_json(CTX.food_list_menus(auth))
                if path == "/api/food/active-menus":
                    return self._send_json(CTX.food_active_menus(auth))
                if path == "/api/food/staff/active-menus":
                    return self._send_json(CTX.food_staff_active_menus(auth))
                if path == "/api/food/staff/tomorrow-teachers":
                    return self._send_json(CTX.food_staff_tomorrow_teachers(auth))
                if path == "/api/food/debug/data-status":
                    return self._send_json(CTX.food_debug_data_status(auth))
                if path == "/api/food/debug/auto-reminder-status":
                    return self._send_json(CTX.food_auto_reminder_status(auth))
                if path == "/api/food/reports/shift":
                    return self._send_json(CTX.food_shift_report(auth, params))
                if path == "/api/food/teacher/class-orders":
                    return self._send_json(CTX.food_teacher_class_orders(auth, params))
                if path == "/api/food/kitchen/menus":
                    return self._send_json(CTX.food_kitchen_menus(auth))
                if path.startswith("/api/food/kitchen/menus/"):
                    _krest = path[len("/api/food/kitchen/menus/"):]
                    _kparts = _krest.split("/")
                    if len(_kparts) == 2 and _kparts[1] == "summary":
                        return self._send_json(CTX.food_kitchen_summary(auth, _kparts[0]))
                if path.startswith("/api/food/menus/"):
                    _menu_rest = path[len("/api/food/menus/"):]
                    _menu_parts = _menu_rest.split("/")
                    if len(_menu_parts) == 1 and _menu_parts[0]:
                        return self._send_json(CTX.food_get_menu(auth, _menu_parts[0]))
                    if len(_menu_parts) == 2 and _menu_parts[1] == "summary":
                        return self._send_json(CTX.food_menu_summary(auth, _menu_parts[0]))
                    if len(_menu_parts) == 2 and _menu_parts[1] == "audit":
                        return self._send_json(CTX.food_summary_audit(auth, _menu_parts[0]))
                    if len(_menu_parts) == 2 and _menu_parts[1] == "admin-persons":
                        return self._send_json(CTX.food_admin_persons_for_menu(auth, _menu_parts[0]))
                return self._send_json({"ok": False, "error": "Unknown API route"}, status=404)
            self.send_error(404, "Not found")
        except Exception as exc:
            log.exception("Mini app GET error")
            self._send_json({"ok": False, "error": str(exc)}, status=500)

    def do_POST(self) -> None:  # noqa: N802
        path, params = _parse_query(self.path)
        try:
            content_type = self.headers.get("Content-Type", "")
            if content_type.lower().startswith("multipart/form-data"):
                length = _safe_int(self.headers.get("Content-Length"), 0)
                raw = self.rfile.read(length) if length > 0 else b""
                fields, files = _parse_multipart_form(content_type, raw)
                auth = CTX.validate_init_data(
                    str(fields.get("initData") or params.get("initData") or ""),
                    dev_user_id=str(fields.get("dev_user_id") or params.get("dev_user_id") or ""),
                    unsafe_user_id=str(fields.get("unsafe_user_id") or params.get("unsafe_user_id") or ""),
                    yc_user_id=str(fields.get("yc_user_id") or params.get("yc_user_id") or ""),
                    yc_ts=str(fields.get("yc_ts") or params.get("yc_ts") or ""),
                    yc_sig=str(fields.get("yc_sig") or params.get("yc_sig") or ""),
                )
                if not auth.get("ok"):
                    return self._send_json(auth, status=401)
                if path == "/api/prep-result-upload":
                    return self._send_json(CTX.upload_prep_result(auth, fields, files))
                if path == "/api/intern/work-upload":
                    return self._send_json(CTX.upload_intern_work(auth, fields, files))
                if path.startswith("/api/food/menus/"):
                    _frest = path[len("/api/food/menus/"):]
                    _fparts = _frest.split("/")
                    if len(_fparts) == 2 and _fparts[1] == "ocr-preview":
                        return self._send_json(CTX.food_ocr_preview(auth, _fparts[0], files))
                return self._send_json({"ok": False, "error": "Unknown multipart API route"}, status=404)

            body = self._read_body_json()
            init_data = str(body.get("initData") or params.get("initData") or "")
            auth = CTX.validate_init_data(
                init_data,
                dev_user_id=str(body.get("dev_user_id") or params.get("dev_user_id") or ""),
                unsafe_user_id=str(body.get("unsafe_user_id") or params.get("unsafe_user_id") or ""),
                yc_user_id=str(body.get("yc_user_id") or params.get("yc_user_id") or ""),
                yc_ts=str(body.get("yc_ts") or params.get("yc_ts") or ""),
                yc_sig=str(body.get("yc_sig") or params.get("yc_sig") or ""),
            )
            if not auth.get("ok"):
                return self._send_json(auth, status=401)
            if path == "/api/action":
                return self._send_json(CTX.action(auth, body))
            if path == "/api/ask":
                return self._send_json(CTX.ask_agent(auth, body))
            if path == "/api/client-task-save":
                return self._send_json(CTX.save_client_task(auth, body))
            if path == "/api/client-task-status":
                return self._send_json(CTX.update_client_task_status(auth, body))
            if path == "/api/client-tasks-sync":
                return self._send_json(CTX.sync_client_tasks_from_moyklass(auth, force=True))
            if path == "/api/work-schedule-save":
                return self._send_json(CTX.save_work_schedule_slot(auth, body))
            if path == "/api/work-schedule-delete":
                return self._send_json(CTX.delete_work_schedule_slot(auth, body))
            if path == "/api/admin/schedule-check":
                return self._send_json(CTX.schedule_check(auth, days=_safe_int(body.get("days"), 30), notify=bool(body.get("notify"))))
            if path.startswith("/api/admin/teacher-diagnostics/") and path.endswith("/refresh"):
                _td_uid = path[len("/api/admin/teacher-diagnostics/"):-len("/refresh")]
                return self._send_json(CTX.teacher_diagnostics_refresh(auth, _td_uid))
            if path == "/api/admin/prep-result-review":
                return self._send_json(CTX.review_prep_result(auth, body))
            if path == "/api/intern/observation-signup":
                return self._send_json(CTX.intern_sign_up_observation(auth, body))
            if path == "/api/intern/observation-comment":
                return self._send_json(CTX.intern_submit_observation(auth, body))
            if path == "/api/intern/work-submit":
                return self._send_json(CTX.intern_submit_work(auth, body))
            if path == "/api/intern/demo-book":
                return self._send_json(CTX.intern_book_demo(auth, body))
            if path == "/api/intern/demo-feedback":
                return self._send_json(CTX.intern_submit_demo_feedback(auth, body))
            if path == "/api/admin/intern/review-work":
                return self._send_json(CTX.admin_intern_review_work(auth, body))
            if path == "/api/admin/intern/review-demo":
                return self._send_json(CTX.admin_intern_review_demo(auth, body))
            if path == "/api/intern/test-reset":
                return self._send_json(CTX.intern_test_reset_action(auth, body))
            if path == "/api/intern/test-set-stage":
                return self._send_json(CTX.intern_test_set_stage_action(auth, body))
            if path == "/api/test-role":
                return self._send_json(CTX.set_test_role(auth, body))
            if path == "/api/admin/set-user-role":
                return self._send_json(CTX.admin_set_user_role(auth, body))
            if path.startswith("/api/admin/staff/") and path.endswith("/deactivate"):
                _staff_uid = path[len("/api/admin/staff/"):-len("/deactivate")]
                return self._send_json(CTX.admin_deactivate_staff(auth, _staff_uid))
            if path.startswith("/api/admin/staff/") and path.endswith("/activate"):
                _staff_uid = path[len("/api/admin/staff/"):-len("/activate")]
                return self._send_json(CTX.admin_activate_staff(auth, _staff_uid))
            if path.startswith("/api/admin/staff/") and path.endswith("/sync-mk-name"):
                _staff_uid = path[len("/api/admin/staff/"):-len("/sync-mk-name")]
                return self._send_json(CTX.admin_sync_mk_name(auth, _staff_uid))
            if path.startswith("/api/admin/staff/") and path.endswith("/unlink-teacher"):
                _staff_uid = path[len("/api/admin/staff/"):-len("/unlink-teacher")]
                return self._send_json(CTX.admin_unlink_teacher(auth, _staff_uid))
            if path.startswith("/api/admin/staff/") and path.endswith("/link-moyklass-teacher"):
                _staff_uid = path[len("/api/admin/staff/"):-len("/link-moyklass-teacher")]
                return self._send_json(CTX.admin_link_moyklass_teacher(auth, _staff_uid, body))
            if path == "/api/admin/moyklass/staff-link":
                return self._send_json(CTX.admin_moyklass_staff_link(auth, body))
            if path == "/api/food/link-child":
                return self._send_json(CTX.food_link_child(auth, body))
            if path == "/api/food/debug/sync-camp-children":
                return self._send_json(CTX.food_debug_sync_camp_children(auth, body))
            if path == "/api/food/debug/clear-camp-children":
                return self._send_json(CTX.food_debug_clear_camp_children(auth))
            if path == "/api/food/debug/cleanup-duplicates":
                return self._send_json(CTX.food_debug_cleanup_duplicates(auth))
            if path == "/api/food/camp-children":
                return self._send_json(CTX.food_list_camp_children(auth))
            if path == "/api/food/camp-children/generate-codes":
                return self._send_json(CTX.food_generate_codes_all(auth))
            if path.startswith("/api/food/camp-children/") and path.endswith("/generate-code"):
                _mk_id = path[len("/api/food/camp-children/"):-len("/generate-code")]
                return self._send_json(CTX.food_generate_code_for_child(auth, _mk_id))
            if path == "/api/food/camp-children/relink":
                return self._send_json(CTX.food_relink_child(auth, body))
            if path == "/api/food/orders":
                return self._send_json(CTX.food_submit_order(auth, body))
            if path == "/api/food/orders/skip":
                return self._send_json(CTX.food_skip_order(auth, body))
            if path == "/api/food/staff/orders":
                return self._send_json(CTX.food_staff_submit_order(auth, body))
            if path == "/api/food/staff/orders/skip":
                return self._send_json(CTX.food_staff_skip_order(auth, body))
            if path == "/api/food/menus":
                return self._send_json(CTX.food_create_menu(auth, body))
            if path == "/api/food/orders/admin-manual-child":
                return self._send_json(CTX.food_admin_manual_child_order(auth, body))
            if path == "/api/food/staff-orders/admin-manual-staff":
                return self._send_json(CTX.food_admin_manual_staff_order(auth, body))
            if path.startswith("/api/food/orders/"):
                _orest = path[len("/api/food/orders/"):]
                _oparts = _orest.split("/")
                if len(_oparts) == 2 and _oparts[1] == "delete":
                    return self._send_json(CTX.food_delete_child_order(auth, _oparts[0]))
                if len(_oparts) == 2 and _oparts[1] == "admin-edit":
                    return self._send_json(CTX.food_admin_edit_child_order(auth, _oparts[0], body))
            if path.startswith("/api/food/staff-orders/"):
                _srest = path[len("/api/food/staff-orders/"):]
                _sparts = _srest.split("/")
                if len(_sparts) == 2 and _sparts[1] == "delete":
                    return self._send_json(CTX.food_delete_staff_order_by_id(auth, _sparts[0]))
                if len(_sparts) == 2 and _sparts[1] == "admin-edit":
                    return self._send_json(CTX.food_admin_edit_staff_order(auth, _sparts[0], body))
            if path.startswith("/api/food/menus/"):
                _mrest = path[len("/api/food/menus/"):]
                _mparts = _mrest.split("/")
                if len(_mparts) == 2 and _mparts[1] == "update":
                    return self._send_json(CTX.food_update_menu(auth, _mparts[0], body))
                if len(_mparts) == 2 and _mparts[1] == "publish":
                    return self._send_json(CTX.food_publish_menu(auth, _mparts[0]))
                if len(_mparts) == 2 and _mparts[1] == "close":
                    return self._send_json(CTX.food_close_menu(auth, _mparts[0]))
                if len(_mparts) == 2 and _mparts[1] == "delete":
                    return self._send_json(CTX.food_delete_menu(auth, _mparts[0]))
                if len(_mparts) == 2 and _mparts[1] == "items":
                    return self._send_json(CTX.food_add_item(auth, _mparts[0], body))
                if len(_mparts) == 2 and _mparts[1] == "remind-missing":
                    return self._send_json(CTX.food_remind_missing(auth, _mparts[0]))
                if len(_mparts) == 2 and _mparts[1] == "notify-published":
                    return self._send_json(CTX.food_notify_published(auth, _mparts[0]))
                if len(_mparts) == 2 and _mparts[1] == "update-deadline":
                    return self._send_json(CTX.food_update_deadline(auth, _mparts[0], body))
            if path.startswith("/api/food/items/"):
                _irest = path[len("/api/food/items/"):]
                _iparts = _irest.split("/")
                if len(_iparts) == 2 and _iparts[1] == "update":
                    return self._send_json(CTX.food_update_item(auth, _iparts[0], body))
                if len(_iparts) == 2 and _iparts[1] == "hide":
                    return self._send_json(CTX.food_hide_item(auth, _iparts[0]))
                if len(_iparts) == 2 and _iparts[1] == "restore":
                    return self._send_json(CTX.food_restore_item(auth, _iparts[0]))
            return self._send_json({"ok": False, "error": "Unknown API route"}, status=404)
        except Exception as exc:
            log.exception("Mini app POST error")
            self._send_json({"ok": False, "error": str(exc)}, status=500)


def run_server() -> None:
    setup_logging()
    host = CTX.settings.web_app_host
    port = CTX.settings.web_app_port
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    if (
        getattr(CTX.settings, "food_module_enabled", False)
        and getattr(CTX.settings, "food_auto_reminders_enabled", False)
    ):
        FoodAutoReminderWatcher(CTX).start()
        log.info(
            "Food auto-reminder watcher started (check every %s min, window %s min before deadline)",
            getattr(CTX.settings, "food_auto_reminder_check_interval_minutes", 15),
            getattr(CTX.settings, "food_auto_reminder_minutes_before_deadline", 120),
        )
    httpd = ThreadingHTTPServer((host, port), MiniAppHandler)
    log.info("Yellow Club Mini App server started: http://%s:%s", host, port)
    log.info("For Telegram Mini App set WEB_APP_URL to an HTTPS URL pointing to this server.")
    httpd.serve_forever()


if __name__ == "__main__":
    run_server()

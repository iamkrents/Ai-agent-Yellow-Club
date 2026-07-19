from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

from utils import now_iso
from payment_domain import (
    PAYMENT_INTENT_ACTIVE_STATUSES as _DOMAIN_ACTIVE_STATUSES,
    MOYKLASS_INVOICE_INTENT_SOURCES,
    is_moyklass_invoice_intent as _is_mk_invoice_intent,
    is_posted_to_moyklass as _is_posted,
    resolve_effective_payment_channel as _resolve_channel,
    is_source_reference_valid as _is_src_ref_valid,
    build_invoice_deduplication_key as _dedup_key,
)


_ONLINE_INDICATORS = frozenset(("онлайн", "online", "yc0", "remote", "дистанционно", "дистанц"))


def _is_online_indicator(value: str) -> bool:
    lower = str(value or "").strip().lower()
    return bool(lower and any(ind in lower for ind in _ONLINE_INDICATORS))


def _resolve_lesson_location(
    group_name: str,
    filial_name: str = "",
    lesson_room_id: str = "",
) -> tuple[str, bool, str]:
    """Return (location_code, is_online, location_source).

    Priority:
    1. filial_name explicitly marks lesson as online → ("ONLINE", True, "moyklass_filial")
    2. filial_name contains a YC code → (yc, False, "moyklass_filial")
    3. lesson_room_id == "0" → ("ONLINE", True, "moyklass_room_id")
    4. group_name fallback → (yc, False, "group_name_fallback")
    """
    if _is_online_indicator(filial_name):
        return "ONLINE", True, "moyklass_filial"
    yc_from_filial = normalize_food_location(filial_name or "")
    if yc_from_filial:
        return yc_from_filial, False, "moyklass_filial"
    room_str = str(lesson_room_id or "").strip()
    if room_str in ("0", "0.0"):
        return "ONLINE", True, "moyklass_room_id"
    yc_from_group = normalize_food_location(group_name or "")
    if yc_from_group:
        return yc_from_group, False, "group_name_fallback"
    return "", False, "unknown"


def _filial_from_raw_preview(raw_preview_text: str) -> tuple[str, str]:
    """Extract (filial_name, room_id) from stored raw_preview JSON string."""
    if not raw_preview_text:
        return "", ""
    try:
        item = json.loads(raw_preview_text)
    except Exception:
        return "", ""
    if not isinstance(item, dict):
        return "", ""
    filial_name = str(
        item.get("_prettyFilialName") or
        item.get("filialName") or
        item.get("branchName") or ""
    ).strip()
    room_id = str(item.get("roomId") or "").strip()
    # If no filial name, check _prettyRoomName as fallback indicator
    if not filial_name:
        pretty_room = str(item.get("_prettyRoomName") or "").strip()
        if _is_online_indicator(pretty_room):
            filial_name = pretty_room
    return filial_name, room_id


def _extract_raw_json_text(obj: Any, depth: int = 0) -> str:
    if depth > 5:
        return ""
    if isinstance(obj, str):
        return obj + " "
    if isinstance(obj, dict):
        return " ".join(_extract_raw_json_text(v, depth + 1) for v in obj.values())
    if isinstance(obj, list):
        return " ".join(_extract_raw_json_text(v, depth + 1) for v in obj)
    return ""


def _get_food_group_info(child: dict) -> dict:
    import json as _json_inner
    for field in ("group_name", "mk_class_name", "classroom"):
        val = str(child.get(field) or "").upper()
        for code in ("YC1", "YC2", "YC3", "YC4", "YC5"):
            if code in val:
                return {"groupCode": code, "groupSource": field}
    raw = child.get("raw_json")
    if raw:
        try:
            raw_obj = _json_inner.loads(raw) if isinstance(raw, str) else raw
            text_blob = _extract_raw_json_text(raw_obj).upper()
            for code in ("YC1", "YC2", "YC3", "YC4", "YC5"):
                if code in text_blob:
                    return {"groupCode": code, "groupSource": "raw_json"}
        except Exception:
            pass
    return {"groupCode": "unknown", "groupSource": ""}


def _get_food_group_code(child: dict) -> str:
    return _get_food_group_info(child)["groupCode"]


def _get_child_week_period(child: dict) -> tuple:
    """Return (week_start_iso, week_end_iso, location_code) for a camp child.

    Priority:
    A. Parse DD.MM-DD.MM range from group_name or mk_class_name.
       E.g. "Yellow Summer Week 3 (13.07-17.07), YC1" → ('2026-07-13', '2026-07-17', 'YC1').
       Year is taken from camp_lesson_date; falls back to current UTC year.
    B. If no parenthesised range found, compute Mon–Fri of the week that
       contains camp_lesson_date.
    C. If camp_lesson_date also absent, return (None, None, location_code).

    location_code is YC1..YC5 derived from group_name / mk_class_name / raw_json,
    or "" when it cannot be determined.
    """
    import re as _re
    from datetime import date as _date, timedelta as _td, datetime as _dt

    info = _get_food_group_info(child)
    location_code = info["groupCode"] if info["groupCode"] != "unknown" else ""

    lesson_date_str = str(child.get("camp_lesson_date") or "").strip()
    year: int = _dt.now().year
    if len(lesson_date_str) >= 4:
        try:
            year = int(lesson_date_str[:4])
        except ValueError:
            pass

    # A: extract (DD.MM-DD.MM) from group_name or mk_class_name
    for field in ("group_name", "mk_class_name"):
        val = str(child.get(field) or "")
        m = _re.search(r'\((\d{1,2})\.(\d{1,2})-(\d{1,2})\.(\d{1,2})\)', val)
        if m:
            try:
                s_day, s_mon = int(m.group(1)), int(m.group(2))
                e_day, e_mon = int(m.group(3)), int(m.group(4))
                ws = _date(year, s_mon, s_day)
                we = _date(year, e_mon, e_day)
                return ws.isoformat(), we.isoformat(), location_code
            except ValueError:
                pass

    # B: compute Mon–Fri from camp_lesson_date
    if len(lesson_date_str) >= 10:
        try:
            d = _date.fromisoformat(lesson_date_str[:10])
            monday = d - _td(days=d.weekday())
            friday = monday + _td(days=4)
            return monday.isoformat(), friday.isoformat(), location_code
        except ValueError:
            pass

    return None, None, location_code


def normalize_food_location(value: str) -> str:
    """Extract YC location code (YC1..YC5) from a string (group_name, menu title, etc.)."""
    if not value:
        return ""
    v = str(value).upper()
    for code in ("YC1", "YC2", "YC3", "YC4", "YC5"):
        if code in v:
            return code
    return ""


class Storage:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _columns(self, conn: sqlite3.Connection, table: str) -> set[str]:
        try:
            return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        except Exception:
            return set()

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
        if column not in self._columns(conn, table):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

    def _init(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    chat_id INTEGER NOT NULL,
                    thread_id INTEGER,
                    chat_type TEXT,
                    user_id INTEGER,
                    username TEXT,
                    full_name TEXT,
                    text TEXT NOT NULL,
                    is_bot INTEGER DEFAULT 0
                )
            """)
            self._ensure_column(conn, "messages", "thread_id", "thread_id INTEGER")
            self._ensure_column(conn, "messages", "chat_type", "chat_type TEXT")
            self._ensure_column(conn, "messages", "full_name", "full_name TEXT")
            self._ensure_column(conn, "messages", "is_bot", "is_bot INTEGER DEFAULT 0")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, thread_id, id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_text ON messages(text)")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS facts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    created_by INTEGER,
                    text TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS trial_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    chat_id INTEGER NOT NULL,
                    thread_id INTEGER,
                    created_by INTEGER,
                    status TEXT NOT NULL DEFAULT 'open',
                    child_name TEXT,
                    parent_name TEXT,
                    age TEXT,
                    direction TEXT,
                    format TEXT,
                    location TEXT,
                    contact TEXT,
                    preferred_time TEXT,
                    notes TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trials_chat ON trial_requests(chat_id, thread_id, status)")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS decisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    chat_id INTEGER,
                    thread_id INTEGER,
                    created_by INTEGER,
                    text TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_decisions_chat ON decisions(chat_id, thread_id, id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_decisions_text ON decisions(text)")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    chat_id INTEGER,
                    thread_id INTEGER,
                    user_id INTEGER,
                    username TEXT,
                    rating TEXT NOT NULL,
                    text TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_chat ON feedback(chat_id, thread_id, id)")


            conn.execute("""
                CREATE TABLE IF NOT EXISTS staff_users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    role TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            self._ensure_column(conn, "staff_users", "mk_teacher_id", "mk_teacher_id TEXT")
            self._ensure_column(conn, "staff_users", "mk_teacher_name", "mk_teacher_name TEXT")
            self._ensure_column(conn, "staff_users", "mk_linked_at", "mk_linked_at TEXT")
            self._ensure_column(conn, "staff_users", "test_role", "test_role TEXT")
            self._ensure_column(conn, "staff_users", "test_mk_teacher_id", "test_mk_teacher_id TEXT")
            self._ensure_column(conn, "staff_users", "test_enabled", "test_enabled INTEGER DEFAULT 0")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_staff_users_role ON staff_users(role, status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_staff_users_mk_teacher ON staff_users(mk_teacher_id)")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS mk_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    user_id INTEGER,
                    action TEXT NOT NULL,
                    object_type TEXT,
                    object_id TEXT,
                    method TEXT,
                    path TEXT,
                    payload TEXT,
                    status INTEGER,
                    ok INTEGER DEFAULT 0,
                    response TEXT,
                    error TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mk_audit_created ON mk_audit(id, created_at)")


            conn.execute("""
                CREATE TABLE IF NOT EXISTS teacher_lesson_control (
                    lesson_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    lesson_date TEXT,
                    lesson_time TEXT,
                    group_name TEXT,
                    lesson_topic TEXT,
                    teacher_user_id INTEGER,
                    teacher_name TEXT,
                    mk_teacher_id TEXT,
                    notion_url TEXT,
                    preparation_status TEXT NOT NULL DEFAULT 'not_started',
                    preparation_confirmed_at TEXT,
                    preparation_comment TEXT,
                    lesson_status TEXT NOT NULL DEFAULT 'not_started',
                    myclass_status TEXT NOT NULL DEFAULT 'not_checked',
                    parent_report_status TEXT NOT NULL DEFAULT 'not_checked',
                    works_status TEXT NOT NULL DEFAULT 'not_checked',
                    classroom_status TEXT NOT NULL DEFAULT 'not_checked',
                    problem_status TEXT NOT NULL DEFAULT 'none',
                    problem_comment TEXT,
                    closed_at TEXT,
                    closed_by INTEGER
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_teacher_lesson_control_date ON teacher_lesson_control(lesson_date, lesson_time)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_teacher_lesson_control_teacher ON teacher_lesson_control(teacher_user_id, lesson_date)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_teacher_lesson_control_status ON teacher_lesson_control(preparation_status, lesson_status, problem_status)")
            self._ensure_column(conn, "teacher_lesson_control", "prep_material_status", "prep_material_status TEXT NOT NULL DEFAULT 'not_checked'")
            self._ensure_column(conn, "teacher_lesson_control", "prep_video_status", "prep_video_status TEXT NOT NULL DEFAULT 'not_checked'")
            self._ensure_column(conn, "teacher_lesson_control", "prep_practice_status", "prep_practice_status TEXT NOT NULL DEFAULT 'not_checked'")
            self._ensure_column(conn, "teacher_lesson_control", "prep_result_status", "prep_result_status TEXT NOT NULL DEFAULT 'not_checked'")
            self._ensure_column(conn, "teacher_lesson_control", "lesson_comment", "lesson_comment TEXT")
            self._ensure_column(conn, "teacher_lesson_control", "prep_result_file_id", "prep_result_file_id TEXT")
            self._ensure_column(conn, "teacher_lesson_control", "filial_name", "filial_name TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "teacher_lesson_control", "lesson_room_id", "lesson_room_id TEXT NOT NULL DEFAULT ''")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS teacher_prep_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    lesson_id TEXT NOT NULL,
                    teacher_user_id INTEGER,
                    file_name TEXT NOT NULL,
                    stored_path TEXT NOT NULL,
                    mime_type TEXT,
                    size_bytes INTEGER DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'submitted',
                    reviewer_user_id INTEGER,
                    reviewer_comment TEXT,
                    reviewed_at TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_teacher_prep_results_lesson ON teacher_prep_results(lesson_id, id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_teacher_prep_results_teacher ON teacher_prep_results(teacher_user_id, status, id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_teacher_prep_results_status ON teacher_prep_results(status, id)")

            # --- Intern onboarding track (стажёр: путь от нуля до преподавателя) ---
            conn.execute("""
                CREATE TABLE IF NOT EXISTS intern_observations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    intern_user_id INTEGER NOT NULL,
                    mk_lesson_id TEXT,
                    lesson_title TEXT,
                    lesson_date TEXT,
                    lesson_time TEXT,
                    location TEXT,
                    status TEXT NOT NULL DEFAULT 'signed_up',
                    comment TEXT NOT NULL DEFAULT ''
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_intern_observations_user ON intern_observations(intern_user_id, status, id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_intern_observations_lesson ON intern_observations(intern_user_id, mk_lesson_id)")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS intern_works (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    intern_user_id INTEGER NOT NULL,
                    file_name TEXT NOT NULL,
                    stored_path TEXT NOT NULL,
                    mime_type TEXT,
                    size_bytes INTEGER DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'submitted',
                    reviewer_user_id INTEGER,
                    reviewer_comment TEXT,
                    reviewed_at TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_intern_works_user ON intern_works(intern_user_id, status, id)")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS intern_demo_bookings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    intern_user_id INTEGER NOT NULL,
                    supervisor_user_id INTEGER,
                    slot_ref TEXT,
                    demo_date TEXT,
                    demo_time TEXT,
                    location TEXT,
                    status TEXT NOT NULL DEFAULT 'requested',
                    reviewer_user_id INTEGER,
                    reviewer_comment TEXT,
                    reviewed_at TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_intern_demo_bookings_user ON intern_demo_bookings(intern_user_id, status, id)")
            self._ensure_column(conn, "intern_demo_bookings", "trainee_feedback_json", "trainee_feedback_json TEXT")
            self._ensure_column(conn, "intern_demo_bookings", "trainee_feedback_at", "trainee_feedback_at TEXT")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS teacher_profiles (
                    user_id INTEGER PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    teacher_status TEXT NOT NULL DEFAULT 'active',
                    profile_type TEXT NOT NULL DEFAULT 'current',
                    location TEXT,
                    can_trial INTEGER DEFAULT 1,
                    can_regular INTEGER DEFAULT 1,
                    can_substitute INTEGER DEFAULT 0,
                    supervisor_user_id INTEGER,
                    notes TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_teacher_profiles_status ON teacher_profiles(teacher_status, profile_type)")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS lesson_snapshots (
                    lesson_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    lesson_date TEXT,
                    lesson_time TEXT,
                    group_name TEXT,
                    lesson_topic TEXT,
                    teacher_ids TEXT,
                    teacher_names TEXT,
                    fingerprint TEXT NOT NULL,
                    raw_preview TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_lesson_snapshots_date ON lesson_snapshots(lesson_date, lesson_time)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_lesson_snapshots_teacher ON lesson_snapshots(teacher_ids)")
            self._ensure_column(conn, "lesson_snapshots", "filial_name", "filial_name TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "lesson_snapshots", "lesson_room_id", "lesson_room_id TEXT NOT NULL DEFAULT ''")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS teacher_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_key TEXT UNIQUE,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    due_at TEXT,
                    status TEXT NOT NULL DEFAULT 'open',
                    priority TEXT NOT NULL DEFAULT 'normal',
                    task_type TEXT NOT NULL,
                    user_id INTEGER,
                    mk_teacher_id TEXT,
                    lesson_id TEXT,
                    title TEXT NOT NULL,
                    text TEXT,
                    source TEXT,
                    completed_at TEXT,
                    completed_by INTEGER
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_teacher_tasks_user_status ON teacher_tasks(user_id, status, due_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_teacher_tasks_type_status ON teacher_tasks(task_type, status, due_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_teacher_tasks_lesson ON teacher_tasks(lesson_id)")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS notification_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    user_id INTEGER,
                    lesson_id TEXT,
                    task_id INTEGER,
                    text TEXT,
                    sent_ok INTEGER DEFAULT 0,
                    error TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_notification_log_event ON notification_log(event_type, user_id, lesson_id, created_at)")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS teacher_work_schedule (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    mk_teacher_id TEXT,
                    day_of_week INTEGER NOT NULL,
                    start_time TEXT NOT NULL,
                    end_time TEXT NOT NULL,
                    location TEXT,
                    work_type TEXT,
                    note TEXT,
                    week_start TEXT,
                    active INTEGER DEFAULT 1
                )
            """)
            self._ensure_column(conn, "teacher_work_schedule", "work_type", "work_type TEXT")
            self._ensure_column(conn, "teacher_work_schedule", "week_start", "week_start TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_teacher_work_schedule_user ON teacher_work_schedule(user_id, week_start, day_of_week, start_time)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_teacher_work_schedule_mk ON teacher_work_schedule(mk_teacher_id, week_start, day_of_week, start_time)")


            conn.execute("""
                CREATE TABLE IF NOT EXISTS client_manager_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    created_by INTEGER,
                    status TEXT NOT NULL DEFAULT 'new',
                    priority TEXT NOT NULL DEFAULT 'normal',
                    task_type TEXT NOT NULL,
                    client_name TEXT,
                    child_name TEXT,
                    contact TEXT,
                    desired_date TEXT,
                    desired_time TEXT,
                    location TEXT,
                    amount TEXT,
                    payment_for TEXT,
                    deadline TEXT,
                    comment TEXT,
                    result_text TEXT,
                    source_type TEXT,
                    source_key TEXT,
                    lesson_id TEXT,
                    mk_student_id TEXT,
                    completed_at TEXT,
                    completed_by INTEGER
                )
            """)
            self._ensure_column(conn, "client_manager_tasks", "source_type", "source_type TEXT")
            self._ensure_column(conn, "client_manager_tasks", "source_key", "source_key TEXT")
            self._ensure_column(conn, "client_manager_tasks", "lesson_id", "lesson_id TEXT")
            self._ensure_column(conn, "client_manager_tasks", "mk_student_id", "mk_student_id TEXT")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_client_manager_tasks_status ON client_manager_tasks(status, priority, desired_date, deadline)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_client_manager_tasks_type ON client_manager_tasks(task_type, status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_client_manager_tasks_created_by ON client_manager_tasks(created_by, status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_client_manager_tasks_source ON client_manager_tasks(source_key, status)")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS client_task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    task_id INTEGER NOT NULL,
                    actor_user_id INTEGER,
                    actor_name TEXT,
                    event_type TEXT NOT NULL,
                    old_status TEXT,
                    new_status TEXT,
                    comment TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_client_task_events_task ON client_task_events(task_id, id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_client_task_events_actor ON client_task_events(actor_user_id, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_client_task_events_type ON client_task_events(event_type, created_at)")

            self._init_food_tables(conn)
            self._init_bepaid_tables(conn)
            self._init_payment_intent_tables(conn)
            self._init_client_link_tables(conn)
            self._init_automation_tables(conn)

    def _init_payment_intent_tables(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payment_intents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_id TEXT UNIQUE NOT NULL,
                mk_user_id INTEGER NOT NULL,
                student_name TEXT,
                amount_minor INTEGER NOT NULL,
                amount_byn REAL NOT NULL,
                currency TEXT NOT NULL DEFAULT 'BYN',
                purpose TEXT NOT NULL DEFAULT 'other',
                period_month TEXT,
                payment_method TEXT NOT NULL DEFAULT 'erip',
                status TEXT NOT NULL DEFAULT 'draft',
                mk_filial_id INTEGER,
                location_code TEXT,
                class_id INTEGER,
                comment TEXT,
                created_by_tg_id INTEGER,
                created_by_name TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                cancelled_at TEXT,
                cancel_reason TEXT,
                bepaid_shop_type TEXT,
                bepaid_order_id TEXT,
                bepaid_tracking_id TEXT,
                bepaid_uid TEXT,
                bepaid_payment_url TEXT,
                bepaid_status TEXT,
                paid_at TEXT,
                mk_payment_id INTEGER,
                mk_posted_at TEXT,
                mk_post_error TEXT,
                raw_context_json TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pi_public_id ON payment_intents(public_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pi_mk_user ON payment_intents(mk_user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pi_period ON payment_intents(period_month)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pi_status ON payment_intents(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pi_bepaid_uid ON payment_intents(bepaid_uid)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pi_mk_payment ON payment_intents(mk_payment_id)")
        # Migrations: safe no-op if columns already present
        self._ensure_column(conn, "payment_intents", "bepaid_account_number", "bepaid_account_number TEXT")
        self._ensure_column(conn, "payment_intents", "bepaid_created_at", "bepaid_created_at TEXT")
        self._ensure_column(conn, "payment_intents", "bepaid_error", "bepaid_error TEXT")
        self._ensure_column(conn, "payment_intents", "bepaid_request_attempts", "bepaid_request_attempts INTEGER DEFAULT 0")
        # v7.0.82
        self._ensure_column(conn, "payment_intents", "bepaid_qr_code_raw", "bepaid_qr_code_raw TEXT")
        # v7.0.90 — MK invoice source tracking
        self._ensure_column(conn, "payment_intents", "mk_invoice_id", "mk_invoice_id TEXT")
        self._ensure_column(conn, "payment_intents", "mk_user_subscription_id", "mk_user_subscription_id TEXT")
        self._ensure_column(conn, "payment_intents", "source", "source TEXT DEFAULT 'manual'")
        self._ensure_column(conn, "payment_intents", "source_reference", "source_reference TEXT")
        self._ensure_column(conn, "payment_intents", "invoice_amount_minor", "invoice_amount_minor INTEGER")
        self._ensure_column(conn, "payment_intents", "invoice_remaining_minor", "invoice_remaining_minor INTEGER")
        self._ensure_column(conn, "payment_intents", "invoice_snapshot_json", "invoice_snapshot_json TEXT")
        self._ensure_column(conn, "payment_intents", "verified_mk_user_at", "verified_mk_user_at TEXT")
        self._ensure_column(conn, "payment_intents", "verified_invoice_at", "verified_invoice_at TEXT")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pi_mk_invoice ON payment_intents(mk_invoice_id)")
        # v7.0.91 — webhook payment reconciliation
        self._ensure_column(conn, "payment_intents", "paid_amount_minor", "paid_amount_minor INTEGER")
        self._ensure_column(conn, "payment_intents", "paid_currency", "paid_currency TEXT")
        self._ensure_column(conn, "payment_intents", "paid_transaction_uid", "paid_transaction_uid TEXT")
        self._ensure_column(conn, "payment_intents", "paid_tracking_id", "paid_tracking_id TEXT")
        self._ensure_column(conn, "payment_intents", "paid_order_id", "paid_order_id TEXT")
        self._ensure_column(conn, "payment_intents", "paid_account_number", "paid_account_number TEXT")
        self._ensure_column(conn, "payment_intents", "last_webhook_at", "last_webhook_at TEXT")
        self._ensure_column(conn, "payment_intents", "payment_state_reason", "payment_state_reason TEXT")
        self._ensure_column(conn, "payment_intents", "webhook_match_method", "webhook_match_method TEXT")
        self._ensure_column(conn, "payment_intents", "webhook_verified", "webhook_verified INTEGER DEFAULT 0")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pi_paid_tx_uid ON payment_intents(paid_transaction_uid)")
        # v7.0.91 — webhook audit log
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payment_webhook_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                bepaid_tx_id INTEGER,
                payment_intent_id INTEGER,
                intent_public_id TEXT,
                transaction_uid TEXT,
                shop_type TEXT,
                status TEXT,
                amount_minor INTEGER,
                currency TEXT,
                match_method TEXT,
                match_confidence TEXT,
                reason TEXT,
                details_json TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pwha_intent ON payment_webhook_audit(intent_public_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pwha_tx ON payment_webhook_audit(bepaid_tx_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pwha_event ON payment_webhook_audit(event_type, created_at)")
        # v7.0.92 — MoyKlass manual payment posting
        self._ensure_column(conn, "payment_intents", "mk_posting_status", "mk_posting_status TEXT")
        self._ensure_column(conn, "payment_intents", "mk_posting_at", "mk_posting_at TEXT")
        self._ensure_column(conn, "payment_intents", "mk_posting_by", "mk_posting_by TEXT")
        self._ensure_column(conn, "payment_intents", "mk_posting_fingerprint", "mk_posting_fingerprint TEXT")
        self._ensure_column(conn, "payment_intents", "mk_posting_error", "mk_posting_error TEXT")
        self._ensure_column(conn, "payment_intents", "mk_posting_invoice_snapshot_json", "mk_posting_invoice_snapshot_json TEXT")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payment_mk_post_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                intent_public_id TEXT,
                payment_intent_id INTEGER,
                transaction_uid TEXT,
                mk_user_id INTEGER,
                mk_invoice_id TEXT,
                mk_user_subscription_id TEXT,
                amount_minor INTEGER,
                currency TEXT,
                invoice_remaining_minor INTEGER,
                mk_payment_id INTEGER,
                fingerprint TEXT,
                result TEXT,
                reason TEXT,
                details_json TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pmkpa_intent ON payment_mk_post_audit(intent_public_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pmkpa_event ON payment_mk_post_audit(event_type, created_at)")
        # v7.0.92.2 — dual-channel payment options
        self._ensure_column(conn, "payment_intents", "paid_channel", "paid_channel TEXT")
        self._ensure_column(conn, "payment_intents", "paid_option_id", "paid_option_id INTEGER")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS payment_intent_options (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_intent_id INTEGER NOT NULL,
                intent_public_id TEXT NOT NULL,
                channel TEXT NOT NULL,
                shop_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'created',
                bepaid_order_id TEXT,
                bepaid_tracking_id TEXT,
                bepaid_uid TEXT,
                bepaid_account_number TEXT,
                payment_url TEXT,
                checkout_token TEXT,
                qr_code_raw TEXT,
                expires_at TEXT,
                transaction_uid TEXT,
                paid_at TEXT,
                paid_amount_minor INTEGER,
                paid_currency TEXT,
                paid_tracking_id TEXT,
                paid_order_id TEXT,
                error_code TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pio_intent ON payment_intent_options(payment_intent_id, channel)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pio_public_id ON payment_intent_options(intent_public_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pio_tracking ON payment_intent_options(bepaid_tracking_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pio_order ON payment_intent_options(bepaid_order_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pio_uid ON payment_intent_options(bepaid_uid)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pio_status ON payment_intent_options(status)")
        # v7.0.92.3 — acquiring checkout token
        self._ensure_column(conn, "payment_intent_options", "checkout_token", "checkout_token TEXT")
        # v7.0.93 — parent client visibility
        self._ensure_column(conn, "payment_intents", "client_visibility", "client_visibility TEXT NOT NULL DEFAULT 'hidden'")
        self._ensure_column(conn, "payment_intents", "published_at", "published_at TEXT")
        self._ensure_column(conn, "payment_intents", "published_by", "published_by TEXT")
        self._ensure_column(conn, "payment_intents", "withdrawn_at", "withdrawn_at TEXT")
        self._ensure_column(conn, "payment_intents", "withdrawn_by", "withdrawn_by TEXT")

    def _init_bepaid_tables(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bepaid_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL DEFAULT 'bepaid',
                shop_type TEXT NOT NULL DEFAULT 'unknown',
                shop_id TEXT,
                transaction_uid TEXT,
                transaction_id TEXT,
                order_id TEXT,
                tracking_id TEXT,
                status TEXT,
                payment_method_type TEXT,
                amount_minor INTEGER,
                amount_byn REAL,
                currency TEXT,
                paid_at TEXT,
                created_at_provider TEXT,
                test INTEGER NOT NULL DEFAULT 0,
                customer_first_name TEXT,
                customer_last_name TEXT,
                customer_phone TEXT,
                customer_email TEXT,
                billing_phone TEXT,
                mk_user_id TEXT,
                mk_user_id_source TEXT,
                mk_user_name TEXT,
                mk_filial_id TEXT,
                description TEXT,
                raw_json TEXT,
                received_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                match_status TEXT,
                match_score REAL,
                match_reason TEXT,
                mk_payment_id TEXT,
                posting_status TEXT,
                posting_error TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bepaid_tx_type_status ON bepaid_transactions(shop_type, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bepaid_tx_paid_at ON bepaid_transactions(paid_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bepaid_tx_uid ON bepaid_transactions(provider, shop_type, transaction_uid)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bepaid_tx_order ON bepaid_transactions(order_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_bepaid_tx_match ON bepaid_transactions(match_status)")
        # Migration: add columns for existing DBs (no-op if already present)
        for _col_sql in [
            "ALTER TABLE bepaid_transactions ADD COLUMN mk_user_id_source TEXT",
            "ALTER TABLE bepaid_transactions ADD COLUMN description TEXT",
            "ALTER TABLE bepaid_transactions ADD COLUMN match_reason TEXT",
        ]:
            try:
                conn.execute(_col_sql)
            except Exception:
                pass
        # v7.0.91 — link bePaid transactions to payment intents
        self._ensure_column(conn, "bepaid_transactions", "payment_intent_id", "payment_intent_id INTEGER")
        self._ensure_column(conn, "bepaid_transactions", "intent_public_id", "intent_public_id TEXT")
        self._ensure_column(conn, "bepaid_transactions", "webhook_verified", "webhook_verified INTEGER DEFAULT 0")
        self._ensure_column(conn, "bepaid_transactions", "webhook_match_method", "webhook_match_method TEXT")
        self._ensure_column(conn, "bepaid_transactions", "match_confidence", "match_confidence TEXT")
        self._ensure_column(conn, "bepaid_transactions", "processed_at", "processed_at TEXT")
        self._ensure_column(conn, "bepaid_transactions", "erip_account_number", "erip_account_number TEXT")
        # v7.0.92.5.3 — provider-verified trust path (separate from webhook RSA signature)
        self._ensure_column(conn, "bepaid_transactions", "provider_verified", "provider_verified INTEGER DEFAULT 0")
        self._ensure_column(conn, "bepaid_transactions", "provider_verified_at", "provider_verified_at TEXT")
        self._ensure_column(conn, "bepaid_transactions", "provider_verification_method", "provider_verification_method TEXT")

    def _init_food_tables(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS camp_children (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                mk_student_id TEXT NOT NULL,
                full_name TEXT NOT NULL,
                first_name TEXT,
                last_name TEXT,
                group_name TEXT,
                mk_class_id TEXT,
                mk_class_name TEXT,
                camp_lesson_id TEXT,
                camp_lesson_date TEXT,
                classroom TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                raw_json TEXT,
                UNIQUE(mk_student_id, mk_class_id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_camp_children_mk_student ON camp_children(mk_student_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_camp_children_class ON camp_children(mk_class_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_camp_children_active ON camp_children(active)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS parent_child_links (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                confirmed_at TEXT,
                parent_telegram_id TEXT,
                mk_student_id TEXT NOT NULL,
                link_code TEXT NOT NULL UNIQUE,
                active INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_parent_child_links_code ON parent_child_links(link_code)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_parent_child_links_parent ON parent_child_links(parent_telegram_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_parent_child_links_student ON parent_child_links(mk_student_id)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS food_menus (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                created_by INTEGER,
                menu_date TEXT NOT NULL,
                title TEXT,
                deadline_at TEXT,
                status TEXT NOT NULL DEFAULT 'draft',
                notes TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_food_menus_date ON food_menus(menu_date)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_food_menus_status ON food_menus(status)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS food_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                menu_id INTEGER NOT NULL,
                category TEXT,
                name TEXT NOT NULL,
                description TEXT,
                weight TEXT,
                price REAL NOT NULL DEFAULT 0,
                is_available INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_food_items_menu ON food_items(menu_id, category, sort_order)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS food_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                menu_id INTEGER NOT NULL,
                parent_telegram_id TEXT NOT NULL,
                mk_student_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'submitted',
                total_price REAL NOT NULL DEFAULT 0,
                submitted_at TEXT,
                UNIQUE(menu_id, parent_telegram_id, mk_student_id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_food_orders_menu ON food_orders(menu_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_food_orders_student ON food_orders(mk_student_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_food_orders_parent ON food_orders(parent_telegram_id)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS food_order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                order_id INTEGER NOT NULL,
                item_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                price_snapshot REAL NOT NULL DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_food_order_items_order ON food_order_items(order_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_food_order_items_item ON food_order_items(item_id)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS food_staff_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                menu_id INTEGER NOT NULL,
                staff_user_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'submitted',
                submitted_at TEXT,
                UNIQUE(menu_id, staff_user_id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_food_staff_orders_menu ON food_staff_orders(menu_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_food_staff_orders_user ON food_staff_orders(staff_user_id)")
        self._ensure_column(conn, "food_staff_orders", "location_code", "location_code TEXT")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS food_staff_order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                order_id INTEGER NOT NULL,
                item_id INTEGER NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 1,
                price_snapshot REAL NOT NULL DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_food_staff_oi_order ON food_staff_order_items(order_id)")

        # Safe migrations
        try:
            conn.execute("ALTER TABLE food_order_items ADD COLUMN quantity INTEGER NOT NULL DEFAULT 1")
        except Exception:
            pass
        self._ensure_column(conn, "food_menus", "location_code", "location_code TEXT")
        self._ensure_column(conn, "food_menus", "deleted_at", "deleted_at TEXT")
        self._ensure_column(conn, "food_menus", "deleted_by", "deleted_by INTEGER")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS food_reminder_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                menu_id INTEGER NOT NULL,
                parent_telegram_id TEXT NOT NULL,
                child_names_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'sent',
                error TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_food_reminder_log_menu ON food_reminder_log(menu_id, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_food_reminder_log_parent ON food_reminder_log(parent_telegram_id, menu_id)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS food_menu_notification_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                menu_id INTEGER NOT NULL,
                parent_telegram_id TEXT NOT NULL,
                child_names_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'sent',
                error TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_food_notif_log_menu ON food_menu_notification_log(menu_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_food_notif_log_parent ON food_menu_notification_log(parent_telegram_id, menu_id)")

        # Safe migration: add triggered_by to food_reminder_log if missing (created in v7.0.2 without it)
        try:
            conn.execute("ALTER TABLE food_reminder_log ADD COLUMN triggered_by TEXT NOT NULL DEFAULT 'manual'")
        except Exception:
            pass

        # v7.0.30: admin manual order tracking columns
        self._ensure_column(conn, "food_orders", "admin_source", "admin_source TEXT")
        self._ensure_column(conn, "food_orders", "admin_created_by", "admin_created_by INTEGER")
        self._ensure_column(conn, "food_orders", "admin_created_at", "admin_created_at TEXT")
        self._ensure_column(conn, "food_orders", "admin_updated_by", "admin_updated_by INTEGER")
        self._ensure_column(conn, "food_orders", "admin_updated_at", "admin_updated_at TEXT")
        self._ensure_column(conn, "food_orders", "admin_comment", "admin_comment TEXT")
        self._ensure_column(conn, "food_staff_orders", "admin_source", "admin_source TEXT")
        self._ensure_column(conn, "food_staff_orders", "admin_created_by", "admin_created_by INTEGER")
        self._ensure_column(conn, "food_staff_orders", "admin_created_at", "admin_created_at TEXT")
        self._ensure_column(conn, "food_staff_orders", "admin_updated_by", "admin_updated_by INTEGER")
        self._ensure_column(conn, "food_staff_orders", "admin_updated_at", "admin_updated_at TEXT")
        self._ensure_column(conn, "food_staff_orders", "admin_comment", "admin_comment TEXT")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS food_order_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                menu_id INTEGER NOT NULL,
                order_id INTEGER,
                order_type TEXT NOT NULL,
                action TEXT NOT NULL,
                actor_user_id INTEGER NOT NULL,
                actor_role TEXT,
                old_data TEXT,
                new_data TEXT,
                comment TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_food_audit_log_menu ON food_order_audit_log(menu_id, created_at)")

    # ── v7.0.93.1 — separate client parent-child link system ─────────────────

    def _init_client_link_tables(self, conn: sqlite3.Connection) -> None:
        """Create tables for the client parent-child link system.

        Completely separate from Food Module (parent_child_links / camp_children).
        Used for: payments, future client cabinet, future visit history.
        """
        import hashlib as _hl  # noqa: F401 (used in methods below)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS client_child_link_codes (
                id                              INTEGER PRIMARY KEY AUTOINCREMENT,
                code_hash                       TEXT NOT NULL UNIQUE,
                mk_user_id                      TEXT NOT NULL,
                child_display_name              TEXT NOT NULL DEFAULT '',
                status                          TEXT NOT NULL DEFAULT 'active',
                created_at                      TEXT NOT NULL,
                expires_at                      TEXT,
                used_at                         TEXT,
                used_by_parent_telegram_user_id TEXT,
                invalidated_at                  TEXT,
                invalidated_by                  TEXT,
                created_by                      TEXT NOT NULL DEFAULT '',
                updated_at                      TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cclc_mk_user ON client_child_link_codes(mk_user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cclc_status ON client_child_link_codes(status, created_at)")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS client_parent_child_links (
                id                          INTEGER PRIMARY KEY AUTOINCREMENT,
                parent_telegram_user_id     TEXT NOT NULL,
                mk_user_id                  TEXT NOT NULL,
                child_display_name          TEXT NOT NULL DEFAULT '',
                status                      TEXT NOT NULL DEFAULT 'active',
                linked_at                   TEXT NOT NULL,
                unlinked_at                 TEXT,
                unlinked_by                 TEXT,
                linked_by_code_id           INTEGER,
                created_at                  TEXT NOT NULL,
                updated_at                  TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cpcl_parent ON client_parent_child_links(parent_telegram_user_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cpcl_mk_user ON client_parent_child_links(mk_user_id, status)")

    # ── v7.0.94.0 — Invoice Automation tables ────────────────────────────────

    def _init_automation_tables(self, conn: sqlite3.Connection) -> None:
        """Create tables for the MoyKlass invoice automation pipeline (v7.0.94.0)."""
        conn.execute("""
            CREATE TABLE IF NOT EXISTS invoice_automation_settings (
                id INTEGER PRIMARY KEY DEFAULT 1,
                discovery_enabled INTEGER NOT NULL DEFAULT 1,
                create_payment_options_enabled INTEGER NOT NULL DEFAULT 0,
                publish_to_parent_enabled INTEGER NOT NULL DEFAULT 0,
                scan_interval_minutes INTEGER NOT NULL DEFAULT 10,
                last_scan_at TEXT,
                updated_at TEXT NOT NULL DEFAULT '',
                updated_by TEXT NOT NULL DEFAULT ''
            )
        """)
        conn.execute(
            "INSERT OR IGNORE INTO invoice_automation_settings (id, updated_at) VALUES (1, '')"
        )

        conn.execute("""
            CREATE TABLE IF NOT EXISTS invoice_automation_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mk_invoice_id TEXT NOT NULL,
                mk_user_id TEXT NOT NULL,
                student_name TEXT,
                current_stage TEXT NOT NULL DEFAULT 'discovered',
                reason_code TEXT,
                readable_reason TEXT,
                intent_public_id TEXT,
                linked_parent_tg_id TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_attempt_at TEXT,
                next_retry_at TEXT,
                invoice_snapshot_json TEXT,
                action_result_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(mk_invoice_id)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_iai_stage ON invoice_automation_items(current_stage, id)"
        )

        conn.execute("""
            CREATE TABLE IF NOT EXISTS invoice_automation_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL UNIQUE,
                trigger TEXT NOT NULL DEFAULT 'scheduled',
                started_by TEXT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL DEFAULT 'running',
                scanned_count INTEGER NOT NULL DEFAULT 0,
                discovered_count INTEGER NOT NULL DEFAULT 0,
                created_count INTEGER NOT NULL DEFAULT 0,
                published_count INTEGER NOT NULL DEFAULT 0,
                missing_parent_count INTEGER NOT NULL DEFAULT 0,
                requires_check_count INTEGER NOT NULL DEFAULT 0,
                skipped_count INTEGER NOT NULL DEFAULT 0,
                error_count INTEGER NOT NULL DEFAULT 0,
                error_summary TEXT,
                lease_token TEXT
            )
        """)
        # v7.0.94.5 — additive columns for terminal outcome accounting
        for _col, _type in [
            ("existing_count",    "INTEGER NOT NULL DEFAULT 0"),
            ("filtered_count",    "INTEGER NOT NULL DEFAULT 0"),
            ("processed_count",   "INTEGER NOT NULL DEFAULT 0"),
            ("unaccounted_count", "INTEGER NOT NULL DEFAULT 0"),
        ]:
            try:
                conn.execute(
                    f"ALTER TABLE invoice_automation_runs ADD COLUMN {_col} {_type}"
                )
            except Exception:
                pass

        # v7.0.94.2 — automation metadata audit log
        conn.execute("""
            CREATE TABLE IF NOT EXISTS automation_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                event_type TEXT NOT NULL,
                automation_item_id INTEGER,
                intent_public_id TEXT,
                mk_invoice_id TEXT,
                mk_user_id TEXT,
                old_source TEXT,
                new_source TEXT,
                name_updated INTEGER NOT NULL DEFAULT 0,
                initiator TEXT,
                details_json TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_aal_intent ON automation_audit_log(intent_public_id)"
        )

        # v7.0.95.1 — explicit per-item auto-publish eligibility flag
        for _col, _type in [
            ("auto_publish_eligible", "INTEGER NOT NULL DEFAULT 0"),
            ("auto_publish_eligible_at", "TEXT"),
        ]:
            try:
                conn.execute(
                    f"ALTER TABLE invoice_automation_items ADD COLUMN {_col} {_type}"
                )
            except Exception:
                pass

        # v7.0.96.0 — per-item auto-post eligibility flag + DB-level post toggle
        for _col, _type in [
            ("auto_post_eligible", "INTEGER NOT NULL DEFAULT 0"),
            ("auto_post_eligible_at", "TEXT"),
        ]:
            try:
                conn.execute(
                    f"ALTER TABLE invoice_automation_items ADD COLUMN {_col} {_type}"
                )
            except Exception:
                pass
        try:
            conn.execute(
                "ALTER TABLE invoice_automation_settings ADD COLUMN "
                "post_to_moyklass_enabled INTEGER NOT NULL DEFAULT 0"
            )
        except Exception:
            pass

    # ── v7.0.94.0 — Invoice Automation methods ───────────────────────────────

    def get_automation_settings(self) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM invoice_automation_settings WHERE id=1"
            ).fetchone()
        return dict(row) if row else {
            "id": 1, "discovery_enabled": 1, "create_payment_options_enabled": 0,
            "publish_to_parent_enabled": 0, "post_to_moyklass_enabled": 0,
            "scan_interval_minutes": 10,
            "last_scan_at": None, "updated_at": "", "updated_by": "",
        }

    def update_automation_settings(
        self,
        *,
        discovery_enabled: bool,
        create_payment_options_enabled: bool,
        publish_to_parent_enabled: bool,
        post_to_moyklass_enabled: bool = False,
        scan_interval_minutes: int,
        updated_by: str,
        now: str,
    ) -> dict:
        interval = max(5, min(int(scan_interval_minutes), 1440))
        with self._connect() as conn:
            conn.execute(
                """UPDATE invoice_automation_settings SET
                   discovery_enabled=?, create_payment_options_enabled=?,
                   publish_to_parent_enabled=?, post_to_moyklass_enabled=?,
                   scan_interval_minutes=?, updated_at=?, updated_by=? WHERE id=1""",
                (
                    int(discovery_enabled), int(create_payment_options_enabled),
                    int(publish_to_parent_enabled), int(post_to_moyklass_enabled),
                    interval, now, str(updated_by)[:200],
                ),
            )
        return self.get_automation_settings()

    def update_automation_last_scan(self, now: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE invoice_automation_settings SET last_scan_at=? WHERE id=1",
                (now,),
            )

    def get_automation_item_by_invoice(self, mk_invoice_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM invoice_automation_items WHERE mk_invoice_id=?",
                (str(mk_invoice_id),),
            ).fetchone()
        return dict(row) if row else None

    def get_automation_item_by_id(self, item_id: int) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM invoice_automation_items WHERE id=?",
                (int(item_id),),
            ).fetchone()
        return dict(row) if row else None

    def upsert_automation_item(
        self,
        mk_invoice_id: str,
        mk_user_id: str,
        student_name: Optional[str],
        invoice_snapshot_json: str,
        now: str,
        *,
        auto_publish_eligible: int = 0,
        auto_post_eligible: int = 0,
    ) -> dict:
        """INSERT OR IGNORE then return the row. Does not overwrite existing stage or eligibility."""
        with self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO invoice_automation_items
                   (mk_invoice_id, mk_user_id, student_name, invoice_snapshot_json,
                    auto_publish_eligible, auto_publish_eligible_at,
                    auto_post_eligible, auto_post_eligible_at,
                    created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (
                    str(mk_invoice_id), str(mk_user_id), student_name,
                    invoice_snapshot_json,
                    auto_publish_eligible, now if auto_publish_eligible else None,
                    auto_post_eligible, now if auto_post_eligible else None,
                    now, now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM invoice_automation_items WHERE mk_invoice_id=?",
                (str(mk_invoice_id),),
            ).fetchone()
        return dict(row) if row else {}

    def update_automation_item_stage(
        self,
        item_id: int,
        stage: str,
        *,
        reason_code: Optional[str] = None,
        readable_reason: Optional[str] = None,
        clear_reason: bool = False,
        intent_public_id: Optional[str] = None,
        linked_parent_tg_id: Optional[str] = None,
        action_result_json: Optional[str] = None,
        now: str = "",
    ) -> None:
        sets = ["current_stage=?", "updated_at=?", "last_attempt_at=?", "attempts=attempts+1"]
        vals: list = [stage, now, now]
        if clear_reason:
            sets.append("reason_code=NULL")
            sets.append("readable_reason=NULL")
        else:
            if reason_code is not None:
                sets.append("reason_code=?"); vals.append(str(reason_code)[:100])
            if readable_reason is not None:
                sets.append("readable_reason=?"); vals.append(str(readable_reason)[:500])
        if intent_public_id is not None:
            sets.append("intent_public_id=?"); vals.append(str(intent_public_id))
        if linked_parent_tg_id is not None:
            sets.append("linked_parent_tg_id=?"); vals.append(str(linked_parent_tg_id))
        if action_result_json is not None:
            sets.append("action_result_json=?"); vals.append(str(action_result_json)[:2000])
        vals.append(int(item_id))
        with self._connect() as conn:
            conn.execute(
                f"UPDATE invoice_automation_items SET {', '.join(sets)} WHERE id=?",
                vals,
            )

    def update_automation_item_student_name(
        self, mk_invoice_id: str, student_name: str, now: str
    ) -> None:
        """Set student_name only when the current stored value is NULL (idempotent)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE invoice_automation_items SET student_name=?, updated_at=?"
                " WHERE mk_invoice_id=? AND student_name IS NULL",
                (student_name, now, str(mk_invoice_id)),
            )

    def relink_automation_item_intent(
        self, item_id: int, intent_public_id: str, now: str
    ) -> None:
        """Update intent_public_id on an automation item (used in deduplication recovery)."""
        with self._connect() as conn:
            conn.execute(
                "UPDATE invoice_automation_items SET intent_public_id=?, updated_at=? WHERE id=?",
                (intent_public_id, now, int(item_id)),
            )

    def repair_intent_metadata(
        self,
        public_id: str,
        *,
        student_name: Optional[str] = None,
        source: Optional[str] = None,
        source_reference: Optional[str] = None,
        now: str,
    ) -> dict:
        """Idempotent repair of safe metadata fields on a payment intent.

        Allowed to change: student_name (if NULL, empty, or userId= placeholder), source (if 'manual'),
        source_reference (only when source is also being corrected).
        Never changes: amount, mk_invoice_id, mk_user_id, bepaid fields, paid/posted fields.
        Returns a dict with ok, changed, old_*/new_* fields for audit logging.
        """
        # Normalise: empty string → None so callers don't need to pre-strip
        student_name = (student_name or "").strip() or None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT public_id, student_name, source, source_reference, status, paid_at, mk_posted_at"
                " FROM payment_intents WHERE public_id=?",
                (public_id,),
            ).fetchone()
        if not row:
            return {"ok": False, "error": "not_found"}

        old_name = row["student_name"]
        old_source = row["source"]

        sets: list[str] = ["updated_at=?"]
        vals: list = [now]
        name_updated = False
        source_updated = False

        if student_name and (not old_name or not old_name.strip() or old_name.startswith("userId=")):
            sets.append("student_name=?")
            vals.append(student_name)
            name_updated = True

        if source and old_source == "manual":
            sets.append("source=?")
            vals.append(source)
            source_updated = True
            if source_reference:
                sets.append("source_reference=?")
                vals.append(source_reference)

        if len(sets) == 1:
            return {"ok": True, "changed": False, "old_name": old_name, "old_source": old_source}

        vals.append(public_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE payment_intents SET {', '.join(sets)} WHERE public_id=?",
                vals,
            )

        return {
            "ok": True,
            "changed": True,
            "old_name": old_name,
            "new_name": student_name if name_updated else old_name,
            "old_source": old_source,
            "new_source": source if source_updated else old_source,
            "name_updated": name_updated,
            "source_updated": source_updated,
        }

    def create_automation_audit_event(self, event: dict) -> None:
        """Append an automation audit record to automation_audit_log."""
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO automation_audit_log
                   (created_at, event_type, automation_item_id, intent_public_id, mk_invoice_id,
                    mk_user_id, old_source, new_source, name_updated, initiator, details_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event.get("created_at") or "",
                    event.get("event_type") or "",
                    event.get("automation_item_id"),
                    event.get("intent_public_id"),
                    str(event.get("mk_invoice_id") or ""),
                    str(event.get("mk_user_id") or ""),
                    event.get("old_source"),
                    event.get("new_source"),
                    1 if event.get("name_updated") else 0,
                    event.get("initiator"),
                    event.get("details_json"),
                ),
            )

    def list_automation_items(
        self,
        stage_filter: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        with self._connect() as conn:
            if stage_filter and stage_filter != "all":
                rows = conn.execute(
                    "SELECT * FROM invoice_automation_items WHERE current_stage=? ORDER BY id DESC LIMIT ? OFFSET ?",
                    (stage_filter, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM invoice_automation_items ORDER BY id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
        return [dict(r) for r in rows]

    def start_automation_run(
        self, run_id: str, trigger: str, started_by: Optional[str], now: str
    ) -> dict:
        with self._connect() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO invoice_automation_runs
                   (run_id, trigger, started_by, started_at, status, lease_token)
                   VALUES (?,?,?,?,'running',?)""",
                (run_id, trigger, started_by, now, run_id),
            )
            row = conn.execute(
                "SELECT * FROM invoice_automation_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        return dict(row) if row else {}

    def finish_automation_run(
        self,
        run_id: str,
        *,
        status: str,
        finished_at: str,
        scanned_count: int = 0,
        discovered_count: int = 0,
        created_count: int = 0,
        published_count: int = 0,
        missing_parent_count: int = 0,
        requires_check_count: int = 0,
        skipped_count: int = 0,
        error_count: int = 0,
        error_summary: Optional[str] = None,
        existing_count: int = 0,
        filtered_count: int = 0,
        processed_count: int = 0,
        unaccounted_count: int = 0,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE invoice_automation_runs SET
                   status=?, finished_at=?, lease_token=NULL,
                   scanned_count=?, discovered_count=?, created_count=?,
                   published_count=?, missing_parent_count=?, requires_check_count=?,
                   skipped_count=?, error_count=?, error_summary=?,
                   existing_count=?, filtered_count=?, processed_count=?, unaccounted_count=?
                   WHERE run_id=?""",
                (
                    status, finished_at, scanned_count, discovered_count, created_count,
                    published_count, missing_parent_count, requires_check_count,
                    skipped_count, error_count,
                    str(error_summary)[:1000] if error_summary else None,
                    existing_count, filtered_count, processed_count, unaccounted_count,
                    run_id,
                ),
            )

    def get_running_automation_run(self) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM invoice_automation_runs WHERE status='running' ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def expire_stale_automation_run(self, timeout_minutes: int = 30, now: str = "") -> None:
        """Mark a stale running run as error so the next scan can start."""
        from datetime import datetime, timezone, timedelta
        if not now:
            now = datetime.now(timezone.utc).isoformat()
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)).isoformat()
        with self._connect() as conn:
            conn.execute(
                """UPDATE invoice_automation_runs SET status='error', finished_at=?,
                   error_summary='lease_timeout' WHERE status='running' AND started_at < ?""",
                (now, cutoff),
            )

    def list_automation_runs(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM invoice_automation_runs ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # --- Food module: camp children ---

    def upsert_camp_child(self, child: dict[str, Any]) -> dict[str, Any]:
        mk_student_id = str(child.get("mk_student_id") or "").strip()
        full_name = str(child.get("full_name") or "").strip()
        if not mk_student_id or not full_name:
            raise ValueError("mk_student_id and full_name are required")
        now = now_iso()
        import json as _json
        raw = child.get("raw_json")
        raw_str = _json.dumps(raw, ensure_ascii=False) if isinstance(raw, dict) else (str(raw) if raw else None)
        # Shared field values (no created_at/updated_at/mk_student_id — those vary by branch).
        shared = (
            full_name,
            str(child.get("first_name") or "").strip() or None,
            str(child.get("last_name") or "").strip() or None,
            str(child.get("group_name") or "").strip() or None,
            str(child.get("mk_class_id") or "").strip() or None,
            str(child.get("mk_class_name") or "").strip() or None,
            str(child.get("camp_lesson_id") or "").strip() or None,
            str(child.get("camp_lesson_date") or "").strip() or None,
            str(child.get("classroom") or "").strip() or None,
            raw_str,
        )
        with self._connect() as conn:
            # Deduplicate by mk_student_id only: one active row per child regardless of class group.
            existing = conn.execute(
                "SELECT id FROM camp_children WHERE mk_student_id=? ORDER BY id LIMIT 1",
                (mk_student_id,),
            ).fetchone()
            if existing:
                # UPDATE: updated_at, full_name…raw_json (10 fields), WHERE id
                conn.execute("""
                    UPDATE camp_children SET
                        updated_at=?, full_name=?, first_name=?, last_name=?,
                        group_name=?, mk_class_id=?, mk_class_name=?, camp_lesson_id=?,
                        camp_lesson_date=?, classroom=?, active=1, raw_json=?
                    WHERE id=?
                """, (now, *shared, existing["id"]))
            else:
                # INSERT: created_at, updated_at, mk_student_id, full_name…raw_json (10 fields), active=1
                conn.execute("""
                    INSERT INTO camp_children(
                        created_at, updated_at, mk_student_id, full_name, first_name, last_name,
                        group_name, mk_class_id, mk_class_name, camp_lesson_id, camp_lesson_date,
                        classroom, active, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                    ON CONFLICT(mk_student_id, mk_class_id) DO UPDATE SET
                        updated_at=excluded.updated_at,
                        full_name=excluded.full_name,
                        first_name=excluded.first_name,
                        last_name=excluded.last_name,
                        group_name=excluded.group_name,
                        mk_class_name=excluded.mk_class_name,
                        camp_lesson_id=excluded.camp_lesson_id,
                        camp_lesson_date=excluded.camp_lesson_date,
                        classroom=excluded.classroom,
                        active=1,
                        raw_json=excluded.raw_json
                """, (now, now, mk_student_id, *shared))
            row = conn.execute(
                "SELECT * FROM camp_children WHERE mk_student_id=? ORDER BY id LIMIT 1",
                (mk_student_id,),
            ).fetchone()
        return dict(row) if row else {}

    def list_camp_children(self, active_only: bool = True) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if active_only:
                rows = conn.execute(
                    "SELECT * FROM camp_children WHERE active=1 ORDER BY group_name, full_name",
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM camp_children ORDER BY active DESC, group_name, full_name",
                ).fetchall()
        return [dict(r) for r in rows]

    def get_camp_child_by_mk_student_id(self, mk_student_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM camp_children WHERE mk_student_id=? AND active=1 LIMIT 1",
                (str(mk_student_id).strip(),),
            ).fetchone()
        return dict(row) if row else None

    def deactivate_all_camp_children(self) -> int:
        now = now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE camp_children SET active=0, updated_at=? WHERE active=1",
                (now,),
            )
            return cur.rowcount

    def cleanup_duplicate_camp_children(self) -> dict[str, Any]:
        """Deactivate duplicate rows per mk_student_id in camp_children and parent_child_links."""
        now = now_iso()
        children_found = 0
        children_deactivated = 0
        links_found = 0
        links_deactivated = 0
        with self._connect() as conn:
            dupes = conn.execute("""
                SELECT mk_student_id, COUNT(*) AS cnt, MIN(id) AS keep_id
                FROM camp_children WHERE active=1
                GROUP BY mk_student_id HAVING cnt > 1
            """).fetchall()
            for row in dupes:
                children_found += row["cnt"]
                cur = conn.execute(
                    "UPDATE camp_children SET active=0, updated_at=? WHERE mk_student_id=? AND active=1 AND id!=?",
                    (now, row["mk_student_id"], row["keep_id"]),
                )
                children_deactivated += cur.rowcount
            link_dupes = conn.execute("""
                SELECT mk_student_id, COUNT(*) AS cnt, MIN(id) AS keep_id
                FROM parent_child_links WHERE active=1
                GROUP BY mk_student_id HAVING cnt > 1
            """).fetchall()
            for row in link_dupes:
                links_found += row["cnt"]
                cur = conn.execute(
                    "UPDATE parent_child_links SET active=0 WHERE mk_student_id=? AND active=1 AND id!=?",
                    (row["mk_student_id"], row["keep_id"]),
                )
                links_deactivated += cur.rowcount
        return {
            "duplicateChildrenFound": children_found,
            "childrenDeactivated": children_deactivated,
            "duplicateLinksFound": links_found,
            "linksDeactivated": links_deactivated,
        }

    def generate_child_link_code(self, mk_student_id: str) -> str:
        import secrets
        import string
        mk_student_id = str(mk_student_id or "").strip()
        # Format: YC-XXXX where X is uppercase alphanumeric (excl. ambiguous chars O/0/I/1)
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        for _ in range(20):
            suffix = "".join(secrets.choice(alphabet) for _ in range(4))
            code = f"YC-{suffix}"
            with self._connect() as conn:
                existing = conn.execute(
                    "SELECT id FROM parent_child_links WHERE link_code=?", (code,)
                ).fetchone()
            if not existing:
                now = now_iso()
                with self._connect() as conn:
                    conn.execute("""
                        INSERT INTO parent_child_links(created_at, mk_student_id, link_code, active)
                        VALUES (?, ?, ?, 1)
                        ON CONFLICT(link_code) DO NOTHING
                    """, (now, mk_student_id, code))
                    inserted = conn.execute(
                        "SELECT id FROM parent_child_links WHERE link_code=?", (code,)
                    ).fetchone()
                if inserted:
                    return code
        raise RuntimeError("Failed to generate unique link code after 20 attempts")

    def get_child_by_link_code(self, code: str) -> Optional[dict[str, Any]]:
        code = str(code or "").strip().upper()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM parent_child_links WHERE link_code=? AND active=1",
                (code,),
            ).fetchone()
        return dict(row) if row else None

    def link_parent_to_child(self, parent_telegram_id: str, code: str) -> dict[str, Any]:
        code = str(code or "").strip().upper()
        parent_telegram_id = str(parent_telegram_id or "").strip()
        if not code or not parent_telegram_id:
            return {"ok": False, "error": "Код и ID родителя обязательны"}
        now = now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM parent_child_links WHERE link_code=? AND active=1",
                (code,),
            ).fetchone()
            if not row:
                return {"ok": False, "error": "Код не найден или уже использован"}
            link = dict(row)
            if link.get("confirmed_at") and link.get("parent_telegram_id"):
                if link["parent_telegram_id"] == parent_telegram_id:
                    return {"ok": True, "already_linked": True, "mk_student_id": link["mk_student_id"]}
                return {"ok": False, "error": "Код уже привязан к другому родителю"}
            conn.execute("""
                UPDATE parent_child_links
                SET parent_telegram_id=?, confirmed_at=?
                WHERE link_code=?
            """, (parent_telegram_id, now, code))
        return {"ok": True, "mk_student_id": link["mk_student_id"]}

    def list_camp_children_with_links(self, active_only: bool = True) -> list[dict[str, Any]]:
        with self._connect() as conn:
            base = """
                SELECT c.*,
                       l.link_code, l.parent_telegram_id, l.confirmed_at AS link_confirmed_at,
                       l.active AS link_active
                FROM camp_children c
                LEFT JOIN parent_child_links l
                    ON l.mk_student_id = c.mk_student_id AND l.active = 1
            """
            if active_only:
                rows = conn.execute(
                    base + "WHERE c.active = 1 ORDER BY c.group_name, c.full_name"
                ).fetchall()
            else:
                rows = conn.execute(
                    base + "ORDER BY c.active DESC, c.group_name, c.full_name"
                ).fetchall()
        return [dict(r) for r in rows]

    def list_children_for_parent(self, parent_telegram_id: str) -> list[dict[str, Any]]:
        parent_telegram_id = str(parent_telegram_id or "").strip()
        if not parent_telegram_id:
            return []
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT c.mk_student_id, c.full_name, c.first_name, c.last_name,
                       c.group_name, c.mk_class_id, c.mk_class_name, c.classroom,
                       c.camp_lesson_date, l.confirmed_at, l.link_code
                FROM parent_child_links l
                JOIN camp_children c ON c.mk_student_id = l.mk_student_id AND c.active = 1
                WHERE l.parent_telegram_id = ? AND l.active = 1
                ORDER BY c.full_name
            """, (parent_telegram_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_active_link_for_student(self, mk_student_id: str) -> Optional[dict[str, Any]]:
        mk_student_id = str(mk_student_id or "").strip()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM parent_child_links WHERE mk_student_id=? AND active=1 ORDER BY id DESC LIMIT 1",
                (mk_student_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_or_create_link_code_for_student(self, mk_student_id: str) -> str:
        existing = self.get_active_link_for_student(mk_student_id)
        if existing and existing.get("link_code"):
            return existing["link_code"]
        return self.generate_child_link_code(mk_student_id)

    def relink_child(self, mk_student_id: str) -> dict[str, Any]:
        """Deactivate current active link (unlinks parent + invalidates old code), generate a new code."""
        mk_student_id = str(mk_student_id or "").strip()
        if not mk_student_id:
            return {"ok": False, "error": "mk_student_id обязателен"}
        old_parent_telegram_id = None
        with self._connect() as conn:
            old_link = conn.execute(
                "SELECT * FROM parent_child_links WHERE mk_student_id=? AND active=1 ORDER BY id DESC LIMIT 1",
                (mk_student_id,),
            ).fetchone()
            if old_link:
                old_parent_telegram_id = dict(old_link).get("parent_telegram_id")
            conn.execute(
                "UPDATE parent_child_links SET active=0 WHERE mk_student_id=? AND active=1",
                (mk_student_id,),
            )
        new_code = self.generate_child_link_code(mk_student_id)
        return {
            "ok": True,
            "mk_student_id": mk_student_id,
            "new_code": new_code,
            "old_parent_telegram_id": old_parent_telegram_id,
        }

    # --- Food module: menus ---

    def create_food_menu(self, menu_date: str, title: Optional[str], deadline_at: Optional[str], created_by: Optional[int] = None, location_code: Optional[str] = None) -> dict[str, Any]:
        now = now_iso()
        loc = str(location_code or "").strip().upper() or None
        if not loc and title:
            loc = normalize_food_location(title) or None
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO food_menus(created_at, updated_at, created_by, menu_date, title, deadline_at, status, location_code) VALUES (?, ?, ?, ?, ?, ?, 'draft', ?)",
                (now, now, created_by, str(menu_date or "").strip(), str(title or "").strip() or None, str(deadline_at or "").strip() or None, loc),
            )
            row = conn.execute("SELECT * FROM food_menus WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row) if row else {}

    def list_food_menus(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT m.*, COUNT(i.id) AS items_count
                FROM food_menus m
                LEFT JOIN food_items i ON i.menu_id = m.id AND i.is_available = 1
                WHERE m.status NOT IN ('archived', 'deleted')
                GROUP BY m.id
                ORDER BY m.menu_date ASC, m.id ASC
            """).fetchall()
        return [dict(r) for r in rows]

    def count_food_menu_orders(self, menu_id: int) -> dict[str, int]:
        mid = int(menu_id)
        with self._connect() as conn:
            child_count = conn.execute(
                "SELECT COUNT(*) FROM food_orders WHERE menu_id=? AND status NOT IN ('skipped','cancelled')", (mid,)
            ).fetchone()[0]
            staff_count = conn.execute(
                "SELECT COUNT(*) FROM food_staff_orders WHERE menu_id=? AND status NOT IN ('skipped','cancelled')", (mid,)
            ).fetchone()[0]
        return {"child": child_count, "staff": staff_count, "total": child_count + staff_count}

    def soft_delete_food_menu(self, menu_id: int, deleted_by: int) -> Optional[dict[str, Any]]:
        now = now_iso()
        with self._connect() as conn:
            conn.execute(
                "UPDATE food_menus SET status='deleted', deleted_at=?, deleted_by=?, updated_at=? WHERE id=?",
                (now, deleted_by, now, int(menu_id)),
            )
        return self.get_food_menu(menu_id)

    def get_food_menu(self, menu_id: int) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM food_menus WHERE id=?", (int(menu_id),)).fetchone()
            if not row:
                return None
            menu = dict(row)
            items = conn.execute(
                "SELECT * FROM food_items WHERE menu_id=? ORDER BY category, sort_order, id",
                (int(menu_id),),
            ).fetchall()
            menu["items"] = [dict(i) for i in items]
        return menu

    def update_food_menu(self, menu_id: int, data: dict[str, Any]) -> Optional[dict[str, Any]]:
        now = now_iso()
        allowed = {"menu_date", "title", "deadline_at", "notes", "location_code"}
        sets = []
        vals: list[Any] = []
        for k, v in data.items():
            if k in allowed:
                sets.append(f"{k}=?")
                vals.append(v)
        if sets:
            vals.extend([now, int(menu_id)])
            with self._connect() as conn:
                conn.execute(f"UPDATE food_menus SET {', '.join(sets)}, updated_at=? WHERE id=?", vals)
        return self.get_food_menu(menu_id)

    def set_food_menu_status(self, menu_id: int, status: str) -> Optional[dict[str, Any]]:
        now = now_iso()
        with self._connect() as conn:
            conn.execute("UPDATE food_menus SET status=?, updated_at=? WHERE id=?", (status, now, int(menu_id)))
        return self.get_food_menu(menu_id)

    def add_food_item(self, menu_id: int, category: Optional[str], name: str, weight: Optional[str], price: float, sort_order: int = 0) -> dict[str, Any]:
        now = now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO food_items(created_at, updated_at, menu_id, category, name, weight, price, is_available, sort_order) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)",
                (now, now, int(menu_id), str(category or "Другое").strip(), str(name or "").strip(), str(weight or "").strip() or None, float(price or 0), int(sort_order or 0)),
            )
            row = conn.execute("SELECT * FROM food_items WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row) if row else {}

    def update_food_item(self, item_id: int, data: dict[str, Any]) -> Optional[dict[str, Any]]:
        now = now_iso()
        allowed = {"category", "name", "weight", "price", "sort_order", "is_available", "description"}
        sets = []
        vals: list[Any] = []
        for k, v in data.items():
            if k in allowed:
                sets.append(f"{k}=?")
                vals.append(v)
        if sets:
            vals.extend([now, int(item_id)])
            with self._connect() as conn:
                conn.execute(f"UPDATE food_items SET {', '.join(sets)}, updated_at=? WHERE id=?", vals)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM food_items WHERE id=?", (int(item_id),)).fetchone()
        return dict(row) if row else None

    def set_food_item_available(self, item_id: int, is_available: bool) -> Optional[dict[str, Any]]:
        now = now_iso()
        with self._connect() as conn:
            conn.execute("UPDATE food_items SET is_available=?, updated_at=? WHERE id=?", (1 if is_available else 0, now, int(item_id)))
            row = conn.execute("SELECT * FROM food_items WHERE id=?", (int(item_id),)).fetchone()
        return dict(row) if row else None

    def get_food_item_menu(self, item_id: int) -> Optional[dict[str, Any]]:
        """Return the food_menus row that owns this item."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT fm.* FROM food_items fi JOIN food_menus fm ON fm.id=fi.menu_id WHERE fi.id=?",
                (int(item_id),),
            ).fetchone()
        return dict(row) if row else None

    def get_food_items_order_counts(self, menu_id: int) -> dict[int, int]:
        """Return {item_id: submitted_order_count} for all items in a menu."""
        mid = int(menu_id)
        counts: dict[int, int] = {}
        with self._connect() as conn:
            for row in conn.execute(
                "SELECT oi.item_id, COUNT(*) FROM food_order_items oi "
                "JOIN food_orders o ON o.id=oi.order_id "
                "JOIN food_items fi ON fi.id=oi.item_id "
                "WHERE fi.menu_id=? AND o.status='submitted' GROUP BY oi.item_id",
                (mid,),
            ).fetchall():
                counts[int(row[0])] = counts.get(int(row[0]), 0) + int(row[1])
            for row in conn.execute(
                "SELECT oi.item_id, COUNT(*) FROM food_staff_order_items oi "
                "JOIN food_staff_orders o ON o.id=oi.order_id "
                "JOIN food_items fi ON fi.id=oi.item_id "
                "WHERE fi.menu_id=? AND o.status='submitted' GROUP BY oi.item_id",
                (mid,),
            ).fetchall():
                counts[int(row[0])] = counts.get(int(row[0]), 0) + int(row[1])
        return counts

    def list_published_food_menus_with_items(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            menus = conn.execute(
                "SELECT * FROM food_menus WHERE status='published' ORDER BY menu_date ASC, id ASC"
            ).fetchall()
            result = []
            for m in menus:
                menu = dict(m)
                items = conn.execute(
                    "SELECT * FROM food_items WHERE menu_id=? AND is_available=1 ORDER BY category, sort_order, id",
                    (m["id"],),
                ).fetchall()
                by_cat: dict[str, list[Any]] = {}
                for item in items:
                    cat = str(item["category"] or "Другое")
                    by_cat.setdefault(cat, []).append(dict(item))
                menu["itemsByCategory"] = by_cat
                result.append(menu)
        return result

    # --- Food module: parent orders ---

    def upsert_food_order(
        self,
        parent_telegram_id: str,
        mk_student_id: str,
        menu_id: int,
        item_quantities: dict,  # {item_id: quantity} — empty for skipped
        status: str = "submitted",
    ) -> dict[str, Any]:
        now = now_iso()
        pid = str(parent_telegram_id)
        sid = str(mk_student_id)
        mid = int(menu_id)
        # Normalise: accept dict {id: qty} or legacy list [id, ...] (qty=1)
        if isinstance(item_quantities, list):
            item_quantities = {int(iid): 1 for iid in item_quantities}
        safe_qty: dict[int, int] = {
            int(iid): min(99, max(1, int(qty or 1)))
            for iid, qty in (item_quantities or {}).items()
            if int(qty or 0) > 0
        }
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM food_orders WHERE menu_id=? AND parent_telegram_id=? AND mk_student_id=?",
                (mid, pid, sid),
            ).fetchone()
            if existing:
                order_id = existing["id"]
                conn.execute(
                    "UPDATE food_orders SET status=?, total_price=0, submitted_at=?, updated_at=? WHERE id=?",
                    (status, now, now, order_id),
                )
            else:
                cur = conn.execute(
                    "INSERT INTO food_orders(created_at, updated_at, menu_id, parent_telegram_id, mk_student_id, status, total_price, submitted_at) VALUES(?,?,?,?,?,?,0,?)",
                    (now, now, mid, pid, sid, status, now),
                )
                order_id = cur.lastrowid
            conn.execute("DELETE FROM food_order_items WHERE order_id=?", (order_id,))
            if safe_qty:
                id_ph = ", ".join("?" for _ in safe_qty)
                price_rows = conn.execute(
                    f"SELECT id, price FROM food_items WHERE id IN ({id_ph})", list(safe_qty.keys())
                ).fetchall()
                price_map: dict[int, float] = {r["id"]: float(r["price"] or 0) for r in price_rows}
                for iid, qty in safe_qty.items():
                    conn.execute(
                        "INSERT INTO food_order_items(created_at, order_id, item_id, quantity, price_snapshot) VALUES(?,?,?,?,?)",
                        (now, order_id, iid, qty, price_map.get(iid, 0.0)),
                    )
            row = conn.execute("SELECT * FROM food_orders WHERE id=?", (order_id,)).fetchone()
            order_items = conn.execute(
                "SELECT oi.item_id, oi.quantity, fi.name, fi.category, fi.weight FROM food_order_items oi JOIN food_items fi ON fi.id=oi.item_id WHERE oi.order_id=?",
                (order_id,),
            ).fetchall()
        result = dict(row)
        result["items"] = [dict(i) for i in order_items]
        return result

    def get_food_order(self, menu_id: int, parent_telegram_id: str, mk_student_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM food_orders WHERE menu_id=? AND parent_telegram_id=? AND mk_student_id=?",
                (int(menu_id), str(parent_telegram_id), str(mk_student_id)),
            ).fetchone()
            if not row:
                return None
            order = dict(row)
            items = conn.execute(
                "SELECT oi.item_id, oi.quantity, fi.name, fi.category, fi.weight FROM food_order_items oi JOIN food_items fi ON fi.id=oi.item_id WHERE oi.order_id=?",
                (order["id"],),
            ).fetchall()
            order["items"] = [dict(i) for i in items]
        return order

    def list_food_orders_for_parent(self, parent_telegram_id: str) -> list[dict[str, Any]]:
        pid = str(parent_telegram_id)
        with self._connect() as conn:
            orders = conn.execute(
                """SELECT o.*, m.menu_date, m.title AS menu_title, m.deadline_at,
                          c.full_name AS child_name
                   FROM food_orders o
                   JOIN food_menus m ON m.id = o.menu_id
                   LEFT JOIN camp_children c ON c.mk_student_id = o.mk_student_id AND c.active = 1
                   WHERE o.parent_telegram_id = ?
                   ORDER BY m.menu_date ASC, o.id ASC""",
                (pid,),
            ).fetchall()
            result = []
            for orow in orders:
                order = dict(orow)
                items = conn.execute(
                    "SELECT oi.item_id, oi.quantity, fi.name, fi.category, fi.weight FROM food_order_items oi JOIN food_items fi ON fi.id=oi.item_id WHERE oi.order_id=?",
                    (order["id"],),
                ).fetchall()
                order["items"] = [dict(i) for i in items]
                result.append(order)
        return result

    def get_food_menu_summary(self, menu_id: int) -> Optional[dict[str, Any]]:
        mid = int(menu_id)
        with self._connect() as conn:
            menu_row = conn.execute("SELECT * FROM food_menus WHERE id=?", (mid,)).fetchone()
            if not menu_row:
                return None
            menu = dict(menu_row)
            children_rows = conn.execute(
                """SELECT c.mk_student_id, c.full_name, c.group_name, c.mk_class_name, c.classroom, c.raw_json,
                          l.parent_telegram_id
                   FROM camp_children c
                   LEFT JOIN parent_child_links l ON l.mk_student_id = c.mk_student_id AND l.active = 1
                   WHERE c.active = 1 ORDER BY c.full_name""",
            ).fetchall()
            all_children = [dict(r) for r in children_rows]
            orders_rows = conn.execute(
                "SELECT * FROM food_orders WHERE menu_id=?", (mid,)
            ).fetchall()
            orders_by_student: dict[str, Any] = {}
            for orow in orders_rows:
                o = dict(orow)
                orders_by_student[o["mk_student_id"]] = o
            for o in orders_by_student.values():
                items = conn.execute(
                    "SELECT oi.item_id, oi.quantity, fi.name, fi.category, fi.weight FROM food_order_items oi JOIN food_items fi ON fi.id=oi.item_id WHERE oi.order_id=?",
                    (o["id"],),
                ).fetchall()
                o["items"] = [dict(i) for i in items]
            items_rows = conn.execute(
                "SELECT * FROM food_items WHERE menu_id=? AND is_available=1 ORDER BY category, sort_order, id",
                (mid,),
            ).fetchall()
            all_items = [dict(r) for r in items_rows]
        submitted = 0
        skipped = 0
        missing = 0
        by_children = []
        missing_children = []
        for ch in all_children:
            sid = ch["mk_student_id"]
            order = orders_by_student.get(sid)
            group_info = _get_food_group_info(ch)
            group_code = group_info["groupCode"]
            group_source = group_info["groupSource"]
            if order and order["status"] == "submitted":
                submitted += 1
                item_details = [{"item_id": i["item_id"], "name": i["name"], "category": i["category"], "weight": i["weight"], "quantity": int(i.get("quantity", 1) or 1)} for i in order.get("items", [])]
                by_children.append({"childName": ch["full_name"], "status": "submitted", "items": [i["name"] for i in order.get("items", [])], "itemDetails": item_details, "mk_student_id": sid, "groupCode": group_code, "groupSource": group_source, "orderId": order["id"], "adminSource": order.get("admin_source"), "adminCreatedAt": order.get("admin_created_at"), "adminUpdatedAt": order.get("admin_updated_at"), "adminComment": order.get("admin_comment")})
            elif order and order["status"] == "skipped":
                skipped += 1
                by_children.append({"childName": ch["full_name"], "status": "skipped", "items": [], "itemDetails": [], "mk_student_id": sid, "groupCode": group_code, "groupSource": group_source, "orderId": order["id"], "adminSource": order.get("admin_source"), "adminCreatedAt": order.get("admin_created_at"), "adminUpdatedAt": order.get("admin_updated_at"), "adminComment": order.get("admin_comment")})
            else:
                missing += 1
                by_children.append({"childName": ch["full_name"], "status": "missing", "items": [], "itemDetails": [], "mk_student_id": sid, "groupCode": group_code, "groupSource": group_source, "orderId": None, "adminSource": None, "adminCreatedAt": None, "adminUpdatedAt": None, "adminComment": None})
                missing_children.append(ch["full_name"])
        item_counts: dict[int, int] = {}
        item_children: dict[int, list[str]] = {}
        for ch in all_children:
            order = orders_by_student.get(ch["mk_student_id"])
            if order and order["status"] == "submitted":
                for it in order.get("items", []):
                    iid = it["item_id"]
                    qty = int(it.get("quantity", 1) or 1)
                    item_counts[iid] = item_counts.get(iid, 0) + qty
                    if ch["full_name"] not in item_children.get(iid, []):
                        item_children.setdefault(iid, []).append(ch["full_name"])
        # Staff orders for this menu
        menu_location_code = str(menu.get("location_code") or "").strip().upper()
        staff_orders = self.list_food_staff_orders_for_menu(mid)
        by_staff = []
        for so in staff_orders:
            order_loc = str(so.get("location_code") or "").strip().upper() or menu_location_code
            is_teacher = bool(str(so.get("staff_mk_teacher_id") or "").strip()) or str(so.get("staff_role") or "") in ("teacher", "intern", "methodist")
            # Prefer MoyKlass teacher name (set after teacher link/auto-match) for authoritative display
            mk_teacher_name = str(so.get("staff_mk_teacher_name") or "").strip()
            staff_full_name = str(so.get("staff_name") or "").strip()
            staff_username = str(so.get("staff_username") or "").strip()
            display_name = mk_teacher_name or staff_full_name or staff_username or f"Сотрудник #{so['staff_user_id']}"
            order_row_id = so["id"]
            if so["status"] == "submitted":
                item_details = [{"item_id": i["item_id"], "name": i["name"], "category": i["category"], "weight": i["weight"], "quantity": int(i.get("quantity", 1) or 1)} for i in so.get("items", [])]
                by_staff.append({"staffName": display_name, "staffUserId": so["staff_user_id"], "orderId": order_row_id, "status": "submitted", "itemDetails": item_details, "locationCode": order_loc, "isTeacher": is_teacher, "adminSource": so.get("admin_source"), "adminCreatedAt": so.get("admin_created_at"), "adminUpdatedAt": so.get("admin_updated_at"), "adminComment": so.get("admin_comment")})
            elif so["status"] == "skipped":
                by_staff.append({"staffName": display_name, "staffUserId": so["staff_user_id"], "orderId": order_row_id, "status": "skipped", "itemDetails": [], "locationCode": order_loc, "isTeacher": is_teacher, "adminSource": so.get("admin_source"), "adminCreatedAt": so.get("admin_created_at"), "adminUpdatedAt": so.get("admin_updated_at"), "adminComment": so.get("admin_comment")})
        # Add staff item counts to aggregate
        for so_entry in by_staff:
            if so_entry["status"] == "submitted":
                for it in so_entry["itemDetails"]:
                    iid = it["item_id"]
                    qty = int(it.get("quantity", 1) or 1)
                    item_counts[iid] = item_counts.get(iid, 0) + qty
        by_items = [
            {"item_id": item["id"], "category": item["category"], "name": item["name"], "weight": item["weight"], "count": item_counts.get(item["id"], 0), "children": item_children.get(item["id"], [])}
            for item in all_items
            if item_counts.get(item["id"], 0) > 0
        ]
        return {
            "menu": menu,
            "menuLocationCode": menu_location_code,
            "totalChildren": len(all_children),
            "submittedOrders": submitted,
            "skippedOrders": skipped,
            "missingOrders": missing,
            "byItems": by_items,
            "byChildren": by_children,
            "missingChildren": missing_children,
            "byStaff": by_staff,
        }

    def get_food_item_prices(self, menu_id: int) -> dict[int, float]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, price FROM food_items WHERE menu_id=?",
                (int(menu_id),),
            ).fetchall()
        return {int(row["id"]): float(row["price"] or 0) for row in rows}

    def get_missing_children_with_parents(self, menu_id: int) -> list[dict[str, Any]]:
        """Return children who have not placed/skipped order for menu_id, with parent link info."""
        import json as _json
        mid = int(menu_id)
        with self._connect() as conn:
            children_rows = conn.execute(
                "SELECT c.mk_student_id, c.full_name, c.group_name, c.mk_class_name, c.classroom, c.raw_json "
                "FROM camp_children c WHERE c.active=1 ORDER BY c.full_name"
            ).fetchall()
            all_children = [dict(r) for r in children_rows]
            if not all_children:
                return []
            orders_rows = conn.execute(
                "SELECT mk_student_id, status FROM food_orders WHERE menu_id=?", (mid,)
            ).fetchall()
            ordered_sids: set[str] = {r["mk_student_id"] for r in orders_rows}
            missing_sids = [c["mk_student_id"] for c in all_children if c["mk_student_id"] not in ordered_sids]
            if not missing_sids:
                return []
            ph = ", ".join("?" for _ in missing_sids)
            link_rows = conn.execute(
                f"SELECT mk_student_id, parent_telegram_id FROM parent_child_links "
                f"WHERE mk_student_id IN ({ph}) AND active=1",
                missing_sids,
            ).fetchall()
            parent_by_sid: dict[str, str | None] = {r["mk_student_id"]: r["parent_telegram_id"] for r in link_rows}
        result = []
        for ch in all_children:
            sid = ch["mk_student_id"]
            if sid not in ordered_sids:
                group_code = _get_food_group_code(ch)
                result.append({
                    "mk_student_id": sid,
                    "full_name": ch["full_name"],
                    "groupCode": group_code,
                    "parent_telegram_id": parent_by_sid.get(sid),
                })
        return result

    def check_food_reminder_cooldown(self, menu_id: int, parent_telegram_id: str, hours: int = 2) -> bool:
        """Return True if this parent was already reminded for this menu within `hours` hours."""
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM food_reminder_log "
                "WHERE menu_id=? AND parent_telegram_id=? AND status='sent' AND created_at >= ?",
                (int(menu_id), str(parent_telegram_id), cutoff),
            ).fetchone()
        return row is not None

    def log_food_reminder(
        self,
        menu_id: int,
        parent_telegram_id: str,
        child_names: list[str],
        status: str,
        error: str = "",
        triggered_by: str = "manual",
    ) -> None:
        import json as _json
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO food_reminder_log"
                "(created_at, menu_id, parent_telegram_id, child_names_json, status, error, triggered_by) "
                "VALUES(?,?,?,?,?,?,?)",
                (now_iso(), int(menu_id), str(parent_telegram_id),
                 _json.dumps(child_names, ensure_ascii=False), status, error or "", triggered_by),
            )

    def get_all_children_with_parents(self) -> list[dict[str, Any]]:
        """Return all active children with their parent link info (for publish notification)."""
        with self._connect() as conn:
            children_rows = conn.execute(
                "SELECT c.mk_student_id, c.full_name, c.group_name, c.mk_class_name, c.classroom, c.raw_json "
                "FROM camp_children c WHERE c.active=1 ORDER BY c.full_name"
            ).fetchall()
            all_children = [dict(r) for r in children_rows]
            if not all_children:
                return []
            sids = [c["mk_student_id"] for c in all_children]
            ph = ", ".join("?" for _ in sids)
            link_rows = conn.execute(
                f"SELECT mk_student_id, parent_telegram_id FROM parent_child_links "
                f"WHERE mk_student_id IN ({ph}) AND active=1",
                sids,
            ).fetchall()
            parent_by_sid: dict[str, str | None] = {r["mk_student_id"]: r["parent_telegram_id"] for r in link_rows}
        result = []
        for ch in all_children:
            sid = ch["mk_student_id"]
            result.append({
                "mk_student_id": sid,
                "full_name": ch["full_name"],
                "groupCode": _get_food_group_code(ch),
                "parent_telegram_id": parent_by_sid.get(sid),
            })
        return result

    def check_food_notification_sent(self, menu_id: int, parent_telegram_id: str) -> bool:
        """Return True if this parent has already been notified for this menu (any time)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id FROM food_menu_notification_log "
                "WHERE menu_id=? AND parent_telegram_id=? AND status='sent'",
                (int(menu_id), str(parent_telegram_id)),
            ).fetchone()
        return row is not None

    def log_food_notification(
        self,
        menu_id: int,
        parent_telegram_id: str,
        child_names: list[str],
        status: str,
        error: str = "",
    ) -> None:
        import json as _json
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO food_menu_notification_log"
                "(created_at, menu_id, parent_telegram_id, child_names_json, status, error) "
                "VALUES(?,?,?,?,?,?)",
                (now_iso(), int(menu_id), str(parent_telegram_id),
                 _json.dumps(child_names, ensure_ascii=False), status, error or ""),
            )

    # --- Food module: staff orders ---

    def upsert_food_staff_order(
        self,
        staff_user_id: int,
        menu_id: int,
        item_quantities: dict,  # {item_id: quantity} — empty for skipped
        status: str = "submitted",
        location_code: str = "",
    ) -> dict[str, Any]:
        now = now_iso()
        uid = int(staff_user_id)
        mid = int(menu_id)
        if isinstance(item_quantities, list):
            item_quantities = {int(iid): 1 for iid in item_quantities}
        safe_qty: dict[int, int] = {
            int(iid): min(99, max(1, int(qty or 1)))
            for iid, qty in (item_quantities or {}).items()
            if int(qty or 0) > 0
        }
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM food_staff_orders WHERE menu_id=? AND staff_user_id=?",
                (mid, uid),
            ).fetchone()
            loc = str(location_code or "").strip().upper() or None
            if existing:
                order_id = existing["id"]
                if loc is not None:
                    conn.execute(
                        "UPDATE food_staff_orders SET status=?, submitted_at=?, updated_at=?, location_code=? WHERE id=?",
                        (status, now, now, loc, order_id),
                    )
                else:
                    conn.execute(
                        "UPDATE food_staff_orders SET status=?, submitted_at=?, updated_at=? WHERE id=?",
                        (status, now, now, order_id),
                    )
            else:
                cur = conn.execute(
                    "INSERT INTO food_staff_orders(created_at, updated_at, menu_id, staff_user_id, status, submitted_at, location_code) VALUES(?,?,?,?,?,?,?)",
                    (now, now, mid, uid, status, now, loc),
                )
                order_id = cur.lastrowid
            conn.execute("DELETE FROM food_staff_order_items WHERE order_id=?", (order_id,))
            if safe_qty:
                id_ph = ", ".join("?" for _ in safe_qty)
                price_rows = conn.execute(
                    f"SELECT id, price FROM food_items WHERE id IN ({id_ph})", list(safe_qty.keys())
                ).fetchall()
                price_map: dict[int, float] = {r["id"]: float(r["price"] or 0) for r in price_rows}
                for iid, qty in safe_qty.items():
                    conn.execute(
                        "INSERT INTO food_staff_order_items(created_at, order_id, item_id, quantity, price_snapshot) VALUES(?,?,?,?,?)",
                        (now, order_id, iid, qty, price_map.get(iid, 0.0)),
                    )
            row = conn.execute("SELECT * FROM food_staff_orders WHERE id=?", (order_id,)).fetchone()
            order_items = conn.execute(
                "SELECT oi.item_id, oi.quantity, oi.price_snapshot, fi.name, fi.category, fi.weight FROM food_staff_order_items oi JOIN food_items fi ON fi.id=oi.item_id WHERE oi.order_id=?",
                (order_id,),
            ).fetchall()
        result = dict(row)
        result["items"] = [dict(i) for i in order_items]
        return result

    def delete_food_child_order(self, order_id: int) -> Optional[dict[str, Any]]:
        """Hard-delete a single child food_order by id. Returns deleted record info or None if not found."""
        oid = int(order_id)
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM food_orders WHERE id=?", (oid,)).fetchone()
            if not row:
                return None
            info = dict(row)
            conn.execute("DELETE FROM food_order_items WHERE order_id=?", (oid,))
            conn.execute("DELETE FROM food_orders WHERE id=?", (oid,))
        return info

    def delete_food_staff_order(self, order_id: int) -> Optional[dict[str, Any]]:
        """Hard-delete a single staff food order by id. Returns deleted record info or None if not found."""
        oid = int(order_id)
        with self._connect() as conn:
            row = conn.execute(
                """SELECT so.*, su.full_name AS staff_name, su.mk_teacher_name AS staff_mk_teacher_name,
                    su.username AS staff_username, su.role AS staff_role
                   FROM food_staff_orders so
                   LEFT JOIN staff_users su ON su.user_id = so.staff_user_id
                   WHERE so.id=?""",
                (oid,),
            ).fetchone()
            if not row:
                return None
            info = dict(row)
            conn.execute("DELETE FROM food_staff_order_items WHERE order_id=?", (oid,))
            conn.execute("DELETE FROM food_staff_orders WHERE id=?", (oid,))
        return info

    # --- Food module: admin manual order management (v7.0.30) ---

    def log_food_order_audit(
        self,
        menu_id: int,
        order_id: Optional[int],
        order_type: str,
        action: str,
        actor_user_id: int,
        actor_role: str,
        old_data: Any,
        new_data: Any,
        comment: str = "",
    ) -> int:
        import json as _json
        now = now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO food_order_audit_log(created_at, menu_id, order_id, order_type, action, actor_user_id, actor_role, old_data, new_data, comment) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (now, int(menu_id), order_id, order_type, action, int(actor_user_id), actor_role,
                 _json.dumps(old_data, ensure_ascii=False) if old_data is not None else None,
                 _json.dumps(new_data, ensure_ascii=False) if new_data is not None else None,
                 str(comment or "")),
            )
            return cur.lastrowid or 0

    def find_food_child_order_for_menu(self, menu_id: int, mk_student_id: str) -> Optional[dict[str, Any]]:
        """Find ANY submitted/skipped order for this child in this menu (regardless of parent_telegram_id)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM food_orders WHERE menu_id=? AND mk_student_id=? ORDER BY id DESC LIMIT 1",
                (int(menu_id), str(mk_student_id)),
            ).fetchone()
            if not row:
                return None
            order = dict(row)
            items = conn.execute(
                "SELECT oi.item_id, oi.quantity, oi.price_snapshot, fi.name, fi.category, fi.weight FROM food_order_items oi JOIN food_items fi ON fi.id=oi.item_id WHERE oi.order_id=?",
                (order["id"],),
            ).fetchall()
            order["items"] = [dict(i) for i in items]
        return order

    def get_food_child_order_by_id(self, order_id: int) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT o.*, c.full_name AS child_display_name
                   FROM food_orders o
                   LEFT JOIN camp_children c ON c.mk_student_id = o.mk_student_id AND c.active = 1
                   WHERE o.id=?""",
                (int(order_id),),
            ).fetchone()
            if not row:
                return None
            order = dict(row)
            items = conn.execute(
                "SELECT oi.item_id, oi.quantity, oi.price_snapshot, fi.name, fi.category, fi.weight FROM food_order_items oi JOIN food_items fi ON fi.id=oi.item_id WHERE oi.order_id=?",
                (order["id"],),
            ).fetchall()
            order["items"] = [dict(i) for i in items]
        return order

    def get_food_staff_order_by_id(self, order_id: int) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                """SELECT so.*, su.full_name AS staff_name, su.mk_teacher_name AS staff_mk_teacher_name,
                          su.username AS staff_username, su.mk_teacher_id AS staff_mk_teacher_id,
                          su.role AS staff_role
                   FROM food_staff_orders so
                   LEFT JOIN staff_users su ON su.user_id = so.staff_user_id
                   WHERE so.id=?""",
                (int(order_id),),
            ).fetchone()
            if not row:
                return None
            order = dict(row)
            items = conn.execute(
                "SELECT oi.item_id, oi.quantity, oi.price_snapshot, fi.name, fi.category, fi.weight FROM food_staff_order_items oi JOIN food_items fi ON fi.id=oi.item_id WHERE oi.order_id=?",
                (order["id"],),
            ).fetchall()
            order["items"] = [dict(i) for i in items]
        return order

    def admin_create_food_child_order(
        self,
        menu_id: int,
        mk_student_id: str,
        item_quantities: dict,
        admin_uid: int,
        admin_comment: str = "",
    ) -> dict[str, Any]:
        now = now_iso()
        mid = int(menu_id)
        sid = str(mk_student_id)
        safe_qty: dict[int, int] = {
            int(iid): min(99, max(1, int(qty or 1)))
            for iid, qty in (item_quantities or {}).items()
            if int(qty or 0) > 0
        }
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM food_orders WHERE menu_id=? AND mk_student_id=? ORDER BY id DESC LIMIT 1",
                (mid, sid),
            ).fetchone()
            if existing:
                raise ValueError("duplicate")
            cur = conn.execute(
                """INSERT INTO food_orders(created_at, updated_at, menu_id, parent_telegram_id, mk_student_id,
                   status, total_price, submitted_at, admin_source, admin_created_by, admin_created_at, admin_comment)
                   VALUES(?,?,?,?,?,?,0,?,?,?,?,?)""",
                (now, now, mid, "admin:0", sid, "submitted", now,
                 "admin_manual", int(admin_uid), now, str(admin_comment or "")),
            )
            order_id = cur.lastrowid
            if safe_qty:
                id_ph = ", ".join("?" for _ in safe_qty)
                price_rows = conn.execute(
                    f"SELECT id, price FROM food_items WHERE id IN ({id_ph})", list(safe_qty.keys())
                ).fetchall()
                price_map: dict[int, float] = {r["id"]: float(r["price"] or 0) for r in price_rows}
                for iid, qty in safe_qty.items():
                    conn.execute(
                        "INSERT INTO food_order_items(created_at, order_id, item_id, quantity, price_snapshot) VALUES(?,?,?,?,?)",
                        (now, order_id, iid, qty, price_map.get(iid, 0.0)),
                    )
            row = conn.execute("SELECT * FROM food_orders WHERE id=?", (order_id,)).fetchone()
            order_items = conn.execute(
                "SELECT oi.item_id, oi.quantity, fi.name, fi.category, fi.weight FROM food_order_items oi JOIN food_items fi ON fi.id=oi.item_id WHERE oi.order_id=?",
                (order_id,),
            ).fetchall()
        result = dict(row)
        result["items"] = [dict(i) for i in order_items]
        return result

    def admin_update_food_child_order(
        self,
        order_id: int,
        item_quantities: dict,
        admin_uid: int,
        admin_comment: str = "",
    ) -> dict[str, Any]:
        now = now_iso()
        oid = int(order_id)
        safe_qty: dict[int, int] = {
            int(iid): min(99, max(1, int(qty or 1)))
            for iid, qty in (item_quantities or {}).items()
            if int(qty or 0) > 0
        }
        with self._connect() as conn:
            _row = conn.execute("SELECT * FROM food_orders WHERE id=?", (oid,)).fetchone()
            if not _row:
                raise ValueError("not_found")
            row = dict(_row)
            mid = row["menu_id"]
            existing_source = row.get("admin_source") or ""
            if existing_source:
                conn.execute(
                    """UPDATE food_orders SET status='submitted', updated_at=?,
                       admin_updated_by=?, admin_updated_at=?, admin_comment=?
                       WHERE id=?""",
                    (now, int(admin_uid), now, str(admin_comment or ""), oid),
                )
            else:
                conn.execute(
                    """UPDATE food_orders SET status='submitted', updated_at=?,
                       admin_source=?, admin_updated_by=?, admin_updated_at=?, admin_comment=?
                       WHERE id=?""",
                    (now, "admin_edit", int(admin_uid), now, str(admin_comment or ""), oid),
                )
            conn.execute("DELETE FROM food_order_items WHERE order_id=?", (oid,))
            if safe_qty:
                id_ph = ", ".join("?" for _ in safe_qty)
                price_rows = conn.execute(
                    f"SELECT id, price FROM food_items WHERE id IN ({id_ph})", list(safe_qty.keys())
                ).fetchall()
                price_map: dict[int, float] = {r["id"]: float(r["price"] or 0) for r in price_rows}
                for iid, qty in safe_qty.items():
                    conn.execute(
                        "INSERT INTO food_order_items(created_at, order_id, item_id, quantity, price_snapshot) VALUES(?,?,?,?,?)",
                        (now, oid, iid, qty, price_map.get(iid, 0.0)),
                    )
            updated = conn.execute("SELECT * FROM food_orders WHERE id=?", (oid,)).fetchone()
            order_items = conn.execute(
                "SELECT oi.item_id, oi.quantity, fi.name, fi.category, fi.weight FROM food_order_items oi JOIN food_items fi ON fi.id=oi.item_id WHERE oi.order_id=?",
                (oid,),
            ).fetchall()
        result = dict(updated)
        result["items"] = [dict(i) for i in order_items]
        return result

    def admin_create_food_staff_order(
        self,
        menu_id: int,
        staff_user_id: int,
        item_quantities: dict,
        location_code: str,
        admin_uid: int,
        admin_comment: str = "",
    ) -> dict[str, Any]:
        now = now_iso()
        mid = int(menu_id)
        uid = int(staff_user_id)
        loc = str(location_code or "").strip().upper() or None
        safe_qty: dict[int, int] = {
            int(iid): min(99, max(1, int(qty or 1)))
            for iid, qty in (item_quantities or {}).items()
            if int(qty or 0) > 0
        }
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM food_staff_orders WHERE menu_id=? AND staff_user_id=?",
                (mid, uid),
            ).fetchone()
            if existing:
                raise ValueError("duplicate")
            cur = conn.execute(
                """INSERT INTO food_staff_orders(created_at, updated_at, menu_id, staff_user_id, status,
                   submitted_at, location_code, admin_source, admin_created_by, admin_created_at, admin_comment)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (now, now, mid, uid, "submitted", now, loc,
                 "admin_manual", int(admin_uid), now, str(admin_comment or "")),
            )
            order_id = cur.lastrowid
            if safe_qty:
                id_ph = ", ".join("?" for _ in safe_qty)
                price_rows = conn.execute(
                    f"SELECT id, price FROM food_items WHERE id IN ({id_ph})", list(safe_qty.keys())
                ).fetchall()
                price_map: dict[int, float] = {r["id"]: float(r["price"] or 0) for r in price_rows}
                for iid, qty in safe_qty.items():
                    conn.execute(
                        "INSERT INTO food_staff_order_items(created_at, order_id, item_id, quantity, price_snapshot) VALUES(?,?,?,?,?)",
                        (now, order_id, iid, qty, price_map.get(iid, 0.0)),
                    )
            row = conn.execute("SELECT * FROM food_staff_orders WHERE id=?", (order_id,)).fetchone()
            order_items = conn.execute(
                "SELECT oi.item_id, oi.quantity, fi.name, fi.category, fi.weight FROM food_staff_order_items oi JOIN food_items fi ON fi.id=oi.item_id WHERE oi.order_id=?",
                (order_id,),
            ).fetchall()
        result = dict(row)
        result["items"] = [dict(i) for i in order_items]
        return result

    def admin_update_food_staff_order(
        self,
        order_id: int,
        item_quantities: dict,
        location_code: str,
        admin_uid: int,
        admin_comment: str = "",
    ) -> dict[str, Any]:
        now = now_iso()
        oid = int(order_id)
        loc = str(location_code or "").strip().upper() or None
        safe_qty: dict[int, int] = {
            int(iid): min(99, max(1, int(qty or 1)))
            for iid, qty in (item_quantities or {}).items()
            if int(qty or 0) > 0
        }
        with self._connect() as conn:
            _row = conn.execute("SELECT * FROM food_staff_orders WHERE id=?", (oid,)).fetchone()
            if not _row:
                raise ValueError("not_found")
            row = dict(_row)
            existing_source = row.get("admin_source") or ""
            if existing_source:
                if loc is not None:
                    conn.execute(
                        """UPDATE food_staff_orders SET status='submitted', updated_at=?, location_code=?,
                           admin_updated_by=?, admin_updated_at=?, admin_comment=? WHERE id=?""",
                        (now, loc, int(admin_uid), now, str(admin_comment or ""), oid),
                    )
                else:
                    conn.execute(
                        """UPDATE food_staff_orders SET status='submitted', updated_at=?,
                           admin_updated_by=?, admin_updated_at=?, admin_comment=? WHERE id=?""",
                        (now, int(admin_uid), now, str(admin_comment or ""), oid),
                    )
            else:
                if loc is not None:
                    conn.execute(
                        """UPDATE food_staff_orders SET status='submitted', updated_at=?, location_code=?,
                           admin_source=?, admin_updated_by=?, admin_updated_at=?, admin_comment=? WHERE id=?""",
                        (now, loc, "admin_edit", int(admin_uid), now, str(admin_comment or ""), oid),
                    )
                else:
                    conn.execute(
                        """UPDATE food_staff_orders SET status='submitted', updated_at=?,
                           admin_source=?, admin_updated_by=?, admin_updated_at=?, admin_comment=? WHERE id=?""",
                        (now, "admin_edit", int(admin_uid), now, str(admin_comment or ""), oid),
                    )
            conn.execute("DELETE FROM food_staff_order_items WHERE order_id=?", (oid,))
            if safe_qty:
                id_ph = ", ".join("?" for _ in safe_qty)
                price_rows = conn.execute(
                    f"SELECT id, price FROM food_items WHERE id IN ({id_ph})", list(safe_qty.keys())
                ).fetchall()
                price_map: dict[int, float] = {r["id"]: float(r["price"] or 0) for r in price_rows}
                for iid, qty in safe_qty.items():
                    conn.execute(
                        "INSERT INTO food_staff_order_items(created_at, order_id, item_id, quantity, price_snapshot) VALUES(?,?,?,?,?)",
                        (now, oid, iid, qty, price_map.get(iid, 0.0)),
                    )
            updated = conn.execute("SELECT * FROM food_staff_orders WHERE id=?", (oid,)).fetchone()
            order_items = conn.execute(
                "SELECT oi.item_id, oi.quantity, fi.name, fi.category, fi.weight FROM food_staff_order_items oi JOIN food_items fi ON fi.id=oi.item_id WHERE oi.order_id=?",
                (oid,),
            ).fetchall()
        result = dict(updated)
        result["items"] = [dict(i) for i in order_items]
        return result

    def get_food_staff_order(self, staff_user_id: int, menu_id: int) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM food_staff_orders WHERE menu_id=? AND staff_user_id=?",
                (int(menu_id), int(staff_user_id)),
            ).fetchone()
            if not row:
                return None
            order = dict(row)
            items = conn.execute(
                "SELECT oi.item_id, oi.quantity, oi.price_snapshot, fi.name, fi.category, fi.weight FROM food_staff_order_items oi JOIN food_items fi ON fi.id=oi.item_id WHERE oi.order_id=?",
                (order["id"],),
            ).fetchall()
            order["items"] = [dict(i) for i in items]
        return order

    def list_food_staff_orders_for_menu(self, menu_id: int) -> list[dict[str, Any]]:
        mid = int(menu_id)
        with self._connect() as conn:
            orders_rows = conn.execute(
                """SELECT so.*,
                    su.full_name AS staff_name,
                    su.mk_teacher_name AS staff_mk_teacher_name,
                    su.username AS staff_username,
                    su.mk_teacher_id AS staff_mk_teacher_id,
                    su.role AS staff_role
                FROM food_staff_orders so
                LEFT JOIN staff_users su ON su.user_id = so.staff_user_id
                WHERE so.menu_id=? ORDER BY so.id""",
                (mid,),
            ).fetchall()
            result = []
            for orow in orders_rows:
                order = dict(orow)
                items = conn.execute(
                    "SELECT oi.item_id, oi.quantity, oi.price_snapshot, fi.name, fi.category, fi.weight FROM food_staff_order_items oi JOIN food_items fi ON fi.id=oi.item_id WHERE oi.order_id=?",
                    (order["id"],),
                ).fetchall()
                order["items"] = [dict(i) for i in items]
                result.append(order)
        return result

    def get_food_menu_audit_raw(self, menu_id: int) -> Optional[dict[str, Any]]:
        """Return raw unfiltered data for audit: all orders (all statuses), all items, all staff orders.
        Child orders include camp_children fields so _get_food_group_info can resolve location."""
        mid = int(menu_id)
        with self._connect() as conn:
            menu_row = conn.execute("SELECT * FROM food_menus WHERE id=?", (mid,)).fetchone()
            if not menu_row:
                return None
            menu = dict(menu_row)
            # All child orders — join camp_children to get group_name/mk_class_name/classroom/raw_json
            # so _get_food_group_info can determine the YC location code.
            child_orders_rows = conn.execute(
                """SELECT o.*,
                          c.full_name AS child_name,
                          c.group_name, c.mk_class_name, c.classroom, c.raw_json
                   FROM food_orders o
                   LEFT JOIN camp_children c ON c.mk_student_id = o.mk_student_id AND c.active = 1
                   WHERE o.menu_id=? ORDER BY o.id""",
                (mid,),
            ).fetchall()
            child_orders = []
            for orow in child_orders_rows:
                o = dict(orow)
                items = conn.execute(
                    "SELECT oi.item_id, oi.quantity, oi.price_snapshot, fi.name, fi.category "
                    "FROM food_order_items oi JOIN food_items fi ON fi.id=oi.item_id WHERE oi.order_id=?",
                    (o["id"],),
                ).fetchall()
                o["items"] = [dict(i) for i in items]
                child_orders.append(o)
            # All staff orders for this menu, regardless of status
            staff_orders_rows = conn.execute(
                """SELECT so.*, su.full_name AS staff_name, su.mk_teacher_name AS staff_mk_teacher_name,
                          su.username AS staff_username, su.mk_teacher_id AS staff_mk_teacher_id,
                          su.role AS staff_role
                   FROM food_staff_orders so
                   LEFT JOIN staff_users su ON su.user_id = so.staff_user_id
                   WHERE so.menu_id=? ORDER BY so.id""",
                (mid,),
            ).fetchall()
            staff_orders = []
            for orow in staff_orders_rows:
                o = dict(orow)
                items = conn.execute(
                    "SELECT oi.item_id, oi.quantity, oi.price_snapshot, fi.name, fi.category "
                    "FROM food_staff_order_items oi JOIN food_items fi ON fi.id=oi.item_id WHERE oi.order_id=?",
                    (o["id"],),
                ).fetchall()
                o["items"] = [dict(i) for i in items]
                staff_orders.append(o)
            # All food items for this menu
            food_items_rows = conn.execute(
                "SELECT * FROM food_items WHERE menu_id=? ORDER BY category, sort_order, id", (mid,)
            ).fetchall()
            food_items = [dict(r) for r in food_items_rows]
        return {"menu": menu, "childOrders": child_orders, "staffOrders": staff_orders, "foodItems": food_items}

    def get_published_menus_needing_auto_reminder(self, minutes_before_deadline: int) -> list[dict[str, Any]]:
        """Return published menus whose deadline is within minutes_before_deadline minutes from now."""
        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(minutes=minutes_before_deadline)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM food_menus WHERE status='published' AND deadline_at IS NOT NULL AND deadline_at != '' "
                "ORDER BY deadline_at"
            ).fetchall()
        result = []
        for row in rows:
            m = dict(row)
            deadline_str = m.get("deadline_at") or ""
            try:
                dl = datetime.fromisoformat(deadline_str.replace("Z", "+00:00"))
                if dl.tzinfo is None:
                    dl = dl.replace(tzinfo=timezone.utc)
                if now < dl <= cutoff:
                    result.append(m)
            except Exception:
                pass
        return result

    def get_food_data_status(self) -> dict[str, Any]:
        def _c(conn: sqlite3.Connection, table: str, where: str = "") -> int:
            q = f"SELECT COUNT(*) FROM {table}"
            if where:
                q += f" WHERE {where}"
            try:
                return int(conn.execute(q).fetchone()[0])
            except sqlite3.OperationalError:
                return 0
        with self._connect() as conn:
            return {
                "campChildren": _c(conn, "camp_children", "active=1"),
                "activeLinkCodes": _c(conn, "parent_child_links", "active=1"),
                "parentLinks": _c(conn, "parent_child_links", "active=1 AND parent_telegram_id IS NOT NULL"),
                "foodMenus": _c(conn, "food_menus"),
                "foodItems": _c(conn, "food_items", "is_available=1"),
                "foodOrders": _c(conn, "food_orders"),
            }

    def get_food_shift_report(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict[str, Any]:
        with self._connect() as conn:
            if start_date and end_date:
                menu_rows = conn.execute(
                    "SELECT * FROM food_menus WHERE menu_date >= ? AND menu_date <= ? ORDER BY menu_date",
                    (start_date, end_date),
                ).fetchall()
            elif start_date:
                menu_rows = conn.execute(
                    "SELECT * FROM food_menus WHERE menu_date >= ? ORDER BY menu_date",
                    (start_date,),
                ).fetchall()
            elif end_date:
                menu_rows = conn.execute(
                    "SELECT * FROM food_menus WHERE menu_date <= ? ORDER BY menu_date",
                    (end_date,),
                ).fetchall()
            else:
                menu_rows = conn.execute("SELECT * FROM food_menus ORDER BY menu_date").fetchall()
            menus = [dict(r) for r in menu_rows]
            menu_ids = [m["id"] for m in menus]
            if not menu_ids:
                return {
                    "menus": [],
                    "totals": {"ordersCount": 0, "childrenCount": 0, "submittedCount": 0, "skippedCount": 0, "missingCount": 0, "totalCost": 0.0},
                    "byDays": [],
                    "byLocations": [],
                    "byChildren": [],
                    "warnings": [],
                }
            children_rows = conn.execute(
                """SELECT c.mk_student_id, c.full_name, c.group_name, c.mk_class_name, c.classroom, c.raw_json
                   FROM camp_children c WHERE c.active = 1 ORDER BY c.full_name"""
            ).fetchall()
            all_children = [dict(r) for r in children_rows]
            child_by_sid: dict[str, dict] = {c["mk_student_id"]: c for c in all_children}
            id_ph = ", ".join("?" for _ in menu_ids)
            orders_rows = conn.execute(
                f"SELECT * FROM food_orders WHERE menu_id IN ({id_ph})", menu_ids
            ).fetchall()
            all_orders: list[dict] = [dict(r) for r in orders_rows]
            order_ids = [o["id"] for o in all_orders]
            order_map: dict[int, dict] = {o["id"]: o for o in all_orders}
            order_items_map: dict[int, list[dict]] = {o["id"]: [] for o in all_orders}
            if order_ids:
                oi_ph = ", ".join("?" for _ in order_ids)
                oi_rows = conn.execute(
                    f"""SELECT oi.order_id, oi.item_id, oi.quantity, oi.price_snapshot,
                               fi.price AS item_current_price, fi.name AS item_name
                        FROM food_order_items oi
                        JOIN food_items fi ON fi.id = oi.item_id
                        WHERE oi.order_id IN ({oi_ph})""",
                    order_ids,
                ).fetchall()
                for r in oi_rows:
                    row = dict(r)
                    order_items_map[row["order_id"]].append(row)
        for o in all_orders:
            o["_items"] = order_items_map[o["id"]]
        orders_by_menu: dict[int, dict[str, dict]] = {}
        for o in all_orders:
            orders_by_menu.setdefault(o["menu_id"], {})[o["mk_student_id"]] = o

        has_estimated = False
        child_totals: dict[str, dict] = {}
        by_days: list[dict] = []
        loc_totals: dict[str, float] = {}
        loc_counts: dict[str, set] = {}
        total_submitted = 0
        total_skipped = 0
        total_missing = 0
        total_cost = 0.0

        def _order_cost(order: dict) -> tuple[float, bool]:
            cost = 0.0
            estimated = False
            for it in order.get("_items", []):
                snap = float(it["price_snapshot"] or 0)
                cur = float(it["item_current_price"] or 0)
                qty = int(it["quantity"] or 1)
                if snap > 0:
                    cost += snap * qty
                elif cur > 0:
                    cost += cur * qty
                    estimated = True
            return cost, estimated

        for menu in menus:
            mid = menu["id"]
            menu_date = menu.get("menu_date", "")
            menu_title = menu.get("title") or menu_date
            menu_orders = orders_by_menu.get(mid, {})
            day_submitted = 0
            day_skipped = 0
            day_missing = 0
            day_cost = 0.0
            for ch in all_children:
                sid = ch["mk_student_id"]
                order = menu_orders.get(sid)
                group_info = _get_food_group_info(ch)
                gc = group_info["groupCode"]
                cdata = child_totals.setdefault(sid, {
                    "childName": ch["full_name"],
                    "groupCode": gc,
                    "daysCount": 0,
                    "submittedCount": 0,
                    "skippedCount": 0,
                    "missingCount": 0,
                    "totalCost": 0.0,
                })
                cdata["daysCount"] += 1
                loc_counts.setdefault(gc, set()).add(sid)
                if order and order["status"] == "submitted":
                    day_submitted += 1
                    cdata["submittedCount"] += 1
                    cost, est = _order_cost(order)
                    if est:
                        has_estimated = True
                    day_cost += cost
                    total_cost += cost
                    cdata["totalCost"] += cost
                    loc_totals[gc] = loc_totals.get(gc, 0.0) + cost
                elif order and order["status"] == "skipped":
                    day_skipped += 1
                    cdata["skippedCount"] += 1
                else:
                    day_missing += 1
                    cdata["missingCount"] += 1
            total_submitted += day_submitted
            total_skipped += day_skipped
            total_missing += day_missing
            by_days.append({
                "date": menu_date,
                "menuTitle": menu_title,
                "cost": round(day_cost, 2),
                "submittedCount": day_submitted,
                "skippedCount": day_skipped,
                "missingCount": day_missing,
            })
        by_locations = [
            {
                "groupCode": gc,
                "cost": round(loc_totals.get(gc, 0.0), 2),
                "childrenCount": len(loc_counts.get(gc, set())),
            }
            for gc in sorted(loc_totals.keys())
        ]
        by_children = sorted(child_totals.values(), key=lambda x: x["childName"])
        for c in by_children:
            c["totalCost"] = round(c["totalCost"], 2)
        warnings: list[str] = []
        if has_estimated:
            warnings.append("Некоторые цены взяты из текущего прайса (price_snapshot=0 для старых заказов)")
        return {
            "menus": menus,
            "totals": {
                "ordersCount": len(all_orders),
                "childrenCount": len(all_children),
                "submittedCount": total_submitted,
                "skippedCount": total_skipped,
                "missingCount": total_missing,
                "totalCost": round(total_cost, 2),
            },
            "byDays": by_days,
            "byLocations": by_locations,
            "byChildren": by_children,
            "warnings": warnings,
        }

    def save_message(self, chat_id: int, thread_id: Optional[int], chat_type: str, user_id: Optional[int], username: str, full_name: str, text: str, is_bot: bool = False) -> None:
        if not text:
            return
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO messages(created_at, chat_id, thread_id, chat_type, user_id, username, full_name, text, is_bot) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (now_iso(), chat_id, thread_id, chat_type, user_id, username or "", full_name or "", text[:8000], 1 if is_bot else 0),
            )

    def count_messages(self) -> int:
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0])

    def recent_messages(self, chat_id: int, thread_id: Optional[int] = None, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if thread_id is not None:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE chat_id=? AND thread_id=? ORDER BY id DESC LIMIT ?",
                    (chat_id, thread_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT ?",
                    (chat_id, limit),
                ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def search_messages(self, query: str, chat_id: Optional[int] = None, thread_id: Optional[int] = None, limit: int = 10) -> list[dict[str, Any]]:
        words = [w for w in query.split() if len(w) > 2]
        if not words:
            return []
        where = []
        params: list[Any] = []
        for w in words[:6]:
            where.append("LOWER(text) LIKE LOWER(?)")
            params.append(f"%{w}%")
        if chat_id is not None:
            where.append("chat_id=?")
            params.append(chat_id)
        if thread_id is not None:
            where.append("thread_id=?")
            params.append(thread_id)
        sql = "SELECT * FROM messages WHERE " + " AND ".join(where) + " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def add_fact(self, text: str, created_by: Optional[int]) -> int:
        with self._connect() as conn:
            cur = conn.execute("INSERT INTO facts(created_at, created_by, text) VALUES (?, ?, ?)", (now_iso(), created_by, text.strip()))
            return int(cur.lastrowid)

    def list_facts(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM facts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def forget_fact(self, fact_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM facts WHERE id=?", (fact_id,))
            return cur.rowcount > 0

    def add_decision(self, text: str, chat_id: Optional[int], thread_id: Optional[int], created_by: Optional[int]) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO decisions(created_at, chat_id, thread_id, created_by, text) VALUES (?, ?, ?, ?, ?)",
                (now_iso(), chat_id, thread_id, created_by, text.strip()),
            )
            return int(cur.lastrowid)

    def list_decisions(self, chat_id: Optional[int] = None, thread_id: Optional[int] = None, limit: int = 30) -> list[dict[str, Any]]:
        where = []
        params: list[Any] = []
        if chat_id is not None:
            where.append("chat_id=?")
            params.append(chat_id)
        if thread_id is not None:
            where.append("thread_id=?")
            params.append(thread_id)
        sql = "SELECT * FROM decisions"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def search_decisions(self, query: str, chat_id: Optional[int] = None, thread_id: Optional[int] = None, limit: int = 8) -> list[dict[str, Any]]:
        words = [w for w in query.split() if len(w) > 2]
        if not words:
            return []
        where = []
        params: list[Any] = []
        for w in words[:6]:
            where.append("LOWER(text) LIKE LOWER(?)")
            params.append(f"%{w}%")
        if chat_id is not None:
            where.append("chat_id=?")
            params.append(chat_id)
        if thread_id is not None:
            where.append("thread_id=?")
            params.append(thread_id)
        sql = "SELECT * FROM decisions WHERE " + " AND ".join(where) + " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def forget_decision(self, decision_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM decisions WHERE id=?", (decision_id,))
            return cur.rowcount > 0

    def add_feedback(self, rating: str, text: str, chat_id: Optional[int], thread_id: Optional[int], user_id: Optional[int], username: str = "") -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO feedback(created_at, chat_id, thread_id, user_id, username, rating, text) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (now_iso(), chat_id, thread_id, user_id, username or "", rating.strip(), (text or "").strip()),
            )
            return int(cur.lastrowid)

    def list_feedback(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM feedback ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def create_or_update_trial(self, chat_id: int, thread_id: Optional[int], created_by: Optional[int], updates: dict[str, str]) -> int:
        trial = self.get_open_trial(chat_id, thread_id)
        now = now_iso()
        if trial:
            trial_id = int(trial["id"])
            allowed = ["child_name", "parent_name", "age", "direction", "format", "location", "contact", "preferred_time", "notes"]
            sets = []
            params: list[Any] = []
            for k, v in updates.items():
                if k in allowed and v:
                    old = trial.get(k) or ""
                    if k == "notes" and old and v not in old:
                        v = old + "\n" + v
                    # Do not overwrite already collected structured fields with weaker duplicates.
                    if k != "notes" and old and old == v:
                        continue
                    sets.append(f"{k}=?")
                    params.append(v)
            if sets:
                sets.append("updated_at=?")
                params.append(now)
                params.append(trial_id)
                with self._connect() as conn:
                    conn.execute(f"UPDATE trial_requests SET {', '.join(sets)} WHERE id=?", params)
            return trial_id
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO trial_requests(created_at, updated_at, chat_id, thread_id, created_by, child_name, parent_name, age, direction, format, location, contact, preferred_time, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now, now, chat_id, thread_id, created_by,
                    updates.get("child_name"), updates.get("parent_name"), updates.get("age"), updates.get("direction"),
                    updates.get("format"), updates.get("location"), updates.get("contact"), updates.get("preferred_time"), updates.get("notes"),
                ),
            )
            return int(cur.lastrowid)

    def get_open_trial(self, chat_id: int, thread_id: Optional[int]) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            if thread_id is not None:
                row = conn.execute(
                    "SELECT * FROM trial_requests WHERE chat_id=? AND thread_id=? AND status='open' ORDER BY id DESC LIMIT 1",
                    (chat_id, thread_id),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM trial_requests WHERE chat_id=? AND status='open' ORDER BY id DESC LIMIT 1",
                    (chat_id,),
                ).fetchone()
        return dict(row) if row else None

    def list_trials(self, status: str = "open", limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            if status == "all":
                rows = conn.execute("SELECT * FROM trial_requests ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
            else:
                rows = conn.execute("SELECT * FROM trial_requests WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit)).fetchall()
        return [dict(r) for r in rows]

    def close_trial(self, trial_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("UPDATE trial_requests SET status='done', updated_at=? WHERE id=?", (now_iso(), trial_id))
            return cur.rowcount > 0

    def cancel_trial(self, trial_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("UPDATE trial_requests SET status='cancelled', updated_at=? WHERE id=?", (now_iso(), trial_id))
            return cur.rowcount > 0

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def get_setting(self, key: str, default: str = "") -> str:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return str(row[0]) if row else default

    # --- Staff registration and roles ---

    def register_staff_user(self, user_id: int, username: str, full_name: str, role: str) -> None:
        role = (role or "other").strip().lower()
        now = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO staff_users(user_id, username, full_name, role, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'active', ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username=excluded.username,
                    full_name=excluded.full_name,
                    role=excluded.role,
                    status='active',
                    updated_at=excluded.updated_at
                """,
                (user_id, username or "", full_name or "", role, now, now),
            )

    def get_staff_user(self, user_id: int | None) -> Optional[dict[str, Any]]:
        if not user_id:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM staff_users WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None

    def get_staff_role(self, user_id: int | None, admin_ids: Optional[list[int]] = None) -> str:
        if user_id and admin_ids and user_id in admin_ids:
            return "owner"
        user = self.get_staff_user(user_id)
        if not user or user.get("status") != "active":
            return ""
        return str(user.get("role") or "")

    def list_staff_users(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM staff_users ORDER BY role, full_name, user_id LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def set_staff_role(self, user_id: int, role: str) -> bool:
        role = (role or "other").strip().lower()
        now = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO staff_users(user_id, username, full_name, role, status, created_at, updated_at)
                VALUES (?, '', '', ?, 'active', ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    role=excluded.role,
                    status='active',
                    updated_at=excluded.updated_at
                """,
                (user_id, role, now, now),
            )
            return True

    def deactivate_staff_user(self, user_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("UPDATE staff_users SET status='inactive', updated_at=? WHERE user_id=?", (now_iso(), user_id))
            return cur.rowcount > 0

    def activate_staff_user(self, user_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute("UPDATE staff_users SET status='active', updated_at=? WHERE user_id=?", (now_iso(), user_id))
            return cur.rowcount > 0

    def set_staff_mk_teacher(self, user_id: int, mk_teacher_id: str | int, mk_teacher_name: str = "") -> bool:
        """Link Telegram staff user to MoyKlass teacherId.

        If the Telegram user is not in staff_users yet, create a teacher record with empty name.
        Owner can change role later via /set_role.
        """
        teacher_id = str(mk_teacher_id or "").strip()
        if not teacher_id:
            return False
        now = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO staff_users(user_id, username, full_name, role, status, created_at, updated_at, mk_teacher_id, mk_teacher_name, mk_linked_at)
                VALUES (?, '', '', 'teacher', 'active', ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    mk_teacher_id=excluded.mk_teacher_id,
                    mk_teacher_name=excluded.mk_teacher_name,
                    mk_linked_at=excluded.mk_linked_at,
                    status='active',
                    updated_at=excluded.updated_at
                """,
                (int(user_id), now, now, teacher_id, mk_teacher_name or "", now),
            )
            return True

    def clear_staff_mk_teacher(self, user_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE staff_users SET mk_teacher_id='', mk_teacher_name='', mk_linked_at='', updated_at=? WHERE user_id=?",
                (now_iso(), int(user_id)),
            )
            return cur.rowcount > 0

    def get_staff_mk_teacher_id(self, user_id: int | None) -> str:
        if not user_id:
            return ""
        user = self.get_staff_user(user_id)
        if not user or user.get("status") != "active":
            return ""
        return str(user.get("mk_teacher_id") or "").strip()

    def update_staff_display_name(self, user_id: int, full_name: str, username: str = "") -> None:
        """Update full_name and/or username in staff_users only when currently empty."""
        now = now_iso()
        with self._connect() as conn:
            if full_name:
                conn.execute(
                    "UPDATE staff_users SET full_name=?, updated_at=? WHERE user_id=? AND (full_name IS NULL OR full_name='')",
                    (full_name.strip(), now, int(user_id)),
                )
            if username:
                conn.execute(
                    "UPDATE staff_users SET username=?, updated_at=? WHERE user_id=? AND (username IS NULL OR username='')",
                    (username.strip(), now, int(user_id)),
                )

    def get_teacher_name_by_mk_id(self, mk_teacher_id: str | int) -> list[str]:
        """Return distinct teacher names from teacher_lesson_control matching this mk_teacher_id."""
        tid = str(mk_teacher_id or "").strip()
        if not tid:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT teacher_name FROM teacher_lesson_control "
                "WHERE mk_teacher_id=? AND teacher_name IS NOT NULL AND teacher_name != '' "
                "ORDER BY teacher_name LIMIT 10",
                (tid,),
            ).fetchall()
        return [str(r[0]).strip() for r in rows if str(r[0]).strip()]

    def update_staff_mk_teacher_name(self, user_id: int, name: str) -> bool:
        """Overwrite mk_teacher_name for a staff user (used by admin sync from MK)."""
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE staff_users SET mk_teacher_name=?, updated_at=? WHERE user_id=?",
                (name.strip(), now_iso(), int(user_id)),
            )
            return cur.rowcount > 0

    def get_staff_by_mk_teacher_id(self, mk_teacher_id: str | int) -> Optional[dict[str, Any]]:
        teacher_id = str(mk_teacher_id or "").strip()
        if not teacher_id:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM staff_users WHERE mk_teacher_id=? AND status='active' LIMIT 1", (teacher_id,)).fetchone()
        return dict(row) if row else None

    def set_staff_test_mode(self, user_id: int, role: str, mk_teacher_id: str = "") -> bool:
        """Enable owner-only test mode for role/cabinet testing.

        This does not change the real employee role. It only stores a temporary
        effective role for the owner so the owner can test teacher/manager panels
        from the same Telegram account.
        """
        role = (role or "other").strip().lower()
        now = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO staff_users(user_id, username, full_name, role, status, created_at, updated_at, test_role, test_mk_teacher_id, test_enabled)
                VALUES (?, '', '', 'owner', 'active', ?, ?, ?, ?, 1)
                ON CONFLICT(user_id) DO UPDATE SET
                    test_role=excluded.test_role,
                    test_mk_teacher_id=CASE
                        WHEN excluded.test_mk_teacher_id IS NOT NULL AND excluded.test_mk_teacher_id != '' THEN excluded.test_mk_teacher_id
                        ELSE COALESCE(staff_users.test_mk_teacher_id, '')
                    END,
                    test_enabled=1,
                    updated_at=excluded.updated_at
                """,
                (int(user_id), now, now, role, str(mk_teacher_id or "").strip()),
            )
            return True

    def clear_staff_test_mode(self, user_id: int) -> bool:
        with self._connect() as conn:
            conn.execute(
                "UPDATE staff_users SET test_enabled=0, test_role='', test_mk_teacher_id='', updated_at=? WHERE user_id=?",
                (now_iso(), int(user_id)),
            )
            return True

    def set_staff_test_teacher_id(self, user_id: int, mk_teacher_id: str | int) -> bool:
        teacher_id = str(mk_teacher_id or "").strip()
        if not teacher_id:
            return False
        now = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO staff_users(user_id, username, full_name, role, status, created_at, updated_at, test_role, test_mk_teacher_id, test_enabled)
                VALUES (?, '', '', 'owner', 'active', ?, ?, 'teacher', ?, 1)
                ON CONFLICT(user_id) DO UPDATE SET
                    test_mk_teacher_id=excluded.test_mk_teacher_id,
                    test_enabled=1,
                    test_role=CASE
                        WHEN staff_users.test_role IS NULL OR staff_users.test_role='' THEN 'teacher'
                        ELSE staff_users.test_role
                    END,
                    updated_at=excluded.updated_at
                """,
                (int(user_id), now, now, teacher_id),
            )
            return True

    def get_staff_test_mode(self, user_id: int | None) -> dict[str, Any]:
        if not user_id:
            return {"enabled": False, "role": "", "mk_teacher_id": ""}
        user = self.get_staff_user(user_id)
        if not user:
            return {"enabled": False, "role": "", "mk_teacher_id": ""}
        enabled = bool(int(user.get("test_enabled") or 0))
        return {
            "enabled": enabled,
            "role": str(user.get("test_role") or "").strip(),
            "mk_teacher_id": str(user.get("test_mk_teacher_id") or "").strip(),
        }



    # --- Teacher lesson control MVP ---

    def upsert_teacher_lesson_control(self, lesson_id: str | int, **fields: Any) -> dict[str, Any]:
        """Create/update local teacher control status for one MoyKlass lesson."""
        lesson_id = str(lesson_id or "").strip()
        if not lesson_id:
            return {}
        allowed = {
            "lesson_date", "lesson_time", "group_name", "lesson_topic", "teacher_user_id",
            "teacher_name", "mk_teacher_id", "notion_url", "preparation_status",
            "preparation_confirmed_at", "preparation_comment", "lesson_status",
            "myclass_status", "parent_report_status", "works_status", "classroom_status",
            "problem_status", "problem_comment", "closed_at", "closed_by",
            "prep_material_status", "prep_video_status", "prep_practice_status",
            "prep_result_status", "lesson_comment", "prep_result_file_id",
            "filial_name", "lesson_room_id",
        }
        now = now_iso()
        clean: dict[str, Any] = {k: v for k, v in fields.items() if k in allowed and v is not None}
        with self._connect() as conn:
            existing = conn.execute("SELECT * FROM teacher_lesson_control WHERE lesson_id=?", (lesson_id,)).fetchone()
            if existing:
                if clean:
                    assignments = ", ".join([f"{k}=?" for k in clean.keys()])
                    params = list(clean.values()) + [now, lesson_id]
                    conn.execute(
                        f"UPDATE teacher_lesson_control SET {assignments}, updated_at=? WHERE lesson_id=?",
                        params,
                    )
            else:
                base = {"lesson_id": lesson_id, "created_at": now, "updated_at": now}
                base.update(clean)
                columns = list(base.keys())
                placeholders = ", ".join(["?"] * len(columns))
                conn.execute(
                    f"INSERT INTO teacher_lesson_control({', '.join(columns)}) VALUES ({placeholders})",
                    [base[c] for c in columns],
                )
            row = conn.execute("SELECT * FROM teacher_lesson_control WHERE lesson_id=?", (lesson_id,)).fetchone()
        return dict(row) if row else {}

    def get_teacher_lesson_control(self, lesson_id: str | int) -> Optional[dict[str, Any]]:
        lesson_id = str(lesson_id or "").strip()
        if not lesson_id:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM teacher_lesson_control WHERE lesson_id=?", (lesson_id,)).fetchone()
        return dict(row) if row else None

    def list_teacher_lesson_control(self, lesson_date: str | None = None, teacher_user_id: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
        where = []
        params: list[Any] = []
        if lesson_date:
            where.append("lesson_date=?")
            params.append(str(lesson_date))
        if teacher_user_id:
            where.append("teacher_user_id=?")
            params.append(int(teacher_user_id))
        sql = "SELECT * FROM teacher_lesson_control"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY COALESCE(lesson_date, ''), COALESCE(lesson_time, ''), lesson_id LIMIT ?"
        params.append(int(limit or 200))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def teacher_has_lesson_on_date(self, mk_teacher_id: str, date_str: str) -> bool:
        """Return True if teacher has any lesson on date_str.
        Checks teacher_lesson_control first, then lesson_snapshots (MoyKlass background sync)."""
        if not mk_teacher_id or not date_str:
            return False
        tid = str(mk_teacher_id).strip()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM teacher_lesson_control WHERE mk_teacher_id=? AND lesson_date=? LIMIT 1",
                (tid, str(date_str)),
            ).fetchone()
            if row:
                return True
            # Fallback: lesson_snapshots contains all MoyKlass-synced lessons including future ones
            snap_rows = conn.execute(
                "SELECT teacher_ids FROM lesson_snapshots "
                "WHERE lesson_date=? AND teacher_ids IS NOT NULL AND teacher_ids != ''",
                (str(date_str),),
            ).fetchall()
        for snap in snap_rows:
            ids = [x.strip() for x in str(snap["teacher_ids"] or "").split(",") if x.strip()]
            if tid in ids:
                return True
        return False

    def get_teacher_lesson_locations(self, mk_teacher_id: str, date_str: str) -> list[str]:
        """Return distinct offline YC location codes for a teacher's food-eligible lessons on date_str.

        Uses filial_name/lesson_room_id priority over group_name to correctly exclude online lessons.
        Checks teacher_lesson_control first; falls back to lesson_snapshots.
        """
        if not mk_teacher_id or not date_str:
            return []
        tid = str(mk_teacher_id).strip()
        codes: list[str] = []
        with self._connect() as conn:
            ctrl_rows = conn.execute(
                "SELECT tlc.group_name, tlc.filial_name, tlc.lesson_room_id, ls.raw_preview "
                "FROM teacher_lesson_control tlc "
                "LEFT JOIN lesson_snapshots ls ON ls.lesson_id = tlc.lesson_id "
                "WHERE tlc.mk_teacher_id=? AND tlc.lesson_date=?",
                (tid, str(date_str)),
            ).fetchall()
            for row in ctrl_rows:
                fn = str(row["filial_name"] or "")
                ri = str(row["lesson_room_id"] or "")
                if not fn and not ri:
                    fn, ri = _filial_from_raw_preview(row["raw_preview"] or "")
                loc, is_online, _ = _resolve_lesson_location(row["group_name"] or "", fn, ri)
                if loc and not is_online and loc not in codes:
                    codes.append(loc)
            if not codes:
                snap_rows = conn.execute(
                    "SELECT group_name, teacher_ids, filial_name, lesson_room_id, raw_preview "
                    "FROM lesson_snapshots "
                    "WHERE lesson_date=? AND teacher_ids IS NOT NULL AND teacher_ids != ''",
                    (str(date_str),),
                ).fetchall()
                for row in snap_rows:
                    ids = [x.strip() for x in str(row["teacher_ids"] or "").split(",") if x.strip()]
                    if tid not in ids:
                        continue
                    fn = str(row["filial_name"] or "")
                    ri = str(row["lesson_room_id"] or "")
                    if not fn and not ri:
                        fn, ri = _filial_from_raw_preview(row["raw_preview"] or "")
                    loc, is_online, _ = _resolve_lesson_location(row["group_name"] or "", fn, ri)
                    if loc and not is_online and loc not in codes:
                        codes.append(loc)
        return codes

    def get_teacher_lesson_contexts(self, mk_teacher_id: str, date_str: str) -> list[dict[str, Any]]:
        """Return per-lesson detail for a teacher on a specific date.

        Each entry: {lesson_id, lesson_date, lesson_time, group_name, raw_filial_name, location_code,
                     is_online, is_food_eligible, location_source, source}
        Checks teacher_lesson_control first; falls back to lesson_snapshots.
        Online (is_food_eligible=False) lessons are included for informational display.
        """
        if not mk_teacher_id or not date_str:
            return []
        tid = str(mk_teacher_id).strip()
        results: list[dict[str, Any]] = []
        with self._connect() as conn:
            ctrl_rows = conn.execute(
                "SELECT tlc.lesson_id, tlc.lesson_date, tlc.lesson_time, tlc.group_name, "
                "       tlc.filial_name, tlc.lesson_room_id, ls.raw_preview "
                "FROM teacher_lesson_control tlc "
                "LEFT JOIN lesson_snapshots ls ON ls.lesson_id = tlc.lesson_id "
                "WHERE tlc.mk_teacher_id=? AND tlc.lesson_date=? ORDER BY tlc.lesson_time",
                (tid, str(date_str)),
            ).fetchall()
            if ctrl_rows:
                for row in ctrl_rows:
                    fn = str(row["filial_name"] or "")
                    ri = str(row["lesson_room_id"] or "")
                    if not fn and not ri:
                        fn, ri = _filial_from_raw_preview(row["raw_preview"] or "")
                    loc, is_online, loc_src = _resolve_lesson_location(row["group_name"] or "", fn, ri)
                    results.append({
                        "lesson_id": row["lesson_id"],
                        "lesson_date": row["lesson_date"],
                        "lesson_time": row["lesson_time"] or "",
                        "group_name": row["group_name"] or "",
                        "raw_filial_name": fn,
                        "location_code": loc,
                        "is_online": is_online,
                        "is_food_eligible": not is_online and bool(loc),
                        "location_source": loc_src,
                        "source": "teacher_lesson_control",
                    })
            if not results:
                snap_rows = conn.execute(
                    "SELECT lesson_id, lesson_date, lesson_time, group_name, teacher_ids, "
                    "       filial_name, lesson_room_id, raw_preview "
                    "FROM lesson_snapshots "
                    "WHERE lesson_date=? AND teacher_ids IS NOT NULL AND teacher_ids != '' "
                    "ORDER BY lesson_time",
                    (str(date_str),),
                ).fetchall()
                for row in snap_rows:
                    ids = [x.strip() for x in str(row["teacher_ids"] or "").split(",") if x.strip()]
                    if tid not in ids:
                        continue
                    fn = str(row["filial_name"] or "")
                    ri = str(row["lesson_room_id"] or "")
                    if not fn and not ri:
                        fn, ri = _filial_from_raw_preview(row["raw_preview"] or "")
                    loc, is_online, loc_src = _resolve_lesson_location(row["group_name"] or "", fn, ri)
                    results.append({
                        "lesson_id": row["lesson_id"],
                        "lesson_date": row["lesson_date"],
                        "lesson_time": row["lesson_time"] or "",
                        "group_name": row["group_name"] or "",
                        "raw_filial_name": fn,
                        "location_code": loc,
                        "is_online": is_online,
                        "is_food_eligible": not is_online and bool(loc),
                        "location_source": loc_src,
                        "source": "lesson_snapshots",
                    })
        # Deduplicate by (location_code, lesson_time, group_name)
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for r in results:
            key = f"{r['location_code']}|{r['lesson_time']}|{r['group_name']}"
            if key not in seen:
                seen.add(key)
                unique.append(r)
        return unique

    def list_teachers_with_lesson_on_date(self, date_str: str) -> list[dict[str, Any]]:
        """Distinct mk_teacher_ids with lessons on date_str, joined to staff_users."""
        if not date_str:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    tlc.mk_teacher_id,
                    tlc.teacher_name,
                    GROUP_CONCAT(DISTINCT tlc.group_name) AS group_names,
                    su.user_id,
                    su.full_name,
                    su.username
                FROM teacher_lesson_control tlc
                LEFT JOIN staff_users su
                    ON su.mk_teacher_id = tlc.mk_teacher_id
                    AND (su.status IS NULL OR su.status != 'inactive')
                WHERE tlc.lesson_date = ?
                  AND tlc.mk_teacher_id IS NOT NULL
                  AND tlc.mk_teacher_id != ''
                GROUP BY tlc.mk_teacher_id
                ORDER BY tlc.teacher_name, tlc.mk_teacher_id
                """,
                (str(date_str),),
            ).fetchall()
        return [dict(r) for r in rows]

    # Latin→Cyrillic word aliases for teacher name matching.
    # Keys are lowercased Latin words; values are the Cyrillic equivalents (also lowercased).
    # Extend this dict when new teacher names with Latin spellings need to be supported.
    _TEACHER_NAME_ALIASES: dict[str, str] = {
        # Common first names
        "alexandr": "александр", "alexander": "александр", "aleksander": "александр",
        "aleksandr": "александр", "alex": "александр",
        "alexei": "алексей", "alexey": "алексей", "aleksei": "алексей", "aleksey": "алексей",
        "anna": "анна", "maria": "мария", "mary": "мария",
        "natalia": "наталья", "natasha": "наташа", "natalya": "наталья", "nataliya": "наталья",
        "olga": "ольга", "elena": "елена", "helen": "елена", "yelena": "елена",
        "irina": "ирина", "iryna": "ирина", "oksana": "оксана",
        "tatiana": "татьяна", "tatyana": "татьяна", "tanya": "татьяна",
        "ekaterina": "екатерина", "katerina": "екатерина", "katya": "катя",
        "svetlana": "светлана", "sveta": "светлана",
        "victoria": "виктория", "vika": "виктория",
        "mikhail": "михаил", "michael": "михаил", "misha": "михаил",
        "nikita": "никита", "nikolai": "николай", "nikolay": "николай",
        "pavel": "павел", "paul": "павел", "pasha": "павел",
        "sergei": "сергей", "sergey": "сергей", "serhiy": "сергей",
        "dmitri": "дмитрий", "dmitry": "дмитрий", "dmitriy": "дмитрий",
        "andrei": "андрей", "andrey": "андрей", "andriy": "андрей",
        "evgeni": "евгений", "evgeny": "евгений", "evgeniy": "евгений",
        "roman": "роман", "viktor": "виктор", "maxim": "максим", "vadim": "вадим",
        "igor": "игорь", "artem": "артём", "artyom": "артём",
        "stanislav": "станислав", "vladislav": "владислав", "vlad": "владислав",
        "yulia": "юлия", "julia": "юлия", "yuliya": "юлия",
        "daria": "дарья", "darya": "дарья", "dasha": "дарья",
        "polina": "полина", "kristina": "кристина", "vera": "вера",
        "liudmila": "людмила", "ludmila": "людмила", "lyudmila": "людмила",
        "valentina": "валентина", "galina": "галина", "tamara": "тамара",
        "artur": "артур", "arthur": "артур", "valentin": "валентин",
        "vadym": "вадим", "yuriy": "юрий", "yuri": "юрий", "yury": "юрий",
        "boris": "борис", "arkady": "аркадий", "gennady": "геннадий",
        "konstantin": "константин", "vitaly": "виталий", "anatoly": "анатолий",
        # Common last names (extend as needed)
        "krents": "кренц", "krenc": "кренц", "krentz": "кренц",
        "skroba": "скроба",
        "ivanov": "иванов", "petrov": "петров", "sidorov": "сидоров",
        "kovalev": "ковалев", "kovalyov": "ковалёв",
        "smirnov": "смирнов", "popov": "попов", "sokolov": "соколов",
        "novikov": "новиков", "morozov": "морозов", "volkov": "волков",
    }

    @classmethod
    def _translit_name(cls, text: str) -> str:
        """Apply word-level Latin→Cyrillic alias mapping to a lowercased name string.
        Returns a version of `text` where known Latin name words are replaced with Cyrillic.
        Unknown words are left as-is (may remain Latin if no alias found)."""
        import re as _re
        words = _re.sub(r"\s+", " ", str(text or "").lower().strip()).split()
        return " ".join(cls._TEACHER_NAME_ALIASES.get(w, w) for w in words)

    def find_teacher_candidates_by_name(self, full_name: str) -> list[dict[str, Any]]:
        """Return list of {mk_teacher_id, teacher_name} where teacher_name matches full_name (normalized).
        Tries both "Имя Фамилия" and "Фамилия Имя" orderings. Searches teacher_lesson_control AND
        lesson_snapshots (background MoyKlass sync). Safe: skips ambiguous multi-teacher rows."""
        import re as _re

        def _norm(s: str) -> str:
            s = str(s or "").lower().strip().replace("ё", "е")
            return _re.sub(r"\s+", " ", s)

        needle = _norm(full_name)
        if not needle:
            return []
        # Also try transliterating Latin words → Cyrillic so "Alexandr Krents" matches "Александр Кренц"
        needle_translit = _norm(self._translit_name(needle))
        # Build candidate needles: direct + translit + reversed variants
        needle_parts = needle.split()
        needle_rev = " ".join(reversed(needle_parts)) if len(needle_parts) == 2 else ""
        needle_trans_parts = needle_translit.split()
        needle_trans_rev = " ".join(reversed(needle_trans_parts)) if len(needle_trans_parts) == 2 else ""

        def _matches(norm_name: str) -> bool:
            return (
                norm_name == needle
                or (needle_rev and norm_name == needle_rev)
                or (needle_translit and norm_name == needle_translit)
                or (needle_trans_rev and norm_name == needle_trans_rev)
            )

        matches: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        with self._connect() as conn:
            # Source 1: teacher_lesson_control (confirmed lessons, includes teacher_name per lesson)
            ctrl_rows = conn.execute(
                "SELECT DISTINCT mk_teacher_id, teacher_name FROM teacher_lesson_control "
                "WHERE mk_teacher_id IS NOT NULL AND mk_teacher_id != '' "
                "AND teacher_name IS NOT NULL AND teacher_name != ''",
            ).fetchall()
            for row in ctrl_rows:
                tid = str(row["mk_teacher_id"]).strip()
                name = str(row["teacher_name"]).strip()
                if tid in seen_ids:
                    continue
                if _matches(_norm(name)):
                    matches.append({"mk_teacher_id": tid, "teacher_name": name})
                    seen_ids.add(tid)

            # Source 2: lesson_snapshots (MoyKlass background sync, more up-to-date)
            # teacher_names is a single string per snapshot row; teacher_ids is comma-separated.
            # Only use rows where exactly 1 teacher_id is present to avoid ambiguous matches.
            snap_rows = conn.execute(
                "SELECT DISTINCT teacher_names, teacher_ids FROM lesson_snapshots "
                "WHERE teacher_names IS NOT NULL AND teacher_names != '' "
                "AND teacher_ids IS NOT NULL AND teacher_ids != '' "
                "AND teacher_names NOT LIKE 'Преподаватель ID%'",
            ).fetchall()
            for row in snap_rows:
                name = str(row["teacher_names"]).strip()
                if not _matches(_norm(name)):
                    continue
                ids = [x.strip() for x in str(row["teacher_ids"] or "").split(",") if x.strip()]
                if len(ids) == 1:
                    tid = ids[0]
                    if tid not in seen_ids:
                        matches.append({"mk_teacher_id": tid, "teacher_name": name})
                        seen_ids.add(tid)
                # If multiple teacher_ids per lesson and name matches: skip (ambiguous)

        return matches

    def mark_teacher_preparation(self, lesson_id: str | int, user_id: int | None, status: str, comment: str = "", **lesson_fields: Any) -> dict[str, Any]:
        status = (status or "not_started").strip()
        fields: dict[str, Any] = dict(lesson_fields)
        fields.update({
            "teacher_user_id": int(user_id) if user_id else lesson_fields.get("teacher_user_id"),
            "preparation_status": status,
            "preparation_comment": (comment or "")[:4000],
            "preparation_confirmed_at": now_iso() if status in {"ready", "needs_help", "not_ready"} else None,
        })
        return self.upsert_teacher_lesson_control(lesson_id, **fields)

    def mark_teacher_lesson_step(self, lesson_id: str | int, step: str, status: str, user_id: int | None = None, **lesson_fields: Any) -> dict[str, Any]:
        step_map = {
            "myclass": "myclass_status",
            "parent_report": "parent_report_status",
            "works": "works_status",
            "classroom": "classroom_status",
            "lesson": "lesson_status",
            "prep_material": "prep_material_status",
            "prep_video": "prep_video_status",
            "prep_practice": "prep_practice_status",
            "prep_result": "prep_result_status",
        }
        column = step_map.get((step or "").strip())
        if not column:
            return self.get_teacher_lesson_control(lesson_id) or {}
        fields: dict[str, Any] = dict(lesson_fields)
        fields[column] = status
        if user_id:
            fields.setdefault("teacher_user_id", int(user_id))
        return self.upsert_teacher_lesson_control(lesson_id, **fields)

    def set_teacher_lesson_comment(self, lesson_id: str | int, user_id: int | None, comment: str, **lesson_fields: Any) -> dict[str, Any]:
        fields: dict[str, Any] = dict(lesson_fields)
        fields.update({
            "teacher_user_id": int(user_id) if user_id else lesson_fields.get("teacher_user_id"),
            "lesson_comment": (comment or "")[:4000],
        })
        return self.upsert_teacher_lesson_control(lesson_id, **fields)

    def mark_teacher_lesson_problem(self, lesson_id: str | int, user_id: int | None, comment: str, **lesson_fields: Any) -> dict[str, Any]:
        fields: dict[str, Any] = dict(lesson_fields)
        fields.update({
            "teacher_user_id": int(user_id) if user_id else lesson_fields.get("teacher_user_id"),
            "problem_status": "problem",
            "problem_comment": (comment or "")[:4000],
            "lesson_status": "problem",
        })
        return self.upsert_teacher_lesson_control(lesson_id, **fields)

    def close_teacher_lesson(self, lesson_id: str | int, user_id: int | None, **lesson_fields: Any) -> dict[str, Any]:
        fields: dict[str, Any] = dict(lesson_fields)
        fields.update({
            "teacher_user_id": int(user_id) if user_id else lesson_fields.get("teacher_user_id"),
            "lesson_status": "closed",
            "closed_at": now_iso(),
            "closed_by": int(user_id) if user_id else None,
        })
        return self.upsert_teacher_lesson_control(lesson_id, **fields)


    # --- Teacher preparation result files ---

    def add_teacher_prep_result_file(
        self,
        lesson_id: str | int,
        teacher_user_id: int | None,
        file_name: str,
        stored_path: str,
        mime_type: str = "",
        size_bytes: int = 0,
        **lesson_fields: Any,
    ) -> dict[str, Any]:
        """Store a teacher practical-work file and mark the lesson as waiting for senior review."""
        lesson_id = str(lesson_id or "").strip()
        file_name = (file_name or "result.bin").strip()[:240]
        stored_path = (stored_path or "").strip()
        if not lesson_id or not stored_path:
            return {}
        now = now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO teacher_prep_results(created_at, updated_at, lesson_id, teacher_user_id, file_name, stored_path, mime_type, size_bytes, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'submitted')
                """,
                (now, now, lesson_id, int(teacher_user_id) if teacher_user_id else None, file_name, stored_path, mime_type or "", int(size_bytes or 0)),
            )
            file_id = int(cur.lastrowid)
        fields: dict[str, Any] = dict(lesson_fields)
        fields.update({
            "teacher_user_id": int(teacher_user_id) if teacher_user_id else lesson_fields.get("teacher_user_id"),
            "prep_result_status": "submitted",
            "prep_result_file_id": str(file_id),
        })
        self.upsert_teacher_lesson_control(lesson_id, **fields)
        return self.get_teacher_prep_result_file(file_id) or {}

    def get_teacher_prep_result_file(self, file_id: int | str) -> Optional[dict[str, Any]]:
        try:
            fid = int(file_id)
        except Exception:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM teacher_prep_results WHERE id=?", (fid,)).fetchone()
        return dict(row) if row else None

    def list_teacher_prep_result_files(self, lesson_id: str | int | None = None, teacher_user_id: int | None = None, status: str = "all", limit: int = 100) -> list[dict[str, Any]]:
        where = []
        params: list[Any] = []
        if lesson_id:
            where.append("r.lesson_id=?")
            params.append(str(lesson_id))
        if teacher_user_id:
            where.append("r.teacher_user_id=?")
            params.append(int(teacher_user_id))
        if status and status != "all":
            where.append("r.status=?")
            params.append(str(status))
        sql = """
            SELECT r.*, s.full_name, s.username, c.group_name, c.lesson_date, c.lesson_time, c.lesson_topic, c.teacher_name
            FROM teacher_prep_results r
            LEFT JOIN staff_users s ON s.user_id = r.teacher_user_id
            LEFT JOIN teacher_lesson_control c ON c.lesson_id = r.lesson_id
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY r.id DESC LIMIT ?"
        params.append(int(limit or 100))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def review_teacher_prep_result_file(self, file_id: int | str, reviewer_user_id: int | None, status: str, comment: str = "") -> dict[str, Any]:
        """Approve/reject submitted practical work and update lesson preparation status."""
        try:
            fid = int(file_id)
        except Exception:
            return {}
        status = (status or "").strip().lower()
        if status not in {"approved", "rejected"}:
            return {}
        now = now_iso()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM teacher_prep_results WHERE id=?", (fid,)).fetchone()
            if not row:
                return {}
            conn.execute(
                """
                UPDATE teacher_prep_results
                SET status=?, reviewer_user_id=?, reviewer_comment=?, reviewed_at=?, updated_at=?
                WHERE id=?
                """,
                (status, int(reviewer_user_id) if reviewer_user_id else None, (comment or "")[:2000], now, now, fid),
            )
            lesson_id = str(row["lesson_id"] or "")
        control = self.get_teacher_lesson_control(lesson_id) or {}
        if status == "approved":
            fields: dict[str, Any] = {
                "prep_result_status": "done",
                "prep_result_file_id": str(fid),
                "preparation_comment": (comment or "Результат практической работы подтверждён старшим преподавателем")[:4000],
            }
            # If the other preparation steps are already done, allow the lesson.
            effective = dict(control)
            effective.update(fields)
            if all(str(effective.get(k) or "") == "done" for k in ("prep_material_status", "prep_video_status", "prep_practice_status", "prep_result_status")):
                fields.update({"preparation_status": "ready", "preparation_confirmed_at": now})
            self.upsert_teacher_lesson_control(lesson_id, **fields)
        else:
            self.upsert_teacher_lesson_control(
                lesson_id,
                prep_result_status="rejected",
                preparation_status="needs_help",
                preparation_comment=(comment or "Результат практической работы отклонён старшим преподавателем")[:4000],
            )
        return self.get_teacher_prep_result_file(fid) or {}


    # --- Intern onboarding track ---

    def add_intern_observation(
        self,
        intern_user_id: int,
        mk_lesson_id: str | int = "",
        lesson_title: str = "",
        lesson_date: str = "",
        lesson_time: str = "",
        location: str = "",
    ) -> dict[str, Any]:
        """Sign an intern up to observe a trial lesson. De-duplicates by (intern, mk_lesson_id)."""
        if not intern_user_id:
            return {}
        mk_lesson_id = str(mk_lesson_id or "").strip()
        now = now_iso()
        with self._connect() as conn:
            if mk_lesson_id:
                existing = conn.execute(
                    "SELECT * FROM intern_observations WHERE intern_user_id=? AND mk_lesson_id=?",
                    (int(intern_user_id), mk_lesson_id),
                ).fetchone()
                if existing:
                    return dict(existing)
            cur = conn.execute(
                """
                INSERT INTO intern_observations(created_at, updated_at, intern_user_id, mk_lesson_id, lesson_title, lesson_date, lesson_time, location, status, comment)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'signed_up', '')
                """,
                (now, now, int(intern_user_id), mk_lesson_id, (lesson_title or "")[:300], (lesson_date or "")[:32], (lesson_time or "")[:16], (location or "")[:120]),
            )
            obs_id = int(cur.lastrowid)
        return self.get_intern_observation(obs_id) or {}

    def get_intern_observation(self, obs_id: int | str) -> Optional[dict[str, Any]]:
        try:
            oid = int(obs_id)
        except Exception:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM intern_observations WHERE id=?", (oid,)).fetchone()
        return dict(row) if row else None

    def list_intern_observations(self, intern_user_id: int, status: str = "all", limit: int = 100) -> list[dict[str, Any]]:
        where = ["intern_user_id=?"]
        params: list[Any] = [int(intern_user_id)]
        if status and status != "all":
            where.append("status=?")
            params.append(str(status))
        sql = "SELECT * FROM intern_observations WHERE " + " AND ".join(where) + " ORDER BY id DESC LIMIT ?"
        params.append(int(limit or 100))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def set_intern_observation_comment(self, obs_id: int | str, comment: str) -> dict[str, Any]:
        """Save the intern's post-observation comment and mark the observation as completed."""
        try:
            oid = int(obs_id)
        except Exception:
            return {}
        comment = (comment or "").strip()
        if not comment:
            return {}
        now = now_iso()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM intern_observations WHERE id=?", (oid,)).fetchone()
            if not row:
                return {}
            conn.execute(
                "UPDATE intern_observations SET comment=?, status='observed', updated_at=? WHERE id=?",
                (comment[:4000], now, oid),
            )
        return self.get_intern_observation(oid) or {}

    def count_intern_completed_observations(self, intern_user_id: int) -> int:
        """A completed observation = status 'observed' with a non-empty comment."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM intern_observations WHERE intern_user_id=? AND status='observed' AND TRIM(comment) <> ''",
                (int(intern_user_id),),
            ).fetchone()
        return int(row["n"]) if row else 0

    def add_intern_work(
        self,
        intern_user_id: int,
        file_name: str,
        stored_path: str,
        mime_type: str = "",
        size_bytes: int = 0,
    ) -> dict[str, Any]:
        """Store an intern's trial-work submission, awaiting senior review."""
        file_name = (file_name or "work.bin").strip()[:240]
        stored_path = (stored_path or "").strip()
        if not intern_user_id or not stored_path:
            return {}
        now = now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO intern_works(created_at, updated_at, intern_user_id, file_name, stored_path, mime_type, size_bytes, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'submitted')
                """,
                (now, now, int(intern_user_id), file_name, stored_path, mime_type or "", int(size_bytes or 0)),
            )
            work_id = int(cur.lastrowid)
        return self.get_intern_work(work_id) or {}

    def get_intern_work(self, work_id: int | str) -> Optional[dict[str, Any]]:
        try:
            wid = int(work_id)
        except Exception:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM intern_works WHERE id=?", (wid,)).fetchone()
        return dict(row) if row else None

    def list_intern_works(self, intern_user_id: int | None = None, status: str = "all", limit: int = 100) -> list[dict[str, Any]]:
        where = []
        params: list[Any] = []
        if intern_user_id:
            where.append("w.intern_user_id=?")
            params.append(int(intern_user_id))
        if status and status != "all":
            where.append("w.status=?")
            params.append(str(status))
        sql = """
            SELECT w.*, s.full_name, s.username
            FROM intern_works w
            LEFT JOIN staff_users s ON s.user_id = w.intern_user_id
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY w.id DESC LIMIT ?"
        params.append(int(limit or 100))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def review_intern_work(self, work_id: int | str, reviewer_user_id: int | None, status: str, comment: str = "") -> dict[str, Any]:
        """Accept or reject an intern's trial work. Rejection requires a comment (enforced by caller)."""
        try:
            wid = int(work_id)
        except Exception:
            return {}
        status = (status or "").strip().lower()
        if status not in {"accepted", "rejected"}:
            return {}
        now = now_iso()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM intern_works WHERE id=?", (wid,)).fetchone()
            if not row:
                return {}
            conn.execute(
                """
                UPDATE intern_works
                SET status=?, reviewer_user_id=?, reviewer_comment=?, reviewed_at=?, updated_at=?
                WHERE id=?
                """,
                (status, int(reviewer_user_id) if reviewer_user_id else None, (comment or "")[:2000], now, now, wid),
            )
        return self.get_intern_work(wid) or {}

    def add_intern_demo_booking(
        self,
        intern_user_id: int,
        supervisor_user_id: int | None = None,
        slot_ref: str = "",
        demo_date: str = "",
        demo_time: str = "",
        location: str = "",
    ) -> dict[str, Any]:
        """Intern books a demo trial slot with a senior teacher / admin."""
        if not intern_user_id:
            return {}
        now = now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO intern_demo_bookings(created_at, updated_at, intern_user_id, supervisor_user_id, slot_ref, demo_date, demo_time, location, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'requested')
                """,
                (now, now, int(intern_user_id), int(supervisor_user_id) if supervisor_user_id else None, (slot_ref or "")[:64], (demo_date or "")[:32], (demo_time or "")[:16], (location or "")[:120]),
            )
            booking_id = int(cur.lastrowid)
        return self.get_intern_demo_booking(booking_id) or {}

    def get_intern_demo_booking(self, booking_id: int | str) -> Optional[dict[str, Any]]:
        try:
            bid = int(booking_id)
        except Exception:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM intern_demo_bookings WHERE id=?", (bid,)).fetchone()
        return dict(row) if row else None

    def list_intern_demo_bookings(self, intern_user_id: int | None = None, status: str = "all", limit: int = 100) -> list[dict[str, Any]]:
        where = []
        params: list[Any] = []
        if intern_user_id:
            where.append("b.intern_user_id=?")
            params.append(int(intern_user_id))
        if status and status != "all":
            where.append("b.status=?")
            params.append(str(status))
        sql = """
            SELECT b.*, s.full_name AS intern_name, sup.full_name AS supervisor_name
            FROM intern_demo_bookings b
            LEFT JOIN staff_users s ON s.user_id = b.intern_user_id
            LEFT JOIN staff_users sup ON sup.user_id = b.supervisor_user_id
        """
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY b.id DESC LIMIT ?"
        params.append(int(limit or 100))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def review_intern_demo_booking(self, booking_id: int | str, reviewer_user_id: int | None, status: str, comment: str = "") -> dict[str, Any]:
        """Senior verdict on a demo booking / the conducted demo.

        Allowed statuses:
          approved  - booking confirmed, intern may conduct the demo
          declined  - booking rejected (requires comment, enforced by caller)
          conducted - demo has been conducted, awaiting final verdict
          passed    - demo accepted -> intern is allowed (caller promotes status)
          failed    - demo not accepted, intern repeats (requires comment)
        """
        try:
            bid = int(booking_id)
        except Exception:
            return {}
        status = (status or "").strip().lower()
        if status not in {"approved", "declined", "conducted", "passed", "failed"}:
            return {}
        now = now_iso()
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM intern_demo_bookings WHERE id=?", (bid,)).fetchone()
            if not row:
                return {}
            conn.execute(
                """
                UPDATE intern_demo_bookings
                SET status=?, reviewer_user_id=?, reviewer_comment=?, reviewed_at=?, updated_at=?
                WHERE id=?
                """,
                (status, int(reviewer_user_id) if reviewer_user_id else None, (comment or "")[:2000], now, now, bid),
            )
        return self.get_intern_demo_booking(bid) or {}

    def save_intern_demo_feedback(
        self,
        booking_id: int | str,
        how: str = "",
        plus: str = "",
        minus: str = "",
        improve: str = "",
        comment: str = "",
    ) -> dict[str, Any]:
        """Save trainee self-assessment on a demo booking."""
        try:
            bid = int(booking_id)
        except Exception:
            return {}
        import json as _json
        fb_json = _json.dumps({
            "how": (how or "").strip()[:2000],
            "plus": (plus or "").strip()[:2000],
            "minus": (minus or "").strip()[:2000],
            "improve": (improve or "").strip()[:2000],
            "comment": (comment or "").strip()[:2000],
        }, ensure_ascii=False)
        now = now_iso()
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM intern_demo_bookings WHERE id=?", (bid,)).fetchone()
            if not row:
                return {}
            conn.execute(
                "UPDATE intern_demo_bookings SET trainee_feedback_json=?, trainee_feedback_at=?, updated_at=? WHERE id=?",
                (fb_json, now, now, bid),
            )
        return self.get_intern_demo_booking(bid) or {}

    def list_all_intern_user_ids(self) -> set[int]:
        """Return all user_ids that have any intern activity (observations / works / bookings)."""
        with self._connect() as conn:
            obs  = {int(r[0]) for r in conn.execute("SELECT DISTINCT intern_user_id FROM intern_observations").fetchall() if r[0]}
            wrk  = {int(r[0]) for r in conn.execute("SELECT DISTINCT intern_user_id FROM intern_works").fetchall() if r[0]}
            demo = {int(r[0]) for r in conn.execute("SELECT DISTINCT intern_user_id FROM intern_demo_bookings").fetchall() if r[0]}
        return obs | wrk | demo

    def get_intern_track(self, intern_user_id: int) -> dict[str, Any]:
        """Compute the intern's onboarding progress from existing event data.

        current_step values:
          1 = observations (need 2 completed)
          2 = submit prep work (or resubmit after rejection)
          3 = waiting for work review
          4 = book demo lesson
          5 = demo outcome pending / done
        admitted=True means demo was passed.
        """
        uid = int(intern_user_id)
        observations = self.list_intern_observations(uid, limit=50)
        obs_count = self.count_intern_completed_observations(uid)
        obs_needed = 2

        works = self.list_intern_works(uid, limit=5)
        latest_work = works[0] if works else None

        bookings = self.list_intern_demo_bookings(uid, limit=5)
        latest_booking = bookings[0] if bookings else None
        if latest_booking:
            latest_booking["feedback_submitted"] = bool(latest_booking.get("trainee_feedback_json"))

        work_status = str(latest_work.get("status") or "") if latest_work else ""
        booking_status = str(latest_booking.get("status") or "") if latest_booking else ""

        if obs_count < obs_needed:
            current_step = 1
        elif not latest_work or work_status == "rejected":
            current_step = 2
        elif work_status == "submitted":
            current_step = 3
        elif work_status == "accepted" and not latest_booking:
            current_step = 4
        else:
            current_step = 5

        admitted = booking_status == "passed"

        return {
            "observations": observations,
            "obs_count": obs_count,
            "obs_needed": obs_needed,
            "latest_work": latest_work,
            "works": [dict(w) for w in works],
            "latest_booking": latest_booking,
            "current_step": current_step,
            "admitted": admitted,
        }


    def intern_test_reset(self, intern_user_id: int) -> None:
        """Delete all onboarding records for an intern (test-only tool)."""
        uid = int(intern_user_id)
        with self._connect() as conn:
            conn.execute("DELETE FROM intern_observations WHERE intern_user_id=?", (uid,))
            conn.execute("DELETE FROM intern_works WHERE intern_user_id=?", (uid,))
            conn.execute("DELETE FROM intern_demo_bookings WHERE intern_user_id=?", (uid,))

    def intern_test_set_stage(
        self, intern_user_id: int, stage: str, reviewer_user_id: int | None = None
    ) -> None:
        """Create minimal records so get_intern_track() returns the target stage (test-only tool)."""
        VALID = {
            "start", "one_observation", "two_observations",
            "work_pending", "work_accepted", "work_rejected",
            "demo_booked", "demo_rejected", "admitted",
        }
        stage = (stage or "").strip().lower()
        if stage not in VALID:
            raise ValueError(f"Unknown test stage: {stage!r}")
        uid = int(intern_user_id)
        self.intern_test_reset(uid)
        if stage == "start":
            return
        now = now_iso()
        obs_count = 1 if stage == "one_observation" else 2
        with self._connect() as conn:
            for i in range(1, obs_count + 1):
                conn.execute(
                    "INSERT INTO intern_observations(created_at,updated_at,intern_user_id,mk_lesson_id,lesson_title,lesson_date,lesson_time,location,status,comment) VALUES (?,?,?,?,?,?,?,?,'observed',?)",
                    (now, now, uid, f"__test__{i}", f"Тест: занятие {i}", "2025-01-01", "10:00", "Тест", f"Тест: отчёт {i}"),
                )
        if stage in {"one_observation", "two_observations"}:
            return
        # Work row
        if stage == "work_pending":
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO intern_works(created_at,updated_at,intern_user_id,file_name,stored_path,mime_type,size_bytes,status) VALUES (?,?,?,?,?,?,?,'submitted')",
                    (now, now, uid, "test_work.pdf", "__test__", "application/pdf", 0),
                )
        elif stage == "work_rejected":
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO intern_works(created_at,updated_at,intern_user_id,file_name,stored_path,mime_type,size_bytes,status,reviewer_user_id,reviewer_comment,reviewed_at) VALUES (?,?,?,?,?,?,?,'rejected',?,'Тест: работа содержит ошибки. Исправьте и загрузите снова.',?)",
                    (now, now, uid, "test_work.pdf", "__test__", "application/pdf", 0, reviewer_user_id, now),
                )
        else:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO intern_works(created_at,updated_at,intern_user_id,file_name,stored_path,mime_type,size_bytes,status,reviewer_user_id,reviewer_comment,reviewed_at) VALUES (?,?,?,?,?,?,?,'accepted',?,'Тест: работа принята.',?)",
                    (now, now, uid, "test_work.pdf", "__test__", "application/pdf", 0, reviewer_user_id, now),
                )
        if stage in {"work_pending", "work_accepted", "work_rejected"}:
            return
        # Demo booking row
        if stage == "demo_booked":
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO intern_demo_bookings(created_at,updated_at,intern_user_id,supervisor_user_id,slot_ref,demo_date,demo_time,location,status) VALUES (?,?,?,?,?,?,?,?,'requested')",
                    (now, now, uid, reviewer_user_id, "Тест", "2025-02-01", "12:00", "Тест"),
                )
        elif stage == "demo_rejected":
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO intern_demo_bookings(created_at,updated_at,intern_user_id,supervisor_user_id,slot_ref,demo_date,demo_time,location,status,reviewer_user_id,reviewer_comment,reviewed_at) VALUES (?,?,?,?,?,?,?,?,'failed',?,'Тест: пробное занятие не принято.',?)",
                    (now, now, uid, reviewer_user_id, "Тест", "2025-02-01", "12:00", "Тест", reviewer_user_id, now),
                )
        elif stage == "admitted":
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO intern_demo_bookings(created_at,updated_at,intern_user_id,supervisor_user_id,slot_ref,demo_date,demo_time,location,status,reviewer_user_id,reviewer_comment,reviewed_at) VALUES (?,?,?,?,?,?,?,?,'passed',?,'Тест: стажёр допущен.',?)",
                    (now, now, uid, reviewer_user_id, "Тест", "2025-02-01", "12:00", "Тест", reviewer_user_id, now),
                )

    # --- MoyKlass write audit ---

    def add_mk_audit(
        self,
        user_id: Optional[int],
        action: str,
        object_type: str,
        object_id: str,
        method: str,
        path: str,
        payload: str,
        status: int,
        ok: bool,
        response: str = "",
        error: str = "",
    ) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO mk_audit(created_at, user_id, action, object_type, object_id, method, path, payload, status, ok, response, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now_iso(), user_id, action or "", object_type or "", object_id or "",
                    method or "", path or "", (payload or "")[:6000], int(status or 0),
                    1 if ok else 0, (response or "")[:6000], (error or "")[:2000],
                ),
            )
            return int(cur.lastrowid)

    def list_mk_audit(self, limit: int = 30) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM mk_audit ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


    # --- Teacher OPS Foundation v4 ---

    VALID_TEACHER_STATUSES = {"trainee", "trial_allowed", "regular_allowed", "active", "paused", "substitute"}

    def upsert_teacher_profile(
        self,
        user_id: int,
        teacher_status: str = "active",
        profile_type: str = "current",
        location: str = "",
        can_trial: bool | int | None = None,
        can_regular: bool | int | None = None,
        can_substitute: bool | int | None = None,
        supervisor_user_id: int | None = None,
        notes: str = "",
    ) -> dict[str, Any]:
        teacher_status = (teacher_status or "active").strip().lower()
        if teacher_status not in self.VALID_TEACHER_STATUSES:
            teacher_status = "active"
        profile_type = (profile_type or "current").strip().lower()
        now = now_iso()
        def _flag(value: bool | int | str) -> int:
            if isinstance(value, str):
                return 1 if value.strip().lower() in {"1", "true", "yes", "да", "y"} else 0
            return 1 if bool(value) else 0

        fields: dict[str, Any] = {
            "teacher_status": teacher_status,
            "profile_type": profile_type,
            "location": location or "",
            "notes": notes or "",
        }
        if can_trial is not None:
            fields["can_trial"] = _flag(can_trial)
        if can_regular is not None:
            fields["can_regular"] = _flag(can_regular)
        if can_substitute is not None:
            fields["can_substitute"] = _flag(can_substitute)
        if supervisor_user_id is not None:
            fields["supervisor_user_id"] = int(supervisor_user_id)
        with self._connect() as conn:
            existing = conn.execute("SELECT * FROM teacher_profiles WHERE user_id=?", (int(user_id),)).fetchone()
            if existing:
                assignments = ", ".join([f"{k}=?" for k in fields.keys()])
                params = list(fields.values()) + [now, int(user_id)]
                conn.execute(f"UPDATE teacher_profiles SET {assignments}, updated_at=? WHERE user_id=?", params)
            else:
                base = {"user_id": int(user_id), "created_at": now, "updated_at": now}
                base.update(fields)
                # Default permissions by status/type.
                base.setdefault("can_trial", 1 if teacher_status in {"trial_allowed", "regular_allowed", "active"} else 0)
                base.setdefault("can_regular", 1 if teacher_status in {"regular_allowed", "active"} else 0)
                base.setdefault("can_substitute", 1 if teacher_status in {"substitute"} else 0)
                cols = list(base.keys())
                conn.execute(
                    f"INSERT INTO teacher_profiles({', '.join(cols)}) VALUES ({', '.join(['?'] * len(cols))})",
                    [base[c] for c in cols],
                )
            row = conn.execute("SELECT * FROM teacher_profiles WHERE user_id=?", (int(user_id),)).fetchone()
        return dict(row) if row else {}

    def get_teacher_profile(self, user_id: int | None) -> Optional[dict[str, Any]]:
        if not user_id:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM teacher_profiles WHERE user_id=?", (int(user_id),)).fetchone()
        return dict(row) if row else None

    def list_teacher_profiles(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT p.*, s.full_name, s.username, s.role, s.mk_teacher_id, s.mk_teacher_name
                FROM teacher_profiles p
                LEFT JOIN staff_users s ON s.user_id = p.user_id
                ORDER BY p.teacher_status, COALESCE(s.full_name, s.username, ''), p.user_id
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [dict(r) for r in rows]

    def ensure_teacher_profiles_from_staff(self) -> int:
        """Create active teacher profiles for existing teacher/methodist staff that do not have one yet."""
        now = now_iso()
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM staff_users WHERE role IN ('teacher','methodist') AND status='active'").fetchall()
            created = 0
            for row in rows:
                exists = conn.execute("SELECT 1 FROM teacher_profiles WHERE user_id=?", (row["user_id"],)).fetchone()
                if exists:
                    continue
                conn.execute(
                    """
                    INSERT INTO teacher_profiles(user_id, created_at, updated_at, teacher_status, profile_type, can_trial, can_regular, can_substitute, notes)
                    VALUES (?, ?, ?, 'active', 'current', 1, 1, 0, '')
                    """,
                    (int(row["user_id"]), now, now),
                )
                created += 1
            return created

    def upsert_lesson_snapshot(self, lesson_id: str | int, snapshot: dict[str, Any]) -> tuple[str, dict[str, Any] | None, dict[str, Any]]:
        """Save a lesson schedule snapshot.

        Returns (event, previous, current): event is new, changed, or unchanged.
        """
        lesson_id = str(lesson_id or "").strip()
        if not lesson_id:
            return "invalid", None, {}
        now = now_iso()
        current = {
            "lesson_id": lesson_id,
            "lesson_date": str(snapshot.get("lesson_date") or "")[:10],
            "lesson_time": str(snapshot.get("lesson_time") or ""),
            "group_name": str(snapshot.get("group_name") or ""),
            "lesson_topic": str(snapshot.get("lesson_topic") or ""),
            "teacher_ids": str(snapshot.get("teacher_ids") or ""),
            "teacher_names": str(snapshot.get("teacher_names") or ""),
            "fingerprint": str(snapshot.get("fingerprint") or ""),
            "raw_preview": str(snapshot.get("raw_preview") or "")[:4000],
            "filial_name": str(snapshot.get("filial_name") or ""),
            "lesson_room_id": str(snapshot.get("lesson_room_id") or ""),
        }
        with self._connect() as conn:
            old_row = conn.execute("SELECT * FROM lesson_snapshots WHERE lesson_id=?", (lesson_id,)).fetchone()
            if not old_row:
                conn.execute(
                    """
                    INSERT INTO lesson_snapshots(lesson_id, created_at, updated_at, last_seen_at, lesson_date, lesson_time, group_name, lesson_topic, teacher_ids, teacher_names, fingerprint, raw_preview, filial_name, lesson_room_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (lesson_id, now, now, now, current["lesson_date"], current["lesson_time"], current["group_name"], current["lesson_topic"], current["teacher_ids"], current["teacher_names"], current["fingerprint"], current["raw_preview"], current["filial_name"], current["lesson_room_id"]),
                )
                return "new", None, current
            previous = dict(old_row)
            event = "changed" if str(previous.get("fingerprint") or "") != current["fingerprint"] else "unchanged"
            conn.execute(
                """
                UPDATE lesson_snapshots
                SET updated_at=?, last_seen_at=?, lesson_date=?, lesson_time=?, group_name=?, lesson_topic=?, teacher_ids=?, teacher_names=?, fingerprint=?, raw_preview=?, filial_name=?, lesson_room_id=?
                WHERE lesson_id=?
                """,
                (now if event == "changed" else previous.get("updated_at") or now, now, current["lesson_date"], current["lesson_time"], current["group_name"], current["lesson_topic"], current["teacher_ids"], current["teacher_names"], current["fingerprint"], current["raw_preview"], current["filial_name"], current["lesson_room_id"], lesson_id),
            )
        return event, previous, current

    def get_lesson_snapshot(self, lesson_id: str | int) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM lesson_snapshots WHERE lesson_id=?", (str(lesson_id),)).fetchone()
        return dict(row) if row else None

    def list_lesson_snapshots(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM lesson_snapshots ORDER BY lesson_date DESC, lesson_time DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]

    def upsert_teacher_task(
        self,
        task_key: str,
        task_type: str,
        title: str,
        text: str = "",
        user_id: int | None = None,
        mk_teacher_id: str = "",
        lesson_id: str = "",
        due_at: str = "",
        priority: str = "normal",
        source: str = "agent",
    ) -> dict[str, Any]:
        task_key = (task_key or "").strip()
        if not task_key:
            task_key = f"{task_type}:{user_id or ''}:{lesson_id or ''}:{title[:40]}"
        now = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO teacher_tasks(task_key, created_at, updated_at, due_at, status, priority, task_type, user_id, mk_teacher_id, lesson_id, title, text, source)
                VALUES (?, ?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_key) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    due_at=CASE WHEN teacher_tasks.status='done' THEN teacher_tasks.due_at ELSE excluded.due_at END,
                    priority=excluded.priority,
                    user_id=excluded.user_id,
                    mk_teacher_id=excluded.mk_teacher_id,
                    lesson_id=excluded.lesson_id,
                    title=excluded.title,
                    text=excluded.text,
                    source=excluded.source
                """,
                (task_key, now, now, due_at or "", priority or "normal", task_type or "task", int(user_id) if user_id else None, str(mk_teacher_id or ""), str(lesson_id or ""), title or "Задача", (text or "")[:4000], source or "agent"),
            )
            row = conn.execute("SELECT * FROM teacher_tasks WHERE task_key=?", (task_key,)).fetchone()
        return dict(row) if row else {}

    def list_teacher_tasks(self, user_id: int | None = None, status: str | None = "open", limit: int = 100) -> list[dict[str, Any]]:
        where = []
        params: list[Any] = []
        if user_id is not None:
            where.append("user_id=?")
            params.append(int(user_id))
        if status:
            where.append("status=?")
            params.append(status)
        sql = "SELECT * FROM teacher_tasks"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY CASE priority WHEN 'urgent' THEN 1 WHEN 'high' THEN 2 WHEN 'normal' THEN 3 ELSE 4 END, COALESCE(due_at, ''), id DESC LIMIT ?"
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def complete_teacher_task(self, task_id: int | str, completed_by: int | None = None) -> bool:
        try:
            tid = int(task_id)
        except Exception:
            return False
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE teacher_tasks SET status='done', completed_at=?, completed_by=?, updated_at=? WHERE id=?",
                (now_iso(), int(completed_by) if completed_by else None, now_iso(), tid),
            )
            return cur.rowcount > 0

    def add_notification_log(self, event_type: str, text: str, user_id: int | None = None, lesson_id: str = "", task_id: int | None = None, sent_ok: bool = False, error: str = "") -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO notification_log(created_at, event_type, user_id, lesson_id, task_id, text, sent_ok, error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (now_iso(), event_type or "event", int(user_id) if user_id else None, str(lesson_id or ""), int(task_id) if task_id else None, (text or "")[:4000], 1 if sent_ok else 0, (error or "")[:1000]),
            )
            return int(cur.lastrowid)

    def list_notification_log(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM notification_log ORDER BY id DESC LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]


    def list_teacher_work_schedule(self, user_id: int | None = None, mk_teacher_id: str = "", week_start: str = "", active_only: bool = True, limit: int = 200) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if user_id is not None:
            where.append("user_id=?")
            params.append(int(user_id))
        if mk_teacher_id:
            where.append("mk_teacher_id=?")
            params.append(str(mk_teacher_id))
        if week_start:
            # Old rows without week_start are shown as a fallback so existing test data is not lost.
            where.append("(week_start=? OR week_start IS NULL OR week_start='')")
            params.append(str(week_start)[:10])
        if active_only:
            where.append("active=1")
        sql = "SELECT * FROM teacher_work_schedule"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY COALESCE(week_start, ''), day_of_week, start_time, end_time, id LIMIT ?"
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def upsert_teacher_work_slot(
        self,
        user_id: int,
        mk_teacher_id: str = "",
        day_of_week: int = 0,
        start_time: str = "",
        end_time: str = "",
        location: str = "",
        work_type: str = "",
        note: str = "",
        week_start: str = "",
        slot_id: int | str | None = None,
    ) -> dict[str, Any]:
        now = now_iso()
        day = max(0, min(6, int(day_of_week or 0)))
        start = str(start_time or "").strip()[:5]
        end = str(end_time or "").strip()[:5]
        loc = str(location or "").strip()[:160]
        kind = str(work_type or "Любое").strip()[:120] or "Любое"
        week = str(week_start or "").strip()[:10]
        txt = str(note or "").strip()[:500]
        sid = int(slot_id) if str(slot_id or "").isdigit() else 0
        with self._connect() as conn:
            if sid:
                conn.execute(
                    """
                    UPDATE teacher_work_schedule
                    SET updated_at=?, mk_teacher_id=?, day_of_week=?, start_time=?, end_time=?, location=?, work_type=?, note=?, week_start=?, active=1
                    WHERE id=? AND user_id=?
                    """,
                    (now, str(mk_teacher_id or ""), day, start, end, loc, kind, txt, week, sid, int(user_id)),
                )
                row = conn.execute("SELECT * FROM teacher_work_schedule WHERE id=? AND user_id=?", (sid, int(user_id))).fetchone()
            else:
                cur = conn.execute(
                    """
                    INSERT INTO teacher_work_schedule(created_at, updated_at, user_id, mk_teacher_id, day_of_week, start_time, end_time, location, work_type, note, week_start, active)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                    """,
                    (now, now, int(user_id), str(mk_teacher_id or ""), day, start, end, loc, kind, txt, week),
                )
                row = conn.execute("SELECT * FROM teacher_work_schedule WHERE id=?", (int(cur.lastrowid),)).fetchone()
        return dict(row) if row else {}

    def delete_teacher_work_slot(self, user_id: int, slot_id: int | str) -> bool:
        try:
            sid = int(slot_id)
        except Exception:
            return False
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE teacher_work_schedule SET active=0, updated_at=? WHERE id=? AND user_id=?",
                (now_iso(), sid, int(user_id)),
            )
            return cur.rowcount > 0


    # --- Client manager tasks ---

    def upsert_client_manager_task(
        self,
        created_by: int,
        task_type: str,
        status: str = "new",
        priority: str = "normal",
        client_name: str = "",
        child_name: str = "",
        contact: str = "",
        desired_date: str = "",
        desired_time: str = "",
        location: str = "",
        amount: str = "",
        payment_for: str = "",
        deadline: str = "",
        comment: str = "",
        result_text: str = "",
        source_type: str = "",
        source_key: str = "",
        lesson_id: str = "",
        mk_student_id: str = "",
        task_id: int | str | None = None,
    ) -> dict[str, Any]:
        now = now_iso()
        kind = (task_type or "").strip().lower()[:40] or "other"
        status = (status or "new").strip().lower()[:40] or "new"
        priority = (priority or "normal").strip().lower()[:40] or "normal"
        tid = int(task_id) if str(task_id or "").isdigit() else 0
        values = {
            "created_by": int(created_by) if created_by else None,
            "status": status,
            "priority": priority,
            "task_type": kind,
            "client_name": (client_name or "").strip()[:240],
            "child_name": (child_name or "").strip()[:240],
            "contact": (contact or "").strip()[:240],
            "desired_date": (desired_date or "").strip()[:40],
            "desired_time": (desired_time or "").strip()[:120],
            "location": (location or "").strip()[:160],
            "amount": (amount or "").strip()[:120],
            "payment_for": (payment_for or "").strip()[:240],
            "deadline": (deadline or "").strip()[:40],
            "comment": (comment or "").strip()[:2000],
            "result_text": (result_text or "").strip()[:4000],
            "source_type": (source_type or "").strip()[:80],
            "source_key": (source_key or "").strip()[:300],
            "lesson_id": (lesson_id or "").strip()[:80],
            "mk_student_id": (mk_student_id or "").strip()[:80],
        }
        with self._connect() as conn:
            if not tid and values.get("source_key"):
                existing = conn.execute(
                    "SELECT * FROM client_manager_tasks WHERE source_key=? ORDER BY id DESC LIMIT 1",
                    (values["source_key"],),
                ).fetchone()
                if existing:
                    existing_dict = dict(existing)
                    existing_status = str(existing_dict.get("status") or "").strip().lower()
                    if existing_status in {"done", "cancelled"}:
                        return existing_dict
                    tid = int(existing_dict["id"])
            if tid:
                assignments = ", ".join([f"{k}=?" for k in values.keys()])
                params = list(values.values()) + [now, tid]
                conn.execute(f"UPDATE client_manager_tasks SET {assignments}, updated_at=? WHERE id=?", params)
                row = conn.execute("SELECT * FROM client_manager_tasks WHERE id=?", (tid,)).fetchone()
            else:
                base = {"created_at": now, "updated_at": now, **values}
                columns = list(base.keys())
                placeholders = ",".join(["?"] * len(columns))
                cur = conn.execute(
                    f"INSERT INTO client_manager_tasks({','.join(columns)}) VALUES ({placeholders})",
                    [base[c] for c in columns],
                )
                row = conn.execute("SELECT * FROM client_manager_tasks WHERE id=?", (int(cur.lastrowid),)).fetchone()
        return dict(row) if row else {}

    def list_client_manager_tasks(self, status: str | None = "active", task_type: str = "", created_by: int | None = None, limit: int = 200) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        status = (status or "").strip().lower()
        if status and status != "all":
            if status == "active":
                where.append("status NOT IN ('done','cancelled')")
            else:
                where.append("status=?")
                params.append(status)
        if task_type and task_type != "all":
            where.append("task_type=?")
            params.append(task_type)
        if created_by is not None:
            where.append("created_by=?")
            params.append(int(created_by))
        sql = "SELECT * FROM client_manager_tasks"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY CASE priority WHEN 'urgent' THEN 1 WHEN 'high' THEN 2 WHEN 'normal' THEN 3 ELSE 4 END, CASE status WHEN 'new' THEN 1 WHEN 'in_work' THEN 2 WHEN 'waiting_client' THEN 3 WHEN 'done' THEN 8 WHEN 'cancelled' THEN 9 ELSE 5 END, COALESCE(deadline, desired_date, ''), id DESC LIMIT ?"
        params.append(int(limit))
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def get_client_manager_task(self, task_id: int | str) -> Optional[dict[str, Any]]:
        try:
            tid = int(task_id)
        except Exception:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM client_manager_tasks WHERE id=?", (tid,)).fetchone()
        return dict(row) if row else None

    def update_client_manager_task_status(self, task_id: int | str, status: str, user_id: int | None = None) -> bool:
        try:
            tid = int(task_id)
        except Exception:
            return False
        clean_status = (status or "").strip().lower()[:40] or "new"
        now = now_iso()
        is_closed = clean_status in {"done", "cancelled"}
        completed_at = now if is_closed else ""
        completed_by = int(user_id) if is_closed and user_id else None
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE client_manager_tasks SET status=?, updated_at=?, completed_at=?, completed_by=? WHERE id=?",
                (clean_status, now, completed_at, completed_by, tid),
            )
            return cur.rowcount > 0

    # --- KPI: client task events ---

    def add_client_task_event(
        self,
        task_id: int | str,
        event_type: str,
        actor_user_id: int | None = None,
        actor_name: str = "",
        old_status: str = "",
        new_status: str = "",
        comment: str = "",
    ) -> int:
        try:
            tid = int(task_id)
        except Exception:
            return 0
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO client_task_events(created_at, task_id, actor_user_id, actor_name, event_type, old_status, new_status, comment) VALUES (?,?,?,?,?,?,?,?)",
                (now_iso(), tid, actor_user_id, (actor_name or "")[:200], (event_type or "")[:60], (old_status or "")[:60], (new_status or "")[:60], (comment or "")[:1000]),
            )
            return int(cur.lastrowid or 0)

    def list_client_task_events(self, task_id: int | str, limit: int = 100) -> list[dict[str, Any]]:
        try:
            tid = int(task_id)
        except Exception:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM client_task_events WHERE task_id=? ORDER BY id ASC LIMIT ?",
                (tid, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _valid_kpi_date(value: str) -> str:
        """Return value as-is if it looks like YYYY-MM-DD, else empty string."""
        import re as _re
        s = str(value or "").strip()[:10]
        return s if _re.fullmatch(r"20\d{2}-\d{2}-\d{2}", s) else ""

    def get_client_tasks_kpi(
        self,
        date_from: str = "",
        date_to: str = "",
        actor_user_id: Optional[int] = None,
    ) -> dict[str, Any]:
        df = self._valid_kpi_date(date_from)
        dt = self._valid_kpi_date(date_to)

        where_tasks: list[str] = []
        params_tasks: list[Any] = []
        if df:
            where_tasks.append("created_at >= ?")
            params_tasks.append(df)
        if dt:
            where_tasks.append("created_at <= ?")
            params_tasks.append(dt + "T23:59:59")

        sql_tasks = "SELECT id, status, task_type, created_at, completed_at FROM client_manager_tasks"
        if where_tasks:
            sql_tasks += " WHERE " + " AND ".join(where_tasks)

        where_ev: list[str] = []
        params_ev: list[Any] = []
        if df:
            where_ev.append("created_at >= ?")
            params_ev.append(df)
        if dt:
            where_ev.append("created_at <= ?")
            params_ev.append(dt + "T23:59:59")
        if actor_user_id is not None:
            where_ev.append("actor_user_id = ?")
            params_ev.append(int(actor_user_id))

        sql_ev = "SELECT actor_user_id, actor_name, event_type, new_status FROM client_task_events"
        if where_ev:
            sql_ev += " WHERE " + " AND ".join(where_ev)

        # avg_completion_hours: only done tasks, using DB columns (reliable & cheap).
        avg_where = ["completed_at IS NOT NULL", "completed_at != ''", "status = 'done'"]
        avg_params: list[Any] = []
        if df:
            avg_where.append("created_at >= ?")
            avg_params.append(df)
        if dt:
            avg_where.append("created_at <= ?")
            avg_params.append(dt + "T23:59:59")
        sql_avg = (
            "SELECT AVG((julianday(completed_at) - julianday(created_at)) * 24)"
            " FROM client_manager_tasks WHERE " + " AND ".join(avg_where)
        )

        try:
            with self._connect() as conn:
                task_rows = [dict(r) for r in conn.execute(sql_tasks, params_tasks).fetchall()]
                event_rows = [dict(r) for r in conn.execute(sql_ev, params_ev).fetchall()]
                avg_row = conn.execute(sql_avg, avg_params).fetchone()
        except Exception:
            task_rows, event_rows, avg_row = [], [], None

        total = len(task_rows)
        done = sum(1 for r in task_rows if r.get("status") == "done")
        canceled = sum(1 for r in task_rows if r.get("status") in ("cancelled", "canceled"))
        in_progress = sum(1 for r in task_rows if r.get("status") == "in_work")
        waiting_client = sum(1 for r in task_rows if r.get("status") == "waiting_client")
        active = sum(1 for r in task_rows if r.get("status") not in ("done", "cancelled", "canceled"))

        by_type: dict[str, int] = {"makeup": 0, "trial": 0, "payment": 0}
        for r in task_rows:
            t = str(r.get("task_type") or "")
            if t in by_type:
                by_type[t] += 1

        actor_map: dict[int, dict[str, Any]] = {}
        for ev in event_rows:
            uid = ev.get("actor_user_id")
            if uid is None:
                continue
            uid = int(uid)
            ev_name = str(ev.get("actor_name") or "").strip()
            if uid not in actor_map:
                actor_map[uid] = {"user_id": uid, "name": ev_name, "done": 0, "canceled": 0, "in_progress": 0, "waiting_client": 0, "total_events": 0}
            elif not actor_map[uid]["name"] and ev_name:
                # Fill in name from the first event that has one.
                actor_map[uid]["name"] = ev_name
            actor_map[uid]["total_events"] += 1
            ns = str(ev.get("new_status") or "")
            if ns == "done":
                actor_map[uid]["done"] += 1
            elif ns in ("cancelled", "canceled"):
                actor_map[uid]["canceled"] += 1
            elif ns == "in_work":
                actor_map[uid]["in_progress"] += 1
            elif ns == "waiting_client":
                actor_map[uid]["waiting_client"] += 1

        avg_hours = None
        try:
            v = avg_row[0] if avg_row else None
            if v is not None:
                avg_hours = round(float(v), 1)
        except Exception:
            pass

        return {
            "total": total,
            "active": active,
            "done": done,
            "canceled": canceled,
            "waiting_client": waiting_client,
            "in_progress": in_progress,
            "by_type": by_type,
            "by_actor": sorted(actor_map.values(), key=lambda x: x["total_events"], reverse=True),
            "avg_completion_hours": avg_hours,
        }

    # ── bePaid integration ─────────────────────────────────────────────────────

    def find_bepaid_transaction(self, provider: str, shop_type: str, transaction_uid: str | None,
                                transaction_id: str | None, order_id: str | None,
                                amount_minor: int | None) -> Optional[dict[str, Any]]:
        with self._connect() as conn:
            if transaction_uid:
                row = conn.execute(
                    "SELECT * FROM bepaid_transactions WHERE provider=? AND shop_type=? AND transaction_uid=? LIMIT 1",
                    (provider, shop_type, transaction_uid),
                ).fetchone()
                if row:
                    return dict(row)
            if transaction_id:
                row = conn.execute(
                    "SELECT * FROM bepaid_transactions WHERE provider=? AND shop_type=? AND transaction_id=? LIMIT 1",
                    (provider, shop_type, transaction_id),
                ).fetchone()
                if row:
                    return dict(row)
            if order_id and amount_minor is not None:
                row = conn.execute(
                    "SELECT * FROM bepaid_transactions WHERE provider=? AND shop_type=? AND order_id=? AND amount_minor=? LIMIT 1",
                    (provider, shop_type, order_id, amount_minor),
                ).fetchone()
                if row:
                    return dict(row)
        return None

    def upsert_bepaid_transaction(self, data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        import json as _json
        now = now_iso()
        provider = str(data.get("provider") or "bepaid")
        shop_type = str(data.get("shop_type") or "unknown")
        transaction_uid = str(data.get("transaction_uid") or "").strip() or None
        transaction_id = str(data.get("transaction_id") or "").strip() or None
        order_id = str(data.get("order_id") or "").strip() or None
        amount_minor = data.get("amount_minor")
        if amount_minor is not None:
            try:
                amount_minor = int(amount_minor)
            except Exception:
                amount_minor = None

        raw = data.get("raw_json")
        raw_str = (_json.dumps(raw, ensure_ascii=False) if isinstance(raw, dict)
                   else (raw if isinstance(raw, str) else None))

        existing = self.find_bepaid_transaction(provider, shop_type, transaction_uid, transaction_id, order_id, amount_minor)
        is_new = existing is None

        fields = {
            "shop_id": str(data.get("shop_id") or "").strip() or None,
            "transaction_uid": transaction_uid,
            "transaction_id": transaction_id,
            "order_id": order_id,
            "tracking_id": str(data.get("tracking_id") or "").strip() or None,
            "status": str(data.get("status") or "").strip() or None,
            "payment_method_type": str(data.get("payment_method_type") or "").strip() or None,
            "amount_minor": amount_minor,
            "amount_byn": data.get("amount_byn"),
            "currency": str(data.get("currency") or "").strip() or None,
            "paid_at": str(data.get("paid_at") or "").strip() or None,
            "created_at_provider": str(data.get("created_at_provider") or "").strip() or None,
            "test": 1 if data.get("test") else 0,
            "customer_first_name": str(data.get("customer_first_name") or "").strip() or None,
            "customer_last_name": str(data.get("customer_last_name") or "").strip() or None,
            "customer_phone": str(data.get("customer_phone") or "").strip() or None,
            "customer_email": str(data.get("customer_email") or "").strip() or None,
            "billing_phone": str(data.get("billing_phone") or "").strip() or None,
            "mk_user_id": str(data.get("mk_user_id") or "").strip() or None,
            "mk_user_id_source": str(data.get("mk_user_id_source") or "").strip() or None,
            "mk_user_name": str(data.get("mk_user_name") or "").strip() or None,
            "mk_filial_id": str(data.get("mk_filial_id") or "").strip() or None,
            "description": str(data.get("description") or "").strip() or None,
            "erip_account_number": str(data.get("erip_account_number") or "").strip() or None,
            "raw_json": raw_str,
        }

        with self._connect() as conn:
            if existing:
                row_id = existing["id"]
                sets = ", ".join(f"{k}=?" for k in fields)
                vals = list(fields.values()) + [now, row_id]
                conn.execute(f"UPDATE bepaid_transactions SET {sets}, updated_at=? WHERE id=?", vals)
            else:
                cols = list(fields.keys()) + ["provider", "shop_type", "received_at", "updated_at"]
                placeholders = ", ".join("?" * len(cols))
                vals = list(fields.values()) + [provider, shop_type, now, now]
                conn.execute(
                    f"INSERT INTO bepaid_transactions ({', '.join(cols)}) VALUES ({placeholders})",
                    vals,
                )
                row_id = conn.execute(
                    "SELECT last_insert_rowid()"
                ).fetchone()[0]
            row = conn.execute("SELECT * FROM bepaid_transactions WHERE id=?", (row_id,)).fetchone()
        return dict(row) if row else {}, is_new

    def update_bepaid_match(self, row_id: int, match_data: dict[str, Any]) -> None:
        now = now_iso()
        allowed = {"match_status", "match_score", "match_reason", "mk_payment_id", "mk_user_id", "mk_user_name", "posting_status", "posting_error"}
        sets, vals = [], []
        for k, v in match_data.items():
            if k in allowed:
                sets.append(f"{k}=?")
                vals.append(v)
        if not sets:
            return
        vals.extend([now, int(row_id)])
        with self._connect() as conn:
            conn.execute(f"UPDATE bepaid_transactions SET {', '.join(sets)}, updated_at=? WHERE id=?", vals)

    def list_bepaid_transactions(
        self,
        month: str = "",
        shop_type: str = "all",
        match_status: str = "all",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        conditions: list[str] = []
        params: list[Any] = []
        if month:
            conditions.append("(paid_at LIKE ? OR (paid_at IS NULL AND received_at LIKE ?))")
            params.extend([f"{month}%", f"{month}%"])
        if shop_type and shop_type != "all":
            conditions.append("shop_type=?")
            params.append(shop_type)
        if match_status and match_status != "all":
            conditions.append("match_status=?")
            params.append(match_status)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        params.append(max(1, min(int(limit), 2000)))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM bepaid_transactions {where} ORDER BY paid_at DESC, received_at DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def get_bepaid_last_webhook_at(self) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT received_at FROM bepaid_transactions ORDER BY received_at DESC LIMIT 1"
            ).fetchone()
        return row["received_at"] if row else None

    # ── payment_intents ───────────────────────────────────────────────────────

    def create_payment_intent(self, data: dict) -> dict:
        # Guard: reject duplicate mk_invoice_id before any INSERT or bePaid call.
        # Raises ValueError("duplicate_mk_invoice_intent:<public_id>") if active intent exists.
        _mk_inv_guard = str(data.get("mk_invoice_id") or "").strip()
        if _mk_inv_guard:
            _existing = self.find_active_intent_by_invoice(_mk_inv_guard)
            if _existing:
                raise ValueError(f"duplicate_mk_invoice_intent:{_existing['public_id']}")

        import uuid as _uuid
        now = data.get("created_at") or __import__("datetime").datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO payment_intents
                  (public_id, mk_user_id, student_name, amount_minor, amount_byn, currency,
                   purpose, period_month, payment_method, status, mk_filial_id, location_code,
                   class_id, comment, created_by_tg_id, created_by_name, created_at, updated_at,
                   raw_context_json,
                   mk_invoice_id, mk_user_subscription_id, source, source_reference,
                   invoice_amount_minor, invoice_remaining_minor, invoice_snapshot_json,
                   verified_mk_user_at, verified_invoice_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "ycpi_tmp",
                    int(data["mk_user_id"]),
                    data.get("student_name"),
                    int(data["amount_minor"]),
                    float(data["amount_byn"]),
                    data.get("currency", "BYN"),
                    data.get("purpose", "other"),
                    data.get("period_month"),
                    data.get("payment_method", "erip"),
                    data.get("status", "draft"),
                    data.get("mk_filial_id"),
                    data.get("location_code"),
                    data.get("class_id"),
                    data.get("comment"),
                    data.get("created_by_tg_id"),
                    data.get("created_by_name"),
                    now,
                    now,
                    data.get("raw_context_json"),
                    data.get("mk_invoice_id"),
                    data.get("mk_user_subscription_id"),
                    data.get("source", "manual"),
                    data.get("source_reference"),
                    data.get("invoice_amount_minor"),
                    data.get("invoice_remaining_minor"),
                    data.get("invoice_snapshot_json"),
                    data.get("verified_mk_user_at"),
                    data.get("verified_invoice_at"),
                ),
            )
            row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            period_part = (data.get("period_month") or now[:7]).replace("-", "")
            public_id = f"ycpi_{period_part}_{row_id}"
            conn.execute(
                "UPDATE payment_intents SET public_id=? WHERE id=?",
                (public_id, row_id),
            )
            row = conn.execute("SELECT * FROM payment_intents WHERE id=?", (row_id,)).fetchone()
        return dict(row)

    def get_payment_intent(self, public_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM payment_intents WHERE public_id=?", (public_id,)
            ).fetchone()
        return dict(row) if row else None

    def list_payment_intents(
        self,
        month: Optional[str] = None,
        status: Optional[str] = None,
        mk_user_id: Optional[int] = None,
        limit: int = 200,
        *,
        exclude_cancelled: bool = False,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list = []
        if month:
            clauses.append("period_month=?")
            params.append(month)
        if status and status != "all":
            clauses.append("status=?")
            params.append(status)
        elif exclude_cancelled:
            clauses.append("status != 'cancelled'")
        if mk_user_id:
            clauses.append("mk_user_id=?")
            params.append(mk_user_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM payment_intents {where} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    def cancel_payment_intent(self, public_id: str, reason: str, now: str) -> Optional[dict]:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE payment_intents
                SET status='cancelled', cancel_reason=?, cancelled_at=?, updated_at=?
                WHERE public_id=? AND status IN ('draft','ready')
                """,
                (reason, now, now, public_id),
            )
            row = conn.execute(
                "SELECT * FROM payment_intents WHERE public_id=?", (public_id,)
            ).fetchone()
        return dict(row) if row else None

    def get_bepaid_transactions_for_intent(self, intent_public_id: str) -> list[dict]:
        """Return all bePaid transactions linked to this intent. Read-only."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM bepaid_transactions WHERE intent_public_id=? ORDER BY id",
                (intent_public_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def cancel_options_for_cleanup(self, intent_public_id: str, now: str) -> int:
        """Mark all active (non-paid, non-cancelled, non-superseded) options as cancelled.

        Never deletes rows. Returns number of rows changed.
        Safe to call multiple times (idempotent on already-cancelled rows).
        """
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE payment_intent_options SET
                    status = 'cancelled',
                    updated_at = ?
                WHERE intent_public_id = ?
                  AND status NOT IN ('paid', 'cancelled', 'superseded')
                """,
                (now, intent_public_id),
            )
            changes = conn.execute("SELECT changes()").fetchone()[0]
        return int(changes)

    def cancel_payment_intent_for_cleanup(
        self, public_id: str, reason: str, now: str
    ) -> dict:
        """Cancel a payment intent for test/maintenance cleanup.

        Covers any pre-payment status. Hard-blocks paid/posted_to_moyklass.
        Idempotent: already-cancelled returns {"ok": True, "idempotent": True}.
        Does NOT delete transactions, options, webhooks, MK identifiers, or audit rows.
        """
        _HARD_BLOCK = frozenset({"paid", "posted_to_moyklass", "double_payment_requires_check"})
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM payment_intents WHERE public_id=?", (public_id,)
            ).fetchone()
        if not row:
            return {"ok": False, "error": "not_found", "public_id": public_id}
        pi = dict(row)
        if pi["status"] == "cancelled":
            return {"ok": True, "idempotent": True, "intent": pi}
        if pi["status"] in _HARD_BLOCK:
            return {
                "ok": False,
                "error": "blocked_paid_or_posted",
                "status": pi["status"],
                "intent": pi,
            }
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE payment_intents
                SET status='cancelled', cancel_reason=?, cancelled_at=?, updated_at=?
                WHERE public_id=?
                  AND status NOT IN ('paid', 'posted_to_moyklass',
                                     'cancelled', 'double_payment_requires_check')
                """,
                (reason, now, now, public_id),
            )
            row = conn.execute(
                "SELECT * FROM payment_intents WHERE public_id=?", (public_id,)
            ).fetchone()
        pi_after = dict(row) if row else pi
        if pi_after.get("status") != "cancelled":
            return {
                "ok": False,
                "error": "update_did_not_apply",
                "status": pi_after.get("status"),
                "intent": pi_after,
            }
        return {"ok": True, "intent": pi_after}

    # ── v7.0.93 — parent client visibility ───────────────────────────────────

    def get_parents_for_child(self, mk_user_id: str) -> list[dict[str, Any]]:
        """Return active client parent-child links for the given student.

        Uses client_parent_child_links (client system), NOT the food parent_child_links table.
        """
        mk_user_id = str(mk_user_id or "").strip()
        if not mk_user_id:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM client_parent_child_links WHERE mk_user_id=? AND status='active' ORDER BY id",
                (mk_user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def publish_payment_intent_to_client(
        self, public_id: str, published_by: str, now: str
    ) -> dict[str, Any]:
        """Set client_visibility='published'. Idempotent if already published."""
        pi = self.get_payment_intent(public_id)
        if not pi:
            return {"ok": False, "error": "not_found", "public_id": public_id}
        if pi.get("client_visibility") == "published":
            return {"ok": True, "idempotent": True, "intent": pi}
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE payment_intents
                SET client_visibility='published', published_at=?, published_by=?, updated_at=?
                WHERE public_id=?
                """,
                (now, str(published_by), now, public_id),
            )
        return {"ok": True, "intent": self.get_payment_intent(public_id)}

    def withdraw_payment_intent_from_client(
        self, public_id: str, withdrawn_by: str, now: str
    ) -> dict[str, Any]:
        """Set client_visibility='withdrawn'. Idempotent if already withdrawn."""
        pi = self.get_payment_intent(public_id)
        if not pi:
            return {"ok": False, "error": "not_found", "public_id": public_id}
        if pi.get("client_visibility") == "withdrawn":
            return {"ok": True, "idempotent": True, "intent": pi}
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE payment_intents
                SET client_visibility='withdrawn', withdrawn_at=?, withdrawn_by=?, updated_at=?
                WHERE public_id=?
                """,
                (now, str(withdrawn_by), now, public_id),
            )
        return {"ok": True, "intent": self.get_payment_intent(public_id)}

    def list_client_visible_payment_intents(
        self, parent_telegram_user_id: str
    ) -> list[dict[str, Any]]:
        """Return published, non-cancelled intents visible to the given parent.

        Uses client_parent_child_links (client system).
        Food parent_child_links do NOT grant access to payments.
        """
        parent_telegram_user_id = str(parent_telegram_user_id or "").strip()
        if not parent_telegram_user_id:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT pi.*
                FROM payment_intents pi
                JOIN client_parent_child_links l
                  ON CAST(l.mk_user_id AS TEXT) = CAST(pi.mk_user_id AS TEXT)
                WHERE l.parent_telegram_user_id = ?
                  AND l.status = 'active'
                  AND pi.client_visibility = 'published'
                  AND pi.status != 'cancelled'
                ORDER BY pi.created_at DESC
                """,
                (parent_telegram_user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── v7.0.93.1 — client parent-child link methods ──────────────────────────

    @staticmethod
    def _hash_client_code(code: str) -> str:
        import hashlib as _hl
        return _hl.sha256(code.encode("utf-8")).hexdigest()

    @staticmethod
    def _generate_client_code_plaintext() -> str:
        import secrets as _sec
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        suffix = "".join(_sec.choice(alphabet) for _ in range(8))
        return f"CL-{suffix}"

    def create_client_link_code(
        self,
        mk_user_id: str,
        child_display_name: str,
        created_by: str,
        expires_at: Optional[str] = None,
    ) -> dict[str, Any]:
        """Generate a new one-time client link code for a student.

        Automatically invalidates any previously active code for the same mk_user_id.
        Returns the plaintext code (shown once) and the stored code_id.
        Does NOT touch food parent_child_links or camp_children.
        """
        mk_user_id = str(mk_user_id or "").strip()
        child_display_name = str(child_display_name or "").strip()
        if not mk_user_id:
            return {"ok": False, "error": "mk_user_id обязателен"}
        if not child_display_name:
            return {"ok": False, "error": "child_display_name обязателен"}
        now = now_iso()
        # Invalidate any existing active codes for this student
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE client_child_link_codes
                SET status='invalidated', invalidated_at=?, invalidated_by=?, updated_at=?
                WHERE mk_user_id=? AND status='active'
                """,
                (now, f"auto_replaced_by:{created_by}", now, mk_user_id),
            )
        # Generate a unique hashed code
        for _ in range(30):
            plaintext = self._generate_client_code_plaintext()
            code_hash = self._hash_client_code(plaintext)
            try:
                with self._connect() as conn:
                    conn.execute(
                        """
                        INSERT INTO client_child_link_codes
                        (code_hash, mk_user_id, child_display_name, status,
                         created_at, expires_at, created_by, updated_at)
                        VALUES (?, ?, ?, 'active', ?, ?, ?, ?)
                        """,
                        (code_hash, mk_user_id, child_display_name, now,
                         expires_at, str(created_by), now),
                    )
                    code_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                return {
                    "ok": True,
                    "code": plaintext,
                    "code_id": code_id,
                    "mk_user_id": mk_user_id,
                    "child_display_name": child_display_name,
                }
            except Exception:
                continue
        return {"ok": False, "error": "Не удалось создать уникальный код"}

    def link_client_child(
        self, parent_telegram_user_id: str, code_plaintext: str, now: str
    ) -> dict[str, Any]:
        """Validate a client code and create a parent-child link.

        One-time use: marks the code as 'used' after successful link.
        Does NOT touch food parent_child_links or camp_children.
        """
        parent_telegram_user_id = str(parent_telegram_user_id or "").strip()
        code_plaintext = str(code_plaintext or "").strip().upper()
        if not parent_telegram_user_id or not code_plaintext:
            return {"ok": False, "error": "Код и ID родителя обязательны"}
        code_hash = self._hash_client_code(code_plaintext)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM client_child_link_codes WHERE code_hash=?", (code_hash,)
            ).fetchone()
        if not row:
            return {"ok": False, "error": "Код не найден. Проверьте правильность ввода."}
        cr = dict(row)
        if cr["status"] == "used":
            # Idempotent: if this same parent already used it
            if cr.get("used_by_parent_telegram_user_id") == parent_telegram_user_id:
                with self._connect() as conn:
                    existing = conn.execute(
                        "SELECT * FROM client_parent_child_links WHERE parent_telegram_user_id=? AND mk_user_id=? AND status='active'",
                        (parent_telegram_user_id, cr["mk_user_id"]),
                    ).fetchone()
                if existing:
                    return {"ok": True, "already_linked": True,
                            "mk_user_id": cr["mk_user_id"],
                            "child_display_name": cr["child_display_name"]}
            return {"ok": False, "error": "Этот код уже был использован."}
        if cr["status"] == "invalidated":
            return {"ok": False, "error": "Этот код недействителен. Обратитесь к администратору."}
        if cr["status"] == "expired":
            return {"ok": False, "error": "Срок действия кода истёк. Обратитесь к администратору."}
        if cr["status"] != "active":
            return {"ok": False, "error": f"Код недействителен (статус: {cr['status']})."}
        if cr.get("expires_at") and cr["expires_at"] < now:
            with self._connect() as conn:
                conn.execute(
                    "UPDATE client_child_link_codes SET status='expired', updated_at=? WHERE id=?",
                    (now, cr["id"]),
                )
            return {"ok": False, "error": "Срок действия кода истёк. Обратитесь к администратору."}
        mk_user_id = cr["mk_user_id"]
        child_display_name = cr.get("child_display_name") or ""
        # Check for existing active link for this pair
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM client_parent_child_links WHERE parent_telegram_user_id=? AND mk_user_id=? AND status='active'",
                (parent_telegram_user_id, mk_user_id),
            ).fetchone()
        if existing:
            return {"ok": True, "already_linked": True,
                    "mk_user_id": mk_user_id, "child_display_name": child_display_name}
        # Create the link and mark code as used (atomic)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO client_parent_child_links
                (parent_telegram_user_id, mk_user_id, child_display_name, status,
                 linked_at, linked_by_code_id, created_at, updated_at)
                VALUES (?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (parent_telegram_user_id, mk_user_id, child_display_name,
                 now, cr["id"], now, now),
            )
            conn.execute(
                """
                UPDATE client_child_link_codes
                SET status='used', used_at=?, used_by_parent_telegram_user_id=?, updated_at=?
                WHERE id=?
                """,
                (now, parent_telegram_user_id, now, cr["id"]),
            )
        return {"ok": True, "mk_user_id": mk_user_id, "child_display_name": child_display_name}

    def list_client_children_for_parent(
        self, parent_telegram_user_id: str
    ) -> list[dict[str, Any]]:
        """Return all active client-linked children for the given parent.

        Does NOT query the food parent_child_links table.
        """
        parent_telegram_user_id = str(parent_telegram_user_id or "").strip()
        if not parent_telegram_user_id:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM client_parent_child_links
                WHERE parent_telegram_user_id=? AND status='active'
                ORDER BY linked_at DESC
                """,
                (parent_telegram_user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def unlink_client_child(
        self,
        parent_telegram_user_id: str,
        mk_user_id: str,
        unlinked_by: str,
        now: str,
    ) -> dict[str, Any]:
        """Mark a client parent-child link as 'unlinked'.

        Does NOT delete payment intents, audit rows, or food links.
        Does NOT affect the Food Module in any way.
        """
        parent_telegram_user_id = str(parent_telegram_user_id or "").strip()
        mk_user_id = str(mk_user_id or "").strip()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM client_parent_child_links
                WHERE parent_telegram_user_id=? AND mk_user_id=? AND status='active'
                """,
                (parent_telegram_user_id, mk_user_id),
            ).fetchone()
        if not row:
            return {"ok": False, "error": "Активная привязка не найдена"}
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE client_parent_child_links
                SET status='unlinked', unlinked_at=?, unlinked_by=?, updated_at=?
                WHERE id=?
                """,
                (now, str(unlinked_by), now, row["id"]),
            )
        return {"ok": True, "unlinked": {"mk_user_id": mk_user_id,
                                          "parent_telegram_user_id": parent_telegram_user_id}}

    def invalidate_client_link_code(
        self, code_id: int, invalidated_by: str, now: str
    ) -> dict[str, Any]:
        """Mark a client link code as invalidated (admin action)."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM client_child_link_codes WHERE id=?", (code_id,)
            ).fetchone()
        if not row:
            return {"ok": False, "error": "Код не найден"}
        cr = dict(row)
        if cr["status"] != "active":
            return {"ok": False, "error": f"Код уже {cr['status']}"}
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE client_child_link_codes
                SET status='invalidated', invalidated_at=?, invalidated_by=?, updated_at=?
                WHERE id=?
                """,
                (now, str(invalidated_by), now, code_id),
            )
        return {"ok": True, "code_id": code_id}

    def get_client_link_status_for_student(self, mk_user_id: str) -> dict[str, Any]:
        """Admin view: active links and recent codes for a student.

        Does NOT expose code_hash or food data.
        """
        mk_user_id = str(mk_user_id or "").strip()
        with self._connect() as conn:
            links = conn.execute(
                "SELECT * FROM client_parent_child_links WHERE mk_user_id=? ORDER BY linked_at DESC",
                (mk_user_id,),
            ).fetchall()
            codes = conn.execute(
                """
                SELECT id, mk_user_id, child_display_name, status,
                       created_at, expires_at, used_at, invalidated_at, created_by
                FROM client_child_link_codes
                WHERE mk_user_id=? ORDER BY created_at DESC LIMIT 20
                """,
                (mk_user_id,),
            ).fetchall()
        return {
            "mk_user_id": mk_user_id,
            "links": [dict(r) for r in links],
            "codes": [dict(r) for r in codes],
        }

    def find_duplicate_payment_intents(
        self,
        mk_user_id: int,
        amount_minor: int,
        purpose: str,
        period_month: Optional[str],
    ) -> list[dict]:
        params: list = [mk_user_id, amount_minor, purpose]
        period_clause = ""
        if period_month:
            period_clause = "AND period_month=? "
            params.append(period_month)
        params += ["cancelled", "error"]
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM payment_intents
                WHERE mk_user_id=? AND amount_minor=? AND purpose=?
                  {period_clause}
                  AND status NOT IN (?,?)
                ORDER BY created_at DESC LIMIT 5
                """,
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    _PI_ACTIVE_STATUSES = tuple(_DOMAIN_ACTIVE_STATUSES)

    def find_active_intent_by_invoice(self, mk_invoice_id: str) -> Optional[dict]:
        """Return the most recent active intent for this MK invoice (excludes cancelled/error)."""
        placeholders = ",".join("?" * len(self._PI_ACTIVE_STATUSES))
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT * FROM payment_intents
                WHERE mk_invoice_id = ?
                  AND status IN ({placeholders})
                ORDER BY id DESC LIMIT 1
                """,
                (str(mk_invoice_id), *self._PI_ACTIVE_STATUSES),
            ).fetchone()
        return dict(row) if row else None

    def find_all_active_intents_by_invoice(self, mk_invoice_id: str) -> list[dict]:
        """Return ALL active intents for this MK invoice, oldest first. Used for duplicate detection.

        Returns 0 items if none exist, 1 if unique (normal), 2+ if duplicates detected.
        """
        placeholders = ",".join("?" * len(self._PI_ACTIVE_STATUSES))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM payment_intents
                WHERE mk_invoice_id = ?
                  AND status IN ({placeholders})
                ORDER BY id ASC
                """,
                (str(mk_invoice_id), *self._PI_ACTIVE_STATUSES),
            ).fetchall()
        return [dict(r) for r in rows]

    def payment_intents_stats(self, month: Optional[str] = None) -> dict:
        with self._connect() as conn:
            clauses = []
            params: list = []
            if month:
                clauses.append("period_month=?")
                params.append(month)
            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            rows = conn.execute(
                f"SELECT status, COUNT(*) as cnt FROM payment_intents {where} GROUP BY status",
                params,
            ).fetchall()
        stats: dict[str, int] = {}
        for r in rows:
            stats[r["status"]] = r["cnt"]
        return {
            "total": sum(stats.values()),
            "draft": stats.get("draft", 0),
            "ready": stats.get("ready", 0),
            "bepaid_creating": stats.get("bepaid_creating", 0),
            "bepaid_created": stats.get("bepaid_created", 0),
            "bepaid_requires_check": stats.get("bepaid_requires_check", 0),
            "paid": stats.get("paid", 0),
            "posted_to_moyklass": stats.get("posted_to_moyklass", 0),
            "cancelled": stats.get("cancelled", 0),
            "error": stats.get("error", 0),
        }

    def audit_payment_integrity(self) -> dict:
        """Read-only audit of payment intent data integrity.

        Returns {"checked": N, "critical": [...], "warning": [...], "info": [...]}
        Performs NO writes, NO external API calls.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM payment_intents ORDER BY id ASC"
            ).fetchall()
        intents = [dict(r) for r in rows]

        critical: list[dict] = []
        warning: list[dict] = []
        info: list[dict] = []

        def _issue(severity: str, intent: dict, code: str, desc: str) -> None:
            entry = {
                "code": code,
                "description": desc,
                "public_id": str(intent.get("public_id") or ""),
                "mk_invoice_id": str(intent.get("mk_invoice_id") or ""),
            }
            if severity == "critical":
                critical.append(entry)
            elif severity == "warning":
                warning.append(entry)
            else:
                info.append(entry)

        # Group by mk_invoice_id for duplicate detection
        from collections import defaultdict
        by_invoice: dict[str, list[dict]] = defaultdict(list)
        by_tx_uid: dict[str, list[dict]] = defaultdict(list)

        for pi in intents:
            mk_id = str(pi.get("mk_invoice_id") or "").strip()
            if mk_id and str(pi.get("status") or "") in _DOMAIN_ACTIVE_STATUSES:
                by_invoice[mk_id].append(pi)
            tx_uid = str(pi.get("paid_transaction_uid") or "").strip()
            if tx_uid:
                by_tx_uid[tx_uid].append(pi)

        # Duplicate active intents per invoice
        for mk_id, pis in by_invoice.items():
            if len(pis) > 1:
                for pi in pis:
                    _issue("critical", pi, "duplicate_active_intent",
                           f"Несколько активных intent для mk_invoice_id={mk_id} "
                           f"({len(pis)} шт.)")

        # Duplicate paid_transaction_uid
        for tx_uid, pis in by_tx_uid.items():
            paid_with_uid = [p for p in pis if str(p.get("status") or "") in ("paid", "posted_to_moyklass")]
            if len(paid_with_uid) > 1:
                for pi in paid_with_uid:
                    _issue("critical", pi, "duplicate_tx_uid",
                           f"Несколько paid intent с одинаковым transaction_uid (prefix {tx_uid[:8]}...)")

        for pi in intents:
            status = str(pi.get("status") or "")
            mk_payment_id = pi.get("mk_payment_id")
            mk_posting_status = str(pi.get("mk_posting_status") or "")
            paid_tx_uid = str(pi.get("paid_transaction_uid") or "").strip()
            paid_at = pi.get("paid_at")
            paid_amount_minor = pi.get("paid_amount_minor")
            webhook_verified = bool(pi.get("webhook_verified"))
            paid_channel = str(pi.get("paid_channel") or "").strip()
            source = str(pi.get("source") or "")
            mk_invoice_id = str(pi.get("mk_invoice_id") or "").strip()

            # posted without mk_payment_id
            if status == "posted_to_moyklass" and not mk_payment_id:
                _issue("critical", pi, "posted_no_mk_payment_id",
                       "status=posted_to_moyklass, но mk_payment_id отсутствует")

            # mk_posting_status=posted without mk_payment_id
            if mk_posting_status == "posted" and not mk_payment_id:
                _issue("critical", pi, "posting_status_posted_no_id",
                       "mk_posting_status=posted, но mk_payment_id отсутствует")

            # mk_payment_id exists but status not final
            if mk_payment_id and status not in ("posted_to_moyklass", "cancelled", "error"):
                _issue("warning", pi, "mk_payment_id_wrong_status",
                       f"mk_payment_id={mk_payment_id} есть, но status={status!r} (ожидается posted_to_moyklass)")

            # webhook_verified without tx_uid
            if webhook_verified and not paid_tx_uid:
                _issue("critical", pi, "verified_no_tx_uid",
                       "webhook_verified=1, но paid_transaction_uid отсутствует")

            # paid_at without paid_amount_minor
            if paid_at and not paid_amount_minor:
                _issue("warning", pi, "paid_at_no_amount",
                       "paid_at установлен, но paid_amount_minor отсутствует или 0")

            # paid intent missing paid_channel
            if status in ("paid", "posted_to_moyklass") and not paid_channel:
                _issue("warning", pi, "paid_no_channel",
                       "Оплаченный intent без paid_channel")

            # source_reference mismatch for automation intents
            if source == "moyklass_invoice_automation" and not _is_src_ref_valid(pi):
                src_ref = str(pi.get("source_reference") or "")
                _issue("warning", pi, "source_reference_mismatch",
                       f"source_reference={src_ref!r} не совпадает с mk_invoice_id={mk_invoice_id!r}")

            # automation intent without mk_invoice_id
            if source == "moyklass_invoice_automation" and not mk_invoice_id:
                _issue("critical", pi, "automation_no_mk_invoice_id",
                       "source=moyklass_invoice_automation, но mk_invoice_id отсутствует")

            # cancelled but parent published (if field exists)
            if status in ("cancelled", "error") and bool(pi.get("parent_published")):
                _issue("warning", pi, "cancelled_parent_published",
                       "Intent отменён, но опубликован родителю")

            # paid intent with awaiting_payment status (stale state)
            if paid_tx_uid and paid_at and status == "awaiting_payment":
                _issue("critical", pi, "paid_but_awaiting",
                       "paid_at и paid_transaction_uid установлены, но status=awaiting_payment")

            # posting_error with status=posted_to_moyklass
            if status == "posted_to_moyklass" and str(pi.get("mk_posting_error") or "").strip():
                _issue("warning", pi, "posted_with_error",
                       "status=posted_to_moyklass, но mk_posting_error не пустой")

            # stale claiming state (mk_posting_status=claiming, no mk_payment_id)
            if mk_posting_status == "claiming" and not mk_payment_id and status not in ("paid",):
                _issue("warning", pi, "stale_posting_claim",
                       f"mk_posting_status=claiming, но status={status!r} — возможно зависший claim")

            # informational: payment_method/paid_channel mismatch (not an error)
            pm = str(pi.get("payment_method") or "").strip()
            if pm and paid_channel and pm != paid_channel and status in ("paid", "posted_to_moyklass"):
                _issue("info", pi, "channel_method_mismatch",
                       f"payment_method={pm!r} отличается от фактического paid_channel={paid_channel!r} "
                       f"— это ожидаемо, если оплата прошла другим способом (например, acquiring вместо erip)")

        return {
            "checked": len(intents),
            "critical": critical,
            "warning": warning,
            "info": info,
        }

    def payment_intent_claim_bepaid_creation(
        self,
        public_id: str,
        account_number: str,
        order_id: str,
        tracking_id: str,
    ) -> bool:
        """Atomically claim this intent for bePaid invoice creation.

        Sets status='bepaid_creating' only if current status is draft/ready AND
        bepaid_uid is not yet set. Returns True if exactly one row was updated
        (claim succeeded). Concurrent callers get False and must not call bePaid.
        """
        from utils import now_iso
        now = now_iso()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE payment_intents
                SET status                  = 'bepaid_creating',
                    bepaid_account_number   = ?,
                    bepaid_order_id         = ?,
                    bepaid_tracking_id      = ?,
                    bepaid_shop_type        = 'erip',
                    bepaid_request_attempts = COALESCE(bepaid_request_attempts, 0) + 1,
                    bepaid_created_at       = ?,
                    updated_at              = ?
                WHERE public_id = ?
                  AND status IN ('draft', 'ready')
                  AND COALESCE(bepaid_uid, '') = ''
                """,
                (account_number, order_id, tracking_id, now, now, public_id),
            )
            return cursor.rowcount == 1

    def payment_intent_mark_requires_check(self, public_id: str, error: str = "", *, reason: str = "") -> None:
        """Transition to bepaid_requires_check after an ambiguous bePaid result or webhook validation failure.

        `error` — bePaid API error string (timeout, HTTP 5xx, etc.)
        `reason` — webhook validation failure reason (amount_mismatch, currency_mismatch, etc.)
        """
        from utils import now_iso
        now = now_iso()
        with self._connect() as conn:
            conn.execute(
                """UPDATE payment_intents
                   SET status = 'bepaid_requires_check',
                       bepaid_error = CASE WHEN ? != '' THEN ? ELSE bepaid_error END,
                       payment_state_reason = CASE WHEN ? != '' THEN ? ELSE payment_state_reason END,
                       last_webhook_at = CASE WHEN ? != '' THEN ? ELSE last_webhook_at END,
                       updated_at = ?
                   WHERE public_id = ?""",
                (str(error)[:500], str(error)[:500], reason, reason,
                 reason, now if reason else None, now, public_id),
            )

    def payment_intent_release_claim(
        self, public_id: str, original_status: str, error: str = ""
    ) -> None:
        """Release a bepaid_creating claim back to original status after a definitive 4xx error.

        Only reverts if status is still 'bepaid_creating' (no-op otherwise).
        """
        from utils import now_iso
        safe_status = original_status if original_status in ("draft", "ready") else "ready"
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE payment_intents
                SET status      = ?,
                    bepaid_error = ?,
                    updated_at  = ?
                WHERE public_id = ? AND status = 'bepaid_creating'
                """,
                (safe_status, str(error)[:500], now_iso(), public_id),
            )

    def payment_intent_save_bepaid_success(
        self,
        public_id: str,
        *,
        bepaid_uid: str,
        bepaid_order_id: str,
        bepaid_account_number: str,
        bepaid_payment_url: str,
        bepaid_status: str,
        bepaid_qr_code_raw: str = "",
    ) -> Optional[dict]:
        """Save confirmed bePaid invoice and transition bepaid_creating → bepaid_created."""
        from utils import now_iso
        now = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE payment_intents
                SET status                = 'bepaid_created',
                    bepaid_uid            = ?,
                    bepaid_order_id       = ?,
                    bepaid_account_number = ?,
                    bepaid_payment_url    = ?,
                    bepaid_status         = ?,
                    bepaid_qr_code_raw    = ?,
                    bepaid_error          = NULL,
                    updated_at            = ?
                WHERE public_id = ? AND status = 'bepaid_creating'
                """,
                (bepaid_uid, bepaid_order_id, bepaid_account_number,
                 bepaid_payment_url, bepaid_status, bepaid_qr_code_raw, now, public_id),
            )
            row = conn.execute(
                "SELECT * FROM payment_intents WHERE public_id = ?", (public_id,)
            ).fetchone()
        return dict(row) if row else None

    # ── v7.0.91: webhook reconciliation ──────────────────────────────────────

    def match_bepaid_transaction_to_intent(self, transaction: dict) -> dict:
        """Try to link a bePaid transaction to a payment_intent.

        Priority: tracking_id > order_id > transaction_uid > erip_account_number
        Returns {matched, intent_id, intent_public_id, method, confidence, reason, conflicts}.
        confidence: 'strong' | 'conflict' | 'none'
        """
        tracking_id = str(transaction.get("tracking_id") or "").strip()
        order_id = str(transaction.get("order_id") or "").strip()
        tx_uid = str(transaction.get("transaction_uid") or "").strip()
        erip_account = str(transaction.get("erip_account_number") or "").strip()

        matches: dict[str, dict] = {}
        with self._connect() as conn:
            if tracking_id:
                row = conn.execute(
                    "SELECT * FROM payment_intents WHERE bepaid_tracking_id=? LIMIT 1",
                    (tracking_id,),
                ).fetchone()
                if row:
                    matches["tracking_id"] = dict(row)
            if order_id:
                row = conn.execute(
                    "SELECT * FROM payment_intents WHERE bepaid_order_id=? LIMIT 1",
                    (order_id,),
                ).fetchone()
                if row:
                    matches["order_id"] = dict(row)
            if tx_uid:
                row = conn.execute(
                    "SELECT * FROM payment_intents WHERE bepaid_uid=? LIMIT 1",
                    (tx_uid,),
                ).fetchone()
                if row:
                    matches["transaction_uid"] = dict(row)
            if erip_account:
                row = conn.execute(
                    "SELECT * FROM payment_intents WHERE bepaid_account_number=? LIMIT 1",
                    (erip_account,),
                ).fetchone()
                if row:
                    matches["account_number"] = dict(row)

        if not matches:
            return {
                "matched": False, "intent_id": None, "intent_public_id": None,
                "method": None, "confidence": "none",
                "reason": "no_identifier_matched", "conflicts": [],
            }

        matched_ids = {r["id"] for r in matches.values()}
        if len(matched_ids) > 1:
            conflicts = [
                {"method": m, "intent_id": r["id"], "intent_public_id": r["public_id"]}
                for m, r in matches.items()
            ]
            return {
                "matched": True, "intent_id": None, "intent_public_id": None,
                "method": "conflict", "confidence": "conflict",
                "reason": "identifiers_point_to_different_intents",
                "conflicts": conflicts,
            }

        for method in ("tracking_id", "order_id", "transaction_uid", "account_number"):
            if method in matches:
                intent = matches[method]
                return {
                    "matched": True,
                    "intent_id": intent["id"],
                    "intent_public_id": intent["public_id"],
                    "method": method,
                    "confidence": "strong",
                    "reason": f"matched_by_{method}",
                    "conflicts": [],
                }

        return {
            "matched": False, "intent_id": None, "intent_public_id": None,
            "method": None, "confidence": "none", "reason": "no_match", "conflicts": [],
        }

    def match_bepaid_transaction_to_payment_target(
        self, transaction: dict, channel: str
    ) -> dict:
        """Option-aware matcher. Searches payment_intent_options by channel first,
        then falls back to legacy intent matching.

        channel: 'acquiring' | 'erip' — from the webhook route, not the payload.

        Returns target_type='payment_option' with option_id and parent_public_id,
        or target_type='legacy_intent' with intent_id and intent_public_id,
        or matched=False with target_type='none'.
        """
        tracking_id = str(transaction.get("tracking_id") or "").strip()
        order_id = str(transaction.get("order_id") or "").strip()
        tx_uid = str(transaction.get("transaction_uid") or "").strip()
        erip_account = str(transaction.get("erip_account_number") or "").strip()

        opt_matches: dict[str, dict] = {}
        with self._connect() as conn:
            if tracking_id:
                row = conn.execute(
                    "SELECT * FROM payment_intent_options WHERE channel=? AND bepaid_tracking_id=? LIMIT 1",
                    (channel, tracking_id),
                ).fetchone()
                if row:
                    opt_matches["tracking_id"] = dict(row)
            if order_id:
                row = conn.execute(
                    "SELECT * FROM payment_intent_options WHERE channel=? AND bepaid_order_id=? LIMIT 1",
                    (channel, order_id),
                ).fetchone()
                if row:
                    opt_matches["order_id"] = dict(row)
            if tx_uid:
                row = conn.execute(
                    "SELECT * FROM payment_intent_options WHERE channel=? AND (bepaid_uid=? OR transaction_uid=?) LIMIT 1",
                    (channel, tx_uid, tx_uid),
                ).fetchone()
                if row:
                    opt_matches["transaction_uid"] = dict(row)
            if channel == "erip" and erip_account:
                row = conn.execute(
                    "SELECT * FROM payment_intent_options WHERE channel='erip' AND bepaid_account_number=? LIMIT 1",
                    (erip_account,),
                ).fetchone()
                if row:
                    opt_matches["account_number"] = dict(row)

        if opt_matches:
            matched_opt_ids = {r["id"] for r in opt_matches.values()}
            if len(matched_opt_ids) > 1:
                conflicts = [
                    {"method": m, "option_id": r["id"], "parent_public_id": r["intent_public_id"]}
                    for m, r in opt_matches.items()
                ]
                return {
                    "matched": True, "target_type": "payment_option",
                    "option_id": None, "payment_intent_id": None, "parent_public_id": None,
                    "channel": channel, "method": "conflict", "confidence": "conflict",
                    "reason": "option_identifiers_point_to_different_options",
                    "conflicts": conflicts,
                }
            for method in ("tracking_id", "order_id", "transaction_uid", "account_number"):
                if method in opt_matches:
                    opt = opt_matches[method]
                    return {
                        "matched": True,
                        "target_type": "payment_option",
                        "option_id": opt["id"],
                        "payment_intent_id": opt["payment_intent_id"],
                        "parent_public_id": opt["intent_public_id"],
                        "channel": channel,
                        "method": method,
                        "confidence": "strong",
                        "reason": f"option_matched_by_{method}",
                        "conflicts": [],
                    }

        # Fall back to legacy intent matching (ERIP-only intents without options table rows)
        legacy = self.match_bepaid_transaction_to_intent(transaction)
        if legacy.get("matched") or legacy.get("confidence") == "conflict":
            return {**legacy, "target_type": "legacy_intent"}

        return {
            "matched": False,
            "target_type": "none",
            "option_id": None, "payment_intent_id": None, "parent_public_id": None,
            "intent_id": None, "intent_public_id": None,
            "method": None, "confidence": "none",
            "reason": "no_option_or_intent_matched",
            "conflicts": [],
        }

    def mark_bepaid_transaction_signature_verified(
        self,
        tx_id: int,
        *,
        verified_at: str,
        verification_method: str = "rsa_pkcs1v15_sha256",
    ) -> None:
        """Persist that the webhook signature passed cryptographic verification.

        Called BEFORE matching — signature verification is independent of whether
        we can find a matching intent or option. Does not touch intent/option fields.
        """
        with self._connect() as conn:
            conn.execute(
                "UPDATE bepaid_transactions SET webhook_verified=1, updated_at=? WHERE id=?",
                (verified_at, int(tx_id)),
            )

    def mark_bepaid_transaction_provider_verified(
        self,
        tx_id: int,
        *,
        verified_at: str,
        verification_method: str = "checkout_status_query",
    ) -> None:
        """Set provider_verified=1 via authenticated checkout status query.

        Separate from webhook_verified (RSA signature). Never sets webhook_verified.
        """
        with self._connect() as conn:
            conn.execute(
                """UPDATE bepaid_transactions SET
                   provider_verified=1, provider_verified_at=?, provider_verification_method=?,
                   updated_at=? WHERE id=?""",
                (verified_at, verification_method, verified_at, int(tx_id)),
            )

    def get_bepaid_transaction_by_id(self, tx_id: int) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM bepaid_transactions WHERE id=?", (int(tx_id),)
            ).fetchone()
        return dict(row) if row else None

    def list_unmatched_bepaid_transactions(self, limit: int = 50) -> list[dict]:
        """Successful, non-test, unlinked transactions eligible for the admin reconcile flow.

        Trust paths (OR):
        - webhook_verified=1: Content-Signature passed RSA verification
        - provider_verified=1: authenticated GET checkout status query confirmed payment

        Both paths require status='successful', test=0, transaction_uid IS NOT NULL,
        and no existing intent match (intent_public_id is empty).
        """
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM bepaid_transactions
                   WHERE status = 'successful'
                     AND test = 0
                     AND (webhook_verified = 1 OR provider_verified = 1)
                     AND (transaction_uid IS NOT NULL AND transaction_uid != '')
                     AND (intent_public_id IS NULL OR intent_public_id = '')
                   ORDER BY received_at DESC LIMIT ?""",
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_bepaid_recovery_queue(self, limit: int = 50) -> list[dict]:
        """Transactions matched to an intent but the intent is still unpaid.

        These are successful, verified, non-test transactions that have
        intent_public_id set but whose intent has NOT been marked paid yet.
        Added in v7.0.93.2.9: covers the case where a webhook previously
        failed with cannot_mark_paid_from_status (bug fixed in v7.0.93.2.8)
        but the transaction already had intent_public_id stored.

        Returns rows joined with payment_intents so the caller gets both the
        transaction fields and intent_status / intent_amount_minor.
        """
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT bt.*,
                       pi.status          AS intent_status,
                       pi.amount_minor    AS intent_amount_minor,
                       pi.paid_transaction_uid AS intent_paid_uid
                FROM bepaid_transactions bt
                JOIN payment_intents pi
                  ON pi.public_id = bt.intent_public_id
                WHERE bt.status = 'successful'
                  AND bt.test = 0
                  AND (bt.webhook_verified = 1 OR bt.provider_verified = 1)
                  AND (bt.transaction_uid IS NOT NULL AND bt.transaction_uid != '')
                  AND (bt.intent_public_id IS NOT NULL AND bt.intent_public_id != '')
                  AND pi.status NOT IN ('paid', 'posted_to_moyklass', 'cancelled')
                ORDER BY bt.received_at DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 200)),),
            ).fetchall()
        return [dict(r) for r in rows]

    def payment_intent_mark_paid(
        self,
        public_id: str,
        *,
        tx_uid: str,
        amount_minor: int,
        currency: str,
        paid_at: str,
        channel: str = "",
        tracking_id: Optional[str] = None,
        order_id: Optional[str] = None,
        account_number: Optional[str] = None,
        verified: bool = False,
        match_method: str = "",
    ) -> dict:
        """Mark a payment_intent as paid (legacy / non-option path).

        State machine: bepaid_created | awaiting_payment | partial_ready → paid.
        awaiting_payment added in v7.0.93.2.8: intents go to awaiting_payment when
        ERIP is created via prepare-options; the legacy webhook match path must also
        honour that state.

        Returns:
          {ok: True, marked_paid: True, intent: {...}}  — success
          {ok: True, idempotent: True, intent: {...}}   — duplicate tx_uid
          {ok: False, conflict: True, reason: ..., intent: {...}} — already paid differently
          {ok: False, wrong_state: True, reason: ..., intent: {...}} — wrong source status
        """
        _allowed_source = ("bepaid_created", "awaiting_payment", "partial_ready")
        now = now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM payment_intents WHERE public_id=?", (public_id,)
            ).fetchone()
            if not row:
                return {"ok": False, "error": "intent_not_found"}
            pi = dict(row)

            if pi["status"] == "paid":
                existing_uid = str(pi.get("paid_transaction_uid") or "").strip()
                if existing_uid == tx_uid:
                    return {"ok": True, "idempotent": True, "intent": pi}
                return {
                    "ok": False, "conflict": True,
                    "reason": f"already_paid_with_uid:{existing_uid}",
                    "intent": pi,
                }

            if pi["status"] not in _allowed_source:
                return {
                    "ok": False, "wrong_state": True,
                    "reason": f"cannot_mark_paid_from_status:{pi['status']}",
                    "intent": pi,
                }

            conn.execute(
                """
                UPDATE payment_intents SET
                    status               = 'paid',
                    paid_at              = ?,
                    paid_amount_minor    = ?,
                    paid_currency        = ?,
                    paid_transaction_uid = ?,
                    paid_channel         = ?,
                    paid_tracking_id     = ?,
                    paid_order_id        = ?,
                    paid_account_number  = ?,
                    webhook_verified     = ?,
                    webhook_match_method = ?,
                    payment_state_reason = 'paid_via_bepaid_webhook',
                    last_webhook_at      = ?,
                    updated_at           = ?
                WHERE public_id = ? AND status IN ('bepaid_created', 'awaiting_payment', 'partial_ready')
                """,
                (
                    paid_at, int(amount_minor), str(currency or "BYN"),
                    tx_uid, channel or None, tracking_id, order_id, account_number,
                    1 if verified else 0, match_method, now, now, public_id,
                ),
            )
            changes = conn.execute("SELECT changes()").fetchone()[0]
            if changes == 0:
                row2 = conn.execute(
                    "SELECT * FROM payment_intents WHERE public_id=?", (public_id,)
                ).fetchone()
                pi2 = dict(row2) if row2 else {}
                if pi2.get("status") == "paid" and str(pi2.get("paid_transaction_uid") or "") == tx_uid:
                    return {"ok": True, "idempotent": True, "intent": pi2}
                return {"ok": False, "conflict": True, "reason": "concurrent_update", "intent": pi2}
            row_after = conn.execute(
                "SELECT * FROM payment_intents WHERE public_id=?", (public_id,)
            ).fetchone()
        return {"ok": True, "marked_paid": True, "intent": dict(row_after) if row_after else {}}

    def payment_intent_update_last_webhook_at(self, public_id: str) -> None:
        now = now_iso()
        with self._connect() as conn:
            conn.execute(
                "UPDATE payment_intents SET last_webhook_at=?, updated_at=? WHERE public_id=?",
                (now, now, public_id),
            )

    def bepaid_transaction_link_intent(
        self,
        tx_id: int,
        *,
        intent_id: int,
        intent_public_id: str,
        match_method: str,
        confidence: str,
        reason: str,
        now: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE bepaid_transactions SET
                    payment_intent_id    = ?,
                    intent_public_id     = ?,
                    webhook_match_method = ?,
                    match_confidence     = ?,
                    match_reason         = ?,
                    processed_at         = ?,
                    updated_at           = ?
                WHERE id = ?
                """,
                (
                    intent_id, intent_public_id, match_method, confidence,
                    reason, now, now, tx_id,
                ),
            )

    def log_payment_webhook_audit(
        self,
        event_type: str,
        *,
        bepaid_tx_id: Optional[int] = None,
        payment_intent_id: Optional[int] = None,
        intent_public_id: Optional[str] = None,
        transaction_uid: Optional[str] = None,
        shop_type: Optional[str] = None,
        status: Optional[str] = None,
        amount_minor: Optional[int] = None,
        currency: Optional[str] = None,
        match_method: Optional[str] = None,
        match_confidence: Optional[str] = None,
        reason: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> None:
        import json as _json
        now = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO payment_webhook_audit
                    (created_at, event_type, bepaid_tx_id, payment_intent_id, intent_public_id,
                     transaction_uid, shop_type, status, amount_minor, currency,
                     match_method, match_confidence, reason, details_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    now, event_type, bepaid_tx_id, payment_intent_id, intent_public_id,
                    transaction_uid, shop_type, status, amount_minor, currency,
                    match_method, match_confidence, reason,
                    _json.dumps(details, ensure_ascii=False) if details is not None else None,
                ),
            )

    def list_payment_webhook_audit(
        self, intent_public_id: Optional[str] = None, limit: int = 100
    ) -> list[dict]:
        params: list = []
        where = ""
        if intent_public_id:
            where = "WHERE intent_public_id=?"
            params.append(intent_public_id)
        params.append(max(1, min(int(limit), 500)))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM payment_webhook_audit {where} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    # ── v7.0.92: MoyKlass manual payment posting ──────────────────────────────

    def payment_intent_claim_moyklass_post(self, public_id: str, by_user: str) -> bool:
        """Atomically claim this intent for MoyKlass posting.

        Sets mk_posting_status='claiming' only if status='paid' and mk_posting_status is NULL/empty.
        Returns True if claim succeeded (rowcount==1). Concurrent callers get False.
        """
        from utils import now_iso
        now = now_iso()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE payment_intents
                SET mk_posting_status = 'claiming',
                    mk_posting_at     = ?,
                    mk_posting_by     = ?,
                    updated_at        = ?
                WHERE public_id = ?
                  AND status = 'paid'
                  AND (mk_posting_status IS NULL OR mk_posting_status = '')
                """,
                (now, str(by_user)[:200], now, public_id),
            )
            return cursor.rowcount == 1

    def payment_intent_release_moyklass_claim(self, public_id: str, error: str = "") -> None:
        """Release a 'claiming' mk_posting_status back to NULL after a definite 4xx failure."""
        from utils import now_iso
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE payment_intents
                SET mk_posting_status = 'failed',
                    mk_posting_error  = ?,
                    updated_at        = ?
                WHERE public_id = ? AND mk_posting_status = 'claiming'
                """,
                (str(error)[:1000], now_iso(), public_id),
            )

    def payment_intent_mark_posted_to_moyklass(
        self,
        public_id: str,
        *,
        mk_payment_id: int,
        posted_at: str,
        fingerprint: str,
        invoice_snapshot_json: str = "",
    ) -> None:
        """Transition intent to posted_to_moyklass after confirmed MoyKlass POST."""
        from utils import now_iso
        now = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE payment_intents
                SET status                         = 'posted_to_moyklass',
                    mk_payment_id                  = ?,
                    mk_posted_at                   = ?,
                    mk_posting_status              = 'posted',
                    mk_posting_fingerprint         = ?,
                    mk_posting_invoice_snapshot_json = ?,
                    mk_posting_error               = NULL,
                    updated_at                     = ?
                WHERE public_id = ?
                """,
                (mk_payment_id, posted_at, fingerprint, invoice_snapshot_json, now, public_id),
            )

    def payment_intent_mark_moyklass_ambiguous(self, public_id: str, reason: str) -> None:
        """Mark mk_posting as ambiguous (timeout/5xx after sending) — blocks retry."""
        from utils import now_iso
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE payment_intents
                SET mk_posting_status = 'ambiguous',
                    mk_posting_error  = ?,
                    updated_at        = ?
                WHERE public_id = ?
                """,
                (str(reason)[:1000], now_iso(), public_id),
            )

    def log_moyklass_post_audit(
        self,
        event_type: str,
        *,
        intent_public_id: Optional[str] = None,
        payment_intent_id: Optional[int] = None,
        transaction_uid: Optional[str] = None,
        mk_user_id: Optional[int] = None,
        mk_invoice_id: Optional[str] = None,
        mk_user_subscription_id: Optional[str] = None,
        amount_minor: Optional[int] = None,
        currency: Optional[str] = None,
        invoice_remaining_minor: Optional[int] = None,
        mk_payment_id: Optional[int] = None,
        fingerprint: Optional[str] = None,
        result: Optional[str] = None,
        reason: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> None:
        import json as _json
        now = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO payment_mk_post_audit
                    (created_at, event_type, intent_public_id, payment_intent_id, transaction_uid,
                     mk_user_id, mk_invoice_id, mk_user_subscription_id,
                     amount_minor, currency, invoice_remaining_minor,
                     mk_payment_id, fingerprint, result, reason, details_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    now, event_type, intent_public_id, payment_intent_id, transaction_uid,
                    mk_user_id, mk_invoice_id, mk_user_subscription_id,
                    amount_minor, currency, invoice_remaining_minor,
                    mk_payment_id, fingerprint, result, reason,
                    _json.dumps(details, ensure_ascii=False) if details is not None else None,
                ),
            )

    def list_moyklass_post_audit(
        self, intent_public_id: Optional[str] = None, limit: int = 50
    ) -> list[dict]:
        params: list = []
        where = ""
        if intent_public_id:
            where = "WHERE intent_public_id=?"
            params.append(intent_public_id)
        params.append(max(1, min(int(limit), 200)))
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM payment_mk_post_audit {where} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        return [dict(r) for r in rows]

    # ─── v7.0.92.2 dual-channel payment options ───────────────────────────────

    def create_payment_intent_option(
        self,
        *,
        payment_intent_id: int,
        intent_public_id: str,
        channel: str,
        shop_type: str,
        bepaid_order_id: Optional[str] = None,
        bepaid_tracking_id: Optional[str] = None,
        bepaid_uid: Optional[str] = None,
        bepaid_account_number: Optional[str] = None,
        payment_url: Optional[str] = None,
        qr_code_raw: Optional[str] = None,
        expires_at: Optional[str] = None,
    ) -> dict:
        """Create a new payment option row (one per channel: erip or acquiring)."""
        now = now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO payment_intent_options
                    (payment_intent_id, intent_public_id, channel, shop_type,
                     bepaid_order_id, bepaid_tracking_id, bepaid_uid, bepaid_account_number,
                     payment_url, qr_code_raw, expires_at, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    payment_intent_id, intent_public_id, channel, shop_type,
                    bepaid_order_id, bepaid_tracking_id, bepaid_uid, bepaid_account_number,
                    payment_url, qr_code_raw, expires_at, now, now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM payment_intent_options WHERE id=?", (cur.lastrowid,)
            ).fetchone()
        return dict(row) if row else {}

    def get_options_for_intent(self, intent_public_id: str) -> list:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM payment_intent_options WHERE intent_public_id=? ORDER BY id",
                (intent_public_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_option_by_channel(self, intent_public_id: str, channel: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM payment_intent_options WHERE intent_public_id=? AND channel=? ORDER BY id DESC LIMIT 1",
                (intent_public_id, channel),
            ).fetchone()
        return dict(row) if row else None

    def get_option_by_provider_ref(
        self,
        *,
        bepaid_tracking_id: Optional[str] = None,
        bepaid_order_id: Optional[str] = None,
        bepaid_uid: Optional[str] = None,
        bepaid_account_number: Optional[str] = None,
    ) -> Optional[dict]:
        """Find a payment_intent_option by any known bePaid provider reference."""
        with self._connect() as conn:
            for col, val in [
                ("bepaid_tracking_id", bepaid_tracking_id),
                ("bepaid_order_id", bepaid_order_id),
                ("bepaid_uid", bepaid_uid),
                ("bepaid_account_number", bepaid_account_number),
            ]:
                if not val:
                    continue
                row = conn.execute(
                    f"SELECT * FROM payment_intent_options WHERE {col}=? ORDER BY id DESC LIMIT 1",
                    (val,),
                ).fetchone()
                if row:
                    return dict(row)
        return None

    def mark_option_paid(
        self,
        option_id: int,
        *,
        tx_uid: str,
        paid_at: str,
        amount_minor: int,
        currency: str,
        tracking_id: Optional[str] = None,
        order_id: Optional[str] = None,
        bepaid_uid: Optional[str] = None,
    ) -> dict:
        """Transition option status: created → paid. Idempotent on same tx_uid."""
        now = now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM payment_intent_options WHERE id=?", (option_id,)
            ).fetchone()
            if not row:
                return {"ok": False, "error": "option_not_found"}
            opt = dict(row)
            if opt["status"] == "paid":
                if str(opt.get("transaction_uid") or "") == tx_uid:
                    return {"ok": True, "idempotent": True, "option": opt}
                return {"ok": False, "conflict": True, "reason": "already_paid_different_uid", "option": opt}
            if opt["status"] not in ("created",):
                return {
                    "ok": False, "wrong_state": True,
                    "reason": f"cannot_pay_from_status:{opt['status']}",
                    "option": opt,
                }
            conn.execute(
                """
                UPDATE payment_intent_options SET
                    status = 'paid',
                    transaction_uid = ?,
                    paid_at = ?,
                    paid_amount_minor = ?,
                    paid_currency = ?,
                    paid_tracking_id = ?,
                    paid_order_id = ?,
                    bepaid_uid = COALESCE(?, bepaid_uid),
                    updated_at = ?
                WHERE id = ? AND status = 'created'
                """,
                (tx_uid, paid_at, int(amount_minor), str(currency or "BYN"),
                 tracking_id, order_id, bepaid_uid, now, option_id),
            )
            changes = conn.execute("SELECT changes()").fetchone()[0]
            if changes == 0:
                row2 = conn.execute(
                    "SELECT * FROM payment_intent_options WHERE id=?", (option_id,)
                ).fetchone()
                opt2 = dict(row2) if row2 else {}
                if opt2.get("status") == "paid" and str(opt2.get("transaction_uid") or "") == tx_uid:
                    return {"ok": True, "idempotent": True, "option": opt2}
                return {"ok": False, "conflict": True, "reason": "concurrent_update", "option": opt2}
            row_after = conn.execute(
                "SELECT * FROM payment_intent_options WHERE id=?", (option_id,)
            ).fetchone()
        return {"ok": True, "marked_paid": True, "option": dict(row_after) if row_after else {}}

    def mark_option_failed(
        self,
        option_id: int,
        *,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> dict:
        now = now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM payment_intent_options WHERE id=?", (option_id,)
            ).fetchone()
            if not row:
                return {"ok": False, "error": "option_not_found"}
            opt = dict(row)
            if opt["status"] not in ("created",):
                return {
                    "ok": False, "wrong_state": True,
                    "reason": f"cannot_fail_from_status:{opt['status']}",
                    "option": opt,
                }
            conn.execute(
                """
                UPDATE payment_intent_options SET
                    status = 'failed',
                    error_code = ?,
                    error_message = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (error_code, error_message, now, option_id),
            )
            row_after = conn.execute(
                "SELECT * FROM payment_intent_options WHERE id=?", (option_id,)
            ).fetchone()
        return {"ok": True, "marked_failed": True, "option": dict(row_after) if row_after else {}}

    def mark_option_expired(self, option_id: int) -> dict:
        now = now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM payment_intent_options WHERE id=?", (option_id,)
            ).fetchone()
            if not row:
                return {"ok": False, "error": "option_not_found"}
            opt = dict(row)
            if opt["status"] not in ("created",):
                return {
                    "ok": False, "wrong_state": True,
                    "reason": f"cannot_expire_from_status:{opt['status']}",
                    "option": opt,
                }
            conn.execute(
                "UPDATE payment_intent_options SET status='expired', updated_at=? WHERE id=?",
                (now, option_id),
            )
            row_after = conn.execute(
                "SELECT * FROM payment_intent_options WHERE id=?", (option_id,)
            ).fetchone()
        return {"ok": True, "marked_expired": True, "option": dict(row_after) if row_after else {}}

    def update_option_checkout(
        self,
        option_id: int,
        *,
        checkout_token: str,
        payment_url: str,
    ) -> dict:
        """Set checkout_token and payment_url on an existing option row (v7.0.92.3)."""
        now = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE payment_intent_options
                SET checkout_token = ?, payment_url = ?, updated_at = ?
                WHERE id = ?
                """,
                (checkout_token, payment_url, now, option_id),
            )
            row = conn.execute(
                "SELECT * FROM payment_intent_options WHERE id=?", (option_id,)
            ).fetchone()
        return dict(row) if row else {}

    def payment_intent_update_status(self, public_id: str, new_status: str) -> bool:
        """Update the status of a payment intent. Returns True if a row was changed."""
        now = now_iso()
        with self._connect() as conn:
            conn.execute(
                "UPDATE payment_intents SET status=?, updated_at=? WHERE public_id=?",
                (new_status, now, public_id),
            )
            changed = conn.execute("SELECT changes()").fetchone()[0]
        return changed == 1

    def supersede_sibling_options(self, intent_public_id: str, winning_option_id: int) -> int:
        """Mark all non-winning, non-paid options as 'superseded'. Returns row count changed."""
        now = now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE payment_intent_options SET
                    status = 'superseded',
                    updated_at = ?
                WHERE intent_public_id = ?
                  AND id != ?
                  AND status NOT IN ('paid', 'superseded')
                """,
                (now, intent_public_id, winning_option_id),
            )
            changes = conn.execute("SELECT changes()").fetchone()[0]
        return changes

    def payment_intent_mark_paid_via_option(
        self,
        public_id: str,
        *,
        option_id: int,
        channel: str,
        tx_uid: str,
        amount_minor: int,
        currency: str,
        paid_at: str,
        tracking_id: Optional[str] = None,
        order_id: Optional[str] = None,
        account_number: Optional[str] = None,
        verified: bool = False,
        match_method: str = "",
    ) -> dict:
        """Atomic: mark option paid, supersede siblings, mark parent intent paid with paid_channel.

        Returns:
          {ok: True, marked_paid: True, intent: {...}, siblings_superseded: N}
          {ok: True, idempotent: True, intent: {...}}     — same tx_uid already recorded
          {ok: False, double_payment: True, ...}          — parent already paid via different tx
          {ok: False, wrong_state: True, ...}             — parent not in bepaid_created
          {ok: False, error: ..., ...}                    — other errors
        """
        now = now_iso()

        with self._connect() as conn:
            pi_row = conn.execute(
                "SELECT * FROM payment_intents WHERE public_id=?", (public_id,)
            ).fetchone()
        if not pi_row:
            return {"ok": False, "error": "intent_not_found"}
        pi = dict(pi_row)

        if pi["status"] == "paid":
            existing_uid = str(pi.get("paid_transaction_uid") or "").strip()
            if existing_uid == tx_uid:
                return {"ok": True, "idempotent": True, "intent": pi}
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE payment_intents SET
                        status = 'double_payment_requires_check',
                        payment_state_reason = ?,
                        last_webhook_at = ?,
                        updated_at = ?
                    WHERE public_id = ? AND status = 'paid'
                    """,
                    (
                        f"double_payment:channel={channel}:tx_uid={tx_uid}",
                        now, now, public_id,
                    ),
                )
            return {
                "ok": False, "double_payment": True,
                "reason": f"already_paid_with_uid:{existing_uid}",
                "intent": pi,
            }

        if pi["status"] == "double_payment_requires_check":
            return {"ok": False, "double_payment": True, "reason": "already_flagged_double", "intent": pi}

        if pi["status"] not in ("bepaid_created", "awaiting_payment", "partial_ready"):
            return {
                "ok": False, "wrong_state": True,
                "reason": f"cannot_mark_paid_from_status:{pi['status']}",
                "intent": pi,
            }

        opt_result = self.mark_option_paid(
            option_id,
            tx_uid=tx_uid,
            paid_at=paid_at,
            amount_minor=amount_minor,
            currency=currency,
            tracking_id=tracking_id,
            order_id=order_id,
        )
        if not opt_result.get("ok"):
            if opt_result.get("idempotent"):
                with self._connect() as conn:
                    row_after = conn.execute(
                        "SELECT * FROM payment_intents WHERE public_id=?", (public_id,)
                    ).fetchone()
                return {"ok": True, "idempotent": True, "intent": dict(row_after) if row_after else {}}
            return {"ok": False, "error": "option_mark_failed", "detail": opt_result}

        siblings_updated = self.supersede_sibling_options(public_id, option_id)

        with self._connect() as conn:
            conn.execute(
                """
                UPDATE payment_intents SET
                    status               = 'paid',
                    paid_at              = ?,
                    paid_amount_minor    = ?,
                    paid_currency        = ?,
                    paid_transaction_uid = ?,
                    paid_tracking_id     = ?,
                    paid_order_id        = ?,
                    paid_account_number  = ?,
                    paid_channel         = ?,
                    paid_option_id       = ?,
                    webhook_verified     = ?,
                    webhook_match_method = ?,
                    payment_state_reason = 'paid_via_bepaid_webhook',
                    last_webhook_at      = ?,
                    updated_at           = ?
                WHERE public_id = ? AND status IN ('bepaid_created', 'awaiting_payment', 'partial_ready')
                """,
                (
                    paid_at, int(amount_minor), str(currency or "BYN"),
                    tx_uid, tracking_id, order_id, account_number,
                    channel, option_id,
                    1 if verified else 0, match_method, now, now,
                    public_id,
                ),
            )
            changes = conn.execute("SELECT changes()").fetchone()[0]
            if changes == 0:
                row2 = conn.execute(
                    "SELECT * FROM payment_intents WHERE public_id=?", (public_id,)
                ).fetchone()
                pi2 = dict(row2) if row2 else {}
                if pi2.get("status") == "paid" and str(pi2.get("paid_transaction_uid") or "") == tx_uid:
                    return {"ok": True, "idempotent": True, "intent": pi2}
                return {"ok": False, "conflict": True, "reason": "concurrent_update", "intent": pi2}
            row_after = conn.execute(
                "SELECT * FROM payment_intents WHERE public_id=?", (public_id,)
            ).fetchone()
        return {
            "ok": True, "marked_paid": True,
            "intent": dict(row_after) if row_after else {},
            "siblings_superseded": siblings_updated,
        }

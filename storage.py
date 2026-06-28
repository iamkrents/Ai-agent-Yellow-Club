from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Optional

from utils import now_iso


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

    # --- Food module: menus ---

    def create_food_menu(self, menu_date: str, title: Optional[str], deadline_at: Optional[str], created_by: Optional[int] = None) -> dict[str, Any]:
        now = now_iso()
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO food_menus(created_at, updated_at, created_by, menu_date, title, deadline_at, status) VALUES (?, ?, ?, ?, ?, ?, 'draft')",
                (now, now, created_by, str(menu_date or "").strip(), str(title or "").strip() or None, str(deadline_at or "").strip() or None),
            )
            row = conn.execute("SELECT * FROM food_menus WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(row) if row else {}

    def list_food_menus(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT m.*, COUNT(i.id) AS items_count
                FROM food_menus m
                LEFT JOIN food_items i ON i.menu_id = m.id AND i.is_available = 1
                WHERE m.status != 'archived'
                GROUP BY m.id
                ORDER BY m.menu_date ASC, m.id ASC
            """).fetchall()
        return [dict(r) for r in rows]

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
        allowed = {"menu_date", "title", "deadline_at", "notes"}
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
        item_ids: list,
        status: str = "submitted",
    ) -> dict[str, Any]:
        now = now_iso()
        pid = str(parent_telegram_id)
        sid = str(mk_student_id)
        mid = int(menu_id)
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
            safe_ids = [int(iid) for iid in (item_ids or [])]
            price_map: dict[int, float] = {}
            if safe_ids:
                id_ph = ", ".join("?" for _ in safe_ids)
                price_rows = conn.execute(
                    f"SELECT id, price FROM food_items WHERE id IN ({id_ph})", safe_ids
                ).fetchall()
                price_map = {r["id"]: float(r["price"] or 0) for r in price_rows}
            for iid in safe_ids:
                conn.execute(
                    "INSERT INTO food_order_items(created_at, order_id, item_id, quantity, price_snapshot) VALUES(?,?,?,1,?)",
                    (now, order_id, iid, price_map.get(iid, 0.0)),
                )
            row = conn.execute("SELECT * FROM food_orders WHERE id=?", (order_id,)).fetchone()
            order_items = conn.execute(
                "SELECT oi.item_id, fi.name, fi.category, fi.weight FROM food_order_items oi JOIN food_items fi ON fi.id=oi.item_id WHERE oi.order_id=?",
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
                "SELECT oi.item_id, fi.name, fi.category, fi.weight FROM food_order_items oi JOIN food_items fi ON fi.id=oi.item_id WHERE oi.order_id=?",
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
                    "SELECT oi.item_id, fi.name, fi.category, fi.weight FROM food_order_items oi JOIN food_items fi ON fi.id=oi.item_id WHERE oi.order_id=?",
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
                    "SELECT oi.item_id, fi.name, fi.category, fi.weight FROM food_order_items oi JOIN food_items fi ON fi.id=oi.item_id WHERE oi.order_id=?",
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
                item_details = [{"item_id": i["item_id"], "name": i["name"], "category": i["category"], "weight": i["weight"]} for i in order.get("items", [])]
                by_children.append({"childName": ch["full_name"], "status": "submitted", "items": [i["name"] for i in order.get("items", [])], "itemDetails": item_details, "mk_student_id": sid, "groupCode": group_code, "groupSource": group_source})
            elif order and order["status"] == "skipped":
                skipped += 1
                by_children.append({"childName": ch["full_name"], "status": "skipped", "items": [], "itemDetails": [], "mk_student_id": sid, "groupCode": group_code, "groupSource": group_source})
            else:
                missing += 1
                by_children.append({"childName": ch["full_name"], "status": "missing", "items": [], "itemDetails": [], "mk_student_id": sid, "groupCode": group_code, "groupSource": group_source})
                missing_children.append(ch["full_name"])
        item_counts: dict[int, int] = {}
        item_children: dict[int, list[str]] = {}
        for ch in all_children:
            order = orders_by_student.get(ch["mk_student_id"])
            if order and order["status"] == "submitted":
                for it in order.get("items", []):
                    iid = it["item_id"]
                    item_counts[iid] = item_counts.get(iid, 0) + 1
                    item_children.setdefault(iid, []).append(ch["full_name"])
        by_items = [
            {"item_id": item["id"], "category": item["category"], "name": item["name"], "weight": item["weight"], "count": item_counts.get(item["id"], 0), "children": item_children.get(item["id"], [])}
            for item in all_items
            if item_counts.get(item["id"], 0) > 0
        ]
        return {
            "menu": menu,
            "totalChildren": len(all_children),
            "submittedOrders": submitted,
            "skippedOrders": skipped,
            "missingOrders": missing,
            "byItems": by_items,
            "byChildren": by_children,
            "missingChildren": missing_children,
        }

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
                    "SELECT * FROM food_menus WHERE date >= ? AND date <= ? ORDER BY date",
                    (start_date, end_date),
                ).fetchall()
            elif start_date:
                menu_rows = conn.execute(
                    "SELECT * FROM food_menus WHERE date >= ? ORDER BY date",
                    (start_date,),
                ).fetchall()
            elif end_date:
                menu_rows = conn.execute(
                    "SELECT * FROM food_menus WHERE date <= ? ORDER BY date",
                    (end_date,),
                ).fetchall()
            else:
                menu_rows = conn.execute("SELECT * FROM food_menus ORDER BY date").fetchall()
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
            menu_date = menu.get("date", "")
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
        }
        with self._connect() as conn:
            old_row = conn.execute("SELECT * FROM lesson_snapshots WHERE lesson_id=?", (lesson_id,)).fetchone()
            if not old_row:
                conn.execute(
                    """
                    INSERT INTO lesson_snapshots(lesson_id, created_at, updated_at, last_seen_at, lesson_date, lesson_time, group_name, lesson_topic, teacher_ids, teacher_names, fingerprint, raw_preview)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (lesson_id, now, now, now, current["lesson_date"], current["lesson_time"], current["group_name"], current["lesson_topic"], current["teacher_ids"], current["teacher_names"], current["fingerprint"], current["raw_preview"]),
                )
                return "new", None, current
            previous = dict(old_row)
            event = "changed" if str(previous.get("fingerprint") or "") != current["fingerprint"] else "unchanged"
            conn.execute(
                """
                UPDATE lesson_snapshots
                SET updated_at=?, last_seen_at=?, lesson_date=?, lesson_time=?, group_name=?, lesson_topic=?, teacher_ids=?, teacher_names=?, fingerprint=?, raw_preview=?
                WHERE lesson_id=?
                """,
                (now if event == "changed" else previous.get("updated_at") or now, now, current["lesson_date"], current["lesson_time"], current["group_name"], current["lesson_topic"], current["teacher_ids"], current["teacher_names"], current["fingerprint"], current["raw_preview"], lesson_id),
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

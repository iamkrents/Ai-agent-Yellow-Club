#!/usr/bin/env python3
"""
Production reset script for Yellow Club food module.

Clears all food-module test data before going live:
  - food_order_items
  - food_orders
  - food_items
  - food_menus
  - parent_child_links  (parent-to-child bindings and link codes)
  - camp_children       (cached children from MoyKlass)

Does NOT touch:
  - staff_users / roles / admin / owner / methodist / teacher / intern
  - messages, facts, settings, mk_audit
  - lesson data (teacher_lesson_control, teacher_prep_results, lesson_snapshots)
  - intern workflows (intern_observations, intern_works, intern_demo_bookings)
  - teacher_tasks, teacher_profiles, teacher_work_schedule
  - client_manager_tasks, client_task_events, notification_log
  - trial_requests, decisions, feedback
  - .env file
  - Any MoyKlass / Notion / Telegram config

Usage:
  python scripts/reset_food_module_for_production.py            # dry-run
  python scripts/reset_food_module_for_production.py --dry-run  # dry-run (explicit)
  python scripts/reset_food_module_for_production.py --confirm  # REAL delete
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Locate project root and import config
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from config import load_settings
except ImportError as exc:
    print(f"[ERROR] Cannot import config: {exc}")
    print(f"        Make sure you run this script from the project root:")
    print(f"        python scripts/reset_food_module_for_production.py")
    sys.exit(1)

# ---------------------------------------------------------------------------
# Tables to clear (order matters: children before parents due to FK)
# ---------------------------------------------------------------------------
FOOD_TABLES: list[tuple[str, str]] = [
    ("food_order_items",          "всегда"),
    ("food_orders",               "всегда"),
    ("food_items",                "всегда"),
    ("food_menus",                "всегда"),
    ("food_reminder_log",         "логи напоминаний (сбрасывает cooldown и флаги повторной отправки)"),
    ("food_menu_notification_log","логи уведомлений о публикации (сбрасывает флаг 'уже уведомляли')"),
    ("parent_child_links",        "все привязки родителей и коды"),
    ("camp_children",             "кэш детей из МойКласс"),
]

SKIPPED_TABLES = [
    "staff_users", "messages", "facts", "settings", "mk_audit",
    "trial_requests", "decisions", "feedback",
    "teacher_lesson_control", "teacher_prep_results", "lesson_snapshots",
    "intern_observations", "intern_works", "intern_demo_bookings",
    "teacher_profiles", "teacher_tasks", "teacher_work_schedule",
    "client_manager_tasks", "client_task_events", "notification_log",
]


def _count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except sqlite3.OperationalError:
        return -1  # table does not exist yet


def _backup(db_path: Path) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backups_dir = PROJECT_ROOT / "backups"
    backups_dir.mkdir(exist_ok=True)
    dest = backups_dir / f"food_reset_{ts}.sqlite"
    shutil.copy2(db_path, dest)
    return dest


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset food module data for production.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true", default=False,
                       help="Show what would be deleted (default)")
    group.add_argument("--confirm", action="store_true", default=False,
                       help="Actually delete the data (no undo without backup)")
    args = parser.parse_args()

    is_real = args.confirm
    print()
    print("=" * 60)
    print("  Yellow Club · Food Module Production Reset")
    print("=" * 60)

    # --- Load config to find DB path ---
    try:
        settings = load_settings()
    except Exception as exc:
        print(f"\n[ERROR] Failed to load settings: {exc}")
        sys.exit(1)

    db_path: Path = settings.db_path
    if not db_path.exists():
        print(f"\n[ERROR] Database not found: {db_path}")
        sys.exit(1)

    print(f"\nDatabase : {db_path}")
    print(f"Mode     : {'РЕАЛЬНОЕ УДАЛЕНИЕ' if is_real else 'DRY-RUN (ничего не удаляется)'}")
    print()

    # --- Connect and count rows ---
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    print("Таблицы food module (будут очищены):")
    counts: dict[str, int] = {}
    total_rows = 0
    for table, note in FOOD_TABLES:
        n = _count(conn, table)
        counts[table] = n
        flag = "   [нет таблицы]" if n < 0 else ""
        print(f"  {table:<30}  {max(n,0):>6} строк  — {note}{flag}")
        if n > 0:
            total_rows += n

    print()
    print("Таблицы НЕ затрагиваются:")
    for t in SKIPPED_TABLES:
        n = _count(conn, t)
        print(f"  {t:<30}  {max(n,0):>6} строк")

    print()
    print(f"Итого строк для удаления: {total_rows}")

    if not is_real:
        print()
        print("▶ Это DRY-RUN. Данные НЕ удалены.")
        print("  Для реального удаления запустите:")
        print("    python scripts/reset_food_module_for_production.py --confirm")
        conn.close()
        return

    # --- Real delete ---
    print()
    backup_path = _backup(db_path)
    print(f"✅ Backup создан: {backup_path}")
    print()

    try:
        with conn:
            for table, _ in FOOD_TABLES:
                if counts.get(table, -1) < 0:
                    print(f"  SKIP {table} (таблица не существует)")
                    continue
                conn.execute(f"DELETE FROM {table}")
                print(f"  DELETE {table} — {counts[table]} строк удалено")
            conn.execute("VACUUM")
        print()
        print("✅ Очистка завершена.")
    except Exception as exc:
        print(f"\n[ERROR] Ошибка при удалении: {exc}")
        print(f"  Backup сохранён: {backup_path}")
        conn.close()
        sys.exit(1)

    conn.close()

    print()
    print("=" * 60)
    print("  После reset выполните:")
    print("=" * 60)
    print()
    print("  1. Запустите Mini App server.")
    print("  2. Откройте Админ → Питание · диагностика.")
    print("  3. Запустите «Запустить диагностику» (синхронизация детей из МойКласс)")
    print("     и поставьте галочку «Сохранить найденных детей в БД».")
    print("  4. Откройте Питание · дети.")
    print("  5. Нажмите «Сгенерировать коды» детям.")
    print("  6. Откройте Питание · меню.")
    print("  7. Создайте меню на текущий день.")
    print("  8. Опубликуйте меню.")
    print()


if __name__ == "__main__":
    main()

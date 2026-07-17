from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _split_ints(value: str) -> List[int]:
    result: List[int] = []
    for part in (value or "").replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            result.append(int(part))
        except ValueError:
            continue
    return result


def _split_strings(value: str) -> List[str]:
    result: List[str] = []
    for part in (value or "").replace(";", ",").split(","):
        part = part.strip().strip('"').strip("'")
        if part:
            result.append(part)
    return result


def _bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on", "да"}


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    admin_ids: List[int]
    senior_teacher_ids: List[int]
    allowed_group_ids: List[int]
    response_mode: str

    ollama_url: str
    ollama_model: str
    ollama_timeout: int

    data_dir: Path
    db_path: Path
    kb_top_k: int
    kb_max_context_chars: int
    use_raw_excel: bool
    log_all_group_messages: bool
    telegram_max_message_chars: int
    enable_trial_manager: bool

    moyklass_api_url: str
    moyklass_api_key: str
    moyklass_timeout: int

    notion_token: str
    notion_page_ids: List[str]
    notion_database_ids: List[str]
    notion_sync_dir: Path
    notion_api_version: str
    notion_database_api_version: str
    notion_timeout: int
    notion_sync_clean: bool
    notion_sync_recursive: bool
    notion_recursive_max_depth: int

    mk_auto_watch_enabled: bool
    mk_watch_interval_minutes: int
    mk_watch_days: int
    mk_watch_initial_delay_seconds: int
    mk_watch_notify_admins: bool

    web_app_url: str
    web_app_host: str
    web_app_port: int
    web_app_dev_mode: bool
    web_app_allow_unsafe_fallback: bool
    web_app_test_roles: bool
    mvp_release_mode: bool

    intern_trial_material_url: str

    food_module_enabled: bool
    food_location_yc1: str
    food_location_yc2: str
    food_location_yc3: str
    food_menu_ocr_enabled: bool
    food_menu_ocr_provider: str
    food_menu_ocr_lang: str
    food_menu_ocr_psm: int
    camp_class_name_filter: str
    camp_lesson_name_filter: str
    camp_lesson_alt_filters: str
    camp_active_week_mode: str
    camp_active_start_date: str
    camp_active_end_date: str
    food_auto_reminders_enabled: bool
    food_auto_reminder_minutes_before_deadline: int
    food_auto_reminder_check_interval_minutes: int

    # bePaid integration
    bepaid_erip_shop_id: str
    bepaid_erip_secret_key: str
    bepaid_erip_public_key: str
    bepaid_acq_shop_id: str
    bepaid_acq_secret_key: str
    bepaid_acq_public_key: str
    bepaid_auto_post_to_moyklass: bool
    bepaid_webhook_path_secret: str
    bepaid_public_base_url: str
    bepaid_request_timeout: int

    # MoyKlass manual payment posting (v7.0.92)
    moyklass_erip_payment_type_id: int        # paymentTypeId for ERIP income payments (0 = not configured)
    # MoyKlass dual-channel payment type mapping (v7.0.92.2)
    moyklass_acquiring_payment_type_id: int   # paymentTypeId for acquiring income payments (0 = not configured)

    # Invoice automation (v7.0.94.0) — global kill switch for scheduled runs
    payment_invoice_automation_enabled: bool

    @property
    def bepaid_erip_enabled(self) -> bool:
        return bool(self.bepaid_erip_shop_id and self.bepaid_erip_secret_key)

    @property
    def bepaid_acq_enabled(self) -> bool:
        return bool(self.bepaid_acq_shop_id and self.bepaid_acq_secret_key)

    @property
    def bepaid_enabled(self) -> bool:
        return self.bepaid_erip_enabled or self.bepaid_acq_enabled

    @property
    def allow_all_groups(self) -> bool:
        return not self.allowed_group_ids or 0 in self.allowed_group_ids

    @property
    def moyklass_enabled(self) -> bool:
        return bool(self.moyklass_api_key and not self.moyklass_api_key.upper().startswith("PASTE_"))

    @property
    def notion_enabled(self) -> bool:
        return bool(
            self.notion_token
            and not self.notion_token.upper().startswith("PASTE_")
            and (self.notion_page_ids or self.notion_database_ids)
        )

    def is_group_allowed(self, chat_id: int) -> bool:
        return self.allow_all_groups or chat_id in self.allowed_group_ids


def load_settings() -> Settings:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    # Common copy-paste mistake protection.
    if token.startswith("TELEGRAM_BOT_TOKEN="):
        token = token.split("=", 1)[1].strip()
    token = token.strip().strip('"').strip("'")

    group_ids = os.getenv("ALLOWED_GROUP_IDS", "").strip()
    if not group_ids:
        group_ids = os.getenv("ALLOWED_GROUP_ID", "0").strip()

    return Settings(
        telegram_bot_token=token,
        admin_ids=_split_ints(os.getenv("ADMIN_IDS", "")),
        senior_teacher_ids=_split_ints(os.getenv("SENIOR_TEACHER_IDS", "")),
        allowed_group_ids=_split_ints(group_ids),
        response_mode=os.getenv("RESPONSE_MODE", "smart").strip().lower() or "smart",
        ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/"),
        ollama_model=os.getenv("OLLAMA_MODEL", "qwen2.5:3b").strip(),
        ollama_timeout=int(os.getenv("OLLAMA_TIMEOUT", "180")),
        data_dir=(BASE_DIR / os.getenv("DATA_DIR", "data")).resolve(),
        db_path=(BASE_DIR / os.getenv("DB_PATH", "storage/messages.db")).resolve(),
        kb_top_k=int(os.getenv("KB_TOP_K", "6")),
        kb_max_context_chars=int(os.getenv("KB_MAX_CONTEXT_CHARS", "9000")),
        use_raw_excel=_bool(os.getenv("USE_RAW_EXCEL", "false"), False),
        log_all_group_messages=_bool(os.getenv("LOG_ALL_GROUP_MESSAGES", "true"), True),
        telegram_max_message_chars=int(os.getenv("TELEGRAM_MAX_MESSAGE_CHARS", "3900")),
        enable_trial_manager=_bool(os.getenv("ENABLE_TRIAL_MANAGER", "true"), True),
        moyklass_api_url=os.getenv("MOYKLASS_API_URL", "https://api.moyklass.com").strip().rstrip("/"),
        moyklass_api_key=os.getenv("MOYKLASS_API_KEY", "").strip().strip("\"").strip("'"),
        moyklass_timeout=int(os.getenv("MOYKLASS_TIMEOUT", "25")),
        notion_token=os.getenv("NOTION_TOKEN", "").strip().strip("\"").strip("'"),
        notion_page_ids=_split_strings(os.getenv("NOTION_PAGE_IDS", "")),
        notion_database_ids=_split_strings(os.getenv("NOTION_DATABASE_IDS", "")),
        notion_sync_dir=(BASE_DIR / os.getenv("NOTION_SYNC_DIR", "data/notion")).resolve(),
        notion_api_version=os.getenv("NOTION_API_VERSION", "2026-03-11").strip(),
        notion_database_api_version=os.getenv("NOTION_DATABASE_API_VERSION", "2022-06-28").strip(),
        notion_timeout=int(os.getenv("NOTION_TIMEOUT", "30")),
        notion_sync_clean=_bool(os.getenv("NOTION_SYNC_CLEAN", "false"), False),
        notion_sync_recursive=_bool(os.getenv("NOTION_SYNC_RECURSIVE", "true"), True),
        notion_recursive_max_depth=int(os.getenv("NOTION_RECURSIVE_MAX_DEPTH", "6")),
        mk_auto_watch_enabled=_bool(os.getenv("MK_AUTO_WATCH_ENABLED", "true"), True),
        mk_watch_interval_minutes=max(1, int(os.getenv("MK_WATCH_INTERVAL_MINUTES", "15"))),
        mk_watch_days=max(1, min(int(os.getenv("MK_WATCH_DAYS", "30")), 120)),
        mk_watch_initial_delay_seconds=max(5, int(os.getenv("MK_WATCH_INITIAL_DELAY_SECONDS", "30"))),
        mk_watch_notify_admins=_bool(os.getenv("MK_WATCH_NOTIFY_ADMINS", "true"), True),
        web_app_url=os.getenv("WEB_APP_URL", "").strip().rstrip("/"),
        web_app_host=os.getenv("WEB_APP_HOST", "127.0.0.1").strip() or "127.0.0.1",
        web_app_port=max(1, int(os.getenv("WEB_APP_PORT", "8088"))),
        web_app_dev_mode=_bool(os.getenv("WEB_APP_DEV_MODE", "false"), False),
        web_app_allow_unsafe_fallback=_bool(os.getenv("WEB_APP_ALLOW_UNSAFE_FALLBACK", "true"), True),
        web_app_test_roles=_bool(os.getenv("WEB_APP_TEST_ROLES", "true"), True),
        mvp_release_mode=_bool(os.getenv("MVP_RELEASE_MODE", "false"), False),
        intern_trial_material_url=os.getenv("INTERN_TRIAL_MATERIAL_URL", "").strip(),
        food_module_enabled=_bool(os.getenv("FOOD_MODULE_ENABLED", "false"), False),
        food_location_yc1=os.getenv("FOOD_LOCATION_YC1", "Кульман 1/1").strip(),
        food_location_yc2=os.getenv("FOOD_LOCATION_YC2", "Мстиславца 6").strip(),
        food_location_yc3=os.getenv("FOOD_LOCATION_YC3", "").strip(),
        food_menu_ocr_enabled=_bool(os.getenv("FOOD_MENU_OCR_ENABLED", "false"), False),
        food_menu_ocr_provider=os.getenv("FOOD_MENU_OCR_PROVIDER", "local_tesseract").strip(),
        food_menu_ocr_lang=os.getenv("FOOD_MENU_OCR_LANG", "rus+eng").strip() or "rus+eng",
        food_menu_ocr_psm=int(os.getenv("FOOD_MENU_OCR_PSM", "6")),
        camp_class_name_filter=os.getenv("CAMP_CLASS_NAME_FILTER", "Summer Camp").strip(),
        camp_lesson_name_filter=(
            os.getenv("CAMP_LESSON_NAME_FILTER", "").strip()
            or os.getenv("CAMP_CLASS_NAME_FILTER", "Summer Camp").strip()
            or "Summer Camp"
        ),
        camp_lesson_alt_filters=os.getenv("CAMP_LESSON_ALT_FILTERS", "").strip(),
        camp_active_week_mode=os.getenv("CAMP_ACTIVE_WEEK_MODE", "auto").strip().lower() or "auto",
        camp_active_start_date=os.getenv("CAMP_ACTIVE_START_DATE", "").strip(),
        camp_active_end_date=os.getenv("CAMP_ACTIVE_END_DATE", "").strip(),
        food_auto_reminders_enabled=_bool(os.getenv("FOOD_AUTO_REMINDERS_ENABLED", "false"), False),
        food_auto_reminder_minutes_before_deadline=max(1, int(os.getenv("FOOD_AUTO_REMINDER_MINUTES_BEFORE_DEADLINE", "120"))),
        food_auto_reminder_check_interval_minutes=max(1, int(os.getenv("FOOD_AUTO_REMINDER_CHECK_INTERVAL_MINUTES", "15"))),
        bepaid_erip_shop_id=os.getenv("BEPAID_ERIP_SHOP_ID", "").strip(),
        bepaid_erip_secret_key=os.getenv("BEPAID_ERIP_SECRET_KEY", "").strip(),
        bepaid_erip_public_key=os.getenv("BEPAID_ERIP_PUBLIC_KEY", "").strip(),
        bepaid_acq_shop_id=os.getenv("BEPAID_ACQ_SHOP_ID", "").strip(),
        bepaid_acq_secret_key=os.getenv("BEPAID_ACQ_SECRET_KEY", "").strip(),
        bepaid_acq_public_key=os.getenv("BEPAID_ACQ_PUBLIC_KEY", "").strip(),
        bepaid_auto_post_to_moyklass=_bool(os.getenv("BEPAID_AUTO_POST_TO_MOYKLASS", "false"), False),
        bepaid_webhook_path_secret=os.getenv("BEPAID_WEBHOOK_PATH_SECRET", "").strip(),
        bepaid_public_base_url=os.getenv("BEPAID_PUBLIC_BASE_URL", "").strip().rstrip("/"),
        bepaid_request_timeout=int(os.getenv("BEPAID_REQUEST_TIMEOUT", "30")),
        moyklass_erip_payment_type_id=int(os.getenv("MOYKLASS_ERIP_PAYMENT_TYPE_ID", "0") or "0"),
        moyklass_acquiring_payment_type_id=int(os.getenv("MOYKLASS_ACQUIRING_PAYMENT_TYPE_ID", "0") or "0"),
        payment_invoice_automation_enabled=_bool(os.getenv("PAYMENT_INVOICE_AUTOMATION_ENABLED", "false"), False),
    )

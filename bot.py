from __future__ import annotations

import asyncio
import logging
import sys

from telegram.ext import ApplicationBuilder, MessageHandler, filters

from admin_panel import AdminPanel
from agent_core import AgentCore
from config import load_settings
from handlers import BotHandlers
from knowledge_base import KnowledgeBase
from llm import OllamaClient
from storage import Storage

MARKER = "YELLOW_CLUB_AGENT_V3_STABLE_MODULAR_239_2026_06_13"


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    # Do not print full Telegram URLs with token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.WARNING)


def main() -> None:
    setup_logging()
    log = logging.getLogger("yellow_club_agent")
    log.info(MARKER)
    log.info("Yellow Club Agent v3 запускается...")

    settings = load_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is empty. Fill .env first.")

    kb = KnowledgeBase(settings.data_dir, use_raw_excel=settings.use_raw_excel)
    storage = Storage(settings.db_path)
    llm = OllamaClient(settings.ollama_url, settings.ollama_model, settings.ollama_timeout)
    core = AgentCore(kb, storage, llm, settings.kb_top_k, settings.kb_max_context_chars)
    admin = AdminPanel(settings, kb, storage, llm)
    handlers = BotHandlers(settings, storage, core, admin)

    app = ApplicationBuilder().token(settings.telegram_bot_token).post_init(handlers.post_init).build()
    app.add_handler(MessageHandler(filters.TEXT | filters.Caption(), handlers.handle_message))

    # Python 3.14 compatibility safety; harmless on 3.12.
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    app.run_polling(allowed_updates=None)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)

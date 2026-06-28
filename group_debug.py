from __future__ import annotations

import logging
from telegram.ext import ApplicationBuilder, MessageHandler, filters
from config import load_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)

async def handler(update, context):
    msg = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if not msg or not chat:
        return
    text = msg.text or msg.caption or ""
    logging.info("DEBUG UPDATE | chat_id=%s | type=%s | thread_id=%s | user_id=%s | username=%s | text=%r", chat.id, chat.type, getattr(msg, "message_thread_id", None), user.id if user else None, user.username if user else None, text)
    if text.startswith("/ping"):
        await msg.reply_text(f"pong debug\nchat_id={chat.id}\ntype={chat.type}\nthread_id={getattr(msg, 'message_thread_id', None)}")

async def post_init(app):
    me = await app.bot.get_me()
    logging.info("Debug bot started as @%s", me.username)

if __name__ == "__main__":
    settings = load_settings()
    app = ApplicationBuilder().token(settings.telegram_bot_token).post_init(post_init).build()
    app.add_handler(MessageHandler(filters.ALL, handler))
    app.run_polling(allowed_updates=None)

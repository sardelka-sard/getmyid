import asyncio
import html
import logging
import os

import asyncpg
from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CopyTextButton
from aiohttp import web

# ---------- Конфиг ----------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_BASE = os.getenv("API_BASE", "https://misugram.cloud-ip.cc")
DATABASE_URL = os.getenv("DATABASE_URL") 
PORT = int(os.getenv("PORT", "8080"))

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("id-bot")

session = AiohttpSession(api=TelegramAPIServer.from_base(API_BASE))
bot = Bot(token=BOT_TOKEN, session=session)
dp = Dispatcher()
db_pool = None

# ---------- БД ----------
async def init_db():
    global db_pool
    if not DATABASE_URL: 
        log.error("DATABASE_URL не задан!")
        return
    try:
        # statement_cache_size=0 обязателен для работы с пулером Supabase (порт 6543)
        db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5, statement_cache_size=0)
        async with db_pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    username TEXT,
                    full_name TEXT
                )
            """)
        log.info("База данных Supabase подключена.")
    except Exception as e:
        log.error(f"Ошибка подключения к БД: {e}")

async def save_user(user_id: int, username: str | None, full_name: str):
    if not db_pool: return
    try:
        async with db_pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO users (user_id, username, full_name) 
                VALUES ($1, $2, $3)
                ON CONFLICT (user_id) DO UPDATE SET username = $2, full_name = $3
            """, user_id, username, full_name)
    except Exception as e:
        log.error(f"Ошибка сохранения юзера: {e}")

async def get_user_by_username(username: str):
    if not db_pool: return None
    try:
        async with db_pool.acquire() as conn:
            # Ищем без учета регистра
            return await conn.fetchrow("SELECT * FROM users WHERE LOWER(username) = LOWER($1)", username)
    except Exception as e:
        log.error(f"Ошибка поиска юзера: {e}")
        return None

# ---------- Логика ----------
@dp.message(F.text)
async def on_any_message(message: Message) -> None:
    if message.from_user.is_bot:
        return

    # Запоминаем каждого, кто пишет (даже если это просто команда)
    await save_user(message.from_user.id, message.from_user.username, message.from_user.full_name)

    text = message.text.strip()

    # --- В ГРУППАХ ---
    if message.chat.type != "private" and text.startswith("/id"):
        parts = text.split(maxsplit=1)
        target_user_id = None
        name_str = ""

        # 1. Ответ на сообщение
        if message.reply_to_message and message.reply_to_message.from_user:
            target_user_id = message.reply_to_message.from_user.id
            name_str = message.reply_to_message.from_user.full_name
        
        # 2. Поиск по @username в базе
        elif len(parts) > 1:
            username = parts[1].replace("@", "").strip()
            user_row = await get_user_by_username(username)
            if user_row:
                target_user_id = user_row['user_id']
                name_str = user_row['full_name']
            else:
                await message.reply(
                    f"❌ Пользователь <b>@{html.escape(username)}</b> не найден в базе.\n"
                    f"Попроси его написать любое сообщение в этот чат, и я его запомню.",
                    parse_mode="HTML"
                )
                return
        
        # 3. Просто /id
        else:
            target_user_id = message.from_user.id
            name_str = message.from_user.full_name

        if target_user_id:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Скопировать ID", copy_text=CopyTextButton(text=str(target_user_id)))]
            ])
            await message.reply(
                f"👤 ID пользователя <b>{html.escape(name_str)}</b>:\n"
                f"<code>{target_user_id}</code>",
                parse_mode="HTML",
                reply_markup=kb
            )
        return

    # --- В ЛИЧКЕ ---
    if message.chat.type == "private":
        if text.startswith("/start") or text.startswith("/id"):
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📋 Скопировать мой ID", copy_text=CopyTextButton(text=str(message.from_user.id)))]
            ])
            await message.answer(
                f"Привет, <b>{html.escape(message.from_user.full_name)}</b>!\n"
                f"Твой ID: <code>{message.from_user.id}</code>\n\n"
                "Команды в группе:\n"
                "• <code>/id</code> — твой ID\n"
                "• <code>/id @username</code> — поиск по базе\n"
                "• <code>/id</code> (ответом) — ID автора сообщения",
                reply_markup=kb,
                parse_mode="HTML"
            )
            return

# ---------- Веб-сервер и Запуск ----------
async def health(request): return web.Response(text="ID Bot is running!")

async def main() -> None:
    await init_db()
    
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    log.info(f"Web server started on port {PORT}")

    await bot.delete_webhook(drop_pending_updates=True)
    log.info("Starting ID-bot polling...")
    await dp.start_polling(bot, allowed_updates=["message"])

if __name__ == "__main__":
    asyncio.run(main())

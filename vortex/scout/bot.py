"""Telegram bot for the Vortex Mini App and opt-in new-order alerts."""
import asyncio
import html
import os
import sys
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from aiogram import Bot, Dispatcher, Router
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Update, WebAppInfo
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from channel_forward import forward_channel_update
from catalog import CatalogClient, CatalogUnavailable
from database.db import DATABASE_PATH

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("Не найден TELEGRAM_BOT_TOKEN в файле .env")
WEBAPP_URL = os.getenv("VORTEX_WEBAPP_URL", "https://vortex-mini-app.onrender.com").strip()
POLL_SECONDS = max(30, int(os.getenv("VORTEX_NOTIFY_INTERVAL", "90")))
CATEGORIES = {"web":"🌐 Сайты и лендинги","telegram":"🤖 Telegram-боты","ai":"🧠 ИИ и автоматизация","python":"🐍 Python и API"}

bot=Bot(token=TOKEN)
dp=Dispatcher()
router=Router()
dp.include_router(router)


@contextmanager
def db():
    connection=sqlite3.connect(DATABASE_PATH)
    connection.execute("CREATE TABLE IF NOT EXISTS bot_subscribers (chat_id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    connection.execute("CREATE TABLE IF NOT EXISTS notified_orders (order_id INTEGER PRIMARY KEY, seen_at TEXT DEFAULT CURRENT_TIMESTAMP)")
    connection.execute("""CREATE TABLE IF NOT EXISTS user_profiles (
        telegram_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT NOT NULL DEFAULT '',
        last_name TEXT NOT NULL DEFAULT '',
        registered_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


def notification_enabled(chat_id:int)->bool:
    with db() as conn:
        row=conn.execute("SELECT enabled FROM bot_subscribers WHERE chat_id=?",(chat_id,)).fetchone()
    return bool(row and row[0])


def main_menu(chat_id:int|None=None):
    rows=[[InlineKeyboardButton(text="🚀 Открыть Vortex",web_app=WebAppInfo(url=WEBAPP_URL))],
          [InlineKeyboardButton(text="👤 Мой профиль",callback_data="show_profile")],
          [InlineKeyboardButton(text="📋 Найти заказы",callback_data="find_orders")]]
    rows.extend([[InlineKeyboardButton(text=name,callback_data=f"category:{key}")] for key,name in CATEGORIES.items()])
    if chat_id is not None:
        rows.insert(1,[InlineKeyboardButton(text="🔔 Выключить уведомления" if notification_enabled(chat_id) else "🔔 Включить уведомления",callback_data="toggle_notifications")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.message(CommandStart())
async def start_handler(message:Message):
    with db() as conn:
        conn.execute("INSERT OR IGNORE INTO bot_subscribers(chat_id,enabled) VALUES (?,0)",(message.chat.id,))
    keyboard=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Зарегистрироваться",callback_data="register_profile")],
        [InlineKeyboardButton(text="🚀 Открыть Vortex",web_app=WebAppInfo(url=WEBAPP_URL))],
        [InlineKeyboardButton(text="👤 Мой профиль",callback_data="show_profile")],
    ])
    await message.answer(
        "👋 Добро пожаловать в Vortex!\n\n"
        "Создайте профиль одной кнопкой, затем открывайте Mini App, ищите заказы и сохраняйте понравившиеся в избранное.\n\n"
        "Уведомления о новых заказах можно включить отдельно.",
        reply_markup=keyboard)


@router.message(Command("register"))
async def register_command(message:Message):
    user=message.from_user
    if not user:
        await message.answer("Не удалось получить данные Telegram-профиля.")
        return
    with db() as conn:
        conn.execute("""INSERT INTO user_profiles(telegram_id,username,first_name,last_name)
            VALUES (?,?,?,?) ON CONFLICT(telegram_id) DO UPDATE SET
            username=excluded.username,first_name=excluded.first_name,last_name=excluded.last_name""",
            (user.id,user.username,user.first_name or "",user.last_name or ""))
        conn.execute("INSERT INTO bot_subscribers(chat_id,enabled) VALUES (?,0) ON CONFLICT(chat_id) DO NOTHING",(message.chat.id,))
    await message.answer("✅ Регистрация завершена! Ваш профиль Vortex создан.",reply_markup=main_menu(message.chat.id))


@router.callback_query(lambda c:c.data=="register_profile")
async def register_profile_callback(callback:CallbackQuery):
    user=callback.from_user
    with db() as conn:
        conn.execute("""INSERT INTO user_profiles(telegram_id,username,first_name,last_name)
            VALUES (?,?,?,?) ON CONFLICT(telegram_id) DO UPDATE SET
            username=excluded.username,first_name=excluded.first_name,last_name=excluded.last_name""",
            (user.id,user.username,user.first_name or "",user.last_name or ""))
    await callback.answer("Регистрация завершена!")
    await callback.message.answer("✅ Ваш профиль Vortex создан.",reply_markup=main_menu(callback.message.chat.id))


@router.message(Command("profile"))
async def profile_command(message:Message):
    user=message.from_user
    if not user:
        await message.answer("Не удалось получить данные Telegram-профиля.")
        return
    with db() as conn:
        row=conn.execute("SELECT registered_at FROM user_profiles WHERE telegram_id=?",(user.id,)).fetchone()
    name=" ".join(part for part in [user.first_name,user.last_name] if part)
    username=f"@{user.username}" if user.username else "не указан"
    status=f"Зарегистрирован\nДата: {row[0]}" if row else "Не зарегистрирован — отправьте /register"
    await message.answer(
        f"👤 <b>Профиль Vortex</b>\n\nИмя: {html.escape(name or 'Пользователь Telegram')}\n"
        f"Username: {html.escape(username)}\nTelegram ID: <code>{user.id}</code>\n\n{html.escape(status)}",
        parse_mode="HTML",reply_markup=main_menu(message.chat.id))


@router.callback_query(lambda c:c.data=="show_profile")
async def show_profile_callback(callback:CallbackQuery):
    user=callback.from_user
    with db() as conn:
        row=conn.execute("SELECT registered_at FROM user_profiles WHERE telegram_id=?",(user.id,)).fetchone()
    name=" ".join(part for part in [user.first_name,user.last_name] if part)
    username=f"@{user.username}" if user.username else "не указан"
    status=f"Зарегистрирован\nДата: {row[0]}" if row else "Не зарегистрирован — нажмите /register или кнопку регистрации в /start"
    await callback.answer()
    await callback.message.answer(
        f"👤 <b>Профиль Vortex</b>\n\nИмя: {html.escape(name or 'Пользователь Telegram')}\n"
        f"Username: {html.escape(username)}\nTelegram ID: <code>{user.id}</code>\n\n{html.escape(status)}",
        parse_mode="HTML",reply_markup=main_menu(callback.message.chat.id))


@router.message(Command("subscribe"))
async def subscribe_handler(message:Message):
    with db() as conn: conn.execute("INSERT INTO bot_subscribers(chat_id,enabled) VALUES (?,1) ON CONFLICT(chat_id) DO UPDATE SET enabled=1",(message.chat.id,))
    await message.answer("🔔 Уведомления о новых заказах включены.",reply_markup=main_menu(message.chat.id))


@router.message(Command("unsubscribe"))
async def unsubscribe_handler(message:Message):
    with db() as conn: conn.execute("INSERT INTO bot_subscribers(chat_id,enabled) VALUES (?,0) ON CONFLICT(chat_id) DO UPDATE SET enabled=0",(message.chat.id,))
    await message.answer("🔕 Уведомления выключены.",reply_markup=main_menu(message.chat.id))


@router.callback_query(lambda c:c.data=="toggle_notifications")
async def toggle_notifications(callback:CallbackQuery):
    enabled=not notification_enabled(callback.message.chat.id)
    with db() as conn:
        conn.execute("INSERT INTO bot_subscribers(chat_id,enabled) VALUES (?,?) ON CONFLICT(chat_id) DO UPDATE SET enabled=excluded.enabled",(callback.message.chat.id,int(enabled)))
    await callback.answer("Уведомления включены" if enabled else "Уведомления выключены")
    await callback.message.edit_reply_markup(reply_markup=main_menu(callback.message.chat.id))


@router.callback_query(lambda c:c.data=="find_orders")
async def find_orders_handler(callback:CallbackQuery):
    await callback.answer("Открывай Mini App — там вся лента заказов.")
    await callback.message.answer("🚀 Нажми кнопку, чтобы открыть каталог.",reply_markup=main_menu(callback.message.chat.id))


@router.callback_query(lambda c:c.data and c.data.startswith("category:"))
async def category_handler(callback:CallbackQuery):
    key=callback.data.split(":",1)[1]
    category=CATEGORIES.get(key)
    await callback.answer()
    if category:
        await callback.message.answer(f"Категория: {category}\\nОткрой Mini App и выбери её в ленте.",reply_markup=main_menu(callback.message.chat.id))


@router.channel_post()
async def channel_post_handler(_channel_post:Message,event_update:Update):
    try:
        payload=event_update.model_dump(mode="json",exclude_unset=True,exclude_none=True)
        await asyncio.to_thread(forward_channel_update,payload)
    except Exception:
        return


async def notification_worker():
    """Polls the existing upstream catalog; it does not collect or create orders."""
    # Seed the seen set at startup, so users receive only genuinely new items.
    try:
        initial=await asyncio.to_thread(CatalogClient.from_env().fetch_jobs,limit=100,offset=0)
        with db() as conn:
            conn.executemany("INSERT OR IGNORE INTO notified_orders(order_id) VALUES (?)",[(item["id"],) for item in initial["items"]])
    except Exception:
        pass
    while True:
        try:
            page=await asyncio.to_thread(CatalogClient.from_env().fetch_jobs,limit=100,offset=0)
            fresh=[]
            with db() as conn:
                for item in page["items"]:
                    cur=conn.execute("INSERT OR IGNORE INTO notified_orders(order_id) VALUES (?)",(item["id"],))
                    if cur.rowcount: fresh.append(item)
                subscribers=[row[0] for row in conn.execute("SELECT chat_id FROM bot_subscribers WHERE enabled=1").fetchall()]
            for item in reversed(fresh):
                url=item.get("url") or ""
                text=f"🆕 <b>Новый заказ в Vortex</b>\\n\\n<b>{html.escape(str(item.get('title','Заказ без названия')))}</b>\\n{html.escape(str(item.get('price','Бюджет не указан')))} · {html.escape(str(item.get('source','Источник не указан')))}"
                if url: text+=f'\\n\\n<a href="{html.escape(url, quote=True)}">Открыть оригинал</a>'
                for chat_id in subscribers:
                    try: await bot.send_message(chat_id,text,disable_web_page_preview=True)
                    except Exception: pass
        except Exception:
            pass
        await asyncio.sleep(POLL_SECONDS)


async def main():
    print("Vortex bot is starting")
    worker=asyncio.create_task(notification_worker())
    try: await dp.start_polling(bot)
    finally:
        worker.cancel()
        await asyncio.gather(worker,return_exceptions=True)
        await bot.session.close()


if __name__=="__main__":
    asyncio.run(main())

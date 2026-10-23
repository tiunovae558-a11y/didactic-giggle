import asyncio
from aiohttp import web
import html
import logging
import os
import random
import sqlite3
import string
import time
from datetime import datetime
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder


# ==================== НАСТРОЙКИ ====================

TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "protivzla.db")
SUPPORT = os.getenv("SUPPORT_USERNAME", "@suqexo")
DELAY = max(0.05, float(os.getenv("BROADCAST_DELAY", "0.08")))

if not TOKEN or not OWNER_ID:
    raise RuntimeError(
        "Задайте переменные окружения BOT_TOKEN и OWNER_ID."
    )

Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
logging.basicConfig(level=logging.INFO)


# ==================== БАЗА ДАННЫХ ====================

def db():
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def ts():
    return int(time.time())


def own(uid):
    return uid == OWNER_ID


def audit(action, details=""):
    with db() as c:
        c.execute(
            "INSERT INTO audit(action,details,ts) VALUES(?,?,?)",
            (action, details[:500], ts()),
        )


def init():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY,
            username TEXT,
            name TEXT,
            joined INTEGER,
            seen INTEGER,
            blocked INTEGER DEFAULT 0,
            points INTEGER DEFAULT 0,
            vip TEXT DEFAULT 'Обычный',
            daily INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS products(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            description TEXT,
            price INTEGER,
            active INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS favorites(
            uid INTEGER,
            type TEXT,
            item TEXT,
            title TEXT,
            ts INTEGER,
            PRIMARY KEY(uid,type,item)
        );

        CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid INTEGER,
            title TEXT,
            price INTEGER,
            status TEXT,
            ts INTEGER
        );

        CREATE TABLE IF NOT EXISTS tickets(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uid INTEGER,
            msg TEXT,
            status TEXT DEFAULT 'Открыто',
            ts INTEGER
        );

        CREATE TABLE IF NOT EXISTS promos(
            code TEXT PRIMARY KEY,
            bonus INTEGER,
            maxuses INTEGER,
            uses INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS promo_uses(
            code TEXT,
            uid INTEGER,
            PRIMARY KEY(code,uid)
        );

        CREATE TABLE IF NOT EXISTS broadcasts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT,
            body TEXT,
            photo TEXT DEFAULT '',
            btn TEXT DEFAULT '',
            url TEXT DEFAULT '',
            status TEXT,
            scheduled INTEGER DEFAULT 0,
            created INTEGER,
            sent INTEGER DEFAULT 0,
            failed INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS audit(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT,
            details TEXT,
            ts INTEGER
        );
        """)

        if not c.execute(
            "SELECT COUNT(*) FROM products"
        ).fetchone()[0]:
            c.executemany(
                "INSERT INTO products(title,description,price) "
                "VALUES(?,?,?)",
                [
                    (
                        "Демо-товар 1",
                        "Замените описание на свой товар.",
                        100,
                    ),
                    (
                        "Демо-товар 2",
                        "Пример карточки товара.",
                        250,
                    ),
                ],
            )


def reg(user):
    with db() as c:
        c.execute("""
            INSERT INTO users(id,username,name,joined,seen)
            VALUES(?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
                username=excluded.username,
                name=excluded.name,
                seen=excluded.seen
        """, (
            user.id,
            user.username or "",
            user.first_name or "",
            ts(),
            ts(),
        ))


# ==================== КЛАВИАТУРЫ ====================

def ik(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back():
    return ik([
        [
            InlineKeyboardButton(
                text="⬅️ Назад",
                callback_data="adm:home",
            )
        ]
    ])


def menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🛍️ Магазин"),
                KeyboardButton(text="🎁 Подарки"),
            ],
            [
                KeyboardButton(text="⭐ Генератор"),
                KeyboardButton(text="❤️ Избранное"),
            ],
            [
                KeyboardButton(text="👤 Профиль"),
                KeyboardButton(text="🎟️ Промокод"),
            ],
            [
                KeyboardButton(text="🎁 Ежедневная награда"),
                KeyboardButton(text="💎 VIP"),
            ],
            [
                KeyboardButton(text="🎧 Поддержка"),
                KeyboardButton(text="ℹ️ Информация"),
            ],
        ],
        resize_keyboard=True,
    )


def adm_menu():
    builder = InlineKeyboardBuilder()

    buttons = [
        ("📊 Статистика", "stats"),
        ("👥 Пользователи", "users"),
        ("🛍️ Товары/заказы", "shop"),
        ("🎟️ Промокоды", "promos"),
        ("💎 VIP", "vip"),
        ("🎧 Поддержка", "tickets"),
        ("📢 Центр рассылок", "broadcast"),
        ("🗄️ Резервная копия", "backup"),
        ("📜 Журнал действий", "audit"),
        ("⚙️ Настройки", "settings"),
    ]

    for title, key in buttons:
        builder.button(
            text=title,
            callback_data="adm:" + key,
        )

    builder.adjust(2, 2, 2, 1, 1, 1, 1)
    return builder.as_markup()


# ==================== СОСТОЯНИЯ ====================

class BC(StatesGroup):
    body = State()
    photo = State()
    caption = State()
    btn = State()
    url = State()
    schedule = State()
    cancel_id = State()


class Support(StatesGroup):
    msg = State()


class Promo(StatesGroup):
    code = State()


dp = Dispatcher()
active = set()
stop = set()


# ==================== ПРОВЕРКА ВЛАДЕЛЬЦА ====================

async def om(message):
    if not own(message.from_user.id):
        await message.answer("⛔ Доступ запрещён.")
        return False
    return True


async def oc(callback):
    if not own(callback.from_user.id):
        await callback.answer(
            "⛔ Доступ запрещён.",
            show_alert=True,
        )
        return False
    return True


# ==================== СТАРТ ====================

@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    reg(message.from_user)

    await message.answer(
        f"👋 Привет, "
        f"<b>{html.escape(message.from_user.first_name or 'пользователь')}</b>!\n"
        "Добро пожаловать в <b>PROTIVZLA ULTIMATE</b>.",
        reply_markup=menu(),
    )


@dp.message(Command("cancel"))
async def cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "Действие отменено.",
        reply_markup=menu(),
    )


@dp.message(Command("admin"))
async def admin(message: Message):
    reg(message.from_user)

    if await om(message):
        await message.answer(
            "👑 МЕГА-ПАНЕЛЬ PROTIVZLA",
            reply_markup=adm_menu(),
        )


# ==================== МАГАЗИН ====================

@dp.message(F.text == "🛍️ Магазин")
async def shop(message: Message):
    reg(message.from_user)

    with db() as c:
        products = c.execute(
            "SELECT * FROM products "
            "WHERE active=1 ORDER BY id DESC"
        ).fetchall()

    if not products:
        await message.answer("Пока товаров нет.")
        return

    for product in products:
        await message.answer(
            f"🛍️ <b>{html.escape(product['title'])}</b>\n"
            f"{html.escape(product['description'])}\n"
            f"Цена: {product['price']} баллов",
            reply_markup=ik([
                [
                    InlineKeyboardButton(
                        text="❤️ В избранное",
                        callback_data=(
                            f"fav:add:product:{product['id']}"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🛒 Оформить заказ",
                        callback_data=f"order:{product['id']}",
                    )
                ],
            ]),
        )


@dp.callback_query(F.data.startswith("order:"))
async def order(callback: CallbackQuery):
    reg(callback.from_user)
    product_id = callback.data.split(":")[1]

    with db() as c:
        product = c.execute(
            "SELECT * FROM products "
            "WHERE id=? AND active=1",
            (product_id,),
        ).fetchone()

        if not product:
            await callback.answer(
                "Товар недоступен.",
                show_alert=True,
            )
            return

        order_id = c.execute(
            "INSERT INTO orders(uid,title,price,status,ts) "
            "VALUES(?,?,?,?,?)",
            (
                callback.from_user.id,
                product["title"],
                product["price"],
                "Новый",
                ts(),
            ),
        ).lastrowid

    await callback.answer("Заказ создан")

    await callback.message.answer(
        f"Заказ №{order_id} создан.\n"
        "Это заявка: оплата пока не списана."
    )

    try:
        await callback.bot.send_message(
            OWNER_ID,
            f"🛒 Заказ #{order_id}\n"
            f"{html.escape(product['title'])}\n"
            f"Пользователь: <code>{callback.from_user.id}</code>",
        )
    except TelegramAPIError:
        pass


# ==================== ИЗБРАННОЕ ====================

@dp.callback_query(F.data.startswith("fav:add:"))
async def favadd(callback: CallbackQuery):
    reg(callback.from_user)

    _, _, item_type, item = callback.data.split(":", 3)
    title = item

    if item_type == "product":
        with db() as c:
            product = c.execute(
                "SELECT title FROM products WHERE id=?",
                (item,),
            ).fetchone()

            if product:
                title = product["title"]

    with db() as c:
        c.execute(
            "INSERT OR IGNORE INTO favorites "
            "VALUES(?,?,?,?,?)",
            (
                callback.from_user.id,
                item_type,
                item,
                title,
                ts(),
            ),
        )

    await callback.answer(
        "Добавлено в избранное ❤️",
        show_alert=True,
    )


@dp.callback_query(F.data.startswith("fav:del:"))
async def favdel(callback: CallbackQuery):
    _, _, item_type, item = callback.data.split(":", 3)

    with db() as c:
        c.execute(
            "DELETE FROM favorites "
            "WHERE uid=? AND type=? AND item=?",
            (
                callback.from_user.id,
                item_type,
                item,
            ),
        )

    await callback.answer("Удалено")


@dp.message(F.text == "❤️ Избранное")
async def favorites(message: Message):
    with db() as c:
        rows = c.execute(
            "SELECT type,item,title FROM favorites "
            "WHERE uid=? ORDER BY ts DESC LIMIT 40",
            (message.from_user.id,),
        ).fetchall()

    if not rows:
        await message.answer("❤️ Избранное пустое.")
        return

    builder = InlineKeyboardBuilder()

    for row in rows:
        builder.button(
            text="🗑️ " + row["title"][:40],
            callback_data=(
                f"fav:del:{row['type']}:{row['item']}"
            ),
        )

    builder.adjust(1)

    await message.answer(
        "❤️ Нажми на элемент, чтобы удалить:",
        reply_markup=builder.as_markup(),
    )


# ==================== ГЕНЕРАТОР ИМЁН ====================

@dp.message(F.text == "⭐ Генератор")
async def generator(message: Message):
    names = [
        "@"
        + "".join(
            random.choice(string.ascii_lowercase + string.digits)
            for _ in range(random.randint(5, 9))
        )
        for _ in range(5)
    ]

    builder = InlineKeyboardBuilder()

    for name in names:
        builder.button(
            text="❤️ " + name,
            callback_data="fav:add:username:" + name[1:],
        )

    builder.adjust(1)

    await message.answer(
        "⭐ Случайные варианты.\n"
        "Доступность имён не проверяется:\n\n"
        + "\n".join(names),
        reply_markup=builder.as_markup(),
    )


# ==================== ПРОФИЛЬ ====================

@dp.message(F.text == "👤 Профиль")
async def profile(message: Message):
    reg(message.from_user)

    with db() as c:
        user = c.execute(
            "SELECT * FROM users WHERE id=?",
            (message.from_user.id,),
        ).fetchone()

        orders_count = c.execute(
            "SELECT COUNT(*) FROM orders WHERE uid=?",
            (message.from_user.id,),
        ).fetchone()[0]

        favorites_count = c.execute(
            "SELECT COUNT(*) FROM favorites WHERE uid=?",
            (message.from_user.id,),
        ).fetchone()[0]

    await message.answer(
        "👤 <b>Профиль</b>\n"
        f"ID: <code>{user['id']}</code>\n"
        f"Баллы: {user['points']}\n"
        f"VIP: {html.escape(user['vip'])}\n"
        f"Заказов: {orders_count}\n"
        f"Избранное: {favorites_count}"
    )


# ==================== ЕЖЕДНЕВНАЯ НАГРАДА ====================

@dp.message(F.text == "🎁 Ежедневная награда")
async def daily(message: Message):
    reg(message.from_user)
    day = int(time.time()) // 86400

    with db() as c:
        user = c.execute(
            "SELECT daily FROM users WHERE id=?",
            (message.from_user.id,),
        ).fetchone()

        if user["daily"] == day:
            await message.answer(
                "Ты уже получил награду сегодня. "
                "Возвращайся завтра!"
            )
            return

        bonus = random.randint(5, 20)

        c.execute(
            "UPDATE users SET points=points+?,daily=? WHERE id=?",
            (bonus, day, message.from_user.id),
        )

    await message.answer(
        f"🎉 Начислено +{bonus} баллов!"
    )


# ==================== VIP И ПОДАРКИ ====================

@dp.message(F.text == "💎 VIP")
async def vip(message: Message):
    with db() as c:
        user = c.execute(
            "SELECT vip FROM users WHERE id=?",
            (message.from_user.id,),
        ).fetchone()

    status = user["vip"] if user else "Обычный"

    await message.answer(
        "💎 Твой статус: " + status
    )


@dp.message(F.text == "🎁 Подарки")
async def gifts(message: Message):
    await message.answer(
        "🎁 Реальные Telegram-подарки подключаются "
        "через поддерживаемые официальные возможности Telegram.\n\n"
        f"Поддержка: {SUPPORT}"
    )


@dp.message(F.text == "ℹ️ Информация")
async def info(message: Message):
    await message.answer(
        "🤖 PROTIVZLA ULTIMATE\n"
        f"Поддержка: {SUPPORT}"
    )


# ==================== ПОДДЕРЖКА ====================

class Support(StatesGroup):
    msg = State()


@dp.message(F.text == "🎧 Поддержка")
async def support_start(
    message: Message,
    state: FSMContext,
):
    reg(message.from_user)
    await state.set_state(Support.msg)

    await message.answer(
        "Напиши обращение одним сообщением.\n"
        "/cancel — отмена."
    )


@dp.message(Support.msg)
async def support_send(
    message: Message,
    state: FSMContext,
):
    if not message.text:
        await message.answer(
            "Отправь обращение текстом."
        )
        return

    with db() as c:
        ticket_id = c.execute(
            "INSERT INTO tickets(uid,msg,ts) VALUES(?,?,?)",
            (
                message.from_user.id,
                message.text[:3000],
                ts(),
            ),
        ).lastrowid

    await state.clear()

    await message.answer(
        f"✅ Обращение №{ticket_id} отправлено."
    )

    try:
        await message.bot.send_message(
            OWNER_ID,
            f"🎧 Обращение #{ticket_id}\n"
            f"От пользователя <code>{message.from_user.id}</code>\n"
            f"{html.escape(message.text[:2500])}\n\n"
            f"Ответ: /reply {ticket_id} текст",
        )
    except TelegramAPIError:
        pass


@dp.message(Command("reply"))
async def reply(message: Message):
    if not await om(message):
        return

    parts = (message.text or "").split(maxsplit=2)

    if len(parts) < 3 or not parts[1].isdigit():
        await message.answer(
            "Формат: /reply НОМЕР текст"
        )
        return

    with db() as c:
        ticket = c.execute(
            "SELECT uid FROM tickets WHERE id=?",
            (int(parts[1]),),
        ).fetchone()

        if not ticket:
            await message.answer(
                "Обращение не найдено."
            )
            return

        c.execute(
            "UPDATE tickets SET status='Отвечено' WHERE id=?",
            (int(parts[1]),),
        )

    try:
        await message.bot.send_message(
            ticket["uid"],
            f"🎧 Ответ поддержки #{parts[1]}:\n\n{parts[2]}",
        )
        await message.answer("Ответ отправлен.")
    except TelegramAPIError:
        await message.answer(
            "Не удалось отправить ответ."
        )


# ==================== ПРОМОКОДЫ ====================

@dp.message(Command("promo"))
async def make_promo(message: Message):
    if not await om(message):
        return

    parts = (message.text or "").split()

    if len(parts) != 4:
        await message.answer(
            "Формат: /promo КОД БОНУС ЛИМИТ"
        )
        return

    try:
        bonus = int(parts[2])
        limit = int(parts[3])

        if bonus < 0 or limit <= 0:
            raise ValueError
    except ValueError:
        await message.answer(
            "Бонус должен быть ≥ 0, лимит ≥ 1."
        )
        return

    with db() as c:
        c.execute(
            "INSERT OR REPLACE INTO promos"
            "(code,bonus,maxuses,uses,active) "
            "VALUES(?,?,?,0,1)",
            (parts[1].upper(), bonus, limit),
        )

    audit("create_promo", parts[1].upper())

    await message.answer("Промокод создан.")


@dp.message(F.text == "🎟️ Промокод")
async def promo_prompt(
    message: Message,
    state: FSMContext,
):
    reg(message.from_user)
    await state.set_state(Promo.code)

    await message.answer(
        "Отправь код промокода. /cancel — отмена."
    )


@dp.message(Promo.code)
async def redeem(
    message: Message,
    state: FSMContext,
):
    reg(message.from_user)
    code = (message.text or "").strip().upper()

    with db() as c:
        promo = c.execute(
            "SELECT * FROM promos WHERE code=? AND active=1",
            (code,),
        ).fetchone()

        if not promo:
            await message.answer(
                "Промокод не найден."
            )
            return

        if promo["uses"] >= promo["maxuses"]:
            await message.answer(
                "Лимит активаций исчерпан."
            )
            return

        used = c.execute(
            "SELECT 1 FROM promo_uses WHERE code=? AND uid=?",
            (code, message.from_user.id),
        ).fetchone()

        if used:
            await message.answer(
                "Ты уже активировал этот код."
            )
            return

        c.execute(
            "INSERT INTO promo_uses VALUES(?,?)",
            (code, message.from_user.id),
        )

        c.execute(
            "UPDATE promos SET uses=uses+1 WHERE code=?",
            (code,),
        )

        c.execute(
            "UPDATE users SET points=points+? WHERE id=?",
            (promo["bonus"], message.from_user.id),
        )

    await state.clear()

    await message.answer(
        f"🎉 Начислено +{promo['bonus']} баллов!"
    )


# ==================== АДМИН-ПАНЕЛЬ ====================

@dp.callback_query(F.data.startswith("adm:"))
async def admin_callback(
    callback: CallbackQuery,
    bot: Bot,
    state: FSMContext,
):
    if not await oc(callback):
        return

    key = callback.data.split(":", 1)[1]

    if key == "home":
        await callback.message.edit_text(
            "👑 МЕГА-ПАНЕЛЬ PROTIVZLA",
            reply_markup=adm_menu(),
        )

    elif key == "stats":
        with db() as c:
            users = c.execute(
                "SELECT COUNT(*) FROM users"
            ).fetchone()[0]

            orders_count = c.execute(
                "SELECT COUNT(*) FROM orders"
            ).fetchone()[0]

            tickets = c.execute(
                "SELECT COUNT(*) FROM tickets WHERE status='Открыто'"
            ).fetchone()[0]

            broadcasts = c.execute(
                "SELECT COUNT(*) FROM broadcasts"
            ).fetchone()[0]

        await callback.message.edit_text(
            "📊 <b>Статистика</b>\n"
            f"Пользователей: {users}\n"
            f"Заказов: {orders_count}\n"
            f"Открытых обращений: {tickets}\n"
            f"Рассылок: {broadcasts}",
            reply_markup=back(),
        )

    elif key == "users":
        with db() as c:
            rows = c.execute(
                "SELECT id,name,points,vip FROM users "
                "ORDER BY seen DESC LIMIT 15"
            ).fetchall()

        text = "👥 <b>Пользователи</b>\n"

        text += "\n".join(
            f"{r['id']} | {html.escape(r['name'] or '')} | "
            f"{r['points']} баллов | {html.escape(r['vip'])}"
            for r in rows
        ) or "Пока нет пользователей"

        text += "\n\nПоиск: /user ID"

        await callback.message.edit_text(
            text,
            reply_markup=back(),
        )

    elif key == "shop":
        with db() as c:
            rows = c.execute(
                "SELECT id,title,price FROM products "
                "ORDER BY id DESC LIMIT 15"
            ).fetchall()

        text = "🛍️ <b>Товары</b>\n"

        text += "\n".join(
            f"#{r['id']} {html.escape(r['title'])} — {r['price']}"
            for r in rows
        ) or "Нет товаров"

        text += (
            "\n\nДобавить товар:\n"
            "/addproduct Название | Описание | Цена"
        )

        await callback.message.edit_text(
            text,
            reply_markup=back(),
        )

    elif key == "promos":
        await callback.message.edit_text(
            "Создать промокод:\n"
            "/promo КОД БОНУС ЛИМИТ",
            reply_markup=back(),
        )

    elif key == "vip":
        await callback.message.edit_text(
            "Изменить VIP:\n"
            "/setvip TELEGRAM_ID Название",
            reply_markup=back(),
        )

    elif key == "tickets":
        with db() as c:
            rows = c.execute(
                "SELECT id,uid,msg,status FROM tickets "
                "ORDER BY id DESC LIMIT 10"
            ).fetchall()

        text = "🎧 <b>Обращения</b>\n"

        text += "\n\n".join(
            f"#{r['id']} | user {r['uid']} | {r['status']}\n"
            f"{html.escape(r['msg'][:200])}"
            for r in rows
        ) or "Нет обращений"

        text += "\n\nОтвет: /reply НОМЕР текст"

        await callback.message.edit_text(
            text,
            reply_markup=back(),
        )

    elif key == "broadcast":
        await callback.message.edit_text(
            "📢 <b>Центр рассылок</b>",
            reply_markup=ik([
                [
                    InlineKeyboardButton(
                        text="✉️ Текст",
                        callback_data="bc:new:text",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🖼️ Фото + подпись",
                        callback_data="bc:new:photo",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🕒 Запланированные",
                        callback_data="bc:scheduled",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📝 История",
                        callback_data="bc:history",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⏸️ Остановить активную",
                        callback_data="bc:stop",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Назад",
                        callback_data="adm:home",
                    )
                ],
            ]),
        )

    elif key == "backup":
        from aiogram.types import BufferedInputFile

        with db() as source:
            destination = sqlite3.connect(":memory:")
            source.backup(destination)
            data = destination.serialize()
            destination.close()

        await bot.send_document(
            OWNER_ID,
            BufferedInputFile(
                data,
                filename=(
                    "protivzla-backup-"
                    + datetime.now().strftime("%Y%m%d-%H%M%S")
                    + ".db"
                ),
            ),
            caption="Резервная копия базы",
        )

        await callback.answer(
            "Копия отправлена владельцу."
        )

    elif key == "audit":
        with db() as c:
            rows = c.execute(
                "SELECT action,details,ts FROM audit "
                "ORDER BY id DESC LIMIT 15"
            ).fetchall()

        text = "📜 <b>Журнал действий</b>\n"

        text += "\n".join(
            datetime.fromtimestamp(r["ts"]).strftime("%d.%m %H:%M")
            + " "
            + html.escape(r["action"])
            + ": "
            + html.escape(r["details"])
            for r in rows
        ) or "Журнал пуст"

        await callback.message.edit_text(
            text,
            reply_markup=back(),
        )

    elif key == "settings":
        await callback.message.edit_text(
            f"⚙️ Поддержка: {html.escape(SUPPORT)}\n"
            f"База: <code>{html.escape(DB_PATH)}</code>\n"
            f"Владелец ID: <code>{OWNER_ID}</code>",
            reply_markup=back(),
        )

    await callback.answer()


# ==================== ЦЕНТР РАССЫЛОК ====================

@dp.callback_query(F.data.startswith("bc:"))
async def broadcast_callback(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
):
    if not await oc(callback):
        return

    parts = callback.data.split(":")
    action = parts[1]

    if action == "new":
        await state.clear()

        await state.update_data(
            kind=parts[2],
            body="",
            photo="",
            btn="",
            url="",
            scheduled=0,
        )

        if parts[2] == "text":
            await state.set_state(BC.body)
            await callback.message.edit_text(
                "✉️ Отправь текст рассылки.\n"
                "/cancel — отмена."
            )
        else:
            await state.set_state(BC.photo)
            await callback.message.edit_text(
                "🖼️ Отправь фотографию.\n"
                "/cancel — отмена."
            )

    elif action == "history":
        with db() as c:
            rows = c.execute(
                "SELECT id,status,created,sent,failed,scheduled "
                "FROM broadcasts ORDER BY id DESC LIMIT 15"
            ).fetchall()

        text = "📝 <b>История рассылок</b>\n"

        text += "\n".join(
            f"#{r['id']} {r['status']} | "
            f"успешно {r['sent']} | ошибки {r['failed']} | "
            + datetime.fromtimestamp(r["created"]).strftime("%d.%m %H:%M")
            for r in rows
        ) or "История пуста"

        await callback.message.edit_text(
            text,
            reply_markup=back(),
        )

    elif action == "scheduled":
        with db() as c:
            rows = c.execute(
                "SELECT id,body,scheduled FROM broadcasts "
                "WHERE status='Запланирована' "
                "ORDER BY scheduled LIMIT 20"
            ).fetchall()

        text = "🕒 <b>Запланированные рассылки</b>\n"

        text += "\n".join(
            f"#{r['id']} "
            f"{datetime.fromtimestamp(r['scheduled']).strftime('%d.%m %H:%M')} "
            f"{html.escape(r['body'][:80])}"
            for r in rows
        ) or "Нет запланированных рассылок"

        await callback.message.edit_text(
            text,
            reply_markup=ik([
                [
                    InlineKeyboardButton(
                        text="Отменить по ID",
                        callback_data="bc:cancel_prompt",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Назад",
                        callback_data="adm:broadcast",
                    )
                ],
            ]),
        )

    elif action == "cancel_prompt":
        await state.set_state(BC.cancel_id)

        await callback.message.edit_text(
            "Отправь ID запланированной рассылки.\n"
            "/cancel — отмена."
        )

    elif action == "stop":
        ids = list(active)

        if not ids:
            await callback.answer(
                "Активных рассылок нет.",
                show_alert=True,
            )
        else:
            stop.update(ids)

            with db() as c:
                c.executemany(
                    "UPDATE broadcasts SET status='Остановлена' "
                    "WHERE id=?",
                    [(broadcast_id,) for broadcast_id in ids],
                )

            await callback.message.edit_text(
                "⏸️ Остановка рассылки запрошена.",
                reply_markup=back(),
            )

    elif action == "discard":
        await state.clear()

        await callback.message.edit_text(
            "Черновик удалён."
        )

    elif action in ("send", "schedule"):
        data = await state.get_data()

        if not data.get("ready"):
            await callback.answer(
                "Сначала подготовь предпросмотр.",
                show_alert=True,
            )
            return

        if action == "schedule":
            await state.set_state(BC.schedule)

            await callback.message.edit_text(
                "Отправь дату и время по часовому поясу сервера:\n"
                "ГГГГ-ММ-ДД ЧЧ:ММ"
            )
        else:
            broadcast_id = save_bc(
                data,
                0,
                "В очереди",
            )

            await state.clear()

            await callback.message.edit_text(
                f"✅ Рассылка #{broadcast_id} поставлена в очередь."
            )

            asyncio.create_task(
                run_bc(bot, broadcast_id)
            )

    elif action == "confirm_schedule":
        data = await state.get_data()

        broadcast_id = save_bc(
            data,
            data.get("scheduled", 0),
            "Запланирована",
        )

        await state.clear()

        await callback.message.edit_text(
            f"🕒 Рассылка #{broadcast_id} запланирована."
        )

    await callback.answer()


def save_bc(data, scheduled, status):
    with db() as c:
        return c.execute(
            "INSERT INTO broadcasts"
            "(kind,body,photo,btn,url,status,scheduled,created) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (
                data["kind"],
                data.get("body", ""),
                data.get("photo", ""),
                data.get("btn", ""),
                data.get("url", ""),
                status,
                scheduled,
                ts(),
            ),
        ).lastrowid


async def ask_btn(message, state):
    await state.set_state(BC.btn)

    await message.answer(
        "🔘 Отправь текст URL-кнопки.\n"
        "Отправь символ —, чтобы пропустить."
    )


@dp.message(BC.body)
async def bc_body(message: Message, state: FSMContext):
    if not await om(message):
        return

    if not message.text:
        await message.answer("Отправь текст сообщением.")
        return

    await state.update_data(
        body=message.text[:3900]
    )

    await ask_btn(message, state)


@dp.message(BC.photo, F.photo)
async def bc_photo(message: Message, state: FSMContext):
    if not await om(message):
        return

    await state.update_data(
        photo=message.photo[-1].file_id
    )

    await state.set_state(BC.caption)

    await message.answer(
        "Отправь подпись к фото или — без подписи."
    )


@dp.message(BC.photo)
async def bc_photo_bad(message: Message):
    await message.answer("Нужно отправить фотографию.")


@dp.message(BC.caption)
async def bc_caption(message: Message, state: FSMContext):
    if not await om(message):
        return

    caption = (message.text or "").strip()

    await state.update_data(
        body="" if caption == "—" else caption[:1000]
    )

    await ask_btn(message, state)


@dp.message(BC.btn)
async def bc_btn(message: Message, state: FSMContext):
    if not await om(message):
        return

    text = (message.text or "").strip()

    if text == "—" or not text:
        await state.update_data(btn="", url="")
        await preview(message, state)
        return

    await state.update_data(
        btn=text[:64]
    )

    await state.set_state(BC.url)

    await message.answer(
        "Отправь URL кнопки (https://... или http://...)."
    )


@dp.message(BC.url)
async def bc_url(message: Message, state: FSMContext):
    if not await om(message):
        return

    url = (message.text or "").strip()

    if not url.startswith(("https://", "http://")):
        await message.answer(
            "Ссылка должна начинаться с https:// или http://."
        )
        return

    await state.update_data(url=url)

    await preview(message, state)


async def preview(message: Message, state: FSMContext):
    data = await state.get_data()

    rows = []

    if data.get("btn") and data.get("url"):
        rows = [[
            InlineKeyboardButton(
                text=data["btn"],
                url=data["url"],
            )
        ]]

    keyboard = ik(rows) if rows else None

    if data.get("photo"):
        await message.answer_photo(
            data["photo"],
            caption=data.get("body") or None,
            reply_markup=keyboard,
        )
    else:
        await message.answer(
            data.get("body", ""),
            reply_markup=keyboard,
        )

    await state.update_data(ready=True)

    await message.answer(
        "👀 Предпросмотр готов. Запускать?",
        reply_markup=ik([
            [
                InlineKeyboardButton(
                    text="🚀 Отправить сейчас",
                    callback_data="bc:send",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🕒 Запланировать",
                    callback_data="bc:schedule",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Удалить",
                    callback_data="bc:discard",
                )
            ],
        ]),
    )


@dp.message(BC.schedule)
async def bc_schedule(message: Message, state: FSMContext):
    if not await om(message):
        return

    try:
        dt = datetime.strptime(
            (message.text or "").strip(),
            "%Y-%m-%d %H:%M",
        )

        scheduled = int(dt.timestamp())

        if scheduled <= ts():
            raise ValueError

    except ValueError:
        await message.answer(
            "Неверный формат или время в прошлом.\n"
            "Пример: 2026-12-31 18:30"
        )
        return

    await state.update_data(
        scheduled=scheduled
    )

    await message.answer(
        f"Запланировать на {dt.strftime('%d.%m.%Y %H:%M')} "
        "по часовому поясу сервера?",
        reply_markup=ik([
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить",
                    callback_data="bc:confirm_schedule",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отмена",
                    callback_data="bc:discard",
                )
            ],
        ]),
    )


@dp.message(BC.cancel_id)
async def cancel_schedule(message: Message, state: FSMContext):
    if not await om(message):
        return

    if not (message.text or "").isdigit():
        await message.answer("Отправь числовой ID.")
        return

    with db() as c:
        result = c.execute(
            "UPDATE broadcasts SET status='Отменена' "
            "WHERE id=? AND status='Запланирована'",
            (int(message.text),),
        )

    await state.clear()

    await message.answer(
        "Запланированная рассылка отменена."
        if result.rowcount
        else "Рассылка не найдена."
    )


# ==================== ВЫПОЛНЕНИЕ РАССЫЛКИ ====================

async def run_bc(bot: Bot, broadcast_id: int):
    if broadcast_id in active:
        return

    active.add(broadcast_id)

    try:
        with db() as c:
            broadcast = c.execute(
                "SELECT * FROM broadcasts WHERE id=?",
                (broadcast_id,),
            ).fetchone()

            if not broadcast:
                return

            if broadcast["status"] not in (
                "В очереди",
                "Запланирована",
            ):
                return

            result = c.execute(
                "UPDATE broadcasts SET status='Выполняется' "
                "WHERE id=? AND status IN ('В очереди','Запланирована')",
                (broadcast_id,),
            )

            if not result.rowcount:
                return

            users = c.execute(
                "SELECT id FROM users "
                "WHERE blocked=0 ORDER BY id"
            ).fetchall()

        sent = 0
        failed = 0

        for user in users:
            if broadcast_id in stop:
                break

            keyboard = None

            if broadcast["btn"] and broadcast["url"]:
                keyboard = ik([[
                    InlineKeyboardButton(
                        text=broadcast["btn"],
                        url=broadcast["url"],
                    )
                ]])

            try:
                if broadcast["photo"]:
                    await bot.send_photo(
                        user["id"],
                        broadcast["photo"],
                        caption=broadcast["body"] or None,
                        reply_markup=keyboard,
                    )
                else:
                    await bot.send_message(
                        user["id"],
                        broadcast["body"],
                        reply_markup=keyboard,
                    )

                sent += 1

            except TelegramForbiddenError:
                failed += 1

                with db() as c:
                    c.execute(
                        "UPDATE users SET blocked=1 WHERE id=?",
                        (user["id"],),
                    )

            except TelegramAPIError as error:
                failed += 1

                logging.warning(
                    "Рассылка %s, пользователь %s: %s",
                    broadcast_id,
                    user["id"],
                    error,
                )

            with db() as c:
                c.execute(
                    "UPDATE broadcasts SET sent=?,failed=? WHERE id=?",
                    (sent, failed, broadcast_id),
                )

            await asyncio.sleep(DELAY)

        status = (
            "Остановлена"
            if broadcast_id in stop
            else "Завершена"
        )

        with db() as c:
            c.execute(
                "UPDATE broadcasts SET status=?,sent=?,failed=? "
                "WHERE id=?",
                (status, sent, failed, broadcast_id),
            )

        audit(
            "broadcast_finished",
            f"id={broadcast_id}; status={status}; "
            f"sent={sent}; failed={failed}",
        )

        try:
            await bot.send_message(
                OWNER_ID,
                f"📢 Рассылка #{broadcast_id}: {status}\n"
                f"Успешно отправлено: {sent}\n"
                f"Ошибки: {failed}",
            )
        except TelegramAPIError:
            pass

    except Exception:
        logging.exception(
            "Ошибка рассылки %s",
            broadcast_id,
        )

        with db() as c:
            c.execute(
                "UPDATE broadcasts SET status='Ошибка' WHERE id=?",
                (broadcast_id,),
            )

    finally:
        active.discard(broadcast_id)
        stop.discard(broadcast_id)


async def scheduler(bot: Bot):
    while True:
        try:
            with db() as c:
                rows = c.execute(
                    "SELECT id FROM broadcasts "
                    "WHERE status='Запланирована' AND scheduled<=?",
                    (ts(),),
                ).fetchall()

            for row in rows:
                asyncio.create_task(
                    run_bc(bot, row["id"])
                )

        except Exception:
            logging.exception("Ошибка планировщика")

        await asyncio.sleep(10)


# ==================== КОМАНДЫ ВЛАДЕЛЬЦА ====================

@dp.message(Command("addproduct"))
async def add_product(message: Message):
    if not await om(message):
        return

    parts = [
        item.strip()
        for item in (message.text or "").partition(" ")[2].split("|")
    ]

    if len(parts) != 3:
        await message.answer(
            "Формат:\n"
            "/addproduct Название | Описание | Цена"
        )
        return

    try:
        price = int(parts[2])

        if price < 0:
            raise ValueError

    except ValueError:
        await message.answer(
            "Цена должна быть числом не меньше нуля."
        )
        return

    with db() as c:
        product_id = c.execute(
            "INSERT INTO products(title,description,price) "
            "VALUES(?,?,?)",
            (
                parts[0][:100],
                parts[1][:1000],
                price,
            ),
        ).lastrowid

    audit("addproduct", str(product_id))

    await message.answer(
        f"✅ Товар добавлен. ID: {product_id}"
    )


@dp.message(Command("user"))
async def user_lookup(message: Message):
    if not await om(message):
        return

    parts = (message.text or "").split()

    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer(
            "Формат: /user TELEGRAM_ID"
        )
        return

    with db() as c:
        user = c.execute(
            "SELECT * FROM users WHERE id=?",
            (int(parts[1]),),
        ).fetchone()

    if not user:
        await message.answer("Пользователь не найден.")
        return

    await message.answer(
        f"ID: {user['id']}\n"
        f"Имя: {html.escape(user['name'] or '')}\n"
        f"Баллы: {user['points']}\n"
        f"VIP: {html.escape(user['vip'])}"
    )


@dp.message(Command("setvip"))
async def set_vip(message: Message):
    if not await om(message):
        return

    parts = (message.text or "").split(maxsplit=2)

    if len(parts) != 3 or not parts[1].isdigit():
        await message.answer(
            "Формат: /setvip ID Статус"
        )
        return

    with db() as c:
        result = c.execute(
            "UPDATE users SET vip=? WHERE id=?",
            (parts[2][:50], int(parts[1])),
        )

    if result.rowcount:
        audit("setvip", f"{parts[1]}={parts[2]}")
        await message.answer("VIP обновлён.")
    else:
        await message.answer("Пользователь не найден.")


# ==================== ЗАПУСК ====================

async def main():
    init()

    bot = Bot(
        TOKEN,
        default=DefaultBotProperties(
            parse_mode=ParseMode.HTML,
        ),
    )

    asyncio.create_task(
        scheduler(bot)
    )

    with db() as c:
        c.execute(
            "UPDATE broadcasts SET status='В очереди' "
            "WHERE status='Выполняется'"
        )

        queued = c.execute(
            "SELECT id FROM broadcasts WHERE status='В очереди'"
        ).fetchall()

    for row in queued:
        asyncio.create_task(
            run_bc(bot, row["id"])
        )

    logging.info(
        "PROTIVZLA запущен. Owner ID=%s, DB=%s",
        OWNER_ID,
        DB_PATH,
    )

    await dp.start_polling(
        async def health(request):
return web.Response(text=“PROTIVZLA OK”)

app = web.Application()
app.router.add_get("/", health)
runner = web.AppRunner(app)
await runner.setup()
port = int(os.environ.get("PORT", "10000"))
site = web.TCPSite(runner, "0.0.0.0", port)
await site.start()
logging.info("HTTP server started on port %s", port)
        bot,
        allowed_updates=dp.resolve_used_update_types(),
    )


if __name__ == "__main__":
    asyncio.run(main())

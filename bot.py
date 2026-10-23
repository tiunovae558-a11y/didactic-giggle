import asyncio
import logging
import os
import random
import sqlite3
import string
from datetime import date
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)

# =========================
# НАСТРОЙКИ
# =========================

TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@suqexo")
DB_PATH = os.getenv("DB_PATH", "protivzla.db")
PORT = int(os.getenv("PORT", "10000"))

logging.basicConfig(level=logging.INFO)

bot = Bot(
    TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)

dp = Dispatcher()


# =========================
# БАЗА ДАННЫХ
# =========================

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                balance INTEGER DEFAULT 0,
                vip INTEGER DEFAULT 0,
                last_reward TEXT DEFAULT ''
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                price INTEGER DEFAULT 0
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                product_id INTEGER,
                status TEXT DEFAULT 'Новая',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS favorites (
                user_id INTEGER,
                product_id INTEGER,
                UNIQUE(user_id, product_id)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS promos (
                code TEXT PRIMARY KEY,
                reward INTEGER DEFAULT 0
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS used_promos (
                user_id INTEGER,
                code TEXT,
                UNIQUE(user_id, code)
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                text TEXT,
                status TEXT DEFAULT 'Открыта'
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        count = conn.execute(
            "SELECT COUNT(*) FROM products"
        ).fetchone()[0]

        if count == 0:
            products = [
                (
                    "⭐ Звёздный набор",
                    "Демонстрационный товар магазина",
                    100,
                ),
                (
                    "🎁 Подарочный набор",
                    "Оформление заказа через администратора",
                    200,
                ),
                (
                    "💎 VIP",
                    "Статус VIP в магазине",
                    300,
                ),
            ]

            conn.executemany(
                "INSERT INTO products(name, description, price) "
                "VALUES (?, ?, ?)",
                products,
            )


def register_user(user_id, username):
    with db() as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO users(user_id, username)
            VALUES (?, ?)
            """,
            (user_id, username or ""),
        )

        conn.execute(
            "UPDATE users SET username=? WHERE user_id=?",
            (username or "", user_id),
        )


# =========================
# КЛАВИАТУРЫ
# =========================

def main_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛍 Магазин",
                    callback_data="shop",
                ),
                InlineKeyboardButton(
                    text="👤 Профиль",
                    callback_data="profile",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❤️ Избранное",
                    callback_data="favorites",
                ),
                InlineKeyboardButton(
                    text="🎁 Награда",
                    callback_data="reward",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🎟 Промокод",
                    callback_data="promo",
                ),
                InlineKeyboardButton(
                    text="🔤 Генератор",
                    callback_data="generator",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📦 Мои заказы",
                    callback_data="orders",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🆘 Поддержка",
                    callback_data="support",
                ),
            ],
        ]
    )


def back_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Главное меню",
                    callback_data="home",
                )
            ]
        ]
    )


def is_owner(user_id):
    return OWNER_ID != 0 and user_id == OWNER_ID


# =========================
# СТАРТ
# =========================

@dp.message(CommandStart())
async def start(message: Message):
    register_user(
        message.from_user.id,
        message.from_user.username,
    )

    await message.answer(
        "🔥 <b>PROTIVZLA ULTIMATE</b>\n\n"
        "Добро пожаловать в магазин!\n"
        "Выбирай нужный раздел в меню ниже.",
        reply_markup=main_keyboard(),
    )


@dp.message(Command("admin"))
async def admin_command(message: Message):
    if not is_owner(message.from_user.id):
        await message.answer("⛔ Нет доступа.")
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 Статистика",
                    callback_data="admin_stats",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📦 Заказы",
                    callback_data="admin_orders",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👥 Пользователи",
                    callback_data="admin_users",
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎟 Создать промокод",
                    callback_data="admin_promo",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📣 Рассылка",
                    callback_data="admin_broadcast",
                )
            ],
        ]
    )

    await message.answer(
        "🛠 <b>Панель администратора</b>",
        reply_markup=keyboard,
    )


# =========================
# ГЛАВНОЕ МЕНЮ
# =========================

@dp.callback_query(F.data == "home")
async def home(call: CallbackQuery):
    await call.message.answer(
        "🔥 <b>PROTIVZLA ULTIMATE</b>",
        reply_markup=main_keyboard(),
    )
    await call.answer()


# =========================
# МАГАЗИН
# =========================

@dp.callback_query(F.data == "shop")
async def shop(call: CallbackQuery):
    with db() as conn:
        products = conn.execute(
            "SELECT * FROM products ORDER BY id"
        ).fetchall()

    keyboard = []

    for product in products:
        keyboard.append([
            InlineKeyboardButton(
                text=(
                    f"{product['name']} — "
                    f"{product['price']} ⭐"
                ),
                callback_data=f"product:{product['id']}",
            )
        ])

    keyboard.append([
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data="home",
        )
    ])

    await call.message.answer(
        "🛍 <b>Магазин</b>\n\nВыбери товар:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=keyboard
        ),
    )

    await call.answer()


@dp.callback_query(F.data.startswith("product:"))
async def product_page(call: CallbackQuery):
    product_id = int(call.data.split(":")[1])

    with db() as conn:
        product = conn.execute(
            "SELECT * FROM products WHERE id=?",
            (product_id,),
        ).fetchone()

    if not product:
        await call.answer("Товар не найден", show_alert=True)
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🛒 Заказать",
                    callback_data=f"buy:{product_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❤️ В избранное",
                    callback_data=f"fav:{product_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ В магазин",
                    callback_data="shop",
                )
            ],
        ]
    )

    await call.message.answer(
        f"<b>{product['name']}</b>\n\n"
        f"{product['description']}\n\n"
        f"Цена: <b>{product['price']} ⭐</b>\n\n"
        "Оформление заказа не означает автоматическую "
        "оплату или выдачу товара.",
        reply_markup=keyboard,
    )

    await call.answer()


@dp.callback_query(F.data.startswith("buy:"))
async def buy_product(call: CallbackQuery):
    product_id = int(call.data.split(":")[1])

    with db() as conn:
        product = conn.execute(
            "SELECT * FROM products WHERE id=?",
            (product_id,),
        ).fetchone()

        if not product:
            await call.answer(
                "Товар не найден",
                show_alert=True,
            )
            return

        cursor = conn.execute(
            """
            INSERT INTO orders(user_id, product_id)
            VALUES (?, ?)
            """,
            (call.from_user.id, product_id),
        )

        order_id = cursor.lastrowid

    await call.message.answer(
        f"✅ Заказ №{order_id} создан!\n\n"
        f"Товар: {product['name']}\n"
        "Статус: Новая заявка\n\n"
        f"Поддержка: {SUPPORT_USERNAME}",
        reply_markup=back_keyboard(),
    )

    if OWNER_ID:
        try:
            await bot.send_message(
                OWNER_ID,
                f"📦 <b>Новый заказ №{order_id}</b>\n"
                f"Пользователь: {call.from_user.id}\n"
                f"Товар: {product['name']}",
            )
        except Exception:
            logging.exception("Не удалось уведомить владельца")

    await call.answer("Заказ создан!")


# =========================
# ИЗБРАННОЕ
# =========================

@dp.callback_query(F.data.startswith("fav:"))
async def favorite_product(call: CallbackQuery):
    product_id = int(call.data.split(":")[1])

    with db() as conn:
        exists = conn.execute(
            """
            SELECT 1 FROM favorites
            WHERE user_id=? AND product_id=?
            """,
            (call.from_user.id, product_id),
        ).fetchone()

        if exists:
            conn.execute(
                """
                DELETE FROM favorites
                WHERE user_id=? AND product_id=?
                """,
                (call.from_user.id, product_id),
            )
            text = "Удалено из избранного."
        else:
            conn.execute(
                """
                INSERT OR IGNORE INTO favorites(user_id, product_id)
                VALUES (?, ?)
                """,
                (call.from_user.id, product_id),
            )
            text = "Добавлено в избранное!"

    await call.answer(text, show_alert=True)


@dp.callback_query(F.data == "favorites")
async def favorites_page(call: CallbackQuery):
    with db() as conn:
        products = conn.execute(
            """
            SELECT p.* FROM products p
            JOIN favorites f ON f.product_id=p.id
            WHERE f.user_id=?
            """,
            (call.from_user.id,),
        ).fetchall()

    if not products:
        await call.message.answer(
            "❤️ Избранное пока пусто.",
            reply_markup=back_keyboard(),
        )
    else:
        text = "❤️ <b>Избранное</b>\n\n"

        for p in products:
            text += (
                f"• {p['name']} — {p['price']} ⭐\n"
            )

        await call.message.answer(
            text,
            reply_markup=back_keyboard(),
        )

    await call.answer()


# =========================
# ПРОФИЛЬ И VIP
# =========================

@dp.callback_query(F.data == "profile")
async def profile(call: CallbackQuery):
    with db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE user_id=?",
            (call.from_user.id,),
        ).fetchone()

        orders = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE user_id=?",
            (call.from_user.id,),
        ).fetchone()[0]

    vip = "Активен 💎" if user["vip"] else "Нет"

    await call.message.answer(
        "👤 <b>Твой профиль</b>\n\n"
        f"ID: <code>{call.from_user.id}</code>\n"
        f"Баланс: {user['balance']} ⭐\n"
        f"VIP: {vip}\n"
        f"Заказов: {orders}\n",
        reply_markup=back_keyboard(),
    )

    await call.answer()


# =========================
# ЕЖЕДНЕВНАЯ НАГРАДА
# =========================

@dp.callback_query(F.data == "reward")
async def daily_reward(call: CallbackQuery):
    today = date.today().isoformat()

    with db() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE user_id=?",
            (call.from_user.id,),
        ).fetchone()

        if user["last_reward"] == today:
            await call.answer(
                "Ты уже получил награду сегодня!",
                show_alert=True,
            )
            return

        reward = 10

        conn.execute(
            """
            UPDATE users
            SET balance=balance+?, last_reward=?
            WHERE user_id=?
            """,
            (reward, today, call.from_user.id),
        )

    await call.message.answer(
        f"🎁 Ты получил {reward} ⭐!\n"
        "Возвращайся завтра за новой наградой.",
        reply_markup=back_keyboard(),
    )

    await call.answer()


# =========================
# ГЕНЕРАТОР ИМЁН
# =========================

@dp.callback_query(F.data == "generator")
async def generator(call: CallbackQuery):
    names = [
        "protiv", "shadow", "ghost", "storm",
        "night", "venom", "blaze", "hunter",
        "dark", "frost",
    ]

    username = (
        random.choice(names)
        + "_"
        + "".join(
            random.choices(
                string.ascii_lowercase + string.digits,
                k=5,
            )
        )
    )

    await call.message.answer(
        "🔤 <b>Генератор имени</b>\n\n"
        f"<code>{username}</code>\n\n"
        "Это случайный вариант. Доступность имени "
        "не проверялась.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔄 Ещё вариант",
                        callback_data="generator",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Назад",
                        callback_data="home",
                    )
                ],
            ]
        ),
    )

    await call.answer()


# =========================
# ПРОМОКОДЫ
# =========================

@dp.callback_query(F.data == "promo")
async def promo_prompt(call: CallbackQuery):
    await call.message.answer(
        "🎟 Для активации промокода напиши:\n"
        "<code>/promo КОД</code>",
        reply_markup=back_keyboard(),
    )
    await call.answer()


@dp.message(Command("promo"))
async def use_promo(message: Message):
    parts = message.text.split(maxsplit=1)

    if len(parts) != 2:
        await message.answer("Пример: /promo WELCOME")
        return

    code = parts[1].strip().upper()

    with db() as conn:
        promo = conn.execute(
            "SELECT * FROM promos WHERE code=?",
            (code,),
        ).fetchone()

        if not promo:
            await message.answer("❌ Промокод не найден.")
            return

        used = conn.execute(
            """
            SELECT 1 FROM used_promos
            WHERE user_id=? AND code=?
            """,
            (message.from_user.id, code),
        ).fetchone()

        if used:
            await message.answer(
                "Этот промокод уже использован."
            )
            return

        conn.execute(
            """
            INSERT INTO used_promos(user_id, code)
            VALUES (?, ?)
            """,
            (message.from_user.id, code),
        )

        conn.execute(
            """
            UPDATE users SET balance=balance+?
            WHERE user_id=?
            """,
            (promo["reward"], message.from_user.id),
        )

    await message.answer(
        f"✅ Промокод активирован!\n"
        f"Начислено: {promo['reward']} ⭐"
    )


# =========================
# ЗАКАЗЫ
# =========================

@dp.callback_query(F.data == "orders")
async def my_orders(call: CallbackQuery):
    with db() as conn:
        orders = conn.execute(
            """
            SELECT o.id, o.status, p.name
            FROM orders o
            JOIN products p ON p.id=o.product_id
            WHERE o.user_id=?
            ORDER BY o.id DESC LIMIT 20
            """,
            (call.from_user.id,),
        ).fetchall()

    if not orders:
        text = "📦 У тебя пока нет заказов."
    else:
        text = "📦 <b>Твои заказы</b>\n\n"

        for order in orders:
            text += (
                f"№{order['id']} — {order['name']}\n"
                f"Статус: {order['status']}\n\n"
            )

    await call.message.answer(
        text,
        reply_markup=back_keyboard(),
    )

    await call.answer()


# =========================
# ПОДДЕРЖКА
# =========================

@dp.callback_query(F.data == "support")
async def support(call: CallbackQuery):
    await call.message.answer(
        "🆘 <b>Поддержка</b>\n\n"
        f"Напиши нам: {SUPPORT_USERNAME}\n\n"
        "Также можешь создать обращение командой:\n"
        "<code>/ticket текст обращения</code>",
        reply_markup=back_keyboard(),
    )

    await call.answer()


@dp.message(Command("ticket"))
async def create_ticket(message: Message):
    parts = message.text.split(maxsplit=1)

    if len(parts) < 2 or not parts[1].strip():
        await message.answer(
            "Напиши обращение после команды /ticket."
        )
        return

    with db() as conn:
        cursor = conn.execute(
            "INSERT INTO tickets(user_id, text) VALUES (?, ?)",
            (message.from_user.id, parts[1].strip()),
        )
        ticket_id = cursor.lastrowid

    await message.answer(
        f"✅ Обращение №{ticket_id} создано."
    )

    if OWNER_ID:
        try:
            await bot.send_message(
                OWNER_ID,
                f"🆘 <b>Обращение №{ticket_id}</b>\n"
                f"От пользователя: {message.from_user.id}\n\n"
                f"{parts[1].strip()}\n\n"
                f"Ответить: /reply {message.from_user.id} текст",
            )
        except Exception:
            logging.exception("Ошибка уведомления о тикете")


@dp.message(Command("reply"))
async def reply_user(message: Message):
    if not is_owner(message.from_user.id):
        return

    parts = message.text.split(maxsplit=2)

    if len(parts) < 3:
        await message.answer(
            "Формат: /reply USER_ID текст"
        )
        return

    try:
        target_id = int(parts[1])
        await bot.send_message(
            target_id,
            "🆘 <b>Ответ поддержки</b>\n\n" + parts[2],
        )
        await message.answer("✅ Ответ отправлен.")
    except Exception as exc:
        await message.answer(
            f"Не удалось отправить ответ: {exc}"
        )


# =========================
# АДМИН-ПАНЕЛЬ
# =========================

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return

    with db() as conn:
        users = conn.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        orders = conn.execute(
            "SELECT COUNT(*) FROM orders"
        ).fetchone()[0]

        tickets = conn.execute(
            "SELECT COUNT(*) FROM tickets WHERE status='Открыта'"
        ).fetchone()[0]

    await call.message.answer(
        "📊 <b>Статистика</b>\n\n"
        f"Пользователей: {users}\n"
        f"Заказов: {orders}\n"
        f"Открытых обращений: {tickets}"
    )

    await call.answer()


@dp.callback_query(F.data == "admin_users")
async def admin_users(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return

    with db() as conn:
        users = conn.execute(
            "SELECT user_id, username, balance, vip "
            "FROM users ORDER BY user_id DESC LIMIT 20"
        ).fetchall()

    text = "👥 <b>Последние пользователи</b>\n\n"

    for user in users:
        text += (
            f"ID: <code>{user['user_id']}</code>\n"
            f"Username: @{user['username'] or 'нет'}\n"
            f"Баланс: {user['balance']} ⭐\n"
            f"VIP: {'Да' if user['vip'] else 'Нет'}\n\n"
        )

    await call.message.answer(text or "Пользователей пока нет.")
    await call.answer()


@dp.callback_query(F.data == "admin_orders")
async def admin_orders(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return

    with db() as conn:
        orders = conn.execute(
            """
            SELECT o.id, o.user_id, o.status, p.name
            FROM orders o
            JOIN products p ON p.id=o.product_id
            ORDER BY o.id DESC LIMIT 20
            """
        ).fetchall()

    if not orders:
        text = "Заказов пока нет."
    else:
        text = "📦 <b>Последние заказы</b>\n\n"

        for order in orders:
            text += (
                f"№{order['id']} — {order['name']}\n"
                f"Пользователь: {order['user_id']}\n"
                f"Статус: {order['status']}\n\n"
            )

    await call.message.answer(text)
    await call.answer()


@dp.callback_query(F.data == "admin_promo")
async def admin_promo_prompt(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return

    await call.message.answer(
        "Создай промокод командой:\n"
        "<code>/makepromo КОД СУММА</code>\n\n"
        "Пример: <code>/makepromo WELCOME 50</code>"
    )

    await call.answer()


@dp.message(Command("makepromo"))
async def make_promo(message: Message):
    if not is_owner(message.from_user.id):
        return

    parts = message.text.split()

    if len(parts) != 3:
        await message.answer(
            "Формат: /makepromo КОД СУММА"
        )
        return

    try:
        code = parts[1].upper()
        reward = int(parts[2])

        if reward <= 0:
            raise ValueError

        with db() as conn:
            conn.execute(
                "INSERT INTO promos(code, reward) VALUES (?, ?)",
                (code, reward),
            )

        await message.answer(
            f"✅ Промокод {code} создан. Награда: {reward} ⭐"
        )

    except sqlite3.IntegrityError:
        await message.answer("Такой промокод уже существует.")
    except ValueError:
        await message.answer("Укажи положительное число награды.")


# =========================
# РАССЫЛКА
# =========================

@dp.callback_query(F.data == "admin_broadcast")
async def admin_broadcast_prompt(call: CallbackQuery):
    if not is_owner(call.from_user.id):
        await call.answer("Нет доступа", show_alert=True)
        return

    await call.message.answer(
        "📣 Чтобы начать рассылку, отправь:\n"
        "<code>/broadcast текст сообщения</code>\n\n"
        "Рассылка предназначена пользователям, "
        "которые запускали бота."
    )

    await call.answer()


@dp.message(Command("broadcast"))
async def broadcast(message: Message):
    if not is_owner(message.from_user.id):
        return

    parts = message.text.split(maxsplit=1)

    if len(parts) < 2:
        await message.answer(
            "Формат: /broadcast текст сообщения"
        )
        return

    with db() as conn:
        users = conn.execute(
            "SELECT user_id FROM users"
        ).fetchall()

    sent = 0
    failed = 0

    status = await message.answer(
        f"📣 Начинаю рассылку {len(users)} пользователям..."
    )

    for user in users:
        try:
            await bot.send_message(
                user["user_id"],
                parts[1],
            )
            sent += 1
        except Exception:
            failed += 1

        await asyncio.sleep(0.08)

    await status.edit_text(
        "✅ <b>Рассылка завершена</b>\n\n"
        f"Отправлено: {sent}\n"
        f"Ошибок: {failed}"
    )


# =========================
# HTTP ДЛЯ RENDER
# =========================

async def health(request):
    return web.Response(text="PROTIVZLA OK")


async def start_http_server():
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT,
    )

    await site.start()

    logging.info("HTTP server started on port %s", PORT)

    return runner


# =========================
# ЗАПУСК
# =========================

async def main():
    if not TOKEN:
        raise RuntimeError(
            "Не задан BOT_TOKEN в переменных окружения Render"
        )

    if OWNER_ID == 0:
        raise RuntimeError(
            "Не задан OWNER_ID в переменных окружения Render"
        )

    init_db()

    runner = await start_http_server()

    try:
        logging.info(
            "PROTIVZLA запущен. Owner ID=%s, DB=%s",
            OWNER_ID,
            DB_PATH,
        )

        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
        )
    finally:
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

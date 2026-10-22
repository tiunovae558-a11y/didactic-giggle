from aiogram.webhook.aiohttp_server import (
    SimpleRequestHandler,
    setup_application,
)

import os
import asyncio
import sqlite3
import logging
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
    PreCheckoutQuery,
)

# =========================
# НАСТРОЙКИ METROSHOP
# =========================

TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_IDS = [
    int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()
]

CHANNEL_ID = os.getenv("CHANNEL_ID", "")
CHANNEL_URL = os.getenv("CHANNEL_URL", "")

if not TOKEN:
    raise RuntimeError("Добавь BOT_TOKEN в переменные окружения!")

bot = Bot(TOKEN)
dp = Dispatcher()

logging.basicConfig(level=logging.INFO)

# =========================
# БАЗА ДАННЫХ
# =========================

db = sqlite3.connect("metroshop.db")
db.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    product TEXT,
    price INTEGER,
    status TEXT DEFAULT 'paid'
)
""")
db.commit()

# =========================
# ТОВАРЫ
# =========================

PRODUCTS = {
    "sites": {
        "name": "🌐 Сайты",
        "items": [
            ("site1", "💻 Современный сайт", 100),
            ("site2", "🖥️ Бизнес-сайт", 100),
            ("site3", "🌌 Тёмный сайт", 100),
        ],
    },
    "accounts": {
        "name": "📱 Telegram-аккаунты",
        "items": [
            ("acc1", "🇺🇸 США", 50),
            ("acc2", "🇬🇧 Великобритания", 50),
            ("acc3", "🇩🇪 Германия", 50),
            ("acc4", "🇫🇷 Франция", 50),
            ("acc5", "🇮🇹 Италия", 50),
            ("acc6", "🇪🇸 Испания", 50),
            ("acc7", "🇹🇷 Турция", 50),
            ("acc8", "🇧🇷 Бразилия", 50),
            ("acc9", "🇮🇳 Индия", 50),
            ("acc10", "🇯🇵 Япония", 50),
            ("acc11", "🇰🇷 Южная Корея", 50),
            ("acc12", "🇨🇦 Канада", 50),
            ("acc13", "🇦🇺 Австралия", 50),
            ("acc14", "🇸🇪 Швеция", 50),
        ],
    },
    "gifts": {
        "name": "🎁 Подарки",
        "items": [
            ("gift1", "🧸 Мишка", 15),
            ("gift2", "❤️ Сердце", 15),
            ("gift3", "🌹 Роза", 25),
            ("gift4", "🎁 Подарок", 25),
            ("gift5", "🎂 Торт", 50),
            ("gift6", "🥂 Шампанское", 50),
            ("gift7", "💐 Цветы", 50),
            ("gift8", "🏆 Кубок", 100),
            ("gift9", "💍 Кольцо", 100),
            ("gift10", "💎 Алмазик", 100),
        ],
    },
}

# =========================
# КЛАВИАТУРЫ
# =========================

def main_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="🌐 Сайты",
            callback_data="category:sites"
        )],
        [InlineKeyboardButton(
            text="📱 Аккаунты",
            callback_data="category:accounts"
        )],
        [InlineKeyboardButton(
            text="🎁 Подарки",
            callback_data="category:gifts"
        )],
        [InlineKeyboardButton(
            text="👤 Поддержка",
            url="https://t.me/"
        )],
    ])


def products_keyboard(category):
    rows = []

    for product_id, name, price in PRODUCTS[category]["items"]:
        rows.append([
            InlineKeyboardButton(
                text=f"{name} — {price} ⭐",
                callback_data=f"buy:{category}:{product_id}"
            )
        ])

    rows.append([
        InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data="home"
        )
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


# =========================
# ПРОВЕРКА ПОДПИСКИ
# =========================

async def subscribed(user_id):
    if not CHANNEL_ID:
        return True

    try:
        member = await bot.get_chat_member(
            chat_id=CHANNEL_ID,
            user_id=user_id
        )

        return member.status in ("member", "administrator", "creator")

    except Exception:
        return False


async def require_subscription(message: Message):
    if await subscribed(message.from_user.id):
        return True

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📢 Подписаться на канал",
            url=CHANNEL_URL
        )],
        [InlineKeyboardButton(
            text="✅ Проверить подписку",
            callback_data="check_sub"
        )],
    ])

    await message.answer(
        "🔒 Чтобы пользоваться METROSHOP, "
        "сначала подпишись на наш канал!",
        reply_markup=keyboard
    )
    return False


# =========================
# СТАРТ
# =========================

@dp.message(CommandStart())
async def start(message: Message):
    db.execute(
        "INSERT OR IGNORE INTO users(user_id) VALUES (?)",
        (message.from_user.id,)
    )
    db.commit()

    if not await require_subscription(message):
        return

    await message.answer(
        "💙 <b>METROSHOP</b>\n\n"
        "Добро пожаловать в наш магазин!\n\n"
        "🌐 Сайты — 100 ⭐\n"
        "📱 Аккаунты — 50 ⭐\n"
        "🎁 Подарки — от 15 ⭐\n\n"
        "Выбирай категорию ниже 👇",
        reply_markup=main_keyboard(),
        parse_mode="HTML"
    )


@dp.callback_query(F.data == "check_sub")
async def check_sub(callback: CallbackQuery):
    if await subscribed(callback.from_user.id):
        await callback.message.answer(
            "💙 Подписка подтверждена!",
            reply_markup=main_keyboard()
        )
    else:
        await callback.answer(
            "Сначала подпишись на канал!",
            show_alert=True
        )

    await callback.answer()


@dp.callback_query(F.data == "home")
async def home(callback: CallbackQuery):
    if not await subscribed(callback.from_user.id):
        await callback.answer(
            "Сначала подпишись на канал!",
            show_alert=True
        )
        return

    await callback.message.edit_text(
        "💙 METROSHOP\n\nВыбери категорию:",
        reply_markup=main_keyboard()
    )
    await callback.answer()


# =========================
# КАТАЛОГ
# =========================

@dp.callback_query(F.data.startswith("category:"))
async def category(callback: CallbackQuery):
    if not await subscribed(callback.from_user.id):
        await callback.answer(
            "Сначала подпишись на канал!",
            show_alert=True
        )
        return

    category_id = callback.data.split(":")[1]

    await callback.message.edit_text(
        f"💙 <b>{PRODUCTS[category_id]['name']}</b>\n\n"
        "Выбери товар:",
        reply_markup=products_keyboard(category_id),
        parse_mode="HTML"
    )
    await callback.answer()


# =========================
# ПОКУПКА И ОПЛАТА
# =========================

@dp.callback_query(F.data.startswith("buy:"))
async def buy(callback: CallbackQuery):
    if not await subscribed(callback.from_user.id):
        await callback.answer(
            "Сначала подпишись на канал!",
            show_alert=True
        )
        return

    _, category_id, product_id = callback.data.split(":")

    product = next(
        (
            item for item in PRODUCTS[category_id]["items"]
            if item[0] == product_id
        ),
        None
    )

    if not product:
        await callback.answer("Товар не найден", show_alert=True)
        return

    _, name, price = product

    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=name,
        description=f"Покупка в METROSHOP: {name}",
        payload=f"{category_id}:{product_id}",
        currency="XTR",
        prices=[LabeledPrice(label=name, amount=price)]
    )

    await callback.answer()


@dp.pre_checkout_query()
async def pre_checkout(pre_checkout: PreCheckoutQuery):
    if not await subscribed(pre_checkout.from_user.id):
        await pre_checkout.answer(
            ok=False,
            error_message="Сначала подпишись на канал."
        )
        return

    await pre_checkout.answer(ok=True)


@dp.message(F.successful_payment)
async def successful_payment(message: Message):
    payment = message.successful_payment
    user_id = message.from_user.id

    db.execute(
        "INSERT INTO orders(user_id, product, price) VALUES (?, ?, ?)",
        (
            user_id,
            payment.invoice_payload,
            payment.total_amount
        )
    )
    db.commit()

    order_id = db.execute(
        "SELECT last_insert_rowid()"
    ).fetchone()[0]

    await message.answer(
        f"💙 Оплата прошла успешно!\n\n"
        f"🧾 Заказ №{order_id}\n"
        f"⭐ Оплачено: {payment.total_amount} Stars\n\n"
        "Заказ сохранён. Администратор свяжется с тобой "
        "для получения товара."
    )

    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"💰 <b>Новый заказ METROSHOP!</b>\n\n"
                f"🧾 Заказ: #{order_id}\n"
                f"👤 Покупатель: {user_id}\n"
                f"📦 Товар: {payment.invoice_payload}\n"
                f"⭐ Сумма: {payment.total_amount}",
                parse_mode="HTML"
            )
        except Exception:
            logging.exception("Не удалось уведомить администратора")


# =========================
# АДМИН-ПАНЕЛЬ
# =========================

@dp.message(Command("admin"))
async def admin(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ Нет доступа.")
        return

    total_orders = db.execute(
        "SELECT COUNT(*) FROM orders"
    ).fetchone()[0]

    total_stars = db.execute(
        "SELECT COALESCE(SUM(price), 0) FROM orders"
    ).fetchone()[0]

    total_users = db.execute(
        "SELECT COUNT(*) FROM users"
    ).fetchone()[0]

    await message.answer(
        "👑 <b>METROSHOP ADMIN</b>\n\n"
        f"👥 Пользователей: {total_users}\n"
        f"📦 Заказов: {total_orders}\n"
        f"⭐ Получено Stars: {total_stars}\n\n"
        "Команды:\n"
        "/orders — последние заказы\n"
        "/users — количество пользователей",
        parse_mode="HTML"
    )


@dp.message(Command("orders"))
async def orders(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    rows = db.execute(
        "SELECT id, user_id, product, price "
        "FROM orders ORDER BY id DESC LIMIT 20"
    ).fetchall()

    if not rows:
        await message.answer("Пока заказов нет.")
        return

    text = "📦 <b>Последние заказы</b>\n\n"

    for order_id, user_id, product, price in rows:
        text += (
            f"#{order_id} | Пользователь: {user_id}\n"
            f"Товар: {product} | {price} ⭐\n\n"
        )

    await message.answer(text, parse_mode="HTML")


@dp.message(Command("users"))
async def users(message: Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    count = db.execute(
        "SELECT COUNT(*) FROM users"
    ).fetchone()[0]

    await message.answer(f"👥 Пользователей: {count}")


# =========================
# ЗАПУСК
# =========================
from aiogram.webhook.aiohttp_server import (
    SimpleRequestHandler,
    setup_application,
)

def main():
    app = web.Application()

    SimpleRequestHandler(
        dispatcher=dp,
        bot=bot,
        secret_token=WEBHOOK_SECRET,
    ).register(app, path=WEBHOOK_PATH)

    setup_application(app, dp, bot=bot)

    async def on_startup(app):
        if not WEBHOOK_SECRET or not os.getenv("RENDER_EXTERNAL_URL"):
            raise RuntimeError("Webhook settings are missing")

        await bot.set_webhook(
            WEBHOOK_URL,
            secret_token=WEBHOOK_SECRET,
        )

    app.on_startup.append(on_startup)

    web.run_app(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "10000")),
    )


if __name__ == "__main__":
    main()

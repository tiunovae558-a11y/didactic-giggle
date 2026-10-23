import os
import re
import ssl
import random
import sqlite3
import logging
import socket
from datetime import datetime, timezone
from urllib.parse import urlparse
from html.parser import HTMLParser

import httpx
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    KeyboardButton,
    LabeledPrice,
)
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    ContextTypes,
    filters,
)

# ================= CONFIG =================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

REQUIRED_CHAT = os.getenv("REQUIRED_CHAT", "@ProtivzlaNFT")
REQUIRED_CHAT_URL = os.getenv(
    "REQUIRED_CHAT_URL",
    "https://t.me/ProtivzlaNFT",
)

DB_PATH = os.getenv("DB_PATH", "bot.db")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================= DATABASE =================

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            joined_at TEXT,
            blocked INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS actions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT,
            details TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS promos (
            code TEXT PRIMARY KEY,
            bonus TEXT,
            max_uses INTEGER DEFAULT 1,
            uses INTEGER DEFAULT 0,
            expires_at TEXT,
            active INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS promo_uses (
            user_id INTEGER,
            code TEXT,
            used_at TEXT,
            PRIMARY KEY(user_id, code)
        );

        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            kind TEXT,
            value TEXT,
            created_at TEXT,
            UNIQUE(user_id, kind, value)
        );

        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            description TEXT,
            price_stars INTEGER DEFAULT 0,
            active INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            product_id INTEGER,
            product_name TEXT,
            price_stars INTEGER,
            status TEXT DEFAULT 'created',
            created_at TEXT,
            telegram_payment_charge_id TEXT UNIQUE
        );

        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            message TEXT,
            status TEXT DEFAULT 'open',
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS admins (
            user_id INTEGER PRIMARY KEY
        );
        """)

        for admin_id in ADMIN_IDS:
            db.execute(
                "INSERT OR IGNORE INTO admins(user_id) VALUES(?)",
                (admin_id,),
            )


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def save_user(user):
    with get_db() as db:
        db.execute("""
            INSERT INTO users(user_id, username, first_name, joined_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name
        """, (
            user.id,
            user.username or "",
            user.first_name or "",
            now(),
        ))


def log_action(user_id, action, details=""):
    with get_db() as db:
        db.execute("""
            INSERT INTO actions(user_id, action, details, created_at)
            VALUES (?, ?, ?, ?)
        """, (user_id, action, str(details)[:500], now()))


def is_admin(user_id):
    with get_db() as db:
        return db.execute(
            "SELECT 1 FROM admins WHERE user_id=?",
            (user_id,),
        ).fetchone() is not None


def is_blocked(user_id):
    with get_db() as db:
        row = db.execute(
            "SELECT blocked FROM users WHERE user_id=?",
            (user_id,),
        ).fetchone()

    return bool(row and row["blocked"])


# ================= MAIN MENU =================

MAIN_MENU = [
    [
        KeyboardButton("🔎 DATA HUNTER"),
        KeyboardButton("🌐 WEB TOOLS"),
    ],
    [
        KeyboardButton("🛡️ SECURITY CENTER"),
        KeyboardButton("🎁 GIFTS SHOP"),
    ],
    [
        KeyboardButton("🛍️ PROTIVZLA SHOP"),
        KeyboardButton("👤 USER PROFILE"),
    ],
    [
        KeyboardButton("💬 SUPPORT"),
        KeyboardButton("👑 ADMIN PANEL"),
    ],
    [
        KeyboardButton("🎟️ PROMO CODES"),
        KeyboardButton("📊 STATISTICS"),
    ],
    [
        KeyboardButton("📜 ACTION HISTORY"),
        KeyboardButton("🎭 USERNAME GENERATOR"),
    ],
    [
        KeyboardButton("⭐ FAVORITES"),
    ],
]

MAIN_MARKUP = ReplyKeyboardMarkup(
    MAIN_MENU,
    resize_keyboard=True,
)


def make_inline(rows):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(label, callback_data=data)
            for label, data in row
        ]
        for row in rows
    ])


# ================= SUBSCRIPTION =================

async def check_subscription(update, context):
    user = update.effective_user

    try:
        member = await context.bot.get_chat_member(
            REQUIRED_CHAT,
            user.id,
        )

        if member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        ):
            return True

    except Exception:
        logger.exception("Subscription check failed")

    message = (
        "💚 Чтобы пользоваться ботом, подпишись на канал "
        "и нажми «Проверить подписку»."
    )

    keyboard = make_inline([
        [("📢 Подписаться", "subscribe")],
        [("✅ Проверить подписку", "check_subscription")],
    ])

    if update.callback_query:
        await update.callback_query.message.reply_text(
            message,
            reply_markup=keyboard,
        )
    else:
        await update.effective_message.reply_text(
            message,
            reply_markup=keyboard,
        )

    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user)

    if is_blocked(user.id):
        await update.effective_message.reply_text(
            "⛔ Доступ к боту ограничен."
        )
        return

    log_action(user.id, "start")

    if not await check_subscription(update, context):
        return

    await update.effective_message.reply_text(
        f"💚 Добро пожаловать, {user.first_name}!\n\n"
        "PROTIVZLA ULTIMATE\n"
        "Выбери нужный раздел:",
        reply_markup=MAIN_MARKUP,
    )


async def ensure_access(update, context):
    user = update.effective_user

    if not user:
        return False

    save_user(user)

    if is_blocked(user.id):
        await update.effective_message.reply_text(
            "⛔ Доступ к боту ограничен."
        )
        return False

    return await check_subscription(update, context)
    
# =========================
# PART 2/3 — FEATURES
# =========================

import html
import secrets
import string
from urllib.parse import quote_plus


# ---------- PUBLIC WEB SEARCH ----------

async def public_web_search(query: str, limit: int = 5):
    """Поиск только по общедоступным страницам интернета."""
    results = []

    try:
        async with httpx.AsyncClient(
            timeout=12,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0"}
        ) as client:
            response = await client.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query}
            )
            response.raise_for_status()

        class ResultParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.items = []
                self.current = None
                self.capture_title = False

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)

                if tag == "a" and "result__a" in attrs.get("class", ""):
                    self.current = {
                        "title": "",
                        "url": attrs.get("href", "")
                    }
                    self.capture_title = True

            def handle_data(self, data):
                if self.current and self.capture_title:
                    self.current["title"] += data

            def handle_endtag(self, tag):
                if tag == "a" and self.current and self.capture_title:
                    self.items.append(self.current)
                    self.current = None
                    self.capture_title = False

        parser = ResultParser()
        parser.feed(response.text)
        results = parser.items[:limit]

    except Exception as exc:
        logging.warning("Web search error: %s", exc)

    return results


# ---------- USER PROFILE ----------

async def show_profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    with get_db() as db:
        row = db.execute(
            "SELECT created_at FROM users WHERE user_id = ?",
            (user.id,)
        ).fetchone()

        action_count = db.execute(
            "SELECT COUNT(*) FROM actions WHERE user_id = ?",
            (user.id,)
        ).fetchone()[0]

        favorites_count = db.execute(
            "SELECT COUNT(*) FROM favorites WHERE user_id = ?",
            (user.id,)
        ).fetchone()[0]

        orders_count = db.execute(
            "SELECT COUNT(*) FROM orders WHERE user_id = ?",
            (user.id,)
        ).fetchone()[0]

    joined = row["created_at"] if row else "неизвестно"

    text = (
        "👤 <b>ТВОЙ ПРОФИЛЬ</b>\n\n"
        f"🆔 ID: <code>{user.id}</code>\n"
        f"👤 Имя: {html.escape(user.full_name)}\n"
        f"🔗 Username: @{html.escape(user.username or 'нет')}\n"
        f"📅 В системе с: {html.escape(str(joined))}\n\n"
        f"📊 Действий: {action_count}\n"
        f"⭐ Избранное: {favorites_count}\n"
        f"🛒 Заказов: {orders_count}"
    )

    await update.effective_message.reply_text(
        text,
        parse_mode="HTML"
    )


# ---------- ACTION HISTORY ----------

async def show_history(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user_id = update.effective_user.id

    with get_db() as db:
        rows = db.execute(
            """
            SELECT action, details, created_at
            FROM actions
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 10
            """,
            (user_id,)
        ).fetchall()

    if not rows:
        await update.effective_message.reply_text(
            "📜 История пока пустая."
        )
        return

    lines = ["📜 <b>ПОСЛЕДНИЕ ДЕЙСТВИЯ</b>\n"]

    for row in rows:
        action = html.escape(str(row["action"]))
        details = html.escape(str(row["details"] or ""))
        created = html.escape(str(row["created_at"]))

        lines.append(
            f"• <b>{action}</b>\n"
            f"  {details}\n"
            f"  <i>{created}</i>"
        )

    await update.effective_message.reply_text(
        "\n\n".join(lines),
        parse_mode="HTML"
    )


# ---------- STATISTICS ----------

async def show_statistics(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    with get_db() as db:
        users = db.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        actions = db.execute(
            "SELECT COUNT(*) FROM actions"
        ).fetchone()[0]

        products = db.execute(
            "SELECT COUNT(*) FROM products WHERE active = 1"
        ).fetchone()[0]

        orders = db.execute(
            "SELECT COUNT(*) FROM orders"
        ).fetchone()[0]

    await update.effective_message.reply_text(
        "📊 <b>СТАТИСТИКА PROTIVZLA</b>\n\n"
        f"👥 Пользователей: {users}\n"
        f"⚡ Действий: {actions}\n"
        f"🛍 Активных товаров: {products}\n"
        f"📦 Заказов: {orders}",
        parse_mode="HTML"
    )


# ---------- USERNAME GENERATOR ----------

def generate_username(length: int = 10) -> str:
    """Создаёт случайный вариант username."""
    length = max(5, min(length, 20))

    alphabet = string.ascii_lowercase + string.digits
    first = secrets.choice(string.ascii_lowercase)

    return first + "".join(
        secrets.choice(alphabet)
        for _ in range(length - 1)
    )


async def username_generator(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    usernames = [generate_username() for _ in range(5)]

    keyboard = [
        [
            InlineKeyboardButton(
                f"⭐ {username}",
                callback_data=f"fav_username:{username}"
            )
        ]
        for username in usernames
    ]

    await update.effective_message.reply_text(
        "🎲 <b>ГЕНЕРАТОР USERNAME</b>\n\n"
        "Вот пять случайных вариантов.\n"
        "Нажми на понравившийся, чтобы сохранить его.\n\n"
        "⚠️ Доступность этих имён в Telegram не проверяется.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ---------- FAVORITES ----------

async def add_favorite(
    user_id: int,
    kind: str,
    value: str
) -> bool:
    with get_db() as db:
        exists = db.execute(
            """
            SELECT id FROM favorites
            WHERE user_id = ? AND kind = ? AND value = ?
            """,
            (user_id, kind, value)
        ).fetchone()

        if exists:
            return False

        db.execute(
            """
            INSERT INTO favorites (user_id, kind, value)
            VALUES (?, ?, ?)
            """,
            (user_id, kind, value)
        )

    return True


async def show_favorites(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user_id = update.effective_user.id

    with get_db() as db:
        rows = db.execute(
            """
            SELECT id, kind, value
            FROM favorites
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 30
            """,
            (user_id,)
        ).fetchall()

    if not rows:
        await update.effective_message.reply_text(
            "⭐ Избранное пока пустое."
        )
        return

    keyboard = []

    for row in rows:
        label = f"❌ {row['kind']}: {row['value']}"
        keyboard.append([
            InlineKeyboardButton(
                label[:60],
                callback_data=f"remove_fav:{row['id']}"
            )
        ])

    await update.effective_message.reply_text(
        "⭐ <b>ТВОЁ ИЗБРАННОЕ</b>\n"
        "Нажми на запись, чтобы удалить её.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ---------- PROMO CODES ----------

async def show_promos(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.effective_message.reply_text(
        "🎟 <b>ПРОМОКОДЫ</b>\n\n"
        "Чтобы активировать код, отправь:\n"
        "<code>/promo КОД</code>\n\n"
        "Промокоды создаёт администратор.",
        parse_mode="HTML"
    )


async def redeem_promo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.effective_message.reply_text(
            "Использование: /promo КОД"
        )
        return

    code = context.args[0].strip().upper()
    user_id = update.effective_user.id

    with get_db() as db:
        promo = db.execute(
            """
            SELECT code, bonus, max_uses, used_count, expires_at, active
            FROM promos
            WHERE code = ?
            """,
            (code,)
        ).fetchone()

        if not promo or not promo["active"]:
            await update.effective_message.reply_text(
                "❌ Промокод не найден или отключён."
            )
            return

        if promo["max_uses"] > 0 and promo["used_count"] >= promo["max_uses"]:
            await update.effective_message.reply_text(
                "❌ Лимит активаций исчерпан."
            )
            return

        expires_at = promo["expires_at"]

        if expires_at and str(expires_at) < now():
            await update.effective_message.reply_text(
                "❌ Срок действия промокода истёк."
            )
            return

        already_used = db.execute(
            """
            SELECT id FROM promo_uses
            WHERE user_id = ? AND code = ?
            """,
            (user_id, code)
        ).fetchone()

        if already_used:
            await update.effective_message.reply_text(
                "⚠️ Ты уже активировал этот промокод."
            )
            return

        db.execute(
            """
            INSERT INTO promo_uses (user_id, code, used_at)
            VALUES (?, ?, ?)
            """,
            (user_id, code, now())
        )

        db.execute(
            """
            UPDATE promos
            SET used_count = used_count + 1
            WHERE code = ?
            """,
            (code,)
        )

        db.execute(
            """
            INSERT INTO actions (user_id, action, details, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, "promo_redeem", code, now())
        )

    await update.effective_message.reply_text(
        "✅ Промокод активирован!\n"
        f"🎁 Бонус: {html.escape(str(promo['bonus']))}\n\n"
        "ℹ️ Бонус отображается как информация. "
        "Автоматическое начисление баланса не настроено.",
        parse_mode="HTML"
    )


# ---------- DATA HUNTER: PUBLIC WEB ONLY ----------

async def data_hunter(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.effective_message.reply_text(
        "🔎 <b>DATA HUNTER</b>\n\n"
        "Я могу искать публичные страницы в интернете.\n"
        "Я не ищу закрытые базы, пароли, адреса или "
        "личные данные частных лиц.\n\n"
        "Отправь запрос командой:\n"
        "<code>/search запрос</code>",
        parse_mode="HTML"
    )


async def search_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.effective_message.reply_text(
            "Пример: /search официальный сайт Telegram"
        )
        return

    query = " ".join(context.args).strip()[:200]

    await update.effective_message.reply_text(
        "🔎 Ищу публичные страницы..."
    )

    results = await public_web_search(query)

    if not results:
        await update.effective_message.reply_text(
            "Не удалось получить результаты. "
            "Попробуй другой запрос чуть позже."
        )
        return

    lines = [f"🔎 <b>РЕЗУЛЬТАТЫ</b>\nЗапрос: {html.escape(query)}\n"]

    for index, result in enumerate(results, start=1):
        title = html.escape(result.get("title", "Без названия"))
        url = html.escape(result.get("url", ""), quote=True)

        lines.append(
            f'{index}. <a href="{url}">{title}</a>'
        )

    await update.effective_message.reply_text(
        "\n\n".join(lines),
        parse_mode="HTML",
        disable_web_page_preview=True
    )


# ---------- WEB TOOLS ----------

async def web_tools(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.effective_message.reply_text(
        "🌐 <b>WEB TOOLS</b>\n\n"
        "Проверить доступность публичного сайта:\n"
        "<code>/webcheck https://example.com</code>\n\n"
        "Посмотреть DNS-записи домена:\n"
        "<code>/dns example.com</code>\n\n"
        "Проверить TLS-сертификат сайта:\n"
        "<code>/ssl example.com</code>",
        parse_mode="HTML"
    )


def normalize_domain(value: str):
    value = value.strip()

    if "://" not in value:
        value = "https://" + value

    parsed = urlparse(value)

    if not parsed.hostname:
        return None

    hostname = parsed.hostname.lower()

    if (
        len(hostname) > 253
        or not re.fullmatch(r"[a-z0-9.-]+", hostname)
        or ".." in hostname
        or hostname.startswith(".")
        or hostname.endswith(".")
    ):
        return None

    return parsed, hostname


async def webcheck_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.effective_message.reply_text(
            "Пример: /webcheck https://example.com"
        )
        return

    parsed_data = normalize_domain(context.args[0])

    if not parsed_data:
        await update.effective_message.reply_text(
            "❌ Проверь адрес сайта."
        )
        return

    parsed, hostname = parsed_data

    if parsed.scheme not in ("http", "https"):
        await update.effective_message.reply_text(
            "❌ Поддерживаются только HTTP и HTTPS."
        )
        return

    try:
        async with httpx.AsyncClient(
            timeout=10,
            follow_redirects=True,
            max_redirects=5
        ) as client:
            response = await client.get(
                f"{parsed.scheme}://{hostname}{parsed.path or '/'}"
            )

        await update.effective_message.reply_text(
            "🌐 <b>РЕЗУЛЬТАТ ПРОВЕРКИ</b>\n\n"
            f"Домен: <code>{html.escape(hostname)}</code>\n"
            f"HTTP-статус: <code>{response.status_code}</code>\n"
            f"Финальный адрес: {html.escape(str(response.url))}",
            parse_mode="HTML",
            disable_web_page_preview=True
        )

    except Exception as exc:
        logging.warning("Web check failed: %s", exc)

        await update.effective_message.reply_text(
            "⚠️ Сайт не ответил или соединение не удалось установить."
        )


async def dns_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.effective_message.reply_text(
            "Пример: /dns example.com"
        )
        return

    parsed_data = normalize_domain(context.args[0])

    if not parsed_data:
        await update.effective_message.reply_text(
            "❌ Неверный домен."
        )
        return

    _, hostname = parsed_data

    try:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(
            hostname,
            None,
            type=socket.SOCK_STREAM
        )

        addresses = sorted({
            info[4][0]
            for info in infos
        })

        await update.effective_message.reply_text(
            "🧭 <b>DNS-РЕЗУЛЬТАТ</b>\n\n"
            f"Домен: <code>{html.escape(hostname)}</code>\n"
            + "\n".join(
                f"• <code>{html.escape(address)}</code>"
                for address in addresses
            ),
            parse_mode="HTML"
        )

    except Exception:
        await update.effective_message.reply_text(
            "⚠️ Не удалось получить DNS-адреса."
        )


async def ssl_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not context.args:
        await update.effective_message.reply_text(
            "Пример: /ssl example.com"
        )
        return

    parsed_data = normalize_domain(context.args[0])

    if not parsed_data:
        await update.effective_message.reply_text(
            "❌ Неверный домен."
        )
        return

    _, hostname = parsed_data

    def get_certificate():
        with socket.create_connection(
            (hostname, 443),
            timeout=8
        ) as sock:
            with ssl.create_default_context().wrap_socket(
                sock,
                server_hostname=hostname
            ) as secure_sock:
                return secure_sock.getpeercert()

    try:
        cert = await asyncio.to_thread(get_certificate)

        subject = dict(
            item[0] for item in cert.get("subject", ())
        )
        issuer = dict(
            item[0] for item in cert.get("issuer", ())
        )

        await update.effective_message.reply_text(
            "🔐 <b>TLS-СЕРТИФИКАТ</b>\n\n"
            f"Домен: <code>{html.escape(hostname)}</code>\n"
            f"Выдан для: {html.escape(subject.get('commonName', '—'))}\n"
            f"Издатель: {html.escape(issuer.get('organizationName', '—'))}\n"
            f"Действует до: {html.escape(cert.get('notAfter', '—'))}",
            parse_mode="HTML"
        )

    except Exception:
        await update.effective_message.reply_text(
            "⚠️ Не удалось проверить TLS-сертификат."
        )


# ---------- SECURITY CENTER ----------

async def security_center(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.effective_message.reply_text(
        "🛡 <b>SECURITY CENTER</b>\n\n"
        "Доступные проверки:\n"
        "• /webcheck — доступность сайта\n"
        "• /dns — DNS-адреса домена\n"
        "• /ssl — TLS-сертификат\n\n"
        "Проверки предназначены для публичных сайтов "
        "и обычной диагностики.",
        parse_mode="HTML"
    )


# ---------- SHOP ----------

async def show_shop(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    with get_db() as db:
        products = db.execute(
            """
            SELECT id, name, description, price
            FROM products
            WHERE active = 1
            ORDER BY id DESC
            LIMIT 20
            """
        ).fetchall()

    if not products:
        await update.effective_message.reply_text(
            "🛍 Магазин пока пуст.\n"
            "Администратор может добавить товары командой "
            "/product_add."
        )
        return

    keyboard = []

    for product in products:
        keyboard.append([
            InlineKeyboardButton(
                f"{product['name']} — {product['price']} ⭐",
                callback_data=f"product:{product['id']}"
            )
        ])

    await update.effective_message.reply_text(
        "🛍 <b>PROTIVZLA SHOP</b>\n\n"
        "Выбери товар:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_gifts_shop(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.effective_message.reply_text(
        "🎁 <b>GIFTS SHOP</b>\n\n"
        "Раздел подготовлен, но отправка Telegram-подарков "
        "пока не подключена.\n\n"
        "Бот не будет утверждать, что подарок отправлен, "
        "если этого не произошло.",
        parse_mode="HTML"
    )


# ---------- SUPPORT TICKETS ----------

async def support_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    context.user_data["waiting_support_message"] = True

    await update.effective_message.reply_text(
        "🎧 <b>ПОДДЕРЖКА</b>\n\n"
        "Отправь следующим сообщением свой вопрос. "
        "Не указывай пароли и секретные коды.",
        parse_mode="HTML"
    )


async def create_ticket(
    user_id: int,
    username: str,
    message: str
):
    with get_db() as db:
        cursor = db.execute(
            """
            INSERT INTO tickets
                (user_id, username, message, status, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user_id,
                username,
                message[:2000],
                "open",
                now()
            )
        )

        return cursor.lastrowid


async def handle_support_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user
    message = update.effective_message

    if not user or not message or not message.text:
        return

    ticket_id = await create_ticket(
        user.id,
        user.username or "",
        message.text
    )

    context.user_data.pop("waiting_support_message", None)

    await message.reply_text(
        f"✅ Обращение #{ticket_id} создано. "
        "Поддержка рассмотрит его, когда администратор будет онлайн."
    )

    with get_db() as db:
        admins = db.execute(
            "SELECT user_id FROM admins"
        ).fetchall()

    for admin in admins:
        try:
            await context.bot.send_message(
                chat_id=admin["user_id"],
                text=(
                    f"🎧 Новое обращение #{ticket_id}\n"
                    f"От: {user.id} (@{user.username or 'нет'})\n\n"
                    f"{message.text[:1500]}\n\n"
                    f"Ответ: /ticket_reply {ticket_id} текст"
                )
            )
        except Exception:
            logging.warning(
                "Could not notify admin %s",
                admin["user_id"]
            )


# ---------- ADMIN COMMANDS ----------

async def admin_stats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text(
            "⛔ Нет доступа."
        )
        return

    with get_db() as db:
        users = db.execute(
            "SELECT COUNT(*) FROM users"
        ).fetchone()[0]

        active_users = db.execute(
            """
            SELECT COUNT(*) FROM users
            WHERE blocked = 0
            """
        ).fetchone()[0]

        open_tickets = db.execute(
            """
            SELECT COUNT(*) FROM tickets
            WHERE status = 'open'
            """
        ).fetchone()[0]

        orders = db.execute(
            "SELECT COUNT(*) FROM orders"
        ).fetchone()[0]

    await update.effective_message.reply_text(
        "🛠 <b>ADMIN STATISTICS</b>\n\n"
        f"👥 Пользователей: {users}\n"
        f"🟢 Не заблокировано: {active_users}\n"
        f"🎧 Открытых обращений: {open_tickets}\n"
        f"🛒 Заказов: {orders}",
        parse_mode="HTML"
    )


async def list_users(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Нет доступа.")
        return

    with get_db() as db:
        rows = db.execute(
            """
            SELECT user_id, username, first_name, blocked
            FROM users
            ORDER BY id DESC
            LIMIT 30
            """
        ).fetchall()

    if not rows:
        await update.effective_message.reply_text(
            "Пользователей пока нет."
        )
        return

    lines = ["👥 <b>ПОСЛЕДНИЕ ПОЛЬЗОВАТЕЛИ</b>\n"]

    for row in rows:
        status = "🚫" if row["blocked"] else "🟢"
        username = "@" + row["username"] if row["username"] else "без username"

        lines.append(
            f"{status} <code>{row['user_id']}</code> — "
            f"{html.escape(username)} — "
            f"{html.escape(row['first_name'] or '')}"
        )

    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode="HTML"
    )


async def block_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Нет доступа.")
        return

    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text(
            "Использование: /block USER_ID"
        )
        return

    target_id = int(context.args[0])

    if target_id in ADMIN_IDS:
        await update.effective_message.reply_text(
            "❌ Нельзя блокировать администратора из этой команды."
        )
        return

    with get_db() as db:
        db.execute(
            "UPDATE users SET blocked = 1 WHERE user_id = ?",
            (target_id,)
        )

    await update.effective_message.reply_text(
        f"🚫 Пользователь {target_id} заблокирован, если он есть в базе."
    )


async def unblock_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Нет доступа.")
        return

    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text(
            "Использование: /unblock USER_ID"
        )
        return

    target_id = int(context.args[0])

    with get_db() as db:
        db.execute(
            "UPDATE users SET blocked = 0 WHERE user_id = ?",
            (target_id,)
        )

    await update.effective_message.reply_text(
        f"✅ Блокировка пользователя {target_id} снята."
    )


async def broadcast_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Нет доступа.")
        return

    text = " ".join(context.args).strip()

    if not text:
        await update.effective_message.reply_text(
            "Использование: /broadcast текст сообщения"
        )
        return

    with get_db() as db:
        rows = db.execute(
            "SELECT user_id FROM users WHERE blocked = 0"
        ).fetchall()

    sent = 0
    failed = 0

    for row in rows:
        try:
            await context.bot.send_message(
                chat_id=row["user_id"],
                text=text[:4000]
            )
            sent += 1
        except Exception:
            failed += 1

    await update.effective_message.reply_text(
        f"📣 Рассылка завершена.\n"
        f"Отправлено: {sent}\n"
        f"Не отправлено: {failed}"
    )


async def ticket_reply_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Нет доступа.")
        return

    if len(context.args) < 2 or not context.args[0].isdigit():
        await update.effective_message.reply_text(
            "Использование: /ticket_reply ID текст ответа"
        )
        return

    ticket_id = int(context.args[0])
    reply_text = " ".join(context.args[1:]).strip()

    with get_db() as db:
        ticket = db.execute(
            """
            SELECT user_id FROM tickets
            WHERE id = ?
            """,
            (ticket_id,)
        ).fetchone()

        if not ticket:
            await update.effective_message.reply_text(
                "❌ Обращение не найдено."
            )
            return

        db.execute(
            """
            UPDATE tickets SET status = 'answered'
            WHERE id = ?
            """,
            (ticket_id,)
        )

    try:
        await context.bot.send_message(
            chat_id=ticket["user_id"],
            text=f"🎧 Ответ поддержки на обращение #{ticket_id}:\n\n{reply_text[:3500]}"
        )

        await update.effective_message.reply_text(
            "✅ Ответ отправлен пользователю."
        )

    except Exception:
        await update.effective_message.reply_text(
            "⚠️ Не удалось отправить ответ."
        )


# ---------- PRODUCT MANAGEMENT ----------

async def product_add_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Нет доступа.")
        return

    raw = " ".join(context.args)

    parts = [part.strip() for part in raw.split("|", 2)]

    if len(parts) != 3:
        await update.effective_message.reply_text(
            "Использование:\n"
            "/product_add Название | описание | цена_в_звёздах"
        )
        return

    name, description, price_text = parts

    try:
        price = int(price_text)
    except ValueError:
        await update.effective_message.reply_text(
            "Цена должна быть целым числом."
        )
        return

    if not name or price < 1:
        await update.effective_message.reply_text(
            "Название обязательно, цена должна быть больше нуля."
        )
        return

    with get_db() as db:
        db.execute(
            """
            INSERT INTO products (name, description, price, active)
            VALUES (?, ?, ?, 1)
            """,
            (name[:100], description[:1000], price)
        )

    await update.effective_message.reply_text(
        "✅ Товар добавлен в магазин."
    )


async def promo_create_cmd(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Нет доступа.")
        return

    if len(context.args) < 3:
        await update.effective_message.reply_text(
            "Использование:\n"
            "/promo_create CODE BONUS MAX_USES\n\n"
            "Пример: /promo_create WELCOME Бонус 100"
        )
        return

    code = context.args[0].upper()

    if not re.fullmatch(r"[A-Z0-9_-]{3,32}", code):
        await update.effective_message.reply_text(
            "Код должен содержать 3–32 символа: A-Z, цифры, _ или -."
        )
        return

    bonus = context.args[1]

    try:
        max_uses = int(context.args[2])
    except ValueError:
        await update.effective_message.reply_text(
            "MAX_USES должен быть числом. 0 означает без общего лимита."
        )
        return

    if max_uses < 0:
        await update.effective_message.reply_text(
            "Лимит не может быть отрицательным."
        )
        return

    try:
        with get_db() as db:
            db.execute(
                """
                INSERT INTO promos
                    (code, bonus, max_uses, used_count, active, expires_at)
                VALUES (?, ?, ?, 0, 1, NULL)
                """,
                (code, bonus[:100], max_uses)
            )

        await update.effective_message.reply_text(
            f"✅ Промокод {code} создан."
        )

    except sqlite3.IntegrityError:
        await update.effective_message.reply_text(
            "❌ Такой промокод уже существует."
        )


# ---------- ADMIN PANEL ----------

async def admin_panel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if not is_admin(update.effective_user.id):
        await update.effective_message.reply_text(
            "⛔ Эта панель доступна только администраторам."
        )
        return

    keyboard = [
        [
            InlineKeyboardButton(
                "📊 Статистика",
                callback_data="admin:stats"
            ),
            InlineKeyboardButton(
                "👥 Пользователи",
                callback_data="admin:users"
            )
        ],
        [
            InlineKeyboardButton(
                "🛍 Магазин",
                callback_data="admin:shop"
            ),
            InlineKeyboardButton(
                "🎟 Промокоды",
                callback_data="admin:promos"
            )
        ]
    ]

    await update.effective_message.reply_text(
        "🛠 <b>PROTIVZLA ADMIN PANEL</b>\n\n"
        "Команды:\n"
        "/admin_stats — статистика\n"
        "/users — пользователи\n"
        "/block ID — блокировка\n"
        "/unblock ID — разблокировка\n"
        "/broadcast текст — рассылка\n"
        "/product_add Название | описание | цена\n"
        "/promo_create CODE BONUS MAX\n"
        "/ticket_reply ID текст — ответить на обращение",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    # =========================
# PART 3/3 — HANDLERS & RUN
# =========================

import asyncio


# ---------- PRODUCT DETAILS ----------

async def show_product(
    query,
    product_id: int
):
    with get_db() as db:
        product = db.execute(
            """
            SELECT id, name, description, price
            FROM products
            WHERE id = ? AND active = 1
            """,
            (product_id,)
        ).fetchone()

    if not product:
        await query.message.reply_text("❌ Товар не найден.")
        return

    keyboard = [
        [
            InlineKeyboardButton(
                f"⭐ Купить за {product['price']} Stars",
                callback_data=f"buy:{product['id']}"
            )
        ],
        [
            InlineKeyboardButton(
                "⭐ В избранное",
                callback_data=f"fav_product:{product['id']}"
            )
        ]
    ]

    await query.message.reply_text(
        "🛍 <b>ТОВАР</b>\n\n"
        f"Название: {html.escape(product['name'])}\n"
        f"Описание: {html.escape(product['description'] or '')}\n"
        f"Цена: {product['price']} ⭐\n\n"
        "Покупка доступна через Telegram Stars.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ---------- TELEGRAM STARS PAYMENT ----------

async def buy_product(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    product_id: int
):
    user_id = query.from_user.id

    with get_db() as db:
        product = db.execute(
            """
            SELECT id, name, description, price
            FROM products
            WHERE id = ? AND active = 1
            """,
            (product_id,)
        ).fetchone()

    if not product:
        await query.message.reply_text(
            "❌ Этот товар больше недоступен."
        )
        return

    price = int(product["price"])

    if price < 1:
        await query.message.reply_text(
            "❌ Некорректная цена товара."
        )
        return

    payload = f"product:{product_id}:user:{user_id}"

    try:
        await context.bot.send_invoice(
            chat_id=user_id,
            title=product["name"][:32],
            description=(product["description"] or product["name"])[:255],
            payload=payload,
            provider_token="",
            currency="XTR",
            prices=[
                LabeledPrice(
                    label=product["name"][:32],
                    amount=price
                )
            ]
        )

        log_action(
            user_id,
            "invoice_created",
            f"product_id={product_id}"
        )

    except Exception:
        logging.exception("Could not create Stars invoice")

        await query.message.reply_text(
            "⚠️ Не удалось создать счёт. Попробуй позже."
        )


async def precheckout_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.pre_checkout_query

    try:
        parts = query.invoice_payload.split(":")

        if (
            len(parts) != 4
            or parts[0] != "product"
            or parts[2] != "user"
        ):
            await query.answer(
                ok=False,
                error_message="Некорректный счёт."
            )
            return

        product_id = int(parts[1])
        invoice_user_id = int(parts[3])

        if query.from_user.id != invoice_user_id:
            await query.answer(
                ok=False,
                error_message="Этот счёт создан для другого пользователя."
            )
            return

        with get_db() as db:
            product = db.execute(
                """
                SELECT id, price
                FROM products
                WHERE id = ? AND active = 1
                """,
                (product_id,)
            ).fetchone()

        if not product or int(product["price"]) != query.total_amount:
            await query.answer(
                ok=False,
                error_message="Товар недоступен или цена изменилась."
            )
            return

        await query.answer(ok=True)

    except Exception:
        logging.exception("Pre-checkout error")

        try:
            await query.answer(
                ok=False,
                error_message="Не удалось проверить заказ."
            )
        except Exception:
            pass


async def successful_payment_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    message = update.effective_message
    payment = message.successful_payment
    user_id = update.effective_user.id

    try:
        parts = payment.invoice_payload.split(":")

        if (
            len(parts) != 4
            or parts[0] != "product"
            or parts[2] != "user"
            or int(parts[3]) != user_id
        ):
            await message.reply_text(
                "⚠️ Оплата получена, но заказ требует проверки администратора."
            )
            return

        product_id = int(parts[1])
        charge_id = payment.telegram_payment_charge_id

        with get_db() as db:
            product = db.execute(
                """
                SELECT id, name, price
                FROM products
                WHERE id = ?
                """,
                (product_id,)
            ).fetchone()

            if not product:
                await message.reply_text(
                    "Оплата получена. Товар не найден — обратись в поддержку."
                )
                return

            if int(product["price"]) != payment.total_amount:
                await message.reply_text(
                    "Оплата получена. Заказ требует проверки поддержки."
                )
                return

            cursor = db.execute(
                """
                INSERT OR IGNORE INTO orders
                    (
                        user_id,
                        product_id,
                        amount,
                        currency,
                        status,
                        telegram_payment_charge_id,
                        created_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    product_id,
                    payment.total_amount,
                    payment.currency,
                    "paid",
                    charge_id,
                    now()
                )
            )

            if cursor.rowcount == 0:
                await message.reply_text(
                    "ℹ️ Эта оплата уже зарегистрирована."
                )
                return

        log_action(
            user_id,
            "purchase",
            f"product_id={product_id}; charge={charge_id}"
        )

        await message.reply_text(
            "✅ <b>ОПЛАТА ПОДТВЕРЖДЕНА</b>\n\n"
            f"🛍 Товар: {html.escape(product['name'])}\n"
            f"⭐ Сумма: {payment.total_amount}\n"
            f"🧾 Заказ сохранён в базе.\n\n"
            "Если товар требует ручной выдачи, дождись ответа поддержки.",
            parse_mode="HTML"
        )

    except Exception:
        logging.exception("Successful payment handler failed")

        await message.reply_text(
            "Оплата получена, но при сохранении заказа возникла ошибка. "
            "Обратись в поддержку и сохрани квитанцию Telegram."
        )


# ---------- SUBSCRIPTION BUTTONS ----------

async def subscription_callback(
    query,
    context: ContextTypes.DEFAULT_TYPE
):
    await query.answer()

    if query.data == "subscribe":
        await query.message.reply_text(
            f"📢 Подпишись на канал: {REQUIRED_CHAT_URL}\n\n"
            "После подписки нажми «Проверить подписку»."
        )
        return

    if query.data == "check_subscription":
        user_id = query.from_user.id

        try:
            member = await context.bot.get_chat_member(
                REQUIRED_CHAT,
                user_id
            )

            status = str(member.status).lower()

            if status in (
                "creator",
                "owner",
                "administrator",
                "member"
            ):
                await query.message.reply_text(
                    "✅ Подписка подтверждена!\n"
                    "Отправь /start, чтобы открыть меню."
                )
            else:
                await query.message.reply_text(
                    "❌ Подписка пока не обнаружена."
                )

        except Exception:
            logging.exception("Subscription check failed")

            await query.message.reply_text(
                "⚠️ Не удалось проверить подписку. "
                "Проверь, что бот может получать информацию о канале."
            )


# ---------- CALLBACK ROUTER ----------

async def callback_router(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    if not query:
        return

    data = query.data or ""

    if data in ("subscribe", "check_subscription"):
        await subscription_callback(query, context)
        return

    await query.answer()

    user_id = query.from_user.id

    if is_blocked(user_id):
        await query.message.reply_text(
            "🚫 Доступ к боту ограничен."
        )
        return

    if data.startswith("fav_username:"):
        username = data.split(":", 1)[1]

        added = await add_favorite(
            user_id,
            "username",
            username
        )

        await query.message.reply_text(
            "⭐ Сохранено в избранное."
            if added
            else "ℹ️ Этот вариант уже есть в избранном."
        )

        log_action(
            user_id,
            "favorite_username",
            username
        )
        return

    if data.startswith("remove_fav:"):
        try:
            favorite_id = int(data.split(":", 1)[1])
        except ValueError:
            return

        with get_db() as db:
            db.execute(
                """
                DELETE FROM favorites
                WHERE id = ? AND user_id = ?
                """,
                (favorite_id, user_id)
            )

        await query.message.reply_text(
            "🗑 Запись удалена из избранного."
        )
        return

    if data.startswith("product:"):
        try:
            product_id = int(data.split(":", 1)[1])
        except ValueError:
            return

        await show_product(query, product_id)
        return

    if data.startswith("buy:"):
        try:
            product_id = int(data.split(":", 1)[1])
        except ValueError:
            return

        await buy_product(query, context, product_id)
        return

    if data.startswith("fav_product:"):
        try:
            product_id = int(data.split(":", 1)[1])
        except ValueError:
            return

        with get_db() as db:
            product = db.execute(
                """
                SELECT name
                FROM products
                WHERE id = ? AND active = 1
                """,
                (product_id,)
            ).fetchone()

        if not product:
            await query.message.reply_text("❌ Товар не найден.")
            return

        added = await add_favorite(
            user_id,
            "product",
            f"{product_id}:{product['name']}"
        )

        await query.message.reply_text(
            "⭐ Товар добавлен в избранное."
            if added
            else "ℹ️ Товар уже есть в избранном."
        )
        return

    if data.startswith("admin:"):
        if not is_admin(user_id):
            await query.message.reply_text("⛔ Нет доступа.")
            return

        section = data.split(":", 1)[1]

        if section == "stats":
            await admin_stats(update, context)
        elif section == "users":
            await list_users(update, context)
        elif section == "shop":
            await show_shop(update, context)
        elif section == "promos":
            await show_promos(update, context)

        return


# ---------- TEXT ROUTER ----------

async def text_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    message = update.effective_message
    user = update.effective_user

    if not message or not message.text or not user:
        return

    if not await ensure_access(update, context):
        return

    text = message.text.strip()

    if context.user_data.get("waiting_support_message"):
        await handle_support_message(update, context)
        return

    routes = {
        "🔎 DATA HUNTER": data_hunter,
        "🌐 WEB TOOLS": web_tools,
        "🛡 SECURITY CENTER": security_center,
        "🎁 GIFTS SHOP": show_gifts_shop,
        "🛍 PROTIVZLA SHOP": show_shop,
        "👤 USER PROFILE": show_profile,
        "🎧 SUPPORT": support_menu,
        "🛠 ADMIN PANEL": admin_panel,
        "🎟 PROMO CODES": show_promos,
        "📊 STATISTICS": show_statistics,
        "📜 ACTION HISTORY": show_history,
        "🎲 USERNAME GENERATOR": username_generator,
        "⭐ FAVORITES": show_favorites,
    }

    handler = routes.get(text)

    if handler:
        await handler(update, context)
        log_action(user.id, "menu", text)
        return

    await message.reply_text(
        "🤖 Не понял команду.\n\n"
        "Выбери раздел в меню или используй /help."
    )


# ---------- HELP ----------

async def help_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    await update.effective_message.reply_text(
        "🤖 <b>PROTIVZLA ULTIMATE</b>\n\n"
        "/start — главное меню\n"
        "/help — помощь\n"
        "/search запрос — поиск публичных страниц\n"
        "/webcheck URL — проверить сайт\n"
        "/dns домен — DNS-адреса\n"
        "/ssl домен — TLS-сертификат\n"
        "/promo КОД — активировать промокод\n"
        "/admin_stats — статистика администратора\n\n"
        "Не отправляй боту пароли и секретные коды.",
        parse_mode="HTML"
    )


# ---------- ERROR HANDLER ----------

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):
    logging.error(
        "Unhandled bot error",
        exc_info=context.error
    )


# ---------- MAIN ----------

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "Не задан BOT_TOKEN в переменных окружения."
        )

    init_db()

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Основные команды
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("webcheck", webcheck_command))
    application.add_handler(CommandHandler("dns", dns_command))
    application.add_handler(CommandHandler("ssl", ssl_command))
    application.add_handler(CommandHandler("promo", redeem_promo))

    # Административные команды
    application.add_handler(CommandHandler("admin_stats", admin_stats))
    application.add_handler(CommandHandler("users", list_users))
    application.add_handler(CommandHandler("block", block_cmd))
    application.add_handler(CommandHandler("unblock", unblock_cmd))
    application.add_handler(CommandHandler("broadcast", broadcast_cmd))
    application.add_handler(CommandHandler("ticket_reply", ticket_reply_cmd))
    application.add_handler(CommandHandler("product_add", product_add_cmd))
    application.add_handler(CommandHandler("promo_create", promo_create_cmd))

    # Telegram Stars
    application.add_handler(
        PreCheckoutQueryHandler(precheckout_handler)
    )

    application.add_handler(
        MessageHandler(
            filters.SUCCESSFUL_PAYMENT,
            successful_payment_handler
        )
    )

    # Кнопки и текстовые сообщения
    application.add_handler(
        CallbackQueryHandler(callback_router)
    )

    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler
        )
    )

    application.add_error_handler(error_handler)

    logging.info("PROTIVZLA ULTIMATE starting...")

    application.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()

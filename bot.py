import os
import random
import sqlite3
import logging
import secrets
import string
from datetime import datetime, timezone

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# =========================
# CONFIGURATION
# =========================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ADMIN_IDS = {
    int(x.strip())
    for x in os.environ.get("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

REQUIRED_CHAT = "@ProtivzlaNFT"
REQUIRED_CHAT_URL = "https://t.me/ProtivzlaNFT"
DB_PATH = os.environ.get("DB_PATH", "bot.db")

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# =========================
# DATABASE
# =========================

def db():
    return sqlite3.connect(DB_PATH)


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                joined_at TEXT,
                last_seen TEXT,
                blocked INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                action TEXT,
                details TEXT,
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                value TEXT,
                created_at TEXT
            )
        """)


def save_user(user):
    now = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        conn.execute("""
            INSERT INTO users
                (user_id, username, first_name, joined_at, last_seen)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name,
                last_seen = excluded.last_seen
        """, (
            user.id,
            user.username or "",
            user.first_name or "",
            now,
            now,
        ))


def log_action(user_id, action, details=""):
    with db() as conn:
        conn.execute("""
            INSERT INTO history (user_id, action, details, created_at)
            VALUES (?, ?, ?, ?)
        """, (
            user_id,
            action,
            details[:500],
            datetime.now(timezone.utc).isoformat(),
        ))


def is_blocked(user_id):
    with db() as conn:
        row = conn.execute(
            "SELECT blocked FROM users WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return bool(row and row[0])


def set_blocked(user_id, value):
    with db() as conn:
        conn.execute("""
            UPDATE users SET blocked = ? WHERE user_id = ?
        """, (int(value), user_id))


# =========================
# SUBSCRIPTION CHECK
# =========================

async def check_subscription(context, user_id):
    try:
        member = await context.bot.get_chat_member(
            REQUIRED_CHAT,
            user_id,
        )
        return member.status in (
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        )
    except Exception:
        logger.exception("Subscription check failed")
        return False


def subscription_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "📢 Вступить в группу",
            url=REQUIRED_CHAT_URL,
        )],
        [InlineKeyboardButton(
            "✅ Проверить подписку",
            callback_data="check_subscription",
        )],
    ])


async def require_subscription(update, context):
    user = update.effective_user
    if not user:
        return False

    if await check_subscription(context, user.id):
        return True

    text = (
        "🔒 PROTIVZLA ULTIMATE\n\n"
        "Для использования бота необходимо вступить "
        "в нашу группу.\n\n"
        "После вступления нажми «Проверить подписку»."
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(
            text,
            reply_markup=subscription_keyboard(),
        )
    elif update.effective_message:
        await update.effective_message.reply_text(
            text,
            reply_markup=subscription_keyboard(),
        )

    return False


# =========================
# MENUS
# =========================

def main_keyboard(user_id):
    rows = [
        [
            InlineKeyboardButton("🔎 DATA HUNTER", callback_data="data"),
            InlineKeyboardButton("🛡 SECURITY", callback_data="security"),
        ],
        [
            InlineKeyboardButton("🌐 WEB TOOLS", callback_data="webtools"),
            InlineKeyboardButton("🎭 Генератор ников", callback_data="nick"),
        ],
        [
            InlineKeyboardButton("📋 История", callback_data="history"),
            InlineKeyboardButton("⭐ Избранное", callback_data="favorites"),
        ],
        [
            InlineKeyboardButton("👤 Профиль", callback_data="profile"),
            InlineKeyboardButton("📊 Статистика", callback_data="stats"),
        ],
    ]

    if user_id in ADMIN_IDS:
        rows.append([
            InlineKeyboardButton("👑 Админ-панель", callback_data="admin")
        ])

    return InlineKeyboardMarkup(rows)


async def show_main(update, context):
    user = update.effective_user
    text = (
        "👑 PROTIVZLA ULTIMATE\n\n"
        "💚 Добро пожаловать!\n"
        "Выбери нужный раздел в меню ниже."
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text,
            reply_markup=main_keyboard(user.id),
        )
    else:
        await update.effective_message.reply_text(
            text,
            reply_markup=main_keyboard(user.id),
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user)

    if is_blocked(user.id):
        await update.effective_message.reply_text(
            "⛔ Доступ к боту ограничен."
        )
        return

    if not await require_subscription(update, context):
        return

    log_action(user.id, "start")
    await show_main(update, context)


# =========================
# NICKNAME GENERATOR
# =========================

PREFIXES = [
    "Dark", "Green", "Shadow", "Ghost", "Storm",
    "Neo", "Frost", "Night", "Cyber", "Royal",
    "Ultra", "Toxic", "Silent", "Emerald", "Alpha",
]

SUFFIXES = [
    "Wolf", "Hunter", "King", "Fox", "Dragon",
    "Ninja", "Legend", "Phantom", "Rider", "Blade",
    "Star", "Pixel", "Tiger", "Knight", "Master",
]


def generate_nick():
    style = random.randint(1, 4)

    if style == 1:
        return random.choice(PREFIXES) + random.choice(SUFFIXES)

    if style == 2:
        return (
            random.choice(PREFIXES)
            + "_"
            + random.choice(SUFFIXES)
        )

    if style == 3:
        return (
            random.choice(PREFIXES)
            + str(random.randint(10, 9999))
        )

    return (
        random.choice(PREFIXES)
        + random.choice(SUFFIXES)
        + str(random.randint(1, 99))
    )


# =========================
# CALLBACKS
# =========================

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user = update.effective_user
    data = query.data

    if data == "check_subscription":
        await query.answer()

        if not await check_subscription(context, user.id):
            await query.message.reply_text(
                "❌ Подписка пока не найдена. "
                "Вступи в группу и попробуй снова.",
                reply_markup=subscription_keyboard(),
            )
            return

        await query.message.reply_text(
            "✅ Подписка подтверждена!"
        )
        await show_main(update, context)
        return

    if is_blocked(user.id):
        await query.answer(
            "Доступ ограничен.",
            show_alert=True,
        )
        return

    if not await require_subscription(update, context):
        return

    await query.answer()
    log_action(user.id, data)

    if data == "home":
        await show_main(update, context)

    elif data == "nick":
        nick = generate_nick()
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🔄 Ещё ник",
                callback_data="nick",
            )],
            [InlineKeyboardButton(
                "⭐ В избранное",
                callback_data=f"save_nick:{nick}",
            )],
            [InlineKeyboardButton(
                "⬅️ Главное меню",
                callback_data="home",
            )],
        ])
        await query.edit_message_text(
            f"🎭 Твой никнейм:\n\n`{nick}`",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

    elif data.startswith("save_nick:"):
        value = data.split(":", 1)[1]
        with db() as conn:
            conn.execute("""
                INSERT INTO favorites (user_id, value, created_at)
                VALUES (?, ?, ?)
            """, (
                user.id,
                value,
                datetime.now(timezone.utc).isoformat(),
            ))
        await query.answer("⭐ Сохранено!", show_alert=True)

    elif data == "history":
        with db() as conn:
            rows = conn.execute("""
                SELECT action, details, created_at
                FROM history
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT 10
            """, (user.id,)).fetchall()

        if not rows:
            text = "📋 История пока пуста."
        else:
            lines = ["📋 Последние действия:\n"]
            for action, details, created in rows:
                lines.append(
                    f"• {action}: {details or '—'}\n"
                    f"  {created[:16]}"
                )
            text = "\n".join(lines)

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🗑 Очистить историю",
                callback_data="clear_history",
            )],
            [InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="home",
            )],
        ])
        await query.edit_message_text(text, reply_markup=keyboard)

    elif data == "clear_history":
        with db() as conn:
            conn.execute(
                "DELETE FROM history WHERE user_id = ?",
                (user.id,),
            )
        await query.edit_message_text(
            "🗑 История очищена.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "⬅️ Главное меню",
                    callback_data="home",
                )
            ]]),
        )

    elif data == "favorites":
        with db() as conn:
            rows = conn.execute("""
                SELECT id, value
                FROM favorites
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT 15
            """, (user.id,)).fetchall()

        if not rows:
            text = "⭐ Избранное пока пусто."
        else:
            text = "⭐ Избранное:\n\n" + "\n".join(
                f"{i}. {value}" for i, value in rows
            )

        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "⬅️ Главное меню",
                    callback_data="home",
                )
            ]]),
        )

    elif data == "profile":
        with db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM history WHERE user_id = ?",
                (user.id,),
            ).fetchone()[0]

        await query.edit_message_text(
            f"👤 ПРОФИЛЬ\n\n"
            f"ID: {user.id}\n"
            f"Имя: {user.first_name or '—'}\n"
            f"Username: @{user.username or 'не указан'}\n"
            f"Количество действий: {count}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "⬅️ Главное меню",
                    callback_data="home",
                )
            ]]),
        )

    elif data in ("data", "security", "webtools"):
        messages = {
            "data": (
                "🔎 DATA HUNTER\n\n"
                "Раздел предназначен для поиска по открытым "
                "источникам. Поиск пока не подключён."
            ),
            "security": (
                "🛡 SECURITY CENTER\n\n"
                "Раздел предназначен для проверки подозрительных "
                "ссылок. Анализ пока не подключён."
            ),
            "webtools": (
                "🌐 WEB TOOLS\n\n"
                "Здесь можно будет добавить проверку DNS, SSL "
                "и HTTP-статусов сайтов."
            ),
        }
        await query.edit_message_text(
            messages[data],
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "⬅️ Главное меню",
                    callback_data="home",
                )
            ]]),
        )

    elif data == "stats":
        with db() as conn:
            users = conn.execute(
                "SELECT COUNT(*) FROM users"
            ).fetchone()[0]
            actions = conn.execute(
                "SELECT COUNT(*) FROM history"
            ).fetchone()[0]

        await query.edit_message_text(
            f"📊 СТАТИСТИКА БОТА\n\n"
            f"👥 Пользователей: {users}\n"
            f"🔎 Всего действий: {actions}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "⬅️ Главное меню",
                    callback_data="home",
                )
            ]]),
        )

    elif data == "admin":
        if user.id not in ADMIN_IDS:
            await query.answer("Нет доступа.", show_alert=True)
            return

        await query.edit_message_text(
            "👑 АДМИН-ПАНЕЛЬ\n\nВыбери действие:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "📊 Статистика",
                    callback_data="stats",
                )],
                [InlineKeyboardButton(
                    "📢 Рассылка",
                    callback_data="broadcast",
                )],
                [InlineKeyboardButton(
                    "🚫 Заблокировать пользователя",
                    callback_data="block_help",
                )],
                [InlineKeyboardButton(
                    "⬅️ Главное меню",
                    callback_data="home",
                )],
            ]),
        )

    elif data == "broadcast":
        if user.id not in ADMIN_IDS:
            return

        context.user_data["awaiting_broadcast"] = True
        await query.message.reply_text(
            "📢 Отправь текст рассылки следующим сообщением."
        )

    elif data == "block_help":
        await query.edit_message_text(
            "Используй команду:\n/block USER_ID\n\n"
            "Для разблокировки:\n/unblock USER_ID",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "⬅️ Главное меню",
                    callback_data="home",
                )
            ]]),
        )


# =========================
# ADMIN COMMANDS
# =========================

async def block_command(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return

    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text(
            "Использование: /block USER_ID"
        )
        return

    target = int(context.args[0])
    with db() as conn:
        exists = conn.execute(
            "SELECT user_id FROM users WHERE user_id = ?",
            (target,),
        ).fetchone()

    if not exists:
        await update.effective_message.reply_text(
            "Пользователь не найден в базе."
        )
        return

    set_blocked(target, True)
    await update.effective_message.reply_text(
        f"🚫 Пользователь {target} заблокирован."
    )


async def unblock_command(update, context):
    if update.effective_user.id not in ADMIN_IDS:
        return

    if not context.args or not context.args[0].isdigit():
        await update.effective_message.reply_text(
            "Использование: /unblock USER_ID"
        )
        return

    target = int(context.args[0])
    set_blocked(target, False)
    await update.effective_message.reply_text(
        f"✅ Пользователь {target} разблокирован."
    )


async def text_handler(update, context):
    user = update.effective_user
    if not user:
        return

    if is_blocked(user.id):
        return

    if not await require_subscription(update, context):
        return

    if context.user_data.pop("awaiting_broadcast", False):
        if user.id not in ADMIN_IDS:
            return

        sent = 0
        failed = 0

        with db() as conn:
            users = conn.execute(
                "SELECT user_id FROM users WHERE blocked = 0"
            ).fetchall()

        for (target_id,) in users:
            try:
                await context.bot.send_message(
                    target_id,
                    update.effective_message.text,
                )
                sent += 1
            except Exception:
                failed += 1

        await update.effective_message.reply_text(
            f"📢 Рассылка завершена.\n"
            f"Отправлено: {sent}\n"
            f"Не отправлено: {failed}"
        )
        return

    await update.effective_message.reply_text(
        "Используй кнопки главного меню или команду /start."
    )


# =========================
# ERROR HANDLER
# =========================

async def error_handler(update, context):
    logger.error(
        "Update processing failed",
        exc_info=context.error,
    )


# =========================
# MAIN
# =========================

def main():
    if not BOT_TOKEN:
        raise RuntimeError(
            "Не задан BOT_TOKEN в переменных окружения."
        )

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("block", block_command))
    app.add_handler(CommandHandler("unblock", unblock_command))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            text_handler,
        )
    )
    app.add_error_handler(error_handler)

    logger.info("PROTIVZLA ULTIMATE started")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

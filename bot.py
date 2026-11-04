import logging
import os
import random
import sqlite3
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ───────────────────────── تنظیمات ─────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
DB_PATH = os.environ.get("DB_PATH", "bot.db")  # روی Railway بهتره روی Volume باشه

TRIGGER_WORD = "جق"
COOLDOWN_SECONDS = 5 * 60
MIN_REWARD = 5
MAX_REWARD = 100

# کانال‌های اجباری (دقیقاً همونی که دادی)
CHANNELS = [
    "@selfsaz_iran",
    "@fox_frenzy",
    "@fox_frnzy",
]

# متن پاسخ ربات بر اساس مبلغ.
# هر ردیف: (حداکثر مبلغ, [متن‌ها]). اولین ردیفی که مبلغ بهش بخوره انتخاب میشه
# و یکی از متن‌هاش رندوم میاد. {amount} = مبلغ
REPLY_TIERS = [
    (20, [
        "اوه ضعیف بود 😐 فقط {amount} تومن بابتش گرفتی 🤛💦",
        "این که آبکی بود! {amount} تومن بیشتر نمی‌ارزید 🤛💦",
    ]),
    (45, [
        "بد نبود! {amount} تومن بابتش گرفتی 🤛💦",
        "اوکی بود، {amount} تومن به حسابت اضافه شد 🤛💦",
    ]),
    (70, [
        "اوه قوی بود! انقدر پول بابتش گرفتی: {amount} تومن 🤛💦",
        "اوه چه قوی! {amount} تومن بابتش گرفتی 🤛💦",
    ]),
    (90, [
        "اوهوووو خیلی قوی بود!! {amount} تومن بابتش گرفتی 🔥🤛💦",
        "وحشی بود! {amount} تومن گرفتی 🔥🤛💦",
    ]),
    (100, [
        "اوه خدایا افسانه‌ای بود!!! {amount} تومن، جکپات 🏆🤛💦",
        "رکورد زدی!!! {amount} تومن بابتش گرفتی 🏆🤛💦",
    ]),
]


def pick_reply(amount: int) -> str:
    for max_amount, texts in REPLY_TIERS:
        if amount <= max_amount:
            return random.choice(texts).format(amount=amount)
    return random.choice(REPLY_TIERS[-1][1]).format(amount=amount)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
log = logging.getLogger("jagh-bot")


# ───────────────────────── دیتابیس ─────────────────────────
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS users (
                user_id  INTEGER PRIMARY KEY,
                name     TEXT,
                balance  INTEGER NOT NULL DEFAULT 0,
                last_ts  REAL    NOT NULL DEFAULT 0,
                started  INTEGER NOT NULL DEFAULT 0
            )"""
        )


def upsert_user(user_id: int, name: str):
    with db() as c:
        c.execute(
            "INSERT INTO users (user_id, name) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET name = excluded.name",
            (user_id, name),
        )


def set_started(user_id: int):
    with db() as c:
        c.execute("UPDATE users SET started = 1 WHERE user_id = ?", (user_id,))


def get_user(user_id: int):
    with db() as c:
        return c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()


# ───────────────────────── عضویت اجباری ─────────────────────────
async def missing_channels(bot, user_id: int) -> list[str]:
    """لیست کانال‌هایی که کاربر عضوشون نیست."""
    missing = []
    for ch in CHANNELS:
        try:
            m = await bot.get_chat_member(ch, user_id)
            ok = m.status in ("member", "administrator", "creator") or (
                m.status == "restricted" and getattr(m, "is_member", False)
            )
        except Exception as e:
            # معمولاً یعنی ربات توی اون کانال ادمین نیست
            log.warning("get_chat_member failed for %s: %s", ch, e)
            ok = False
        if not ok:
            missing.append(ch)
    return missing


def join_keyboard(missing: list[str]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"📢 عضویت در {ch}", url=f"https://t.me/{ch.lstrip('@')}")]
        for ch in missing
    ]
    rows.append([InlineKeyboardButton("✅ عضو شدم", callback_data="check_join")])
    return InlineKeyboardMarkup(rows)


WELCOME_OK = (
    "✅ عضویتت تایید شد!\n\n"
    f"حالا ربات رو توی گروه اضافه کن و هر ۵ دقیقه یه بار بنویس «{TRIGGER_WORD}» "
    f"تا بین {MIN_REWARD} تا {MAX_REWARD} تومن بگیری.\n\n"
    "دستورها:\n/wallet — موجودی\n/top — برترین‌ها"
)
JOIN_MSG = "🔒 برای استفاده از ربات باید توی کانال‌های زیر عضو باشی، بعدش دکمه «✅ عضو شدم» رو بزن:"


# ───────────────────────── هندلرها ─────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    upsert_user(user.id, user.full_name)
    missing = await missing_channels(context.bot, user.id)
    if missing:
        await update.message.reply_text(JOIN_MSG, reply_markup=join_keyboard(missing))
        return
    set_started(user.id)
    await update.message.reply_text(WELCOME_OK)


async def check_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = q.from_user
    upsert_user(user.id, user.full_name)
    missing = await missing_channels(context.bot, user.id)
    if missing:
        await q.answer("هنوز عضو همه کانال‌ها نشدی ❌", show_alert=True)
        try:
            await q.edit_message_reply_markup(reply_markup=join_keyboard(missing))
        except Exception:
            pass
        return
    set_started(user.id)
    await q.answer("تایید شد ✅")
    await q.edit_message_text(WELCOME_OK)


async def wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    row = get_user(user.id)
    bal = row["balance"] if row else 0
    await update.message.reply_text(f"💰 موجودی شما: {bal:,} تومن")


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db() as c:
        rows = c.execute(
            "SELECT name, balance FROM users WHERE balance > 0 "
            "ORDER BY balance DESC LIMIT 10"
        ).fetchall()
    if not rows:
        await update.message.reply_text("هنوز کسی پولی نگرفته!")
        return
    lines = [f"{i}. {r['name']} — {r['balance']:,} تومن" for i, r in enumerate(rows, 1)]
    await update.message.reply_text("🏆 برترین‌ها:\n\n" + "\n".join(lines))


async def on_group_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not msg.text or not user or user.is_bot:
        return
    if msg.text.strip() != TRIGGER_WORD:
        return

    upsert_user(user.id, user.full_name)
    row = get_user(user.id)
    bot_username = (await context.bot.get_me()).username

    # باید قبلاً /start زده و عضو کانال‌ها شده باشه
    if not row["started"]:
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("▶️ استارت ربات", url=f"https://t.me/{bot_username}?start=go")]]
        )
        await msg.reply_text("اول ربات رو استارت کن و توی کانال‌ها عضو شو 👇", reply_markup=kb)
        return

    # هر بار عضویت دوباره چک میشه (اگه لفت داده باشه دیگه پول نمی‌گیره)
    missing = await missing_channels(context.bot, user.id)
    if missing:
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("📢 عضویت در کانال‌ها", url=f"https://t.me/{bot_username}?start=go")]]
        )
        await msg.reply_text("از کانال‌ها خارج شدی! دوباره عضو شو 👇", reply_markup=kb)
        return

    now = time.time()
    remaining = COOLDOWN_SECONDS - (now - row["last_ts"])
    if remaining > 0:
        m, s = divmod(int(remaining) + 1, 60)
        await msg.reply_text(f"⏳ صبر کن! {m} دقیقه و {s} ثانیه دیگه می‌تونی دوباره بنویسی.")
        return

    amount = random.randint(MIN_REWARD, MAX_REWARD)
    with db() as c:
        c.execute(
            "UPDATE users SET balance = balance + ?, last_ts = ? WHERE user_id = ?",
            (amount, now, user.id),
        )
    await msg.reply_text(pick_reply(amount))


def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE))
    app.add_handler(CallbackQueryHandler(check_join, pattern="^check_join$"))
    app.add_handler(CommandHandler("wallet", wallet))
    app.add_handler(CommandHandler("top", top))
    app.add_handler(
        MessageHandler(filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND, on_group_text)
    )
    log.info("bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

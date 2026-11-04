import html
import logging
import os
import random
import time

import asyncpg
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


# ───────────────────── ایموجی پرمیوم ─────────────────────
# شماره‌ها رو با دستور /emojis (تو پی‌وی ربات) می‌تونی ببینی کدوم کدومه.
# اگه ایموجی پرمیوم نمایش داده نشد، همون ایموجی معمولی داخل تگ دیده میشه.
EMOJI_IDS = {
    1: "5411097243165929032",
    2: "5397669599145636159",
    3: "5987616823636401062",
    4: "5830203935775789535",
    5: "5834643712189141114",
    6: "5830326445422940546",
    7: "5832353674281620438",
    8: "5397818149179502406",
    9: "5938264290740933445",
    10: "5938388342281343001",
}
# کدوم شماره برای چی استفاده بشه (برای عوض کردن فقط عدد رو تغییر بده)
E_HAND, E_DROPS, E_FIRE, E_TROPHY, E_MEH = 1, 2, 3, 4, 5
E_WALLET, E_WAIT, E_OK = 6, 7, 8
E_CROWN = 9  # تاج لیدربرد (نفرات برتر)


def ce(n: int, fallback: str) -> str:
    return f'<tg-emoji emoji-id="{EMOJI_IDS[n]}">{fallback}</tg-emoji>'


HAND_DROPS = ce(E_HAND, "🤛") + ce(E_DROPS, "💦")
FIRE = ce(E_FIRE, "🔥")
TROPHY = ce(E_TROPHY, "🏆")
MEH = ce(E_MEH, "😐")

# ───────────────────────── تنظیمات ─────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]  # از Postgres ریلوی میاد

TRIGGER_WORD = "جق"
COOLDOWN_SECONDS = 5 * 60
MIN_REWARD = 5
MAX_REWARD = 100

# کانال‌های اجباری
CHANNELS = []  # خالی = عضویت اجباری خاموش

# متن پاسخ ربات بر اساس مبلغ.
# هر ردیف: (حداکثر مبلغ, [متن‌ها]). اولین ردیفی که مبلغ بهش بخوره انتخاب میشه
# و یکی از متن‌هاش رندوم میاد. {amount} = مبلغ
REPLY_TIERS = [
    (20, [
        "اوه ضعیف بود {MEH} فقط {amount} تومن بابتش گرفتی {HD}",
        "این که آبکی بود! {amount} تومن بیشتر نمی‌ارزید {HD}",
    ]),
    (45, [
        "بد نبود! {amount} تومن بابتش گرفتی {HD}",
        "اوکی بود، {amount} تومن به حسابت اضافه شد {HD}",
    ]),
    (70, [
        "اوه قوی بود! انقدر پول بابتش گرفتی: {amount} تومن {HD}",
        "اوه چه قوی! {amount} تومن بابتش گرفتی {HD}",
    ]),
    (90, [
        "اوهوووو خیلی قوی بود!! {amount} تومن بابتش گرفتی {FIRE}{HD}",
        "وحشی بود! {amount} تومن گرفتی {FIRE}{HD}",
    ]),
    (100, [
        "اوه خدایا افسانه‌ای بود!!! {amount} تومن، جکپات {TROPHY}{HD}",
        "رکورد زدی!!! {amount} تومن بابتش گرفتی {TROPHY}{HD}",
    ]),
]


def pick_reply(amount: int) -> str:
    for max_amount, texts in REPLY_TIERS:
        if amount <= max_amount:
            return random.choice(texts).format(amount=amount, HD=HAND_DROPS, FIRE=FIRE, TROPHY=TROPHY, MEH=MEH)
    return random.choice(REPLY_TIERS[-1][1]).format(amount=amount, HD=HAND_DROPS, FIRE=FIRE, TROPHY=TROPHY, MEH=MEH)


logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
)
log = logging.getLogger("jagh-bot")

pool: asyncpg.Pool | None = None


# ───────────────────────── دیتابیس (PostgreSQL) ─────────────────────────
async def init_db(app: Application):
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=5)
    async with pool.acquire() as c:
        await c.execute(
            """CREATE TABLE IF NOT EXISTS users (
                user_id  BIGINT PRIMARY KEY,
                name     TEXT,
                balance  BIGINT NOT NULL DEFAULT 0,
                last_ts  DOUBLE PRECISION NOT NULL DEFAULT 0,
                started  BOOLEAN NOT NULL DEFAULT FALSE
            )"""
        )
    log.info("database ready")


async def close_db(app: Application):
    if pool:
        await pool.close()


async def upsert_user(user_id: int, name: str):
    await pool.execute(
        "INSERT INTO users (user_id, name) VALUES ($1, $2) "
        "ON CONFLICT (user_id) DO UPDATE SET name = EXCLUDED.name",
        user_id,
        name,
    )


async def set_started(user_id: int):
    await pool.execute("UPDATE users SET started = TRUE WHERE user_id = $1", user_id)


async def get_user(user_id: int):
    return await pool.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)


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
    ce(E_OK, "✅") + " عضویتت تایید شد!\n\n"
    f"حالا ربات رو توی گروه اضافه کن و هر ۵ دقیقه یه بار بنویس «{TRIGGER_WORD}» "
    f"تا بین {MIN_REWARD} تا {MAX_REWARD} تومن بگیری.\n\n"
    "دستورها:\n/wallet — موجودی\n/top — برترین‌ها"
)
JOIN_MSG = "🔒 برای استفاده از ربات باید توی کانال‌های زیر عضو باشی، بعدش دکمه «✅ عضو شدم» رو بزن:"


# ───────────────────────── هندلرها ─────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await upsert_user(user.id, user.full_name)
    missing = await missing_channels(context.bot, user.id)
    if missing:
        await update.message.reply_text(JOIN_MSG, reply_markup=join_keyboard(missing))
        return
    await set_started(user.id)
    await update.message.reply_text(WELCOME_OK, parse_mode="HTML")


async def check_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user = q.from_user
    await upsert_user(user.id, user.full_name)
    missing = await missing_channels(context.bot, user.id)
    if missing:
        await q.answer("هنوز عضو همه کانال‌ها نشدی ❌", show_alert=True)
        try:
            await q.edit_message_reply_markup(reply_markup=join_keyboard(missing))
        except Exception:
            pass
        return
    await set_started(user.id)
    await q.answer("تایید شد ✅")
    await q.edit_message_text(WELCOME_OK, parse_mode="HTML")


async def wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    row = await get_user(update.effective_user.id)
    bal = row["balance"] if row else 0
    await update.message.reply_text(f"{ce(E_WALLET, '💰')} موجودی شما: {bal:,} تومن", parse_mode="HTML")


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await pool.fetch(
        "SELECT name, balance FROM users WHERE balance > 0 ORDER BY balance DESC LIMIT 10"
    )
    if not rows:
        await update.message.reply_text("هنوز کسی پولی نگرفته!")
        return
    lines = []
    for i, r in enumerate(rows, 1):
        mark = ce(E_CROWN, "👑") if i == 1 else f"{i}."
        lines.append(f"{mark} {html.escape(r['name'] or '')} — {r['balance']:,} تومن")
    await update.message.reply_text(
        f"{ce(E_CROWN, '👑')} نفرات برتر:\n\n" + "\n".join(lines), parse_mode="HTML"
    )


async def on_group_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not msg.text or not user or user.is_bot:
        return
    # حذف فاصله و کاراکترهای نامرئی (کیبوردهای فارسی گاهی نیم‌فاصله اضافه می‌کنن)
    text = "".join(ch for ch in msg.text if ch not in "\u200c\u200d\u200e\u200f\u200b\ufeff").strip()
    log.info("group text from %s: %r", user.id, msg.text)
    if text != TRIGGER_WORD:
        return

    await upsert_user(user.id, user.full_name)
    row = await get_user(user.id)
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
    amount = random.randint(MIN_REWARD, MAX_REWARD)

    # آپدیت اتمیک: فقط اگه ۵ دقیقه گذشته باشه پول اضافه میشه
    updated = await pool.fetchrow(
        "UPDATE users SET balance = balance + $1::bigint, last_ts = $2::double precision "
        "WHERE user_id = $3::bigint AND last_ts <= $2::double precision - $4::double precision "
        "RETURNING balance",
        amount,
        now,
        user.id,
        float(COOLDOWN_SECONDS),
    )
    if updated is None:
        row = await get_user(user.id)
        remaining = COOLDOWN_SECONDS - (now - row["last_ts"])
        m, s = divmod(int(max(remaining, 0)) + 1, 60)
        await msg.reply_text(f"{ce(E_WAIT, '⏳')} صبر کن! {m} دقیقه و {s} ثانیه دیگه می‌تونی دوباره بنویسی.", parse_mode="HTML")
        return

    await msg.reply_text(pick_reply(amount), parse_mode="HTML")


async def debug_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/check — نشون میده چک عضویت هر کانال چی برمی‌گردونه (برای عیب‌یابی)."""
    me = await context.bot.get_me()
    uid = update.effective_user.id
    lines = [f"🤖 ربات: @{me.username}\n"]
    for ch in CHANNELS:
        try:
            chat = await context.bot.get_chat(ch)
            m = await context.bot.get_chat_member(ch, uid)
            lines.append(f"✅ {ch} ({chat.type}) → وضعیت شما: {m.status}")
        except Exception as e:
            lines.append(f"❌ {ch} → {type(e).__name__}: {e}")
    await update.message.reply_text("\n".join(lines))


async def show_emojis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/emojis — شماره هر ایموجی پرمیوم رو نشون میده."""
    lines = [f"{n} → {ce(n, '⭐')}" for n in EMOJI_IDS]
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(init_db)
        .post_shutdown(close_db)
        .build()
    )
    app.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE))
    app.add_handler(CallbackQueryHandler(check_join, pattern="^check_join$"))
    app.add_handler(CommandHandler("check", debug_channels, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("emojis", show_emojis, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("wallet", wallet))
    app.add_handler(CommandHandler("top", top))
    app.add_handler(
        MessageHandler(filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND, on_group_text)
    )
    log.info("bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

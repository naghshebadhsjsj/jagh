import asyncio
import html
import logging
import os
import random
import re
import time

import asyncpg
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyParameters, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


# ───────────────────── ایموجی پرمیوم ─────────────────────
# هر «بخش» ربات یه لیست ایموجی داره (جق زدن، لیدربرد، موجودی، ...).
# پشتیبان‌ها تو پی‌وی ربات با /setemoji ایموجی‌های هر بخش رو عوض می‌کنن؛
# لازم نیست کد رو دست بزنن. تغییرها تو دیتابیس ذخیره میشن.
#
# برای اضافه کردن بخش جدید:
#   ۱) یه خط به SECTIONS اضافه کن: "کلید": ("اسم فارسی", ["اسلت۱", "اسلت۲"])
#   ۲) تو متن پیام بنویس: ce("کلید", 1, "⭐")  (عدد = شماره‌ی اسلت، ⭐ = ایموجی جایگزین)
# همین! پشتیبان‌ها خودشون با /setemoji کلید ... ایموجیش رو میذارن.
def B(text: str) -> str:
    """کل متن رو بولد می‌کنه. متن باید HTML معتبر باشه (مثلا شامل تگ ایموجی پرمیوم)."""
    return f"<b>{text}</b>"


def BP(text: str) -> str:
    """مثل B ولی برای متن ساده: کاراکترهای خاص HTML رو امن می‌کنه."""
    return B(html.escape(text))


SECTIONS = {
    "jagh":    ("جق زدن", ["دست", "قطره", "آتش", "جام", "بی‌حال", "صبر"]),
    "top":     ("لیدربرد", ["تاج"]),
    "wallet":  ("موجودی", ["کیف پول"]),
    "welcome": ("خوش‌آمد", ["تایید"]),
    "me":      ("کاراکتر", ["مدال", "پله‌ی بعدی"]),
}
# اسم فارسی بخش‌ها هم تو دستور قبوله (مثلا /setemoji لیدربرد ...)
ALIASES = {title: key for key, (title, _) in SECTIONS.items()}
ALIASES["جق"] = "jagh"

# ایموجی‌های پیش‌فرض (تا وقتی پشتیبان چیزی ست نکرده). اسلتی که اینجا نباشه
# و ست هم نشده باشه، ایموجی معمولی نشون داده میشه.
EMOJI_IDS = {
    ("jagh", 1): "5411097243165929032",
    ("jagh", 2): "5397669599145636159",
    ("jagh", 3): "5987616823636401062",
    ("jagh", 4): "5830203935775789535",
    ("jagh", 5): "5834643712189141114",
    ("jagh", 6): "5832353674281620438",
    ("wallet", 1): "5830326445422940546",
    ("welcome", 1): "5397818149179502406",
    ("top", 1): "5938264290740933445",
}


def ce(section: str, slot: int, fallback: str) -> str:
    eid = EMOJI_IDS.get((section, slot))
    if not eid:
        return fallback
    return f'<tg-emoji emoji-id="{eid}">{fallback}</tg-emoji>'


def emoji_vars() -> dict:
    """هر بار تازه ساخته میشه تا تغییرات /setemoji بدون ری‌استارت اعمال بشه."""
    return dict(
        HD=ce("jagh", 1, "🤛") + ce("jagh", 2, "💦"),
        FIRE=ce("jagh", 3, "🔥"),
        TROPHY=ce("jagh", 4, "🏆"),
        MEH=ce("jagh", 5, "😐"),
    )


# ───────────────────────── تنظیمات ─────────────────────────
BOT_TOKEN = os.environ["BOT_TOKEN"]
DATABASE_URL = os.environ["DATABASE_URL"]  # از Postgres ریلوی میاد

# آیدی عددی پشتیبان‌ها (با کاما جدا کن). هر کس آیدی خودش رو با /myid تو پی‌وی ربات می‌گیره.
# تو Railway یه Variable به اسم ADMIN_IDS بساز، مثلا: 123456789,987654321
ADMIN_IDS = {
    int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()
}

TRIGGER_WORD = "جق"
COOLDOWN_SECONDS = 5 * 60
MIN_REWARD = 5
MAX_REWARD = 100

# ───────────────────────── کاراکترها ─────────────────────────
# هر کاربر یه کاراکتر داره که با بالا رفتن موجودیش ارتقا می‌گیره (۱۰ سطح).
# موجودی لازم برای رسیدن به هر سطح (سطح ۱ از صفره). عددها رو راحت عوض کن.
LEVEL_THRESHOLDS = [0, 300, 1_000, 2_500, 5_000, 10_000, 20_000, 40_000, 80_000, 150_000]

# هر سطح: (اسم, توضیح کوتاه). "m" = آقا، "f" = خانوم
CHARACTERS = {
    "m": [
        ("سرباز جقی", "تازه از پادگان اومده، هنوز دستش گرم نشده."),
        ("دانشجو جقی", "جزوه‌ش خالیه ولی دستش همیشه پره."),
        ("راننده تاکسی جقی", "کرایه نمی‌گیره، فقط می‌پیچونه."),
        ("فروشنده جقی", "ساعت ۳ شبه و فقط یه مشتری داره."),
        ("وکیل جقی", "پرونده‌ها رو ول کرده و خوابیده."),
        ("فضانورد جقی", "تا مدار زمین رفته ولی دستش هنوز همونجاست."),
        ("آقای رئیس خسته", "جلسه ۴ ساعته شد و هنوز ساعت یک ظهره."),
        ("رئیس‌جمهور چرتی", "وسط جلسه خوابید، کسی بیدارش نمی‌کنه."),
        ("رهبر خواب‌آلود", "از خستگیه یا از حوصله‌سررفتگی؟."),
        ("میلیاردر بی‌خواب", "راکت داره، ربات داره، خواب نداره."),
    ],
    "f": [
        ("دختر دانشجو جقی", "جزوه‌ها رو باز کرده ولی فقط به یه چیز فکر می‌کنه."),
        ("دختر بلاگر جقی", "لایو رو زده ولی فالوئرها تو ذهنشن، نه تو دوربین."),
        ("دختر رقاص جقی", "روی صحنه می‌رقصه، پشت صحنه خسته‌ست."),
        ("دختر ناخن‌کار جقی", "ناخن مشتری رو می‌کشه، حواسش جای دیگه‌ست."),
        ("دختر دندان‌پزشک جقی", "ماسکش افتاده و کسی نمی‌پرسه چرا."),
        ("دختر داروخونه‌ای جقی", "برای همه نسخه می‌پیچه، برای خودش نه."),
        ("دختر وکیل جقی", "پرونده‌ها رو بسته و دلیلش رو فقط خودش می‌دونه."),
        ("دختر فضانورد جقی", "تا مدار زمین رفته ولی دلش هنوز همینجاست."),
        ("دختر رازآلود جقی", "منتظر عکس جدید."),
        ("دختر میلیاردر جقی", "پول رو دور خودش ریخته و خوابش نمیاد."),
    ],
}

# ایموجی پرمیومِ هر سطح (کنار اسم کاراکتر تو کپشن میاد)
CHAR_EMOJIS = {
    "m": {
        1: ["5253988473349640454"],
        2: ["6325398374921016616", "5447485932674494985"],
        3: ["5379668974596302326"],
        4: ["5947405845162629459"],
        5: ["5992274629769566150", "5992496323096483934"],
        6: ["5992127827787388962", "5992470763746105553"],
        7: ["5947405845162629459"],
        8: ["5285185006958828732", "5388837552952205051"],
        9: ["5947405845162629459"],
        10: ["6316490939166625054", "5343870765781493324", "5071341394199578555"],
    },
    "f": {
        1: ["5469905619009951647"],
        2: ["5379814861750443581"],
        3: ["5226754369193523447"],
        4: ["5379814861750443581"],
        5: ["5379814861750443581"],
        6: ["5379814861750443581"],
        7: ["5226703465241133001"],
        8: ["5379814861750443581"],
        9: ["5379814861750443581"],
        10: ["5343870765781493324"],
    },
}


def char_emojis(gender: str, lvl: int) -> str:
    return "".join(f'<tg-emoji emoji-id="{e}">⭐</tg-emoji>' for e in CHAR_EMOJIS.get(gender, {}).get(lvl, []))


GENDER_WORDS = {
    "m": "m", "مرد": "m", "آقا": "m", "پسر": "m",
    "f": "f", "زن": "f", "خانوم": "f", "دختر": "f",
}
ME_WORD = "خودم"  # تو گروه این کلمه رو بنویسی کاراکترت نشون داده میشه

# عکس هر کاراکتر (file_id تلگرام). پشتیبان با /setphoto ست می‌کنه و تو دیتابیس می‌مونه.
CHAR_PHOTOS: dict[tuple[str, int], str] = {}


BOT_VERSION = "کاراکترها + شغل‌ها"


def norm_word(text: str) -> str:
    """نرمال‌سازی برای تشخیص «خودم»: ی/ک عربی، نیم‌فاصله، فاصله و علامت‌های آخر جمله."""
    text = text.replace("ي", "ی").replace("ك", "ک")
    text = "".join(ch for ch in text if ch not in "\u200c\u200d\u200e\u200f\u200b\ufeff")
    return text.strip().strip(".!؟?،, ")


def level_for(balance: int) -> int:
    lvl = 1
    for i, need in enumerate(LEVEL_THRESHOLDS, 1):
        if balance >= need:
            lvl = i
    return lvl


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
    texts = REPLY_TIERS[-1][1]
    for max_amount, tier_texts in REPLY_TIERS:
        if amount <= max_amount:
            texts = tier_texts
            break
    return random.choice(texts).format(amount=amount, **emoji_vars())


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
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS gender TEXT")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS jaghs BIGINT NOT NULL DEFAULT 0")
        # کاربرهای قدیمی که تعداد جقشون ثبت نشده: از روی موجودی تخمین زده میشه (میانگین هر جق ≈ ۵۲ تومن)
        await c.execute("UPDATE users SET jaghs = balance / 52 WHERE jaghs = 0 AND balance > 0")
        await c.execute(
            """CREATE TABLE IF NOT EXISTS char_photos (
                gender  TEXT NOT NULL,
                level   INT  NOT NULL,
                file_id TEXT NOT NULL,
                PRIMARY KEY (gender, level)
            )"""
        )
        for r in await c.fetch("SELECT gender, level, file_id FROM char_photos"):
            CHAR_PHOTOS[(r["gender"], r["level"])] = r["file_id"]
        await c.execute(
            """CREATE TABLE IF NOT EXISTS emoji_slots (
                section  TEXT NOT NULL,
                slot     INT  NOT NULL,
                emoji_id TEXT NOT NULL,
                PRIMARY KEY (section, slot)
            )"""
        )
        for r in await c.fetch("SELECT section, slot, emoji_id FROM emoji_slots"):
            EMOJI_IDS[(r["section"], r["slot"])] = r["emoji_id"]
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


def welcome_ok() -> str:
    return B(
        ce("welcome", 1, "✅") + " عضویتت تایید شد!\n\n"
        f"حالا ربات رو توی گروه اضافه کن و هر ۵ دقیقه یه بار بنویس «{TRIGGER_WORD}» "
        f"تا بین {MIN_REWARD} تا {MAX_REWARD} تومن بگیری.\n\n"
        f"تو گروه «{ME_WORD}» رو بنویس تا کاراکترت رو ببینی.\n\n"
        "دستورها:\n/wallet — موجودی\n/top — برترین‌ها\n/me — کاراکتر من"
    )

JOIN_MSG = "🔒 برای استفاده از ربات باید توی کانال‌های زیر عضو باشی، بعدش دکمه «✅ عضو شدم» رو بزن:"


# ───────────────────────── هندلرها ─────────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await upsert_user(user.id, user.full_name)
    missing = await missing_channels(context.bot, user.id)
    if missing:
        await update.message.reply_text(BP(JOIN_MSG), parse_mode="HTML", reply_markup=join_keyboard(missing))
        return
    await set_started(user.id)
    await update.message.reply_text(welcome_ok(), parse_mode="HTML")


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
    await q.edit_message_text(welcome_ok(), parse_mode="HTML")


async def wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    row = await get_user(update.effective_user.id)
    bal = row["balance"] if row else 0
    await update.message.reply_text(B(f"{ce('wallet', 1, '💰')} موجودی شما: {bal:,} تومن"), parse_mode="HTML")


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = await pool.fetch(
        "SELECT name, balance FROM users WHERE balance > 0 ORDER BY balance DESC LIMIT 10"
    )
    if not rows:
        await update.message.reply_text(BP("هنوز کسی پولی نگرفته!"), parse_mode="HTML")
        return
    lines = []
    for i, r in enumerate(rows, 1):
        mark = ce("top", 1, "👑") if i == 1 else f"{i}."
        lines.append(f"{mark} {html.escape(r['name'] or '')} — {r['balance']:,} تومن")
    await update.message.reply_text(
        B(f"{ce('top', 1, '👑')} نفرات برتر:\n\n" + "\n".join(lines)), parse_mode="HTML"
    )


async def on_group_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not msg.text or not user or user.is_bot:
        return
    # حذف فاصله و کاراکترهای نامرئی (کیبوردهای فارسی گاهی نیم‌فاصله اضافه می‌کنن)
    text = "".join(ch for ch in msg.text if ch not in "\u200c\u200d\u200e\u200f\u200b\ufeff").strip()
    log.info("group text from %s: %r", user.id, msg.text)
    if norm_word(text) == ME_WORD:
        await show_me(update, context)
        return
    if norm_word(text).replace(" ", "").lower() in JOBS_WORDS:
        await show_jobs(update, context)
        return
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
        await msg.reply_text(BP("اول ربات رو استارت کن و توی کانال‌ها عضو شو 👇"), parse_mode="HTML", reply_markup=kb)
        return

    # هر بار عضویت دوباره چک میشه (اگه لفت داده باشه دیگه پول نمی‌گیره)
    missing = await missing_channels(context.bot, user.id)
    if missing:
        kb = InlineKeyboardMarkup(
            [[InlineKeyboardButton("📢 عضویت در کانال‌ها", url=f"https://t.me/{bot_username}?start=go")]]
        )
        await msg.reply_text(BP("از کانال‌ها خارج شدی! دوباره عضو شو 👇"), parse_mode="HTML", reply_markup=kb)
        return

    now = time.time()
    amount = random.randint(MIN_REWARD, MAX_REWARD)

    # آپدیت اتمیک: فقط اگه ۵ دقیقه گذشته باشه پول اضافه میشه
    updated = await pool.fetchrow(
        "UPDATE users SET balance = balance + $1::bigint, jaghs = jaghs + 1, last_ts = $2::double precision "
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
        await msg.reply_text(B(f"{ce('jagh', 6, '⏳')} صبر کن! {m} دقیقه و {s} ثانیه دیگه می‌تونی دوباره بنویسی."), parse_mode="HTML")
        return

    text = pick_reply(amount)
    new_bal = updated["balance"]
    gender = row["gender"]
    if gender in CHARACTERS and level_for(new_bal) > level_for(new_bal - amount):
        title = html.escape(CHARACTERS[gender][level_for(new_bal) - 1][0])
        text += f"\n\n{ce('me', 1, '🎖')} ارتقا گرفتی! حالا «{title}» هستی. برای دیدنش بنویس «{ME_WORD}»"
    await msg.reply_text(B(text), parse_mode="HTML")


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
    await update.message.reply_text(BP("\n".join(lines)), parse_mode="HTML")


def resolve_section(word: str):
    w = word.strip().lower()
    return w if w in SECTIONS else ALIASES.get(word.strip())


def sections_text() -> str:
    lines = []
    for key, (title, names) in SECTIONS.items():
        slots = "، ".join(f"{i}={n}" for i, n in enumerate(names, 1))
        lines.append(f"{title} ({key}): {slots}")
    return "\n".join(lines)


async def show_emojis(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/emojis — ایموجی فعلی هر بخش رو نشون میده."""
    blocks = []
    for key, (title, names) in SECTIONS.items():
        rows = [
            f"{i} ({n}) → " + (ce(key, i, "⭐") if (key, i) in EMOJI_IDS else "تنظیم نشده")
            for i, n in enumerate(names, 1)
        ]
        blocks.append(f"{title} ({key})\n" + "\n".join(rows))
    await update.message.reply_text(B("\n\n".join(blocks)), parse_mode="HTML")


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/myid — آیدی عددی خودت رو میده تا بذاری تو ADMIN_IDS."""
    await update.message.reply_text(BP(f"آیدی شما: {update.effective_user.id}"), parse_mode="HTML")


SETEMOJI_HELP = (
    "برای عوض کردن ایموجی‌های یه بخش، اسم بخش + ایموجی‌های پرمیوم رو بفرست:\n\n"
    "/setemoji jagh 🤛💦🔥🏆😐⏳\n"
    "/setemoji top 👑\n"
    "/setemoji wallet 💰\n\n"
    "ایموجی‌ها به ترتیب میرن جای اسلت‌های اون بخش. اگه فقط یکی رو می‌خوای عوض کنی، "
    "شماره‌ی اسلت رو بعد از اسم بخش بنویس:\n"
    "/setemoji jagh 3 🔥  (فقط آتش عوض میشه)\n\n"
    "بخش‌ها و اسلت‌هاشون:\n" + sections_text() + "\n\nبرای دیدن وضعیت فعلی: /emojis"
)


async def set_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setemoji <بخش> [شماره‌ی اسلت] <ایموجی‌های پرمیوم> — فقط پشتیبان‌ها (ADMIN_IDS)."""
    msg = update.message
    if update.effective_user.id not in ADMIN_IDS:
        await msg.reply_text(BP("این دستور فقط برای پشتیبان‌هاست. آیدی خودت رو با /myid بگیر و به صاحب ربات بده."), parse_mode="HTML")
        return

    args = context.args or []
    ents = sorted(
        (e for e in (msg.entities or []) if e.type == "custom_emoji"), key=lambda e: e.offset
    )
    if not args:
        await msg.reply_text(BP(SETEMOJI_HELP), parse_mode="HTML")
        return

    section = resolve_section(args[0])
    if section is None:
        await msg.reply_text(BP(f"بخشی به اسم «{args[0]}» نداریم.\n\nبخش‌ها:\n" + sections_text()), parse_mode="HTML")
        return
    if not ents:
        await msg.reply_text(BP("ایموجی پرمیوم پیدا نشد. دستور رو همراه ایموجی‌های پرمیوم بفرست.\n\n" + SETEMOJI_HELP), parse_mode="HTML")
        return

    first = int(args[1]) if len(args) > 1 and args[1].isdigit() else 1
    title, names = SECTIONS[section]
    if first < 1 or first + len(ents) - 1 > len(names):
        await msg.reply_text(
            BP(
                f"بخش «{title}» فقط {len(names)} اسلت داره. از اسلت {first} با {len(ents)} ایموجی جا نمیشه.\n\n"
                + sections_text()
            ),
            parse_mode="HTML",
        )
        return

    for i, e in enumerate(ents):
        slot = first + i
        await pool.execute(
            "INSERT INTO emoji_slots (section, slot, emoji_id) VALUES ($1, $2, $3) "
            "ON CONFLICT (section, slot) DO UPDATE SET emoji_id = EXCLUDED.emoji_id",
            section,
            slot,
            e.custom_emoji_id,
        )
        EMOJI_IDS[(section, slot)] = e.custom_emoji_id

    lines = [f"{first + i} ({names[first + i - 1]}) → {ce(section, first + i, '⭐')}" for i in range(len(ents))]
    await msg.reply_text(B(f"✅ بخش «{title}» ذخیره شد:\n" + "\n".join(lines)), parse_mode="HTML")


# ───────────────────────── کاراکتر من ─────────────────────────
def card_text(name: str, gender: str, balance: int) -> str:
    lvl = level_for(balance)
    title, tagline = CHARACTERS[gender][lvl - 1]
    lines = [
        f"{ce('me', 1, '🎖')} {html.escape(name)}",
        "",
        f"سطح {lvl} از {len(LEVEL_THRESHOLDS)}: {html.escape(title)} {char_emojis(gender, lvl)}",
        html.escape(tagline),
        "",
        f"{ce('wallet', 1, '💰')} موجودی: {balance:,} تومن",
    ]
    if lvl < len(LEVEL_THRESHOLDS):
        need = LEVEL_THRESHOLDS[lvl] - balance
        nxt = html.escape(CHARACTERS[gender][lvl][0])
        lines.append(f"{ce('me', 2, '⬆️')} {need:,} تومن تا «{nxt}»")
    else:
        lines.append(f"{ce('top', 1, '👑')} به بالاترین سطح رسیدی!")
    return B("\n".join(lines))



# ───────────── عکس کاراکتر با دارایی کاربر ─────────────
BASE_DIR = Path(__file__).parent
_FA_DIGITS = str.maketrans("0123456789,", "۰۱۲۳۴۵۶۷۸۹،")


def _first_existing(*paths: Path) -> Path | None:
    for p in paths:
        if p.exists():
            return p
    return None


def card_path(gender: str, level: int) -> Path | None:
    """عکس کارت: یا کنار bot.py با اسم m_3.png / f_3.png، یا تو پوشه‌ی chars/m/char_3.png"""
    return _first_existing(
        BASE_DIR / f"{gender}_{level}.png",
        BASE_DIR / "chars" / gender / f"char_{level}.png",
    )



# فونت کوچیکِ فقط-عدد (فارسی و لاتین) که تو خود کد گذاشته شده؛ پس لازم نیست فایل فونت آپلود بشه.
_DIGIT_FONT_B64 = """
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
A3J9A3H+A3BvLAVvLANu+gNt+gNs+gNr/gNq/gNp/gNoYwwFaDIDZ/4DZjIDZWQKBWX+A2QKA2RABGNiCgVjDANiCgNhYBUFYZYD
YAERBWAVA18KA17+A13+A1wBEQVc/gNbWhsFW/4DWgERBVobA1n+A1j6A1f+A1YBEQVA/1b+A1X+A1QeA1MUA1JRGQVS+gNRAREF
URkDUE8ZBVD6A09OEQVPGQNOEQNNHgNMSxQFTBUDS0oRBUsUA0pJDgVKEQNJDgNI+gNHRhQFRxUDRhQDRfoDREMOBUQPA0MOA0JB
JQVC+gNBAREFQSUDQD8PBUD+Az8+DgU/DwM+DgM9PA0FPRYDPA0DO2QDOv4DORQDOP4DNxMDNjUaBTYlAzU0FAU1GgM1wAQ0Cg0F
NBQDNIAEMzIMBTMUAzNABDIMAzEwpgUx/gMwAREFMKYDLwwDLhMDLSw6BS36AywVJQUsOgMrZAMqZAMp/gMoFQMnFxEFJx4DJiAD
JR4DJCMRBUArJB4DIxEDIgANBSL6AyEPAyFABCAUAx8KAx4eAx0cGQUdJQMcDxMFHBkDHLgBAECRBBsNAxoZSwUafQMZAREFGUsD
GP4DFxEDFhUlBRb6AxUBEQUVJQMUZAMTEQMS/gMRAREFEf4DEGQDDw4QBQ8TAw/ABA4QAw6ABA0BEQUN+gMMMgMLCg0FCxYDC4AE
Cg0DCkAECf4DCP4DB/4DBgUKBQb+AwUKAwVABAT6AwNkAwIBEQUC/gMBAA0FAREDAA0DAbgBZIWNASsrKysrKysrKysrKysrKysr
KysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysr
KysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysr
KysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysAKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysr
KysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysr
KysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysrKysr
KysrKysrKysrKysrKysrKysrKysrKysrKysrHQABZgEzAWYAvADpAAABPQCiAPoDHwACAAIAZgFmAAIAAgCsAVQA7AC8AGIBZgGB
BIUBVAFmAW0EpAACAWYAfwTNAAAAAgEzAGIAcQAAACUEpAG8ALoA5QBmAYEBjQVIBVoBZgFtAAAAAAACAAIA9gXDAfAFOQI5AFgE
bQQ9BLIEgQSyAWYBdQRmBIEAsARmBDkC0QScBHsEzwR7AFgBMwFmAUwBZgFMAAIArACaAUoBIwCaApoBRAEZAUQCzQDBAAABZgE/
AZoBOwXLBcsA1QDVAVAArACsAHcCCgHHAfIBLwFYAbIBIwD2APYBHwEvATUCNQHuAecBMwCYANEDWAUKAJoAjwESAJgAvADNAOUA
5QDyAHMEAAFmAI8F1QIrBdUAwwDhANcA5QAAAGoBAgAAAB0DLQXVBdUF8ACoAGoA7ADhAQIF1QYUByEEZgL4AOwBgwKmAvgBIwEC
AQIBEgEfAx8AXgPNBGAExwSJAOwBvAC6AQIDMwMfA0IDMwNcARIBHwXVAZoAmgDhBmYBeQRgBGAEYAR7AAAA7ALDArgCzQC+AN0A
1QAAAGoCXAJ7ApoA3QGuAboBEgAAAIUBrgRgB2IEGwCaBpoEWADuAJoCmgDRAs0BmgFQBcsFywCLAIsGMQD2BAYA8ANMAWAEqADB
AAAAJQXBAQABIQdKBhIAlgFKB4MAqAAAAzcAewAUAAAAyQEABcEFwQXBBcEBAAEIBh0AlgQnA54A7AECAn0BMwCYANEDWAF5AM0C
OQNiAJwAnACcAJMBuACTALgAcwAAFAADJgAAACUAJQBDAIUAsgEjAYYBxgIWAnACpgMUA20DfgOMA6UD1QQTBDwEZQSnBK8EtwS/
BMcFCgVIBX4FhgWOBZYAAgBm/pYEZgWkAAMABwAfvAAEASYAAAAGASa2AQgFiQIEAC/E1OwxABDU7NTsMBMRIRElIREhZgQA/HMD
G/zl/pYHDvjycgYpAAABAG3+3QI5AYMABQAZQAwDqQCoBgMEAQIAEwYQ/OzUzDEAEPzsMBMhEQMjE9EBaPfVZAGD/s/+iwF1AAAC
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
BRQQBgATAAD//wA/AAAEuQUgEAYAFAAAAAAABwBaAAMAAQQJAAABMAAAAAMAAQQJAAEAFgEwAAMAAQQJAAIACAFGAAMAAQQJAAMA
IAFOAAMAAQQJAAQAIAFOAAMAAQQJAAUAGAFuAAMAAQQJAAYAHgGGAEMAbwBwAHkAcgBpAGcAaAB0ACAAKABjACkAIAAyADAAMAAz
ACAAYgB5ACAAQgBpAHQAcwB0AHIAZQBhAG0ALAAgAEkAbgBjAC4AIABBAGwAbAAgAFIAaQBnAGgAdABzACAAUgBlAHMAZQByAHYA
ZQBkAC4ACgBDAG8AcAB5AHIAaQBnAGgAdAAgACgAYwApACAAMgAwADAANgAgAGIAeQAgAFQAYQB2AG0AagBvAG4AZwAgAEIAYQBo
AC4AIABBAGwAbAAgAFIAaQBnAGgAdABzACAAUgBlAHMAZQByAHYAZQBkAC4ACgBEAGUAagBhAFYAdQAgAGMAaABhAG4AZwBlAHMA
IABhAHIAZQAgAGkAbgAgAHAAdQBiAGwAaQBjACAAZABvAG0AYQBpAG4ACgBEAGUAagBhAFYAdQAgAFMAYQBuAHMAQgBvAGwAZABE
AGUAagBhAFYAdQAgAFMAYQBuAHMAIABCAG8AbABkAFYAZQByAHMAaQBvAG4AIAAyAC4AMwA3AEQAZQBqAGEAVgB1AFMAYQBuAHMA
LQBCAG8AbABkAAAAAwAAAAAAAP/YAFoAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAIACAAC//8AAwABAAAADAAAAAAAAAACAAIAAQAN
AAEAFQAeAAEAAQAAAAoAHAAeAAFERkxUAAgABAAAAAD//wAAAAAAAAABAAAACgCSAJQAFERGTFQAemFyYWIAhGFybW4AhGJyYWkA
hGNhbnMAhGNoZXIAhGN5cmwAhGdlb3IAhGdyZWsAhGhhbmkAhGhlYnIAhGthbmEAhGxhbyAAhGxhdG4AhG1hdGgAhG5rbyAAhG9n
YW0AhHJ1bnIAhHRmbmcAhHRoYWkAhAAEAAAAAP//AAAAAAAAAAAAAAAA
"""


def load_font(size: int):
    fp = _first_existing(BASE_DIR / "DejaVuSans-Bold.ttf", BASE_DIR / "fonts" / "DejaVuSans-Bold.ttf")
    if fp:
        return ImageFont.truetype(str(fp), size)
    import base64
    return ImageFont.truetype(BytesIO(base64.b64decode("".join(_DIGIT_FONT_B64.split()))), size)


def font_path() -> Path | None:
    return _first_existing(BASE_DIR / "DejaVuSans-Bold.ttf", BASE_DIR / "fonts" / "DejaVuSans-Bold.ttf")


def render_card_image(gender: str, level: int, balance: int) -> BytesIO | None:
    """عکس آماده‌ی سطح (چاپ‌شده با اسم و لقب) رو برمیدارد و عدد دارایی رو تو کادر پایینش می‌نویسه."""
    path = card_path(gender, level)
    if not path:
        log.warning("card image NOT FOUND: %s_%s.png (کنار bot.py آپلودش کردی؟)", gender, level)
        return None
    im = Image.open(path).convert("RGB")
    d = ImageDraw.Draw(im)
    font = load_font(52)
    d.text((355, 912), f"{balance:,}".translate(_FA_DIGITS), font=font, fill=(255, 255, 255), anchor="mm")
    out = BytesIO()
    im.save(out, "JPEG", quality=92)
    out.seek(0)
    out.name = f"char_{level}.jpg"
    return out


async def send_card(bot, chat_id: int, row, reply_to: int | None = None):
    """کارت کاراکتر کاربر: عکس + کپشن (اسم، سطح، موجودی). اگه یه مرحله خطا بده میره سراغ مرحله‌ی بعد، ولی تا جایی که بشه عکس همراه کپشن میاد."""
    gender = row["gender"]
    lvl = level_for(row["balance"])
    text = card_text(row["name"] or "", gender, row["balance"])
    rp = ReplyParameters(message_id=reply_to, allow_sending_without_reply=True) if reply_to else None

    # کپشن بدون ایموجی پرمیوم (برای وقتی که تلگرام ایموجی رو قبول نکنه)
    plain_text = re.sub(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", r"\1", text)

    photo = CHAR_PHOTOS.get((gender, lvl))  # عکس دلخواهِ پشتیبان (file_id) اولویت داره
    if not photo:
        try:
            photo = await asyncio.to_thread(render_card_image, gender, lvl, row["balance"])
        except Exception:
            log.exception("render_card_image failed (level %s)", lvl)
            photo = None

    if photo:
        for caption in (text, plain_text):
            try:
                if hasattr(photo, "seek"):
                    photo.seek(0)
                await bot.send_photo(chat_id, photo, caption=caption, parse_mode="HTML", reply_parameters=rp)
                return
            except Exception:
                log.exception("send_photo failed (level %s)", lvl)
    for t in (text, plain_text):
        try:
            await bot.send_message(chat_id, t, parse_mode="HTML", reply_parameters=rp)
            return
        except Exception:
            log.exception("send_message failed")


def gender_keyboard(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("🧔 آقا", callback_data=f"g:{uid}:m"),
            InlineKeyboardButton("👩 خانوم", callback_data=f"g:{uid}:f"),
        ]]
    )


async def ask_gender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/gender — انتخاب (یا عوض کردن) جنسیت کاراکتر."""
    msg, user = update.effective_message, update.effective_user
    await upsert_user(user.id, user.full_name)
    await msg.reply_text(
        BP(f"{user.full_name}، جنسیتت رو انتخاب کن تا کاراکترت مشخص بشه 👇"),
        parse_mode="HTML",
        reply_markup=gender_keyboard(user.id),
    )


async def show_me(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/me یا نوشتن «خودم» تو گروه — کاراکتر کاربر."""
    msg, user = update.effective_message, update.effective_user
    await upsert_user(user.id, user.full_name)
    row = await get_user(user.id)
    if not row["gender"]:
        await ask_gender(update, context)
        return
    await send_card(context.bot, msg.chat_id, row, reply_to=msg.message_id)


async def on_private_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تو پی‌وی ربات هم «خودم» کار می‌کنه."""
    msg = update.effective_message
    if msg and msg.text and norm_word(msg.text) == ME_WORD:
        await show_me(update, context)
    elif msg and msg.text and norm_word(msg.text).replace(" ", "").lower() in JOBS_WORDS:
        await show_jobs(update, context)


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/ping — نشون میده ربات روشنه و کدوم نسخه‌ی کد داره اجرا میشه."""
    await update.effective_message.reply_text(
        B(f"✅ ربات روشنه\nنسخه: {BOT_VERSION}"), parse_mode="HTML"
    )


async def pick_gender(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, uid, g = q.data.split(":")
    if q.from_user.id != int(uid):
        await q.answer("این دکمه مال تو نیست 😅", show_alert=True)
        return
    await upsert_user(q.from_user.id, q.from_user.full_name)
    await pool.execute("UPDATE users SET gender = $1 WHERE user_id = $2", g, q.from_user.id)
    await q.answer("ثبت شد ✅")
    try:
        await q.message.delete()
    except Exception:
        pass
    row = await get_user(q.from_user.id)
    await send_card(context.bot, q.message.chat_id, row)


# ───────────── عکس کاراکترها (فقط پشتیبان‌ها) ─────────────
def photos_status() -> str:
    lines = []
    for g, label in (("m", "آقا"), ("f", "خانوم")):
        for i, (title, _) in enumerate(CHARACTERS[g], 1):
            mark = "✅" if (g, i) in CHAR_PHOTOS else "❌"
            lines.append(f"{mark} {g} {i} — {title}")
        lines.append("")
    return "\n".join(lines).strip()


async def photo_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setphoto — راهنما و وضعیت عکس‌ها."""
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text(BP("این دستور فقط برای پشتیبان‌هاست."), parse_mode="HTML")
        return
    await update.message.reply_text(
        BP(
            "برای ست کردن عکس یه کاراکتر، عکس رو بفرست و تو کپشنش بنویس:\n\n"
            "/setphoto m 1\n\n"
            "m = آقا، f = خانوم، عدد = سطح (۱ تا ۱۰). مثلا /setphoto f 10 عکس ملکه‌ست. "
            "عکسی که ست نشده باشه، کاراکتر بدون عکس نشون داده میشه.\n\n"
            + photos_status()
        ),
        parse_mode="HTML",
    )


async def set_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عکس با کپشن /setphoto m 3 — فقط پشتیبان‌ها."""
    msg = update.message
    if update.effective_user.id not in ADMIN_IDS:
        await msg.reply_text(BP("این دستور فقط برای پشتیبان‌هاست."), parse_mode="HTML")
        return
    parts = (msg.caption or "").split()
    g = GENDER_WORDS.get(parts[1].lower()) if len(parts) > 1 else None
    lvl = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
    if g is None or not 1 <= lvl <= len(LEVEL_THRESHOLDS):
        await msg.reply_text(
            BP("کپشن درست نیست. باید این شکلی باشه: /setphoto m 1  (m یا f، بعد سطح از ۱ تا ۱۰)"),
            parse_mode="HTML",
        )
        return
    fid = msg.photo[-1].file_id
    await pool.execute(
        "INSERT INTO char_photos (gender, level, file_id) VALUES ($1, $2, $3) "
        "ON CONFLICT (gender, level) DO UPDATE SET file_id = EXCLUDED.file_id",
        g,
        lvl,
        fid,
    )
    CHAR_PHOTOS[(g, lvl)] = fid
    title = CHARACTERS[g][lvl - 1][0]
    await msg.reply_text(BP(f"✅ عکس «{title}» ذخیره شد."), parse_mode="HTML")



# ───────────────────────── شغل‌ها ─────────────────────────
# با نوشتن «شغل» یا «شغل ها» لیست شغل‌ها میاد: حقوق + اینکه چند جق مونده تا اون شغل.
JOB_REQUIRED_JAGHS = [5, 100, 250, 450, 700, 1000, 1400, 1800, 2150, 2500]
JOB_PAY = [
    ("30K", "50K"), ("80K", "100K"), ("150K", "250K"), ("400K", "600K"), ("1.2M", "2M"),
    ("4M", "7M"), ("12M", "20M"), ("35M", "55M"), ("90M", "130M"), ("175M", "200M"),
]
# (اسم شغل، سطح کاراکتری که ایموجیش ازش میاد)
JOBS = {
    "m": [("دانشجو", 2), ("سرباز", 1), ("راننده تاکسی", 3), ("فروشنده", 4), ("وکیل", 5),
          ("فضانورد", 6), ("رئیس اداره", 7), ("رئیس‌جمهور", 8), ("رهبر", 9), ("میلیاردر", 10)],
    "f": [("دانشجو", 1), ("بلاگر", 2), ("رقاص", 3), ("ناخن‌کار", 4), ("دندان‌پزشک", 5),
          ("داروخونه‌دار", 6), ("وکیل", 7), ("فضانورد", 8), ("شغل مرموز", 9), ("میلیاردر", 10)],
}
JOB_GLYPHS = {"دانشجو": "🎓", "سرباز": "🪖", "راننده تاکسی": "🚕", "فروشنده": "🛍", "وکیل": "⚖️",
              "فضانورد": "🚀", "رئیس اداره": "👔", "رئیس‌جمهور": "🏛", "رهبر": "🎖", "میلیاردر": "💰",
              "بلاگر": "📱", "رقاص": "💃", "ناخن‌کار": "💅", "دندان‌پزشک": "🦷", "داروخونه‌دار": "💊"}
JOBS_WORDS = {"شغل", "شغلها", "jobs"}


def _w(t: str) -> int:
    """عرض متن در فونت مونواسپیس (نیم‌فاصله عرض نداره)."""
    return len(t.replace("\u200c", ""))


def _pad(t: str, n: int) -> str:
    return t + " " * max(n - _w(t), 0)


def jobs_rows(gender: str, jaghs: int):
    """[(ایموجی، اسم، حقوق، جق مانده یا None، پیدا شده؟)] + شغل بعدی."""
    rows, nxt = [], None
    for i, (name, _lvl) in enumerate(JOBS[gender]):
        left = max(JOB_REQUIRED_JAGHS[i] - jaghs, 0)
        lo, hi = JOB_PAY[i]
        if left and nxt is None:
            nxt = (name, left)
        rows.append((JOB_GLYPHS.get(name, "💼"), name, f"{lo}-{hi}", left))
    return rows, nxt


def jobs_footer(jaghs: int, nxt) -> str:
    lines = [f"✅ داری · 🔒 جق کم", f"{ce('jagh', 1, '🤛')} جق‌های تو: {jaghs:,}"]
    if nxt:
        lines.append(f"{ce('me', 2, '⬆️')} شغل بعدی: {html.escape(nxt[0])} — {nxt[1]:,} جق دیگه")
    else:
        lines.append(f"{ce('top', 1, '👑')} همه‌ی شغل‌ها رو داری!")
    return "\n".join(lines)


def jobs_rich_html(gender: str, jaghs: int) -> str:
    """جدول واقعی (Rich Message تلگرام)."""
    rows, nxt = jobs_rows(gender, jaghs)
    out = ["<table><tr><th>شغل</th><th>حقوق</th><th>جق مانده</th></tr>"]
    for glyph, name, pay, left in rows:
        st = "✅ داری" if left == 0 else f"🔒 {left:,}"
        out.append(f"<tr><td>{glyph} {html.escape(name)}</td><td>{pay}</td><td>{st}</td></tr>")
    out.append("</table>")
    out.append("<p>" + jobs_footer(jaghs, nxt).replace("\n", "<br>") + "</p>")
    return "".join(out)


def jobs_text(gender: str, jaghs: int) -> str:
    """نسخه‌ی جایگزین: جدول مونواسپیس داخل <pre> (اگه Rich Message کار نکرد)."""
    rows, nxt = jobs_rows(gender, jaghs)
    cells = [("شغل", "حقوق", "جق مانده")]
    for glyph, name, pay, left in rows:
        cells.append((name, pay, "داری ✅" if left == 0 else f"{left:,}"))
    w = [max(_w(r[c]) for r in cells) for c in range(3)]
    sep = "─" * w[0] + "─┼─" + "─" * w[1] + "─┼─" + "─" * w[2]
    lines = []
    for k, r in enumerate(cells):
        lines.append(" │ ".join(_pad(r[c], w[c]) for c in range(3)))
        if k == 0:
            lines.append(sep)
    table = "<pre>" + html.escape("\n".join(lines)) + "</pre>"
    return f"{ce('me', 1, '🎖')} <b>شغل‌ها</b>\n{table}\n" + jobs_footer(jaghs, nxt)


async def show_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/jobs یا نوشتن «شغل» / «شغل ها» — جدول شغل‌ها."""
    msg, user = update.effective_message, update.effective_user
    await upsert_user(user.id, user.full_name)
    row = await get_user(user.id)
    if not row["gender"]:
        await ask_gender(update, context)
        return
    rp = ReplyParameters(message_id=msg.message_id, allow_sending_without_reply=True)
    # اول جدول واقعی تلگرام (Bot API 10.1)، اگه نشد جدول مونواسپیس
    try:
        await context.bot._post("sendRichMessage", {
            "chat_id": msg.chat_id,
            "rich_message": {"html": jobs_rich_html(row["gender"], row["jaghs"])},
            "reply_parameters": rp.to_dict(),
        })
        return
    except Exception:
        log.warning("sendRichMessage failed, using <pre> table", exc_info=True)
    text = jobs_text(row["gender"], row["jaghs"])
    try:
        await msg.reply_text(text, parse_mode="HTML", reply_parameters=rp)
    except Exception:
        log.exception("jobs message failed, retrying without premium emojis")
        await msg.reply_text(re.sub(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", r"\1", text), parse_mode="HTML")


async def card_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cardcheck — فقط پشتیبان‌ها: نشون میده عکس‌ها و فونت رو سرور پیدا کرده یا نه."""
    if update.effective_user.id not in ADMIN_IDS:
        return
    lines = [f"پوشه‌ی ربات: {BASE_DIR}"]
    lines.append("فونت: ✅ (داخل کد)")
    for g, label in (("m", "آقا"), ("f", "خانوم")):
        have = [str(i) for i in range(1, len(LEVEL_THRESHOLDS) + 1) if card_path(g, i)]
        lines.append(f"عکس {label} ({g}_N.png): {len(have)} از {len(LEVEL_THRESHOLDS)} — سطح‌ها: {', '.join(have) or 'هیچی'}")
    try:
        ok = render_card_image("m", 1, 123) is not None
        lines.append(f"ساخت کارت تستی: {'✅' if ok else '❌'}")
    except Exception as e:
        lines.append(f"ساخت کارت تستی: ❌ {type(e).__name__}: {e}")
    await update.message.reply_text("\n".join(lines))


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
    app.add_handler(CommandHandler("setemoji", set_emoji, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("myid", my_id, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("cardcheck", card_check, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("setphoto", photo_help, filters=filters.ChatType.PRIVATE))
    app.add_handler(
        MessageHandler(
            filters.PHOTO & filters.ChatType.PRIVATE & filters.CaptionRegex(r"^/setphoto"), set_photo
        )
    )
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("me", show_me))
    app.add_handler(CommandHandler("jobs", show_jobs))
    app.add_handler(CommandHandler("gender", ask_gender))
    app.add_handler(CallbackQueryHandler(pick_gender, pattern=r"^g:\d+:[mf]$"))
    app.add_handler(CommandHandler("wallet", wallet))
    app.add_handler(CommandHandler("top", top))
    app.add_handler(
        MessageHandler(filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND, on_group_text)
    )
    app.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, on_private_text)
    )
    log.info("bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

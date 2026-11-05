import html
import logging
import os
import random
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
}

# ایموجی پرمیومِ هر سطح (کنار اسم کاراکتر تو کپشن میاد)
CHAR_EMOJIS = {
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
}


def char_emojis(lvl: int) -> str:
    return "".join(f'<tg-emoji emoji-id="{e}">⭐</tg-emoji>' for e in CHAR_EMOJIS.get(lvl, []))

GENDER_WORDS = {
    "m": "m", "مرد": "m", "آقا": "m", "پسر": "m",
    "f": "f", "زن": "f", "خانوم": "f", "دختر": "f",
}
ME_WORD = "خودم"  # تو گروه این کلمه رو بنویسی کاراکترت نشون داده میشه

# عکس هر کاراکتر (file_id تلگرام). پشتیبان با /setphoto ست می‌کنه و تو دیتابیس می‌مونه.
CHAR_PHOTOS: dict[tuple[str, int], str] = {}


BOT_VERSION = "کاراکترها + بولد"


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
        f"سطح {lvl} از {len(LEVEL_THRESHOLDS)}: {html.escape(title)} {char_emojis(lvl)}",
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


def render_card_image(level: int, balance: int) -> BytesIO | None:
    """عکس آماده‌ی سطح (چاپ‌شده با اسم و لقب) رو برمیدارد و عدد دارایی رو تو کادر پایینش می‌نویسه."""
    path = BASE_DIR / "chars" / f"char_{level}.png"
    if not path.exists():
        return None
    im = Image.open(path).convert("RGB")
    d = ImageDraw.Draw(im)
    font = ImageFont.truetype(str(BASE_DIR / "fonts" / "DejaVuSans-Bold.ttf"), 52)
    d.text((355, 912), f"{balance:,}".translate(_FA_DIGITS), font=font, fill=(255, 255, 255), anchor="mm")
    out = BytesIO()
    im.save(out, "PNG")
    out.seek(0)
    out.name = f"char_{level}.png"
    return out


async def send_card(bot, chat_id: int, row, reply_to: int | None = None):
    """کارت کاراکتر کاربر: عکس (اگه ست شده باشه) + اسم و سطح و موجودی."""
    gender = row["gender"]
    text = card_text(row["name"] or "", gender, row["balance"])
    lvl = level_for(row["balance"])
    fid = CHAR_PHOTOS.get((gender, lvl))  # اگه پشتیبان با /setphoto عکس دلخواه گذاشته باشه همون اولویت داره
    if not fid:
        try:
            img = render_card_image(lvl, row["balance"])
            if img:
                rp0 = ReplyParameters(message_id=reply_to, allow_sending_without_reply=True) if reply_to else None
                await bot.send_photo(chat_id, img, caption=text, parse_mode="HTML", reply_parameters=rp0)
                return
        except Exception as e:
            log.warning("render card failed (%s) — sending text only", e)
    rp = ReplyParameters(message_id=reply_to, allow_sending_without_reply=True) if reply_to else None
    if fid:
        try:
            await bot.send_photo(chat_id, fid, caption=text, parse_mode="HTML", reply_parameters=rp)
            return
        except Exception as e:
            log.warning("send_photo failed (%s) — sending text only", e)
    await bot.send_message(chat_id, text, parse_mode="HTML", reply_parameters=rp)


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
    app.add_handler(CommandHandler("setphoto", photo_help, filters=filters.ChatType.PRIVATE))
    app.add_handler(
        MessageHandler(
            filters.PHOTO & filters.ChatType.PRIVATE & filters.CaptionRegex(r"^/setphoto"), set_photo
        )
    )
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("me", show_me))
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

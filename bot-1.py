import asyncio
import html
import json
import logging
import os
import random
import re
import time


# ───────── حالت عیب‌یابی: تو Railway متغیر DIAG=1 بذار؛ ربات به‌جای اجرای اصلی، گزارش وضعیت رو تو تلگرام می‌فرسته ─────────
if os.environ.get("DIAG") == "1":
    import json as _j
    import urllib.parse as _p
    import urllib.request as _u

    _tok = os.environ.get("BOT_TOKEN", "")
    _admins = [int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()]

    def _clean(x) -> str:
        return str(x).replace(_tok, "***")[:200] if _tok else str(x)[:200]

    def _api(method, **params):
        data = _p.urlencode(params).encode()
        with _u.urlopen(f"https://api.telegram.org/bot{_tok}/{method}", data=data, timeout=60) as r:
            return _j.loads(r.read())

    _rep = ["🔧 گزارش عیب‌یابی",
            f"BOT_TOKEN: {'هست' if _tok else 'نیست ❌'}",
            f"DATABASE_URL: {'هست' if os.environ.get('DATABASE_URL') else 'نیست ❌'}",
            f"ADMIN_IDS: {len(_admins)} نفر"]
    try:
        _me = _api("getMe")
        _rep.append(f"getMe: ok={_me.get('ok')} @{(_me.get('result') or {}).get('username')}")
    except Exception as e:
        _rep.append(f"getMe ❌ {type(e).__name__}: {_clean(e)}")
    for _m in ("telegram", "asyncpg", "PIL"):
        try:
            __import__(_m)
            _rep.append(f"{_m}: ok")
        except Exception as e:
            _rep.append(f"{_m} ❌ {_clean(e)}")
    try:
        import asyncpg as _apg

        async def _dbtest():
            c = await _apg.connect(os.environ["DATABASE_URL"], timeout=15)
            await c.close()

        asyncio.run(_dbtest())
        _rep.append("دیتابیس: ok")
    except Exception as e:
        _rep.append(f"دیتابیس ❌ {type(e).__name__}: {_clean(e)}")
    _text = "\n".join(_rep)
    print(_text, flush=True)
    for _a in _admins:
        try:
            _api("sendMessage", chat_id=_a, text=_text)
        except Exception as e:
            print("send failed", _clean(e), flush=True)
    _off = 0
    while True:  # به هر پیام جواب میده تا بفهمی ربات زنده‌ست
        try:
            for _u_ in _api("getUpdates", offset=_off, timeout=30).get("result", []):
                _off = _u_["update_id"] + 1
                _m = _u_.get("message")
                if _m:
                    _api("sendMessage", chat_id=_m["chat"]["id"], text="pong ✅ (حالت عیب‌یابی)")
        except Exception as e:
            print("poll error", _clean(e), flush=True)
            time.sleep(5)

import asyncpg
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, MessageEntity, ReplyParameters, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ApplicationHandlerStop,
    MessageHandler,
    TypeHandler,
    filters,
)
from telegram.error import BadRequest, Forbidden, RetryAfter


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
    "job":     ("ایموجی‌شغل", ["شغل۱", "شغل۲", "شغل۳", "شغل۴", "شغل۵", "شغل۶", "شغل۷", "شغل۸", "شغل۹", "شغل۱۰"]),
    "lock":    ("قفل", ["قفل بسته", "قفل باز"]),
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
    ("lock", 2): "6034962180875490251",  # قفل باز (شغلی که کاربر داره)
}


# ایموجی‌های پرمیومِ ثابت (⏳ 🏆 🆚 💰 ⚔️ و دکمه‌ی برگشت)
EID_HOURGLASS = "5787432469598835099"
EID_TROPHY = "5413566144986503832"
EID_VS = "6213060666320754449"
EID_MONEY = "6233367447789899509"
EID_SWORDS = "5431421337648256443"
EID_BACK = "5816895683256390576"
HG_CE = f'<tg-emoji emoji-id="{EID_HOURGLASS}">⏳</tg-emoji>'
TROPHY_CE = f'<tg-emoji emoji-id="{EID_TROPHY}">🏆</tg-emoji>'
VS_CE = f'<tg-emoji emoji-id="{EID_VS}">🆚</tg-emoji>'
MONEY_CE = f'<tg-emoji emoji-id="{EID_MONEY}">💰</tg-emoji>'
SWORDS_CE = f'<tg-emoji emoji-id="{EID_SWORDS}">⚔️</tg-emoji>'
EMOJI_IDS[("jagh", 6)] = EID_HOURGLASS
EMOJI_IDS[("jagh", 4)] = EID_TROPHY
EMOJI_IDS[("wallet", 1)] = EID_MONEY


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
# (فقط تعداد جق‌ها حساب میشه، دارایی/پول هیچ تاثیری نداره)
LEVEL_THRESHOLDS = [0, 100, 250, 450, 700, 1000, 1400, 1800, 2150, 2500]

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


BOT_VERSION = "کازینو پنلی + اسلات + مین"


def norm_word(text: str) -> str:
    """نرمال‌سازی برای تشخیص «خودم»: ی/ک عربی، نیم‌فاصله، فاصله و علامت‌های آخر جمله."""
    text = text.replace("ي", "ی").replace("ك", "ک")
    text = "".join(ch for ch in text if ch not in "\u200c\u200d\u200e\u200f\u200b\ufeff")
    return text.strip().strip(".!؟?،, ")


def level_for(jaghs: int) -> int:
    lvl = 1
    for i, need in enumerate(LEVEL_THRESHOLDS, 1):
        if jaghs >= need:
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
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_shift DOUBLE PRECISION NOT NULL DEFAULT 0")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS shift_seen TEXT NOT NULL DEFAULT ''")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_duel DOUBLE PRECISION NOT NULL DEFAULT 0")
        await c.execute("CREATE TABLE IF NOT EXISTS chats (chat_id BIGINT PRIMARY KEY, title TEXT)")
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
        await c.execute(
            """CREATE TABLE IF NOT EXISTS bot_settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )"""
        )
        if not await c.fetchval("SELECT 1 FROM bot_settings WHERE key = 'emoji_migr_2026_11'"):
            for (_sec, _slot), _eid in (
                (("jagh", 6), EID_HOURGLASS), (("jagh", 4), EID_TROPHY), (("wallet", 1), EID_MONEY),
            ):
                await c.execute(
                    "INSERT INTO emoji_slots (section, slot, emoji_id) VALUES ($1, $2, $3) "
                    "ON CONFLICT (section, slot) DO UPDATE SET emoji_id = EXCLUDED.emoji_id",
                    _sec, _slot, _eid,
                )
                EMOJI_IDS[(_sec, _slot)] = _eid
            await c.execute("INSERT INTO bot_settings (key, value) VALUES ('emoji_migr_2026_11', '1') ON CONFLICT (key) DO NOTHING")
        global BOT_ENABLED
        _be = await c.fetchval("SELECT value FROM bot_settings WHERE key = 'bot_enabled'")
        BOT_ENABLED = _be != "0"
        for r in await c.fetch("SELECT chat_id FROM chats"):
            KNOWN_CHATS.add(r["chat_id"])
        for r in await c.fetch("SELECT key, value FROM bot_settings WHERE key IN ('jobs_custom_m', 'jobs_custom_f')"):
            try:
                CUSTOM_JOBS[r["key"][-1]] = json.loads(r["value"])
            except Exception:
                log.exception("bad %s in db", r["key"])
    log.info("database ready")


async def startup(app: Application):
    """موقع روشن شدن: دیتابیس رو آماده می‌کنه و به پشتیبان‌ها تو تلگرام خبر میده (بدون نیاز به لاگ Railway)."""
    try:
        await init_db(app)
        note = f"✅ ربات روشن شد.\nنسخه: {BOT_VERSION}"
    except Exception as e:
        log.exception("startup failed")
        note = f"❌ ربات روشن شد ولی دیتابیس وصل نشد:\n{type(e).__name__}: {str(e)[:200]}"
    for aid in ADMIN_IDS:
        try:
            await app.bot.send_message(aid, note)
        except Exception:
            log.warning("could not notify admin %s", aid)


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


def btn(text: str, style: str | None = None, icon: str | None = None, **kw) -> InlineKeyboardButton:
    """دکمه‌ی رنگی: style = primary (آبی) | success (سبز) | danger (قرمز)؛ icon = آیدی ایموجی پرمیوم کنار دکمه."""
    extra = {}
    if style:
        extra["style"] = style
    if icon:
        extra["icon_custom_emoji_id"] = icon
    if extra:
        try:
            return InlineKeyboardButton(text, api_kwargs=extra, **kw)
        except TypeError:
            pass
    return InlineKeyboardButton(text, **kw)


def join_keyboard(missing: list[str]) -> InlineKeyboardMarkup:
    rows = [
        [btn(f"📢 عضویت در {ch}", "primary", url=f"https://t.me/{ch.lstrip('@')}")]
        for ch in missing
    ]
    rows.append([btn("✅ عضو شدم", "success", callback_data="check_join")])
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


WALLET_WORDS = {"دارایی", "داراییم", "داراییمن", "موجودی", "موجودیم", "کیفپول"}
TOP_WORDS = {"لیدربرد", "برترین", "برترینها", "لیدربردبرترینها", "لیدربردبرترین", "top", "leaderboard"}
TOP_NAME_EMOJI = "5970047654321721218"


def _word_key(text: str) -> str:
    return norm_word(text).replace(" ", "").lower()


async def wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/wallet یا نوشتن «دارایی» — دارایی کل کاربر."""
    user = update.effective_user
    await upsert_user(user.id, user.full_name)
    row = await get_user(user.id)
    bal = row["balance"] if row else 0
    await update.effective_message.reply_text(
        B(f"{ce('wallet', 1, '💰')} دارایی کل شما: {bal:,} تومن"), parse_mode="HTML",
        reply_parameters=ReplyParameters(message_id=update.effective_message.message_id, allow_sending_without_reply=True),
    )


def _top_lines(rows, col: str, unit: str) -> str:
    out = []
    for i, r in enumerate(rows, 1):
        name = html.escape((r["name"] or "کاربر")[:30])
        mark = ce("top", 1, "👑") if i == 1 else f"{i}."
        out.append(
            f'{mark} <tg-emoji emoji-id="{TOP_NAME_EMOJI}">⭐</tg-emoji> '
            f'<a href="tg://user?id={r["user_id"]}">{name}</a> — {r[col]:,} {unit}'
        )
    return "\n\n".join(out)  # بین هر نفر یه خط فاصله


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/top یا «لیدربرد» / «برترین» / «برترین ها» — ۱۰ نفر اول جق و دارایی."""
    by_j = await pool.fetch("SELECT user_id, name, jaghs FROM users WHERE jaghs > 0 ORDER BY jaghs DESC LIMIT 10")
    by_b = await pool.fetch("SELECT user_id, name, balance FROM users WHERE balance > 0 ORDER BY balance DESC LIMIT 10")
    if not by_j and not by_b:
        await update.effective_message.reply_text(BP("هنوز کسی تو لیست نیست!"), parse_mode="HTML")
        return
    text = (
        f"{ce('top', 1, '👑')} <b>برترین‌ها</b>\n\n"
        f"{ce('jagh', 1, '🤛')} <b>بیشترین جق</b>\n\n{_top_lines(by_j, 'jaghs', 'جق')}\n\n"
        f"〰️〰️〰️\n\n"
        f"{ce('wallet', 1, '💰')} <b>بیشترین دارایی</b>\n\n{_top_lines(by_b, 'balance', 'تومن')}"
    )
    rp = ReplyParameters(message_id=update.effective_message.message_id, allow_sending_without_reply=True)
    try:
        await update.effective_message.reply_text(text, parse_mode="HTML", reply_parameters=rp)
    except Exception:
        log.exception("top failed, retrying without premium emojis")
        await update.effective_message.reply_text(re.sub(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", r"\1", text), parse_mode="HTML", reply_parameters=rp)


# ───────────── انتقال پول (ریپلای + «انتقال پول 100 هزار») ─────────────
_NUM_WORDS = {
    "یک": 1, "دو": 2, "سه": 3, "چهار": 4, "پنج": 5, "شش": 6, "هفت": 7, "هشت": 8, "نه": 9, "ده": 10,
    "یازده": 11, "دوازده": 12, "سیزده": 13, "چهارده": 14, "پانزده": 15, "شانزده": 16, "هفده": 17, "هجده": 18, "نوزده": 19,
    "بیست": 20, "سی": 30, "چهل": 40, "پنجاه": 50, "شصت": 60, "هفتاد": 70, "هشتاد": 80, "نود": 90,
    "صد": 100, "یکصد": 100, "دویست": 200, "سیصد": 300, "چهارصد": 400, "پانصد": 500, "ششصد": 600, "هفتصد": 700, "هشتصد": 800, "نهصد": 900,
}
_UNITS = {"هزار": 1_000, "k": 1_000, "کا": 1_000, "میلیون": 1_000_000, "m": 1_000_000, "میلیارد": 1_000_000_000, "b": 1_000_000_000}
_DIG = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩٫٬،", "01234567890123456789.,,")


def parse_amount(text: str) -> int:
    """«100هزار»، «۲.۵ میلیون»، «صد هزار»، «یک میلیون و پانصد هزار»، «50k» → تومن (۰ = نامعتبر)."""
    t = text.translate(_DIG).replace(",", "").lower()
    toks = re.findall(r"\d+(?:\.\d+)?|[^\W\d_]+", t)
    total, cur, seen = 0.0, 0.0, False
    for tok in toks:
        if re.fullmatch(r"\d+(?:\.\d+)?", tok):
            cur += float(tok); seen = True
        elif tok in _NUM_WORDS:
            cur += _NUM_WORDS[tok]; seen = True
        elif tok in _UNITS:
            total += (cur or 1) * _UNITS[tok]; cur = 0.0; seen = True
    total += cur
    return int(round(total)) if seen else 0


async def on_transfer_request(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str):
    msg, user = update.effective_message, update.effective_user
    rest = re.sub(r"^انتقال(\s*پول)?", "", norm_word(text)).strip()
    target = msg.reply_to_message.from_user if msg.reply_to_message else None
    if target is None:
        await msg.reply_text(BP("برای انتقال پول، روی پیام اون کاربر ریپلای کن و بنویس: انتقال پول 100 هزار"), parse_mode="HTML")
        return
    if target.is_bot or target.id == user.id:
        await msg.reply_text(BP("نمی‌تونی به خودت یا ربات پول بفرستی 😅"), parse_mode="HTML")
        return
    amount = parse_amount(rest)
    if amount <= 0:
        await msg.reply_text(BP("مبلغ رو نفهمیدم. مثلا: انتقال پول 100 هزار"), parse_mode="HTML")
        return
    await upsert_user(user.id, user.full_name)
    row = await get_user(user.id)
    if row["balance"] < amount:
        await msg.reply_text(B(f"{ce('wallet', 1, '💰')} دارایی‌ت کافی نیست! موجودی: {row['balance']:,} تومن"), parse_mode="HTML")
        return
    kb = InlineKeyboardMarkup([[
        btn("✅ تایید", "success", callback_data=f"tr:ok:{user.id}:{target.id}:{amount}"),
        btn("❌ لغو", "danger", callback_data=f"tr:no:{user.id}:{target.id}:{amount}"),
    ]])
    await msg.reply_text(
        f'<b>{html.escape(user.full_name)}، مطمئنی می‌خوای <u>{amount:,} تومن</u> به '
        f'<a href="tg://user?id={target.id}">{html.escape(target.full_name)}</a> انتقال بدی؟</b>',
        parse_mode="HTML", reply_markup=kb,
        reply_parameters=ReplyParameters(message_id=msg.message_id, allow_sending_without_reply=True),
    )


async def transfer_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, act, frm, to, amt = q.data.split(":")
    frm, to, amt = int(frm), int(to), int(amt)
    if q.from_user.id != frm:
        await q.answer("این تایید مال تو نیست 😅", show_alert=True)
        return
    key = (q.message.chat_id, q.message.message_id)
    done = context.bot_data.setdefault("tr_done", set())
    if key in done:  # جلوگیری از دوبار زدن
        await q.answer("قبلاً انجام شده.", show_alert=True)
        return
    done.add(key)
    if act == "no":
        await q.answer("لغو شد")
        await q.edit_message_text("❌ انتقال لغو شد.")
        return
    try:
        age = time.time() - q.message.date.timestamp()
    except Exception:
        age = 0
    if age > 300:
        await q.answer("زمان تایید تموم شده، دوباره درخواست بده.", show_alert=True)
        await q.edit_message_text("⌛ زمان تایید تموم شد.")
        return
    try:
        async with pool.acquire() as c:
            async with c.transaction():
                ok = await c.fetchval(
                    "UPDATE users SET balance = balance - $1::bigint WHERE user_id = $2::bigint AND balance >= $1::bigint RETURNING 1",
                    amt, frm,
                )
                if not ok:
                    raise ValueError("insufficient")
                await c.execute("INSERT INTO users (user_id, name) VALUES ($1, '') ON CONFLICT (user_id) DO NOTHING", to)
                await c.execute("UPDATE users SET balance = balance + $1::bigint WHERE user_id = $2::bigint", amt, to)
    except ValueError:
        await q.answer("دارایی‌ت کافی نیست!", show_alert=True)
        await q.edit_message_text("❌ دارایی کافی نبود؛ انتقال انجام نشد.")
        return
    except Exception:
        done.discard(key)
        log.exception("transfer failed")
        await q.answer("خطا، دوباره امتحان کن.", show_alert=True)
        return
    await q.answer("انجام شد ✅")
    tname = await pool.fetchval("SELECT name FROM users WHERE user_id = $1", to) or "کاربر"
    await q.edit_message_text(
        f'✅ <b>{amt:,} تومن</b> از <a href="tg://user?id={frm}">{html.escape(q.from_user.full_name)}</a> '
        f'به <a href="tg://user?id={to}">{html.escape(tname)}</a> منتقل شد.',
        parse_mode="HTML",
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
    if norm_word(text).replace(" ", "").lower() in SHIFT_WORDS:
        await show_shift(update, context)
        return
    if _word_key(text) in TOP_WORDS:
        await top(update, context)
        return
    if _word_key(text) in WALLET_WORDS:
        await wallet(update, context)
        return
    if norm_word(text).startswith("انتقال"):
        await on_transfer_request(update, context, text)
        return
    if _word_key(text) in ("دوئلجقی", "دوئلجق"):
        await duel_request(update, context)
        return
    if await casino_text(update, context, text):
        return
    if text != TRIGGER_WORD:
        return

    await upsert_user(user.id, user.full_name)
    row = await get_user(user.id)
    bot_username = (await context.bot.get_me()).username

    # باید قبلاً /start زده و عضو کانال‌ها شده باشه
    if not row["started"]:
        kb = InlineKeyboardMarkup(
            [[btn("▶️ استارت ربات", "success", url=f"https://t.me/{bot_username}?start=go")]]
        )
        await msg.reply_text(BP("اول ربات رو استارت کن و توی کانال‌ها عضو شو 👇"), parse_mode="HTML", reply_markup=kb)
        return

    # هر بار عضویت دوباره چک میشه (اگه لفت داده باشه دیگه پول نمی‌گیره)
    missing = await missing_channels(context.bot, user.id)
    if missing:
        kb = InlineKeyboardMarkup(
            [[btn("📢 عضویت در کانال‌ها", "primary", url=f"https://t.me/{bot_username}?start=go")]]
        )
        await msg.reply_text(BP("از کانال‌ها خارج شدی! دوباره عضو شو 👇"), parse_mode="HTML", reply_markup=kb)
        return

    now = time.time()
    amount = random.randint(MIN_REWARD, MAX_REWARD)

    # آپدیت اتمیک: فقط اگه ۵ دقیقه گذشته باشه پول اضافه میشه
    updated = await pool.fetchrow(
        "UPDATE users SET balance = balance + $1::bigint, jaghs = jaghs + 1, last_ts = $2::double precision "
        "WHERE user_id = $3::bigint AND last_ts <= $2::double precision - $4::double precision "
        "RETURNING balance, jaghs",
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
    new_jaghs = updated["jaghs"]
    if gender in CHARACTERS and level_for(new_jaghs) > level_for(new_jaghs - 1):
        title = html.escape(CHARACTERS[gender][level_for(new_jaghs) - 1][0])
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
def card_text(name: str, gender: str, balance: int, jaghs: int) -> str:
    # سطح/تصویر کاراکتر با سطحی که برای شغل فعلی در جدول JOBS تعریف شده هماهنگ است.
    job = current_job(gender, jaghs) if gender in JOBS else None
    lvl = JOBS[gender][job[0]][1] if job else level_for(jaghs)
    title, tagline = CHARACTERS[gender][lvl - 1]
    lines = [
        f"{ce('me', 1, '🎖')} {html.escape(name)}",
        "",
        f"سطح {lvl} از {len(LEVEL_THRESHOLDS)}: {html.escape(title)} {char_emojis(gender, lvl)}",
        html.escape(tagline),
        "",
        f"{ce('jagh', 1, '🤛')} جق‌ها: {jaghs:,}",
        f"{ce('wallet', 1, '💰')} دارایی: {balance:,} تومن",
    ]
    if lvl < len(LEVEL_THRESHOLDS):
        need = LEVEL_THRESHOLDS[lvl] - jaghs
        nxt = html.escape(CHARACTERS[gender][lvl][0])
        lines.append(f"{ce('me', 2, '⬆️')} {need:,} جق دیگه تا «{nxt}»")
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
AAEAAAARAQAABAAQR0RFRgAvACUAABrgAAAAHEdQT1NEdkx1AAAa/AAAACBHU1VCJ6Q/wwAAGxwAAACWT1MvMosjeGsAAAGYAAAA
VmNtYXAGZgfdAAACTAAAAFRjdnQgPrkxCAAACvQAAAJUZnBnbVsCa/AAAAKgAAAArGdhc3AABwAHAAAa1AAAAAxnbHlmbSxCbQAA
DYgAAAssaGVhZChqTDwAAAEcAAAANmhoZWEOrweAAAABVAAAACRobXR4TkkK/wAAAfAAAABcbG9jYTI2NO8AAA1IAAAAQG1heHAG
ZAYtAAABeAAAACBuYW1lLAxBcgAAGLQAAAH+cG9zdP/bAFoAABq0AAAAIHByZXB8YaLnAAADTAAAB6cAAQAAAAJeuFfeSg5fDzz1
AB8IAAAAAADg+tE5AAAAAOD60Tn3cvyuD80JZwABAAgAAgAAAAAAAAABAAAHbf4dAAAQIfdy+TIPzQABAAAAAAAAAAAAAAAAAAAA
DwABAAAAHwNOACsAeAAMAAIAEABAAAgAAAXtAiEACAAEAAEElQK8AAUAAAUzBZkAAAEeBTMFmQAAA9cAZgISAAACCwgDAwYEAgIE
AAAgAQAAAAAAAAAAAAAAAFBmRWQAIAAgBvkGFP4UAZoHbQHjAAAAAQAAAAAAAATNAGYCyQAAAwoAbQWRAGIFkQDnBZEAogWRAIkF
kQBcBZEAngWRAH8FkQCJBZEAfQWRAGoDCgDIBOIBvwEeABkAGQAeAB4APwG/AR4AGQAZABkAPgCvAB4AHgA/AAAAAgAAAAMAAAAU
AAMAAQAAABQABABAAAAADAAIAAIABAAgACwAOQYMBvn//wAAACAALAAwBgwG8P///+H/1v/T+gH5JQABAAAAAAAAAAAAAAAAtwcG
BQQDAgEALCAQsAIlSWSwQFFYIMhZIS0ssAIlSWSwQFFYIMhZIS0sIBAHILAAULANeSC4//9QWAQbBVmwBRywAyUIsAQlI+EgsABQ
sA15ILj//1BYBBsFWbAFHLADJQjhLSxLUFgguAEoRURZIS0ssAIlRWBELSxLU1iwAiWwAiVFRFkhIS0sRUQtLLACJbACJUmwBSWw
BSVJYLAgY2ggihCKIzqKEGU6LUGEAoABJgD+AAMBJQARAAMBJAEhADoABQEkAPoAAwEjABYAAwEiASEAOgAFASIA/gADASEAOgAD
ASAA+gADAR8AuwADAR4AZAADAR0A/gADARwAGQADARsAHgADARoA/gADARkA/gADARgA/gADARcA/gADARYA/gADARUBFAAOAAUB
FQD+AAMBFAAOAAMBEwD+AAMBEgD+AAMBDwEOAH0ABQEPAP4AAwEOAH0AAwENAQwAjAAFAQ0A/gADAQ0AwAAEAQwBCwBZAAUBDACM
AAMBDACAAAQBCwEKACYABQELAFkAAwELAEAABAEKACYAAwEJAP4AAwEIAP4AAwEHAAwAAwEHAIAABAEGspcuBUETAQYA+gADAQUA
+gADAQQA/gADAQMAGQADAQIA+gADAQEA+gADAQBA/30D/z4D/v4D/PssBfz+A/ssA/r+A/n4RwX5fQP4RwP3+gP2/gP1/gP0/gPz
uwPy/gPx/gPw/gPvHgPu/gPt7AoF7f4D7AoD7EAE6+oKBesyA+oKA+n6A+iRFgXo/gPn+gPm+gPlkRYF5f4D5P4D4/4D4v4D4f4D
4P4D3/4D3voD3dwYBd1kA9wYA9ugHgXbZAPa2SUF2voD2SUD2NElBdj6A9fWFAXXFgPW1RAF1hQD1RAD1NMLBdQgA9MLA9LRJQXS
+gPRkRYF0SUD0JQMBdAjA8/OFAXPJgPOzRIFzhQDzRIDzJEWBcwdA8sUA8rJuwXK/gPJyF0FybsDyYAEyED/xyUFyF0DyEAExyUD
xv4DxWQDxJAQBcT+A8McA8L+A8H+A8C/OgXA+gO/rRsFvzoDvr0aBb4yA728EQW9GgO8uw8FvBEDu7oMBbsPA7oMA7mRFgW5/gO4
/gO3FQO2EgO1/gO0/gOz/gOyFwOxGQOwFgOvrRsFr/oDrq0bBa76A62RFgWtGwOskRYFrH0Dq/4DqiYDqf4DqP4Dp/4Dpv4DpQoD
pP4Do6IOBaP+A6IOA6JABKGgHgWh+gOgkRYFoB4Dn5EWBZ/6A56UDAWeHAOd/gOcm7sFnP4Dm5pdBZu7A5uABJqPJQWaXQOaQASZ
/gOYly4FmP4Dly4DlpEWBZYeQP8DlZQMBZUgA5QMA5ORFgWTSwOSkRYFkv4DkZAQBZEWA5AQA48lA47+A43+A4z+A4v+A4r+A4n+
A4iHJQWI/gOHJQOG/gOF/gOEMgODlgOC/gOB/gOAGQN/CgN+/gN9/gN8/gN7+gN6+gN5/gN3dqYFd/4DdqYDdXQbBXX6A3QbA3P6
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
AGL/4wUvBfAACwAXACNAEwmsDwOsFZwPmBgAFgwXBhYSFBgQ/Oz87DEAEOT07BDuMAEQJiMiBhEQFjMyNgEQACEgABEQACEgAAOu
aXx8amp8e2oBgf7A/tr+2f7AAUABJwEmAUAC7AEY5eX+6P7l6OgBGP6N/m0BkwFzAXQBk/5tAAABAOcAAAUEBdUACgAoQBUDrgQC
rgWNBwCuCQgYBhoDABgFAQsQ1MTsxPzsMQAv7DL07NTsMBMhEQURJSERIREh8AFU/qMBWwFuAVT77AEKA8VIAQZI+zX+9gABAKIA
AATfBfAAGACLQCkAHQQFBBcBFhgdBQUEJQUYAA6QDwusEpwEAK8CGBUFAA4IFhUBGw4DGRDcS7ANVFi5AAP/wDhZxPzU7BE5ORE5
MQAv7DL07NTsETk5MEtTWAcQDu0RFzkHEAXtWSIBQCYCFyoWKhcDAwAOFwUYFxcXGCIAIhciGDUANRc1GEIASgVGF0YYD10AXQEh
ESERAT4BNTQmIyIGBxE+ATMgBBUUBgcCTgKR+8MCIUlGjXVa1nqC/noBDAEpfsoBG/7lARsB4UJ+RGmATUwBSCst7NN607EAAQCJ
/+ME7gXwACgATEArABWsEwmWCrENrAYglh+xHKwTsCOcBpgpFhMZFAAQGRYmEBYDHxQfIAkeKRD85MT87NTsEjkREjk5MQAQ5PTk
/PTsEP717hDuOTABHgEVFAQhIiYnER4BMzI2NTQmKwE1MzI2NTQmIyIGBxE+ATMgBBUUBgO6l53+rP66c+dxbNVnmaOno5qikY6K
fl2+XnLgbAEjASGKAyUnwZXe5yUlASk2N2pjZmn4W11WXiopARogIL/Ag6cAAgBcAAAFMwXVAAIADQBDQCABIQ0DDQAhAwMNJQAD
CweuBQEDjQkBDAoAGgYIBAwUDhD81DzE7DIROTEAL+TUPOwyEjkwS1NYBxAE7QcQBe1ZIgkBIQMhETMRIxEhESERAvL+WgGmQAGs
1dX+lP1qBJj9jwOu/FL+6f7wARABSgABAJ7/4wUCBdUAHQA9QCIEBx2VGqwHEJYRlRSsB7INAq8AjQ2YHgMiAAEXFgofABAeENzE
/OzEEO4xABDk9OwQ5v717hD+5BI5MBMhESEVPgEzIAAVFAAhIiYnER4BMzI2NTQmIyIGB9kDvf12LFkwAREBMP61/tp/+Xt622GM
oaGMU7xsBdX+5ecMDf7v9PL+7jEyAS9GRol1dogrLQAAAgB//+MFIwXuAAsAJAA3QB8TAKwWBqwcDJYNlRCsIpwcmCUMCRoZAyUT
GhkXHyQlEPzs/OQQ7sQxABDk9Pz07BDu1u45MAEiBhUUFjMyNjU0JgERLgEjIgYHPgEzMgAVFAAhIAAREAAhMhYC5WVlZWVmZWUB
dl+oUKzAEEKaW+UBGf7G/vj+3f7BAXUBRWfCAuGDg4ODg4ODgwLN/uwtK7+8MTH+9Nnw/t8BiQFpAXIBpyAAAQCJAAAE7gXVAAYA
RUAXBRkCAwIEGQMDAiUFrwCNAwUEAwMBAAcQ3MwXOTEAL/TsMEtTWAcQBe0HEAXtWSKyBwMBAV1ACwcDGgUmAzUDRgMFXRMhFQEh
ASGJBGX9uv6JAif9MQXV2fsEBLoAAwB9/+MFEgXwAAsAIwAvAEdAKBgMJ6wABqweALAtrBKcHpgwGBUJDAMkGg8qGhUmCRobJwMa
DyYhJDAQ/OTs/Oz07BDuEjkREjkxABDk9OzkEO4Q7jk5MAEiBhUUFjMyNjU0JiUuATU0JCEgBBUUBgceARUUBCEgJDU0NhMUFjMy
NjU0JiMiBgLJbHR0bGtycv58iIoBGgERAQ8BGouImJv+2f7e/t3+15vyY1xaYmJaXGMCnHZubnV1bm91fymqf73Gxb5/qikqvZDe
4+PekL0BVVlgYFlZX2AAAAIAav/jBQ4F7gAYACQAN0AfBxmsCgCWAZUErBYKH6wQnBaYJRwlBxoTFwAiGg0kJRD87MT8/OQxABDk
9OzEEP717hDuOTA3ER4BMzI2Nw4BIyIANTQAISAAERAAISImATI2NTQmIyIGFRQWzVyoUqzAEUSaWuX+5wE5AQcBJAFA/or+umnA
AX9lZmZlZWZmIQEUKyu/vDIyAQva8QEi/nb+mP6O/lkfAu6Dg4KEhIKDgwABAMgAAAKUAqYABQAAKQEREzMDAjD+mPfVZAExAXX+
iwAAAQG/AZADIwLuAAMAAAEhESEBvwFk/pwC7v6iAAEBHgAAA3IFFAALAAAhMCEQJwIDMCESExYDcv6eJTGcAXJ1TSABqdwBIQFu
/uj+iZsAAQAZAAAEyQUUABsAAAEWFxYRMCEQJwIDMCEWFxYXFjc2NzAhBgcCISICIxIYIP6eJTGcAXUtESuxayshBgFkDhhr/tWm
A0xPeKf+IgGp3AEhAW5iLXACAW5VP6g+/vAAAQAZAAAExwUUACgAAAEWFxYRMCEQJwIDMCEWFxYzMjc2NTAhFBcWMzI3NjUwIRAH
BiMiJwYiAiYYDyD+niUxnAFyMyMTKCcCAQEACAsuIxANAQAnWZWhMzFsAzVXWb7+OQGp3AEhAW5SZTZMMm+kHCcqI5r+yjZ5Q0QA
AAEAHgAABMQFFAAVAAABNjcSNzAhBgMGAzAhAicCJzAhFhMWAnEZMl05AXJ/lFI7/po7UpR/AXI5XTICiHqWARdl9v46+v6iAV76
Acb2Zf7plgABAB4AAATEBRQAFQAAAQYHAgcwITYTNhMwIRIXEhcwISYDJgJxGTJdOf6Of5RSOwFmO1KUf/6OOV0yAox6lv7pZfYB
xvoBXv6i+v469mUBF5YAAgA/AAAEuQUgABUAJQAAASInJicmJyY3NhcEFxYXFhcSEzAhAgMwJyYnJgciBwYHBhcWFxYC3M0+0Ftj
AgJhlLgBSVuBDQoSJVr+jl0iCAMhNVUpJSABAR8VJk8CJRI8Vl6KnVN+AQFUdqeHlv7N/qQBnAGJQTI0UwEoIy0nJhsHEAD//wG/
AZADIwLuEAYADgAA//8BHgAAA3IFFBAGAA8AAP//ABkAAATJBRQQBgAQAAD//wAZAAAExwUUEAYAEQAAAAEAGQAABJUFMwArAAAB
JjU0NzYzMhcwFSYHBgcGFRQXFjc2NzARBiMiJxYXFhEwIRAnAgMwIRYXFgIpBGxcwVRYWkZZGB8ZJDGUacNTuJILDSD+niUxnAF1
ICUYBCcPE2ZHPRnIIwEBGiILJhYgAQIR/wAaUU9Hsv4tAancASEBbkhYOAAAAgA+//AEpAUkABQAJwAAARYzMjc2NTQnJiIHBhUU
FxYzMjcwAiATFhEUBwYjIicGIyInJjUQNwLoCCoVCwY+PqY+PgYLFSoIqgJCs181Q/uWKiqW+0M1XwF8fFI2Su+ysbGy70o2UnwD
qP6KxP7asYCjZWWjgLEBJsQAAAEArwAABB0FKwAjAAAzNjc2NyInJjU0NzY3NhcwESYjIgcGFRQXFjc2NzARBgcGBzCvIo5Gh2o3
hmKCytNbIKpBLUI3NF9jifdajS7M6HJLLG2XuFh2AgIh/wAfGCNBTTMxCAgx/wBEdrfM//8AHgAABMQFFBAGABIAAP//AB4AAATE
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
    job = current_job(gender, row["jaghs"]) if row["gender"] else None
    lvl = JOBS[gender][job[0]][1] if job else level_for(row["jaghs"])
    text = card_text(row["name"] or "", gender, row["balance"], row["jaghs"])
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
            btn("🧔 آقا", "primary", callback_data=f"g:{uid}:m"),
            btn("👩 خانوم", "success", callback_data=f"g:{uid}:f"),
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
    if await admin_input(update, context):
        return
    if msg and msg.text and context.user_data.get("await_jobs") and update.effective_user.id in ADMIN_IDS:
        if msg.text.strip() == "لغو":
            context.user_data.pop("await_jobs", None)
            await msg.reply_text("لغو شد.")
        else:
            await save_custom_jobs(update, context, context.user_data["await_jobs"])
        return
    if msg and msg.text and norm_word(msg.text) == ME_WORD:
        await show_me(update, context)
    elif msg and msg.text and norm_word(msg.text).replace(" ", "").lower() in JOBS_WORDS:
        await show_jobs(update, context)
    elif msg and msg.text and norm_word(msg.text).replace(" ", "").lower() in SHIFT_WORDS:
        await show_shift(update, context)
    elif msg and msg.text and _word_key(msg.text) in TOP_WORDS:
        await top(update, context)
    elif msg and msg.text and _word_key(msg.text) in WALLET_WORDS:
        await wallet(update, context)
    elif msg and msg.text:
        await casino_text(update, context, msg.text)


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
    ("30K", "50K"), ("80K", "100K"), ("150K", "250K"), ("400K", "600K"), ("2M", "3700K"),
    ("3700K", "4300K"), ("4300K", "6M"), ("6M", "7100K"), ("7100K", "8500K"), ("8500K", "10M"),
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


# جدول‌هایی که پشتیبان‌ها با /setjobs ساختن، جدا برای هر جنسیت: {"m": {"text":..., "entities":[...]}, "f": {...}}
CUSTOM_JOBS: dict = {}
GENDER_FA = {"m": "مردها 👨", "f": "زن‌ها 👩"}

SETJOBS_HELP = (
    "حالا جدول شغل‌های {who} رو بساز و بفرست؛ همون پیام (با ایموجی پرمیوم، بولد، کادر مونواسپیس و ...) "
    "عیناً به کاربرهای {who} نشون داده میشه و زیرش «جق‌های تو» به‌صورت شخصی اضافه میشه.\n\n"
    "نکته: برای اینکه ستون‌ها مرتب بیفتن، جدول رو تو بلوک مونواسپیس (کد) بنویس.\n"
    "لغو: بنویس «لغو»"
)


async def set_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setjobs — فقط پشتیبان‌ها، تو پی‌وی: اول انتخاب مرد/زن، بعد پیام بعدی‌ات میشه جدولش."""
    if update.effective_user.id not in ADMIN_IDS:
        return
    context.user_data.pop("await_jobs", None)
    kb = InlineKeyboardMarkup([[
        btn("👨 جدول مردها", "primary", callback_data="sj:m"),
        btn("👩 جدول زن‌ها", "success", callback_data="sj:f"),
    ]])
    await update.effective_message.reply_text("جدول شغل‌های کدوم جنسیت رو می‌خوای عوض کنی؟", reply_markup=kb)


async def set_jobs_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer("فقط پشتیبان‌ها", show_alert=True)
        return
    g = q.data.split(":")[1]
    context.user_data["await_jobs"] = g
    await q.answer()
    await q.edit_message_text(SETJOBS_HELP.format(who=GENDER_FA[g]))


async def jobs_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/jobsreset — برگشت به جدول خودکار (مرد، زن یا هر دو)."""
    if update.effective_user.id not in ADMIN_IDS:
        return
    context.user_data.pop("await_jobs", None)
    kb = InlineKeyboardMarkup([[
        btn("👨 مردها", "primary", callback_data="sr:m"),
        btn("👩 زن‌ها", "success", callback_data="sr:f"),
        btn("هر دو", "danger", callback_data="sr:a"),
    ]])
    await update.effective_message.reply_text("جدول سفارشی کدوم رو پاک کنم؟", reply_markup=kb)


async def jobs_reset_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer("فقط پشتیبان‌ها", show_alert=True)
        return
    g = q.data.split(":")[1]
    gs = ["m", "f"] if g == "a" else [g]
    for x in gs:
        CUSTOM_JOBS.pop(x, None)
        await pool.execute("DELETE FROM bot_settings WHERE key = $1", f"jobs_custom_{x}")
    await q.answer()
    await q.edit_message_text("✅ جدول سفارشی پاک شد؛ دوباره جدول خودکار نمایش داده میشه.")


def _rich_of(msg):
    """اگه پیام جدول واقعی تلگرام (Rich Message) باشه، بدنه‌ش رو از داده‌ی خام برمی‌داره."""
    try:
        raw = msg.to_dict()
    except Exception:
        raw = {}
    for src in (raw, getattr(msg, "api_kwargs", None) or {}):
        for k, v in src.items():
            if "rich" in k.lower() and v:
                return v
    return None


async def send_custom_jobs(bot, data: dict, chat_id: int, rp=None):
    """جدول ذخیره‌شده رو می‌فرسته؛ چند روش پشت سر هم: کپی ← فوروارد ← Rich ← متن."""
    errs = []
    if data.get("kind") == "copy":
        try:
            kw = {"reply_parameters": rp} if rp else {}
            await bot.copy_message(chat_id=chat_id, from_chat_id=data["chat_id"], message_id=data["message_id"], **kw)
            return
        except Exception as e:
            errs.append(f"copy:{type(e).__name__}")
            log.warning("jobs copy failed: %s", e)
        try:
            await bot.forward_message(chat_id=chat_id, from_chat_id=data["chat_id"], message_id=data["message_id"])
            return
        except Exception as e:
            errs.append(f"forward:{type(e).__name__}")
            log.warning("jobs forward failed: %s", e)
        if data.get("rich"):
            try:
                payload = {"chat_id": chat_id, "rich_message": data["rich"]}
                if rp:
                    payload["reply_parameters"] = rp.to_dict()
                await bot._post("sendRichMessage", payload)
                return
            except Exception as e:
                errs.append(f"rich:{type(e).__name__}")
                log.warning("jobs rich failed: %s", e)
    if data.get("text"):
        ents = [MessageEntity.de_json(d, bot) for d in data.get("entities", [])]
        kw = {"reply_parameters": rp} if rp else {}
        await bot.send_message(chat_id=chat_id, text=data["text"], entities=ents, **kw)
        return
    raise RuntimeError(" | ".join(errs) or "no data")


async def save_custom_jobs(update: Update, context: ContextTypes.DEFAULT_TYPE, g: str):
    """پیامی که پشتیبان فرستاده (ساخته‌شده یا فورواردشده از هر جا) رو برای همون جنسیت ذخیره می‌کنه."""
    msg = update.effective_message
    is_fwd = bool(getattr(msg, "forward_origin", None))
    data: dict = {}
    if msg.text:
        data["text"] = msg.text
        data["entities"] = [e.to_dict() for e in (msg.entities or [])]
    if is_fwd or not msg.text:
        # فوروارد یا جدول واقعی/غیرمتنی: خود پیام رو نگه می‌داریم و موقع نمایش کپی می‌کنیم
        data["kind"] = "copy"
        data["chat_id"] = msg.chat_id
        data["message_id"] = msg.message_id
        rich = _rich_of(msg)
        if rich:
            data["rich"] = rich
    try:
        from collections import Counter
        blob = json.dumps(msg.to_dict(), ensure_ascii=False)
        ids = re.findall(r'(?:emoji-id=\\?"|custom_emoji_id\\?"\s*:\s*\\?")(\d{10,})', blob)
        if ids:
            lock_id = Counter(ids).most_common(1)[0][0]
            order = [x for x in dict.fromkeys(ids) if x != lock_id]
            if len(order) >= 10:
                data["job_emojis"] = order[:10]
                data["lock_closed"] = lock_id
    except Exception:
        log.exception("emoji learn failed")
    try:
        await send_custom_jobs(context.bot, data, msg.chat_id)
    except Exception as e:
        log.exception("jobs preview failed")
        await msg.reply_text(f"❌ نتونستم این پیام رو برای نمایش آماده کنم: {type(e).__name__}: {str(e)[:150]}\nدوباره امتحان کن یا جدول رو متنی (مونواسپیس) بفرست.")
        return
    await pool.execute(
        "INSERT INTO bot_settings (key, value) VALUES ($1, $2) "
        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        f"jobs_custom_{g}",
        json.dumps(data, ensure_ascii=False),
    )
    CUSTOM_JOBS[g] = data
    context.user_data.pop("await_jobs", None)
    extra = "\n(این پیام رو از چت ربات پاک نکن؛ نمایش به همین پیام وصله.)" if data.get("kind") == "copy" else ""
    await msg.reply_text(f"✅ ذخیره شد و اعمال شد. از این به بعد همین جدول به {GENDER_FA[g]} نشون داده میشه. بالا پیش‌نمایشش بود.{extra}")


async def on_private_other(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """پیام‌های غیرمتنی تو پی‌وی (مثل جدول واقعی تلگرام): فقط وقتی پشتیبان بعد از /setjobs منتظره."""
    msg = update.effective_message
    if await admin_input(update, context):
        return
    g = context.user_data.get("await_jobs")
    if msg and g and update.effective_user.id in ADMIN_IDS:
        await save_custom_jobs(update, context, g)


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


LOCK_OPEN_ID = "6034962180875490251"


def _tge(eid, fb: str) -> str:
    return f'<tg-emoji emoji-id="{eid}">{fb}</tg-emoji>' if eid else fb


def job_glyph(gender: str, i: int, name: str) -> str:
    """ایموجی شغل: اول /setemoji job، بعد ایموجی‌هایی که از جدول فورواردشده یاد گرفتیم، آخرش ایموجی معمولی."""
    fb = JOB_GLYPHS.get(name, "💼")
    eid = EMOJI_IDS.get(("job", i + 1))
    if not eid:
        learned = (CUSTOM_JOBS.get(gender) or {}).get("job_emojis") or []
        if i < len(learned):
            eid = learned[i]
    return _tge(eid, fb)


def lock_closed() -> str:
    return _tge(EMOJI_IDS.get(("lock", 1)) or (CUSTOM_JOBS.get("m") or CUSTOM_JOBS.get("f") or {}).get("lock_closed"), "🔒")


def lock_open() -> str:
    return _tge(EMOJI_IDS.get(("lock", 2)) or LOCK_OPEN_ID, "🔓")


def jobs_footer(jaghs: int, nxt) -> str:
    lines = [f"{ce('jagh', 1, '🤛')} جق‌های تو: {jaghs:,}"]
    if nxt:
        lines.append(f"{ce('me', 2, '⬆️')} شغل بعدی: {html.escape(nxt[0])} — {nxt[1]:,} جق دیگه")
    else:
        lines.append(f"{ce('top', 1, '👑')} همه‌ی شغل‌ها رو داری!")
    return "\n".join(lines)


def jobs_rich_html(gender: str, jaghs: int) -> str:
    """جدول واقعی (Rich Message تلگرام)، شخصی برای هر کاربر: قفل باز برای شغل‌هایی که داره."""
    rows, nxt = jobs_rows(gender, jaghs)
    out = ["<table><tr><th>شغل</th><th>حقوق</th><th>جق باقی مانده</th></tr>"]
    for i, (glyph, name, pay, left) in enumerate(rows):
        st = f"{lock_open()} 0" if left == 0 else f"{lock_closed()} {left:,}"
        out.append(f"<tr><td>{job_glyph(gender, i, name)} {html.escape(name)}</td><td>{pay}</td><td>{st}</td></tr>")
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
    # اگه پشتیبان با /setjobs جدول ساخته باشه، همون برای همه نشون داده میشه
    custom = CUSTOM_JOBS.get(row["gender"])
    personal = (not custom) or custom.get("job_emojis") or any(k[0] == "job" for k in EMOJI_IDS)
    if custom and not personal:
        try:
            await send_custom_jobs(context.bot, custom, msg.chat_id, rp)
            await msg.reply_text(f"🤛 جق‌های تو: {row['jaghs']:,}")
            return
        except Exception:
            log.exception("custom jobs failed, falling back to default table")
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


# ───────────────────────── شیفت کار ─────────────────────────
# با نوشتن «کار» یا «شیفت» هر ۲۰ دقیقه یه بار شغل کاراکترت یه سوال ۳ گزینه‌ای می‌پرسه؛
# بعد از جواب، حقوق (بین همون بازه‌ی جدول شغل‌ها) به موجودی اضافه میشه.
SHIFT_COOLDOWN = 20 * 60
SHIFT_WORDS = {"کار", "شیفت", "shift"}
SHIFT_EMOJI_START = "5379844406830476532"  # اول پنل شیفت
SHIFT_EMOJI_DONE = "5183926089189163699"   # اول متن نتیجه


def _te(eid: str, fallback: str) -> str:
    return f'<tg-emoji emoji-id="{eid}">{fallback}</tg-emoji>'


def _strip_te(text: str) -> str:
    return re.sub(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", r"\1", text)


# هر شغل: چند سوال؛ هر سوال: (متن سوال، [(گزینه، چیزی که انجام دادی) × ۳])
SHIFT_QS = {
    "سرباز": [
        ("فرمانده گفته نگهبانی امشب با توئه. چکار می‌کنی؟", [
            ("سر پستم می‌مونم", "تا صبح سر پستت وایسادی و نگهبانی دادی."),
            ("چای دم می‌کنم", "چای دم کردی و با چای تا صبح بیدار موندی."),
            ("دور پادگان گشت می‌زنم", "کل پادگان رو گشت زدی و همه‌چیز امن بود."),
        ]),
        ("صبح رژه‌ست و پوتینت پاره شده. چکار می‌کنی؟", [
            ("با چسب درستش می‌کنم", "پوتینت رو چسب زدی و رژه رو بدون مشکل رفتی."),
            ("از رفیقم قرض می‌گیرم", "پوتین رفیقت رو قرض گرفتی و تو صف جلو وایسادی."),
            ("می‌رم با همون رژه", "با همون پوتین پاره رژه رفتی و فرمانده تشویقت کرد."),
        ]),
    ],
    "دانشجو": [
        ("فردا امتحانه و جزوه‌ات رو گم کردی. چکار می‌کنی؟", [
            ("از دوستم جزوه می‌گیرم", "جزوه‌ی دوستت رو کپی کردی و تا صبح خوندیش."),
            ("از شانس استفاده می‌کنم", "بدون جزوه رفتی سر امتحان و با شانس قبول شدی."),
            ("می‌رم استاد رو می‌بینم", "پیش استاد رفتی و یه فرصت اضافه گرفتی."),
        ]),
        ("کلاس ساعت ۸ صبحه و خوابت میاد. چکار می‌کنی؟", [
            ("قهوه می‌خورم", "قهوه خوردی و سر کلاس بیدار موندی."),
            ("ته کلاس می‌شینم", "ته کلاس نشستی و یواشکی جزوه نوشتی."),
            ("غیبت می‌کنم", "غیبت کردی ولی از رفیقت جزوه گرفتی."),
        ]),
    ],
    "راننده تاکسی": [
        ("مسافر آدرس رو بلد نیست. چکار می‌کنی؟", [
            ("نقشه رو باز می‌کنم", "با نقشه مسافر رو سالم رسوندی."),
            ("از مسافر می‌پرسم", "از مسافر پرسیدی و با هم مسیر رو پیدا کردین."),
            ("از رفیقم تو تاکسی‌ها می‌پرسم", "از بی‌سیم راننده‌ها کمک گرفتی و رسوندیش."),
        ]),
        ("وسط ترافیک سنگین گیر کردی. چکار می‌کنی؟", [
            ("صبر می‌کنم", "با آرامش تو ترافیک موندی و مسافر راضی بود."),
            ("از کوچه‌پس‌کوچه می‌رم", "از یه میان‌بر رفتی و زودتر رسیدی."),
            ("رادیو رو روشن می‌کنم", "رادیو گذاشتی و مسافر کرایه‌ی اضافه داد."),
        ]),
    ],
    "فروشنده": [
        ("مشتری می‌گه جنس گرونه. چکار می‌کنی؟", [
            ("تخفیف می‌دم", "یه تخفیف کوچیک دادی و جنس فروش رفت."),
            ("کیفیتش رو توضیح می‌دم", "کیفیت جنس رو توضیح دادی و مشتری راضی شد."),
            ("جنس ارزون‌تر نشون می‌دم", "یه جنس ارزون‌تر نشون دادی و فروش رفت."),
        ]),
        ("ساعت ۳ شبه و فقط یه مشتری داری. چکار می‌کنی؟", [
            ("بهش چای تعارف می‌کنم", "چای تعارف کردی و مشتری دو برابر خرید کرد."),
            ("پیشنهاد ویژه می‌دم", "یه پیشنهاد ویژه دادی و مشتری خوشحال رفت."),
            ("باهاش گپ می‌زنم", "کلی گپ زدین و مشتری ثابتت شد."),
        ]),
    ],
    "وکیل": [
        ("موکلت می‌گه پرونده رو می‌بازه. چکار می‌کنی؟", [
            ("مدارک رو دوباره بررسی می‌کنم", "مدارک رو دوباره دیدی و یه نکته‌ی مهم پیدا کردی."),
            ("با طرف مقابل مذاکره می‌کنم", "با طرف مقابل توافق کردی و پرونده بسته شد."),
            ("آرومش می‌کنم", "موکل رو آروم کردی و جلسه خوب پیش رفت."),
        ]),
        ("قاضی ازت مدرک می‌خواد و پوشه‌ات رو جا گذاشتی. چکار می‌کنی؟", [
            ("از حفظ توضیح می‌دم", "از حفظ دفاع کردی و قاضی تحت تأثیر قرار گرفت."),
            ("زنگ می‌زنم بیارن", "زنگ زدی و پوشه رسید و جلسه ادامه پیدا کرد."),
            ("درخواست زمان می‌کنم", "یه ساعت وقت گرفتی و پرونده رو درست کردی."),
        ]),
    ],
    "فضانورد": [
        ("آلارم ایستگاه فضایی زده شده. چکار می‌کنی؟", [
            ("سیستم رو چک می‌کنم", "سیستم رو چک کردی و مشکل رو پیدا کردی."),
            ("به مرکز کنترل خبر می‌دم", "به زمین خبر دادی و راهنمایی گرفتی."),
            ("آروم می‌مونم", "خونسرد موندی و آلارم الکی بود."),
        ]),
        ("اکسیژن داره کم می‌شه. چکار می‌کنی؟", [
            ("مخزن ذخیره رو وصل می‌کنم", "مخزن ذخیره رو وصل کردی و همه نجات پیدا کردن."),
            ("رفت‌وآمد رو کم می‌کنم", "فعالیت رو کم کردی تا اکسیژن کافی باشه."),
            ("کپسول رو چک می‌کنم", "کپسول رو چک کردی و نشتی رو بستی."),
        ]),
    ],
    "رئیس اداره": [
        ("کارمندی دیر اومده. چکار می‌کنی؟", [
            ("تذکر می‌دم", "یه تذکر دوستانه دادی و اداره نظم گرفت."),
            ("دلیلش رو می‌پرسم", "دلیلش رو پرسیدی و مشکلش حل شد."),
            ("نادیده می‌گیرم", "نادیده گرفتی و کارمند با انگیزه کار کرد."),
        ]),
        ("جلسه ۴ ساعته شده. چکار می‌کنی؟", [
            ("جلسه رو تموم می‌کنم", "جلسه رو جمع کردی و همه نفس راحت کشیدن."),
            ("استراحت اعلام می‌کنم", "ده دقیقه استراحت دادی و ادامه‌ش خوب پیش رفت."),
            ("چای سفارش می‌دم", "چای سفارش دادی و جلسه شاد تموم شد."),
        ]),
    ],
    "رئیس‌جمهور": [
        ("خبرنگارها سوال سختی پرسیدن. چکار می‌کنی؟", [
            ("صادقانه جواب می‌دم", "صادقانه جواب دادی و اعتماد مردم بیشتر شد."),
            ("می‌گم بعداً توضیح می‌دم", "از سوال طفره رفتی و کنفرانس تموم شد."),
            ("شوخی می‌کنم", "یه شوخی کردی و جو رو عوض کردی."),
        ]),
        ("جلسه‌ی هیئت دولت داره طول می‌کشه. چکار می‌کنی؟", [
            ("تصمیم نهایی می‌گیرم", "تصمیم نهایی رو گرفتی و جلسه تموم شد."),
            ("نظر همه رو می‌شنوم", "نظر همه رو شنیدی و یه راه‌حل خوب پیدا شد."),
            ("یه استراحت می‌دم", "یه استراحت دادی و همه با انرژی برگشتن."),
        ]),
    ],
    "رهبر": [
        ("مردم گله دارن. چکار می‌کنی؟", [
            ("پیام می‌دم", "یه پیام آرامش‌بخش دادی و مردم خوشحال شدن."),
            ("میان مردم می‌رم", "میان مردم رفتی و حرفاشون رو شنیدی."),
            ("مسئولین رو احضار می‌کنم", "مسئولین رو احضار کردی و قول رسیدگی گرفتی."),
        ]),
        ("جلسه‌ی مهمی داری و خوابت میاد. چکار می‌کنی؟", [
            ("چای سبز می‌خورم", "چای سبز خوردی و جلسه رو با انرژی گذروندی."),
            ("جلسه رو کوتاه می‌کنم", "جلسه رو کوتاه کردی و همه راضی بودن."),
            ("بلند می‌شم قدم می‌زنم", "قدم زدی و ایده‌ی خوبی به ذهنت رسید."),
        ]),
    ],
    "میلیاردر": [
        ("یه استارتاپ ازت سرمایه می‌خواد. چکار می‌کنی؟", [
            ("سرمایه‌گذاری می‌کنم", "سرمایه‌گذاری کردی و سودش زود رسید."),
            ("ایده‌ش رو بررسی می‌کنم", "ایده رو بررسی کردی و یه شراکت خوب بستی."),
            ("رد می‌کنم", "رد کردی و پولت رو جای بهتری گذاشتی."),
        ]),
        ("بورس داره می‌ریزه. چکار می‌کنی؟", [
            ("می‌فروشم", "به‌موقع فروختی و ضرر نکردی."),
            ("نگه می‌دارم", "نگه داشتی و چند روز بعد برگشت."),
            ("بیشتر می‌خرم", "ارزون خریدی و سود خوبی کردی."),
        ]),
    ],
    "بلاگر": [
        ("لایو داری و فالوئرها سوال می‌پرسن. چکار می‌کنی؟", [
            ("به همه جواب می‌دم", "به همه جواب دادی و لایو ترکوند."),
            ("نظرسنجی می‌ذارم", "یه نظرسنجی گذاشتی و بازدید رفت بالا."),
            ("چالش می‌ذارم", "یه چالش گذاشتی و ترند شد."),
        ]),
        ("پست جدیدت لایک کمی خورده. چکار می‌کنی؟", [
            ("استوری می‌ذارم", "استوری گذاشتی و پستت دیده شد."),
            ("هشتگ عوض می‌کنم", "هشتگ‌ها رو عوض کردی و لایک‌ها بالا رفت."),
            ("پست جدید می‌ذارم", "یه پست جدید گذاشتی و ترکوند."),
        ]),
    ],
    "رقاص": [
        ("وسط اجرا موزیک قطع شد. چکار می‌کنی؟", [
            ("بی‌موزیک می‌رقصم", "بدون موزیک رقصیدی و تماشاچی‌ها دست زدن."),
            ("با دست‌زدن ریتم می‌دم", "با تماشاچی‌ها ریتم گرفتی و اجرا ادامه پیدا کرد."),
            ("منتظر می‌مونم", "صبر کردی و موزیک برگشت."),
        ]),
        ("کفشت وسط اجرا درد گرفت. چکار می‌کنی؟", [
            ("با لبخند ادامه می‌دم", "با لبخند ادامه دادی و کسی نفهمید."),
            ("کفش رو در میارم", "کفش رو در آوردی و بی‌کفش رقصیدی."),
            ("حرکت رو عوض می‌کنم", "حرکت رو عوض کردی و اجرا قشنگ‌تر شد."),
        ]),
    ],
    "ناخن‌کار": [
        ("مشتری رنگی می‌خواد که نداری. چکار می‌کنی؟", [
            ("رنگ ترکیب می‌کنم", "با ترکیب دو رنگ دقیقاً همون رو ساختی."),
            ("رنگ مشابه نشون می‌دم", "یه رنگ مشابه نشون دادی و مشتری پسندید."),
            ("می‌گم فردا بیاد", "قرار فردا گذاشتی و مشتری راضی رفت."),
        ]),
        ("طرح پیچیده‌ای سفارش دادن. چکار می‌کنی؟", [
            ("آروم و دقیق می‌کشم", "آروم و دقیق کشیدی و نتیجه عالی شد."),
            ("از قالب استفاده می‌کنم", "از قالب استفاده کردی و زود تموم شد."),
            ("ایده‌ی خودم رو اضافه می‌کنم", "یه ایده‌ی خودت اضافه کردی و مشتری عاشقش شد."),
        ]),
    ],
    "دندان‌پزشک": [
        ("بیمار از دندون‌پزشکی می‌ترسه. چکار می‌کنی؟", [
            ("باهاش حرف می‌زنم", "باهاش حرف زدی و آروم شد."),
            ("موزیک می‌ذارم", "موزیک گذاشتی و بیمار بی‌استرس موند."),
            ("اول ابزار رو نشون می‌دم", "ابزار رو توضیح دادی و ترسش ریخت."),
        ]),
        ("دندون عقل یه بیمار درد داره. چکار می‌کنی؟", [
            ("عکس می‌گیرم", "عکس گرفتی و درمان رو دقیق انجام دادی."),
            ("بی‌حس می‌کنم", "بی‌حس کردی و درمان بدون درد تموم شد."),
            ("نسخه می‌نویسم", "نسخه نوشتی و بیمار راضی رفت."),
        ]),
    ],
    "داروخونه‌دار": [
        ("مشتری بدون نسخه دارو می‌خواد. چکار می‌کنی؟", [
            ("می‌گم نسخه بیاره", "درست عمل کردی و مشتری نسخه آورد."),
            ("جایگزین بی‌خطر پیشنهاد می‌دم", "یه جایگزین بی‌خطر پیشنهاد دادی."),
            ("راهنماییش می‌کنم دکتر بره", "راهنماییش کردی و مشتری ممنون بود."),
        ]),
        ("قفسه‌ی داروها به هم ریخته. چکار می‌کنی؟", [
            ("مرتبش می‌کنم", "قفسه رو مرتب کردی و کار راحت‌تر شد."),
            ("لیست‌برداری می‌کنم", "لیست‌برداری کردی و کمبودها مشخص شد."),
            ("برچسب جدید می‌زنم", "برچسب‌ها رو عوض کردی و همه‌چیز سر جاش رفت."),
        ]),
    ],
    "شغل مرموز": [
        ("یه پیام ناشناس بهت رسیده. چکار می‌کنی؟", [
            ("بازش می‌کنم", "پیام رو باز کردی و یه سرنخ پیدا کردی."),
            ("نادیده می‌گیرم", "نادیده گرفتی و همه‌چیز آروم موند."),
            ("پیگیریش می‌کنم", "فرستنده رو پیدا کردی و کار انجام شد."),
        ]),
        ("نیمه‌شبه و یه نفر در می‌زنه. چکار می‌کنی؟", [
            ("از چشمی نگاه می‌کنم", "از چشمی نگاه کردی و خطری نبود."),
            ("در رو باز می‌کنم", "در رو باز کردی و یه مأموریت جدید گرفتی."),
            ("صدا نمی‌کنم", "ساکت موندی و رفت."),
        ]),
    ],
}
# ───── سوال‌های بیشتر شیفت: هر شغل ۸ سوال اضافه (روی ۲ سوال قبلی = ۱۰ سوال). فرمت: (سوال، گزینه۱، نتیجه۱، گزینه۲، نتیجه۲، گزینه۳، نتیجه۳) ─────
SHIFT_QS_EXTRA = {
    "سرباز": [
        ("فرمانده گفته صبحگاه ساعت ۵ بیدار باش. ساعت ۴:۵۹ چی‌کار می‌کنی؟", "از قبل بیدارم", "قبل از شیپور آماده ایستادی و فرمانده سر تکون داد.", "بالشت رو بغل می‌کنم", "با ده دقیقه تاخیر رسیدی و یه دور اضافه دویدی.", "وانمود می‌کنم مریضم", "مسئول بهداری فهمید و فقط خندید."),
        ("نگهبانی نصف شبه و صدایی از بوته‌ها میاد.", "داد می‌زنم «ایست!»", "یه گربه از بوته در رفت و تو سربلند موندی.", "چراغ‌قوه رو روشن می‌کنم", "یه خرگوش پیدا کردی و پادگان یه شب آروم داشت.", "فرار می‌کنم", "فرمانده فردا یه نگهبانی اضافه بهت داد."),
        ("غذای سربازخونه امروز عدسیه. چی‌کار می‌کنی؟", "با اشتها می‌خورم", "همه‌ی بشقاب رو خوردی و آشپز بهت یه ملاقه اضافه داد.", "نون و پنیر می‌خرم", "از بوفه خریدی و تا شب سیر بودی.", "با رفیقم معامله می‌کنم", "ظرفت رو با یه ژله عوض کردی. معامله‌ی سال!"),
        ("گروهبان گفت پوتینت رو واکس بزن تا برق بزنه.", "تا صبح براقش می‌کنم", "پوتینت آینه شد و گروهبان خودش رو توش دید.", "با آستینم تمیزش می‌کنم", "یه برق مختصر زد و گروهبان فقط سر تکون داد.", "واکس نداشتم", "گروهبان واکسش رو داد و ازت چایی خواست."),
        ("مرخصی گرفتی ولی قطار دیر کرده.", "زنگ می‌زنم به فرمانده", "فرمانده فهمید و یه روز مهلت بهت داد.", "ماشین دربست می‌گیرم", "سر وقت رسیدی ولی حقوقت پرید.", "تو ایستگاه می‌خوابم", "صبح ساعت ۶ رسیدی و آماده بودی."),
        ("تمرین تیراندازیه و هدف خیلی دوره.", "نفسم رو حبس می‌کنم", "درست وسط هدف زدی و همه کف زدن.", "چشم‌بسته شلیک می‌کنم", "تیرت خورد به تابلوی کناری! ولی خنده‌دار بود.", "از رفیقم کمک می‌گیرم", "با راهنمایی رفیقت دومین تیر درست شد."),
        ("هم‌اتاقیت خیلی خرناس می‌کشه.", "گوش‌گیر می‌ذارم", "تا صبح راحت خوابیدی.", "تکونش می‌دم", "ساکت شد، ولی ده دقیقه بعد دوباره شروع کرد.", "ضبطش می‌کنم", "فردا صبح با صدای خودش بیدارش کردی. همه خندیدن."),
        ("بازرسی وسایله و ته کوله‌ت شکلات قایم کردی.", "میگم مال فرماندس", "افسر خندید و یه تیکه برداشت.", "پنهونش می‌کنم", "بازرس ندید و شکلات‌ها نجات پیدا کردن.", "خودم می‌خورم", "قبل از بازرسی همه رو خوردی، ولی دهنت شکلاتی بود."),
    ],
    "دانشجو": [
        ("استاد سرزده کوییز گرفت.", "جواب می‌دم", "هرچی یاد داشتی نوشتی و استاد تشویقت کرد.", "از بغلی نگاه می‌کنم", "بغلی هم ننوشته بود. هر دو با هم صفر شدین.", "میگم یادم نمیاد", "استاد دلش سوخت و بهت نمره‌ی ارفاقی داد."),
        ("پروژه‌ی گروهیه و یکی از اعضا غیبش زده.", "کارش رو خودم می‌کنم", "پروژه تموم شد و استاد قدردانی کرد.", "بهش پیام می‌دم", "جواب داد «الان میام» و نیومد.", "اسمش رو حذف می‌کنم", "استاد فهمید و سه نفرتون رو دعوت کرد."),
        ("سلف پر از جمعیته و فقط ۱۰ دقیقه وقت داری.", "تو صف می‌ایستم", "غذات رو گرفتی و با عجله خوردی.", "ساندویچ می‌خرم", "با ساندویچ رسیدی سر کلاس.", "گرسنه می‌رم", "کلاس رو گرسنه گذروندی و شکمت آواز خوند."),
        ("اینترنت خوابگاه قطعه و تحویل پروژه ساعت ۱۲ شبه.", "نت گوشیم رو روشن می‌کنم", "پروژه ارسال شد، ۵ ثانیه مونده به آخر.", "می‌رم کافی‌نت", "به‌موقع رسیدی ولی چایی ریخت روی کیبورد.", "به استاد ایمیل می‌زنم", "استاد با اینکه دیر بود قبول کرد."),
        ("استاد پرسید: «این سوال از کدوم فصل بود؟»", "فصل ۳", "جوابت درست بود و استاد لبخند زد.", "فصل ۵", "غلط بود ولی استاد تشویقت کرد که دوباره امتحان کنی.", "سکوت می‌کنم", "استاد ادامه داد و تو نجات پیدا کردی."),
        ("شب امتحانه و قهوه‌ت تموم شده.", "چایی می‌خورم", "چایی کار کرد و تا صبح بیدار موندی.", "آب یخ می‌زنم تو صورتم", "بیدار شدی، ولی همخوابگاهی‌ت ناراحت شد.", "می‌خوابم", "صبح زود با ذهن باز بیدار شدی و راحت نوشتی."),
        ("استاد گفته ارائه بده ولی پاورپوینت نساختی.", "روی تخته می‌نویسم", "ارائه‌ی بدون اسلاید همه رو خیلی جذب کرد.", "گوشیم رو نشون می‌دم", "نصف اسلایدها هنگ کرد ولی رد شد.", "میگم فایلم پاک شد", "استاد گفت هفته‌ی بعد. چه شانسی!"),
        ("دوستت پیام داده: «کلاس نرو، بریم بیرون!»", "می‌رم کلاس", "کلاس مهم بود و تو یه مبحث کلیدی رو یاد گرفتی.", "می‌رم بیرون", "خوش گذشت ولی حضورغیابت خراب شد.", "نصف کلاس، نصف بیرون", "هم کلاس رفتی هم بستنی خوردی."),
    ],
    "راننده تاکسی": [
        ("مسافر میگه: «از نزدیک‌ترین راه برو.»", "از خیابون اصلی", "ترافیک نبود و زود رسیدی.", "از کوچه‌پس‌کوچه‌ها", "مسافر کلی خوشحال شد از میان‌بر.", "با GPS می‌رم", "GPS تو رو برد توی یه بن‌بست خنده‌دار."),
        ("ترافیک بزرگراه خیلی سنگینه.", "رادیو روشن می‌کنم", "آهنگ خوب پخش شد و مسافر باهات خوند.", "از خط ویژه می‌رم", "جریمه نشدی ولی دلت شور افتاد.", "صبر می‌کنم", "ترافیک باز شد و مسافر ازت تشکر کرد."),
        ("مسافر وسط راه خوابش برد.", "آروم رانندگی می‌کنم", "مسافر آروم بیدار شد و بهت انعام داد.", "بوق می‌زنم", "از جا پرید و یه کم ناراحت شد.", "آهنگ رو بلند می‌کنم", "با آهنگ بیدار شد و باهات خندید."),
        ("بنزین ماشین داره تموم میشه.", "می‌رم جایگاه", "پر کردی و شیفتت ادامه پیدا کرد.", "با ذخیره می‌رم", "رسیدی ولی عرق کردی.", "از مسافر می‌خوام کمک کنه", "مسافر خندید و نصف هزینه رو داد."),
        ("مسافر ساعت ۳ شب سوار شد و ساکته.", "باهاش حرف می‌زنم", "کلی خاطره تعریف کرد و بهت انعام داد.", "ساکت می‌رم", "بی‌سروصدا رسوندیش و کرایه کامل گرفتی.", "موسیقی آروم می‌ذارم", "حال مسافر خوب شد و تشکر کرد."),
        ("یه مسافر خارجی میگه «Azadi Tower».", "می‌برمش", "تو راه ازش انعام دلاری گرفتی.", "نمی‌فهمم", "باهم با ژست حرف زدین و جالب شد.", "از گوشیم ترجمه می‌کنم", "مسافر راضی بود و ازت عکس گرفت."),
        ("پلیس راهنمایی جلوت رو گرفت.", "گواهینامه می‌دم", "همه‌چی درست بود و رد شدی.", "میگم عجله دارم", "پلیس خندید و بدون جریمه رفتی.", "آروم می‌ایستم", "پلیس فقط خوش‌وبش کرد و رد شدی."),
        ("مسافر میگه پول خرد نداره.", "می‌گم مهم نیست", "مسافر خوشحال شد و بیشتر بهت داد.", "خرد می‌کنم", "با سختی خرد شد ولی کار گذشت.", "از بقالی خرد می‌کنم", "بقال خندید و پول رو داد."),
    ],
    "فروشنده": [
        ("مشتری میگه: «این خیلی گرونه!»", "تخفیف می‌دم", "مشتری خرید و دوباره هم میاد.", "میگم کیفیتشو ببین", "مشتری قانع شد و خرید.", "ازش نظرش رو می‌پرسم", "مشتری حرف زد و با خرید دوتایی رفت."),
        ("۵ دقیقه به ساعت بستن، مشتری وارد شد.", "با رغبت خدمت می‌کنم", "مشتری کلی خرید کرد!", "میگم بستیم", "مشتری ناراحت رفت.", "دکونو می‌بندم و می‌رم", "یه مشتری دیگه ندیدی و خسته رفتی خونه."),
        ("یه جنس ته‌مونده مونده تو انبار.", "حراجش می‌کنم", "تموم شد و جای انبار آزاد شد.", "بسته‌بندی‌ش رو عوض می‌کنم", "بسته‌ی جدید یه مشتری رو جذب کرد.", "می‌ذارمش برا بعدا", "بعد از یه ماه یه مشتری پیداش کرد."),
        ("مشتری مشکوک دستش تو جیبشه.", "نگاهش می‌کنم", "فهمید و با خجالت دستش رو درآورد.", "ازش می‌پرسم چی می‌خواد", "فقط موبایل می‌خواست و چیزی هم خرید.", "دوربین رو چک می‌کنم", "همه‌چی آروم بود و مشتری هم رفت."),
        ("مشتری برگشته که جنس رو پس بده.", "پس می‌گیرم", "مشتری خوشحال شد و یه چیز دیگه خرید.", "میگم مهلت تموم شده", "دعواتون شد ولی با قانون جلو رفتی.", "عوضش می‌کنم", "مشتری راضی شد و رفتین سراغ جنس جدید."),
        ("دکون روبه‌رویی تخفیف نصفه زده!", "منم تخفیف می‌دم", "هر دو فروش داشتین.", "کیفیت رو تبلیغ می‌کنم", "مشتری‌های دلسوز اومدن.", "اهمیت نمی‌دم", "فروشت کم شد ولی دلت آروم بود."),
        ("از صبح هیچ مشتری نیومده.", "بیرون تابلو می‌ذارم", "مشتری‌ها جذب شدن و کار راه افتاد.", "گوشیم رو چک می‌کنم", "تو اینستا یه سفارش جدید دیدی!", "چایی می‌خورم", "چایی تموم نشده یه مشتری بزرگ اومد."),
        ("مشتری ازت میپرسه کدوم بهتره.", "گرون‌ترین رو پیشنهاد می‌دم", "مشتری خرید ولی یه کم ناراضی بود.", "صادقانه میگم", "مشتری اعتماد کرد و رفیق دائمیت شد.", "ازش می‌پرسم بودجه‌ش چقدره", "مشتری راضی شد و خریدش رو انجام داد."),
    ],
    "وکیل": [
        ("موکلت یه‌جاهایی دروغ گفته.", "بهش تذکر می‌دم", "موکل راستش رو گفت و پرونده محکم‌تر شد.", "نادیده می‌گیرم", "ریسکش بالا بود ولی گذشت.", "مدارک رو دوباره بررسی می‌کنم", "تناقض‌ها رو پیدا کردی و تو دادگاه استفاده کردی."),
        ("قاضی نیم ساعت دیر اومده.", "چایی می‌خورم", "وقتی قاضی اومد تو آماده بودی.", "پرونده رو مرور می‌کنم", "یه نکته‌ی تازه پیدا کردی.", "گوشی بازی می‌کنم", "حواست پرت شد و کلمه‌ی اول رو یادت رفت."),
        ("موکل میگه «ارزون‌تر کن!»", "تخفیف می‌دم", "موکل خوشحال شد و دوستاش رو آورد.", "میگم کیفیت قیمت داره", "موکل قانع شد و قرارداد بست.", "قسطی پیشنهاد می‌کنم", "موکل راضی بود و تسویه شد."),
        ("طرف مقابل پیشنهاد صلح داده.", "قبول می‌کنم", "پرونده ساده بسته شد و همه راضی بودن.", "مذاکره می‌کنم", "بهتر از اولش شد.", "رد می‌کنم", "رفتی دادگاه و پرونده سخت‌تر شد."),
        ("یه مدرک مهم توی ماشین جا مونده.", "با تاکسی می‌رم", "به‌موقع رسیدی و مدرک رو دادی.", "از منشی می‌خوام", "منشی هم جا گذاشت. خنده‌دار شد.", "دوباره می‌نویسمش", "نسخه‌ی جدید حتی بهتر از قبلی بود."),
        ("قاضی یه سوال خیلی سخت پرسید.", "فکر می‌کنم و جواب می‌دم", "جواب منطقی بود و قاضی تایید کرد.", "از پرونده کمک می‌گیرم", "ماده‌ی قانونی رو پیدا کردی و جواب دادی.", "میگم مهلت بدید", "قاضی ۵ دقیقه استراحت داد."),
        ("خبرنگار می‌خواد مصاحبه کنه.", "قبول می‌کنم", "مصاحبه‌ت خوب بود و اعتبارت بیشتر شد.", "میگم بی‌توضیح", "خبرنگار کلی کنجکاو شد.", "آماده می‌شم", "با آمادگی جواب دادی و همه ازت تعریف کردن."),
        ("شب قبل از دفاع، پرونده‌ت ناقصه.", "تا صبح مطالعه می‌کنم", "صبح با ذهن پر رفتی و حرفت سنگین بود.", "می‌خوابم", "با ذهن تازه ولی بدون جزئیات رفتی.", "از همکارم کمک می‌گیرم", "همکارت نکته‌ی طلایی بهت داد."),
    ],
    "فضانورد": [
        ("اکسیژن داره کم می‌شه!", "پنل رو چک می‌کنم", "خرابی کوچیک بود و درست شد.", "به زمین گزارش می‌دم", "مرکز کنترل راه‌حل داد و اکسیژن پایدار شد.", "نفس عمیق می‌کشم", "آروم موندی و مشکل حل شد."),
        ("یه ستاره‌ی ناشناس از پنجره دیده میشه.", "ثبتش می‌کنم", "اسمش رو بذاری یه کشف جدیده!", "عکس می‌گیرم", "عکست تو مجله‌ها چاپ شد.", "به مرکز میگم", "همه سرگرم تحلیل ستاره شدن."),
        ("قدم‌زدن فضاییه و کابلت شل شده.", "محکمش می‌کنم", "کار تموم شد و آروم برگشتی.", "کمک می‌خوام", "همکارت اومد و با هم درستش کردین.", "ادامه می‌دم", "یه لحظه ترسیدی ولی خوب شد."),
        ("قهوه‌ت وسط کابین شناور شده!", "با دهن می‌گیرمش", "قهوه رو مثل بازی خوردی.", "با کیسه می‌گیرمش", "هیچی خیس نشد.", "ولش می‌کنم", "قطره‌ها همه‌جا پخش شد ولی خنده‌دار بود."),
        ("ارتباط با زمین قطع شده.", "فرکانس رو عوض می‌کنم", "ارتباط برقرار شد و همه نفس راحت کشیدن.", "انتظار می‌کشم", "بعد ۱۰ دقیقه ارتباط برگشت.", "با دستی علامت می‌دم", "از پنجره‌ی ایستگاه یه علامت نور فرستادی."),
        ("غذای امروز لوله‌ایه، مزه‌ش عجیبه.", "می‌خورم", "حس کردی شبیه غذای مامانه.", "با ادویه ترکیب می‌کنم", "غذا خوش‌مزه‌تر شد.", "نمی‌خورم", "گرسنه موندی و شب سخت بود."),
        ("یه شهاب‌سنگ کوچیک نزدیک می‌شه.", "مسیرم رو عوض می‌کنم", "رد شد و ایستگاه سالم موند.", "به مرکز خبر می‌دم", "مرکز مسیر رو محاسبه کرد.", "تماشا می‌کنم", "از کنار رد شد و عکس خوبی گرفتی."),
        ("دلت برای زمین تنگ شده.", "از خانواده فیلم می‌بینم", "حالت بهتر شد و انرژی گرفتی.", "به زمین نگاه می‌کنم", "از بالا خیلی زیبا بود.", "کار می‌کنم", "با کار زمان گذشت و حواست پرت شد."),
    ],
    "رئیس اداره": [
        ("یه کارمند دیر اومده.", "اخطار می‌دم", "کارمند خجالت کشید و دیگه دیر نیومد.", "ازش دلیل می‌پرسم", "ترافیک بود و همه‌چی روشن شد.", "نادیده می‌گیرم", "کارمند دیگه‌ها هم دیر اومدن!"),
        ("جلسه‌ی بی‌هدفی در جریانه.", "جمعش می‌کنم", "جلسه کوتاه شد و همه تشکر کردن.", "سوال می‌پرسم", "جلسه سمت هدف رفت.", "تا آخر می‌شینم", "ساعت‌ها گذشت و همه خسته شدن."),
        ("بودجه کمه و پروژه گرونه.", "هزینه‌ها رو کم می‌کنم", "با برنامه‌ریزی پروژه پیش رفت.", "از بالادست می‌خوام", "بودجه‌ی اضافه تصویب شد.", "پروژه رو عقب می‌ندازم", "پروژه شروع نشد ولی اضطرابت کم شد."),
        ("مدیرعامل سرزده اومده بازدید.", "خوش‌آمد می‌گم", "بازدید عالی بود و تعریف شنیدی.", "همه رو مرتب می‌کنم", "اداره تمیز و منظم شد.", "قایم می‌شم", "بعد پیدات کردن و همه خندیدن."),
        ("کارمندت درخواست مرخصی داده.", "قبول می‌کنم", "کارمند با انرژی برگشت.", "می‌گم بعدا", "کارمند ناراحت شد.", "کار جایگزین می‌ذارم", "مرخصی با برنامه انجام شد."),
        ("فایل مهم رو اشتباهی پاک کردن.", "از بکاپ بازیابی می‌کنم", "فایل برگشت و همه نفس کشیدن.", "کارمند رو توبیخ می‌کنم", "مشکل حل نشد ولی عصبی شدی.", "فایل رو دوباره می‌سازم", "کار از اول شد ولی بهتر از قبل."),
        ("دو کارمند سر یه موضوع دعواشون شده.", "آشتی‌شون می‌دم", "دست دادن و ماجرا تموم شد.", "جداشون می‌کنم", "یه نفر رفت اتاق دیگه و آروم شد.", "ساکت می‌شم", "دعوا بالا گرفت و دلت درد گرفت."),
        ("گزارش فصلی باید تا فردا آماده بشه.", "همه‌ی تیم رو جمع می‌کنم", "با کمک هم زود تموم شد.", "خودم می‌نویسم", "تا صبح بیدار موندی.", "تعویقش می‌دم", "مدیرعامل ناراحت شد."),
    ],
    "رئیس‌جمهور": [
        ("سخنرانی داری و تله‌پرامپتر خراب شده.", "از حفظ می‌گم", "همه تعجب کردن از حافظه‌ت.", "از کاغذ می‌خونم", "کاغذ هم یه کم مچاله بود ولی گذشت.", "شوخی می‌کنم", "همه خندیدن و جو خوب شد."),
        ("قیمت نون یهو بالا رفته.", "جلسه‌ی فوری می‌ذارم", "با برنامه‌ریزی قیمت کنترل شد.", "با مردم حرف می‌زنم", "مردم راضی شدن.", "وعده می‌دم", "مردم منتظر موندن."),
        ("خبرنگار یه سوال خیلی تند می‌پرسه.", "جواب صادقانه می‌دم", "خبرنگار تحسین کرد.", "موضوع رو عوض می‌کنم", "جلسه آروم شد.", "میگم بعدا", "خبرنگار با اکراه قبول کرد."),
        ("مهمان خارجی ۲ ساعت دیر رسیده.", "منتظر می‌مونم", "مهمان ممنون شد و نشست.", "غذا رو برمی‌دارم", "غذا سرد شد ولی همه خوردن.", "برنامه رو جابجا می‌کنم", "برنامه‌ریزی دقیق جواب داد."),
        ("یکی از وزیرها استعفا داده.", "جایگزین پیدا می‌کنم", "یه جایگزین قوی انتخاب شد.", "با خودش حرف می‌زنم", "استعفاش رو پس گرفت.", "قبول می‌کنم", "کار رو ادامه دادی."),
        ("مراسم افتتاحه و قیچی نمی‌بره.", "با دست پاره می‌کنم", "روبان پاره شد و همه کف زدن.", "قیچی جدید می‌خوام", "قیچی آوردن و مراسم ادامه پیدا کرد.", "می‌خندم", "همه خندیدن و جو خوب شد."),
        ("ساعت ۳ شب یه تماس فوری داری.", "جواب می‌دم", "مشکل کوچیک بود و حل شد.", "می‌خوابم", "صبح فهمیدی خبر مهمی نبود.", "مشاور رو بیدار می‌کنم", "مشاور تصمیم درست رو گرفت."),
        ("نظرسنجی نشون میده محبوبیتت پایینه.", "کار می‌کنم", "با کار و تلاش محبوبیت بالا رفت.", "تبلیغ می‌کنم", "تبلیغ جواب داد.", "نادیده می‌گیرم", "محبوبیت همون‌جا موند."),
    ],
    "رهبر": [
        ("دو هم‌پیمان با هم اختلاف دارن.", "وساطت می‌کنم", "آشتی کردن و اتحاد قوی‌تر شد.", "یه طرف رو می‌گیرم", "یه طرف راضی و یکی ناراضی شد.", "ساکت می‌شم", "اختلاف خودش حل شد."),
        ("جمع اعتراض کوچیکی داره.", "گوش می‌دم", "مشکل پیدا شد و حل شد.", "سخنرانی می‌کنم", "جمع آروم گرفت.", "نادیده می‌گیرم", "اعتراض بزرگ‌تر شد."),
        ("سخنرانی‌ت خیلی طولانی شده.", "کوتاهش می‌کنم", "همه راضی بودن.", "ادامه می‌دم", "نصف جمع خوابید.", "با شوخی تمومش می‌کنم", "همه خندیدن و کف زدن."),
        ("شورا یه پیشنهاد عجیب داده.", "قبول می‌کنم", "پیشنهاد جواب داد!", "رد می‌کنم", "شورا یه کم ناراحت شد.", "بررسی می‌کنم", "با بررسی ایده بهتر شد."),
        ("جانشینت خیلی مشتاق جای توئه.", "آموزشش می‌دم", "جانشین قوی شد و تو راحت شدی.", "مراقبش می‌شم", "کارهاش رو پیگیری کردی.", "بهش اعتماد می‌کنم", "همه‌چی آروم موند."),
        ("مراسم سالانه نزدیکه.", "برنامه‌ریزی می‌کنم", "مراسم عالی برگزار شد.", "به مشاور می‌سپارم", "مشاور کار رو تموم کرد.", "ساده‌ش می‌کنم", "ساده بود ولی همه راضی بودن."),
        ("خدمتکار چای خیلی داغ ریخته.", "صبر می‌کنم", "چای خنک شد و لذت بردی.", "می‌خندم", "همه خندیدن.", "عوضش می‌کنم", "چای جدید آوردن."),
        ("مشاورت یه نقشه‌ی بلندپروازانه داده.", "اجراش می‌کنم", "نقشه جواب داد و افتخار کردی.", "اصلاحش می‌کنم", "نقشه بهتر شد.", "بایگانیش می‌کنم", "نقشه برای بعدا موند."),
    ],
    "میلیاردر": [
        ("سهام شرکتت ۱۰٪ ریخته.", "می‌فروشم", "ضرر کم شد.", "نگه می‌دارم", "هفته‌ی بعد بالا رفت!", "بیشتر می‌خرم", "قیمت برگشت و سود کردی."),
        ("تصمیم داری یه جزیره بخری.", "خصوصی می‌خرم", "جزیره مال تو شد.", "اجاره می‌کنم", "ارزون‌تر تموم شد.", "فکر می‌کنم", "هنوز دودلی ولی جزیره می‌مونه."),
        ("شریکت کلاهبردار از آب دراومد.", "شکایت می‌کنم", "پولت برگشت.", "ازش می‌گذرم", "درس گرفتی.", "شرکت رو تقسیم می‌کنم", "با جدایی راحت شدی."),
        ("یه گدا جلوی ماشینت ایستاده.", "کمک می‌کنم", "دعاش بدرقه‌ت شد.", "کار بهش می‌دم", "یه کارمند وفادار پیدا کردی.", "رد می‌شم", "ناراحت شدی ولی ادامه دادی."),
        ("جشن تولدت داره برگزار می‌شه.", "بزرگ می‌گیرم", "همه خوش گذروندن.", "ساده می‌گیرم", "فقط با دوستات بودی.", "کنسلش می‌کنم", "یه روز آروم داشتی."),
        ("یه استارتاپ جوون ایده‌ی خوبی داره.", "سرمایه‌گذاری می‌کنم", "شرکت رشد کرد و سود دادی.", "مشاوره می‌دم", "جوون‌ها ممنون شدن.", "رد می‌کنم", "یه شرکت دیگه شانسش رو گرفت."),
        ("پهپاد جدیدت افتاد تو استخر.", "بیرونش میارم", "خشکش کردی و روشن شد!", "یکی جدید می‌خرم", "ریخت‌وپاش کردی ولی خوشحال شدی.", "ولش می‌کنم", "پهپاد تو استخر موند."),
        ("وقتشه وصیت‌نامه بنویسی.", "همه رو به خیریه می‌دم", "نامت پرآوازه شد.", "بین خانواده تقسیم می‌کنم", "همه راضی بودن.", "یه معما می‌ذارم", "همه سرگرم حل معما شدن."),
    ],
    "بلاگر": [
        ("یهو ۱۰۰۰ فالوئر ریختن.", "پست می‌ذارم", "برگشتن و بیشتر شدن!", "استوری می‌ذارم", "همه سلام کردن.", "نگران می‌شم", "بعد فهمیدی اینستا باگ داشته."),
        ("یه برند اسپانسر شده با شرط عجیب.", "قبول می‌کنم", "درآمد خوبی داشتی.", "مذاکره می‌کنم", "شرط بهتر شد.", "رد می‌کنم", "یه برند بهتر پیدات کرد."),
        ("یه کامنت منفی زیر پستت اومده.", "جواب می‌دم", "با ادب جواب دادی و همه تحسین کردن.", "بلاکش می‌کنم", "آروم شدی.", "نادیده می‌گیرم", "کامنت گم شد."),
        ("لایوت وسطش قطع شد.", "دوباره لایو می‌زنم", "اینبار بیشتر دیدنت.", "پست می‌ذارم", "همه پیگیر شدن.", "می‌خوابم", "صبح پر از پیام بود."),
        ("یه ویدیوت ناخواسته ویروسی شد.", "دنبالش می‌رم", "فالوئرها زیاد شد.", "توضیح می‌دم", "همه خندیدن.", "حذفش می‌کنم", "یه فرصت از دست رفت."),
        ("بلاگر رقیب ایده‌ت رو کپی کرده.", "اعلام می‌کنم", "همه طرفت شدن.", "بهتر می‌سازمش", "ویدیوت خیلی بهتر شد.", "نادیده می‌گیرم", "گذشت و ادامه دادی."),
        ("ایده‌ی تازه‌ای برای محتوا نداری.", "از فالوئرها می‌پرسم", "ایده‌های باحالی گرفتی.", "فیلم قدیمی می‌سازم", "جواب داد.", "یه چالش می‌سازم", "چالش وایرال شد."),
        ("یه شبکه‌ی تلویزیونی ازت مصاحبه می‌خواد.", "قبول می‌کنم", "مصاحبه‌ت عالی بود.", "شرط می‌ذارم", "شرطت پذیرفته شد.", "رد می‌کنم", "شبکه‌ی دیگه‌ای ازت دعوت کرد."),
    ],
    "رقاص": [
        ("وسط اجرا کفشت از پات دراومد.", "ادامه می‌دم", "مردم فکر کردن جزو برنامه‌ست.", "کفش رو برمی‌دارم", "همه خندیدن.", "کفش دیگه رو هم درمیارم", "برنامه‌ی جدیدی شروع شد."),
        ("موسیقی وسط اجرا قطع شد.", "بی‌صدا می‌رقصم", "تماشاگرها حبس نفس کردن.", "با دست ریتم می‌گیرم", "همه باهات هماهنگ شدن.", "منتظر می‌مونم", "موسیقی برگشت."),
        ("همرقصت یه حرکت رو اشتباه رفت.", "اصلاحش می‌کنم", "با هم هماهنگ شدین.", "ادامه می‌دم", "تماشاگرها نفهمیدن.", "می‌خندم", "اشتباه شد ولی قشنگ بود."),
        ("قبل اجرا کمرت درد گرفته.", "گرم می‌کنم", "درد کم شد و عالی اجرا کردی.", "داروی مسکن می‌خورم", "با آرامش اجرا کردی.", "کنسل می‌کنم", "اجرای بعدی بهتر شد."),
        ("داور مسابقه خیلی سخت‌گیره.", "با اعتماد می‌رقصم", "داور لبخند زد.", "یه حرکت جدید می‌زنم", "همه شوکه شدن!", "ساده می‌رقصم", "آروم و قشنگ بود."),
        ("صحنه لیز شده.", "با احتیاط می‌رقصم", "سالم موندی.", "پاک می‌کنم", "صحنه تمیز شد.", "ریسک می‌کنم", "لیز خوردی ولی با چرخش نجات پیدا کردی."),
        ("لباست قبل اجرا پاره شده.", "سنجاق می‌زنم", "کسی نفهمید.", "از لباس دیگه استفاده می‌کنم", "اجرا بدون مشکل بود.", "با همین می‌رقصم", "حرکتت اونقدر قشنگ بود که فراموش شد."),
        ("تمرین ساعت ۶ صبحه.", "بیدار می‌شم", "تمرینت عالی شد.", "دیر می‌رم", "نصف تمرین رو گذروندی.", "نمی‌رم", "عضلاتت تنبل شد."),
    ],
    "ناخن‌کار": [
        ("مشتری یه طرح خیلی پیچیده می‌خواد.", "قبول می‌کنم", "طرح فوق‌العاده شد.", "ساده‌ترش می‌کنم", "مشتری خوشش اومد.", "ازش وقت می‌خوام", "با دقت بیشتر کار کردی."),
        ("لاک روی دستت ریخت.", "پاک می‌کنم", "تمیز شد و ادامه دادی.", "استایل جدید می‌سازم", "یه طرح جدید به دست اومد.", "ولش می‌کنم", "لاک ریخته ولی مشتری نفهمید."),
        ("مشتری وسط کار نظرش رو عوض کرد.", "دوباره می‌کنم", "مشتری راضی شد.", "طرح جدید پیشنهاد می‌دم", "بهتر از اولیش شد.", "می‌گم دیره", "مشتری یه کم ناراضی شد."),
        ("مشتری دیر اومده و نوبت بعدی منتظره.", "سریع کار می‌کنم", "هر دو راضی بودن.", "نوبت رو جابجا می‌کنم", "همه‌چی درست شد.", "منتظرش می‌ذارم", "یه مشتری ناراحت شد."),
        ("ناخن مشتری شکسته.", "ترمیم می‌کنم", "ناخن مثل قبل شد.", "ناخن مصنوعی می‌ذارم", "ناخن قشنگ‌تر شد.", "کوتاهش می‌کنم", "مشتری راضی شد."),
        ("رنگ مورد نظر مشتری تموم شده.", "ترکیب می‌کنم", "رنگ جدیدی ساختی.", "سفارش می‌دم", "مشتری منتظر موند.", "رنگ دیگه پیشنهاد می‌دم", "مشتری قبول کرد."),
        ("مشتری یک ساعته حرف می‌زنه.", "گوش می‌دم", "مشتری کلی بهت انعام داد.", "کار می‌کنم و سر تکون می‌دم", "کارت دقیق شد.", "ساکتش می‌کنم", "مشتری یه کم ناراحت شد."),
        ("دستگاه فر خراب شده.", "درستش می‌کنم", "دوباره راه افتاد.", "از رفیقم قرض می‌گیرم", "کار تموم شد.", "ناخن رو طبیعی می‌ذارم", "مشتری قبول کرد."),
    ],
    "دندان‌پزشک": [
        ("یه بچه نمی‌خواد دهنش رو باز کنه.", "قصه می‌گم", "بچه آروم شد و دهنش رو باز کرد.", "آینه نشونش می‌دم", "بچه کنجکاو شد و همکاری کرد.", "هدیه می‌دم", "بچه با خوشحالی نشست."),
        ("بیمار از دندان‌پزشک می‌ترسه.", "آرومش می‌کنم", "ترسش ریخت.", "بی‌حسی می‌زنم", "بدون درد کار انجام شد.", "با شوخی حرف می‌زنم", "بیمار خندید."),
        ("دستگاه وسط کار خراب شد.", "تعمیرش می‌کنم", "راه افتاد.", "دستگاه دیگه می‌آرم", "کار ادامه پیدا کرد.", "نوبت می‌دم بعدا", "بیمار با رضایت رفت."),
        ("بیمار هیچ‌وقت مسواک نمی‌زنه.", "آموزش می‌دم", "یاد گرفت.", "یه مسواک بهش می‌دم", "ذوق کرد.", "ازش می‌خوام نشون بده", "با هم تمرین کردین."),
        ("پرونده‌ی یه بیمار گم شده.", "دنبالش می‌گردم", "پیدا شد.", "از اول می‌سازم", "پرونده‌ی جدید و منظم شد.", "از بیمار می‌پرسم", "همه‌چی یادش بود."),
        ("بیمار تنهاست و حوصله نداره.", "باهاش حرف می‌زنم", "با گفتگو آروم شد.", "موزیک می‌ذارم", "آهنگ آرومش کرد.", "سریع کار می‌کنم", "زود تموم شد."),
        ("بیمار وسط کار هم حرف می‌زنه.", "جوابش رو می‌دم", "کار آروم پیش رفت.", "می‌گم صبر کنه", "ساکت شد.", "نگاه می‌کنم", "بالاخره دهنش بسته شد."),
        ("یه بیمار اورژانسی رسید.", "سریع می‌گیرمش", "درد بیمار تموم شد.", "کمک می‌خوام", "همکارت اومد.", "نوبت می‌دم", "بیمار با اضطراب منتظر موند."),
    ],
    "داروخونه‌دار": [
        ("نسخه‌ی دکتر ناخوانا است.", "زنگ می‌زنم به دکتر", "دکتر توضیح داد و درست شد.", "با تجربه حدس می‌زنم", "درست حدس زدی.", "از همکار می‌پرسم", "همکارت کمک کرد."),
        ("مشتری بدون نسخه دارو قوی می‌خواد.", "نمی‌دم", "مشتری ناراحت ولی سالم موند.", "توضیح می‌دم", "مشتری فهمید.", "به دکتر ارجاع می‌دم", "مشتری رفت دکتر."),
        ("داروی مشتری تموم شده.", "جایگزین پیشنهاد می‌دم", "مشتری راضی شد.", "سفارش می‌دم", "مشتری فردا اومد.", "می‌گم دیگه نمی‌یاد", "مشتری دنبال داروخونه دیگه رفت."),
        ("مشتری برای سردرد مشاوره می‌خواد.", "سوال می‌پرسم", "علت پیدا شد.", "یه قرص ساده می‌دم", "سردردش خوب شد.", "می‌گم دکتر بره", "مشتری رفت دکتر."),
        ("یه داروی تاریخ گذشته پیدا کردی.", "دور می‌ندازم", "ایمنی حفظ شد.", "گزارش می‌دم", "انبار دوباره بررسی شد.", "بقیه رو چک می‌کنم", "چند داروی دیگه هم پیدا شد."),
        ("شیفت شبه و کسی نمیاد.", "کتاب می‌خونم", "چیزی جدید یاد گرفتی.", "قفسه‌ها رو مرتب می‌کنم", "داروخونه برق زد.", "می‌خوابم", "یه مشتری اورژانسی در رو زد."),
        ("یه پیرمرد کمک می‌خواد داروهاش رو بشناسه.", "یه لیست می‌نویسم", "پیرمرد خوشحال شد.", "توضیح شفاهی می‌دم", "خوب فهمید.", "برچسب می‌زنم", "همه‌چی منظم شد."),
        ("مشتری از قیمت‌ها شاکیه.", "توضیح می‌دم", "قانع شد.", "جایگزین ارزون‌تر پیشنهاد می‌دم", "خوشحال شد.", "همدردی می‌کنم", "ساکت شد."),
    ],
    "شغل مرموز": [
        ("یه نفر یه پاکت ناشناس بهت داده.", "بازش می‌کنم", "داخلش یه نامه‌ی عجیب بود.", "نگهش می‌دارم", "فردا بازش کردی.", "به کسی نمی‌گم", "راز خودت موند."),
        ("یه پیام رمزی روی گوشیت اومده.", "رمزگشایی می‌کنم", "پیام یه آدرس بود.", "جواب می‌دم", "یه نفر ناشناس جواب داد.", "پاک می‌کنم", "پیام برگشت."),
        ("حس می‌کنی کسی تعقیبت می‌کنه.", "برمی‌گردم", "هیچ‌کس نبود.", "سریع می‌رم", "تعقیب‌کننده گم شد.", "جلوش می‌ایستم", "یه شخص خجالت‌زده فرار کرد."),
        ("یه کیف قفل‌شده پیدا کردی.", "قفلش رو باز می‌کنم", "داخلش یه نقشه بود.", "به پلیس می‌دم", "همه‌چی حل شد.", "نگهش می‌دارم", "هنوز راز موند."),
        ("یه نقاب مرموز تو کشوت پیدا کردی.", "می‌پوشمش", "حس عجیبی بهت داد.", "جاش می‌ذارم", "دوباره پنهون شد.", "دور می‌اندازمش", "فردا همونجا بود!"),
        ("چراغ‌ها یهو خاموش شد.", "فندک روشن می‌کنم", "همه‌چی روشن شد.", "منتظر می‌مونم", "برق برگشت.", "سکوت می‌کنم", "یه صدا از تاریکی اومد."),
        ("دوربین مداربسته یه چیز عجیب نشون می‌ده.", "بازش می‌کنم", "یه سایه دیدی.", "گزارش می‌دم", "امنیت چک شد.", "نادیده می‌گیرم", "چیزی نبود."),
        ("یه نقشه‌ی گنج پیدا کردی.", "دنبالش می‌رم", "یه صندوقچه پیدا کردی!", "به دوستم نشون می‌دم", "با هم دنبالش رفتین.", "قابش می‌کنم", "یه تابلوی خفن شد."),
    ],
}
for _job, _rows in SHIFT_QS_EXTRA.items():
    SHIFT_QS[_job] = list(SHIFT_QS.get(_job, [])) + [
        (r[0], [(r[1], r[2]), (r[3], r[4]), (r[5], r[6])]) for r in _rows
    ]

SHIFT_DEFAULT_QS = SHIFT_QS["دانشجو"]


def parse_pay(s: str) -> int:
    s = s.strip().upper()
    mult = 1_000_000 if s.endswith("M") else 1_000 if s.endswith("K") else 1
    return int(float(s.rstrip("KM")) * mult)


def current_job(gender: str, jaghs: int):
    """بالاترین شغلی که کاربر واقعاً داره (طبق جق‌ها و جدول شغل‌ها) → (ایندکس، اسم) یا None."""
    best = None
    for i, (name, _lvl) in enumerate(JOBS[gender]):
        if jaghs >= JOB_REQUIRED_JAGHS[i]:
            best = (i, name)
    return best


def shift_header(name: str) -> str:
    return f"{_te(SHIFT_EMOJI_START, '💼')} <b>شیفت {html.escape(name)}</b>"


async def show_shift(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """نوشتن «کار» یا «شیفت» — یه شیفت ۲۰ دقیقه‌ای با سوال ۳ گزینه‌ای."""
    msg, user = update.effective_message, update.effective_user
    await upsert_user(user.id, user.full_name)
    row = await get_user(user.id)
    if not row["gender"]:
        await ask_gender(update, context)
        return
    rp = ReplyParameters(message_id=msg.message_id, allow_sending_without_reply=True)
    left = SHIFT_COOLDOWN - (time.time() - row["last_shift"])
    if left > 0:
        m, s = divmod(int(left), 60)
        await msg.reply_text(
            B(f"{ce('jagh', 6, '⏳')} شیفت بعدیت {m} دقیقه و {s} ثانیه دیگه‌ست."),
            parse_mode="HTML", reply_parameters=rp,
        )
        return
    job = current_job(row["gender"], row["jaghs"])
    if job is None:
        need = JOB_REQUIRED_JAGHS[0] - row["jaghs"]
        await msg.reply_text(
            B(f"{ce('me', 1, '🎖')} هنوز شغلی نداری! {need:,} جق دیگه بزن تا اولین شغلت رو بگیری."),
            parse_mode="HTML", reply_parameters=rp,
        )
        return
    _, name = job
    qs = SHIFT_QS.get(name, SHIFT_DEFAULT_QS)
    # هر بار یه سوال که تا حالا برای این شغل نیومده؛ وقتی همه‌ی ۱۰ تا اومد از اول
    seen_job, _, seen_s = (row["shift_seen"] or "").partition("|")
    seen = {int(x) for x in seen_s.split(",") if x.isdigit()} if seen_job == name else set()
    pool_idx = [i for i in range(len(qs)) if i not in seen] or list(range(len(qs)))
    if len(pool_idx) == len(qs):
        seen = set()
    qi = random.choice(pool_idx)
    seen.add(qi)
    await pool.execute("UPDATE users SET shift_seen = $1 WHERE user_id = $2", f"{name}|{','.join(map(str, sorted(seen)))}", user.id)
    question, options = qs[qi]
    order = list(range(len(options)))
    random.shuffle(order)
    kb = InlineKeyboardMarkup(
        [[btn(options[o][0], "primary", callback_data=f"sh:{user.id}:{qi}:{o}")] for o in order]
    )
    text = f"{shift_header(name)}\n\n{html.escape(question)}"
    try:
        await msg.reply_text(text, parse_mode="HTML", reply_markup=kb, reply_parameters=rp)
    except Exception:
        log.exception("shift panel failed, retrying without premium emojis")
        await msg.reply_text(_strip_te(text), parse_mode="HTML", reply_markup=kb, reply_parameters=rp)


async def shift_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, uid, qi, oi = q.data.split(":")
    if q.from_user.id != int(uid):
        await q.answer("این شیفت مال تو نیست 😅", show_alert=True)
        return
    row = await get_user(q.from_user.id)
    if not row or not row["gender"]:
        await q.answer()
        return
    job = current_job(row["gender"], row["jaghs"])
    if job is None:
        await q.answer("هنوز شغلی نداری!", show_alert=True)
        return
    idx, name = job
    qs = SHIFT_QS.get(name, SHIFT_DEFAULT_QS)
    qi, oi = int(qi), int(oi)
    if qi >= len(qs) or oi >= len(qs[qi][1]):
        await q.answer("این شیفت دیگه معتبر نیست.", show_alert=True)
        return
    lo, hi = (parse_pay(x) for x in JOB_PAY[idx])
    pay = random.randint(lo // 1000, hi // 1000) * 1000
    now = time.time()
    new_bal = await pool.fetchval(
        "UPDATE users SET balance = balance + $1::bigint, last_shift = $2::double precision "
        "WHERE user_id = $3::bigint AND last_shift <= $2::double precision - $4::double precision "
        "RETURNING balance",
        pay, now, q.from_user.id, float(SHIFT_COOLDOWN),
    )
    if new_bal is None:
        await q.answer("هنوز وقت شیفت بعدیت نرسیده!", show_alert=True)
        return
    done = qs[qi][1][oi][1]
    text = (
        f"{shift_header(name)}\n\n"
        f"<blockquote><b>{html.escape(done)}\n\n"
        f"{_te(SHIFT_EMOJI_DONE, '💰')} حقوق این شیفت: {pay:,} تومن</b></blockquote>"
    )
    await q.answer()
    try:
        await q.edit_message_text(text, parse_mode="HTML")
    except Exception:
        log.exception("shift result failed, retrying without premium emojis")
        try:
            await q.edit_message_text(_strip_te(text), parse_mode="HTML")
        except Exception:
            log.exception("shift result edit failed again")


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


# ───────────────────────── کازینو: تاس و اسلات ─────────────────────────
# «کازینو» پنل رو باز می‌کنه: تاس / اسلات ← ۱ نفره / ۲ نفره ← مبلغ ← بازی؛ همه‌چی تو همون پنل.
# میان‌بر متنی: «تاس 3 100هزار»، «اسلات 100هزار»، «میز تاس 100هزار»، «میز اسلات 100هزار».
CASINO_MIN_BET = 1_000
DICE_NET = 5          # حدس درست تاس: سود ۵ برابر شرط (کلاً ۶ برابر برمی‌گرده)
CASINO_WAIT = 3.8     # صبر برای تموم شدن انیمیشن تاس/اسلات
DICE_DELETE_AFTER = 5  # چند ثانیه بعد از اعلام نتیجه، تاس/اسلات‌های فرستاده‌شده پاک میشن
TABLE_TTL = 600       # میز بعد از ۱۰ دقیقه منقضی میشه
BET_CHOICES = [10_000, 50_000, 100_000, 500_000, 1_000_000, 5_000_000]
MINES_COUNT = 3
MINES_CELLS = 21
MINES_BOMB_EID = "5256147609243889028"
MINES_TILE_EID = "5256156224948284902"
MINES_CASHOUT_EID = "5381992813896412558"
MINES_BOMB_CE = f'<tg-emoji emoji-id="{MINES_BOMB_EID}">💣</tg-emoji>'
MINES_TILE_CE = f'<tg-emoji emoji-id="{MINES_TILE_EID}">▢</tg-emoji>'
MINES_CASHOUT_CE = f'<tg-emoji emoji-id="{MINES_CASHOUT_EID}">💰</tg-emoji>'

# اسلات: امتیاز هر نماد (BAR = 🔔). جمع امتیاز سه چرخ → ضریب پرداخت.
SLOT_POINTS = [0, 13, 18, 22]  # 🔔، 🍇، 🍋، 7️⃣
# (حداقل امتیاز، ضریب) از بالا به پایین؛ ضریب × شرط برمی‌گرده (۰ = باخت، ۱ = پولت برمی‌گرده)
SLOT_TIERS = [(60, 2.0), (55, 1.8), (45, 1.7), (35, 1.5), (25, 1.2), (18, 1.0)]

DICE_EID = "5123323069850452850"
DICE_CE = f'<tg-emoji emoji-id="{DICE_EID}">🎲</tg-emoji>'
SLOT_SYMS = [
    "🔔",
    '<tg-emoji emoji-id="4961065085217801230">🍇</tg-emoji>',
    '<tg-emoji emoji-id="5348476388062015170">🍋</tg-emoji>',
    '<tg-emoji emoji-id="5915988541644475488">7️⃣</tg-emoji>',
]
GAME_EMOJI = {"dice": "🎲", "slot": "🎰"}  # ایموجی خودِ تلگرام برای sendDice
GAME_FA = {"dice": f"تاس {DICE_CE}", "slot": "اسلات 🎰", "mines": f"مین {MINES_BOMB_CE}"}


async def send_html(bot, chat_id: int, text: str, **kw):
    """HTML می‌فرسته؛ اگه ایموجی پرمیوم قبول نشد بدون اون می‌فرسته."""
    try:
        return await bot.send_message(chat_id, text, parse_mode="HTML", **kw)
    except Exception:
        log.exception("send_html failed, retrying without premium emojis")
        return await bot.send_message(chat_id, re.sub(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", r"\1", text), parse_mode="HTML", **kw)


async def edit_html(q, text: str, reply_markup=None):
    try:
        await q.edit_message_text(text, parse_mode="HTML", reply_markup=reply_markup)
    except Exception as e:
        if "not modified" in str(e).lower():
            return
        log.exception("edit_html failed, retrying without premium emojis")
        try:
            await q.edit_message_text(re.sub(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", r"\1", text), parse_mode="HTML", reply_markup=reply_markup)
        except Exception:
            log.exception("edit_html failed again")


def delete_later(bot, chat_id: int, ids: list[int], delay: float = DICE_DELETE_AFTER):
    """تاس/اسلات‌های فرستاده‌شده رو چند ثانیه بعد از نتیجه پاک می‌کنه."""
    async def _run():
        await asyncio.sleep(delay)
        for mid in ids:
            try:
                await bot.delete_message(chat_id, mid)
            except Exception:
                log.warning("dice delete failed", exc_info=True)
    asyncio.create_task(_run())


def slot_reels(v: int) -> list[int]:
    v -= 1
    return [v % 4, (v // 4) % 4, v // 16]


def slot_points(v: int) -> int:
    return sum(SLOT_POINTS[i] for i in slot_reels(v))


def slot_mult(points: int) -> float:
    for need, mult in SLOT_TIERS:
        if points >= need:
            return mult
    return 0.0


def slot_text(v: int) -> str:
    return " ".join(SLOT_SYMS[i] for i in slot_reels(v))


def _mult_txt(m: float) -> str:
    return f"{m:g}"


async def _take(uid: int, amt: int):
    return await pool.fetchval(
        "UPDATE users SET balance = balance - $1::bigint WHERE user_id = $2::bigint AND balance >= $1::bigint RETURNING balance",
        amt, uid,
    )


async def _give(uid: int, amt: int):
    await pool.execute("UPDATE users SET balance = balance + $1::bigint WHERE user_id = $2::bigint", amt, uid)


def _uname(user) -> str:
    return html.escape(user.full_name)


def _ulink(uid: int, name: str) -> str:
    return f'<a href="tg://user?id={uid}">{html.escape(name or "کاربر")}</a>'


def _fa_bet(n: int) -> str:
    return f"{n // 1_000_000} میلیون" if n >= 1_000_000 else f"{n // 1000} هزار"


# ───── پنل ─────
def panel_main(uid: int):
    text = B("🎰 کازینو\n\nیکی از بازی‌ها رو انتخاب کن 👇")
    kb = InlineKeyboardMarkup([[
        btn("تاس", "primary", icon=DICE_EID, callback_data=f"cz:g:{uid}:dice"),
        btn("🎰 اسلات", "primary", callback_data=f"cz:g:{uid}:slot"),
        btn("مین", "primary", icon=MINES_BOMB_EID, callback_data=f"cz:g:{uid}:mines"),
    ]])
    return text, kb


def panel_game(uid: int, game: str):
    if game == "dice":
        rules = (f"{DICE_CE} تاس\n\n۱ نفره: یه عدد ۱ تا ۶ حدس می‌زنی؛ درست بیاد {DICE_NET + 1} برابر پولت رو می‌گیری.\n"
                 f"۲ نفره: یه میز تو گروه باز می‌شه، نفر دوم میاد؛ هر کی تاس بالاتر بندازه کل پول رو می‌بره.")
    elif game == "mines":
        rules = (f"{MINES_BOMB_CE} مین — بازی تک‌نفره\n\n۲۱ خانه وجود داره و ۳ خانه بمبه. هر خانه امن ضریب رو بیشتر می‌کنه. با پیدا کردن بمب شرطت از دست میره؛ هر زمان خواستی برداشت کن تا شرط × ضریب به حسابت برگرده. حداکثر ضریب ۵ برابره.")
    else:
        rules = ("🎰 اسلات\n\nهر نماد امتیاز داره: 🔔 صفر | 🍇 ۱۳ | 🍋 ۱۸ | 7️⃣ ۲۲\nجمع امتیاز سه چرخ ضریبت رو تعیین می‌کنه:\n"
                 + "\n".join(f"• {need}+ امتیاز ← ضریب {_mult_txt(m)}" for need, m in SLOT_TIERS)
                 + "\n• زیر ۱۸ ← ضریب ۰ (می‌بازی)\n\n۲ نفره: هر کی امتیاز بیشتر بیاره کل پول رو می‌بره.")
    kb = InlineKeyboardMarkup([
        ([btn("👤 ۱ نفره", "primary", callback_data=f"cz:a:{uid}:{game}:1")] if game == "mines" else [btn("👤 ۱ نفره", "primary", callback_data=f"cz:a:{uid}:{game}:1"),
         btn("👥 ۲ نفره", "success", callback_data=f"cz:a:{uid}:{game}:2")]),
        [btn("برگشت", None, icon=EID_BACK, callback_data=f"cz:m:{uid}")],
    ])
    return B(rules), kb


def panel_amounts(uid: int, game: str, mode: str):
    who = "۱ نفره" if mode == "1" else "۲ نفره"
    if mode == "1":
        pre = "n" if game == "dice" else ("pm" if game == "mines" else "ps")
    else:
        pre = "td" if game == "dice" else "ts"
    rows, row = [], []
    for amt in BET_CHOICES:
        row.append(btn(_fa_bet(amt), "primary", callback_data=f"cz:{pre}:{uid}:{amt}"))
        if len(row) == 3:
            rows.append(row); row = []
    if row:
        rows.append(row)
    rows.append([btn("✏️ مبلغ دلخواه", "success", callback_data=f"cz:custom:{uid}:{game}:{mode}")])
    rows.append([btn("برگشت", None, icon=EID_BACK, callback_data=f"cz:g:{uid}:{game}")])
    text = B(f"{GAME_FA[game]} — {who}\n\nمبلغ شرط رو انتخاب کن (تومن):\n(یا مبلغ دلخواه رو وارد کن)")
    return text, InlineKeyboardMarkup(rows)


def panel_number(uid: int, amt: int):
    nums = [btn(str(i), "primary", callback_data=f"cz:pd:{uid}:{amt}:{i}") for i in range(1, 7)]
    kb = InlineKeyboardMarkup([nums[:3], nums[3:], [btn("برگشت", None, icon=EID_BACK, callback_data=f"cz:a:{uid}:dice:1")]])
    return B(f"{DICE_CE} تاس ۱ نفره — شرط {amt:,} تومن\n\nکدوم عدد میاد؟ یکی رو انتخاب کن 👇"), kb


# ───── بازی تکی ─────
def _busy(context) -> set:
    return context.application.bot_data.setdefault("casino_busy", set())


async def play_single(bot, context, chat_id: int, user, game: str, bet: int, pick: int | None = None, reply_to: int | None = None, q=None):
    """q = پیام پنل (callback). اگه باشه نتیجه توی همون پنل نشون داده میشه، نه پیام جدید."""
    rp = ReplyParameters(message_id=reply_to, allow_sending_without_reply=True) if reply_to else None
    again_kb = None
    if q is not None:
        again_kb = InlineKeyboardMarkup([
            [btn("🔄 دوباره", "success", callback_data=f"cz:a:{user.id}:{game}:1")],
            [btn("برگشت", None, icon=EID_BACK, callback_data=f"cz:m:{user.id}")],
        ])

    async def say(text, kb=None):
        if q is not None:
            await edit_html(q, text, kb if kb is not None else again_kb)
        else:
            await send_html(bot, chat_id, text, reply_parameters=rp)

    if bet < CASINO_MIN_BET:
        await say(BP(f"حداقل شرط {CASINO_MIN_BET:,} تومنه."))
        return
    busy = _busy(context)
    if user.id in busy:
        await say(BP("صبر کن بازی قبلیت تموم شه 😅"))
        return
    await upsert_user(user.id, user.full_name)
    if await _take(user.id, bet) is None:
        row = await get_user(user.id)
        await say(B(f"{ce('wallet', 1, '💰')} دارایی‌ت کافی نیست! موجودی: {row['balance']:,} تومن"))
        return
    busy.add(user.id)
    try:
        if q is not None:
            await edit_html(q, B(f"{GAME_FA[game]} — شرط {bet:,} تومن\n\n{HG_CE} داره می‌چرخه..."), InlineKeyboardMarkup([]))
        try:
            m = await bot.send_dice(chat_id, emoji=GAME_EMOJI[game], reply_parameters=rp)
            v = m.dice.value
        except Exception:
            log.exception("send_dice failed")
            await _give(user.id, bet)
            await say(BP("خطا تو انداختن تاس؛ پولت برگشت."))
            return
        await asyncio.sleep(CASINO_WAIT)
        name = _uname(user)
        if game == "dice":
            if v == pick:
                await _give(user.id, bet * (1 + DICE_NET))
                text = f"{DICE_CE} تاس: {v} (حدست {pick})\n🎉 {name} بردی! +{bet * DICE_NET:,} تومن سود"
            else:
                text = f"{DICE_CE} تاس: {v} (حدست {pick})\n😢 {name} باختی! -{bet:,} تومن"
        else:
            pts = slot_points(v)
            mult = slot_mult(pts)
            payout = int(bet * mult)
            if payout > 0:
                await _give(user.id, payout)
            diff = payout - bet
            if diff > 0:
                res = f"🎉 {name} بردی! +{diff:,} تومن سود"
            elif diff == 0:
                res = f"🤝 {name} پولت برگشت"
            else:
                res = f"😢 {name} باختی! {diff:,} تومن"
            text = f"🎰 {slot_text(v)}\nامتیاز: {pts} ← ضریب {_mult_txt(mult)}\n{res}"
        row = await get_user(user.id)
        await say(B(text + f"\n{ce('wallet', 1, '💰')} دارایی: {row['balance']:,}"))
        delete_later(bot, chat_id, [m.message_id])
    finally:
        busy.discard(user.id)


# ───── میز دو نفره ─────
async def create_table(bot, chat_id: int, user, game: str, bet: int, reply_to: int | None = None, q=None):
    rp = ReplyParameters(message_id=reply_to, allow_sending_without_reply=True) if reply_to else None
    back_kb = InlineKeyboardMarkup([[btn("برگشت", None, icon=EID_BACK, callback_data=f"cz:m:{user.id}")]]) if q is not None else None

    async def say(text, kb=None):
        if q is not None:
            await edit_html(q, text, kb or back_kb)
        else:
            await send_html(bot, chat_id, text, reply_parameters=rp)

    if chat_id > 0:
        await say(BP("میز دو نفره فقط تو گروه ممکنه."))
        return
    if bet < CASINO_MIN_BET:
        await say(BP(f"حداقل شرط {CASINO_MIN_BET:,} تومنه."))
        return
    await upsert_user(user.id, user.full_name)
    row = await get_user(user.id)
    if row["balance"] < bet:
        await say(B(f"{ce('wallet', 1, '💰')} دارایی‌ت کافی نیست! موجودی: {row['balance']:,} تومن"))
        return
    k = "d" if game == "dice" else "s"
    kb = InlineKeyboardMarkup([[
        btn("✅ می‌شینم", "success", callback_data=f"cz:join:{k}:{user.id}:{bet}"),
        btn("❌ بستن میز", "danger", callback_data=f"cz:close:{user.id}"),
    ]])
    await say(
        f"<b>{GAME_FA[game]} — میز دو نفره\n\n👤 {_ulink(user.id, user.full_name)}\n{MONEY_CE} شرط: {bet:,} تومن\n\n"
        f"منتظر نفر دوم... (برنده کل {bet * 2:,} تومن رو می‌بره)</b>",
        kb,
    )


def mines_multiplier(safe_count: int) -> float:
    """ضریب پلکانی تا ۵ برابر در آخرین خانه امن؛ ۳ بمب در ۲۱ خانه."""
    if safe_count <= 0:
        return 1.0
    return round(1.0 + 4.0 * (safe_count / (MINES_CELLS - MINES_COUNT)) ** 1.35, 2)


def mines_keyboard(uid: int, game_id: str, opened: set[int], bombs: set[int], finished=False, can_cash=True):
    rows = []
    for r in range(3):
        row = []
        for c in range(7):
            i = r * 7 + c
            if i in opened:
                label = "💣" if i in bombs else "💎"
                row.append(btn(label, "danger" if i in bombs else "success", callback_data=f"cz:mx:{uid}:{game_id}:{i}"))
            elif finished and i in bombs:
                row.append(btn("💣", "danger", icon=MINES_BOMB_EID, callback_data=f"cz:mx:{uid}:{game_id}:{i}"))
            elif finished:
                row.append(btn("·", None, callback_data=f"cz:mx:{uid}:{game_id}:{i}"))
            else:
                row.append(btn("خانه", "primary", icon=MINES_TILE_EID, callback_data=f"cz:mx:{uid}:{game_id}:{i}"))
        rows.append(row)
    if can_cash and not finished:
        rows.append([btn("برداشت", "success", icon=MINES_CASHOUT_EID, callback_data=f"cz:mc:{uid}:{game_id}")])
    return InlineKeyboardMarkup(rows)


async def start_mines(bot, context, chat_id, user, bet, q=None, reply_to=None):
    if bet < CASINO_MIN_BET:
        text = BP(f"حداقل شرط {CASINO_MIN_BET:,} تومنه.")
        if q: await edit_html(q, text)
        else: await send_html(bot, chat_id, text)
        return
    busy = _busy(context)
    if user.id in busy:
        text = BP("اول بازی قبلیت رو تموم کن.")
        if q: await edit_html(q, text)
        else: await send_html(bot, chat_id, text)
        return
    await upsert_user(user.id, user.full_name)
    if await _take(user.id, bet) is None:
        row = await get_user(user.id)
        text = B(f"موجودی کافی نیست؛ موجودی فعلی: {row['balance']:,} تومن")
        if q: await edit_html(q, text)
        else: await send_html(bot, chat_id, text)
        return
    import uuid
    game_id = uuid.uuid4().hex[:8]
    state = {"uid": user.id, "bet": bet, "bombs": set(random.sample(range(MINES_CELLS), MINES_COUNT)), "opened": set(), "chat_id": chat_id, "message_id": q.message.message_id if q else None, "finished": False}
    context.application.bot_data.setdefault("mines_games", {})[game_id] = state
    busy.add(user.id)
    text = f"{MINES_BOMB_CE} بازی مین\nشرط: {bet:,} تومن\n۳ بمب بین ۲۱ خانه پنهانه. خانه‌های امن رو باز کن و هر وقت خواستی برداشت بزن.\nضریب فعلی: ۱×"
    kb = mines_keyboard(user.id, game_id, state["opened"], state["bombs"])
    if q: await edit_html(q, B(text), kb)
    else: await send_html(bot, chat_id, B(text), reply_parameters=ReplyParameters(message_id=reply_to, allow_sending_without_reply=True) if reply_to else None, reply_markup=kb)


async def mines_cb(update: Update, context: ContextTypes.DEFAULT_TYPE, parts):
    q = update.callback_query
    _, action, uid_s, game_id, *tail = parts
    uid = int(uid_s)
    if q.from_user.id != uid:
        await q.answer("این بازی مال تو نیست.", show_alert=True); return
    games = context.application.bot_data.setdefault("mines_games", {})
    st = games.get(game_id)
    if not st or st["finished"]:
        await q.answer("این بازی تموم شده.", show_alert=True); return
    if action == "mx":
        idx = int(tail[0])
        if idx < 0 or idx >= MINES_CELLS or idx in st["opened"]:
            await q.answer("این خانه قبلاً باز شده.", show_alert=True); return
        if idx in st["bombs"]:
            st["finished"] = True
            _busy(context).discard(uid)
            kb = mines_keyboard(uid, game_id, st["opened"] | {idx}, st["bombs"], True, False)
            await q.answer("بمب! بازی تموم شد.", show_alert=True)
            await edit_html(q, B(f"{MINES_BOMB_CE} بمب پیدا کردی و باختی!\nشرط {st['bet']:,} تومن از دست رفت.\nمحل هر سه بمب مشخص شد."), kb)
            return
        st["opened"].add(idx)
        count = len(st["opened"]); mult = mines_multiplier(count)
        if count >= MINES_CELLS - MINES_COUNT:
            st["finished"] = True; _busy(context).discard(uid)
            payout = int(st["bet"] * 5)
            await _give(uid, payout)
            row = await get_user(uid)
            await q.answer("همه خانه‌های امن باز شد!")
            await edit_html(q, B(f"🏆 همه ۱۸ خانه امن رو باز کردی!\nضریب نهایی: ۵×\nپرداخت: {payout:,} تومن\nموجودی: {row['balance']:,}\nمحل بمب‌ها مشخص شد."), mines_keyboard(uid, game_id, st["opened"], st["bombs"], True, False))
            return
        await q.answer(f"خانه امن! ضریب {mult:g}×")
        await edit_html(q, B(f"{MINES_BOMB_CE} مین\nشرط: {st['bet']:,} تومن\nخانه امن: {count} از ۱۸\nضریب برداشت: {mult:g}×\nمبلغ دریافتی: {int(st['bet']*mult):,} تومن"), mines_keyboard(uid, game_id, st["opened"], st["bombs"]))
    elif action == "mc":
        st["finished"] = True; _busy(context).discard(uid)
        mult = mines_multiplier(len(st["opened"]))
        payout = int(st["bet"] * mult)
        await _give(uid, payout)
        row = await get_user(uid)
        await q.answer("برداشت انجام شد.")
        await edit_html(q, B(f"{MINES_CASHOUT_CE} برداشت موفق!\nخانه امن: {len(st['opened'])}\nضریب: {mult:g}×\nشرط: {st['bet']:,} تومن\nدریافتی: {payout:,} تومن\nموجودی: {row['balance']:,}\n\nمحل هر سه بمب مشخص شد."), mines_keyboard(uid, game_id, st["opened"], st["bombs"], True, False))


async def casino_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    bot = context.bot
    parts = q.data.split(":")
    act = parts[1]
    if act in ("mx", "mc"):
        await mines_cb(update, context, parts)
        return
    chat_id = q.message.chat_id
    user = q.from_user

    # ── میز: نشستن / بستن (هر کسی) ──
    if act in ("join", "close"):
        await table_cb(update, context, parts)
        return

    # ── پنل: فقط صاحب پنل ──
    uid = int(parts[2])
    if user.id != uid:
        await q.answer("این پنل مال تو نیست؛ خودت بنویس «کازینو» 😅", show_alert=True)
        return
    if act == "m":
        await q.answer()
        text, kb = panel_main(uid)
        await edit_html(q, text, kb)
    elif act == "g":
        await q.answer()
        text, kb = panel_game(uid, parts[3])
        await edit_html(q, text, kb)
    elif act == "a":
        await q.answer()
        text, kb = panel_amounts(uid, parts[3], parts[4])
        await edit_html(q, text, kb)
    elif act == "custom":
        game, mode = parts[3], parts[4]
        if game == "mines" and mode != "1":
            await q.answer("مین فقط یک‌نفره است.", show_alert=True); return
        context.user_data["casino_custom_bet"] = (game, mode)
        await q.answer()
        await edit_html(q, B(f"مبلغ دلخواه شرط {GAME_FA[game]} رو به تومان بفرست. مثال: 250000 یا ۲۵۰ هزار"), InlineKeyboardMarkup([[btn("لغو", None, callback_data=f"cz:g:{uid}:{game}")]]))
    elif act == "n":  # تاس ۱ نفره: انتخاب عدد
        await q.answer()
        text, kb = panel_number(uid, int(parts[3]))
        await edit_html(q, text, kb)
    elif act == "pd":
        await q.answer()
        await play_single(bot, context, chat_id, user, "dice", int(parts[3]), int(parts[4]), q.message.message_id, q=q)
    elif act == "pm":
        await q.answer()
        await start_mines(bot, context, chat_id, user, int(parts[3]), q=q)
    elif act == "ps":
        await q.answer()
        await play_single(bot, context, chat_id, user, "slot", int(parts[3]), None, q.message.message_id, q=q)
    elif act in ("td", "ts"):
        await q.answer()
        await create_table(bot, chat_id, user, "dice" if act == "td" else "slot", int(parts[3]), q=q)
    else:
        await q.answer()


async def table_cb(update: Update, context: ContextTypes.DEFAULT_TYPE, parts: list[str]):
    q = update.callback_query
    bot = context.bot
    act = parts[1]
    if act in ("mx", "mc"):
        await mines_cb(update, context, parts)
        return
    chat_id = q.message.chat_id
    user = q.from_user
    done = context.application.bot_data.setdefault("cz_done", set())
    key = (chat_id, q.message.message_id)
    if act == "close":
        if user.id != int(parts[2]) and user.id not in ADMIN_IDS:
            await q.answer("فقط صاحب میز می‌تونه ببنده", show_alert=True)
            return
        if key in done:
            await q.answer("این میز دیگه فعال نیست", show_alert=True)
            return
        done.add(key)
        await q.answer()
        await q.edit_message_text("❌ میز بسته شد.")
        return
    game = "dice" if parts[2] == "d" else "slot"
    creator, bet = int(parts[3]), int(parts[4])
    if user.id == creator:
        await q.answer("نمی‌تونی با خودت بازی کنی 😅", show_alert=True)
        return
    if key in done:
        await q.answer("این میز دیگه فعال نیست", show_alert=True)
        return
    try:
        age = time.time() - q.message.date.timestamp()
    except Exception:
        age = 0
    if age > TABLE_TTL:
        done.add(key)
        await q.answer("میز منقضی شده", show_alert=True)
        await q.edit_message_text("⌛ میز منقضی شد.")
        return
    done.add(key)  # همون لحظه رزرو میشه تا دو نفر همزمان نشینن
    await upsert_user(user.id, user.full_name)

    class _Broke(Exception):
        pass

    try:
        async with pool.acquire() as c:
            async with c.transaction():
                ok1 = await c.fetchval(
                    "UPDATE users SET balance = balance - $1::bigint WHERE user_id = $2::bigint AND balance >= $1::bigint RETURNING 1", bet, creator)
                if not ok1:
                    raise _Broke("creator")
                ok2 = await c.fetchval(
                    "UPDATE users SET balance = balance - $1::bigint WHERE user_id = $2::bigint AND balance >= $1::bigint RETURNING 1", bet, user.id)
                if not ok2:
                    raise _Broke("joiner")
    except _Broke as e:
        if str(e) == "joiner":
            done.discard(key)  # میز هنوز بازه
            await q.answer("دارایی‌ت برای این شرط کافی نیست!", show_alert=True)
        else:
            await q.answer("دارایی صاحب میز کافی نیست", show_alert=True)
            await q.edit_message_text("❌ دارایی صاحب میز کم شده؛ میز بسته شد.")
        return
    except Exception:
        done.discard(key)
        log.exception("table join failed")
        await q.answer("خطا، دوباره امتحان کن.", show_alert=True)
        return

    await q.answer("بازی شروع شد!")
    cname = await pool.fetchval("SELECT name FROM users WHERE user_id = $1", creator) or "کاربر"
    a, b = _ulink(creator, cname), _ulink(user.id, user.full_name)
    await edit_html(q, f"<b>{GAME_FA[game]} — بازی شروع شد!\n\n{a} {VS_CE} {b}\n{MONEY_CE} شرط هر نفر: {bet:,} تومن</b>")
    rp = ReplyParameters(message_id=q.message.message_id, allow_sending_without_reply=True)
    try:
        m1 = await bot.send_dice(chat_id, emoji=GAME_EMOJI[game], reply_parameters=rp)
        m2 = await bot.send_dice(chat_id, emoji=GAME_EMOJI[game], reply_parameters=rp)
        v1, v2 = m1.dice.value, m2.dice.value
    except Exception:
        log.exception("table dice failed, refunding")
        await _give(creator, bet)
        await _give(user.id, bet)
        await bot.send_message(chat_id, BP("خطا تو انداختن تاس؛ پول هر دو نفر برگشت."), parse_mode="HTML")
        return
    await asyncio.sleep(CASINO_WAIT)
    if game == "dice":
        s1, s2 = v1, v2
        r1, r2 = f"{DICE_CE} {v1}", f"{DICE_CE} {v2}"
    else:
        s1, s2 = slot_points(v1), slot_points(v2)
        r1, r2 = f"🎰 {slot_text(v1)} ({s1} امتیاز)", f"🎰 {slot_text(v2)} ({s2} امتیاز)"
    if s1 == s2:
        await _give(creator, bet)
        await _give(user.id, bet)
        result = "🤝 مساوی شد؛ پول هر دو نفر برگشت."
    else:
        win_id, win_link = (creator, a) if s1 > s2 else (user.id, b)
        await _give(win_id, bet * 2)
        result = f"{TROPHY_CE} برنده: {win_link}\n{MONEY_CE} {bet * 2:,} تومن به حسابش رفت!"
    await edit_html(q, f"<b>{GAME_FA[game]} — نتیجه\n\n{a}: {r1}\n{b}: {r2}\n\n{result}</b>")
    delete_later(bot, chat_id, [m1.message_id, m2.message_id])


async def casino_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """متن‌های کازینو رو تشخیص میده. True = مصرف شد."""
    msg, user = update.effective_message, update.effective_user
    t = norm_word(text).translate(_DIG)
    bot, chat_id = context.bot, msg.chat_id
    rp = ReplyParameters(message_id=msg.message_id, allow_sending_without_reply=True)
    custom_choice = context.user_data.pop("casino_custom_bet", None)
    if custom_choice:
        custom_game, custom_mode = custom_choice
        amt = parse_amount(t)
        if amt < CASINO_MIN_BET:
            context.user_data["casino_custom_bet"] = custom_choice
            await msg.reply_text(BP(f"مبلغ معتبر نیست؛ حداقل {CASINO_MIN_BET:,} تومنه. دوباره بفرست."), parse_mode="HTML")
        elif custom_mode == "2":
            await create_table(bot, chat_id, user, custom_game, amt, msg.message_id)
        elif custom_game == "mines":
            await start_mines(bot, context, chat_id, user, amt, reply_to=msg.message_id)
        elif custom_game == "dice":
            context.user_data["casino_custom_dice"] = amt
            await msg.reply_text(BP("حالا عدد حدسیت رو بفرست: ۱ تا ۶"), parse_mode="HTML")
        else:
            await play_single(bot, context, chat_id, user, "slot", amt, None, msg.message_id)
        return True
    if context.user_data.get("casino_custom_dice") and re.fullmatch(r"[1-6]", t):
        amt = context.user_data.pop("casino_custom_dice")
        await play_single(bot, context, chat_id, user, "dice", amt, int(t), msg.message_id)
        return True
    if re.fullmatch(r"کازینو|casino", t, re.I):
        ptext, kb = panel_main(user.id)
        await send_html(bot, chat_id, ptext, reply_markup=kb, reply_parameters=rp)
        return True
    m = re.fullmatch(r"(?:میز\s*(تاس|اسلات)|(تاس|اسلات)\s*میز)\s+(.+)", t)
    if m:
        game = "dice" if (m.group(1) or m.group(2)) == "تاس" else "slot"
        amt = parse_amount(m.group(3))
        if amt <= 0:
            await msg.reply_text(BP("مبلغ رو نفهمیدم. مثلا: میز تاس 100هزار"), parse_mode="HTML")
        else:
            await create_table(bot, chat_id, user, game, amt, msg.message_id)
        return True
    m = re.fullmatch(r"تاس\s+([1-6])\s+(.+)", t)
    if m:
        amt = parse_amount(m.group(2))
        if amt <= 0:
            await msg.reply_text(BP("مبلغ رو نفهمیدم. مثلا: تاس 3 100هزار"), parse_mode="HTML")
        else:
            await play_single(bot, context, chat_id, user, "dice", amt, int(m.group(1)), msg.message_id)
        return True
    m = re.fullmatch(r"مین\s+(.+)", t)
    if m:
        amt = parse_amount(m.group(1))
        if amt > 0: await start_mines(bot, context, chat_id, user, amt, reply_to=msg.message_id)
        else: await msg.reply_text(BP("مبلغ رو نفهمیدم. مثال: مین 100هزار"), parse_mode="HTML")
        return True
    m = re.fullmatch(r"اسلات\s+(.+)", t)
    if m and re.match(r"[\d\u06f0-\u06f9]|صد|یک|دو|ده", m.group(1)):
        amt = parse_amount(m.group(1))
        if amt > 0:
            await play_single(bot, context, chat_id, user, "slot", amt, None, msg.message_id)
            return True
    return False


# ───────────────────────── دوئل جقی ─────────────────────────
# روی پیام یه کاربر ریپلای کن و بنویس «دوئل جقی»؛ اگه قبول کنه بعد ۵ ثانیه برنده اعلام میشه و ۱۰۰ تومن می‌گیره.
# هر کاربر هر ۵ دقیقه فقط یه دوئل می‌تونه بکنه.
DUEL_PRIZE = 100
DUEL_COOLDOWN = 5 * 60
DUEL_WAIT = 5
DUEL_TTL = 300
DUEL_LINES = [
    "با یه حرکت برقی حریف رو زمین زد 💦",
    "دستش از همه سریع‌تر بود 🤛",
    "تا آخرش دووم آورد و حریف نفس کم آورد 😮‍💨",
    "با یه ضربه‌ی آخر کار رو تموم کرد 🔥",
    "حریف وسط راه خسته شد و تسلیم شد 🏳️",
]


def _cd_text(left: float) -> str:
    m, s = divmod(int(max(left, 0)) + 1, 60)
    return f"{m} دقیقه و {s} ثانیه"


async def duel_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, user = update.effective_message, update.effective_user
    if msg.chat_id > 0:
        await msg.reply_text(BP("دوئل جقی فقط تو گروه ممکنه."), parse_mode="HTML")
        return
    target = msg.reply_to_message.from_user if msg.reply_to_message else None
    if target is None:
        await msg.reply_text(BP("برای دوئل، روی پیام حریفت ریپلای کن و بنویس: دوئل جقی"), parse_mode="HTML")
        return
    if target.is_bot or target.id == user.id:
        await msg.reply_text(BP("نمی‌تونی با خودت یا ربات دوئل کنی 😅"), parse_mode="HTML")
        return
    await upsert_user(user.id, user.full_name)
    row = await get_user(user.id)
    left = DUEL_COOLDOWN - (time.time() - row["last_duel"])
    if left > 0:
        await msg.reply_text(B(f"{ce('jagh', 6, '⏳')} دوئل بعدیت {_cd_text(left)} دیگه‌ست."), parse_mode="HTML")
        return
    kb = InlineKeyboardMarkup([[
        btn("✅ قبول", "success", callback_data=f"du:ok:{user.id}:{target.id}"),
        btn("❌ رد", "danger", callback_data=f"du:no:{user.id}:{target.id}"),
    ]])
    await msg.reply_text(
        f'<b>{SWORDS_CE} دوئل جقی!\n\n{_ulink(user.id, user.full_name)} {_ulink(target.id, target.full_name)} رو به دوئل دعوت کرد.\n'
        f'برنده {DUEL_PRIZE:,} تومن می‌گیره. {_ulink(target.id, target.full_name)}، قبول می‌کنی؟</b>',
        parse_mode="HTML", reply_markup=kb,
        reply_parameters=ReplyParameters(message_id=msg.message_id, allow_sending_without_reply=True),
    )


async def duel_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, act, frm, to = q.data.split(":")
    frm, to = int(frm), int(to)
    if q.from_user.id != to:
        await q.answer("این دعوت مال تو نیست 😅", show_alert=True)
        return
    done = context.application.bot_data.setdefault("du_done", set())
    key = (q.message.chat_id, q.message.message_id)
    if key in done:
        await q.answer("این دوئل دیگه فعال نیست", show_alert=True)
        return
    done.add(key)
    if act == "no":
        await q.answer()
        await q.edit_message_text("❌ دوئل رد شد.")
        return
    try:
        age = time.time() - q.message.date.timestamp()
    except Exception:
        age = 0
    if age > DUEL_TTL:
        await q.answer("دعوت منقضی شده", show_alert=True)
        await q.edit_message_text("⌛ دعوت دوئل منقضی شد.")
        return
    await upsert_user(to, q.from_user.full_name)
    now = time.time()

    class _CD(Exception):
        pass

    try:
        async with pool.acquire() as c:
            async with c.transaction():
                for uid in (frm, to):
                    ok = await c.fetchval(
                        "UPDATE users SET last_duel = $1::double precision WHERE user_id = $2::bigint "
                        "AND last_duel <= $1::double precision - $3::double precision RETURNING 1",
                        now, uid, float(DUEL_COOLDOWN),
                    )
                    if not ok:
                        raise _CD(str(uid))
    except _CD as e:
        who = "خودت" if int(str(e)) == to else "حریفت"
        await q.answer(f"هنوز وقت دوئل {who} نرسیده (هر ۵ دقیقه یه دوئل).", show_alert=True)
        await q.edit_message_text(f"{HG_CE} دوئل انجام نشد؛ {who} هنوز تو استراحت ۵ دقیقه‌ایه.", parse_mode="HTML")
        return
    except Exception:
        done.discard(key)
        log.exception("duel start failed")
        await q.answer("خطا، دوباره امتحان کن.", show_alert=True)
        return
    await q.answer("دوئل شروع شد!")
    fname = await pool.fetchval("SELECT name FROM users WHERE user_id = $1", frm) or "کاربر"
    a, b = _ulink(frm, fname), _ulink(to, q.from_user.full_name)
    try:
        await q.edit_message_text(f"<b>{SWORDS_CE} {a} {VS_CE} {b}\n\n{HG_CE} دوئل شروع شد... نتیجه تا ۵ ثانیه‌ی دیگه!</b>", parse_mode="HTML")
    except Exception:
        pass
    await asyncio.sleep(DUEL_WAIT)
    win_id, win_link = random.choice(((frm, a), (to, b)))
    await _give(win_id, DUEL_PRIZE)
    try:
        await q.edit_message_text(
            f"<b>{SWORDS_CE} {a} {VS_CE} {b}\n\n{TROPHY_CE} برنده: {win_link}\n{random.choice(DUEL_LINES)}\n{MONEY_CE} {DUEL_PRIZE:,} تومن به حسابش رفت!</b>",
            parse_mode="HTML",
        )
    except Exception:
        log.exception("duel result edit failed")
        await context.bot.send_message(q.message.chat_id, f"{TROPHY_CE} برنده‌ی دوئل: {win_link} (+{DUEL_PRIZE:,} تومن)", parse_mode="HTML")


# ───────────────────────── پنل پشتیبانی /admin ─────────────────────────
BOT_ENABLED = True
KNOWN_CHATS: set = set()


async def gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قبل از همه‌ی هندلرها: ثبت گروه‌ها + اگه ربات خاموش باشه فقط پشتیبان‌ها کار می‌کنن."""
    user, chat = update.effective_user, update.effective_chat
    if chat and chat.type in ("group", "supergroup") and chat.id not in KNOWN_CHATS:
        KNOWN_CHATS.add(chat.id)
        try:
            await pool.execute(
                "INSERT INTO chats (chat_id, title) VALUES ($1, $2) ON CONFLICT (chat_id) DO UPDATE SET title = EXCLUDED.title",
                chat.id, chat.title or "",
            )
        except Exception:
            log.exception("chat save failed")
    if not BOT_ENABLED and not (user and user.id in ADMIN_IDS):
        if update.callback_query:
            try:
                await update.callback_query.answer("ربات موقتاً خاموشه 🔴", show_alert=True)
            except Exception:
                pass
        raise ApplicationHandlerStop


def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("💸 کم کردن پول", "primary", callback_data="ad:money"), btn("🤛 کم کردن جق", "primary", callback_data="ad:jagh")],
        [btn("🔴 خاموش کردن ربات", "danger", callback_data="ad:toggle") if BOT_ENABLED
         else btn("🟢 روشن کردن ربات", "success", callback_data="ad:toggle")],
        [btn("📢 پیام همگانی", "primary", callback_data="ad:bc"), btn("📊 آمار", "primary", callback_data="ad:stats")],
    ])


def admin_menu_text() -> str:
    return f"🛠 پنل پشتیبانی\n\nوضعیت ربات: {'🟢 روشن' if BOT_ENABLED else '🔴 خاموش'}"


CANCEL_KB = InlineKeyboardMarkup([[btn("❌ لغو", "danger", callback_data="ad:cancel")]])


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/admin — فقط پشتیبان‌ها، تو پی‌وی ربات."""
    if update.effective_user.id not in ADMIN_IDS:
        return
    context.user_data.pop("adm", None)
    context.user_data.pop("await_jobs", None)
    await update.effective_message.reply_text(admin_menu_text(), reply_markup=admin_menu_kb())


async def admin_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global BOT_ENABLED
    q = update.callback_query
    if q.from_user.id not in ADMIN_IDS:
        await q.answer("فقط پشتیبان‌ها", show_alert=True)
        return
    parts = q.data.split(":")
    act = parts[1]
    await q.answer()
    if act in ("menu", "cancel"):
        context.user_data.pop("adm", None)
        await q.edit_message_text(admin_menu_text(), reply_markup=admin_menu_kb())
    elif act in ("money", "jagh"):
        context.user_data["adm"] = {"act": act, "step": "target"}
        what = "پول" if act == "money" else "جق"
        await q.edit_message_text(
            f"کم کردن {what}\n\nآیدی عددی کاربر رو بفرست، یا یه پیام ازش اینجا فوروارد کن.\n(لغو: بنویس «لغو»)",
            reply_markup=CANCEL_KB,
        )
    elif act == "toggle":
        BOT_ENABLED = not BOT_ENABLED
        await pool.execute(
            "INSERT INTO bot_settings (key, value) VALUES ('bot_enabled', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            "1" if BOT_ENABLED else "0",
        )
        await q.edit_message_text(admin_menu_text(), reply_markup=admin_menu_kb())
    elif act == "stats":
        n_users = await pool.fetchval("SELECT count(*) FROM users")
        n_started = await pool.fetchval("SELECT count(*) FROM users WHERE started")
        n_chats = await pool.fetchval("SELECT count(*) FROM chats")
        tot = await pool.fetchval("SELECT coalesce(sum(balance), 0) FROM users")
        jg = await pool.fetchval("SELECT coalesce(sum(jaghs), 0) FROM users")
        kb = InlineKeyboardMarkup([[btn("برگشت", "primary", icon=EID_BACK, callback_data="ad:menu")]])
        await q.edit_message_text(
            f"📊 آمار\n\nکاربرها: {n_users:,} (استارت‌کرده: {n_started:,})\nگروه‌ها: {n_chats:,}\n"
            f"مجموع دارایی: {tot:,} تومن\nمجموع جق‌ها: {jg:,}",
            reply_markup=kb,
        )
    elif act == "bc" and len(parts) == 2:
        kb = InlineKeyboardMarkup([
            [btn("👤 همه‌ی کاربرها", "primary", callback_data="ad:bc:u"), btn("👥 همه‌ی گروه‌ها", "primary", callback_data="ad:bc:g")],
            [btn("📣 هر دو", "success", callback_data="ad:bc:a")],
            [btn("❌ لغو", "danger", callback_data="ad:cancel")],
        ])
        await q.edit_message_text("📢 پیام همگانی رو به کی بفرستم؟", reply_markup=kb)
    elif act == "bc":
        context.user_data["adm"] = {"act": "bc", "target": parts[2], "step": "msg"}
        await q.edit_message_text(
            "حالا پیامت رو بفرست (متن، عکس، ویدیو، فایل، ویس، هر چیزی). همون‌طور که هست برای همه کپی می‌شه.\n(لغو: «لغو»)",
            reply_markup=CANCEL_KB,
        )
    elif act == "bcgo":
        st = context.user_data.get("adm")
        if not st or st.get("act") != "bc" or st.get("step") != "confirm":
            await q.edit_message_text("چیزی برای ارسال نیست.", reply_markup=admin_menu_kb())
            return
        context.user_data.pop("adm", None)
        await q.edit_message_text(f"{HG_CE} ارسال شروع شد؛ وقتی تموم شد خبرت می‌کنم.", parse_mode="HTML")
        task = asyncio.create_task(do_broadcast(context.bot, q.message.chat_id, st))
        context.application.bot_data.setdefault("tasks", set()).add(task)
        task.add_done_callback(lambda t: context.application.bot_data["tasks"].discard(t))


async def do_broadcast(bot, admin_chat: int, st: dict):
    targets = []
    if st["target"] in ("u", "a"):
        targets += [r["user_id"] for r in await pool.fetch("SELECT user_id FROM users WHERE started")]
    if st["target"] in ("g", "a"):
        targets += [r["chat_id"] for r in await pool.fetch("SELECT chat_id FROM chats")]
    ok = fail = 0
    for cid in dict.fromkeys(targets):
        for attempt in range(2):
            try:
                await bot.copy_message(chat_id=cid, from_chat_id=st["src_chat"], message_id=st["src_msg"])
                ok += 1
                break
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except (Forbidden, BadRequest):
                fail += 1
                if cid < 0:
                    await pool.execute("DELETE FROM chats WHERE chat_id = $1", cid)
                    KNOWN_CHATS.discard(cid)
                break
            except Exception:
                fail += 1
                break
        await asyncio.sleep(0.06)
    try:
        await bot.send_message(admin_chat, f"✅ پیام همگانی تموم شد.\nموفق: {ok:,}\nناموفق: {fail:,}")
    except Exception:
        log.exception("broadcast report failed")


async def admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """پیام‌های پشتیبان وقتی تو مرحله‌ی پنل هست. True یعنی مصرف شد."""
    msg, user = update.effective_message, update.effective_user
    st = context.user_data.get("adm")
    if not st or not msg or user.id not in ADMIN_IDS:
        return False
    if msg.text and msg.text.strip() == "لغو":
        context.user_data.pop("adm", None)
        await msg.reply_text("لغو شد.", reply_markup=admin_menu_kb())
        return True
    act = st["act"]
    if act in ("money", "jagh"):
        if st["step"] == "target":
            uid = None
            fo = getattr(msg, "forward_origin", None)
            if fo is not None and getattr(fo, "sender_user", None):
                uid = fo.sender_user.id
            elif msg.text and msg.text.strip().lstrip("-").isdigit():
                uid = int(msg.text.strip())
            if uid is None:
                await msg.reply_text("آیدی عددی بفرست یا یه پیام از کاربر فوروارد کن (اگه کاربر فوروارد رو بسته باشه آیدی عددی لازمه).", reply_markup=CANCEL_KB)
                return True
            row = await get_user(uid)
            if not row:
                await msg.reply_text("این کاربر تو دیتابیس نیست (باید قبلاً با ربات کار کرده باشه).", reply_markup=CANCEL_KB)
                return True
            st.update(step="amount", uid=uid)
            what = "پول (مثلا 50000 یا 100 هزار)" if act == "money" else "جق (مثلا 20)"
            await msg.reply_text(
                f"👤 {row['name'] or uid}\nدارایی: {row['balance']:,} تومن | جق‌ها: {row['jaghs']:,}\n\nچقدر {what} کم بشه؟",
                reply_markup=CANCEL_KB,
            )
            return True
        if st["step"] == "amount":
            amt = parse_amount(msg.text or "")
            if amt <= 0:
                await msg.reply_text("عدد معتبر بفرست.", reply_markup=CANCEL_KB)
                return True
            col = "balance" if act == "money" else "jaghs"
            new = await pool.fetchval(
                f"UPDATE users SET {col} = GREATEST({col} - $1::bigint, 0) WHERE user_id = $2::bigint RETURNING {col}",
                amt, st["uid"],
            )
            context.user_data.pop("adm", None)
            unit = "تومن" if act == "money" else "جق"
            await msg.reply_text(f"✅ {amt:,} {unit} کم شد. مقدار جدید: {new:,} {unit}", reply_markup=admin_menu_kb())
            return True
    if act == "bc" and st["step"] == "msg":
        st.update(step="confirm", src_chat=msg.chat_id, src_msg=msg.message_id)
        n_u = await pool.fetchval("SELECT count(*) FROM users WHERE started") if st["target"] in ("u", "a") else 0
        n_g = await pool.fetchval("SELECT count(*) FROM chats") if st["target"] in ("g", "a") else 0
        kb = InlineKeyboardMarkup([[
            btn("✅ ارسال", "success", callback_data="ad:bcgo"),
            btn("❌ لغو", "danger", callback_data="ad:cancel"),
        ]])
        await msg.reply_text(f"این پیام برای {n_u:,} کاربر و {n_g:,} گروه فرستاده می‌شه. مطمئنی؟", reply_markup=kb)
        return True
    return False


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(startup)
        .post_shutdown(close_db)
        .build()
    )
    app.add_handler(TypeHandler(Update, gate), group=-1)
    app.add_handler(CallbackQueryHandler(casino_cb, pattern=r"^cz:"))
    app.add_handler(CallbackQueryHandler(duel_answer, pattern=r"^du:(ok|no):\d+:\d+$"))
    app.add_handler(CommandHandler("admin", admin_cmd, filters=filters.ChatType.PRIVATE))
    app.add_handler(CallbackQueryHandler(admin_cb, pattern=r"^ad:"))
    app.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE))
    app.add_handler(CallbackQueryHandler(check_join, pattern="^check_join$"))
    app.add_handler(CommandHandler("check", debug_channels, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("emojis", show_emojis, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("setemoji", set_emoji, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("setjobs", set_jobs, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("jobsreset", jobs_reset, filters=filters.ChatType.PRIVATE))
    app.add_handler(CallbackQueryHandler(set_jobs_pick, pattern=r"^sj:[mf]$"))
    app.add_handler(CallbackQueryHandler(jobs_reset_pick, pattern=r"^sr:[mfa]$"))
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
    app.add_handler(CommandHandler("shift", show_shift))
    app.add_handler(CallbackQueryHandler(shift_answer, pattern=r"^sh:\d+:\d+:\d+$"))
    app.add_handler(CommandHandler("gender", ask_gender))
    app.add_handler(CallbackQueryHandler(pick_gender, pattern=r"^g:\d+:[mf]$"))
    app.add_handler(CommandHandler("wallet", wallet))
    app.add_handler(CommandHandler("top", top))
    app.add_handler(CallbackQueryHandler(transfer_answer, pattern=r"^tr:(ok|no):\d+:\d+:\d+$"))
    app.add_handler(
        MessageHandler(filters.ChatType.GROUPS & filters.TEXT & ~filters.COMMAND, on_group_text)
    )
    app.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, on_private_text)
    )
    app.add_handler(
        MessageHandler(filters.ChatType.PRIVATE & ~filters.COMMAND & ~filters.TEXT, on_private_other)
    )
    log.info("bot started")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()

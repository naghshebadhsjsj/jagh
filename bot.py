import asyncio
import html
import json
import logging
import os
import random
import re
import time
from datetime import datetime, timedelta


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
import math
from io import BytesIO
from pathlib import Path
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
from telegram import InputMediaAnimation, BotCommandScopeAllPrivateChats, InlineKeyboardButton, KeyboardButton, ReplyKeyboardMarkup, InlineKeyboardMarkup, InputMediaPhoto, MessageEntity, ReplyParameters, Update, WebAppInfo
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
    "energy":  ("انرژی", ["نوار پر", "نوار خالی"]),
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
    ("energy", 1): "5400280896311944960",  # نوار انرژی (خونه‌ی پر)
}


# ایموجی‌های پرمیومِ ثابت (⏳ 🏆 🆚 💰 ⚔️ و دکمه‌ی برگشت)
EID_HOURGLASS = "5787432469598835099"
EID_TROPHY = "5413566144986503832"
EID_VS = "6213060666320754449"
EID_MONEY = "6233367447789899509"
EID_SWORDS = "5431421337648256443"
EID_BACK = "5816895683256390576"
EID_HAND = "5388881284309195257"
EID_HAND_DONE = "5204118276635892650"
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
# صاحب‌ها (ADMIN_IDS تو Railway): همه‌ی دسترسی‌ها + مدیریت پشتیبان‌ها با /support
OWNER_IDS = set(ADMIN_IDS)
# پشتیبان‌هایی که صاحب‌ها با /support اضافه می‌کنن: {آیدی: {دسترسی‌ها}} (تو دیتابیس می‌مونه)
STAFF: dict[int, set] = {}
SUPPORT_PERMS = {
    "panel": "ورود به پنل پشتیبانی (/admin)",
    "emoji": "ایموجی‌ها (/setemoji)",
    "photo": "عکس کاراکترها (/setphoto)",
    "jobs": "جدول شغل‌ها (/setjobs و /jobsreset)",
    "check": "بررسی کارت‌ها (/cardcheck)",
    "pay": "تایید/رد رسید خرید پول بازی",
    "staff": "مدیریت دسترسی پشتیبان‌های انتخاب‌شده (/admin)",
}
# دسترسیِ تک‌تکِ دکمه‌های /admin و مینی‌اپ؛ صاحب ربات برای هر پشتیبان جدا روشن/خاموش می‌کنه
ADMIN_PERMS = {
    "money_sub": "💸 کم کردن پول",
    "money_add": "➕ افزودن پول",
    "jagh_sub": "🤛 کم کردن جق",
    "jagh_add": "➕ افزودن جق",
    "miniapp": "📱 مینی‌اپ پشتیبانی",
    "group_msgs": "👁 پیام‌های گروه‌ها",
    "ban": "🚫 محروم کردن کاربر",
    "unban": "✅ برداشتن محرومیت",
    "staff_list": "📋 لیست پشتیبانی",
    "task_daily": "📋 افزودن تسک روزانه",
    "task_weekly": "📆 افزودن تسک هفتگی",
    "bot_toggle": "🔴 خاموش/روشن کردن ربات",
    "stats": "📊 آمار",
    "broadcast": "📢 پیام همگانی",
    "giveaway": "🎁 قرعه‌کشی",
}
SUPPORT_PERMS.update(ADMIN_PERMS)
# این دو تا رو فقط صاحب ربات می‌تونه بده؛ پشتیبانِ مدیر فقط می‌تونه ازشون بگیره
OWNER_GRANT_ONLY = {"miniapp", "group_msgs"}
# {آیدی پشتیبانِ مدیر: {آیدی پشتیبان‌هایی که اجازه داره دسترسی‌هاشون رو بده/بگیره}} (تو دیتابیس می‌مونه)
STAFF_MANAGE: dict[int, set] = {}


def has_perm(uid: int, perm: str) -> bool:
    """صاحب‌ها همه‌چیز دارن؛ پشتیبان‌ها فقط دسترسی‌هایی که بهشون داده شده."""
    return uid in OWNER_IDS or perm in STAFF.get(uid, ())


def perms_of(uid: int) -> set:
    """همه‌ی دسترسی‌های فعالِ یه کاربر (صاحب‌ها همه‌چیز)."""
    return set(SUPPORT_PERMS) if uid in OWNER_IDS else set(STAFF.get(uid, ()))

TRIGGER_WORD = "جق"
COOLDOWN_SECONDS = 5 * 60 + 5  # پایه ۵:۰۵؛ با پنج ارتقا به ۴:۴۰ می‌رسد
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
ME_WORD = "کاراکتر"  # این کلمه کاراکتر رو نشون میده؛ «خودم» پنل شخصی (بانک، خلاف، دست، کازینو، تنظیمات) رو باز می‌کنه

# عکس هر کاراکتر (file_id تلگرام). پشتیبان با /setphoto ست می‌کنه و تو دیتابیس می‌مونه.
CHAR_PHOTOS: dict[tuple[str, int], str] = {}


BOT_VERSION = "کازینو سم یا خوراکی + جاسوسی + پشتیبانی v6"


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
CHANNELS = ["@jagh_game", "@selfsaz_iran"]  # عضویت اجباری در هر دو کانال

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


def pick_reply(amount: int, tier_amount: int | None = None) -> str:
    """amount = پولی که نشون داده میشه؛ tier_amount = مبلغ پایه (بدون ضریب ایونت) برای انتخاب جمله."""
    texts = REPLY_TIERS[-1][1]
    ta = amount if tier_amount is None else tier_amount
    for max_amount, tier_texts in REPLY_TIERS:
        if ta <= max_amount:
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
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS bank_balance BIGINT NOT NULL DEFAULT 0")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS hand_level INT NOT NULL DEFAULT 0")
        await c.execute("CREATE TABLE IF NOT EXISTS user_house (user_id BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE, repair_done_at DOUBLE PRECISION NOT NULL DEFAULT 0, last_claim DOUBLE PRECISION NOT NULL DEFAULT 0, stored_income BIGINT NOT NULL DEFAULT 0, items TEXT NOT NULL DEFAULT '')")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS condoms INT NOT NULL DEFAULT 0")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS pills INT NOT NULL DEFAULT 0")
        await c.execute("CREATE TABLE IF NOT EXISTS couple_relation (pair_key TEXT PRIMARY KEY, relation INT NOT NULL DEFAULT 0, last_sex DOUBLE PRECISION NOT NULL DEFAULT 0)")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS gender TEXT")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS banned_until DOUBLE PRECISION NOT NULL DEFAULT 0")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS jaghs BIGINT NOT NULL DEFAULT 0")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_shift DOUBLE PRECISION NOT NULL DEFAULT 0")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS shift_seen TEXT NOT NULL DEFAULT ''")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS work_count BIGINT NOT NULL DEFAULT 0")
        await c.execute(f"ALTER TABLE users ADD COLUMN IF NOT EXISTS energy DOUBLE PRECISION NOT NULL DEFAULT {ENERGY_START}")
        await c.execute(f"ALTER TABLE users ALTER COLUMN energy SET DEFAULT {ENERGY_START}")
        await c.execute("CREATE TABLE IF NOT EXISTS rifts (user_lo BIGINT NOT NULL, user_hi BIGINT NOT NULL, ends_at DOUBLE PRECISION NOT NULL, PRIMARY KEY (user_lo, user_hi))")
        await c.execute("CREATE TABLE IF NOT EXISTS rift_log (initiator BIGINT NOT NULL, target BIGINT NOT NULL, day TEXT NOT NULL, cnt INT NOT NULL DEFAULT 0, PRIMARY KEY (initiator, target, day))")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_hug DOUBLE PRECISION NOT NULL DEFAULT 0")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS jagh_machine BIGINT NOT NULL DEFAULT 0")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_steal DOUBLE PRECISION NOT NULL DEFAULT 0")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS spouse_id BIGINT")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS spy_owned BOOLEAN NOT NULL DEFAULT FALSE")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS spy_day TEXT NOT NULL DEFAULT ''")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS spy_uses INT NOT NULL DEFAULT 0")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_affection DOUBLE PRECISION NOT NULL DEFAULT 0")
        await c.execute("CREATE TABLE IF NOT EXISTS affection_cooldowns (actor_id BIGINT NOT NULL, target_id BIGINT NOT NULL, last_ts DOUBLE PRECISION NOT NULL, PRIMARY KEY(actor_id,target_id))")
        await c.execute("CREATE TABLE IF NOT EXISTS actor_cooldowns (actor_id BIGINT NOT NULL, kind TEXT NOT NULL, last_ts DOUBLE PRECISION NOT NULL, PRIMARY KEY(actor_id,kind))")
        await c.execute("CREATE TABLE IF NOT EXISTS marriage_proposals (bride_id BIGINT PRIMARY KEY, groom_id BIGINT NOT NULL, chat_id BIGINT NOT NULL, created_at DOUBLE PRECISION NOT NULL)")
        await c.execute("CREATE TABLE IF NOT EXISTS casino_records (chat_id BIGINT NOT NULL, game TEXT NOT NULL, user_id BIGINT NOT NULL, name TEXT, record BIGINT NOT NULL DEFAULT 0, PRIMARY KEY(chat_id, game))")
        await c.execute("CREATE TABLE IF NOT EXISTS robbery_state (user_id BIGINT NOT NULL, robbery_type TEXT NOT NULL, cooldown_until DOUBLE PRECISION NOT NULL DEFAULT 0, blocked_until DOUBLE PRECISION NOT NULL DEFAULT 0, PRIMARY KEY (user_id, robbery_type))")
        await c.execute("CREATE TABLE IF NOT EXISTS robbery_profile (user_id BIGINT PRIMARY KEY, trace INTEGER NOT NULL DEFAULT 0, cooldown_until DOUBLE PRECISION NOT NULL DEFAULT 0, arrest_until DOUBLE PRECISION NOT NULL DEFAULT 0, trace_until DOUBLE PRECISION NOT NULL DEFAULT 0)")
        await c.execute("ALTER TABLE robbery_profile ADD COLUMN IF NOT EXISTS bail_amount BIGINT NOT NULL DEFAULT 0")
        await c.execute("ALTER TABLE robbery_profile ADD COLUMN IF NOT EXISTS clean_cost BIGINT NOT NULL DEFAULT 0")
        await c.execute("ALTER TABLE robbery_profile ADD COLUMN IF NOT EXISTS clean_step INT NOT NULL DEFAULT 0")
        await c.execute("CREATE TABLE IF NOT EXISTS tasks (task_id BIGSERIAL PRIMARY KEY, period TEXT NOT NULL CHECK(period IN ('daily', 'weekly')), title TEXT NOT NULL, active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")
        await c.execute("CREATE TABLE IF NOT EXISTS task_completions (task_id BIGINT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, user_id BIGINT NOT NULL, period_key TEXT NOT NULL, completed BOOLEAN NOT NULL DEFAULT FALSE, PRIMARY KEY(task_id, user_id, period_key))")
        await c.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS metric TEXT")
        await c.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS target BIGINT NOT NULL DEFAULT 1")
        await c.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS reward BIGINT NOT NULL DEFAULT 0")
        await c.execute("ALTER TABLE tasks ADD COLUMN IF NOT EXISTS is_default BOOLEAN NOT NULL DEFAULT FALSE")
        await c.execute("ALTER TABLE task_completions ADD COLUMN IF NOT EXISTS progress BIGINT NOT NULL DEFAULT 0")
        await c.execute("ALTER TABLE task_completions ADD COLUMN IF NOT EXISTS rewarded BIGINT NOT NULL DEFAULT 0")
        if not await c.fetchval("SELECT 1 FROM tasks WHERE is_default = TRUE LIMIT 1"):   # فقط بار اول
            for _per, _met, _tg, _rw in TASK_DEFAULTS:
                await c.execute("INSERT INTO tasks(period,title,metric,target,reward,is_default) VALUES($1,$2,$3,$4,$5,TRUE)",
                                _per, task_auto_title(_met, _tg), _met, _tg, _rw)
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_duel DOUBLE PRECISION NOT NULL DEFAULT 0")
        await c.execute("CREATE TABLE IF NOT EXISTS chats (chat_id BIGINT PRIMARY KEY, title TEXT)")
        await c.execute("CREATE TABLE IF NOT EXISTS group_msgs (chat_id BIGINT NOT NULL, msg_id BIGINT NOT NULL, user_id BIGINT NOT NULL, name TEXT, username TEXT, text TEXT, ts DOUBLE PRECISION NOT NULL, PRIMARY KEY (chat_id, msg_id))")
        await c.execute("CREATE INDEX IF NOT EXISTS group_msgs_user_idx ON group_msgs (chat_id, user_id, ts DESC)")
        await c.execute("CREATE INDEX IF NOT EXISTS group_msgs_chat_ts_idx ON group_msgs (chat_id, ts DESC)")
        await c.execute("CREATE INDEX IF NOT EXISTS group_msgs_ts_idx ON group_msgs (ts)")
        for _col in ("kind TEXT", "file_id TEXT", "reply_to BIGINT", "reply_name TEXT", "reply_text TEXT", "fwd TEXT",
                     "edited_text TEXT", "edited_ts DOUBLE PRECISION"):
            await c.execute(f"ALTER TABLE group_msgs ADD COLUMN IF NOT EXISTS {_col}")
        await c.execute("CREATE TABLE IF NOT EXISTS tg_profiles (user_id BIGINT PRIMARY KEY, first_name TEXT, last_name TEXT, username TEXT, is_premium BOOLEAN NOT NULL DEFAULT FALSE, lang TEXT, first_seen DOUBLE PRECISION NOT NULL, last_seen DOUBLE PRECISION NOT NULL)")
        await c.execute("CREATE TABLE IF NOT EXISTS admin_log (id BIGSERIAL PRIMARY KEY, admin_id BIGINT NOT NULL, admin_name TEXT, action TEXT NOT NULL, target BIGINT, details TEXT, ts DOUBLE PRECISION NOT NULL)")
        await c.execute(
            """CREATE TABLE IF NOT EXISTS cars (
                user_id  BIGINT NOT NULL,
                car_idx  INT    NOT NULL,
                color    INT    NOT NULL DEFAULT 0,
                level    INT    NOT NULL DEFAULT 1,
                last_pay DOUBLE PRECISION NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, car_idx)
            )"""
        )
        # مهاجرت امن برای سیستم قطعات و صندوق درآمد ماشین
        await c.execute("ALTER TABLE cars ADD COLUMN IF NOT EXISTS pending_profit BIGINT NOT NULL DEFAULT 0")
        await c.execute("ALTER TABLE cars ADD COLUMN IF NOT EXISTS up_engine INT NOT NULL DEFAULT 0")
        await c.execute("ALTER TABLE cars ADD COLUMN IF NOT EXISTS up_body INT NOT NULL DEFAULT 0")
        await c.execute("ALTER TABLE cars ADD COLUMN IF NOT EXISTS up_tires INT NOT NULL DEFAULT 0")
        await c.execute("ALTER TABLE cars ADD COLUMN IF NOT EXISTS up_glass_rims INT NOT NULL DEFAULT 0")
        # (مهاجرت قدیمی «jaghs = balance / 52» حذف شد: با هر ریستارت برای کسی که پولش از راه‌های دیگه اومده بود
        #  تعداد جق الکی هزارتایی می‌ساخت و تو لیدربرد می‌اومد.)
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
        # یک‌بار: رنگ «سفید» شد رنگ شماره‌ی ۰ (پیش‌فرض). قرمز/آبی/صورتی یکی می‌رن جلو تا رنگ خریده‌شده‌ی کاربرها حفظ بشه
        if not await c.fetchval("SELECT 1 FROM bot_settings WHERE key = 'car_white_migr_1'"):
            await c.execute("UPDATE cars SET color = color + 1 WHERE color >= 1")
            await c.execute("INSERT INTO bot_settings (key, value) VALUES ('car_white_migr_1', '1') ON CONFLICT (key) DO NOTHING")
        # یک‌بار: ماشین همه‌ی کاربرها گرفته میشه (سیستم جدید پنل ماشین). با ری‌استارت‌های بعدی دوباره اجرا نمیشه.
        if not await c.fetchval("SELECT 1 FROM bot_settings WHERE key = 'cars_wipe_2026_11_06'"):
            await c.execute("DELETE FROM cars")
            await c.execute("INSERT INTO bot_settings (key, value) VALUES ('cars_wipe_2026_11_06', '1') ON CONFLICT (key) DO NOTHING")
        # یک‌بار: کسایی که انرژیشون صفره (قبل از سیستم انرژی) با انرژی اولیه شروع می‌کنن
        if not await c.fetchval("SELECT 1 FROM bot_settings WHERE key = 'energy_start_1'"):
            await c.execute(f"UPDATE users SET energy = {ENERGY_START} WHERE energy = 0")
            await c.execute("INSERT INTO bot_settings (key, value) VALUES ('energy_start_1', '1') ON CONFLICT (key) DO NOTHING")
        BANNED.clear()
        for r in await c.fetch("SELECT user_id, banned_until FROM users WHERE banned_until > $1", time.time()):
            BANNED[r["user_id"]] = r["banned_until"]
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
        await c.execute("CREATE TABLE IF NOT EXISTS support_staff (user_id BIGINT PRIMARY KEY, perms TEXT NOT NULL DEFAULT '')")
        await c.execute("CREATE TABLE IF NOT EXISTS transfer_daily (user_id BIGINT NOT NULL, day TEXT NOT NULL, total BIGINT NOT NULL DEFAULT 0, PRIMARY KEY(user_id, day))")
        await c.execute("CREATE TABLE IF NOT EXISTS support_manage (manager_id BIGINT NOT NULL, target_id BIGINT NOT NULL, PRIMARY KEY(manager_id, target_id))")
        await c.execute("CREATE TABLE IF NOT EXISTS referrals (referred_id BIGINT PRIMARY KEY, referrer_id BIGINT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at DOUBLE PRECISION NOT NULL DEFAULT 0, rewarded_at DOUBLE PRECISION NOT NULL DEFAULT 0, last_check DOUBLE PRECISION NOT NULL DEFAULT 0)")
        await c.execute("CREATE INDEX IF NOT EXISTS referrals_referrer_idx ON referrals (referrer_id)")
        await c.execute("CREATE TABLE IF NOT EXISTS giveaways (id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, prize TEXT NOT NULL DEFAULT '', channels TEXT NOT NULL DEFAULT '', ends_at DOUBLE PRECISION NOT NULL DEFAULT 0, duration DOUBLE PRECISION NOT NULL DEFAULT 0, max_users INT NOT NULL DEFAULT 0, winners INT NOT NULL DEFAULT 1, circles INT NOT NULL DEFAULT 4, top_public BOOLEAN NOT NULL DEFAULT FALSE, send_groups BOOLEAN NOT NULL DEFAULT TRUE, send_users BOOLEAN NOT NULL DEFAULT FALSE, send_channels BOOLEAN NOT NULL DEFAULT TRUE, status TEXT NOT NULL DEFAULT 'draft', created_by BIGINT, created_at DOUBLE PRECISION NOT NULL DEFAULT 0, winner_ids TEXT NOT NULL DEFAULT '')")
        await c.execute("CREATE TABLE IF NOT EXISTS giveaway_entries (gid BIGINT NOT NULL, user_id BIGINT NOT NULL, inviter_id BIGINT, status TEXT NOT NULL DEFAULT 'active', joined_at DOUBLE PRECISION NOT NULL DEFAULT 0, last_check DOUBLE PRECISION NOT NULL DEFAULT 0, PRIMARY KEY(gid, user_id))")
        await c.execute("CREATE INDEX IF NOT EXISTS giveaway_entries_inv_idx ON giveaway_entries (gid, inviter_id)")
        await c.execute("CREATE TABLE IF NOT EXISTS giveaway_posts (gid BIGINT NOT NULL, chat_id BIGINT NOT NULL, message_id BIGINT NOT NULL, PRIMARY KEY(gid, chat_id, message_id))")
        await c.execute("CREATE TABLE IF NOT EXISTS giveaway_sent (gid BIGINT NOT NULL, chat_id BIGINT NOT NULL, PRIMARY KEY(gid, chat_id))")
        await c.execute("CREATE TABLE IF NOT EXISTS purchases (id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, amount BIGINT NOT NULL, price BIGINT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', created_at DOUBLE PRECISION NOT NULL DEFAULT 0, file_id TEXT, reviewer BIGINT, reviewed_at DOUBLE PRECISION)")
        # جقستگرام: پروفایل، رسانه، پست، استوری، ری‌اکشن و چت‌روم عمومی
        await c.execute("CREATE TABLE IF NOT EXISTS social_profiles (user_id BIGINT PRIMARY KEY, username TEXT UNIQUE, display_name TEXT NOT NULL, bio TEXT NOT NULL DEFAULT '', avatar_media_id BIGINT, created_at DOUBLE PRECISION NOT NULL)")
        await c.execute("CREATE TABLE IF NOT EXISTS social_media (media_id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, kind TEXT NOT NULL, data BYTEA NOT NULL, content_type TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL, expires_at DOUBLE PRECISION)")
        await c.execute("CREATE TABLE IF NOT EXISTS social_posts (post_id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, caption TEXT NOT NULL DEFAULT '', media_id BIGINT, created_at DOUBLE PRECISION NOT NULL)")
        await c.execute("CREATE INDEX IF NOT EXISTS social_posts_created_idx ON social_posts(created_at DESC)")
        await c.execute("CREATE TABLE IF NOT EXISTS social_stories (story_id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, caption TEXT NOT NULL DEFAULT '', media_id BIGINT, created_at DOUBLE PRECISION NOT NULL, expires_at DOUBLE PRECISION NOT NULL)")
        await c.execute("CREATE INDEX IF NOT EXISTS social_stories_exp_idx ON social_stories(expires_at DESC)")
        await c.execute("CREATE TABLE IF NOT EXISTS social_reactions (post_id BIGINT NOT NULL, user_id BIGINT NOT NULL, emoji TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL, PRIMARY KEY(post_id,user_id))")
        await c.execute("CREATE TABLE IF NOT EXISTS social_room_messages (message_id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL, text TEXT NOT NULL, reply_to BIGINT, premium_emoji_id TEXT, created_at DOUBLE PRECISION NOT NULL)")
        await c.execute("CREATE INDEX IF NOT EXISTS social_room_created_idx ON social_room_messages(created_at DESC)")
        await c.execute("CREATE TABLE IF NOT EXISTS social_room_reactions (message_id BIGINT NOT NULL REFERENCES social_room_messages(message_id) ON DELETE CASCADE, user_id BIGINT NOT NULL, emoji TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL, PRIMARY KEY(message_id,user_id,emoji))")
        await c.execute("CREATE TABLE IF NOT EXISTS social_comments (comment_id BIGSERIAL PRIMARY KEY, post_id BIGINT NOT NULL, user_id BIGINT NOT NULL, text TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL)")
        await c.execute("CREATE INDEX IF NOT EXISTS social_comments_post_idx ON social_comments(post_id, comment_id)")
        await c.execute("CREATE TABLE IF NOT EXISTS social_quota (user_id BIGINT NOT NULL, day TEXT NOT NULL, posts INT NOT NULL DEFAULT 0, stories INT NOT NULL DEFAULT 0, PRIMARY KEY(user_id, day))")
        await c.execute("CREATE INDEX IF NOT EXISTS social_reactions_post_idx ON social_reactions(post_id)")
        STAFF.clear()
        for r in await c.fetch("SELECT user_id, perms FROM support_staff"):
            STAFF[int(r["user_id"])] = {p for p in r["perms"].split(",") if p in SUPPORT_PERMS}
            ADMIN_IDS.add(int(r["user_id"]))
        STAFF_MANAGE.clear()
        for r in await c.fetch("SELECT manager_id, target_id FROM support_manage"):
            STAFF_MANAGE.setdefault(int(r["manager_id"]), set()).add(int(r["target_id"]))
        # مهاجرت یک‌باره‌ی دسترسی‌های جزئی: پشتیبان‌های قدیمی (دارای panel) همون چیزی که قبلاً داشتن رو نگه می‌دارن.
        # قبلاً پشتیبانِ انتخاب‌شده مینی‌اپ و پیام‌های گروه نداشت؛ «لیست پشتیبانی» هم تازه‌ست و پیش‌فرض خاموشه.
        if not await c.fetchval("SELECT 1 FROM bot_settings WHERE key = 'admin_perms_v2'"):
            base = set(ADMIN_PERMS) - {"staff_list", "miniapp", "group_msgs"}
            for _uid, _perms in STAFF.items():
                if "panel" not in _perms:
                    continue
                _perms |= base
                if not any(_uid in v for v in STAFF_MANAGE.values()):
                    _perms |= {"miniapp", "group_msgs"}
                await c.execute("UPDATE support_staff SET perms = $2 WHERE user_id = $1", _uid, ",".join(sorted(_perms)))
            await c.execute("INSERT INTO bot_settings (key, value) VALUES ('admin_perms_v2', '1') ON CONFLICT (key) DO NOTHING")
    await _event_load()
    log.info("database ready")


# ═════════════════ مینی‌اپ پنل پشتیبانی (فقط پشتیبان‌ها) ═════════════════
# گپ‌های گروه‌ها مثل تلگرام + پروفایل کاربرها + کل پنل مدیریت (پول، جق، محرومیت، تسک، پیام همگانی، ...)
WEBAPP_URL = os.environ.get("WEBAPP_URL", "").strip().rstrip("/")   # آدرس عمومی Railway، مثلاً https://xxx.up.railway.app
BOT_REF = None   # خود ربات (برای گرفتن عکس پروفایل و مدیا)؛ تو startup ست میشه

WEBAPP_HTML = r"""<!doctype html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,user-scalable=no">
<title>پنل پشتیبانی</title>
<script src="https://telegram.org/js/telegram-web-app.js"></script>
<style>
:root{--bg:var(--tg-theme-bg-color,#fff);--fg:var(--tg-theme-text-color,#111);--hint:var(--tg-theme-hint-color,#8b8f97);--sec:var(--tg-theme-secondary-bg-color,#f0f2f5);--btn:var(--tg-theme-button-color,#3390ec);--btnt:var(--tg-theme-button-text-color,#fff);--link:var(--tg-theme-link-color,#3390ec);--line:rgba(127,127,127,.2);--bub:#fff;--bubfg:#111;--chat:#d6e0ea;--red:#e5484d;--green:#2ea44f}
body.dark{--bub:#202d3b;--bubfg:#f1f3f5;--chat:#0e1621}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--fg);font:15px/1.55 system-ui,-apple-system,"Segoe UI",Tahoma,sans-serif;overflow:hidden}
button,input,textarea{font:inherit;color:inherit}
#app{display:flex;flex-direction:column;height:100vh;height:100dvh}
#views{flex:1;position:relative;overflow:hidden}
.scr{position:absolute;inset:0;display:flex;flex-direction:column;background:var(--bg)}
.hid{display:none!important}
.scroll{flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch}
.grow{flex:1;min-width:0}
.top{background:var(--sec);padding:10px 12px;border-bottom:1px solid var(--line);flex:none}
.top.row{display:flex;align-items:center;gap:10px}
.ttl{font-size:19px;font-weight:700}
.sub{color:var(--hint);font-size:12.5px}
.top b{font-weight:600}
.ib{background:none;border:0;font-size:20px;padding:4px 8px;cursor:pointer;border-radius:10px;color:var(--fg)}
.ib:active{background:var(--line)}
body.tgback .backib{display:none}
.inp{width:100%;padding:10px 12px;border:1px solid var(--line);border-radius:12px;background:var(--bg);outline:none;margin-top:8px}
textarea.inp{min-height:130px;resize:vertical}
.btn{border:0;background:var(--btn);color:var(--btnt);border-radius:12px;padding:11px 16px;cursor:pointer;font-weight:600;width:100%;margin-top:10px}
.btn.s{width:auto;margin:0;padding:8px 14px}
.btn.ok{background:var(--green);color:#fff}.btn.red{background:var(--red);color:#fff}.btn.gh{background:var(--sec);color:var(--fg)}
.btn:disabled{opacity:.5}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
.chip{border:1px solid var(--line);background:var(--sec);border-radius:18px;padding:5px 12px;cursor:pointer;font-size:13.5px}
.chip.on{background:var(--btn);color:var(--btnt);border-color:var(--btn)}
.empty{text-align:center;color:var(--hint);padding:40px 14px}.empty.err{color:var(--red)}
.badge{background:var(--btn);color:var(--btnt);border-radius:20px;padding:0 8px;font-size:12px;min-width:22px;text-align:center;white-space:nowrap}
.badge.red{background:var(--red)}.badge.gr{background:var(--hint)}.badge.ok{background:var(--green)}
.prem{color:#a35cf0}
#tabs{display:flex;background:var(--sec);border-top:1px solid var(--line);padding-bottom:env(safe-area-inset-bottom);flex:none}
#tabs button{flex:1;border:0;background:none;padding:7px 0 6px;display:flex;flex-direction:column;align-items:center;gap:0;color:var(--hint);cursor:pointer}
#tabs i{font-style:normal;font-size:21px}#tabs span{font-size:11.5px}
#tabs button.on{color:var(--btn);font-weight:600}
#app.deep #tabs{display:none}
/* جقستگرام */
.sg{--sg1:#7b2ff7;--sg2:#f107a3;--sg3:#ff9b42}
.sg-top{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:14px 14px 22px;color:#fff;background:linear-gradient(135deg,#6a1bff 0%,#c11bd8 55%,#ff5d8f 100%);border-radius:0 0 28px 28px;position:relative;overflow:hidden;flex:none}
.sg-top::before{content:'';position:absolute;width:170px;height:170px;border-radius:50%;background:rgba(255,255,255,.12);top:-70px;inset-inline-start:-40px}
.sg-top::after{content:'';position:absolute;width:120px;height:120px;border-radius:50%;background:rgba(255,155,66,.28);bottom:-60px;inset-inline-end:40px}
.sg-logo{display:flex;align-items:center;gap:10px;position:relative;z-index:1}
.sg-cam{width:44px;height:44px;border-radius:15px;background:rgba(255,255,255,.2);display:flex;align-items:center;justify-content:center;font-size:23px}
.sg-brand{font-size:22px;font-weight:900;letter-spacing:-.4px;line-height:1.1}
.sg-tag{font-size:11.5px;opacity:.9}
.sg-ico{position:relative;z-index:1;border:0;background:rgba(255,255,255,.2);color:#fff;width:38px;height:38px;border-radius:50%;font-size:19px;cursor:pointer}
.sg-ico:active{background:rgba(255,255,255,.35)}
.sg-nav{display:flex;gap:6px;margin:-16px 12px 4px;padding:5px;background:var(--bg);border:1px solid var(--line);border-radius:20px;box-shadow:0 6px 18px rgba(120,40,200,.18);position:relative;z-index:2;flex:none}
.sg-nav button{flex:1;border:0;background:none;border-radius:16px;padding:6px 2px;display:flex;flex-direction:column;align-items:center;color:var(--hint);cursor:pointer;transition:.18s}
.sg-nav i{font-style:normal;font-size:18px}.sg-nav span{font-size:11px}
.sg-nav button.on{background:linear-gradient(135deg,var(--sg1),var(--sg2));color:#fff;font-weight:700;box-shadow:0 3px 10px rgba(193,27,216,.4)}
.sg-fab{position:absolute;bottom:18px;inset-inline-end:16px;width:56px;height:56px;border-radius:50%;border:0;color:#fff;font-size:30px;line-height:1;background:linear-gradient(135deg,var(--sg1),var(--sg2) 60%,var(--sg3));box-shadow:0 8px 22px rgba(193,27,216,.5);cursor:pointer;z-index:5;transition:transform .15s}
.sg-fab:active{transform:scale(.9)}
.sg-ban{margin:10px 12px 0;padding:14px;border-radius:18px;color:#fff;background:linear-gradient(135deg,var(--sg1),var(--sg2));display:flex;align-items:center;gap:12px;font-size:13.5px}
.sg-ban .btn{width:auto;margin:0;background:#fff;color:#a71cc8;padding:8px 14px;flex:none}
.sg-strip{display:flex;gap:13px;padding:14px 12px 8px;overflow-x:auto;scrollbar-width:none}.sg-strip::-webkit-scrollbar{display:none}
.sg-st{flex:none;width:68px;text-align:center;font-size:11.5px;cursor:pointer}
.sg-st span{display:block;margin-top:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sg-ring{position:relative;width:68px;height:68px;border-radius:50%;padding:3px;background:conic-gradient(from 200deg,#ff9b42,#f107a3,#7b2ff7,#ff9b42);display:flex;align-items:center;justify-content:center}
.sg-ring>.av{border:3px solid var(--bg)}
.sg-ring.seen,.sg-ring.none{background:var(--line)}
.sg-plus{position:absolute;bottom:-2px;inset-inline-end:-2px;width:22px;height:22px;border-radius:50%;border:2px solid var(--bg);background:var(--btn);color:#fff;font-size:15px;line-height:1;padding:0;cursor:pointer;display:flex;align-items:center;justify-content:center}
.sg-post{margin:12px;background:var(--sec);border-radius:22px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.06);border:1px solid var(--line)}
.sg-ph{display:flex;align-items:center;gap:10px;padding:11px 12px;cursor:pointer}
.sg-ib2{border:0;background:none;font-size:22px;padding:0 6px;color:var(--hint);cursor:pointer}
.sg-pw{position:relative;background:#000;touch-action:manipulation}
.sg-m{position:relative;min-height:120px;background:var(--line);overflow:hidden}
.sg-m img,.sg-m video{display:block;width:100%;max-height:480px;object-fit:cover;background:#000}
.sg-pw .sg-m video{object-fit:contain}
.sg-shim{position:absolute;inset:0;background:linear-gradient(100deg,transparent 30%,rgba(255,255,255,.22) 50%,transparent 70%),var(--line);background-size:200% 100%;animation:sgsh 1.2s infinite linear}
@keyframes sgsh{from{background-position:200% 0}to{background-position:-200% 0}}
.sg-bad{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:var(--hint);font-size:12px}
.sg-big{position:absolute;inset:0;margin:auto;width:96px;height:96px;font-size:80px;line-height:96px;text-align:center;opacity:0;pointer-events:none}
.sg-big.go{animation:sgpop .75s ease}
@keyframes sgpop{0%{transform:scale(.2);opacity:0}25%{transform:scale(1.15);opacity:1}60%{transform:scale(1);opacity:1}100%{transform:scale(1.25);opacity:0}}
.sg-ptext{padding:34px 22px;min-height:170px;display:flex;align-items:center;justify-content:center;text-align:center;font-size:20px;font-weight:800;color:#fff;white-space:pre-wrap;word-break:break-word}
.g0{background:linear-gradient(135deg,#7b2ff7,#f107a3)}.g1{background:linear-gradient(135deg,#ff9b42,#f107a3)}.g2{background:linear-gradient(135deg,#00b4ff,#7b2ff7)}.g3{background:linear-gradient(135deg,#11998e,#38c172)}.g4{background:linear-gradient(135deg,#f7971e,#ff4e50)}
.sg-acts{display:flex;gap:6px;padding:9px 10px 2px}
.sg-act{border:0;background:var(--bg);border-radius:20px;padding:6px 14px;display:flex;align-items:center;gap:6px;cursor:pointer;font-size:14px;font-weight:700;transition:transform .12s}
.sg-act i{font-style:normal;font-size:18px}.sg-act:active{transform:scale(.92)}
.sg-act.on{background:rgba(241,7,163,.13);color:#e0158f}
.sg-act.pulse i{animation:sgpulse .35s}
@keyframes sgpulse{50%{transform:scale(1.6)}}
.sg-cap{padding:7px 14px 4px;white-space:pre-wrap;word-break:break-word}
.sg-more{border:0;background:none;color:var(--hint);padding:2px 14px 12px;font-size:13px;cursor:pointer}
.sg-more:empty{display:none}
.sg-create{display:grid;gap:10px;margin:8px 0 4px}
.sg-cbtn{display:flex;align-items:center;gap:12px;border:0;background:var(--sec);border-radius:18px;padding:13px;cursor:pointer;text-align:start;width:100%}
.sg-cbtn i{font-style:normal;font-size:24px;width:46px;height:46px;border-radius:14px;display:flex;align-items:center;justify-content:center;background:linear-gradient(135deg,var(--sg1),var(--sg2));flex:none}
.sg-cbtn b{flex:1}.sg-cbtn span{font-size:12px;color:var(--hint)}
.sg-pick{width:100%;margin-top:10px;border:2px dashed var(--line);background:var(--sec);border-radius:18px;padding:22px 10px;display:flex;flex-direction:column;align-items:center;gap:4px;cursor:pointer}
.sg-pick i{font-style:normal;font-size:34px}.sg-pick span{font-size:12px;color:var(--hint)}
.sg-prev{position:relative;margin-top:10px;border-radius:16px;overflow:hidden;background:#000}.sg-prev:empty{display:none}
.sg-prev img,.sg-prev video{display:block;width:100%;max-height:300px;object-fit:contain}
.sg-x{position:absolute;top:8px;inset-inline-end:8px;width:32px;height:32px;border-radius:50%;border:0;background:rgba(0,0,0,.6);color:#fff;cursor:pointer}
.sg-cnt{text-align:end;font-size:11.5px;color:var(--hint);margin-top:2px}
.sg-note{font-size:12px;color:var(--hint);background:var(--sec);border-radius:12px;padding:8px 10px;margin-top:8px}
.sg-go{background:linear-gradient(135deg,var(--sg1),var(--sg2))}
.sg-sv{position:fixed;inset:0;z-index:90;background:#000;display:flex;flex-direction:column;touch-action:manipulation;user-select:none;-webkit-user-select:none}
.sg-bars{display:flex;gap:4px;padding:calc(10px + env(safe-area-inset-top)) 10px 6px;position:absolute;top:0;inset-inline:0;z-index:3}
.sg-bars i{flex:1;height:3px;border-radius:3px;background:rgba(255,255,255,.3);overflow:hidden;display:block}.sg-bars b{display:block;height:100%;width:0;background:#fff}
.sg-who{position:absolute;top:calc(22px + env(safe-area-inset-top));inset-inline:0;z-index:3;display:flex;align-items:center;gap:9px;padding:8px 12px 18px;color:#fff;background:linear-gradient(rgba(0,0,0,.55),transparent)}
.sg-ago{font-size:11.5px;opacity:.8}
.sg-ib{border:0;background:rgba(255,255,255,.2);color:#fff;width:36px;height:36px;border-radius:50%;font-size:17px;cursor:pointer}
.sg-stage{flex:1;display:flex;min-height:0}
.sg-stage>.sg-m{flex:1;min-height:0;align-self:stretch;background:#000}
.sg-stage .sg-m img,.sg-stage .sg-m video{position:absolute;inset:0;width:100%;height:100%;max-height:none;object-fit:contain}
.sg-stxt{flex:1;display:flex;align-items:center;justify-content:center;padding:30px;text-align:center;color:#fff;font-size:26px;font-weight:800;white-space:pre-wrap;word-break:break-word}
.sg-scap{position:absolute;bottom:0;inset-inline:0;padding:44px 18px calc(24px + env(safe-area-inset-bottom));color:#fff;font-size:16px;text-align:center;background:linear-gradient(transparent,rgba(0,0,0,.7));white-space:pre-wrap;z-index:3}
.sg-hero{position:relative;text-align:center;padding:0 14px 8px}
.sg-cover{height:96px;margin:0 -14px;background:linear-gradient(135deg,#7b2ff7,#f107a3 60%,#ff9b42)}
.sg-pav{display:inline-block;margin-top:-46px;padding:4px;border-radius:50%;background:var(--bg);position:relative}
.sg-pn{font-size:20px;font-weight:800;margin-top:4px}
.sg-bio{margin:6px auto 0;max-width:340px;font-size:14px;white-space:pre-wrap}
.sg-stats{display:flex;justify-content:center;gap:10px;margin:12px 0 4px}
.sg-stat{background:var(--sec);border-radius:16px;padding:8px 14px;min-width:84px}.sg-stat b{display:block;font-size:18px}.sg-stat span{font-size:12px;color:var(--hint)}
.sg-btns{display:flex;gap:8px;margin:10px 14px 0}.sg-btns .btn{margin:0;flex:1;padding:10px 6px;font-size:13.5px}
.sg-q{display:flex;gap:8px;margin:10px 14px 0}.sg-q div{flex:1;background:var(--sec);border-radius:14px;padding:8px 10px;font-size:12px;color:var(--hint);text-align:center}.sg-q b{display:block;font-size:15px;color:var(--fg)}
.sg-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:4px;padding:8px 12px 96px}
.sg-tile{position:relative;aspect-ratio:1;border-radius:12px;overflow:hidden;background:var(--sec);cursor:pointer}
.sg-tile .sg-m{position:absolute;inset:0;min-height:0}.sg-tile .sg-m img{height:100%;max-height:none}
.sg-ttxt{position:absolute;inset:0;padding:8px;display:flex;align-items:center;justify-content:center;text-align:center;color:#fff;font-size:12px;font-weight:700;overflow:hidden}
.sg-tstat{position:absolute;bottom:0;inset-inline:0;padding:14px 6px 4px;font-size:11px;color:#fff;background:linear-gradient(transparent,rgba(0,0,0,.6));text-align:center}
.sg-tv{position:absolute;top:6px;inset-inline-end:7px;color:#fff;font-size:14px;text-shadow:0 1px 3px #000}
.sg-h{font-weight:800;font-size:16px;padding:14px 14px 2px}
.sg-cl{max-height:46vh;overflow:auto;margin-top:4px}
.sg-c{display:flex;gap:10px;padding:9px 0;align-items:flex-start}
.sg-c .t{flex:1;min-width:0;word-break:break-word;white-space:pre-wrap;font-size:14px}.sg-c b{font-size:13px}.sg-c small{color:var(--hint);margin-inline-start:6px;font-size:11px}
.sg-c .d{border:0;background:none;color:var(--hint);font-size:15px;cursor:pointer;padding:0 4px}
.sg-cbar{display:flex;gap:8px;align-items:center;position:sticky;bottom:-16px;background:var(--bg);padding:10px 0 2px;border-top:1px solid var(--line);margin-top:6px}
.sg-cbar .inp{margin:0}
.sg-sk{margin:12px;border-radius:22px;background:var(--sec);overflow:hidden;height:300px;position:relative}
.sg-gate{text-align:center;padding:44px 22px}.sg-gi{font-size:56px}.sg-gate h3{margin:8px 0 4px}.sg-gate p{color:var(--hint);font-size:13.5px;margin:0 0 6px}
.sg-flex{display:flex;flex-direction:column;overflow:hidden}
.sroom{flex:1;min-height:0;overflow:auto;padding:10px;background:var(--sec)}.smsg{max-width:85%;margin:7px 0;padding:9px 11px;border-radius:14px;background:var(--bg);box-shadow:0 1px 2px #0001;touch-action:pan-y}.smsg.mine{margin-inline-start:auto;background:rgba(177,43,213,.13)}.sreply{border-inline-start:3px solid #b12bd5;padding:3px 7px;margin-bottom:5px;font-size:12px;color:var(--hint)}.sreacts{display:flex;gap:5px;flex-wrap:wrap;margin-top:5px}.sreacts button{border:1px solid var(--line);background:var(--sec);border-radius:14px;padding:2px 7px;font-size:12px}.scomposer{display:flex;gap:7px;padding:9px;background:var(--sec);align-items:center}.scomposer .inp{margin:0;min-width:0}.sfile{display:none}.snotice{margin:12px;padding:12px;border-radius:14px;background:var(--sec);font-size:13px;color:var(--hint)}
.av{border-radius:50%;flex:none;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;overflow:hidden;user-select:none}
.av img{width:100%;height:100%;object-fit:cover;display:block}
/* گپ‌ها / کاربران */
.crow,.urow{display:flex;gap:12px;padding:9px 14px;align-items:center;border-bottom:1px solid var(--line);cursor:pointer}
.crow:active,.urow:active{background:var(--sec)}
.cmid{flex:1;min-width:0}.ctop,.cbot{display:flex;justify-content:space-between;gap:8px;align-items:center}
.ct{overflow:hidden;white-space:nowrap;text-overflow:ellipsis;font-weight:600}.cw{color:var(--hint);font-size:12px;white-space:nowrap}
.cprev{color:var(--hint);font-size:13.5px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.tags{display:flex;gap:5px;flex-wrap:wrap;margin-top:2px;font-size:12px;color:var(--hint)}
/* چت */
.msgs{flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch;background:var(--chat);background-image:radial-gradient(rgba(127,127,127,.1) 1px,transparent 1.6px);background-size:22px 22px;padding:6px 0 16px}
.daysep{text-align:center;margin:10px 0 6px}.daysep span{background:rgba(0,0,0,.28);color:#fff;border-radius:14px;padding:2px 12px;font-size:12.5px}
.older{display:block;margin:8px auto;border:0;background:rgba(0,0,0,.28);color:#fff;border-radius:16px;padding:6px 16px;cursor:pointer}
.mrow{display:flex;align-items:flex-end;gap:6px;padding:1px 8px}.mrow.first{margin-top:7px}
.avsp{width:34px;flex:none}
.bub{max-width:82%;min-width:64px;background:var(--bub);color:var(--bubfg);border-radius:14px;padding:5px 10px 3px;box-shadow:0 1px 1px rgba(0,0,0,.12);word-break:break-word;cursor:pointer}
.mrow.last .bub{border-end-start-radius:4px}
.bub.stk{background:none;box-shadow:none;padding:0}
.nm{font-weight:600;font-size:13px;margin-bottom:1px}.ctag{color:var(--hint);font-weight:400;font-size:11.5px}
.tx{white-space:pre-wrap;font-size:15px}
.lnk{color:var(--link);text-decoration:none}
.mt{font-size:11px;color:var(--hint);text-align:end;margin-top:1px;display:flex;gap:6px;justify-content:flex-end}
.ed{font-style:italic}
.fwd{font-size:12.5px;color:var(--btn);margin-bottom:2px}
.rq{border-inline-start:3px solid var(--btn);background:rgba(127,127,127,.13);border-radius:4px 8px 8px 4px;padding:2px 8px;margin:2px 0 4px}
.rq b{font-size:12.5px;color:var(--btn)}.rqt{font-size:12.5px;color:var(--hint);max-width:230px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.md{margin:2px 0 4px;border-radius:10px;overflow:hidden;background:rgba(127,127,127,.16);min-width:150px;min-height:80px;display:flex;align-items:center;justify-content:center;position:relative}
.md img{display:block;max-width:100%;max-height:340px}
.md-sticker{background:none;min-width:0;min-height:0}.md-sticker img{width:150px;max-height:150px;object-fit:contain}
.play{position:absolute;inset:0;margin:auto;width:46px;height:46px;border-radius:50%;background:rgba(0,0,0,.5);color:#fff;display:flex;align-items:center;justify-content:center;pointer-events:none}
.mph,.chipm{color:var(--hint);font-size:13px;padding:8px 12px}
.qbar{display:flex;gap:8px;padding:6px 12px;background:var(--sec);border-bottom:1px solid var(--line);align-items:center}.qbar .inp{margin:0}
.fchip{padding:6px 12px;background:var(--sec);border-bottom:1px solid var(--line);font-size:13.5px;display:flex;gap:8px;align-items:center}
.fchip button{border:0;background:var(--line);border-radius:50%;width:22px;height:22px;cursor:pointer}
.down{position:absolute;bottom:14px;inset-inline-start:14px;width:42px;height:42px;border-radius:50%;border:0;background:var(--bg);color:var(--fg);box-shadow:0 2px 8px rgba(0,0,0,.3);font-size:20px;cursor:pointer;z-index:3}
.live{color:var(--red)}
/* پروفایل */
.prof{padding-bottom:30px}
.hero{display:flex;flex-direction:column;align-items:center;padding:22px 14px 12px;gap:2px;background:linear-gradient(var(--sec),var(--bg))}
.hero .av{box-shadow:0 3px 14px rgba(0,0,0,.25)}.hn{font-size:21px;font-weight:700;margin-top:8px;text-align:center}
.qa{display:flex;justify-content:center;gap:10px;padding:6px 10px 12px;flex-wrap:wrap}
.qb{width:78px;border:0;background:var(--sec);border-radius:14px;padding:9px 0 7px;cursor:pointer;display:flex;flex-direction:column;align-items:center;gap:2px;font-size:12px;color:var(--btn)}
.qb i{font-style:normal;font-size:22px}
.card{background:var(--sec);border-radius:14px;margin:10px 12px;padding:6px 14px}
.card h3{margin:8px 0 2px;font-size:13.5px;color:var(--btn);font-weight:700}
.kv{display:flex;justify-content:space-between;gap:12px;padding:9px 0;border-bottom:1px solid var(--line);cursor:default}
.kv:last-child{border:0}.kv span:first-child{color:var(--hint);white-space:nowrap}.kv span:last-child{text-align:end;word-break:break-word}
.kv.cp{cursor:pointer}.ltr{direction:ltr;unicode-bidi:embed}
.mini{padding:8px 0;border-bottom:1px solid var(--line);cursor:pointer}.mini:last-child{border:0}
.mini .tx{font-size:14px;max-height:4.6em;overflow:hidden}.mini .sub{display:flex;justify-content:space-between}
/* پنل */
.grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:10px 12px}
.stat{background:var(--sec);border-radius:14px;padding:10px 12px}.stat b{display:block;font-size:19px}.stat span{font-size:12.5px;color:var(--hint)}
.act{border:0;background:var(--sec);border-radius:14px;padding:13px 10px;cursor:pointer;font-weight:600;text-align:center}
.act:active{background:var(--line)}.act.red{color:var(--red)}.act.ok{color:var(--green)}
.row2{display:flex;justify-content:space-between;align-items:center;gap:10px}
.sw{width:50px;height:28px;border-radius:16px;background:var(--hint);position:relative;cursor:pointer;flex:none;transition:.2s}
.sw::after{content:'';position:absolute;top:3px;inset-inline-start:3px;width:22px;height:22px;border-radius:50%;background:#fff;transition:.2s}
.sw.on{background:var(--green)}.sw.on::after{inset-inline-start:25px}
.bars{display:flex;align-items:flex-end;gap:3px;height:70px;margin:8px 0 4px;direction:ltr}.bars i{flex:1;background:var(--btn);border-radius:3px 3px 0 0;min-height:3px;opacity:.85}
.seg{display:flex;background:var(--sec);border-radius:12px;padding:3px;margin-top:8px}.seg button{flex:1;border:0;background:none;padding:8px 4px;border-radius:10px;cursor:pointer;font-size:14px}.seg button.on{background:var(--btn);color:var(--btnt)}
.bar{height:10px;border-radius:6px;background:var(--line);overflow:hidden;margin-top:10px}.bar i{display:block;height:100%;background:var(--green);width:0;transition:.3s}
.lg{padding:8px 0;border-bottom:1px solid var(--line);font-size:14px}.lg:last-child{border:0}
.tk{display:flex;gap:10px;align-items:center;padding:9px 0;border-bottom:1px solid var(--line)}
/* شیت و ابزار */
.ovl{position:fixed;inset:0;background:rgba(0,0,0,.5);z-index:50;display:flex;align-items:flex-end}
.sheet{width:100%;max-height:88%;overflow:auto;background:var(--bg);border-radius:20px 20px 0 0;padding:8px 16px calc(16px + env(safe-area-inset-bottom));animation:up .18s}
@keyframes up{from{transform:translateY(40px);opacity:0}}
.grab{width:40px;height:4px;border-radius:3px;background:var(--line);margin:2px auto 10px}.sh-t{font-weight:700;font-size:16px;margin-bottom:4px}
.sact{display:block;width:100%;text-align:start;border:0;background:none;padding:13px 4px;border-bottom:1px solid var(--line);cursor:pointer;font-size:15px}
.mh{display:flex;gap:12px;align-items:center;padding:4px 0 10px;border-bottom:1px solid var(--line)}
.orig{background:var(--sec);border-radius:12px;padding:8px 12px;margin:10px 0}
.who{display:flex;align-items:center;gap:10px;background:var(--sec);border-radius:12px;padding:8px 10px;margin-top:6px}
.plist{max-height:50vh;overflow:auto;margin-top:6px}
#toast{position:fixed;bottom:84px;inset-inline:16px;margin:auto;max-width:420px;background:#222;color:#fff;border-radius:14px;padding:11px 14px;text-align:center;z-index:99;opacity:0;pointer-events:none;transition:.2s;transform:translateY(10px)}
#toast.show{opacity:1;transform:none}#toast.bad{background:var(--red)}
.vw{position:fixed;inset:0;background:#000;z-index:80;display:flex;align-items:center;justify-content:center}
.vw img{max-width:100%;max-height:100%}.vw .x{position:absolute;top:calc(12px + env(safe-area-inset-top));inset-inline-end:14px;color:#fff;font-size:26px;background:rgba(255,255,255,.15);border:0;border-radius:50%;width:40px;height:40px}
.vw .nv{position:absolute;top:50%;color:#fff;font-size:30px;background:rgba(255,255,255,.15);border:0;border-radius:50%;width:42px;height:42px}
.vw .cnt{position:absolute;bottom:calc(18px + env(safe-area-inset-bottom));color:#fff;font-size:14px}
</style></head><body>
<div id="app">
  <div id="views"></div>
  <nav id="tabs">
    <button data-k="chats" class="on"><i>💬</i><span>گپ‌ها</span></button>
    <button data-k="users"><i>👥</i><span>کاربران</span></button>
    <button data-k="panel"><i>🛠</i><span>پنل</span></button>
    <button data-k="social"><i>📸</i><span>جقستگرام</span></button>
  </nav>
</div>
<div id="toast"></div>
<script>
'use strict';
const tg=window.Telegram&&Telegram.WebApp;
const tgv=v=>!!(tg&&tg.isVersionAtLeast&&tg.isVersionAtLeast(v));
if(tg){try{tg.ready();tg.expand();if(tgv('7.7'))tg.disableVerticalSwipes();tg.setHeaderColor&&tg.setHeaderColor('secondary_bg_color')}catch(e){}} if(!tg||!tg.initData){setTimeout(()=>toast('این مینی‌اپ باید از دکمه «مینی‌اپ پنل پشتیبانی» داخل ربات باز شود.',1),300)}
const setDark=()=>document.body.classList.toggle('dark',tg?tg.colorScheme==='dark':matchMedia('(prefers-color-scheme:dark)').matches);
setDark();if(tg)tg.onEvent('themeChanged',setDark);
if(tgv('6.1'))document.body.classList.add('tgback');

/* ───── ابزارها ───── */
const $=s=>document.querySelector(s);
function h(tag,a,...kids){
  const e=document.createElement(tag);
  if(a)for(const k in a){const v=a[k];if(v==null||v===false)continue;
    if(k==='class')e.className=v;else if(k==='style')e.style.cssText=v;
    else if(k.startsWith('on')||k==='value'||k==='checked'||k==='disabled')e[k]=v;
    else e.setAttribute(k,v===true?'':v)}
  for(const c of kids.flat(2)){if(c==null||c===false)continue;e.append(c.nodeType?c:document.createTextNode(String(c)))}
  return e;
}
const num=n=>Number(n||0).toLocaleString('fa-IR');
const COLORS=['#e17076','#faa774','#a695e7','#7bc862','#6ec9cb','#65aadd','#ee7aae'];
const colorOf=id=>COLORS[Math.abs(Number(id)||0)%7];
const TZ='Asia/Tehran';
const fTime=new Intl.DateTimeFormat('fa-IR',{hour:'2-digit',minute:'2-digit',hour12:false,timeZone:TZ});
const fFull=new Intl.DateTimeFormat('fa-IR',{year:'numeric',month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false,timeZone:TZ});
const fDay=new Intl.DateTimeFormat('fa-IR',{day:'numeric',month:'long',year:'numeric',timeZone:TZ});
const fShort=new Intl.DateTimeFormat('fa-IR',{day:'numeric',month:'short',timeZone:TZ});
const fKey=new Intl.DateTimeFormat('en-CA',{timeZone:TZ});
const dayKey=ts=>fKey.format(ts*1000);
const dayLabel=ts=>{const n=Date.now()/1000,k=dayKey(ts);return k===dayKey(n)?'امروز':k===dayKey(n-86400)?'دیروز':fDay.format(ts*1000)};
const whenShort=ts=>!ts?'':dayKey(ts)===dayKey(Date.now()/1000)?fTime.format(ts*1000):fShort.format(ts*1000);
const oneLine=(t,n=70)=>{t=String(t||'').replace(/\s+/g,' ').trim();return t.length>n?t.slice(0,n)+'…':t};
const ini=n=>{const s=String(n||'?').trim();return s?Array.from(s)[0].toUpperCase():'?'};
const hap=t=>{try{tg&&tgv('6.1')&&tg.HapticFeedback.notificationOccurred(t)}catch(e){}};
let toastT;function toast(m,bad){const t=$('#toast');t.textContent=m;t.className='show'+(bad?' bad':'');clearTimeout(toastT);toastT=setTimeout(()=>t.className='',2800);hap(bad?'error':'success')}
const ask=m=>new Promise(r=>{if(tgv('6.2'))tg.showConfirm(m,ok=>r(!!ok));else r(confirm(m))});
function copy(t){t=String(t);const done=()=>toast('کپی شد ✅');
  if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(t).then(done,()=>fb())}else fb();
  function fb(){const x=h('textarea',{value:t,style:'position:fixed;opacity:0'});document.body.append(x);x.select();try{document.execCommand('copy');done()}catch(e){toast('کپی نشد',1)}x.remove()}}
function openLink(u){if(tg){/^https:\/\/t\.me\//.test(u)?tg.openTelegramLink(u):tg.openLink(u)}else window.open(u,'_blank')}
const URL_RE=/(https?:\/\/[^\s<>()]+|t\.me\/[^\s<>()]+|@[A-Za-z0-9_]{4,32})/g;
function rich(text){
  const f=document.createDocumentFragment();let last=0,m;URL_RE.lastIndex=0;
  while((m=URL_RE.exec(text))){
    if(m.index>last)f.append(text.slice(last,m.index));
    const s=m[0],href=s[0]==='@'?'https://t.me/'+s.slice(1):/^https?:/.test(s)?s:'https://'+s;
    f.append(h('a',{class:'lnk',onclick:e=>{e.stopPropagation();e.preventDefault();openLink(href)}},s));last=m.index+s.length}
  if(last<text.length)f.append(text.slice(last));return f;
}
let dbT;const deb=(fn,ms)=>{clearTimeout(dbT);dbT=setTimeout(fn,ms)};

/* ───── ارتباط با سرور ───── */
const HDR=()=>({'X-Init-Data':tg?tg.initData:''});
async function api(path,body){
  const o={headers:HDR()};
  if(body!==undefined){o.method='POST';o.headers['Content-Type']='application/json';o.body=JSON.stringify(body)}
  const r=await fetch(path,o);let j=null;try{j=await r.json()}catch(e){}
  if(r.status===403)throw new Error((j&&j.error)||'نشست معتبر نیست؛ مینی‌اپ را از دکمه داخل ربات باز کن.');
  if(!r.ok)throw new Error((j&&j.error)||('خطای سرور ('+r.status+')'));
  return j;
}
/* عکس‌ها (آواتار و مدیا) با هدر احراز هویت میان؛ صف ۶تایی، آخرین درخواست اول (چیزی که الان دیده میشه) */
const BL=new Map(),BQ=[];let BA=0;
function pump(){while(BA<6&&BQ.length){const j=BQ.pop();BA++;j().finally(()=>{BA--;pump()})}}
function blob(key,path){
  if(BL.has(key))return BL.get(key);
  const p=new Promise(res=>{BQ.push(()=>fetch(path,{headers:HDR()}).then(r=>r.ok?r.blob():null).then(b=>res(b?URL.createObjectURL(b):null)).catch(()=>res(null)));pump()});
  BL.set(key,p);return p;
}
function avatar(kind,id,name,size,big){
  const c=kind==='chat';
  const d=h('div',{class:'av',style:`width:${size}px;height:${size}px;font-size:${Math.round(size*.42)}px;background:${colorOf(id)}`},ini(name));
  blob((c?'c':'u')+id+(big?'b':'s'),(c?'/api/avatar?chat=':'/api/avatar?uid=')+id+(big?'&big=1':'')).then(u=>{if(u){d.style.background='none';d.replaceChildren(h('img',{src:u,draggable:'false'}))}});
  return d;
}

/* ───── ناوبری ───── */
const views=$('#views'),stack=[];
function setBack(){const deep=stack.length>1;$('#app').classList.toggle('deep',deep);if(tgv('6.1')){deep?tg.BackButton.show():tg.BackButton.hide()}}
function push(el,root){
  if(root){while(stack.length){const s=stack.pop();s.remove();s._destroy&&s._destroy()}}
  else if(stack.length)stack[stack.length-1].classList.add('hid');
  el.classList.remove('hid');views.append(el);stack.push(el);setBack();el._show&&el._show();
}
function back(){
  if(stack.length<2)return;const s=stack.pop();s.remove();s._destroy&&s._destroy();
  const t=stack[stack.length-1];t.classList.remove('hid');setBack();t._show&&t._show();
}
if(tgv('6.1'))tg.BackButton.onClick(back);
const backBtn=()=>h('button',{class:'ib backib',onclick:back},'→');
const openChat=(c,flt)=>push(ChatScreen(c,flt));
const openProfile=uid=>push(ProfileScreen(uid));
const openGroupProfile=chat=>push(GroupProfileScreen(chat));
function GroupProfileScreen(chat){
  const body=h('div',{class:'scroll prof'});
  const el=h('div',{class:'scr'},h('div',{class:'top row'},backBtn(),h('b',{class:'grow'},'پروفایل گپ'),h('button',{class:'ib',onclick:load},'↻')),body);
  async function load(){body.replaceChildren(h('div',{class:'empty'},'در حال دریافت اعضا…'));try{const d=await api('/api/group?chat='+encodeURIComponent(chat.chat_id));const hero=h('div',{class:'hero'},avatar('chat',chat.chat_id,d.title||chat.title,92),h('div',{class:'hn'},d.title||chat.title),h('div',{class:'sub'},num(d.known_count)+' عضو ثبت‌شده / مدیر قابل مشاهده'));const note=h('div',{class:'card'},h('div',{class:'sub'},d.note));const list=h('div',{class:'card'},h('h3',{},'اعضای شناخته‌شده'),...(d.members.length?d.members.map(m=>h('div',{class:'urow',onclick:()=>openProfile(m.user_id)},avatar('user',m.user_id,m.name,42),h('div',{class:'cmid'},h('b',{},m.name||m.user_id),h('div',{class:'sub ltr'},(m.username?'@'+m.username+' • ':'')+m.user_id),h('div',{class:'sub'},num(m.messages)+' پیام • '+(m.is_admin?'مدیر گروه':'عضو ثبت‌شده'))),m.is_admin?h('span',{class:'badge ok'},'مدیر'):null)):[h('div',{class:'empty'},'هنوز عضوی در پیام‌های ربات ثبت نشده')]));body.replaceChildren(hero,note,list)}catch(e){body.replaceChildren(h('div',{class:'empty err'},e.message))}}
  load();return el;
}

/* ───── شیت‌ها ───── */
function sheet(title,build){
  const ov=h('div',{class:'ovl'});const close=()=>ov.remove();
  ov.onclick=e=>{if(e.target===ov)close()};
  ov.append(h('div',{class:'sheet'},h('div',{class:'grab'}),title?h('div',{class:'sh-t'},title):null,build(close)));
  document.body.append(ov);return close;
}
const sact=(t,fn)=>h('button',{class:'sact',onclick:fn},t);
function viewer(count,getUrl,start){
  let i=start||0;const img=h('img'),cnt=h('div',{class:'cnt'});
  const v=h('div',{class:'vw'},img,h('button',{class:'x',onclick:()=>v.remove()},'✕'),cnt);
  async function show(){cnt.textContent=count>1?num(i+1)+' / '+num(count):'';const u=await getUrl(i);if(u)img.src=u;else toast('عکس لود نشد',1)}
  if(count>1){v.append(h('button',{class:'nv',style:'right:10px',onclick:()=>{i=(i+count-1)%count;show()}},'›'),h('button',{class:'nv',style:'left:10px',onclick:()=>{i=(i+1)%count;show()}},'‹'))}
  let x0=null;v.ontouchstart=e=>{x0=e.touches[0].clientX};
  v.ontouchend=e=>{if(x0===null||count<2)return;const dx=e.changedTouches[0].clientX-x0;x0=null;if(Math.abs(dx)>60){i=dx>0?(i+1)%count:(i+count-1)%count;show()}};
  document.body.append(v);show();
}
function userChip(u,onchange){return h('div',{class:'who'},avatar('user',u.id,u.name,36),h('div',{class:'grow'},h('b',{},u.name||u.id),h('div',{class:'sub ltr'},(u.username?'@'+u.username+' • ':'')+u.id)),onchange?h('button',{class:'btn s gh',onclick:onchange},'تغییر'):null)}
function pickUser(cb){
  sheet('انتخاب کاربر',close=>{
    const inp=h('input',{class:'inp',placeholder:'اسم / @یوزرنیم / آیدی عددی',oninput:()=>deb(run,250)});
    const list=h('div',{class:'plist'});
    async function run(){
      const q=inp.value.trim();
      try{const d=await api('/api/users?limit=25&sort=recent&q='+encodeURIComponent(q));
        const rows=d.users.map(u=>h('div',{class:'urow',onclick:()=>{close();cb({id:u.user_id,name:u.name||String(u.user_id),username:u.username})}},avatar('user',u.user_id,u.name,40),h('div',{class:'grow'},h('b',{},u.name||u.user_id),h('div',{class:'sub ltr'},(u.username?'@'+u.username+' • ':'')+u.user_id))));
        if(/^\d{5,}$/.test(q)&&!d.users.some(u=>String(u.user_id)===q))rows.push(h('div',{class:'urow',onclick:()=>{close();cb({id:Number(q),name:q})}},h('b',{},'👤 استفاده از آیدی '+q)));
        list.replaceChildren(...(rows.length?rows:[h('div',{class:'empty'},'کاربری پیدا نشد')]));
      }catch(e){list.replaceChildren(h('div',{class:'empty err'},e.message))}
    }
    run();setTimeout(()=>inp.focus(),80);
    return h('div',{},inp,list);
  });
}
function adjustSheet(field,op0,user,done){
  const money=field==='balance';
  sheet(money?'💰 تغییر پول':'🤛 تغییر تعداد جق',close=>{
    let u=user,op=op0;
    const who=h('div'),seg=h('div',{class:'seg'});
    const inp=h('input',{class:'inp',placeholder:money?'مثلا 50000 یا 100 هزار':'مثلا 20',dir:'auto'});
    const go=h('button',{class:'btn'});
    const drawWho=()=>who.replaceChildren(u?userChip(u,()=>pickUser(x=>{u=x;drawWho()})):h('button',{class:'btn gh',onclick:()=>pickUser(x=>{u=x;drawWho()})},'👤 انتخاب کاربر'));
    const drawSeg=()=>{seg.replaceChildren(h('button',{class:op==='add'?'on':'',onclick:()=>{op='add';drawSeg()}},'➕ افزودن'),h('button',{class:op==='sub'?'on':'',onclick:()=>{op='sub';drawSeg()}},'➖ کم کردن'));go.textContent=op==='add'?'افزودن':'کم کردن';go.className='btn '+(op==='add'?'ok':'red')};
    drawWho();drawSeg();
    go.onclick=async()=>{
      if(!u)return toast('اول کاربر رو انتخاب کن',1);
      if(!inp.value.trim())return toast('مقدار رو بنویس',1);
      go.disabled=true;
      try{const r=await api('/api/user/adjust',{user_id:u.id,field,op,amount:inp.value.trim()});toast('✅ '+num(r.before)+' ← '+num(r.after));close();done&&done(r)}
      catch(e){toast(e.message,1);go.disabled=false}
    };
    const vals=money?[1000,5000,10000,50000,100000,1000000]:[1,5,10,50,100,500];
    return h('div',{},who,seg,inp,h('div',{class:'chips'},vals.map(v=>h('button',{class:'chip',onclick:()=>{inp.value=String(v)}},num(v)))),go);
  });
}
function banSheet(user,done){
  sheet('🚫 محروم کردن',close=>{
    const opts=[['30 دقیقه','۳۰ دقیقه'],['2 ساعت','۲ ساعت'],['1 روز','۱ روز'],['3 روز','۳ روز'],['1 هفته','۱ هفته'],['1 ماه','۱ ماه'],['دائمی','دائمی']];
    let u=user,sel='1 روز';
    const who=h('div'),chips=h('div',{class:'chips'});
    const custom=h('input',{class:'inp',placeholder:'یا خودت بنویس: مثلا 5 ساعت',oninput:()=>{sel='';drawC()}});
    const drawWho=()=>who.replaceChildren(u?userChip(u,()=>pickUser(x=>{u=x;drawWho()})):h('button',{class:'btn gh',onclick:()=>pickUser(x=>{u=x;drawWho()})},'👤 انتخاب کاربر'));
    const drawC=()=>chips.replaceChildren(...opts.map(([v,l])=>h('button',{class:'chip'+(sel===v?' on':''),onclick:()=>{sel=v;custom.value='';drawC()}},l)));
    drawWho();drawC();
    const go=h('button',{class:'btn red',onclick:async()=>{
      if(!u)return toast('اول کاربر رو انتخاب کن',1);
      const dur=custom.value.trim()||sel;go.disabled=true;
      try{const r=await api('/api/user/ban',{user_id:u.id,duration:dur});toast('🚫 '+r.text);close();done&&done(r)}catch(e){toast(e.message,1);go.disabled=false}
    }},'محروم کن');
    return h('div',{},who,chips,custom,go);
  });
}
async function doUnban(u,done){
  if(!await ask('محرومیت '+(u.name||u.id)+' برداشته بشه؟'))return;
  try{await api('/api/user/unban',{user_id:u.id});toast('✅ محرومیت برداشته شد');done&&done()}catch(e){toast(e.message,1)}
}

/* ───── پیام و حباب ───── */
const PLAY=new Set(['video','animation','video_note']);
function mediaBlock(m){
  const d=h('div',{class:'md md-'+m.kind});
  if(m.media){
    d.append(h('span',{class:'mph'},m.tag||'📎'));
    blob('m'+m.chat_id+'_'+m.id,'/api/media?chat='+m.chat_id+'&msg='+m.id).then(u=>{
      if(!u){d.replaceChildren(h('span',{class:'chipm'},m.tag||'📎'));return}
      d.replaceChildren(h('img',{src:u,onclick:e=>{e.stopPropagation();viewer(1,()=>Promise.resolve(u))}}));
      if(PLAY.has(m.kind))d.append(h('span',{class:'play'},'▶'));
    });
  }else d.append(h('span',{class:'chipm'},m.tag||'📎'));
  return d;
}
function bubble(m,first,last,showChat,ctx){
  const c=colorOf(m.user_id);
  const edited=m.edited!=null,body=edited?m.edited:m.text;
  const stk=m.kind==='sticker'&&m.media&&!body&&!m.reply_to&&!m.fwd;
  const b=h('div',{class:'bub'+(stk?' stk':''),onclick:()=>msgSheet(m,ctx)});
  if(first&&!stk)b.append(h('div',{class:'nm',style:'color:'+c,onclick:e=>{e.stopPropagation();openProfile(m.user_id)}},m.name||String(m.user_id),m.premium?h('span',{class:'prem'},' ★'):null,showChat&&m.title?h('span',{class:'ctag'},' • '+m.title):null));
  if(m.fwd)b.append(h('div',{class:'fwd'},'↪ ارسال‌شده از ',h('b',{},m.fwd)));
  if(m.reply_to)b.append(h('div',{class:'rq'},h('b',{},m.reply_name||'پیام'),h('div',{class:'rqt'},oneLine(m.reply_text,90)||'…')));
  if(m.kind&&m.tag)b.append(mediaBlock(m));
  if(body)b.append(h('div',{class:'tx',dir:'auto'},rich(body)));
  b.append(h('div',{class:'mt'},edited?h('span',{class:'ed'},'ویرایش‌شده'):null,h('span',{},fTime.format(m.ts*1000))));
  let av=h('div',{class:'avsp'});
  if(last){av=avatar('user',m.user_id,m.name,34);av.style.cursor='pointer';av.onclick=()=>openProfile(m.user_id)}
  return h('div',{class:'mrow'+(first?' first':'')+(last?' last':'')},av,b);
}
function msgSheet(m,ctx){
  sheet(null,close=>{
    const kids=[h('div',{class:'mh'},avatar('user',m.user_id,m.name,46),h('div',{class:'grow'},h('b',{},m.name||m.user_id),h('div',{class:'sub ltr'},(m.username?'@'+m.username+' • ':'')+m.user_id),h('div',{class:'sub'},fFull.format(m.ts*1000)+(m.title?' • '+m.title:''))))];
    if(m.edited!=null)kids.push(h('div',{class:'orig'},h('div',{class:'sub'},'متن اصلی (قبل از ویرایش):'),h('div',{class:'tx',dir:'auto'},m.text||'—'),h('div',{class:'sub'},'ویرایش: '+fFull.format((m.edited_ts||m.ts)*1000))));
    kids.push(sact('👤 پروفایل کاربر',()=>{close();openProfile(m.user_id)}));
    if(ctx&&ctx.filter)kids.push(sact('🔎 فقط پیام‌های این کاربر',()=>{close();ctx.filter({id:m.user_id,name:m.name})}));
    else kids.push(sact('🔎 همه‌ی پیام‌های این کاربر',()=>{close();openChat({chat_id:'all',title:'پیام‌های '+(m.name||m.user_id)},{id:m.user_id,name:m.name})}));
    const t=m.edited!=null?m.edited:m.text;
    if(t)kids.push(sact('📋 کپی متن',()=>{close();copy(t)}));
    kids.push(sact('🆔 کپی آیدی عددی',()=>{close();copy(m.user_id)}));
    if(m.link)kids.push(sact('🔗 باز کردن پیام در تلگرام',()=>{close();openLink(m.link)}));
    return h('div',{},kids);
  });
}

/* ───── تب گپ‌ها ───── */
function ChatsScreen(){
  const list=h('div',{class:'scroll'}),sub=h('div',{class:'sub'});
  const search=h('input',{class:'inp',placeholder:'جست‌وجوی گروه…',oninput:()=>draw()});
  const el=h('div',{class:'scr'},h('div',{class:'top'},h('div',{class:'row2'},h('div',{class:'ttl'},'گپ‌ها'),h('button',{class:'ib',onclick:()=>load(true)},'↻')),sub,search),list);
  let data=[],t0=0;
  function draw(){
    const q=search.value.trim().toLowerCase();
    const rows=data.filter(c=>!q||String(c.title||c.chat_id).toLowerCase().includes(q));
    const all=h('div',{class:'crow',onclick:()=>openChat({chat_id:'all',title:'همه‌ی گروه‌ها'})},h('div',{class:'av',style:'width:52px;height:52px;font-size:24px;background:var(--btn)'},'📚'),h('div',{class:'cmid'},h('div',{class:'ctop'},h('b',{class:'ct'},'همه‌ی گروه‌ها'),h('span',{class:'badge gr'},num(data.reduce((s,c)=>s+c.n,0)))),h('div',{class:'cprev'},'همه‌ی پیام‌های ثبت‌شده، یکجا')));
    list.replaceChildren(all,...rows.map(c=>h('div',{class:'crow',onclick:()=>openChat(c)},avatar('chat',c.chat_id,c.title||'?',52),h('div',{class:'cmid'},h('div',{class:'ctop'},h('b',{class:'ct'},c.title||String(c.chat_id)),h('span',{class:'cw'},whenShort(c.last_ts))),h('div',{class:'cbot'},h('span',{class:'cprev'},c.last_name?c.last_name+': '+oneLine(c.last_text,60):'هنوز پیامی ثبت نشده'),h('span',{class:'badge'},num(c.n)))))));
    if(!rows.length&&data.length)list.append(h('div',{class:'empty'},'گروهی با این اسم نیست'));
  }
  async function load(force){
    if(!force&&Date.now()-t0<15000)return;
    try{data=await api('/api/groups');t0=Date.now();sub.textContent=num(data.length)+' گروه • '+num(data.reduce((s,c)=>s+c.n,0))+' پیام';draw()}
    catch(e){list.replaceChildren(h('div',{class:'empty err'},e.message))}
  }
  el._show=()=>load(false);return el;
}

/* ───── صفحه‌ی گپ (مثل تلگرام) ───── */
function ChatScreen(chat,flt0){
  const S={flt:flt0||null,q:'',msgs:[],more:false,total:0,timer:null,busy:false};
  const all=chat.chat_id==='all';
  const box=h('div',{class:'msgs'}),sub=h('div',{class:'sub'});
  const qin=h('input',{class:'inp',placeholder:'جست‌وجو در متن پیام‌ها…',onkeydown:e=>{if(e.key==='Enter'){S.q=qin.value.trim();reload()}}});
  const qbar=h('div',{class:'qbar hid'},qin,h('button',{class:'btn s',onclick:()=>{S.q=qin.value.trim();reload()}},'برو'));
  const fchip=h('div',{class:'fchip hid'});
  const liveB=h('button',{class:'ib',onclick:()=>toggleLive()},'●');
  const down=h('button',{class:'down hid',onclick:()=>box.scrollTo({top:box.scrollHeight,behavior:'smooth'})},'↓');
  const head=h('div',{class:'top row'},backBtn(),all?h('div',{class:'av',style:'width:38px;height:38px;background:var(--btn)'},'📚'):h('div',{onclick:()=>openGroupProfile(chat),style:'cursor:pointer'},avatar('chat',chat.chat_id,chat.title,38)),h('div',{class:'grow',onclick:()=>{if(!all)openGroupProfile(chat)}},h('b',{class:'ct',style:'display:block'},chat.title||chat.chat_id),sub),h('button',{class:'ib',onclick:()=>reload()},'↻'),h('button',{class:'ib',onclick:()=>{qbar.classList.toggle('hid');if(!qbar.classList.contains('hid'))qin.focus()}},'🔍'),liveB);
  const wrap=h('div',{style:'flex:1;position:relative;display:flex;flex-direction:column;min-height:0'},box,down);
  const el=h('div',{class:'scr'},head,qbar,fchip,wrap);
  const ctx={filter:u=>{S.flt=u;reload()}};
  box.onscroll=()=>down.classList.toggle('hid',box.scrollHeight-box.scrollTop-box.clientHeight<160);
  const P=ex=>{const p=new URLSearchParams({chat:chat.chat_id,limit:ex.limit||500});if(S.flt)p.set('user',S.flt.id);if(S.q)p.set('q',S.q);for(const k in ex)p.set(k,ex[k]);return p};
  function head2(){
    sub.textContent=num(S.total)+' پیام'+(S.flt||S.q?' (فیلتر شده)':'')+(S.msgs.length<S.total?' • '+num(S.msgs.length)+' نمایش داده‌شده':'');
    fchip.classList.toggle('hid',!S.flt);fchip.replaceChildren(...(S.flt?['👤 فقط پیام‌های '+(S.flt.name||S.flt.id),h('button',{onclick:()=>{S.flt=null;reload()}},'✕')]:[]));
  }
  function render(mode){
    const pt=box.scrollTop,ph=box.scrollHeight,f=document.createDocumentFragment();
    if(!S.msgs.length){box.replaceChildren(h('div',{class:'empty'},'پیامی پیدا نشد. (فقط پیام‌هایی که ربات دیده ثبت میشن)'));head2();return}
    f.append(S.more?h('button',{class:'older',onclick:older},'⬆ نمایش ۵۰۰ پیام قدیمی‌تر'):h('div',{class:'daysep'},h('span',{},'ابتدای پیام‌های ثبت‌شده')));
    let lastDay='',prev=null;
    S.msgs.forEach((m,i)=>{
      const dk=dayKey(m.ts);
      if(dk!==lastDay){f.append(h('div',{class:'daysep'},h('span',{},dayLabel(m.ts))));lastDay=dk;prev=null}
      const nx=S.msgs[i+1];
      const first=!prev||prev.user_id!==m.user_id||prev.chat_id!==m.chat_id||m.ts-prev.ts>300;
      const grouped=nx&&nx.user_id===m.user_id&&nx.chat_id===m.chat_id&&dayKey(nx.ts)===dk&&nx.ts-m.ts<=300;
      f.append(bubble(m,first,!grouped,all,ctx));prev=m;
    });
    box.replaceChildren(f);
    if(mode==='bottom')box.scrollTop=box.scrollHeight;
    else if(mode==='keep')box.scrollTop=pt+(box.scrollHeight-ph);
    else box.scrollTop=pt;
    head2();
  }
  async function reload(){
    S.busy=true;box.replaceChildren(h('div',{class:'empty'},'در حال بارگذاری…'));
    try{const d=await api('/api/messages?'+P({}));S.msgs=d.msgs;S.more=d.more;S.total=d.total||0;render('bottom')}
    catch(e){box.replaceChildren(h('div',{class:'empty err'},e.message))}
    S.busy=false;
  }
  async function older(){
    if(S.busy||!S.msgs.length)return;S.busy=true;const f=S.msgs[0];
    try{const d=await api('/api/messages?'+P({bts:f.ts,bid:f.id}));S.msgs=d.msgs.concat(S.msgs);S.more=d.more;render('keep')}catch(e){toast(e.message,1)}
    S.busy=false;
  }
  async function poll(){
    if(S.busy)return;const l=S.msgs[S.msgs.length-1];
    try{const d=await api('/api/messages?'+P({ats:l?l.ts:0,aid:l?l.id:0,limit:200}));
      if(d.msgs.length){const nb=box.scrollHeight-box.scrollTop-box.clientHeight<160;S.msgs=S.msgs.concat(d.msgs);S.total+=d.msgs.length;render(nb?'bottom':'stay')}}catch(e){}
  }
  function toggleLive(){
    if(S.timer){clearInterval(S.timer);S.timer=null;liveB.classList.remove('live');toast('حالت زنده خاموش شد')}
    else{S.timer=setInterval(poll,5000);liveB.classList.add('live');toast('🔴 حالت زنده روشن شد (هر ۵ ثانیه)')}
  }
  el._destroy=()=>{clearInterval(S.timer)};
  reload();return el;
}

/* ───── تب کاربران ───── */
function UsersScreen(onlyBanned){
  let sort='recent',offset=0,total=0;
  const list=h('div',{class:'scroll'}),sub=h('div',{class:'sub'});
  const inp=h('input',{class:'inp',placeholder:'جست‌وجو: اسم / @یوزرنیم / آیدی',oninput:()=>deb(()=>load(true),300)});
  const chips=h('div',{class:'chips'});
  const SORTS=[['recent','جدیدترین فعالیت'],['balance','پردارایی‌ها'],['jaghs','بیشترین جق'],['name','الفبا']];
  const drawChips=()=>chips.replaceChildren(...SORTS.map(([k,l])=>h('button',{class:'chip'+(sort===k?' on':''),onclick:()=>{sort=k;drawChips();load(true)}},l)));
  drawChips();
  const el=h('div',{class:'scr'},h('div',{class:'top'},h('div',{class:'row2'},onlyBanned?h('div',{class:'row2',style:'gap:6px'},backBtn(),h('div',{class:'ttl'},'🚫 محروم‌ها')):h('div',{class:'ttl'},'کاربران'),h('button',{class:'ib',onclick:()=>load(true)},'↻')),sub,inp,onlyBanned?null:chips),list);
  const row=u=>h('div',{class:'urow',onclick:()=>openProfile(u.user_id)},avatar('user',u.user_id,u.name,46),h('div',{class:'cmid'},h('div',{class:'ctop'},h('b',{class:'ct'},u.name||String(u.user_id),u.premium?h('span',{class:'prem'},' ★'):null),u.banned?h('span',{class:'badge red'},'محروم'):null),h('div',{class:'sub ltr',style:'text-align:start'},(u.username?'@'+u.username+' • ':'')+u.user_id),h('div',{class:'tags'},u.in_game?[h('span',{},'💰 '+num(u.balance)),h('span',{},'🤛 '+num(u.jaghs)),h('span',{},u.started?'✅ استارت':'⬜ استارت نزده')]:h('span',{},'فقط تو گروه‌ها دیده شده'))));
  async function load(reset){
    if(reset){offset=0;list.replaceChildren(h('div',{class:'empty'},'در حال بارگذاری…'))}
    try{
      const d=await api('/api/users?limit=60&offset='+offset+'&sort='+sort+(onlyBanned?'&banned=1':'')+'&q='+encodeURIComponent(inp.value.trim()));
      total=d.total;sub.textContent=num(d.total)+' کاربر';
      const more=list.querySelector('.moreb');more&&more.remove();
      if(reset)list.replaceChildren();
      if(!d.users.length&&reset){list.replaceChildren(h('div',{class:'empty'},'کاربری پیدا نشد'));return}
      d.users.forEach(u=>list.append(row(u)));offset+=d.users.length;
      if(offset<total)list.append(h('button',{class:'btn gh moreb',style:'width:calc(100% - 28px);margin:12px 14px',onclick:()=>load(false)},'نمایش بیشتر'));
    }catch(e){list.replaceChildren(h('div',{class:'empty err'},e.message))}
  }
  let t0=0;el._show=()=>{if(Date.now()-t0>20000){t0=Date.now();load(true)}};
  return el;
}

/* ───── پروفایل کاربر (مثل پروفایل تلگرام) ───── */
function ProfileScreen(uid){
  const body=h('div',{class:'scroll prof'});
  const el=h('div',{class:'scr'},h('div',{class:'top row'},backBtn(),h('b',{class:'grow'},'پروفایل'),h('button',{class:'ib',onclick:()=>load()},'↻')),body);
  async function load(){
    body.replaceChildren(h('div',{class:'empty'},'در حال بارگذاری…'));
    let d;try{d=await api('/api/user?id='+uid)}catch(e){body.replaceChildren(h('div',{class:'empty err'},e.message));return}
    draw(d);
  }
  function kv(label,val,cp){if(val==null||val==='')return null;return h('div',{class:'kv'+(cp?' cp':''),onclick:cp?()=>copy(val):null},h('span',{},label),h('span',{class:cp?'ltr':''},val))}
  function draw(d){
    const name=d.name||String(uid),g=d.game,u={id:uid,name,username:d.username};
    const avb=avatar('user',uid,name,104,true);
    if(d.photos!==0){avb.style.cursor='pointer';avb.onclick=()=>viewer(Math.max(d.photos||1,1),i=>blob('uph'+uid+'_'+i,'/api/avatar?uid='+uid+'&big=1&i='+i))}
    const last=d.msgs&&d.msgs.last_ts;
    const nodes=[h('div',{class:'hero'},avb,h('div',{class:'hn'},name,d.premium?h('span',{class:'prem'},' ★'):null),h('div',{class:'sub'},d.username?'@'+d.username:'بدون نام کاربری'),h('div',{class:'sub'},last?'آخرین پیام: '+dayLabel(last)+' '+fTime.format(last*1000):'پیامی از این کاربر ثبت نشده'))];
    const qb=(i,t,fn)=>h('button',{class:'qb',onclick:fn},h('i',{},i),t);
    const qa=h('div',{class:'qa'},qb('💬','پیام‌ها',()=>openChat({chat_id:'all',title:'پیام‌های '+name},{id:uid,name})),qb('💰','پول',()=>adjustSheet('balance','add',u,load)),qb('🤛','جق',()=>adjustSheet('jaghs','add',u,load)));
    if(!d.is_admin)qa.append(g&&g.banned?qb('✅','رفع محرومیت',()=>doUnban(u,load)):qb('🚫','محروم‌کردن',()=>banSheet(u,load)));
    nodes.push(qa);
    const bd=d.birthdate?(d.birthdate.day+'/'+d.birthdate.month+(d.birthdate.year?'/'+d.birthdate.year:'')):null;
    nodes.push(h('div',{class:'card'},h('h3',{},'اطلاعات'),kv('شناسه عددی',String(uid),true),kv('نام کاربری',d.username?'@'+d.username:null,true),kv('نام',[d.first_name,d.last_name].filter(Boolean).join(' ')||null),kv('بیو',d.bio),kv('تاریخ تولد',bd),kv('زبان',d.lang),kv('تعداد عکس پروفایل',d.photos!=null?num(d.photos):null),kv('تلگرام پرمیوم',d.premium?'بله ★':null),kv('اولین‌بار دیده شد',d.first_seen?fFull.format(d.first_seen*1000):null),kv('پیام‌های ثبت‌شده',num(d.msgs.n)),kv('اولین پیام ثبت‌شده',d.msgs.first_ts?fFull.format(d.msgs.first_ts*1000):null)));
    if(g){
      nodes.push(h('div',{class:'card'},h('h3',{},'بازی'),kv('دارایی',num(g.balance)+' تومن'),kv('تعداد جق',num(g.jaghs)),kv('سطح',g.level?num(g.level)+(g.character?' • '+g.character:''):null),kv('دفعات کار',num(g.work_count)),kv('استارت ربات',g.started?'زده ✅':'نزده'),kv('وضعیت',g.banned?'🚫 محروم '+g.banned_text:'آزاد')));
    }else nodes.push(h('div',{class:'card'},h('h3',{},'بازی'),h('div',{class:'sub',style:'padding:8px 0'},'این کاربر هنوز با ربات بازی نکرده. اگه پول/جق بدی یا محرومش کنی، براش حساب ساخته میشه.')));
    if(d.groups.length)nodes.push(h('div',{class:'card'},h('h3',{},'گروه‌ها'),d.groups.map(x=>h('div',{class:'kv cp',onclick:()=>openChat({chat_id:x.chat_id,title:x.title||String(x.chat_id)},{id:uid,name})},h('span',{},x.title||String(x.chat_id)),h('span',{},num(x.n)+' پیام • '+whenShort(x.last_ts))))));
    const rec=h('div',{class:'card'},h('h3',{},'آخرین پیام‌ها'),h('div',{class:'sub',style:'padding:8px 0'},'…'));
    nodes.push(rec);
    if(d.msgs.n)nodes.push(h('button',{class:'btn',style:'width:calc(100% - 24px);margin:6px 12px',onclick:()=>openChat({chat_id:'all',title:'پیام‌های '+name},{id:uid,name})},'همه‌ی '+num(d.msgs.n)+' پیام این کاربر ←'));
    body.replaceChildren(...nodes);
    api('/api/messages?chat=all&limit=15&user='+uid).then(r=>{
      rec.replaceChildren(h('h3',{},'آخرین پیام‌ها'),...(r.msgs.length?r.msgs.slice().reverse().map(m=>h('div',{class:'mini',onclick:()=>msgSheet(m,null)},h('div',{class:'tx',dir:'auto'},(m.kind&&m.tag?m.tag+' ':'')+(m.edited!=null?m.edited:m.text||'')),h('div',{class:'sub'},h('span',{},m.title||''),h('span',{},dayLabel(m.ts)+' '+fTime.format(m.ts*1000))))):[h('div',{class:'sub',style:'padding:8px 0'},'پیامی ثبت نشده')]));
    }).catch(()=>{});
  }
  load();return el;
}

/* ───── تب پنل ───── */
const ACT={money_add:'➕ پول',money_sub:'➖ پول',jagh_add:'➕ جق',jagh_sub:'➖ جق',ban:'🚫 محرومیت',unban:'✅ رفع محرومیت',task_add:'📋 تسک جدید',task_del:'🗑 حذف تسک',toggle:'🔌 روشن/خاموش ربات',broadcast:'📢 پیام همگانی'};
function PanelScreen(){
  const body=h('div',{class:'scroll'});
  const el=h('div',{class:'scr'},h('div',{class:'top'},h('div',{class:'row2'},h('div',{class:'ttl'},'🛠 پنل پشتیبانی'),h('button',{class:'ib',onclick:()=>load()},'↻'))),body);
  async function load(){
    let me,st=null;try{me=await api('/api/me');if((me.perms||[]).includes('stats'))st=await api('/api/stats')}catch(e){body.replaceChildren(h('div',{class:'empty err'},e.message));return}
    const P=new Set(me.perms||[]),has=k=>P.has(k);
    const sw=!has('bot_toggle')?h('span',{}):h('div',{class:'sw'+(me.bot_enabled?' on':''),onclick:async()=>{
      if(!await ask(me.bot_enabled?'ربات خاموش بشه؟ (فقط پشتیبان‌ها می‌تونن ازش استفاده کنن)':'ربات روشن بشه؟'))return;
      try{const r=await api('/api/toggle',{});toast(r.enabled?'🟢 ربات روشن شد':'🔴 ربات خاموش شد');load()}catch(e){toast(e.message,1)}}});
    const S=!st?[]:[['👥 کاربران',st.users],['✅ استارت‌کرده',st.started],['💬 گروه‌ها',st.chats],['✉️ پیام‌های ثبت‌شده',st.msgs],['🔥 پیام ۲۴ ساعت اخیر',st.msgs24],['🧑‍🤝‍🧑 فعال ۲۴ ساعت اخیر',st.active24],['💰 مجموع دارایی',st.balance],['🤛 مجموع جق‌ها',st.jaghs],['🚫 محروم‌ها',st.banned],['🪪 اعضای دیده‌شده',st.members]];
    const mx=st?Math.max(1,...st.hours):1;
    const mk=(k,cls,label,fn)=>has(k)?h('button',{class:'act '+cls,onclick:fn},label):null;
    const grp1=[mk('money_sub','red','💸 کم کردن پول',()=>adjustSheet('balance','sub',null)),mk('money_add','ok','➕ افزودن پول',()=>adjustSheet('balance','add',null)),
      mk('jagh_sub','red','🤛 کم کردن جق',()=>adjustSheet('jaghs','sub',null)),mk('jagh_add','ok','➕ افزودن جق',()=>adjustSheet('jaghs','add',null))].filter(Boolean);
    const grp2=[mk('ban','red','🚫 محروم کردن',()=>banSheet(null)),mk('unban','ok','✅ رفع محرومیت',()=>pickUser(u=>doUnban(u))),
      (has('ban')||has('unban'))?h('button',{class:'act',onclick:()=>push(UsersScreen(true))},'📛 لیست محروم‌ها'):null,
      (has('task_daily')||has('task_weekly'))?h('button',{class:'act',onclick:()=>push(TasksScreen())},'📋 تسک‌ها'):null,
      mk('broadcast','','📢 پیام همگانی',()=>push(BroadcastScreen())),
      h('button',{class:'act',onclick:()=>push(LogScreen())},'🧾 گزارش عملیات')].filter(Boolean);
    body.replaceChildren(
      h('div',{class:'card'},h('div',{class:'row2',style:'padding:10px 0'},h('div',{},h('b',{},'وضعیت ربات'),h('div',{class:'sub'},me.bot_enabled?'🟢 روشن':'🔴 خاموش')),sw)),
      h('div',{class:'grid'},S.map(([l,v])=>h('div',{class:'stat'},h('b',{},num(v)),h('span',{},l)))),
      st?h('div',{class:'card'},h('h3',{},'پیام‌های ۲۴ ساعت اخیر (هر ستون = یک ساعت)'),h('div',{class:'bars'},st.hours.map((n,i)=>h('i',{style:'height:'+Math.round(n/mx*100)+'%',title:num(n)}))),st.top.length?h('div',{style:'margin-top:8px'},h('div',{class:'sub'},'پرحرف‌های ۲۴ ساعت اخیر'),st.top.map(t=>h('div',{class:'kv cp',onclick:()=>openProfile(t.user_id)},h('span',{},t.name||t.user_id),h('span',{},num(t.n)+' پیام')))):null):null,
      grp1.length?h('div',{class:'card'},h('h3',{},'پول و جق'),h('div',{class:'grid',style:'margin:6px 0 10px'},grp1)):null,
      h('div',{class:'card'},h('h3',{},'مدیریت'),h('div',{class:'grid',style:'margin:6px 0 10px'},grp2)),
      h('div',{class:'sub',style:'text-align:center;padding:6px 12px 24px'},(me.version||'')+' • نگهداری پیام‌ها: '+(me.retention_days?num(me.retention_days)+' روز':'نامحدود'))
    );
  }
  let t0=0;el._show=()=>{if(Date.now()-t0>10000){t0=Date.now();load()}};
  return el;
}

function BroadcastScreen(){
  let target='u',html=false,job=null,timer=null;
  const txt=h('textarea',{class:'inp',placeholder:'متن پیام همگانی…',maxlength:'4096'});
  const seg=h('div',{class:'seg'}),info=h('div',{class:'sub',style:'margin-top:8px'}),prog=h('div',{class:'card hid'});
  const T=[['u','👤 کاربرها'],['g','👥 گروه‌ها'],['a','📣 هر دو'],['me','🧪 فقط خودم']];
  const drawSeg=()=>seg.replaceChildren(...T.map(([k,l])=>h('button',{class:target===k?'on':'',onclick:()=>{target=k;drawSeg();count()}},l)));
  let st=null;async function count(){try{st=st||await api('/api/stats');const n=target==='u'?st.started:target==='g'?st.chats:target==='a'?st.started+st.chats:1;info.textContent='گیرنده‌ها: حدود '+num(n)+' نفر/گروه'}catch(e){}}
  const htmlCb=h('input',{type:'checkbox',onchange:e=>{html=e.target.checked}});
  const go=h('button',{class:'btn',onclick:send},'📢 ارسال');
  async function send(){
    const text=txt.value.trim();if(!text)return toast('متن پیام خالیه',1);
    if(target!=='me'&&!await ask('این پیام برای '+T.find(t=>t[0]===target)[1]+' فرستاده بشه؟ ('+info.textContent+')'))return;
    go.disabled=true;
    try{const r=await api('/api/bc/start',{target,text,html});
      if(r.done){toast('✅ برای خودت فرستاده شد')}else{job=r.id;prog.classList.remove('hid');poll();timer=setInterval(poll,1500);toast('ارسال شروع شد')}}
    catch(e){toast(e.message,1)}
    go.disabled=false;
  }
  async function poll(){
    try{const j=await api('/api/bc/status?id='+job),d=j.ok+j.fail;
      prog.replaceChildren(h('h3',{},j.running?'⏳ در حال ارسال…':'✅ ارسال تموم شد'),h('div',{class:'kv'},h('span',{},'موفق'),h('span',{},num(j.ok))),h('div',{class:'kv'},h('span',{},'ناموفق'),h('span',{},num(j.fail))),h('div',{class:'kv'},h('span',{},'کل'),h('span',{},num(j.total))),h('div',{class:'bar'},h('i',{style:'width:'+(j.total?Math.round(d/j.total*100):0)+'%'})));
      if(!j.running){clearInterval(timer);timer=null}}catch(e){clearInterval(timer)}
  }
  drawSeg();count();
  const el=h('div',{class:'scr'},h('div',{class:'top row'},backBtn(),h('b',{class:'grow'},'📢 پیام همگانی')),h('div',{class:'scroll',style:'padding:12px'},h('div',{class:'sub'},'مخاطب'),seg,info,txt,h('label',{class:'sub',style:'display:flex;gap:8px;align-items:center;margin-top:8px'},htmlCb,'پیام با فرمت HTML نوشته شده (مثل <b>متن</b>) — قبلش یه نسخه‌ی آزمایشی برای خودت میاد'),go,prog));
  el._destroy=()=>clearInterval(timer);return el;
}

function TasksScreen(){
  const list=h('div',{class:'card'});let period='daily';let metrics=[];
  const inp=h('input',{class:'inp',placeholder:'عنوان دلخواه (اختیاری برای تسک خودکار)…',maxlength:'300'});
  const sel=h('select',{class:'inp'});
  const tg=h('input',{class:'inp',placeholder:'تعداد هدف (برای واریز بانک: مبلغ)',inputmode:'numeric'});
  const rw=h('input',{class:'inp',placeholder:'جایزه به تومن (مثلا 300000)',inputmode:'numeric'});
  const seg=h('div',{class:'seg'});
  const drawSeg=()=>seg.replaceChildren(h('button',{class:period==='daily'?'on':'',onclick:()=>{period='daily';drawSeg()}},'روزانه'),h('button',{class:period==='weekly'?'on':'',onclick:()=>{period='weekly';drawSeg()}},'هفتگی'));drawSeg();
  const fill=()=>sel.replaceChildren(h('option',{value:''},'تسک متنی (بدون جایزه)'),...metrics.map(m=>h('option',{value:m.key},m.label)));
  async function load(){
    try{const d=await api('/api/tasks');metrics=d.metrics||[];fill();
      list.replaceChildren(h('h3',{},'تسک‌های فعال'),...(d.tasks.length?d.tasks.map(t=>h('div',{class:'tk'},h('span',{class:'badge '+(t.period==='daily'?'':'gr')},t.period==='daily'?'روزانه':'هفتگی'),h('div',{class:'grow'},t.title,h('div',{class:'sub'},(t.metric?('💰 '+num(t.reward)+' تومن • '):'')+num(t.done)+' نفر انجام داده')),h('button',{class:'ib',onclick:async()=>{if(!await ask('این تسک حذف بشه؟'))return;try{await api('/api/task/delete',{task_id:t.task_id});toast('حذف شد');load()}catch(e){toast(e.message,1)}}},'🗑'))):[h('div',{class:'sub',style:'padding:10px 0'},'تسکی نیست')]));
    }catch(e){list.replaceChildren(h('div',{class:'empty err'},e.message))}
  }
  const add=h('button',{class:'btn',onclick:async()=>{const t=inp.value.trim();const m=sel.value;
    if(!m&&!t)return toast('متن تسک خالیه',1);
    if(m&&(!tg.value||!rw.value))return toast('هدف و جایزه رو وارد کن',1);
    try{await api('/api/task/add',{period,title:t,metric:m,target:tg.value,reward:rw.value});inp.value='';tg.value='';rw.value='';toast('✅ تسک اضافه شد');load()}catch(e){toast(e.message,1)}}},'➕ افزودن تسک');
  load();
  return h('div',{class:'scr'},h('div',{class:'top row'},backBtn(),h('b',{class:'grow'},'📋 تسک‌ها')),h('div',{class:'scroll'},list,h('div',{class:'card',style:'padding-bottom:14px'},h('h3',{},'تسک جدید'),seg,sel,tg,rw,inp,add)));
}

function LogScreen(){
  const list=h('div',{class:'card'});
  (async()=>{try{const d=await api('/api/log');list.replaceChildren(...(d.length?d.map(x=>h('div',{class:'lg'},h('div',{class:'row2'},h('b',{},ACT[x.action]||x.action),h('span',{class:'sub'},fFull.format(x.ts*1000))),h('div',{class:'sub'},'توسط '+(x.admin_name||x.admin_id)+(x.target?' • روی '+x.target:'')),x.details?h('div',{},x.details):null)):[h('div',{class:'empty'},'هنوز عملیاتی ثبت نشده')]))}catch(e){list.replaceChildren(h('div',{class:'empty err'},e.message))}})();
  return h('div',{class:'scr'},h('div',{class:'top row'},backBtn(),h('b',{class:'grow'},'🧾 گزارش عملیات')),h('div',{class:'scroll'},list));
}

/* ───── جقستگرام ───── */
async function imageData(file){
  if(!file)return null;
  if(!/^image\/(jpeg|png|webp)$/.test(file.type))throw new Error('فقط عکس JPG، PNG یا WEBP');
  const src=await new Promise((res,rej)=>{const r=new FileReader();r.onload=()=>res(r.result);r.onerror=rej;r.readAsDataURL(file)});
  const im=await new Promise((res,rej)=>{const x=new Image();x.onload=()=>res(x);x.onerror=rej;x.src=src});
  const scale=Math.min(1,1000/Math.max(im.width,im.height));const c=document.createElement('canvas');c.width=Math.round(im.width*scale);c.height=Math.round(im.height*scale);c.getContext('2d').drawImage(im,0,0,c.width,c.height);
  let out=c.toDataURL('image/jpeg',.72);if(out.length>180000)out=c.toDataURL('image/jpeg',.52);if(out.length>185000)throw new Error('حجم عکس زیاده؛ عکس کم‌حجم‌تری انتخاب کن');return out;
}
const SG_MAX=3*1024*1024;
const toDataURL=f=>new Promise((res,rej)=>{const r=new FileReader();r.onload=()=>res(r.result);r.onerror=()=>rej(new Error('خواندن فایل ممکن نشد'));r.readAsDataURL(f)});
const isVideoFile=f=>/^video\//.test(f.type||'')||/\.(mp4|mov|m4v|webm)$/i.test(f.name||'');
async function socialMedia(file){
  if(!file)return null;
  if(isVideoFile(file)){
    if(file.size>SG_MAX)throw new Error('حجم ویدیو باید کمتر از ۳ مگابایت باشد؛ ویدیوی کوتاه‌تری انتخاب کن');
    return await toDataURL(file);
  }
  if(!/^image\//.test(file.type||'')&&!/\.(jpe?g|png|webp|gif)$/i.test(file.name||''))throw new Error('فقط عکس یا ویدیو انتخاب کن');
  if(file.type==='image/gif'){
    if(file.size>SG_MAX)throw new Error('حجم گیف باید کمتر از ۳ مگابایت باشد');
    return await toDataURL(file);
  }
  const src=await toDataURL(file);
  const im=await new Promise((res,rej)=>{const x=new Image();x.onload=()=>res(x);x.onerror=()=>rej(new Error('این عکس قابل خواندن نیست'));x.src=src});
  const scale=Math.min(1,1280/Math.max(im.width,im.height));
  const c=document.createElement('canvas');c.width=Math.max(1,Math.round(im.width*scale));c.height=Math.max(1,Math.round(im.height*scale));
  const ctx=c.getContext('2d');ctx.fillStyle='#fff';ctx.fillRect(0,0,c.width,c.height);ctx.drawImage(im,0,0,c.width,c.height);
  let out=c.toDataURL('image/jpeg',.82);
  if(out.length*.75>SG_MAX)out=c.toDataURL('image/jpeg',.6);
  if(out.length*.75>SG_MAX)throw new Error('حجم عکس زیاده؛ عکس کم‌حجم‌تری انتخاب کن');
  return out;
}
const ago=ts=>{const s=Math.max(0,Date.now()/1000-ts);if(s<60)return 'الان';if(s<3600)return num(Math.floor(s/60))+' دقیقه پیش';if(s<86400)return num(Math.floor(s/3600))+' ساعت پیش';if(s<7*86400)return num(Math.floor(s/86400))+' روز پیش';return whenShort(ts)};
const SEEN=(()=>{try{return JSON.parse(localStorage.getItem('jg_seen')||'{}')}catch(e){return {}}})();
const markSeen=id=>{SEEN[id]=1;try{const k=Object.keys(SEEN);if(k.length>500)k.slice(0,250).forEach(x=>delete SEEN[x]);localStorage.setItem('jg_seen',JSON.stringify(SEEN))}catch(e){}};
const sgIO=('IntersectionObserver' in window)?new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting){sgIO.unobserve(e.target);e.target._go&&e.target._go()}}),{rootMargin:'400px'}):null;
function sgMedia(id,type,cls,opt){
  opt=opt||{};
  const vid=/^video\//.test(type||'');
  const box=h('div',{class:'sg-m '+(cls||'')});
  const ph=h('div',{class:'sg-shim'});
  const el=vid?h('video',{playsinline:true,preload:'metadata',controls:opt.controls!==false}):h('img',{alt:'',draggable:'false'});
  if(vid)el.muted=!!opt.muted;
  box.append(el,ph);
  let started=false;
  box._go=()=>{if(started)return;started=true;
    blob('social'+id,'/api/social/media?id='+id).then(u=>{
      if(u){el.src=u;ph.remove();opt.onready&&opt.onready(el)}
      else{ph.className='sg-bad';ph.textContent='رسانه در دسترس نیست';opt.onfail&&opt.onfail()}
    })};
  if(opt.lazy===false||!sgIO)box._go();else sgIO.observe(box);
  return box;
}
function sgAvatar(p,size){
  const name=p.display_name||p.username||String(p.user_id||'?');
  const d=h('div',{class:'av',style:`width:${size}px;height:${size}px;font-size:${Math.round(size*.42)}px;background:${colorOf(p.user_id)}`},ini(name));
  if(p.avatar_media_id)blob('social'+p.avatar_media_id,'/api/social/media?id='+p.avatar_media_id).then(u=>{if(u){d.style.background='none';d.replaceChildren(h('img',{src:u,draggable:'false'}))}});
  return d;
}
const sgSkel=()=>h('div',{class:'sg-sk'},h('div',{class:'sg-shim'}));
function SocialScreen(){
  let me=null,section='feed',profile=null,roomTimer=null,roomAfter=0,reply=null,roomBusy=false,stripBox=null;
  const root=h('div',{class:'scr sg'}),body=h('div',{class:'scroll'});
  const head=h('div',{class:'sg-top'},
    h('div',{class:'sg-logo'},h('div',{class:'sg-cam'},'📸'),h('div',{},h('div',{class:'sg-brand'},'جقستگرام'),h('div',{class:'sg-tag'},'بساز، ببین، لایک کن و کامنت بذار'))),
    h('button',{class:'sg-ico',title:'بروزرسانی',onclick:()=>renderSection()},'↻'));
  const nav=h('div',{class:'sg-nav'});
  const tabs=[['feed','🏠','خانه'],['explore','🔥','اکسپلور'],['room','💬','چت‌روم'],['profile','👤','پروفایل']];
  function drawNav(){nav.replaceChildren(...tabs.map(([k,i,l])=>h('button',{class:section===k?'on':'',onclick:()=>{section=k;drawNav();renderSection()}},h('i',{},i),h('span',{},l))))}
  const fab=h('button',{class:'sg-fab hid',onclick:()=>createMenu()},'＋');
  root.append(head,nav,body,fab);

  /* ── پیج ── */
  function profileGate(){toast('برای انتشار محتوا اول پیج بساز',1);profileSheet()}
  const gateCard=()=>h('div',{class:'sg-gate'},h('div',{class:'sg-gi'},'📸'),h('h3',{},'پیج جقستگرامت رو بساز'),h('p',{},'با ساخت پیج نام کاربری اختصاصی می‌گیری و می‌تونی هر روز ۵ پست و ۲ استوری با عکس و ویدیو منتشر کنی، لایک کنی و کامنت بذاری.'),h('button',{class:'btn sg-go',onclick:profileSheet},'✨ ساخت پیج'));
  function profileSheet(){
    sheet(profile?'ویرایش پیج':'ساخت پیج جقستگرام',close=>{
      const un=h('input',{class:'inp',placeholder:'نام کاربری انگلیسی (مثلاً murad_1)',value:profile?.username||'',dir:'ltr'});
      const nm=h('input',{class:'inp',placeholder:'نام نمایشی',value:profile?.display_name||me.name||''});
      const bio=h('textarea',{class:'inp',style:'min-height:80px',placeholder:'بیوگرافی کوتاه',value:profile?.bio||''});
      const fi=h('input',{type:'file',accept:'image/jpeg,image/png,image/webp',class:'sfile'});
      const prev=h('button',{class:'sg-pick',onclick:()=>fi.click()},h('i',{},'🙂'),h('b',{},'عکس پروفایل (اختیاری)'),h('span',{},'از گالری انتخاب کن'));
      fi.onchange=()=>{prev.querySelector('b').textContent=fi.files[0]?.name||'عکس انتخاب شد'};
      const save=h('button',{class:'btn sg-go',onclick:async()=>{save.disabled=true;try{const media=fi.files[0]?await imageData(fi.files[0]):null;await api('/api/social/profile/save',{username:un.value,display_name:nm.value,bio:bio.value,media_data:media});me=await api('/api/social/me');profile=me.profile;close();toast('پیجت آماده شد ✨');drawNav();renderSection()}catch(e){toast(e.message,1);save.disabled=false}}},'ذخیره پیج');
      return h('div',{},h('div',{class:'sg-note'},'با ساخت پیج نام کاربری اختصاصی می‌گیری و می‌تونی پست و استوری منتشر کنی.'),un,nm,bio,fi,prev,save);
    });
  }

  /* ── ساخت محتوا ── */
  function createMenu(){
    if(!profile)return profileGate();
    const q=me.quota||{};
    const left=(n,m)=>q.unlimited?'نامحدود':num(n)+' از '+num(m)+' باقی‌مونده امروز';
    sheet('ساخت محتوای جدید',close=>h('div',{class:'sg-create'},
      h('button',{class:'sg-cbtn',onclick:()=>{close();compose('post')}},h('i',{},'🖼'),h('b',{},'پست جدید'),h('span',{},left(q.posts_left,q.posts_max))),
      h('button',{class:'sg-cbtn',onclick:()=>{close();compose('story')}},h('i',{},'⭕'),h('b',{},'استوری جدید (۲۴ ساعته)'),h('span',{},left(q.stories_left,q.stories_max)))));
  }
  function compose(kind){
    if(!profile)return profileGate();
    const q=me.quota||{},story=kind==='story',left=story?q.stories_left:q.posts_left;
    if(!q.unlimited&&left<=0)return toast(story?'سهمیه‌ی استوری امروزت تموم شده؛ فردا دوباره':'سهمیه‌ی پست امروزت تموم شده؛ فردا دوباره',1);
    sheet(story?'استوری جدید':'پست جدید',close=>{
      let file=null;const maxc=story?500:1500;
      const fi=h('input',{type:'file',accept:'image/*,video/mp4,video/webm,video/quicktime',class:'sfile'});
      const prev=h('div',{class:'sg-prev'});
      const pick=h('button',{class:'sg-pick',onclick:()=>fi.click()},h('i',{},'🖼'),h('b',{},'انتخاب عکس یا ویدیو از گالری'),h('span',{},'عکس یا ویدیو • حداکثر ۳ مگابایت'));
      const txt=h('textarea',{class:'inp',style:'min-height:90px',placeholder:story?'متن استوری…':'کپشن پست…',maxlength:String(maxc)});
      const cnt=h('div',{class:'sg-cnt'},'۰ / '+num(maxc));
      txt.oninput=()=>{cnt.textContent=num(txt.value.length)+' / '+num(maxc)};
      fi.onchange=()=>{
        const f=fi.files[0];if(!f)return;
        if(isVideoFile(f)&&f.size>SG_MAX){fi.value='';return toast('حجم ویدیو باید کمتر از ۳ مگابایت باشد',1)}
        if(!isVideoFile(f)&&f.size>40*1024*1024){fi.value='';return toast('این عکس خیلی بزرگه',1)}
        file=f;const url=URL.createObjectURL(f);
        const m=isVideoFile(f)?h('video',{src:url,controls:true,playsinline:true,muted:true}):h('img',{src:url});
        prev.replaceChildren(m,h('button',{class:'sg-x',onclick:()=>{file=null;fi.value='';prev.replaceChildren();pick.classList.remove('hid')}},'✕'));
        pick.classList.add('hid');
      };
      const go=h('button',{class:'btn sg-go',onclick:async()=>{
        if(!file&&!txt.value.trim())return toast('عکس، ویدیو یا متن اضافه کن',1);
        go.disabled=true;go.textContent='در حال ارسال…';
        try{
          const data=file?await socialMedia(file):null;
          await api(story?'/api/social/story':'/api/social/post',{caption:txt.value,media_data:data});
          close();toast(story?'استوری منتشر شد ✨':'پست منتشر شد ✨');
          me=await api('/api/social/me');renderSection();
        }catch(e){toast(e.message,1);go.disabled=false;go.textContent='انتشار'}
      }},'انتشار');
      const note=h('div',{class:'sg-note'},story?'استوری بعد از ۲۴ ساعت خودکار حذف میشه.':'پست روی صفحه‌ی خانه و اکسپلور نمایش داده میشه.');
      return h('div',{},fi,pick,prev,txt,cnt,note,go);
    });
  }

  /* ── استوری ── */
  const allSeen=list=>list.every(s=>SEEN[s.story_id]);
  async function loadStories(box){
    try{
      const rows=await api('/api/social/stories');
      const by=new Map();
      for(let k=rows.length-1;k>=0;k--){const s=rows[k];if(!by.has(s.user_id))by.set(s.user_id,[]);by.get(s.user_id).push(s)}
      const strip=h('div',{class:'sg-strip'});
      const mine=by.get(me.id);
      const ownAv=profile?sgAvatar({...profile,user_id:me.id},62):h('div',{class:'av',style:'width:62px;height:62px;font-size:26px;background:var(--btn)'},'👤');
      const own=h('div',{class:'sg-st',onclick:()=>mine?openStories(mine):compose('story')},
        h('div',{class:'sg-ring '+(mine?(allSeen(mine)?'seen':''):'none')},ownAv,h('button',{class:'sg-plus',onclick:e=>{e.stopPropagation();compose('story')}},'+')),
        h('span',{},'استوری شما'));
      strip.append(own);
      const others=[...by.entries()].filter(([uid])=>uid!==me.id).sort((a,b)=>{const sa=allSeen(a[1]),sb=allSeen(b[1]);if(sa!==sb)return sa?1:-1;return b[1][b[1].length-1].created_at-a[1][a[1].length-1].created_at});
      others.forEach(([uid,list])=>{const f=list[list.length-1];strip.append(h('div',{class:'sg-st',onclick:()=>openStories(list)},h('div',{class:'sg-ring '+(allSeen(list)?'seen':'')},sgAvatar(f,62)),h('span',{},oneLine(f.display_name||f.username||uid,10))))});
      box.replaceChildren(strip);
    }catch(e){}
  }
  function openStories(list){const i=list.findIndex(s=>!SEEN[s.story_id]);storyViewer(list,i<0?0:i)}
  function storyViewer(list,start){
    let i=-1,elapsed=0,dur=6000,paused=false,vid=null,ready=false,down=0;
    const ov=h('div',{class:'sg-sv'});
    const bars=h('div',{class:'sg-bars'},...list.map(()=>h('i',{},h('b',{}))));
    const who=h('div',{class:'sg-who'}),stage=h('div',{class:'sg-stage'}),cap=h('div',{class:'sg-scap hid'});
    const tm=setInterval(tick,60);
    const stopEv=e=>e.stopPropagation();
    function close(){clearInterval(tm);ov.remove();if(stripBox)loadStories(stripBox)}
    function setBar(p){const b=bars.children[i]&&bars.children[i].firstChild;if(b)b.style.width=Math.min(100,Math.max(0,p*100))+'%'}
    function tick(){
      if(paused||i<0||!ready)return;
      if(vid&&vid.duration&&isFinite(vid.duration)){if(vid.ended)return next();setBar(vid.currentTime/vid.duration);return}
      elapsed+=60;setBar(elapsed/dur);if(elapsed>=dur)next();
    }
    function next(){show(i+1)}
    function prev(){show(Math.max(0,i-1))}
    async function del(st){
      if(!await ask('این استوری حذف بشه؟'))return;
      try{await api('/api/social/story/delete',{story_id:st.story_id});toast('استوری حذف شد');close()}catch(e){toast(e.message,1)}
    }
    function show(n){
      if(n>=list.length)return close();
      i=n;elapsed=0;paused=false;ready=false;vid=null;
      const st=list[i];markSeen(st.story_id);
      [...bars.children].forEach((x,j)=>{x.firstChild.style.width=j<i?'100%':'0%'});
      const canDel=st.user_id===me.id||me.is_support;
      who.replaceChildren(sgAvatar(st,38),h('div',{class:'grow'},h('b',{},st.display_name||st.username||'کاربر'),h('div',{class:'sg-ago'},ago(st.created_at))),
        canDel?h('button',{class:'sg-ib',onpointerdown:stopEv,onpointerup:stopEv,onclick:()=>del(st)},'🗑'):null,
        h('button',{class:'sg-ib',onpointerdown:stopEv,onpointerup:stopEv,onclick:close},'✕'));
      stage.replaceChildren();
      cap.textContent=st.caption||'';cap.classList.toggle('hid',!st.caption||!st.media_id);
      if(st.media_id){
        stage.append(sgMedia(st.media_id,st.media_type,'',{controls:false,lazy:false,
          onready:el=>{ready=true;if(el.tagName==='VIDEO'){vid=el;el.play().catch(()=>{el.muted=true;el.play().catch(()=>{})})}},
          onfail:()=>{ready=true}}));
      }else{ready=true;stage.append(h('div',{class:'sg-stxt g'+(st.story_id%5)},st.caption||''))}
    }
    ov.onpointerdown=()=>{down=Date.now();paused=true;if(vid)vid.pause()};
    ov.onpointercancel=()=>{paused=false;if(vid)vid.play().catch(()=>{})};
    ov.onpointerup=e=>{paused=false;if(vid)vid.play().catch(()=>{});if(Date.now()-down<260){e.clientX<innerWidth/2?next():prev()}};
    ov.append(stage,bars,who,cap);document.body.append(ov);show(start||0);
  }

  /* ── پست ── */
  function postMenu(p,card,opt){
    sheet('',close=>h('div',{},
      sact('🗑 حذف پست',async()=>{close();if(!await ask('این پست حذف بشه؟'))return;try{await api('/api/social/post/delete',{post_id:p.post_id});card.remove();opt.close&&opt.close();toast('پست حذف شد');renderSection()}catch(e){toast(e.message,1)}}),
      sact('بستن',close)));
  }
  function commentsSheet(p,onCount){
    sheet('💬 کامنت‌ها',close=>{
      const list=h('div',{class:'sg-cl'});
      const inp=h('input',{class:'inp',placeholder:profile?'کامنتت رو بنویس…':'برای کامنت اول پیج بساز',maxlength:'500',disabled:!profile});
      const send=h('button',{class:'btn s sg-go',disabled:!profile,onclick:go},'ارسال');
      function row(c){
        const mine=c.user_id===me.id||me.is_support;
        return h('div',{class:'sg-c'},sgAvatar(c,34),
          h('div',{class:'t',dir:'auto'},h('b',{},c.display_name||c.username||'کاربر'),h('small',{},ago(c.created_at)),h('div',{},c.text)),
          mine?h('button',{class:'d',onclick:async()=>{if(!await ask('این کامنت حذف بشه؟'))return;try{await api('/api/social/comment/delete',{comment_id:c.comment_id});load()}catch(e){toast(e.message,1)}}},'🗑'):null);
      }
      async function load(){
        try{const rows=await api('/api/social/comments?post_id='+p.post_id);
          list.replaceChildren(...(rows.length?rows.map(row):[h('div',{class:'empty'},'هنوز کامنتی نیست؛ اولین نفر باش 💬')]));
          onCount(rows.length);
        }catch(e){list.replaceChildren(h('div',{class:'empty err'},e.message))}
      }
      async function go(){
        const t=inp.value.trim();if(!t)return;send.disabled=true;
        try{await api('/api/social/comment',{post_id:p.post_id,text:t});inp.value='';await load();list.scrollTop=list.scrollHeight}catch(e){toast(e.message,1)}
        send.disabled=!profile;
      }
      inp.onkeydown=e=>{if(e.key==='Enter')go()};
      list.append(h('div',{class:'empty'},'در حال بارگذاری…'));load();
      return h('div',{},list,h('div',{class:'sg-cbar'},inp,send));
    });
  }
  function postCard(p,opt){
    opt=opt||{};
    const name=p.display_name||p.username||String(p.user_id);
    const card=h('article',{class:'sg-post'});
    const canMenu=p.user_id===me.id||me.is_support;
    card.append(h('div',{class:'sg-ph',onclick:()=>{opt.close&&opt.close();openUser(p.user_id)}},sgAvatar(p,40),
      h('div',{class:'grow'},h('b',{},name),h('div',{class:'sub'},(p.username?'@'+p.username+' • ':'')+ago(p.created_at))),
      canMenu?h('button',{class:'sg-ib2',onclick:e=>{e.stopPropagation();postMenu(p,card,opt)}},'⋯'):null));
    const isV=/^video\//.test(p.media_type||'');
    const heart=h('div',{class:'sg-big'},'❤️');
    if(p.media_id){
      const wrap=h('div',{class:'sg-pw'},sgMedia(p.media_id,p.media_type,'',{}),heart);
      if(!isV){let last=0;wrap.onclick=()=>{const n=Date.now();if(n-last<320){like(true)}last=n}}
      card.append(wrap);
    }else if(p.caption){
      const wrap=h('div',{class:'sg-pw'},h('div',{class:'sg-ptext g'+(p.post_id%5),dir:'auto'},p.caption),heart);
      let last=0;wrap.onclick=()=>{const n=Date.now();if(n-last<320){like(true)}last=n};
      card.append(wrap);
    }
    const likeI=h('i',{}),likeN=h('span',{}),likeBtn=h('button',{class:'sg-act',onclick:()=>like(false)},likeI,likeN);
    const cmN=h('span',{}),cmBtn=h('button',{class:'sg-act',onclick:()=>commentsSheet(p,setCount)},h('i',{},'💬'),cmN);
    const more=h('button',{class:'sg-more',onclick:()=>commentsSheet(p,setCount)});
    function upd(){likeBtn.classList.toggle('on',!!p.my_reaction);likeI.textContent=p.my_reaction?'❤️':'🤍';likeN.textContent=num(p.likes);cmN.textContent=num(p.comments);more.textContent=p.comments>0?'مشاهده '+num(p.comments)+' کامنت':''}
    function setCount(n){p.comments=n;upd()}
    function burst(){heart.classList.remove('go');void heart.offsetWidth;heart.classList.add('go')}
    async function like(dbl){
      if(dbl&&p.my_reaction){burst();return}
      try{const r=await api('/api/social/react',{post_id:p.post_id,emoji:'❤️'});p.my_reaction=r.liked?'❤️':null;p.likes=r.likes;upd();
        if(r.liked){burst();likeBtn.classList.add('pulse');setTimeout(()=>likeBtn.classList.remove('pulse'),400)}}
      catch(e){toast(e.message,1)}
    }
    upd();
    card.append(h('div',{class:'sg-acts'},likeBtn,cmBtn));
    if(p.caption&&(p.media_id))card.append(h('div',{class:'sg-cap',dir:'auto'},h('b',{},name+' '),p.caption));
    card.append(more);
    return card;
  }
  function openPost(p){sheet('',close=>postCard(p,{close}))}
  function tile(p){
    const isV=/^video\//.test(p.media_type||'');
    const t=h('div',{class:'sg-tile',onclick:()=>openPost(p)});
    if(p.media_id&&!isV)t.append(sgMedia(p.media_id,p.media_type,'',{controls:false}));
    else if(isV)t.append(h('div',{class:'sg-ttxt g'+(p.post_id%5),style:'font-size:30px'},'▶'));
    else t.append(h('div',{class:'sg-ttxt g'+(p.post_id%5),dir:'auto'},oneLine(p.caption,70)));
    if(isV)t.append(h('div',{class:'sg-tv'},'🎬'));
    t.append(h('div',{class:'sg-tstat'},'❤️ '+num(p.likes)+'   💬 '+num(p.comments)));
    return t;
  }

  /* ── صفحه‌ها ── */
  async function renderFeed(){
    body.replaceChildren();
    const strip=h('div',{}),posts=h('div',{});stripBox=strip;
    if(!profile)body.append(h('div',{class:'sg-ban'},h('div',{class:'grow'},'پیج بساز تا پست و استوری بذاری و لایک و کامنت کنی 📸'),h('button',{class:'btn s',onclick:profileSheet},'ساخت پیج')));
    body.append(strip,posts);loadStories(strip);posts.append(sgSkel());
    try{const rows=await api('/api/social/feed?limit=30');
      posts.replaceChildren(...(rows.length?rows.map(p=>postCard(p)):[h('div',{class:'empty'},h('div',{style:'font-size:46px'},'✨'),'هنوز پستی منتشر نشده؛ اولین پست رو تو بذار!')]));
    }catch(e){posts.replaceChildren(h('div',{class:'empty err'},e.message))}
  }
  async function renderExplore(){
    body.replaceChildren(h('div',{class:'sg-h'},'🔥 داغ‌ترین‌های این هفته'),sgSkel());
    const rows=await api('/api/social/feed?limit=60&mode=top');
    const grid=h('div',{class:'sg-grid'},...rows.map(tile));
    body.replaceChildren(h('div',{class:'sg-h'},'🔥 داغ‌ترین‌های این هفته'),rows.length?grid:h('div',{class:'empty'},'هنوز پستی برای نمایش نیست'));
  }
  function profileBlock(d,isMe){
    const pr=d.profile,box=h('div',{});
    if(!pr){box.append(h('div',{class:'empty'},'این کاربر هنوز پیجی نساخته'));return box}
    const st=d.stats||{};
    const stat=(n,l)=>h('div',{class:'sg-stat'},h('b',{},num(n)),h('span',{},l));
    box.append(h('div',{class:'sg-hero'},h('div',{class:'sg-cover'}),h('div',{class:'sg-pav'},sgAvatar(pr,88)),
      h('div',{class:'sg-pn'},pr.display_name),h('div',{class:'sub ltr'},'@'+pr.username),
      pr.bio?h('div',{class:'sg-bio',dir:'auto'},pr.bio):null,
      h('div',{class:'sg-stats'},stat(st.posts,'پست'),stat(st.likes,'لایک'),stat(st.comments,'کامنت'))));
    if(isMe){
      const q=me.quota||{};
      box.append(h('div',{class:'sg-btns'},h('button',{class:'btn s gh',onclick:profileSheet},'✏️ ویرایش'),h('button',{class:'btn s sg-go',onclick:()=>compose('post')},'＋ پست'),h('button',{class:'btn s sg-go',onclick:()=>compose('story')},'＋ استوری')));
      box.append(h('div',{class:'sg-q'},h('div',{},h('b',{},q.unlimited?'∞':num(q.posts_left)+'/'+num(q.posts_max)),'پست امروز'),h('div',{},h('b',{},q.unlimited?'∞':num(q.stories_left)+'/'+num(q.stories_max)),'استوری امروز')));
    }
    box.append(d.posts.length?h('div',{class:'sg-grid'},...d.posts.map(tile)):h('div',{class:'empty'},isMe?'هنوز پستی نذاشتی؛ با دکمه‌ی ＋ شروع کن!':'هنوز پستی نداره'));
    return box;
  }
  async function renderProfile(){
    if(!profile){body.replaceChildren(gateCard());return}
    body.replaceChildren(sgSkel());
    const d=await api('/api/social/profile?uid='+me.id);
    body.replaceChildren(profileBlock(d,true));
  }
  function UserScreen(uid){
    const scroll=h('div',{class:'scroll'});
    const el=h('div',{class:'scr sg'},h('div',{class:'top row'},backBtn(),h('b',{class:'grow'},'پروفایل')),scroll);
    scroll.append(sgSkel());
    (async()=>{try{const d=await api('/api/social/profile?uid='+uid);scroll.replaceChildren(profileBlock(d,false))}catch(e){scroll.replaceChildren(h('div',{class:'empty err'},e.message))}})();
    return el;
  }
  function openUser(uid){if(uid===me.id&&profile){section='profile';drawNav();renderSection();return}push(UserScreen(uid))}
  async function renderRoom(){clearInterval(roomTimer);roomTimer=null;roomAfter=0;body.replaceChildren(h('div',{class:'empty'},'در حال بارگذاری چت‌روم…'));const msgs=h('div',{class:'sroom'}),composer=h('div',{class:'scomposer'}),inp=h('input',{class:'inp',placeholder:'پیامت رو بنویس…'}),send=h('button',{class:'btn s',style:'background:#b12bd5',onclick:sendMsg},'ارسال');let replyBar=h('div',{class:'snotice hid'});const premium=h('button',{class:'ib',title:'ایموجی پرمیوم',onclick:()=>{const id=prompt('شناسه عددی ایموجی پرمیوم را وارد کن');if(id&&/^\d+$/.test(id)){premium.dataset.id=id;toast('ایموجی برای پیام بعدی انتخاب شد')}}},'💎');if(!me.premium_emoji)premium.classList.add('hid');composer.append(premium,inp,send);
    function renderMessage(m){const name=m.display_name||m.username||String(m.user_id);const node=h('div',{class:'smsg'+(m.user_id===me.id?' mine':'')});node.append(h('div',{class:'sub'},name+' • '+whenShort(m.created_at)));if(m.reply_to)node.append(h('div',{class:'sreply'},(m.reply_name?m.reply_name+' • ':'')+(m.reply_text||'پاسخ به پیام')));if(m.text)node.append(h('div',{class:'tx',dir:'auto'},m.text));if(m.premium_emoji_id){const tag=h('tg-emoji',{'emoji-id':m.premium_emoji_id},'⭐');tag.className='prem';node.append(tag)}const rs=h('div',{class:'sreacts'},...(m.reactions||[]).map(r=>h('button',{onclick:()=>react(m.message_id,r.emoji)},r.emoji+' '+r.n)));rs.append(h('button',{onclick:()=>reactSheet(m)},'＋'));node.append(rs);let x=null;node.ontouchstart=e=>{x=e.touches[0].clientX};node.ontouchend=e=>{if(x!==null&&x-e.changedTouches[0].clientX>55){reply=m;replyBar.classList.remove('hid');replyBar.textContent='پاسخ به '+name+': '+oneLine(m.text,60)+'  ✕';replyBar.onclick=()=>{reply=null;replyBar.classList.add('hid')}}x=null};return node}
    async function loadRoom(){if(roomBusy)return;roomBusy=true;try{const arr=await api('/api/social/room?after='+roomAfter);if(arr.length){arr.forEach(m=>{msgs.append(renderMessage(m));roomAfter=Math.max(roomAfter,m.message_id)});msgs.scrollTop=msgs.scrollHeight}}catch(e){}roomBusy=false}
    async function react(mid,em){try{await api('/api/social/room/react',{message_id:mid,emoji:em});loadRoom()}catch(e){toast(e.message,1)}}
    function reactSheet(m){sheet('ری‌اکشن پیام',close=>h('div',{class:'chips'},['❤️','😂','😍','🔥','👏','👍','👎','😮'].map(em=>h('button',{class:'chip',onclick:()=>{close();react(m.message_id,em)}},em))))}
    async function sendMsg(){const text=inp.value.trim(),pe=premium.dataset.id||null;if(!text&&!pe)return;send.disabled=true;try{await api('/api/social/room/send',{text,reply_to:reply?.message_id||null,premium_emoji_id:pe});inp.value='';premium.dataset.id='';reply=null;replyBar.classList.add('hid');await loadRoom()}catch(e){toast(e.message,1)}send.disabled=false}
    body.replaceChildren(msgs,replyBar,composer);await loadRoom();roomTimer=setInterval(loadRoom,3500);root._destroy=()=>{clearInterval(roomTimer);roomTimer=null};
  }
  async function renderSection(){
    clearInterval(roomTimer);roomTimer=null;
    body.classList.toggle('sg-flex',section==='room');
    fab.classList.toggle('hid',!(profile&&section!=='room'));
    if(!me){body.replaceChildren(h('div',{class:'empty'},'در حال بارگذاری…'));return}
    try{
      if(section==='feed')await renderFeed();
      else if(section==='explore')await renderExplore();
      else if(section==='room')await renderRoom();
      else await renderProfile();
    }catch(e){body.replaceChildren(h('div',{class:'empty err'},e.message))}
  }
  async function init(){try{me=await api('/api/social/me');profile=me.profile;drawNav();await renderSection()}catch(e){body.replaceChildren(h('div',{class:'empty err'},e.message))}}
  root._show=()=>{if(!me)init()};root._destroy=()=>{clearInterval(roomTimer)};init();return root;
}

/* ───── شروع ───── */
const TABS={chats:ChatsScreen,users:()=>UsersScreen(false),panel:PanelScreen,social:SocialScreen},tabEls={};
function openTab(k){
  document.querySelectorAll('#tabs button').forEach(b=>b.classList.toggle('on',b.dataset.k===k));
  if(!tabEls[k])tabEls[k]=TABS[k]();push(tabEls[k],true);
}
async function bootTabs(){
  try{const m=await api('/api/social/me');const isSupport=m.is_support;
    if(!isSupport){document.querySelectorAll('#tabs button').forEach(b=>{if(b.dataset.k!=='social')b.remove()});openTab('social')}
    else{if(!(m.perms||[]).includes('group_msgs')){const cb=document.querySelector('#tabs button[data-k="chats"]');if(cb)cb.remove()}openTab('social')}
  }catch(e){document.querySelectorAll('#tabs button').forEach(b=>{if(b.dataset.k!=='social')b.remove()});openTab('social')}
}
document.querySelectorAll('#tabs button').forEach(b=>b.onclick=()=>openTab(b.dataset.k));
bootTabs();
</script></body></html>
"""


# ───────────── ابزارهای کوچک ─────────────
class _ApiError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


def _need(user, *perms):
    """اگه هیچ‌کدوم از این دسترسی‌ها رو نداشته باشه خطای ۴۰۳ میده."""
    uid = int(user["id"])
    if not any(has_perm(uid, p) for p in perms):
        raise _ApiError("این بخش برای حساب تو فعال نیست.", 403)


def _j(obj, status: int = 200):
    return status, "application/json", json.dumps(obj, ensure_ascii=False, default=str).encode(), {}


def _int(v, default=None, lo=None, hi=None):
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return default
    if lo is not None and n < lo:
        n = lo
    if hi is not None and n > hi:
        n = hi
    return n


def _flt(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _esc_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


_TTL: dict = {}


async def _ttl(key: str, ttl: float, fn):
    """کش کوتاه‌مدت تو حافظه تا دیتابیس/تلگرام زیر فشار نره."""
    now = time.time()
    hit = _TTL.get(key)
    if hit and hit[0] > now:
        return hit[1]
    val = await fn()
    _TTL[key] = (now + ttl, val)
    if len(_TTL) > 500:
        for k in [k for k, v in _TTL.items() if v[0] <= now]:
            _TTL.pop(k, None)
    return val


# کش باینری (عکس پروفایل/مدیا): حداکثر ۴۰ مگابایت
from collections import OrderedDict
_BIN: "OrderedDict[str, tuple]" = OrderedDict()
_BIN_BYTES = 0
_BIN_MAX = 40 * 1024 * 1024
_TG_SEM = asyncio.Semaphore(6)   # هم‌زمانی درخواست به تلگرام


def _bin_get(key: str):
    v = _BIN.get(key)
    if v and v[0] > time.time():
        _BIN.move_to_end(key)
        return v
    return None


def _bin_put(key: str, ttl: float, ctype, data: bytes):
    global _BIN_BYTES
    old = _BIN.pop(key, None)
    if old:
        _BIN_BYTES -= len(old[2])
    _BIN[key] = (time.time() + ttl, ctype, data)
    _BIN_BYTES += len(data)
    while _BIN and (_BIN_BYTES > _BIN_MAX or len(_BIN) > 3000):
        _k, v = _BIN.popitem(last=False)
        _BIN_BYTES -= len(v[2])


def _sniff_image(b: bytes):
    if b[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None


async def _tg_call(fn, *a, **kw):
    async with _TG_SEM:
        return await asyncio.wait_for(fn(*a, **kw), 25)


async def _download(file_id: str, limit: int = 8 * 1024 * 1024) -> bytes:
    f = await _tg_call(BOT_REF.get_file, file_id)
    if f.file_size and f.file_size > limit:
        return b""
    return bytes(await _tg_call(f.download_as_bytearray))


def _img_resp(ctype, data: bytes):
    if not data or not ctype:
        return 404, "application/json", b"{}", {"Cache-Control": "private, max-age=300"}
    return 200, ctype, data, {"Cache-Control": "private, max-age=3600"}


def _verify_init_data(init: str):
    """initData مینی‌اپ تلگرام رو با توکن ربات چک می‌کنه؛ دیکشنری کاربر (id, first_name, ...) یا None."""
    import hashlib
    import hmac
    import urllib.parse
    try:
        d = dict(urllib.parse.parse_qsl(init or "", keep_blank_values=True))
        h = d.pop("hash", None)
        if not h:
            return None
        check = "\n".join(f"{k}={v}" for k, v in sorted(d.items()))
        secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(hmac.new(secret, check.encode(), hashlib.sha256).hexdigest(), h):
            return None
        if time.time() - int(d.get("auth_date", "0")) > 86400:
            return None
        u = json.loads(d["user"])
        int(u["id"])
        return u
    except Exception:
        return None


async def _audit(admin: dict, action: str, target=None, details: str = ""):
    """هر کاری که پشتیبان تو پنل می‌کنه ثبت میشه (تو تب «گزارش عملیات» دیده میشه)."""
    try:
        await pool.execute(
            "INSERT INTO admin_log (admin_id, admin_name, action, target, details, ts) VALUES ($1,$2,$3,$4,$5,$6)",
            int(admin["id"]), (admin.get("first_name") or "")[:60], action, target, (details or "")[:500], time.time())
    except Exception:
        log.warning("audit failed", exc_info=True)


# ───────────── خروجی پیام‌ها ─────────────
def _msg_out(r) -> dict:
    kind = r["kind"]
    tag = _KIND_TAG.get(kind) if kind else None

    def strip(t):
        t = t or ""
        if tag and t.startswith(tag):
            t = t[len(tag):].lstrip()
        return t

    cid = r["chat_id"]
    return {
        "id": r["msg_id"], "chat_id": cid, "user_id": r["user_id"], "name": r["name"], "username": r["username"],
        "text": strip(r["text"]), "ts": r["ts"], "kind": kind, "tag": tag, "media": bool(r["has_media"]),
        "reply_to": r["reply_to"], "reply_name": r["reply_name"], "reply_text": r["reply_text"], "fwd": r["fwd"],
        "edited": strip(r["edited_text"]) if r["edited_text"] is not None else None, "edited_ts": r["edited_ts"],
        "premium": bool(r["premium"]), "title": r["title"],
        "link": f"https://t.me/c/{str(cid)[4:]}/{r['msg_id']}" if str(cid).startswith("-100") else None,
    }


# ───────────── GET ها ─────────────
async def _g_me(qs, user):
    return _j({"id": int(user["id"]), "name": user.get("first_name") or "", "bot_enabled": BOT_ENABLED,
               "version": BOT_VERSION, "retention_days": MSG_LOG_DAYS, "perms": sorted(perms_of(int(user["id"])))})


async def _g_stats(qs, user):
    async def go():
        now = time.time()
        r = await pool.fetchrow(
            "SELECT (SELECT count(*) FROM users) AS users, (SELECT count(*) FROM users WHERE started) AS started, "
            "(SELECT count(*) FROM chats) AS chats, (SELECT COALESCE(sum(balance),0)::bigint FROM users) AS balance, "
            "(SELECT COALESCE(sum(jaghs),0)::bigint FROM users) AS jaghs, (SELECT count(*) FROM group_msgs) AS msgs, "
            "(SELECT count(*) FROM group_msgs WHERE ts > $1::double precision - 86400) AS msgs24, "
            "(SELECT count(DISTINCT user_id) FROM group_msgs WHERE ts > $1::double precision - 86400) AS active24, "
            "(SELECT count(*) FROM users WHERE banned_until > $1::double precision) AS banned, "
            "(SELECT count(*) FROM tg_profiles) AS members", now)
        hours = [0] * 24
        for x in await pool.fetch(
                "SELECT floor(($1::double precision - ts) / 3600)::int AS hr, count(*) AS n FROM group_msgs "
                "WHERE ts > $1::double precision - 86400 GROUP BY 1", now):
            if 0 <= x["hr"] < 24:
                hours[23 - x["hr"]] = x["n"]
        top = await pool.fetch(
            "SELECT user_id, max(name) AS name, count(*) AS n FROM group_msgs WHERE ts > $1::double precision - 86400 "
            "GROUP BY user_id ORDER BY n DESC LIMIT 5", now)
        return {**dict(r), "hours": hours, "top": [dict(t) for t in top]}
    return _j(await _ttl("stats", 10, go))


async def _g_groups(qs, user):
    async def go():
        rows = await pool.fetch(
            "SELECT ids.chat_id, ch.title, COALESCE(cnt.n, 0) AS n, cnt.last_ts, l.name AS last_name, l.text AS last_text "
            "FROM (SELECT chat_id FROM chats UNION SELECT DISTINCT chat_id FROM group_msgs) ids "
            "LEFT JOIN chats ch ON ch.chat_id = ids.chat_id "
            "LEFT JOIN (SELECT chat_id, count(*) AS n, max(ts) AS last_ts FROM group_msgs GROUP BY chat_id) cnt ON cnt.chat_id = ids.chat_id "
            "LEFT JOIN (SELECT DISTINCT ON (chat_id) chat_id, name, text FROM group_msgs ORDER BY chat_id, ts DESC) l ON l.chat_id = ids.chat_id "
            "ORDER BY cnt.last_ts DESC NULLS LAST LIMIT 300")
        return [dict(r) for r in rows]
    return _j(await _ttl("groups", 8, go))


async def _g_messages(qs, user):
    c = (qs.get("chat") or "").strip()
    if c == "all":
        cid = None
    else:
        cid = _int(c)
        if cid is None:
            raise _ApiError("گروه نامعتبر")
    u = (qs.get("user") or "").strip()
    uid_f = int(u) if u.lstrip("-").isdigit() else None
    un_f = u.lstrip("@").lower() if (u and uid_f is None) else None
    q = (qs.get("q") or "").strip() or None
    limit = _int(qs.get("limit"), 500, 1, 1000)
    bts, ats = _flt(qs.get("bts")), _flt(qs.get("ats"))
    bid, aid = _int(qs.get("bid"), 0), _int(qs.get("aid"), 0)

    w, a = [], []

    def P(v):
        a.append(v)
        return f"${len(a)}"

    if cid is not None:
        w.append(f"g.chat_id = {P(cid)}")
    if uid_f is not None:
        w.append(f"g.user_id = {P(uid_f)}")
    if un_f:
        w.append(f"lower(g.username) = {P(un_f)}")
    if q:
        p = P("%" + _esc_like(q) + "%")
        w.append(f"(g.text ILIKE {p} OR g.edited_text ILIKE {p})")
    count_sql = "SELECT count(*) FROM group_msgs g WHERE " + (" AND ".join(w) or "TRUE")
    count_args = list(a)
    asc = ats is not None
    if bts is not None:
        w.append(f"(g.ts, g.msg_id) < ({P(bts)}::double precision, {P(bid)}::bigint)")
    if asc:
        w.append(f"(g.ts, g.msg_id) > ({P(ats)}::double precision, {P(aid)}::bigint)")
    order = "ASC" if asc else "DESC"
    rows = await pool.fetch(
        "SELECT g.msg_id, g.chat_id, g.user_id, g.name, g.username, g.text, g.ts, g.kind, (g.file_id IS NOT NULL) AS has_media, "
        "g.reply_to, g.reply_name, g.reply_text, g.fwd, g.edited_text, g.edited_ts, COALESCE(p.is_premium, FALSE) AS premium, "
        "(SELECT title FROM chats c WHERE c.chat_id = g.chat_id) AS title "
        "FROM group_msgs g LEFT JOIN tg_profiles p ON p.user_id = g.user_id WHERE " + (" AND ".join(w) or "TRUE") +
        f" ORDER BY g.ts {order}, g.msg_id {order} LIMIT {limit + 1}", *a)
    more = len(rows) > limit
    rows = list(rows[:limit])
    if not asc:
        rows.reverse()
    total = await pool.fetchval(count_sql, *count_args) if (bts is None and not asc) else None
    return _j({"msgs": [_msg_out(r) for r in rows], "more": more and not asc, "total": total})


async def _g_users(qs, user):
    q = (qs.get("q") or "").strip() or None
    limit = _int(qs.get("limit"), 60, 1, 200)
    offset = _int(qs.get("offset"), 0, 0, 1000000)
    order = {
        "balance": "COALESCE(u.balance, 0) DESC", "jaghs": "COALESCE(u.jaghs, 0) DESC", "name": "name ASC",
    }.get(qs.get("sort") or "", "COALESCE(p.last_seen, 0) DESC, COALESCE(u.balance, 0) DESC")
    w, a = [], []

    def P(v):
        a.append(v)
        return f"${len(a)}"

    if q:
        qn = q.lstrip("@")
        pl = P("%" + _esc_like(qn) + "%")
        pid = P(q)
        w.append(f"(COALESCE(u.name,'') ILIKE {pl} OR COALESCE(p.first_name,'') ILIKE {pl} OR COALESCE(p.last_name,'') ILIKE {pl} "
                 f"OR COALESCE(u.username, p.username, '') ILIKE {pl} OR COALESCE(u.user_id, p.user_id)::text = {pid})")
    if qs.get("banned") == "1":
        w.append(f"COALESCE(u.banned_until, 0) > {P(time.time())}::double precision")
    where = " AND ".join(w) or "TRUE"
    frm = "FROM users u FULL OUTER JOIN tg_profiles p ON p.user_id = u.user_id"
    total = await pool.fetchval(f"SELECT count(*) {frm} WHERE {where}", *a)
    rows = await pool.fetch(
        "SELECT COALESCE(u.user_id, p.user_id) AS user_id, "
        "COALESCE(NULLIF(u.name, ''), NULLIF(btrim(COALESCE(p.first_name,'') || ' ' || COALESCE(p.last_name,'')), '')) AS name, "
        "COALESCE(u.username, lower(p.username)) AS username, COALESCE(u.balance, 0) AS balance, COALESCE(u.jaghs, 0) AS jaghs, "
        "COALESCE(u.started, FALSE) AS started, COALESCE(u.banned_until, 0) AS banned_until, (u.user_id IS NOT NULL) AS in_game, "
        f"COALESCE(p.is_premium, FALSE) AS premium {frm} WHERE {where} ORDER BY {order} LIMIT {limit} OFFSET {offset}", *a)
    now = time.time()
    users = []
    for r in rows:
        d = dict(r)
        d["banned"] = (d.pop("banned_until") or 0) > now
        users.append(d)
    return _j({"users": users, "total": total})


async def _tg_live(uid: int) -> dict:
    """اطلاعات زنده از تلگرام (بیو، تولد، تعداد عکس پروفایل). بعضی‌ها فقط وقتی کاربر با ربات کار کرده در دسترسن."""
    async def go():
        out: dict = {}
        if BOT_REF is None:
            return out
        try:
            ch = await _tg_call(BOT_REF.get_chat, uid)
            out["bio"] = getattr(ch, "bio", None)
            out["first_name"] = getattr(ch, "first_name", None)
            out["last_name"] = getattr(ch, "last_name", None)
            out["username"] = getattr(ch, "username", None)
            bd = getattr(ch, "birthdate", None)
            if bd:
                out["birthdate"] = {"day": bd.day, "month": bd.month, "year": getattr(bd, "year", None)}
        except Exception:
            pass
        try:
            ph = await _tg_call(BOT_REF.get_user_profile_photos, uid, limit=1)
            out["photos"] = ph.total_count
        except Exception:
            pass
        return out
    return await _ttl(f"live:{uid}", 600, go)


async def _g_user(qs, user):
    uid = _int(qs.get("id"))
    if uid is None:
        raise _ApiError("آیدی نامعتبر")
    now = time.time()
    u = await pool.fetchrow(
        "SELECT user_id, name, username, balance, jaghs, started, gender, banned_until, work_count, last_ts FROM users WHERE user_id = $1", uid)
    p = await pool.fetchrow("SELECT * FROM tg_profiles WHERE user_id = $1", uid)
    act = await pool.fetchrow("SELECT count(*) AS n, min(ts) AS first_ts, max(ts) AS last_ts FROM group_msgs WHERE user_id = $1", uid)
    lastmsg = await pool.fetchrow("SELECT name, username FROM group_msgs WHERE user_id = $1 ORDER BY ts DESC LIMIT 1", uid)
    groups = await pool.fetch(
        "SELECT g.chat_id, (SELECT title FROM chats c WHERE c.chat_id = g.chat_id) AS title, count(*) AS n, max(g.ts) AS last_ts "
        "FROM group_msgs g WHERE g.user_id = $1 GROUP BY g.chat_id ORDER BY n DESC LIMIT 60", uid)
    live = await _tg_live(uid)
    if not (u or p or act["n"] or live):
        raise _ApiError("کاربر پیدا نشد.", 404)
    tg_name = " ".join(x for x in ((p["first_name"], p["last_name"]) if p else ()) if x) or None
    name = (u["name"] if u and u["name"] else None) or tg_name or (lastmsg["name"] if lastmsg else None) \
        or " ".join(x for x in (live.get("first_name"), live.get("last_name")) if x) or None
    username = (u["username"] if u and u["username"] else None) or (p["username"] if p and p["username"] else None) \
        or (lastmsg["username"] if lastmsg and lastmsg["username"] else None) or live.get("username")
    game = None
    if u:
        bu = u["banned_until"] or 0
        lvl = level_for(u["jaghs"])
        ch = CHARACTERS.get(u["gender"] or "")
        game = {"balance": u["balance"], "jaghs": u["jaghs"], "started": u["started"], "level": lvl,
                "character": ch[lvl - 1][0] if ch and 0 < lvl <= len(ch) else None, "work_count": u["work_count"],
                "banned": bu > now, "banned_text": _ban_until_txt(bu) if bu > now else None}
    return _j({
        "id": uid, "name": name, "username": username, "premium": bool(p and p["is_premium"]), "lang": p["lang"] if p else None,
        "first_name": (p["first_name"] if p else None) or live.get("first_name"), "last_name": (p["last_name"] if p else None) or live.get("last_name"),
        "bio": live.get("bio"), "birthdate": live.get("birthdate"), "photos": live.get("photos"),
        "first_seen": p["first_seen"] if p else None, "last_seen": p["last_seen"] if p else None,
        "msgs": {"n": act["n"], "first_ts": act["first_ts"], "last_ts": act["last_ts"]},
        "groups": [dict(g) for g in groups], "game": game, "is_admin": uid in ADMIN_IDS,
    })


async def _g_avatar(qs, user):
    big = qs.get("big") == "1"
    if qs.get("chat"):
        cid = _int(qs["chat"])
        if cid is None:
            raise _ApiError("bad chat")
        key, uid, idx = f"c{cid}{'b' if big else 's'}", None, 0
    else:
        uid = _int(qs.get("uid"))
        if uid is None:
            raise _ApiError("bad uid")
        idx = _int(qs.get("i"), 0, 0, 99)
        key, cid = f"u{uid}:{idx}:{'b' if big else 's'}", None
    hit = _bin_get(key)
    if hit:
        return _img_resp(hit[1], hit[2])
    data, ctype, failed = b"", None, False
    try:
        file_id = None
        if cid is not None:
            ph = (await _tg_call(BOT_REF.get_chat, cid)).photo
            file_id = (ph.big_file_id if big else ph.small_file_id) if ph else None
        else:
            ph = await _tg_call(BOT_REF.get_user_profile_photos, uid, offset=idx, limit=1)
            if ph.photos:
                sizes = ph.photos[0]
                file_id = (sizes[-1] if big else sizes[0]).file_id
        if file_id:
            data = await _download(file_id)
            ctype = _sniff_image(data) if data else None
            if not ctype:
                data = b""
    except Exception:
        failed = True
        log.info("avatar fetch failed (%s)", key, exc_info=True)
    _bin_put(key, 60 if failed else (3600 if data else 900), ctype, data)
    return _img_resp(ctype, data)


async def _g_media(qs, user):
    cid, mid = _int(qs.get("chat")), _int(qs.get("msg"))
    if cid is None or mid is None:
        raise _ApiError("bad id")
    key = f"m{cid}:{mid}"
    hit = _bin_get(key)
    if hit:
        return _img_resp(hit[1], hit[2])
    file_id = await pool.fetchval("SELECT file_id FROM group_msgs WHERE chat_id = $1 AND msg_id = $2", cid, mid)
    data, ctype, failed = b"", None, False
    if file_id:
        try:
            data = await _download(file_id)
            ctype = _sniff_image(data) if data else None
            if not ctype:
                data = b""
        except Exception:
            failed = True
            log.info("media fetch failed (%s)", key, exc_info=True)
    _bin_put(key, 60 if failed else (3600 if data else 900), ctype, data)
    return _img_resp(ctype, data)


async def _g_tasks(qs, user):
    dk, wk = task_period_key("daily"), task_period_key("weekly")
    rows = await pool.fetch(
        "SELECT t.task_id, t.period, t.title, t.metric, t.target, t.reward, (SELECT count(*) FROM task_completions tc WHERE tc.task_id = t.task_id AND tc.completed "
        "AND tc.period_key = CASE t.period WHEN 'daily' THEN $1 ELSE $2 END) AS done "
        "FROM tasks t WHERE t.active = TRUE ORDER BY CASE t.period WHEN 'daily' THEN 0 ELSE 1 END, t.task_id", dk, wk)
    return _j({"tasks": [dict(r) for r in rows],
               "metrics": [{"key": k, "label": v[0], "kind": v[1]} for k, v in TASK_METRICS.items()],
               "caps": TASK_MAX_REWARD})


async def _g_log(qs, user):
    rows = await pool.fetch("SELECT id, admin_id, admin_name, action, target, details, ts FROM admin_log ORDER BY id DESC LIMIT 120")
    return _j([dict(r) for r in rows])


async def _g_bc_status(qs, user):
    job = BC_JOBS.get(qs.get("id") or "")
    if not job:
        raise _ApiError("چنین ارسالی پیدا نشد.", 404)
    return _j({k: job[k] for k in ("total", "ok", "fail", "running")})


# ───────────── POST ها (عملیات پنل) ─────────────
BC_JOBS: dict = {}


async def _ensure_game_row(uid: int):
    """اگه کاربر هنوز با ربات بازی نکرده، با اطلاعاتی که ازش داریم براش حساب ساخته میشه."""
    if await pool.fetchval("SELECT 1 FROM users WHERE user_id = $1", uid):
        return
    p = await pool.fetchrow("SELECT first_name, last_name, username FROM tg_profiles WHERE user_id = $1", uid)
    m = await pool.fetchrow("SELECT name, username FROM group_msgs WHERE user_id = $1 ORDER BY ts DESC LIMIT 1", uid)
    if not p and not m:
        raise _ApiError("این کاربر رو نمی‌شناسم (هیچ فعالیتی ازش ثبت نشده).", 404)
    name = (" ".join(x for x in ((p["first_name"], p["last_name"]) if p else ()) if x)) or (m["name"] if m else None) or str(uid)
    un = (p["username"] if p and p["username"] else None) or (m["username"] if m and m["username"] else None)
    await pool.execute("INSERT INTO users (user_id, name, username) VALUES ($1, $2, $3) ON CONFLICT (user_id) DO NOTHING",
                       uid, name, un.lower() if un else None)


async def _p_adjust(b, user):
    uid = _int(b.get("user_id"))
    field, op = b.get("field"), b.get("op")
    if uid is None or field not in ("balance", "jaghs") or op not in ("add", "sub"):
        raise _ApiError("درخواست نامعتبر")
    _need(user, {("balance", "sub"): "money_sub", ("balance", "add"): "money_add",
                 ("jaghs", "sub"): "jagh_sub", ("jaghs", "add"): "jagh_add"}[(field, op)])
    raw = b.get("amount")
    amt = int(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else parse_amount(str(raw or ""))
    if amt <= 0:
        raise _ApiError("یه مقدار معتبر بنویس (مثلا 50000 یا 100 هزار)")
    if amt > 10 ** 15:
        raise _ApiError("این مقدار خیلی بزرگه")
    await _ensure_game_row(uid)
    async with pool.acquire() as c:
        async with c.transaction():
            before = await c.fetchval(f"SELECT {field} FROM users WHERE user_id = $1 FOR UPDATE", uid)
            after = max(before + (amt if op == "add" else -amt), 0)
            await c.execute(f"UPDATE users SET {field} = $1::bigint WHERE user_id = $2::bigint", after, uid)
    await _audit(user, f"{'money' if field == 'balance' else 'jagh'}_{op}", uid, f"{amt:,}  ({before:,} ← {after:,})")
    return _j({"ok": True, "before": before, "after": after})


async def _p_ban(b, user):
    uid = _int(b.get("user_id"))
    if uid is None:
        raise _ApiError("درخواست نامعتبر")
    if uid in ADMIN_IDS:
        raise _ApiError("نمی‌تونی یه پشتیبان رو محروم کنی.")
    dur = parse_duration(str(b.get("duration") or ""))
    if dur is None:
        raise _ApiError("مدت رو نفهمیدم. مثلا: 30 دقیقه، 2 ساعت، 3 روز، دائمی")
    await _ensure_game_row(uid)
    until = BAN_PERM if dur == -1 else time.time() + dur
    await pool.execute("UPDATE users SET banned_until = $1::double precision WHERE user_id = $2::bigint", until, uid)
    BANNED[uid] = until
    text = _ban_until_txt(until)
    await _audit(user, "ban", uid, text)
    return _j({"ok": True, "text": f"محروم شد ({text})"})


async def _p_unban(b, user):
    uid = _int(b.get("user_id"))
    if uid is None:
        raise _ApiError("درخواست نامعتبر")
    await pool.execute("UPDATE users SET banned_until = 0 WHERE user_id = $1", uid)
    BANNED.pop(uid, None)
    await _audit(user, "unban", uid)
    return _j({"ok": True})


async def _p_task_add(b, user):
    period, title = b.get("period"), str(b.get("title") or "").strip()[:300]
    if period not in ("daily", "weekly"):
        raise _ApiError("دوره نامعتبره")
    _need(user, "task_daily" if period == "daily" else "task_weekly")
    metric = b.get("metric") or None
    target, reward = 1, 0
    if metric:
        if metric not in TASK_METRICS:
            raise _ApiError("معیار نامعتبره")
        target, reward = _int(b.get("target")), _int(b.get("reward"))
        if not target or target < 1 or target > 100_000_000_000:
            raise _ApiError("تعداد/مبلغ هدف نامعتبره")
        if reward is None or reward < 1 or reward > TASK_MAX_REWARD[period]:
            raise _ApiError(f"جایزه باید بین ۱ تا {TASK_MAX_REWARD[period]:,} تومن باشه")
        if not title:
            title = task_auto_title(metric, target)
    elif not title:
        raise _ApiError("متن تسک خالیه")
    await pool.execute("INSERT INTO tasks (period, title, metric, target, reward) VALUES ($1, $2, $3, $4, $5)",
                       period, title, metric, target, reward)
    task_cache_clear()
    await _audit(user, "task_add", None, f"{'روزانه' if period == 'daily' else 'هفتگی'}: {title}" + (f" (جایزه {reward:,})" if metric else ""))
    return _j({"ok": True})


async def _p_task_del(b, user):
    tid = _int(b.get("task_id"))
    if tid is None:
        raise _ApiError("درخواست نامعتبر")
    per = await pool.fetchval("SELECT period FROM tasks WHERE task_id = $1", tid)
    if per:
        _need(user, "task_daily" if per == "daily" else "task_weekly")
    title = await pool.fetchval("UPDATE tasks SET active = FALSE WHERE task_id = $1 RETURNING title", tid)
    task_cache_clear()
    await _audit(user, "task_del", None, title or str(tid))
    return _j({"ok": True})


async def _p_toggle(b, user):
    global BOT_ENABLED
    BOT_ENABLED = not BOT_ENABLED
    await pool.execute(
        "INSERT INTO bot_settings (key, value) VALUES ('bot_enabled', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        "1" if BOT_ENABLED else "0")
    await _audit(user, "toggle", None, "روشن شد" if BOT_ENABLED else "خاموش شد")
    return _j({"enabled": BOT_ENABLED})


async def _p_bc_start(b, user):
    target = b.get("target")
    text = str(b.get("text") or "").strip()
    if target not in ("u", "g", "a", "me"):
        raise _ApiError("مخاطب نامعتبر")
    if not text:
        raise _ApiError("متن پیام خالیه")
    if len(text) > 4096:
        raise _ApiError("متن بیشتر از ۴۰۹۶ کاراکتره")
    parse = "HTML" if b.get("html") else None
    admin_id = int(user["id"])
    if target == "me" or parse:   # تست اول رو خود پشتیبان؛ اگه HTML خراب باشه همین‌جا معلوم میشه، نه وسط ارسال
        try:
            await BOT_REF.send_message(admin_id, text, parse_mode=parse)
        except Exception as e:
            raise _ApiError(f"ارسال نشد: {str(e)[:150]}")
        if target == "me":
            return _j({"ok": True, "done": True})
    if any(j["running"] for j in BC_JOBS.values()):
        raise _ApiError("یه ارسال همگانی هنوز در حال انجامه؛ صبر کن تموم شه.")
    job = {"id": os.urandom(4).hex(), "total": 0, "ok": 0, "fail": 0, "running": True}
    BC_JOBS[job["id"]] = job
    for k in list(BC_JOBS)[:-8]:
        BC_JOBS.pop(k, None)
    task = asyncio.create_task(do_broadcast(BOT_REF, admin_id, {"target": target, "text": text, "parse": parse}, job))
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    await _audit(user, "broadcast", None, f"{ {'u': 'کاربرها', 'g': 'گروه‌ها', 'a': 'هر دو'}[target] }: {text[:120]}")
    return _j({"ok": True, "id": job["id"]})



async def _g_group(qs, user):
    cid = _int(qs.get("chat"))
    if cid is None:
        raise _ApiError("گروه نامعتبره")
    chat = await pool.fetchrow("SELECT chat_id,title FROM chats WHERE chat_id=$1", cid)
    members = await pool.fetch(
        "SELECT user_id, max(name) AS name, max(username) AS username, count(*) AS messages, max(ts) AS last_ts "
        "FROM group_msgs WHERE chat_id=$1 GROUP BY user_id ORDER BY max(ts) DESC LIMIT 500", cid)
    admins = []
    try:
        if BOT_REF:
            admins = await _tg_call(BOT_REF.get_chat_administrators, cid)
    except Exception:
        admins = []
    admin_ids = {int(x.user.id) for x in admins}
    out = []
    seen = set()
    for r in members:
        uid = int(r["user_id"]); seen.add(uid)
        out.append({**dict(r), "is_admin": uid in admin_ids})
    for a in admins:
        uid = int(a.user.id)
        if uid not in seen:
            out.append({"user_id":uid,"name":a.user.full_name,"username":a.user.username,"messages":0,"last_ts":None,"is_admin":True})
    return _j({"chat_id":cid,"title":chat["title"] if chat else str(cid),"members":out,"known_count":len(out),"note":"تلگرام فهرست کامل همه اعضای گروه را از طریق Bot API در اختیار ربات نمی‌گذارد؛ این فهرست شامل اعضای ثبت‌شده در پیام‌ها و مدیرانی است که ربات می‌تواند ببیند."})


def _social_staff(user):
    uid = int(user["id"])
    return uid in OWNER_IDS or "panel" in STAFF.get(uid, set())

def _social_emoji_staff(user):
    uid = int(user["id"])
    return uid in OWNER_IDS or "emoji" in STAFF.get(uid, set())

SOCIAL_MEDIA_MAX = 3 * 1024 * 1024        # عکس/ویدیوی پست و استوری: حداکثر ۳ مگابایت
SOCIAL_AVATAR_MAX = 135_000               # عکس پروفایل (بعد از فشرده‌سازی)
SOCIAL_POSTS_PER_DAY = 5                  # سقف پست روزانه‌ی کاربر عادی
SOCIAL_STORIES_PER_DAY = 2                # سقف استوری روزانه‌ی کاربر عادی
_SOCIAL_LAST_CLEAN = 0.0


def _social_day() -> str:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Tehran")).strftime("%Y-%m-%d")


def _social_mod(user) -> bool:
    uid = int(user["id"])
    return _social_staff(user) and has_perm(uid, "miniapp")


def _sniff_video(b: bytes):
    if len(b) > 12 and b[4:8] == b"ftyp":
        return "video/quicktime" if b[8:12] == b"qt  " else "video/mp4"
    if b[:4] == b"\x1a\x45\xdf\xa3":
        return "video/webm"
    return None


def _social_media_input(b, uid, kind):
    raw = b.get("media_data")
    if not raw:
        return None
    if not isinstance(raw, str) or "," not in raw:
        raise _ApiError("فایل معتبر نیست")
    head, payload = raw.split(",", 1)
    if not re.fullmatch(r"data:[\w/+.\-]*;base64", head):
        raise _ApiError("فایل معتبر نیست")
    big = kind in ("post", "story")
    limit = SOCIAL_MEDIA_MAX if big else SOCIAL_AVATAR_MAX
    too_big = "حجم فایل باید کمتر از ۳ مگابایت باشد" if big else "حجم تصویر باید کمتر از ۱۳۵ کیلوبایت باشد"
    if len(payload) > limit * 4 // 3 + 16:
        raise _ApiError(too_big)
    import base64, binascii
    try:
        data = base64.b64decode(payload, validate=True)
    except (ValueError, binascii.Error):
        raise _ApiError("فایل خراب است")
    if not data or len(data) > limit:
        raise _ApiError(too_big)
    ctype = _sniff_image(data)
    if ctype == "image/gif" and not big:
        ctype = None
    if not ctype and big:
        ctype = _sniff_video(data)
    if not ctype:
        raise _ApiError("فقط عکس (JPG/PNG/WEBP/GIF) یا ویدیو (MP4/MOV/WEBM) پشتیبانی می‌شود")
    return data, ctype


async def _social_cleanup():
    """استوری‌های منقضی‌شده (بعد از ۲۴ ساعت) و فایل‌هاشون رو از دیتابیس پاک می‌کنه."""
    global _SOCIAL_LAST_CLEAN
    now = time.time()
    if now - _SOCIAL_LAST_CLEAN < 300:
        return
    _SOCIAL_LAST_CLEAN = now
    try:
        async with pool.acquire() as c:
            async with c.transaction():
                await c.execute("DELETE FROM social_media WHERE kind='story' AND expires_at IS NOT NULL AND expires_at<$1", now)
                await c.execute("DELETE FROM social_stories WHERE expires_at<$1", now)
    except Exception:
        log.warning("social cleanup failed", exc_info=True)


async def _social_quota(uid, user):
    if _social_staff(user):
        return {"unlimited": True, "posts_max": 0, "stories_max": 0, "posts_left": 999, "stories_left": 999}
    row = await pool.fetchrow("SELECT posts, stories FROM social_quota WHERE user_id=$1 AND day=$2", uid, _social_day())
    p = int(row["posts"]) if row else 0
    s = int(row["stories"]) if row else 0
    return {"unlimited": False, "posts_max": SOCIAL_POSTS_PER_DAY, "stories_max": SOCIAL_STORIES_PER_DAY,
            "posts_left": max(0, SOCIAL_POSTS_PER_DAY - p), "stories_left": max(0, SOCIAL_STORIES_PER_DAY - s)}


async def _social_take_quota(c, uid, user, col, maxn, label):
    if _social_staff(user):
        return
    r = await c.fetchval(
        f"INSERT INTO social_quota(user_id,day,{col}) VALUES($1,$2,1) "
        f"ON CONFLICT(user_id,day) DO UPDATE SET {col}=social_quota.{col}+1 WHERE social_quota.{col}<$3 RETURNING {col}",
        uid, _social_day(), maxn)
    if r is None:
        raise _ApiError(f"سهمیه‌ی {label} امروزت تموم شده (روزی {maxn} تا)؛ فردا دوباره می‌تونی", 429)


async def _social_me(qs, user):
    uid = int(user["id"])
    p = await pool.fetchrow("SELECT username,display_name,bio,avatar_media_id,created_at FROM social_profiles WHERE user_id=$1", uid)
    return _j({"id": uid, "name": user.get("first_name") or "کاربر",
               "is_support": _social_staff(user) and has_perm(uid, "miniapp"),
               "perms": sorted(perms_of(uid)) if _social_staff(user) and has_perm(uid, "miniapp") else [],
               "premium_emoji": _social_emoji_staff(user), "profile": dict(p) if p else None,
               "quota": await _social_quota(uid, user)})


async def _social_posts_rows(me_id, uid=None, top=False, limit=30):
    args = [limit, me_id]
    cond = ""
    if uid is not None:
        args.append(uid)
        cond = f"WHERE p.user_id=${len(args)}"
    elif top:
        args.append(time.time() - 7 * 86400)
        cond = f"WHERE p.created_at>${len(args)}"
    order = ("((SELECT count(*) FROM social_reactions r2 WHERE r2.post_id=p.post_id)*2 + "
             "(SELECT count(*) FROM social_comments c2 WHERE c2.post_id=p.post_id)) DESC, p.created_at DESC") if top else "p.created_at DESC"
    rows = await pool.fetch(
        "SELECT p.post_id,p.user_id,p.caption,p.media_id,p.created_at,sp.username,sp.display_name,sp.avatar_media_id, "
        "(SELECT content_type FROM social_media m WHERE m.media_id=p.media_id) AS media_type, "
        "(SELECT count(*) FROM social_reactions r WHERE r.post_id=p.post_id) AS likes, "
        "(SELECT count(*) FROM social_comments c WHERE c.post_id=p.post_id) AS comments, "
        "(SELECT emoji FROM social_reactions r WHERE r.post_id=p.post_id AND r.user_id=$2) AS my_reaction "
        "FROM social_posts p LEFT JOIN social_profiles sp ON sp.user_id=p.user_id " + cond +
        " ORDER BY " + order + " LIMIT $1", *args)
    return [dict(r) for r in rows]


async def _social_feed(qs, user):
    limit = _int(qs.get("limit"), 30, 1, 60)
    return _j(await _social_posts_rows(int(user["id"]), _int(qs.get("uid")), qs.get("mode") == "top", limit))


async def _social_stories(qs, user):
    await _social_cleanup()
    rows = await pool.fetch(
        "SELECT s.story_id,s.user_id,s.caption,s.media_id,s.created_at,s.expires_at,sp.username,sp.display_name,sp.avatar_media_id, "
        "(SELECT content_type FROM social_media m WHERE m.media_id=s.media_id) AS media_type "
        "FROM social_stories s LEFT JOIN social_profiles sp ON sp.user_id=s.user_id "
        "WHERE s.expires_at>$1 ORDER BY s.created_at DESC LIMIT 120", time.time())
    return _j([dict(r) for r in rows])


async def _social_profile(qs, user):
    uid = _int(qs.get("uid"), int(user["id"]))
    p = await pool.fetchrow("SELECT user_id,username,display_name,bio,avatar_media_id,created_at FROM social_profiles WHERE user_id=$1", uid)
    if not p:
        return _j({"profile": None, "posts": [], "stats": {"posts": 0, "likes": 0, "comments": 0}})
    posts = await _social_posts_rows(int(user["id"]), uid, False, 60)
    st = await pool.fetchrow(
        "SELECT (SELECT count(*) FROM social_posts WHERE user_id=$1) AS posts, "
        "(SELECT count(*) FROM social_reactions r JOIN social_posts x ON x.post_id=r.post_id WHERE x.user_id=$1) AS likes, "
        "(SELECT count(*) FROM social_comments c JOIN social_posts x ON x.post_id=c.post_id WHERE x.user_id=$1) AS comments", uid)
    return _j({"profile": dict(p), "posts": posts, "stats": dict(st)})


async def _social_media_get(qs, user):
    mid = _int(qs.get("id"))
    if mid is None: raise _ApiError("رسانه نامعتبره")
    r = await pool.fetchrow("SELECT data,content_type,expires_at FROM social_media WHERE media_id=$1", mid)
    if not r or (r["expires_at"] and r["expires_at"] < time.time()):
        return 404, "text/plain", b"not found", {}
    return 200, r["content_type"], bytes(r["data"]), {"Cache-Control": "private, max-age=300"}


async def _social_profile_save(b, user):
    uid=int(user["id"]); username=(b.get("username") or "").strip().lower().lstrip("@"); name=(b.get("display_name") or user.get("first_name") or "کاربر").strip()[:60]; bio=(b.get("bio") or "").strip()[:300]
    if not re.fullmatch(r"[a-z0-9_]{3,24}",username): raise _ApiError("نام کاربری باید ۳ تا ۲۴ حرف انگلیسی، عدد یا _ باشد")
    if not name: raise _ApiError("نام نمایشی خالیه")
    media=_social_media_input(b,uid,"avatar")
    try:
        async with pool.acquire() as c:
            async with c.transaction():
                mid=None
                if media:
                    mid=await c.fetchval("INSERT INTO social_media(user_id,kind,data,content_type,created_at) VALUES($1,'avatar',$2,$3,$4) RETURNING media_id",uid,media[0],media[1],time.time())
                await c.execute("INSERT INTO social_profiles(user_id,username,display_name,bio,avatar_media_id,created_at) VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT(user_id) DO UPDATE SET username=EXCLUDED.username,display_name=EXCLUDED.display_name,bio=EXCLUDED.bio,avatar_media_id=COALESCE(EXCLUDED.avatar_media_id,social_profiles.avatar_media_id)",uid,username,name,bio,mid,time.time())
    except asyncpg.UniqueViolationError: raise _ApiError("این نام کاربری قبلاً گرفته شده")
    return _j({"ok":True})


async def _social_post_create(b, user):
    uid = int(user["id"])
    if not await pool.fetchval("SELECT 1 FROM social_profiles WHERE user_id=$1", uid): raise _ApiError("برای انتشار پست اول پیج بساز")
    caption = (b.get("caption") or "").strip()[:1500]; media = _social_media_input(b, uid, "post")
    if not caption and not media: raise _ApiError("متن، عکس یا ویدیوی پست رو وارد کن")
    mid = None
    async with pool.acquire() as c:
        async with c.transaction():
            await _social_take_quota(c, uid, user, "posts", SOCIAL_POSTS_PER_DAY, "پست")
            if media: mid = await c.fetchval("INSERT INTO social_media(user_id,kind,data,content_type,created_at) VALUES($1,'post',$2,$3,$4) RETURNING media_id", uid, media[0], media[1], time.time())
            pid = await c.fetchval("INSERT INTO social_posts(user_id,caption,media_id,created_at) VALUES($1,$2,$3,$4) RETURNING post_id", uid, caption, mid, time.time())
    return _j({"ok": True, "post_id": pid})


async def _social_story_create(b, user):
    uid = int(user["id"])
    if not await pool.fetchval("SELECT 1 FROM social_profiles WHERE user_id=$1", uid): raise _ApiError("برای انتشار استوری اول پیج بساز")
    caption = (b.get("caption") or "").strip()[:500]; media = _social_media_input(b, uid, "story")
    if not caption and not media: raise _ApiError("متن، عکس یا ویدیوی استوری رو وارد کن")
    await _social_cleanup()
    now = time.time(); mid = None
    async with pool.acquire() as c:
        async with c.transaction():
            await _social_take_quota(c, uid, user, "stories", SOCIAL_STORIES_PER_DAY, "استوری")
            if media: mid = await c.fetchval("INSERT INTO social_media(user_id,kind,data,content_type,created_at,expires_at) VALUES($1,'story',$2,$3,$4,$5) RETURNING media_id", uid, media[0], media[1], now, now + 86400)
            sid = await c.fetchval("INSERT INTO social_stories(user_id,caption,media_id,created_at,expires_at) VALUES($1,$2,$3,$4,$5) RETURNING story_id", uid, caption, mid, now, now + 86400)
    return _j({"ok": True, "story_id": sid})


async def _social_post_delete(b, user):
    uid = int(user["id"]); pid = _int(b.get("post_id"))
    row = await pool.fetchrow("SELECT user_id,media_id FROM social_posts WHERE post_id=$1", pid) if pid is not None else None
    if not row: raise _ApiError("پست پیدا نشد", 404)
    if int(row["user_id"]) != uid and not _social_mod(user): raise _ApiError("اجازه‌ی حذف این پست رو نداری", 403)
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("DELETE FROM social_reactions WHERE post_id=$1", pid)
            await c.execute("DELETE FROM social_comments WHERE post_id=$1", pid)
            await c.execute("DELETE FROM social_posts WHERE post_id=$1", pid)
            if row["media_id"]: await c.execute("DELETE FROM social_media WHERE media_id=$1", row["media_id"])
    return _j({"ok": True})


async def _social_story_delete(b, user):
    uid = int(user["id"]); sid = _int(b.get("story_id"))
    row = await pool.fetchrow("SELECT user_id,media_id FROM social_stories WHERE story_id=$1", sid) if sid is not None else None
    if not row: raise _ApiError("استوری پیدا نشد", 404)
    if int(row["user_id"]) != uid and not _social_mod(user): raise _ApiError("اجازه‌ی حذف این استوری رو نداری", 403)
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("DELETE FROM social_stories WHERE story_id=$1", sid)
            if row["media_id"]: await c.execute("DELETE FROM social_media WHERE media_id=$1", row["media_id"])
    return _j({"ok": True})


async def _social_react(b, user):
    pid = _int(b.get("post_id")); emoji = (b.get("emoji") or "❤️").strip()
    if pid is None or emoji not in ("❤️", "🔥", "😂", "😍", "👏", "💯", "😮"): raise _ApiError("واکنش نامعتبره")
    if not await pool.fetchval("SELECT 1 FROM social_posts WHERE post_id=$1", pid): raise _ApiError("پست پیدا نشد", 404)
    uid = int(user["id"])
    old = await pool.fetchval("SELECT emoji FROM social_reactions WHERE post_id=$1 AND user_id=$2", pid, uid)
    if old == emoji:
        await pool.execute("DELETE FROM social_reactions WHERE post_id=$1 AND user_id=$2", pid, uid); liked = False
    else:
        await pool.execute("INSERT INTO social_reactions(post_id,user_id,emoji,created_at) VALUES($1,$2,$3,$4) ON CONFLICT(post_id,user_id) DO UPDATE SET emoji=EXCLUDED.emoji,created_at=EXCLUDED.created_at", pid, uid, emoji, time.time()); liked = True
    likes = await pool.fetchval("SELECT count(*) FROM social_reactions WHERE post_id=$1", pid)
    return _j({"ok": True, "liked": liked, "likes": int(likes)})


async def _social_comments(qs, user):
    pid = _int(qs.get("post_id"))
    if pid is None: raise _ApiError("پست نامعتبره")
    rows = await pool.fetch(
        "SELECT c.comment_id,c.user_id,c.text,c.created_at,sp.username,sp.display_name,sp.avatar_media_id "
        "FROM social_comments c LEFT JOIN social_profiles sp ON sp.user_id=c.user_id "
        "WHERE c.post_id=$1 ORDER BY c.comment_id ASC LIMIT 300", pid)
    return _j([dict(r) for r in rows])


async def _social_comment_add(b, user):
    uid = int(user["id"]); pid = _int(b.get("post_id")); text = (b.get("text") or "").strip()[:500]
    if not await pool.fetchval("SELECT 1 FROM social_profiles WHERE user_id=$1", uid): raise _ApiError("برای کامنت گذاشتن اول پیج بساز")
    if pid is None or not await pool.fetchval("SELECT 1 FROM social_posts WHERE post_id=$1", pid): raise _ApiError("پست پیدا نشد", 404)
    if not text: raise _ApiError("کامنت خالیه")
    if int(await pool.fetchval("SELECT count(*) FROM social_comments WHERE user_id=$1 AND created_at>$2", uid, time.time() - 30)) >= 8:
        raise _ApiError("خیلی سریع کامنت می‌ذاری؛ کمی صبر کن", 429)
    cid = await pool.fetchval("INSERT INTO social_comments(post_id,user_id,text,created_at) VALUES($1,$2,$3,$4) RETURNING comment_id", pid, uid, text, time.time())
    return _j({"ok": True, "comment_id": cid})


async def _social_comment_del(b, user):
    uid = int(user["id"]); cid = _int(b.get("comment_id"))
    owner = await pool.fetchval("SELECT user_id FROM social_comments WHERE comment_id=$1", cid) if cid is not None else None
    if owner is None: raise _ApiError("کامنت پیدا نشد", 404)
    if int(owner) != uid and not _social_mod(user): raise _ApiError("اجازه‌ی حذف این کامنت رو نداری", 403)
    await pool.execute("DELETE FROM social_comments WHERE comment_id=$1", cid)
    return _j({"ok": True})


async def _social_room(qs, user):
    after = _int(qs.get("after"), 0, 0)
    rows = await pool.fetch(
        "SELECT m.message_id,m.user_id,m.text,m.reply_to,m.premium_emoji_id,m.created_at,sp.username,sp.display_name, "
        "(SELECT json_agg(json_build_object('emoji',r.emoji,'n',r.n,'mine',r.mine)) FROM (SELECT emoji,count(*)::int n,bool_or(user_id=$2) mine FROM social_room_reactions WHERE message_id=m.message_id GROUP BY emoji) r) reactions, "
        "(SELECT text FROM social_room_messages x WHERE x.message_id=m.reply_to) reply_text, "
        "(SELECT COALESCE(sp2.display_name,sp2.username,x.user_id::text) FROM social_room_messages x LEFT JOIN social_profiles sp2 ON sp2.user_id=x.user_id WHERE x.message_id=m.reply_to) reply_name "
        "FROM social_room_messages m LEFT JOIN social_profiles sp ON sp.user_id=m.user_id "
        "WHERE ($1=0 OR m.message_id>$1) ORDER BY m.message_id DESC LIMIT 100", after, int(user["id"]))
    return _j([dict(r) for r in reversed(rows)])


async def _social_room_send(b,user):
    uid=int(user["id"]); txt=(b.get("text") or "").strip()[:1200]; reply=_int(b.get("reply_to")); pe=(str(b.get("premium_emoji_id") or "").strip() or None)
    if not txt and not pe: raise _ApiError("پیام خالیه")
    if pe and (not _social_emoji_staff(user) or not pe.isdigit()): raise _ApiError("ایموجی پرمیوم فقط برای پشتیبانی فعاله")
    if reply and not await pool.fetchval("SELECT 1 FROM social_room_messages WHERE message_id=$1",reply): reply=None
    mid=await pool.fetchval("INSERT INTO social_room_messages(user_id,text,reply_to,premium_emoji_id,created_at) VALUES($1,$2,$3,$4,$5) RETURNING message_id",uid,txt,reply,pe,time.time())
    return _j({"ok":True,"message_id":mid})


async def _social_room_react(b,user):
    mid=_int(b.get("message_id")); emoji=(b.get("emoji") or "❤️").strip()
    if mid is None or emoji not in ("❤️","😂","😍","🔥","👏","👍","👎","😮"): raise _ApiError("ری‌اکشن نامعتبره")
    uid=int(user["id"])
    await pool.execute("INSERT INTO social_room_reactions(message_id,user_id,emoji,created_at) VALUES($1,$2,$3,$4) ON CONFLICT(message_id,user_id,emoji) DO NOTHING",mid,uid,emoji,time.time())
    return _j({"ok":True})

_SOCIAL_GET={"/api/social/me":_social_me,"/api/social/feed":_social_feed,"/api/social/stories":_social_stories,"/api/social/profile":_social_profile,"/api/social/room":_social_room,"/api/social/media":_social_media_get,"/api/social/comments":_social_comments}
_SOCIAL_POST={"/api/social/profile/save":_social_profile_save,"/api/social/post":_social_post_create,"/api/social/story":_social_story_create,"/api/social/post/delete":_social_post_delete,"/api/social/story/delete":_social_story_delete,"/api/social/react":_social_react,"/api/social/comment":_social_comment_add,"/api/social/comment/delete":_social_comment_del,"/api/social/room/send":_social_room_send,"/api/social/room/react":_social_room_react}

# هر مسیر API پنل برای کار کردن به کدوم دسترسی‌ها نیاز داره (هر کدوم باشه کافیه).
# adjust / task add / task delete داخل خودِ تابع‌هاشون بر اساس نوع عملیات چک میشن.
_API_PERM = {
    "/api/stats": ("stats",),
    "/api/groups": ("group_msgs",), "/api/group": ("group_msgs",),
    "/api/messages": ("group_msgs",), "/api/media": ("group_msgs",),
    "/api/tasks": ("task_daily", "task_weekly"),
    "/api/task/add": ("task_daily", "task_weekly"), "/api/task/delete": ("task_daily", "task_weekly"),
    "/api/user/adjust": ("money_sub", "money_add", "jagh_sub", "jagh_add"),
    "/api/user/ban": ("ban",), "/api/user/unban": ("unban",),
    "/api/toggle": ("bot_toggle",),
    "/api/bc/start": ("broadcast",), "/api/bc/status": ("broadcast",),
}

_GET = {
    "/api/me": _g_me, "/api/stats": _g_stats, "/api/groups": _g_groups, "/api/group": _g_group, "/api/messages": _g_messages,
    "/api/users": _g_users, "/api/user": _g_user, "/api/avatar": _g_avatar, "/api/media": _g_media,
    "/api/tasks": _g_tasks, "/api/log": _g_log, "/api/bc/status": _g_bc_status,
}
_POST = {
    "/api/user/adjust": _p_adjust, "/api/user/ban": _p_ban, "/api/user/unban": _p_unban,
    "/api/task/add": _p_task_add, "/api/task/delete": _p_task_del, "/api/toggle": _p_toggle,
    "/api/bc/start": _p_bc_start,
}


async def _web_route(method: str, path: str, qs: dict, headers: dict, body: bytes):
    """(status, content-type, body bytes, extra headers)"""
    if path in ("/", "/index.html"):
        return 200, "text/html; charset=utf-8", WEBAPP_HTML.encode(), {}
    if path == "/health":
        return 200, "text/plain", b"ok", {}
    if not path.startswith("/api/"):
        return 404, "text/plain", b"not found", {}
    user = _verify_init_data(headers.get("x-init-data", ""))
    if user is None:
        return _j({"error": "نشست تلگرام معتبر نیست؛ مینی‌اپ را از داخل تلگرام باز کن"}, 403)
    social = path.startswith("/api/social/")
    # پنل پشتیبانی: صاحب ربات و فقط پشتیبان‌های دارای مجوز panel.
    # API اجتماعی جداست و طبق قواعد خودش برای کاربران واردشده بررسی می‌شود.
    if not social:
        _uid = int(user["id"])
        if not (has_perm(_uid, "panel") and has_perm(_uid, "miniapp")):
            return _j({"error": "برای پنل پشتیبانی دسترسی نداری"}, 403)
        _need_p = _API_PERM.get(path)
        if _need_p and not any(has_perm(_uid, p) for p in _need_p):
            return _j({"error": "این بخش برای حساب تو فعال نیست."}, 403)
    if pool is None:
        return _j({"error": "دیتابیس هنوز آماده نیست"}, 503)
    try:
        if method in ("GET", "HEAD"):
            fn = (_SOCIAL_GET if social else _GET).get(path)
            if fn:
                return await fn(qs, user)
        elif method == "POST":
            fn = (_SOCIAL_POST if social else _POST).get(path)
            if fn:
                try:
                    data = json.loads(body.decode("utf-8") or "{}")
                except Exception:
                    raise _ApiError("بدنه‌ی درخواست نامعتبره")
                if not isinstance(data, dict):
                    raise _ApiError("بدنه‌ی درخواست نامعتبره")
                return await fn(data, user)
    except _ApiError as e:
        return _j({"error": str(e)}, e.status)
    except Exception:
        log.exception("api error %s", path)
        return _j({"error": "خطای داخلی سرور"}, 500)
    return _j({"error": "not found"}, 404)


async def _http_conn(reader, writer):
    import gzip
    import urllib.parse
    from http import HTTPStatus
    status, ctype, body, extra, method, headers = 500, "text/plain", b"error", {}, "GET", {}
    try:
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 10)
        lines = head.decode("latin1").split("\r\n")
        method, target, _v = lines[0].split(" ", 2)
        headers = {k.strip().lower(): v.strip() for k, v in (l.split(":", 1) for l in lines[1:] if ":" in l)}
        clen = _int(headers.get("content-length"), 0, 0)
        if method not in ("GET", "HEAD", "POST"):
            status, ctype, body, extra = 405, "text/plain", b"method not allowed", {}
        elif clen > (4_700_000 if urllib.parse.urlsplit(target).path in ("/api/social/post", "/api/social/story") else 200_000):
            status, ctype, body, extra = 413, "text/plain", b"too large", {}
        else:
            raw = await asyncio.wait_for(reader.readexactly(clen), 15 if clen < 300_000 else 90) if clen else b""
            url = urllib.parse.urlsplit(target)
            status, ctype, body, extra = await _web_route(method, url.path, dict(urllib.parse.parse_qsl(url.query)), headers, raw)
    except Exception:
        log.warning("webapp request failed", exc_info=True)
    try:
        hdr = {"Content-Type": ctype, "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
               "Referrer-Policy": "no-referrer", "Connection": "close", **extra}
        if len(body) > 1200 and (ctype.startswith("text/") or ctype.startswith("application/json")) \
                and "gzip" in headers.get("accept-encoding", ""):
            body = gzip.compress(body, 5)
            hdr["Content-Encoding"] = "gzip"
            hdr["Vary"] = "Accept-Encoding"
        hdr["Content-Length"] = str(len(body))
        try:
            reason = HTTPStatus(status).phrase
        except ValueError:
            reason = "Error"
        out = f"HTTP/1.1 {status} {reason}\r\n" + "".join(f"{k}: {v}\r\n" for k, v in hdr.items()) + "\r\n"
        writer.write(out.encode("latin1") + (b"" if method == "HEAD" else body))
        await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def start_webapp():
    """سرور کوچک مینی‌اپ (بدون نیاز به پکیج جدید). Railway پورتش رو تو PORT میده."""
    port = int(os.environ.get("PORT", "8080"))
    srv = await asyncio.start_server(_http_conn, "0.0.0.0", port)
    log.info("webapp listening on :%s (WEBAPP_URL=%s)", port, WEBAPP_URL or "—")
    return srv


async def startup(app: Application):
    """موقع روشن شدن: دیتابیس رو آماده می‌کنه و به پشتیبان‌ها تو تلگرام خبر میده (بدون نیاز به لاگ Railway)."""
    global BOT_REF
    BOT_REF = app.bot
    try:
        await init_db(app)
        app.bot_data["car_task"] = asyncio.create_task(car_profit_loop())
        app.bot_data["energy_task"] = asyncio.create_task(energy_decay_loop())
        app.bot_data["bank_interest_task"] = asyncio.create_task(bank_interest_loop())
        try:
            from telegram import MenuButtonDefault
            await app.bot.delete_my_commands(scope=BotCommandScopeAllPrivateChats())
            await app.bot.set_chat_menu_button(menu_button=MenuButtonDefault())
        except Exception:
            log.warning("reset commands menu failed", exc_info=True)
        app.bot_data["ref_task"] = asyncio.create_task(referral_check_loop(app))
        app.bot_data["gw_task"] = asyncio.create_task(giveaway_loop(app))
        try:
            app.bot_data["web"] = await start_webapp()
        except Exception:
            log.exception("webapp start failed")
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
        "🔥 جق بزن و پولدار شو! 🔥\n\n"
        "اینجا هر پیام می‌تونه شروع یه امپراتوری باشه؛ جق بزن، پول جمع کن، خلاف کن، ماشین بخر و رکورد بزن!\n\n"
        f"💸 هر ۵ دقیقه یک‌بار توی گروه «{TRIGGER_WORD}» رو بنویس و جایزه بگیر.\n"
        f"👤 برای دیدن کاراکترت بنویس «{ME_WORD}».\n"
        "🎮 برای بانک، خلاف، ماشین، دست، کازینو و تنظیمات بنویس «خودم».\n\n"
        "🚀 آماده‌ای؟ ربات رو به گروهت ببر و بازی رو شروع کن!"
    )


def welcome_keyboard(bot_username: str = "") -> InlineKeyboardMarkup:
    rows = [
        [btn("زیرمجموعه‌گیری", "success", icon=REF_EID, callback_data="rf:open")],
        [btn("خرید پول بازی", "primary", icon=EID_MONEY, callback_data="bu:menu")],
        [btn("📖 راهنما", "primary", callback_data="guide:open")],
    ]
    if bot_username:
        rows.append([btn("👥 منو ببر به گروهت", "success", url=f"https://t.me/{bot_username}?startgroup=true")])
    return InlineKeyboardMarkup(rows)


def _gq(title: str, body: str) -> str:
    return f"<blockquote><b>{title}</b>\n{body}</blockquote>"


def _guide_sections() -> list:
    return [
        _gq("🤛 جق", "<code>جق</code>\nهر ۵ دقیقه یه بار جایزه می‌گیری (با ارتقای دست تا ۴ دقیقه و ۴۰ ثانیه کم میشه). پولش میره تو دستگاه جق. اگه انرژیت کم باشه پولش نصفه."),
        _gq(f"{JM_CE} دستگاه جق", f"<code>دستگاه جق</code>\nپول جق، بوس و بغل میره تو دستگاه (ظرفیت {JM_CAP:,} تومن). با نوشتن این کلمه همه‌ش میره تو کیف پولت؛ حداقل {JM_MIN:,} تومن باید توش باشه. وقتی پر بشه پول جدید جا نمیشه، پس به موقع برداشت کن. وضعیتش تو پنل «خودم» هم دیده میشه."),
        _gq("🎖 پنل شخصی", "<code>خودم</code>\nبانک، خلاف، ماشینم، کازینو، قدرت‌ها و تنظیمات (تغییر جنسیت، راهنما، انتقال اکانت) رو از اینجا باز می‌کنی. کاراکتر و شغل و دارایی‌ت هم همین‌جا نوشته میشه."),
        _gq(f"{POWERS_CE} قدرت‌ها", "از پنل «خودم» ← قدرت‌ها: جاسوسی، دست و دزدی."),
        _gq(f"{HAND_CE} دست", "<code>دست</code>\n۵ سطح ارتقا داره و هر سطح ۵ ثانیه از زمان انتظار جق کم می‌کنه."),
        _gq(f"{SPY_CE} جاسوسی", f"<code>خرید جاسوسی</code>\nقیمت: {SPY_PRICE:,} تومن. بعدش روی پیام کاربر ریپلای کن و بنویس <code>جاسوسی</code> تا پول، بانک، شغل و فعالیت‌هاش رو ببینی. روزی {SPY_DAILY_LIMIT} بار."),
        _gq(f"{THEFT_CE} دزدی", f"روی پیام کاربر ریپلای کن و بنویس <code>دزدی</code>\nهر ۳ ساعت یه بار بین {THEFT_MIN:,} تا {THEFT_MAX:,} تومن از جیبش برمی‌داری. پولی که تو بانکه امنه؛ اگه جیبش خالی باشه فقط می‌فهمی بدبخته."),
        _gq("🏦 بانک", f"<code>بانک</code>\n<code>واریز 100m</code> (کارمزد {BANK_FEE:,} تومن)\n<code>برداشت 100m</code> (بدون کارمزد)\n{BANK_INTEREST_RATE_PCT}٪ سود هر ۱۲ ساعت روی موجودی بانک؛ مقدار سود بعدی تو پنل بانک نوشته میشه."),
        _gq("👛 دارایی", "<code>دارایی</code>\nپول جیب، بانک و مجموع دارایی‌ت."),
        _gq("💼 شغل و شیفت", "<code>شغل</code> لیست شغل‌ها و جق لازم برای هر کدوم\n<code>کار</code> شیفت کاری (هر ۲۰ دقیقه یه بار) و گرفتن حقوق."),
        _gq("🎖 کاراکتر", "<code>کاراکتر</code>\nکاراکتر و سطحت رو می‌بینی؛ با جق زدن بیشتر سطحت بالا میره."),
        _gq("🚗 ماشین و گاراژ", f"<code>ماشینم</code> پنل ماشین\n<code>گاراژ</code> جدول ماشین‌ها\nماشین‌ها به ترتیب خریده میشن. هر ۴ قطعه (موتور، بدنه، لاستیک، شیشه و رینگ) تا سطح {CAR_MAX_LEVEL} ارتقا می‌خوره و رنگ ماشین {CAR_COLOR_PRICE:,} تومنه. درآمد ساعتی میره تو صندوق (سقف {CAR_VAULT_CAP:,} تومن برای هر ماشین) و با دکمه «جمع کن» برداشت میشه."),
        _gq("🏠 خونه", "<code>خونه</code>\nخرید و تعمیر خونه و وسایلش و جمع کردن درآمدش."),
        _gq("🤗 بوس و بغل", f"روی پیام کاربر ریپلای کن و بنویس <code>بوس</code> یا <code>بغل</code>\nهر کاربر رو هر ۵ دقیقه یه بار می‌شه بغل/بوس کرد. به هر دو نفر انرژی و {HUG_MONEY[0]} تا {HUG_MONEY[1]} تومن (تو دستگاه جق) می‌رسه. بوس و بغل روی همسر بقیه ۳ انرژی منفی داره."),
        _gq("🎁 هدیه", "<code>هدیه</code> لیست هدیه‌ها و قیمتشون\nریپلای + <code>هدیه گل</code>\nهدیه به طرف مقابل انرژی میده."),
        _gq("⚡ انرژی", "انرژی زیر ۳۰٪ یعنی پول جق نصف، درآمد ماشین ۲۵٪ کمتر و شانس گیر افتادن تو خلاف بیشتر. با بغل، بوس و هدیه بالا میره."),
        _gq("💍 ازدواج", "ریپلای مرد روی پیام زن + <code>ازدواج</code>\n<code>همسرم</code> اطلاعات همسر و میزان رابطه\n<code>سکس</code> بین زوج‌ها (هر ۱۲۰ دقیقه)\n<code>فروشگاه</code> خرید کاندوم و قرص."),
        _gq("💔 قهر و آشتی", "ریپلای + <code>قهر</code>\nتا ۳ ساعت بغل، بوس، هدیه، دوئل و کازینو با هم ندارن.\nریپلای + <code>آشتی</code> قهر رو تموم می‌کنه."),
        _gq("🎭 گی و لز", "ریپلای + <code>گی</code> یا <code>لز</code>\nهر کاربر هر ۳۰ دقیقه یه بار."),
        _gq("⚔️ دوئل جقی", "ریپلای + <code>دوئل جقی</code>\nطرف مقابل باید قبول کنه؛ هر ۵ دقیقه یه بار."),
        _gq("🎰 کازینو", "<code>کازینو</code>\nتاس، اسلات، مین، موشک و میزهای دونفره. سقف شرط ۵۰ میلیون تومنه و بعد از هر بازی ۱۵ ثانیه استراحت داری."),
        _gq("🚨 خلاف", "<code>خلاف</code>\nعملیات‌های خلاف با ریسک گیر افتادن؛ هرچی انرژی کمتر، شانس گیر افتادنت بیشتر."),
        _gq("💸 انتقال پول", "ریپلای + <code>انتقال پول 100 هزار</code>\nسقف انتقال: ۲۰۰ میلیون تومن در روز."),
        _gq("📋 تسک", "<code>تسک</code>\nتسک‌های روزانه و هفتگی با جایزه؛ ربات خودش پیشرفتت رو حساب می‌کنه."),
        _gq("🏆 لیدربرد", "<code>لیدربرد</code>\nبرترین‌ها بر اساس پول و جق."),
        _gq("🔥 ایونت درآمد", "گاهی پشتیبانی ایونت می‌ذاره و برای مدت محدود پول جق و کار چند برابر میشه؛ ربات تو همه‌ی گروه‌ها اعلامش می‌کنه."),
        _gq("🎁 قرعه‌کشی", "پشتیبانی‌ها قرعه‌کشی می‌سازن. با دکمه «شرکت در قرعه‌کشی» وارد میشی و با دعوت دوستات تیکتت بیشتر میشه (اگه دعوت‌شده از کانال‌ها بره تیکت هم میره)."),
        _gq("🔁 انتقال اکانت", "فقط تو پی‌وی ربات: <code>خودم</code> ← تنظیمات ← انتقال اکانت با آیدی عددی."),
    ]


GUIDE_SPLIT = 14


def guide_text(page: int = 0) -> str:
    secs = _guide_sections()
    part = secs[:GUIDE_SPLIT] if page == 0 else secs[GUIDE_SPLIT:]
    head = f"📖 <b>راهنمای ربات — صفحه {1 if page == 0 else 2} از ۲</b>"
    return head + "\n\n" + "\n".join(part)


def guide_kb(page: int, back_cb: str, next_cb: str, prev_cb: str) -> InlineKeyboardMarkup:
    nav = [btn("صفحه بعد ⬅️", "primary", callback_data=next_cb)] if page == 0 else [btn("➡️ صفحه قبل", "primary", callback_data=prev_cb)]
    return InlineKeyboardMarkup([nav, [btn("🔙 برگشت", "primary", callback_data=back_cb)]])


async def guide_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    page = 1 if q.data == "guide:p1" else 0
    await q.answer()
    await q.edit_message_text(guide_text(page), parse_mode="HTML", reply_markup=guide_kb(page, "guide:back", "guide:p1", "guide:open"))

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
    await process_referral(context.bot, user.id)
    bot_username = (await context.bot.get_me()).username or ""
    await update.message.reply_text(welcome_ok(), parse_mode="HTML", reply_markup=welcome_keyboard(bot_username))
    await send_main_menu(context.bot, user.id)


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
    await process_referral(context.bot, user.id)
    await q.answer("تایید شد ✅")
    bot_username = (await context.bot.get_me()).username or ""
    await q.edit_message_text(welcome_ok(), parse_mode="HTML", reply_markup=welcome_keyboard(bot_username))
    await send_main_menu(context.bot, user.id)


# ───────────────────────── بانک و املاک ─────────────────────────
BANK_EMOJIS = ("5445355530111437729", "5445353829304387411", "5443127283898405358")
BANK_ICON_ID = "5332455502917949981"
BANK_CE = "".join(f'<tg-emoji emoji-id="{eid}">🏦</tg-emoji>' for eid in BANK_EMOJIS)
BANK_FEE = 5_000
# ───── ایونت درآمد: پشتیبان می‌نویسه «ایونت درآمد 5 5دقیقه» ← تو اون مدت حقوق شیفت (کار) و پول جق ×۵ میشه ─────
INCOME_EVENT = {"mult": 1.0, "end": 0.0}
EVENT_CE = '<tg-emoji emoji-id="5875483791702630534">🎉</tg-emoji><tg-emoji emoji-id="5875065225664798722">🔥</tg-emoji>'
EVENT_MAX_SECONDS = 24 * 3600
EVENT_MAX_MULT = 100


def income_mult() -> float:
    return float(INCOME_EVENT["mult"]) if INCOME_EVENT["end"] > time.time() else 1.0


def event_banner() -> str:
    """متن زیر پیام کار/جق وقتی ایونت فعاله (وقتی تموم شد خالیه و همه‌چی مثل قبل)."""
    left = INCOME_EVENT["end"] - time.time()
    if left <= 0 or INCOME_EVENT["mult"] <= 1:
        return ""
    return f"\n\n{EVENT_CE} ایونت درآمد ×{INCOME_EVENT['mult']:g} شروع شده! {_duration_fa(int(left) + 1)} دیگه مونده تا تموم شه، بدو"


async def _event_save():
    await pool.execute("INSERT INTO bot_settings (key, value) VALUES ('income_event', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
                       f"{INCOME_EVENT['mult']}|{INCOME_EVENT['end']}")


async def _event_load():
    v = await pool.fetchval("SELECT value FROM bot_settings WHERE key = 'income_event'")
    try:
        m, e = (v or "").split("|")
        INCOME_EVENT["mult"], INCOME_EVENT["end"] = float(m), float(e)
    except Exception:
        pass


def _ev_e(eid: str, fb: str) -> str:
    return f'<tg-emoji emoji-id="{eid}">{fb}</tg-emoji>'


def event_announce_html(mult: float, dur: int) -> str:
    e1, e2 = _ev_e("5875483791702630534", "🎉"), _ev_e("5875065225664798722", "🔥")
    line = "━━━━━━━━━━━━━━"
    return (
        f"<b>{e1}{e2} ایونت درآمد شروع شد! {e2}{e1}\n{line}\n"
        f"{e2} ضریب درآمد: ×{mult:g}\n"
        f"{ce('jagh', 6, '⏳')} مدت: {_duration_fa(dur)}\n{line}\n"
        f"{_ev_e(SHIFT_EMOJI_START, '💼')} با «کار» کردن و {ce('jagh', 1, '🤛')} «جق» زدن تو این مدت\n"
        f"{GIFT_MONEY_CE}{GIFT_MONEY_CE} {mult:g} برابر پول می‌گیری!\n\n"
        f"{e1} بدو که وقت کمه! {e1}</b>"
    )


async def _event_announce(bot, text: str, starter_id: int):
    """اعلام شروع ایونت به همه‌ی گروه‌ها (اگه ایموجی پرمیوم قبول نشد، بدونش دوباره می‌فرسته)."""
    ok = fail = 0
    for cid in [r["chat_id"] for r in await pool.fetch("SELECT chat_id FROM chats")]:
        sent = False
        for attempt in range(3):
            try:
                try:
                    await bot.send_message(chat_id=cid, text=text, parse_mode="HTML")
                except BadRequest as e:
                    if "chat not found" in str(e).lower():
                        raise
                    await bot.send_message(chat_id=cid, text=_strip_te(text), parse_mode="HTML")
                sent = True
                break
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except (Forbidden, BadRequest) as e:
                if isinstance(e, Forbidden) or "chat not found" in str(e).lower():
                    await pool.execute("DELETE FROM chats WHERE chat_id = $1", cid)
                    KNOWN_CHATS.discard(cid)
                break
            except Exception:
                break
        ok += sent
        fail += not sent
        await asyncio.sleep(0.06)
    try:
        await bot.send_message(starter_id, f"📣 اعلام ایونت به {ok:,} گروه رفت" + (f" ({fail:,} ناموفق)." if fail else "."))
    except Exception:
        pass


async def event_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """فقط پشتیبان‌ها: «ایونت درآمد 5 5دقیقه» (ضریب، مدت) | «پایان ایونت»."""
    msg, user = update.effective_message, update.effective_user
    t = norm_word(text).translate(_DIG)
    compact = t.replace(" ", "")
    if not (compact.startswith("ایونت") or compact.startswith("پایانایونت")):
        return False
    if not has_perm(user.id, "panel"):
        return False
    if compact.startswith("پایانایونت") or compact.startswith("ایونتپایان") or compact.startswith("ایونتتمام"):
        INCOME_EVENT["end"] = 0.0
        await _event_save()
        await msg.reply_text(B(f"{EVENT_CE} ایونت درآمد تموم شد؛ همه‌چی برگشت به حالت عادی."), parse_mode="HTML")
        return True
    m = re.match(r"^ایونت\s*(?:درآمد|درامد)\s*(.*)$", t)
    usage = ("مثال: <code>ایونت درآمد 5 5دقیقه</code> (ضریب ۵ برابر برای ۵ دقیقه)\n"
             f"حداکثر ضریب {EVENT_MAX_MULT} و حداکثر مدت ۲۴ ساعت. برای تموم کردن: <code>پایان ایونت</code>")
    if not m:
        cur = event_banner().strip()
        await msg.reply_text(B((cur + "\n\n" if cur else "الان ایونتی فعال نیست.\n\n") + usage), parse_mode="HTML")
        return True
    mm = re.match(r"^[x×*]?\s*(\d+(?:\.\d+)?)\s*(?:برابر|x|×)?\s*(.+)$", m.group(1).strip())
    dur = parse_duration(mm.group(2).strip()) if mm else None
    mult = float(mm.group(1)) if mm else 0.0
    if not mm or dur is None or dur <= 0 or dur > EVENT_MAX_SECONDS or not (1 < mult <= EVENT_MAX_MULT):
        await msg.reply_text(B("ایونت رو نفهمیدم.\n" + usage), parse_mode="HTML")
        return True
    INCOME_EVENT["mult"], INCOME_EVENT["end"] = mult, time.time() + dur
    await _event_save()
    if msg.chat.type == "private":   # تو گروه خود اعلامیه جواب دستوره؛ تو پی‌وی یه تایید کوتاه میدیم
        await msg.reply_text(B(f"{EVENT_CE} ایونت ثبت شد؛ دارم به همه‌ی گروه‌ها اعلام می‌کنم…"), parse_mode="HTML")
    task = asyncio.create_task(_event_announce(context.bot, event_announce_html(mult, int(dur)), user.id))
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return True


BANK_INTEREST_RATE_PCT = 3          # درصد سود
BANK_INTEREST_EVERY = 12 * 3600     # هر چند ثانیه
BANK_INTEREST_NEXT = 0.0            # زمان سود بعدی (توسط حلقه‌ی سود ست میشه)


def bank_interest_note(bank: int | None = None) -> str:
    left = BANK_INTEREST_NEXT - time.time()
    tail = f" • سود بعدی: {_duration_fa(int(left) + 1)} دیگه" if BANK_INTEREST_NEXT and left > 0 else ""
    note = f"سود بانک: <b>{BANK_INTEREST_RATE_PCT}٪ هر ۱۲ ساعت</b> روی موجودی بانک{tail}"
    if bank is not None:
        bank = int(bank)
        profit = (bank * BANK_INTEREST_RATE_PCT) // 100   # دقیقاً مثل فرمول حلقه‌ی سود
        note += (f"\n{MONEY_CE} سود بعدی تو: <b>+{profit:,} تومان</b>"
                 f"\n{BANK_CE} موجودی بانک بعد از سود: <b>{bank + profit:,} تومان</b>")
    return note


async def bank_interest_loop():
    """هر ۱۲ ساعت ۳٪ سود روی موجودی حساب بانکی همه. زمان آخرین سود تو دیتابیس می‌مونه تا با ری‌استارت دوباره/جا نندازه."""
    global BANK_INTEREST_NEXT
    key = "bank_interest_last"
    while True:
        try:
            now = time.time()
            v = await pool.fetchval("SELECT value FROM bot_settings WHERE key = $1", key)
            last = float(v) if v else now
            if not v:
                await pool.execute("INSERT INTO bot_settings (key, value) VALUES ($1, $2) ON CONFLICT (key) DO NOTHING", key, str(last))
            periods = int((now - last) // BANK_INTEREST_EVERY)
            if periods > 0:
                async with pool.acquire() as c:
                    async with c.transaction():
                        for _ in range(min(periods, 28)):
                            await c.execute("UPDATE users SET bank_balance = bank_balance + (bank_balance * $1::bigint) / 100 WHERE bank_balance > 0", BANK_INTEREST_RATE_PCT)
                        last += periods * BANK_INTEREST_EVERY
                        await c.execute("UPDATE bot_settings SET value = $2 WHERE key = $1", key, str(last))
                log.info("bank interest applied x%s", periods)
            BANK_INTEREST_NEXT = last + BANK_INTEREST_EVERY
            await asyncio.sleep(max(5.0, min(BANK_INTEREST_NEXT - time.time() + 1, 3600)))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("bank interest loop failed")
            await asyncio.sleep(60)

_FA_DIGITS_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_MONEY_ALL_WORDS = {"همه", "همش", "همهاش", "همه‌اش", "کل", "تمام", "تمامش", "all", "max"}


def parse_money_amount(raw: str) -> int | None:
    value = raw.translate(_FA_DIGITS_MAP).strip().lower()
    value = value.replace("ي", "ی").replace("ك", "ک")
    value = value.replace(",", "").replace("٬", "").replace("،", "").replace("٫", ".").replace("/", ".")
    value = "".join(ch for ch in value if ch not in "\u200c\u200d\u200e\u200f")
    m = re.search(r"(\d{1,3}(?:\.\d{3})+|\d+(?:\.\d+)?)\s*(میلیارد|میلیون|هزار|[bmk])?", value)
    if not m:
        return None
    num, unit = m.group(1), m.group(2) or ""
    if not unit and re.fullmatch(r"\d{1,3}(?:\.\d{3})+", num):   # 5.000.000 → نقطه‌ها جداکننده‌ی هزارگانِ
        num = num.replace(".", "")
    number = float(num)
    multiplier = 1_000_000_000 if unit in ("میلیارد", "b") else 1_000_000 if unit in ("m", "میلیون") else 1_000 if unit in ("k", "هزار") else 1
    amount = int(round(number * multiplier))
    return amount if amount > 0 else None


async def show_bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await upsert_user(user.id, user.full_name)
    row = await pool.fetchrow("SELECT balance, bank_balance FROM users WHERE user_id=$1", user.id)
    pocket, bank = int(row["balance"] or 0), int(row["bank_balance"] or 0)
    text = (f"{BANK_CE} <b>بانک مرکزی شهر</b>\n\n"
            f"{ce('wallet',1,'👛')} پول نقد داخل جیب: <b>{pocket:,} تومان</b>\n"
            f"{BANK_CE} موجودی حساب بانکی: <b>{bank:,} تومان</b>\n"
            f"{MONEY_CE} مجموع دارایی: <b>{pocket+bank:,} تومان</b>\n\n"
            f"کارمزد واریز: <b>{BANK_FEE:,} تومان</b> | برداشت بدون کارمزد\n"
            f"{bank_interest_note(bank)}")
    kb=InlineKeyboardMarkup([[btn("واریز", "success", callback_data=f"bk:dep:{user.id}"), btn("برداشت", "primary", callback_data=f"bk:wd:{user.id}")]])
    await update.effective_message.reply_text(B(text), parse_mode="HTML", reply_markup=kb)

async def bank_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; _,act,uid=q.data.split(":")
    if q.from_user.id != int(uid):
        await q.answer("این بانک برای شما نیست.", show_alert=True); return
    context.user_data["bank_action"] = "واریز" if act=="dep" else "برداشت"
    await q.answer()
    await q.message.reply_text(B(f"مبلغ {context.user_data['bank_action']} رو به تومان بفرست.\nمثال: <code>100000</code> یا <code>100m</code>"),parse_mode="HTML")

async def handle_bank_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    user=update.effective_user; key=_word_key(text)
    m=re.match(r"^(واریز|واريز|برداشت)\s+(.+)$", text.strip())
    if m:
        action=m.group(1); raw_amount=m.group(2)
    elif context.user_data.get("bank_action"):
        action=context.user_data.get("bank_action"); raw_amount=text.strip()
    else:
        return False
    want_all = norm_word(raw_amount).replace(" ", "") in _MONEY_ALL_WORDS
    amount = None if want_all else parse_money_amount(raw_amount)
    if amount is None and not want_all:
        await update.effective_message.reply_text(B("مبلغ معتبر نیست. نمونه: <code>واریز 100m</code> یا <code>برداشت 100m</code>"),parse_mode="HTML"); return True
    context.user_data.pop("bank_action", None)
    await upsert_user(user.id,user.full_name)
    async with pool.acquire() as conn:
        async with conn.transaction():
            row=await conn.fetchrow("SELECT balance, bank_balance FROM users WHERE user_id=$1 FOR UPDATE",user.id)
            pocket,bank=int(row['balance']),int(row['bank_balance'])
            if want_all:
                amount = (pocket - BANK_FEE) if action in ("واریز","واريز") else bank
            if amount <= 0:
                await update.effective_message.reply_text(B("برای این کار موجودی کافی نیست."),parse_mode="HTML"); return True
            if action in ("واریز","واريز"):
                total=amount+BANK_FEE
                if pocket < total:
                    result=f"موجودی جیبت کافی نیست. مبلغ واریز به‌علاوه کارمزد: <b>{total:,}</b> تومان؛ موجودی جیب: <b>{pocket:,}</b> تومان."
                    ok=False
                else:
                    await conn.execute("UPDATE users SET balance=balance-$1, bank_balance=bank_balance+$2 WHERE user_id=$3",total,amount,user.id)
                    pocket-=total; bank+=amount; ok=True
                    result=f"{BANK_CE} <b>واریز با موفقیت انجام شد!</b>\nمبلغ واریز: <b>{amount:,} تومان</b>\nکارمزد: <b>{BANK_FEE:,} تومان</b>\nموجودی جیب: <b>{pocket:,}</b>\nموجودی بانک: <b>{bank:,}</b>"
            else:
                if bank < amount:
                    result=f"موجودی بانک کافی نیست. موجودی بانک: <b>{bank:,}</b> تومان."; ok=False
                else:
                    await conn.execute("UPDATE users SET bank_balance=bank_balance-$1, balance=balance+$1 WHERE user_id=$2",amount,user.id)
                    pocket+=amount; bank-=amount; ok=True
                    result=f"{BANK_CE} <b>برداشت با موفقیت انجام شد!</b>\nمبلغ برداشت: <b>{amount:,} تومان</b>\nکارمزد: <b>۰ تومان</b>\nموجودی جیب: <b>{pocket:,}</b>\nموجودی بانک: <b>{bank:,}</b>"
    await update.effective_message.reply_text(B(result),parse_mode="HTML")
    if ok and action in ("واریز", "واريز"):
        await task_event(context.bot, update.effective_chat.id, user.id, "bank", int(amount), name=user.full_name)
    return True

# ───────────────────────── خونه (تعمیر + وسایل + درآمد روزانه) ─────────────────────────
HOUSE_REPAIR_COST = 10_000            # هزینه‌ی تعمیر اولیه (10k)
HOUSE_REPAIR_SECONDS = 30 * 60        # تعمیر ۳۰ دقیقه طول می‌کشه
HOUSE_BASE_INCOME = 2_000_000         # درآمد روزانه‌ی خونه‌ی تعمیرشده (2m)
HOUSE_MAX_INCOME = 50_000_000         # سقف درآمد روزانه وقتی همه‌ی وسایل خریده شده (50m)
HOUSE_STORE_DAYS = 7                  # درآمد جمع‌نشده حداکثر تا چند روز ذخیره می‌شه
HOUSE_EID = "5197291967284801683"     # ایموجی خود خونه
# (کلید، ایموجی جایگزین، اسم، قیمت، آیدی ایموجی پرمیوم)
_HOUSE_ITEM_DEFS = (
    ("tv", "📺", "تلویزیون", 35_000_000, "5882223664836907064"),
    ("fridge", "🧊", "یخچال", 90_000_000, "5800648191342809303"),
    ("carpet", "🧶", "فرش", 50_000_000, "5402506758818122460"),
    ("sofa", "🛋", "مبل", 80_000_000, "5319032080850496268"),
    ("bed", "🛏", "تخت", 100_000_000, "5291884812343201811"),
    ("chandelier", "💡", "لوستر", 50_000_000, "5314562428644635117"),
    ("window", "🪟", "پنجره رو به بیرون", 150_000_000, "5233525505394751729"),
    ("wardrobe", "🚪", "کمد", 45_000_000, "5474624933369765083"),
)
_HOUSE_PRICE_SUM = sum(d[3] for d in _HOUSE_ITEM_DEFS)
# درآمد هر وسیله متناسب با قیمتش؛ جمع کل با خرید همه‌ی وسایل دقیقاً می‌شه HOUSE_MAX_INCOME
HOUSE_ITEMS = {
    k: {"emoji": e, "name": n, "price": p, "eid": eid,
        "ce": f'<tg-emoji emoji-id="{eid}">{e}</tg-emoji>',
        "income": (p * (HOUSE_MAX_INCOME - HOUSE_BASE_INCOME)) // _HOUSE_PRICE_SUM}
    for k, e, n, p, eid in _HOUSE_ITEM_DEFS
}
HOUSE_CE = f'<tg-emoji emoji-id="{HOUSE_EID}">🏠</tg-emoji>'
_HOUSE_IMG_CACHE: "OrderedDict[tuple, bytes]" = OrderedDict()
_HOUSE_BASE_IMG: Image.Image | None = None
_HOUSE_TASKS: set = set()


def _house_daily(items: set) -> int:
    return HOUSE_BASE_INCOME + sum(HOUSE_ITEMS[k]["income"] for k in items if k in HOUSE_ITEMS)


def _house_items_of(row) -> set:
    raw = (row["items"] if row else "") or ""
    return {x for x in raw.split(",") if x in HOUSE_ITEMS}


def _house_days_ready(row, now: float) -> int:
    """چند «روز کامل» از آخرین دریافت گذشته (درآمد فقط سر ساعتِ ۲۴ ساعته آماده می‌شه، نه تدریجی)."""
    if not row or now < float(row["repair_done_at"] or 0):
        return 0
    elapsed = max(0.0, now - float(row["last_claim"] or now))
    return min(int(elapsed // 86400), HOUSE_STORE_DAYS)


def _house_pending(row, now: float) -> int:
    return _house_daily(_house_items_of(row)) * _house_days_ready(row, now) if row else 0


def _house_wait(row, now: float) -> int:
    """چند ثانیه تا آماده شدن درآمد بعدی."""
    return max(0, int(float(row["last_claim"]) + 86400 - now))


def _load_house_base():
    global _HOUSE_BASE_IMG
    if _HOUSE_BASE_IMG is None:
        here = Path(__file__).resolve().parent
        for p in (here / "house_room.jpg", here / "assets" / "house_room.jpg", here / "static" / "house_room.jpg"):
            if p.exists():
                _HOUSE_BASE_IMG = Image.open(p).convert("RGB")
                break
    return _HOUSE_BASE_IMG


def _render_house(repaired: bool, items: frozenset) -> bytes | None:
    """عکس اتاق: خراب (تعمیرنشده) یا تمیز + وسایل خریداری‌شده روی همون عکس کشیده می‌شه."""
    from PIL import ImageEnhance
    base = _load_house_base()
    if base is None:
        return None
    img = base.copy()

    def tint(im, color, a):
        return Image.blend(im, Image.new("RGB", im.size, color), a)

    def glow_region(im, box, bright):
        m = Image.new("L", im.size, 0)
        ImageDraw.Draw(m).rectangle(box, fill=255)
        m = m.filter(ImageFilter.GaussianBlur(14))
        lit = tint(ImageEnhance.Brightness(im).enhance(bright), (255, 205, 150), 0.12)
        return Image.composite(lit, im, m)

    if repaired:
        soft = img.filter(ImageFilter.MedianFilter(5)).filter(ImageFilter.GaussianBlur(1.2))
        img = Image.blend(img, soft, 0.65)
        img = ImageEnhance.Brightness(img).enhance(1.35)
        img = ImageEnhance.Contrast(img).enhance(0.92)
        img = tint(img, (120, 110, 150), 0.10)
    else:
        img = ImageEnhance.Brightness(img).enhance(0.8)
    if repaired and "wardrobe" in items:
        img = glow_region(img, (150, 365, 385, 870), 1.45)
    if repaired and "bed" in items:
        img = glow_region(img, (840, 625, 1285, 945), 1.4)

    over = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    gold = (214, 170, 90)
    if repaired:
        if "window" not in items:      # تا پنجره رو نخری با تخته بسته‌ست
            for y0 in (335, 440, 545, 640):
                d.polygon([(578, y0 + 4), (738, y0 - 4), (738, y0 + 22), (578, y0 + 30)],
                          fill=(70, 46, 34, 255), outline=(36, 22, 18, 255))
        if "bed" in items:
            d.ellipse([925, 708, 1025, 734], fill=(236, 232, 248, 235))
        if "carpet" in items:
            d.polygon([(470, 930), (900, 930), (1060, 1170), (300, 1170)], fill=(120, 38, 58, 235))
            d.polygon([(500, 950), (872, 950), (1015, 1150), (345, 1150)], outline=gold + (255,), width=5)
            d.polygon([(540, 975), (845, 975), (965, 1128), (395, 1128)], outline=gold + (200,), width=3)
            for i in range(5):
                cx, cy = 560 + i * 70, 1050
                d.polygon([(cx, cy - 22), (cx + 26, cy), (cx, cy + 22), (cx - 26, cy)], fill=gold + (210,))
        if "sofa" in items:
            d.rounded_rectangle([470, 700, 800, 800], 24, fill=(46, 62, 120, 255))
            d.rounded_rectangle([455, 760, 815, 860], 26, fill=(60, 80, 150, 255))
            d.rounded_rectangle([440, 745, 500, 860], 18, fill=(40, 54, 108, 255))
            d.rounded_rectangle([770, 745, 830, 860], 18, fill=(40, 54, 108, 255))
            d.line([(635, 770), (635, 850)], fill=(36, 48, 98, 255), width=3)
            d.rectangle([470, 860, 490, 880], fill=(30, 24, 30, 255))
            d.rectangle([780, 860, 800, 880], fill=(30, 24, 30, 255))
        if "fridge" in items:
            d.rounded_rectangle([5, 520, 150, 930], 10, fill=(176, 188, 214, 255), outline=(110, 124, 160, 255), width=4)
            d.line([(5, 690), (150, 690)], fill=(110, 124, 160, 255), width=4)
            d.rounded_rectangle([122, 570, 134, 650], 5, fill=(70, 80, 110, 255))
            d.rounded_rectangle([122, 710, 134, 800], 5, fill=(70, 80, 110, 255))
            d.rectangle([14, 530, 60, 680], fill=(205, 216, 238, 120))
        if "tv" in items:
            d.rounded_rectangle([830, 300, 1110, 470], 8, fill=(14, 14, 20, 255), outline=(60, 64, 90, 255), width=5)
            d.rectangle([845, 313, 1095, 457], fill=(70, 120, 220, 255))
            d.polygon([(845, 313), (1000, 313), (900, 457), (845, 457)], fill=(120, 170, 255, 180))
            d.rectangle([955, 470, 985, 492], fill=(40, 42, 56, 255))
            d.rectangle([920, 490, 1020, 500], fill=(40, 42, 56, 255))
        if "chandelier" in items:
            cx = 650
            d.line([(cx, 0), (cx, 58)], fill=(200, 170, 100, 255), width=6)
            d.polygon([(cx - 20, 58), (cx + 20, 58), (cx + 60, 100), (cx - 60, 100)], fill=(214, 176, 96, 255))
            d.arc([cx - 100, 70, cx + 100, 150], 0, 180, fill=(214, 176, 96, 255), width=8)
            for k in (-95, -55, -15, 15, 55, 95):
                x = cx + k
                d.line([(x, 108), (x, 126)], fill=(214, 176, 96, 255), width=4)
                d.ellipse([x - 9, 124, x + 9, 148], fill=(255, 236, 170, 255))
    img = img.convert("RGBA")
    img.alpha_composite(over)
    if repaired and "chandelier" in items:
        glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
        g = ImageDraw.Draw(glow)
        for k in (-95, -55, -15, 15, 55, 95):
            g.ellipse([650 + k - 36, 120, 650 + k + 36, 180], fill=(255, 220, 140, 95))
        img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(22)))
    out = BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=88)
    return out.getvalue()


async def _house_photo(repaired: bool, items: set) -> BytesIO | None:
    key = (bool(repaired), tuple(sorted(items)))
    data = _HOUSE_IMG_CACHE.get(key)
    if data is None:
        data = await asyncio.to_thread(_render_house, bool(repaired), frozenset(items))
        if data is None:
            return None
        _HOUSE_IMG_CACHE[key] = data
        while len(_HOUSE_IMG_CACHE) > 40:
            _HOUSE_IMG_CACHE.popitem(last=False)
    else:
        _HOUSE_IMG_CACHE.move_to_end(key)
    bio = BytesIO(data)
    bio.name = "house.jpg"
    return bio


def _house_price_label(p: int) -> str:
    return f"{p // 1_000_000} میلیون" if p % 1_000_000 == 0 else f"{p:,}"


async def _house_panel(uid: int, view: str = "main"):
    """(عکس، کپشن، کیبورد). view: main = صفحه‌ی اول | up = صفحه‌ی ارتقا (خرید وسایل)."""
    now = time.time()
    row = await pool.fetchrow("SELECT repair_done_at, last_claim, stored_income, items FROM user_house WHERE user_id=$1", uid)
    bal = await pool.fetchval("SELECT balance FROM users WHERE user_id=$1", uid) or 0
    if not row:
        cap = (f"🏚 <b>خونه‌ی خراب</b>\n\nیه خونه‌ی ویرونه داری که باید تعمیرش کنی!\n"
               f"🔧 هزینه‌ی تعمیر: <b>{HOUSE_REPAIR_COST:,} تومان</b>\n⏳ مدت تعمیر: <b>۳۰ دقیقه</b>\n"
               f"{MONEY_CE} بعد از تعمیر روزانه <b>{HOUSE_BASE_INCOME:,}</b> تومان درآمد می‌ده و با ارتقا تا <b>{HOUSE_MAX_INCOME:,}</b> تومان در روز می‌ره بالا.\n\n"
               f"موجودی جیبت: <b>{bal:,}</b>")
        kb = InlineKeyboardMarkup([[btn(f"تعمیر خونه | {HOUSE_REPAIR_COST:,}", "success", icon=HOUSE_EID, callback_data=f"hm:repair:{uid}:x")]])
        return await _house_photo(False, set()), cap, kb
    if now < float(row["repair_done_at"]):
        left = int(float(row["repair_done_at"]) - now)
        m, s_ = divmod(left, 60)
        cap = (f"🔧 <b>خونه در حال تعمیره...</b>\n\n{HG_CE} زمان باقی‌مانده: <b>{m:02d}:{s_:02d}</b>\n"
               f"بعد از تعمیر می‌تونی ارتقا بدی و درآمد روزانه بگیری.")
        kb = InlineKeyboardMarkup([[btn("بروزرسانی", "primary", icon=EID_HOURGLASS, callback_data=f"hm:ref:{uid}:x")]])
        return await _house_photo(False, set()), cap, kb
    items = _house_items_of(row)
    daily = _house_daily(items)
    photo = await _house_photo(True, items)
    if view == "up":
        lines = [f"{HOUSE_CE} <b>ارتقای خونه</b>", "هر وسیله درآمد روزانه‌ی خونه رو بیشتر می‌کنه:", ""]
        buttons: list[list[InlineKeyboardButton]] = []
        pair: list[InlineKeyboardButton] = []
        for k, it in HOUSE_ITEMS.items():
            if k in items:
                lines.append(f"✅ {it['ce']} {it['name']} — +{it['income']:,}")
            else:
                lines.append(f"🔒 {it['ce']} {it['name']} — <b>{it['price']:,}</b> (+{it['income']:,}/روز)")
                pair.append(btn(f"{it['name']} | {_house_price_label(it['price'])}", "primary", icon=it["eid"], callback_data=f"hm:buy:{uid}:{k}"))
                if len(pair) == 2:
                    buttons.append(pair); pair = []
        if pair:
            buttons.append(pair)
        if len(items) == len(HOUSE_ITEMS):
            lines.append("\n🎉 همه‌ی وسایل رو خریدی؛ خونه‌ت کامله!")
        lines += ["", f"{MONEY_CE} درآمد روزانه: <b>{daily:,}</b> / سقف {HOUSE_MAX_INCOME:,}", f"موجودی جیبت: <b>{bal:,}</b>"]
        buttons.append([btn("برگشت به خونه", "primary", icon=EID_BACK, callback_data=f"hm:main:{uid}:x")])
        return photo, "\n".join(lines), InlineKeyboardMarkup(buttons)
    pending = _house_pending(row, now)
    lines = [f"{HOUSE_CE} <b>خونه‌ی شما</b> ✨ تعمیرشده", ""]
    if items:
        lines.append("وسایل خریده‌شده:")
        for k, it in HOUSE_ITEMS.items():
            if k in items:
                lines.append(f"{it['ce']} {it['name']}")
    else:
        lines.append("هنوز وسیله‌ای نخریدی؛ از «ارتقا» وسایل بخر تا درآمدت بره بالا.")
    lines += ["", f"{MONEY_CE} درآمد روزانه: <b>{daily:,}</b> / سقف {HOUSE_MAX_INCOME:,}"]
    if pending > 0:
        lines.append(f"✅ درآمد امروزت آماده‌ست: <b>{pending:,}</b> تومان")
        claim_btn = btn(f"دریافت درآمد | {pending:,}", "success", icon=EID_MONEY, callback_data=f"hm:claim:{uid}:all")
    else:
        w = _house_wait(row, now)
        lines.append(f"{HG_CE} درآمد بعدی: <b>{_duration_fa(w)}</b> دیگه آماده می‌شه")
        claim_btn = btn(f"دریافت بعدی: {_duration_fa(w)}", None, icon=EID_HOURGLASS, callback_data=f"hm:claim:{uid}:all")
    lines.append(f"موجودی جیبت: <b>{bal:,}</b>")
    kb = InlineKeyboardMarkup([
        [claim_btn],
        [btn("ارتقا", "primary", icon=EID_TROPHY, callback_data=f"hm:upg:{uid}:x"),
         btn("بروزرسانی", "primary", icon=EID_HOURGLASS, callback_data=f"hm:ref:{uid}:x")],
    ])
    return photo, "\n".join(lines), kb


async def show_homes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await upsert_user(user.id, user.full_name)
    photo, cap, kb = await _house_panel(user.id)
    msg = update.effective_message
    if photo is not None:
        try:
            await msg.reply_photo(photo=photo, caption=cap, parse_mode="HTML", reply_markup=kb)
            return
        except Exception:
            log.warning("house photo send failed", exc_info=True)
            cap = re.sub(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", r"\1", cap)
            if photo is not None:
                try:
                    photo.seek(0)
                    await msg.reply_photo(photo=photo, caption=cap, parse_mode="HTML", reply_markup=kb)
                    return
                except Exception:
                    log.warning("house photo send failed again", exc_info=True)
    await msg.reply_text(cap, parse_mode="HTML", reply_markup=kb)


async def _house_repair_notify(bot, chat_id: int, reply_to: int | None, uid: int):
    try:
        await asyncio.sleep(HOUSE_REPAIR_SECONDS + 2)
        rp = ReplyParameters(message_id=reply_to, allow_sending_without_reply=True) if reply_to else None
        await bot.send_message(chat_id, B("🏠 تعمیر خونه‌ات تموم شد! بنویس «خونه» و وسایلش رو بخر تا درآمدش بره بالا."),
                               parse_mode="HTML", reply_parameters=rp)
    except Exception:
        log.warning("house repair notify failed", exc_info=True)


async def home_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    if len(parts) != 4:
        await q.answer("درخواست نامعتبر است.", show_alert=True); return
    _, action, uid_s, key = parts
    try:
        uid = int(uid_s)
    except ValueError:
        await q.answer("درخواست نامعتبر است.", show_alert=True); return
    if q.from_user.id != uid:
        await q.answer("این پنل خونه برای شما نیست.", show_alert=True); return
    now = time.time(); message = ""; ok = False; started_repair = False; view = "main"
    async with pool.acquire() as conn:
        async with conn.transaction():
            bal = await conn.fetchval("SELECT balance FROM users WHERE user_id=$1 FOR UPDATE", uid) or 0
            row = await conn.fetchrow("SELECT * FROM user_house WHERE user_id=$1 FOR UPDATE", uid)
            repaired = bool(row) and now >= float(row["repair_done_at"])
            if action == "repair":
                if row: message = "خونه رو قبلاً تعمیر کردی."
                elif bal < HOUSE_REPAIR_COST: message = f"برای تعمیر خونه {HOUSE_REPAIR_COST:,} تومان لازم داری."
                else:
                    done = now + HOUSE_REPAIR_SECONDS
                    await conn.execute("UPDATE users SET balance=balance-$1 WHERE user_id=$2", HOUSE_REPAIR_COST, uid)
                    await conn.execute("INSERT INTO user_house(user_id,repair_done_at,last_claim) VALUES($1,$2,$2)", uid, done)
                    message = "تعمیر خونه شروع شد! ۳۰ دقیقه دیگه آماده‌ست 🔧"; ok = True; started_repair = True
            elif action == "upg":
                if repaired: view = "up"; ok = True
                else: message = "اول باید خونه تعمیر بشه."
            elif action == "buy":
                view = "up"
                it = HOUSE_ITEMS.get(key)
                if not it: message = "این وسیله وجود نداره."
                elif not row: message = "اول باید خونه رو تعمیر کنی."
                elif not repaired: message = "خونه هنوز در حال تعمیره؛ بعد از تعمیر می‌تونی وسیله بخری."
                elif key in _house_items_of(row): message = f"{it['name']} رو قبلاً خریدی."
                elif bal < it["price"]: message = f"برای خرید {it['name']} به {it['price']:,} تومان نیاز داری."
                else:
                    new_items = ",".join(sorted(_house_items_of(row) | {key}))
                    await conn.execute("UPDATE users SET balance=balance-$1 WHERE user_id=$2", it["price"], uid)
                    await conn.execute("UPDATE user_house SET items=$2 WHERE user_id=$1", uid, new_items)
                    message = f"{it['name']} خریداری شد! درآمد روزانه‌ات +{it['income']:,} شد ✨"; ok = True
            elif action == "claim":
                if not repaired: message = "خونه هنوز تعمیر نشده."
                else:
                    payout = _house_pending(row, now)
                    if payout > 0:
                        await conn.execute("UPDATE users SET balance=balance+$1 WHERE user_id=$2", payout, uid)
                        await conn.execute("UPDATE user_house SET last_claim=$2 WHERE user_id=$1", uid, now)
                        message = f"+{payout:,} تومان درآمد خونه دریافت شد!"; ok = True
                    else:
                        message = f"درآمد روزانه هنوز آماده نشده؛ {_duration_fa(_house_wait(row, now))} دیگه مونده."
            elif action in ("ref", "main"):
                ok = True
            else:
                message = "درخواست نامعتبر است."
    if action == "ref":
        view = "up" if "ارتقای خونه" in (q.message.caption or "") else "main"
    await q.answer(message[:180] if message else None, show_alert=bool(message) and not ok)
    photo, cap, kb = await _house_panel(uid, view)
    try:
        if photo is not None:
            await q.edit_message_media(InputMediaPhoto(photo, caption=cap, parse_mode="HTML"), reply_markup=kb)
        else:
            await q.edit_message_text(cap, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        if "not modified" not in str(e).lower():
            log.warning("house panel update failed, resending", exc_info=True)
            try: await q.message.delete()
            except Exception: pass
            photo2, cap2, kb2 = await _house_panel(uid, view)
            cap2 = re.sub(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", r"\1", cap2)
            if photo2 is not None:
                await context.bot.send_photo(q.message.chat_id, photo=photo2, caption=cap2, parse_mode="HTML", reply_markup=kb2)
            else:
                await context.bot.send_message(q.message.chat_id, cap2, parse_mode="HTML", reply_markup=kb2)
    if started_repair:
        t = asyncio.create_task(_house_repair_notify(context.bot, q.message.chat_id, q.message.message_id, uid))
        _HOUSE_TASKS.add(t); t.add_done_callback(_HOUSE_TASKS.discard)


# ───────────────────────── پنل یکپارچه «خودم اینارو ببین» ─────────────────────────
HAND_UPGRADES = [
    (500_000, 5),
    (1_500_000, 5),
    (3_000_000, 5),
    (6_000_000, 5),
    (12_000_000, 5),
]
HAND_CE = f'<tg-emoji emoji-id="{EID_HAND}">🤛</tg-emoji>'
HAND_DONE_CE = f'<tg-emoji emoji-id="{EID_HAND_DONE}">⚡</tg-emoji>'


CARS_BTN_ICON = "5323272789824654613"


# تو پی‌وی ربات فقط این بخش‌های پنل «خودم» کار می‌کنن؛ بقیه (ماشین، کازینو، خلاف، دست، خونه، ...) باید تو گروه انجام بشن.
PM_ALLOWED_PANEL_ACTIONS = {"home", "settings", "gender", "guide", "guide2", "transfer", "bank"}
PM_GROUP_ONLY_TEXT = "این بخش فقط تو گروه کار می‌کنه؛ برو تو گروه و همونجا انجامش بده. تو پی‌وی فقط تنظیمات و بانک فعاله."


def self_panel_keyboard(uid: int, private: bool = False) -> InlineKeyboardMarkup:
    if private:
        return InlineKeyboardMarkup([[btn("⚙️ تنظیمات", None, callback_data=f"sp:settings:{uid}"), btn("بانک", "primary", icon=BANK_ICON_ID, callback_data=f"sp:bank:{uid}")]])
    return InlineKeyboardMarkup([
        [btn("ماشینم", "primary", icon=CARS_BTN_ICON, callback_data=f"sp:cars:{uid}"), btn("کازینو", "primary", icon=DICE_EID, callback_data=f"sp:casino:{uid}")],
        [btn("خلاف", "danger", icon=EID_SWORDS, callback_data=f"sp:robbery:{uid}"), btn("قدرت‌ها", "success", icon=POWERS_EID, callback_data=f"sp:powers:{uid}")],
        [btn("⚙️ تنظیمات", None, callback_data=f"sp:settings:{uid}"), btn("بانک", "primary", icon=BANK_ICON_ID, callback_data=f"sp:bank:{uid}")],
    ])


def settings_keyboard(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("🧔👩 تغییر جنسیت", "primary", callback_data=f"sp:gender:{uid}")],
        [btn("📖 راهنما", "success", callback_data=f"sp:guide:{uid}")],
        [btn("🔁 انتقال اکانت با آیدی عددی", "danger", callback_data=f"sp:transfer:{uid}")],
        [btn("🔙 برگشت به پنل شخصی", "primary", icon=EID_BACK, callback_data=f"sp:home:{uid}")],
    ])


async def self_panel_text(uid: int, name: str = "") -> str:
    row = await get_user(uid)
    total = int(row["balance"] or 0) + int(row["bank_balance"] or 0)
    level = int(row.get("hand_level", 0) or 0)
    cooldown = max(280, COOLDOWN_SECONDS - level * 5)
    remaining = cooldown - (time.time() - float(row["last_ts"] or 0))
    jagh_t = "آماده‌ست ✅" if remaining <= 0 else f"{_duration_fa(int(remaining) + 1)} مونده"
    g = row["gender"]
    if g in JOBS:
        job = current_job(g, int(row["jaghs"] or 0), int(row["work_count"] or 0))
        lvl = JOBS[g][job[0]][1] if job else level_for(int(row["jaghs"] or 0))
        char_name = CHARACTERS[g][lvl - 1][0]
        job_name = job[1] if job else "بیکار"
    else:
        char_name, job_name = "جنسیت انتخاب نشده (تنظیمات ← تغییر جنسیت)", "—"
    return B(
        f"{ce('me', 1, '🎖')} {html.escape(name or row['name'] or 'کاربر')}\n\n"
        f"کاراکتر: {html.escape(char_name)}\n"
        f"💼 شغل: {html.escape(job_name)}\n"
        f"{MONEY_CE} دارایی کل: {total:,} تومان\n"
        f"{HG_CE} زمان جق: {jagh_t} (هر {_duration_fa(cooldown)})\n"
        f"{jm_status_line(row.get('jagh_machine', 0))}\n\n"
        f"{energy_line(row.get('energy', 0))}\n\n"
        "یکی از بخش‌ها رو انتخاب کن:"
    )


async def send_self_panel(bot, chat_id: int, uid: int, name: str = "", reply_to: int | None = None):
    """پنل شخصی: عکس کاراکتر + کپشن + دکمه‌ها تو یه پیام."""
    row = await get_user(uid)
    text = await self_panel_text(uid, name)
    kb = self_panel_keyboard(uid, private=chat_id > 0)
    if row and row["gender"] in JOBS:
        await send_card(bot, chat_id, row, reply_to=reply_to, text=text, reply_markup=kb,
                        img_balance=int(row["balance"] or 0) + int(row["bank_balance"] or 0))
        return
    rp = ReplyParameters(message_id=reply_to, allow_sending_without_reply=True) if reply_to else None
    await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=kb, reply_parameters=rp)


async def show_self_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, msg = update.effective_user, update.effective_message
    await upsert_user(user.id, user.full_name)
    await send_self_panel(context.bot, msg.chat_id, user.id, user.full_name, reply_to=msg.message_id)


async def _sp_edit_text(q, text: str, parse_mode: str | None = "HTML", reply_markup=None):
    """ادیت پیام پنل؛ اگه پیام عکس‌دار باشه (کارت کاراکتر) پاک میشه و پیام متنی جدید میاد."""
    m = q.message
    if m is not None and getattr(m, "photo", None):
        try:
            await m.delete()
        except Exception:
            pass
        await q.get_bot().send_message(m.chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup)
        return
    await q.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup)


async def self_panel_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; parts = q.data.split(":")
    if len(parts) != 3:
        await q.answer("درخواست نامعتبره.", show_alert=True); return
    action, uid_s = parts[1], parts[2]
    try: uid = int(uid_s)
    except ValueError:
        await q.answer("درخواست نامعتبره.", show_alert=True); return
    if q.from_user.id != uid:
        await q.answer("این پنل برای شما نیست.", show_alert=True); return
    if action == "casino" and not CASINO_OPEN:
        await q.answer(CASINO_CLOSED_TEXT, show_alert=True); return
    if q.message.chat.type == "private" and action not in PM_ALLOWED_PANEL_ACTIONS:
        await q.answer(PM_GROUP_ONLY_TEXT, show_alert=True); return
    if action == "transfer" and q.message.chat.type != "private":
        await q.answer("انتقال اکانت فقط تو پی‌وی ربات ممکنه؛ بیا پی‌وی و بنویس «خودم».", show_alert=True); return
    await q.answer()
    back = [[btn("🔙 برگشت به پنل شخصی", "primary", icon=EID_BACK, callback_data=f"sp:home:{uid}")]]
    back_settings = [[btn("🔙 برگشت به تنظیمات", "primary", icon=EID_BACK, callback_data=f"sp:settings:{uid}")]]
    if action != "transfer":
        context.user_data.pop("acc_transfer", None)
    if action == "home":
        text = await self_panel_text(uid, q.from_user.full_name)
        kb = self_panel_keyboard(uid, private=q.message.chat.type == "private")
        if getattr(q.message, "photo", None):
            try:
                await q.edit_message_caption(caption=text, parse_mode="HTML", reply_markup=kb)
            except BadRequest as e:
                if "not modified" not in str(e).lower():
                    raise
        else:
            try:
                await q.message.delete()
            except Exception:
                pass
            await send_self_panel(context.bot, q.message.chat_id, uid, q.from_user.full_name)
        return
    if action == "bank":
        row = await get_user(uid); pocket=int(row['balance'] or 0); bank=int(row['bank_balance'] or 0)
        text=(f"{BANK_CE} <b>بانک مرکزی</b>\n\nموجودی بانک: <b>{bank:,} تومان</b>\nپول جیب: <b>{pocket:,} تومان</b>\nمجموع دارایی: <b>{bank+pocket:,} تومان</b>\n\n"
              f"واریز: <code>واریز 100m</code>\nبرداشت: <code>برداشت 100m</code>\nکارمزد هر واریز: <b>۵٬۰۰۰ تومان</b>؛ برداشت بدون کارمزد.\n{bank_interest_note(bank)}")
        await _sp_edit_text(q, B(text), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(back)); return
    if action == "robbery":
        text, kb = rob_view(uid, await _rob_profile(uid), time.time())
        await _sp_edit_text(q, B(text), parse_mode="HTML", reply_markup=kb); return
    if action == "cars":
        owned=await _owned_cars(uid)
        car_rows=[]
        if not owned: text="🚘 <b>گاراژ من</b>\n\nهنوز ماشینی نخریدی. برای خرید و دیدن ماشین‌ها از دستور «ماشین» استفاده کن."
        else:
            lines=["🚘 <b>ماشین‌های من</b>"]
            for idx,car in owned.items():
                title=CARS[idx][0] if idx < len(CARS) else f"ماشین {idx+1}"
                lines.append(f"• {html.escape(title)}")
                car_rows.append([btn(f"مشاهده و ارتقای {title}", "primary", icon=EID_TROPHY, callback_data=f"car:upmenu:{uid}:{idx}:0")])
            text="\n".join(lines)
        await _sp_edit_text(q, B(text),parse_mode="HTML",reply_markup=InlineKeyboardMarkup(car_rows+back)); return
    back_powers = [[btn("🔙 برگشت به قدرت‌ها", "primary", icon=EID_BACK, callback_data=f"sp:powers:{uid}")]]
    if action == "powers":
        rows = [[btn("جاسوسی", "primary", icon=SPY_EID, callback_data=f"sp:spy:{uid}"), btn("دزدی", "danger", icon=THEFT_EID, callback_data=f"sp:theft:{uid}")],
                [btn("دست", "success", icon=EID_HAND, callback_data=f"sp:hand:{uid}")]] + back
        await _sp_edit_text(q, B(f"{POWERS_CE} قدرت‌ها\n\nیکی از قدرت‌هات رو انتخاب کن:"), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows)); return
    if action == "spy":
        row = await get_user(uid)
        owned = bool(row["spy_owned"])
        used = int(row["spy_uses"] or 0) if row["spy_day"] == _spy_day_key() else 0
        st = f"خریداری شده ✅\nامروز {used} از {SPY_DAILY_LIMIT} بار استفاده کردی." if owned else f"هنوز نخریدی ❌\nقیمت: {SPY_PRICE:,} تومن — بنویس <code>خرید جاسوسی</code>"
        text = (f"{SPY_CE} <b>جاسوسی</b>\n\n{st}\n\nروی پیام یه کاربر ریپلای کن و بنویس <code>جاسوسی</code> تا پول، بانک، شغل، وضعیت تأهل و فعالیت‌هاش رو ببینی.")
        await _sp_edit_text(q, B(text), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(back_powers)); return
    if action == "theft":
        row = await get_user(uid)
        left = THEFT_COOLDOWN - (time.time() - float(row["last_steal"] or 0))
        st = "آماده‌ست ✅" if left <= 0 else f"{_duration_fa(int(left) + 1)} مونده"
        text = (f"{THEFT_CE} <b>دزدی</b>\n\nوضعیت: {st}\n\nروی پیام یه کاربر ریپلای کن و بنویس <code>دزدی</code> تا بین {THEFT_MIN:,} تا {THEFT_MAX:,} تومن از جیبش برداری. "
                f"پولی که تو بانکه امنه. هر {_duration_fa(THEFT_COOLDOWN)} یه بار می‌تونی استفاده کنی.")
        await _sp_edit_text(q, B(text), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(back_powers)); return
    if action == "hand":
        row=await get_user(uid); level=int(row.get('hand_level',0) or 0); cooldown=COOLDOWN_SECONDS-level*5
        lines=[f"{HAND_CE} <b>ارتقای دست</b>","\nبا هر ارتقا، ۵ ثانیه از زمان انتظار جق کم می‌شه.",f"سطح فعلی: <b>{level}/5</b>",f"زمان فعلی: <b>{_duration_fa(cooldown)}</b>","\n<b>فروشگاه ارتقاها</b>"]
        rows=[]
        for i,(cost,reduce) in enumerate(HAND_UPGRADES,1):
            status="✅ خریداری شده" if level>=i else (f"{cost:,} تومان" if level==i-1 else "🔒 ابتدا سطح قبلی")
            lines.append(f"{i}. ارتقای {i}: <b>{status}</b>")
            if level==i-1: rows.append([btn(f"ارتقای {i} • {cost:,} تومان", "success", icon=EID_HAND, callback_data=f"sp:upgrade:{uid}:{i}")])
        rows += back_powers
        lines.append(f"\n{HAND_DONE_CE} زمان نهایی پس از ۵ ارتقا: <b>۴ دقیقه و ۴۰ ثانیه</b>")
        await _sp_edit_text(q, B("\n".join(lines)),parse_mode="HTML",reply_markup=InlineKeyboardMarkup(rows)); return
    if action == "casino":
        text, kb = panel_main(uid)
        await _sp_edit_text(q, text, reply_markup=InlineKeyboardMarkup(list(kb.inline_keyboard) + back)); return
    if action == "settings":
        await _sp_edit_text(q, B("⚙️ تنظیمات\n\nیکی از گزینه‌ها رو انتخاب کن:"), parse_mode="HTML", reply_markup=settings_keyboard(uid)); return
    if action == "gender":
        rows = list(gender_keyboard(uid).inline_keyboard) + back_settings
        await _sp_edit_text(q, BP("جنسیتت رو انتخاب کن تا کاراکترت عوض بشه 👇"), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows)); return
    if action in ("guide", "guide2"):
        pg = 1 if action == "guide2" else 0
        kb = guide_kb(pg, f"sp:settings:{uid}", f"sp:guide2:{uid}", f"sp:guide:{uid}")
        await _sp_edit_text(q, guide_text(pg), parse_mode="HTML", reply_markup=kb); return
    if action == "transfer":
        context.user_data["acc_transfer"] = {"ts": time.time()}
        await _sp_edit_text(q, 
            B("🔁 انتقال اکانت\n\nآیدی عددی اکانت مقصد رو بفرست.\n\n"
              "• اکانت مقصد باید قبلاً ربات رو استارت کرده باشه و کاملاً تازه و خالی باشه.\n"
              "• همه‌ی پول، بانک، جق‌ها، ماشین‌ها، خونه‌ها و سطح دست به اون اکانت منتقل میشه و اکانت فعلی خالی میشه.\n"
              "• اکانت مقصد هم باید انتقال رو تایید کنه.\n\n"
              "برای لغو بنویس «لغو»."),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[btn("❌ لغو", "danger", callback_data=f"sp:settings:{uid}")]]),
        ); return
    await q.answer("این بخش پیدا نشد.", show_alert=True)


# ───────────── انتقال اکانت با آیدی عددی ─────────────
TRANSFER_PENDING: dict[tuple[int, int], float] = {}   # (مبدا, مقصد) -> زمان درخواست
TRANSFER_TTL = 600                                    # اعتبار درخواست: ۱۰ دقیقه
_DIGITS_TR = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


async def _transfer_target_error(src: int, dst: int) -> str | None:
    """None یعنی مقصد معتبره؛ وگرنه متن خطا."""
    if dst == src:
        return "نمی‌تونی اکانت رو به خودش منتقل کنی."
    if src in ADMIN_IDS or dst in ADMIN_IDS:
        return "انتقال از/به اکانت پشتیبان‌ها ممکن نیست."
    until = BANNED.get(dst)
    if until and until > time.time():
        return "اکانت مقصد محروم شده و نمی‌تونه انتقال بگیره."
    d = await get_user(dst)
    if not d or not d["started"]:
        return "این آیدی پیدا نشد یا هنوز ربات رو استارت نکرده. از اکانت مقصد تو پی‌وی ربات /start بزن و دوباره امتحان کن."
    if any(int(d[c] or 0) for c in ("balance", "bank_balance", "jaghs", "hand_level", "work_count")):
        return "اکانت مقصد باید تازه و خالی باشه (پول، جق، دست یا شغلش صفر نیست)."
    extra = await pool.fetchval("SELECT (SELECT count(*) FROM cars WHERE user_id=$1) + (SELECT count(*) FROM user_house WHERE user_id=$1)", dst)
    if extra:
        return "اکانت مقصد باید تازه و خالی باشه (ماشین یا خونه داره)."
    return None


async def account_transfer_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """تو پی‌وی: بعد از زدن «انتقال اکانت»، آیدی عددی مقصد رو می‌گیره."""
    msg, user = update.effective_message, update.effective_user
    st = context.user_data.get("acc_transfer")
    if not st or not msg or not msg.text:
        return False
    if time.time() - st.get("ts", 0) > TRANSFER_TTL:
        context.user_data.pop("acc_transfer", None)
        return False
    text = msg.text.strip()
    if _word_key(text) in ("لغو", "انصراف"):
        context.user_data.pop("acc_transfer", None)
        await msg.reply_text("❌ انتقال اکانت لغو شد.")
        return True
    digits = text.translate(_DIGITS_TR)
    if not digits.isdigit() or len(digits) > 15:
        await msg.reply_text("فقط آیدی عددی رو بفرست (مثلا 123456789) یا بنویس «لغو».")
        return True
    dst = int(digits)
    err = await _transfer_target_error(user.id, dst)
    if err:
        await msg.reply_text(f"❌ {err}")
        return True
    context.user_data.pop("acc_transfer", None)
    d = await get_user(dst)
    kb = InlineKeyboardMarkup([[
        btn("✅ تایید و ارسال درخواست", "success", callback_data=f"at:go:{user.id}:{dst}"),
        btn("❌ لغو", "danger", callback_data=f"at:no:{user.id}:{dst}"),
    ]])
    await msg.reply_text(
        B(f"⚠️ مطمئنی؟\n\nهمه‌ی اطلاعات اکانت فعلیت (پول، بانک، جق‌ها، ماشین‌ها، خونه‌ها، سطح دست و ...) به اکانت "
          f"{html.escape(d['name'] or 'کاربر')} ({dst}) منتقل میشه و اکانت فعلیت خالی میشه.\n"
          "این کار برگشت‌پذیر نیست و اکانت مقصد هم باید تایید کنه."),
        parse_mode="HTML", reply_markup=kb,
    )
    return True


async def _do_account_transfer(src: int, dst: int) -> str | None:
    """انتقال واقعی تو یه تراکنش. None = موفق، وگرنه متن خطا."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch("SELECT * FROM users WHERE user_id = ANY($1::bigint[]) ORDER BY user_id FOR UPDATE", [src, dst])
            by = {r["user_id"]: r for r in rows}
            s_row, d_row = by.get(src), by.get(dst)
            if not s_row or not d_row or not d_row["started"]:
                return "یکی از اکانت‌ها تو دیتابیس پیدا نشد."
            if any(int(d_row[c] or 0) for c in ("balance", "bank_balance", "jaghs", "hand_level", "work_count")):
                return "اکانت مقصد دیگه خالی نیست."
            if await conn.fetchval("SELECT (SELECT count(*) FROM cars WHERE user_id=$1) + (SELECT count(*) FROM user_house WHERE user_id=$1)", dst):
                return "اکانت مقصد دیگه خالی نیست."
            await conn.execute(
                "UPDATE users SET balance=$2, bank_balance=$3, jaghs=$4, hand_level=$5, work_count=$6, gender=COALESCE($7, gender) WHERE user_id=$1",
                dst, int(s_row["balance"] or 0), int(s_row["bank_balance"] or 0), int(s_row["jaghs"] or 0),
                int(s_row["hand_level"] or 0), int(s_row["work_count"] or 0), s_row["gender"],
            )
            await conn.execute("UPDATE users SET balance=0, bank_balance=0, jaghs=0, hand_level=0, work_count=0, gender=NULL WHERE user_id=$1", src)
            await conn.execute("UPDATE cars SET user_id=$2 WHERE user_id=$1", src, dst)
            await conn.execute("UPDATE user_house SET user_id=$2 WHERE user_id=$1", src, dst)
            # وضعیت خلاف (بازداشت/ردگیری/کول‌داون) هم همراه اکانت میره تا انتقال راه فرار نباشه
            await conn.execute("DELETE FROM robbery_state WHERE user_id=$1", dst)
            await conn.execute("UPDATE robbery_state SET user_id=$2 WHERE user_id=$1", src, dst)
            await conn.execute("DELETE FROM robbery_profile WHERE user_id=$1", dst)
            await conn.execute("UPDATE robbery_profile SET user_id=$2 WHERE user_id=$1", src, dst)
            await conn.execute("INSERT INTO robbery_profile(user_id) VALUES($1) ON CONFLICT DO NOTHING", src)
            await conn.execute(
                "INSERT INTO task_completions(task_id, user_id, period_key, completed) "
                "SELECT task_id, $2, period_key, completed FROM task_completions WHERE user_id=$1 ON CONFLICT DO NOTHING", src, dst)
            await conn.execute("UPDATE casino_records SET user_id=$2, name=$3 WHERE user_id=$1", src, dst, d_row["name"])
    log.info("account transfer %s -> %s", src, dst)
    return None


async def account_transfer_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, act, src_s, dst_s = q.data.split(":")
    src, dst, me = int(src_s), int(dst_s), q.from_user.id
    key = (src, dst)

    async def tell(uid: int, text: str):
        try:
            await context.bot.send_message(uid, text)
        except Exception:
            pass

    if act == "go":  # مبدا تایید کرد → درخواست برای مقصد فرستاده میشه
        if me != src:
            await q.answer("این دکمه مال تو نیست 😅", show_alert=True); return
        err = await _transfer_target_error(src, dst)
        if err:
            await q.answer(); await q.edit_message_text(f"❌ {err}"); return
        s_row = await get_user(src)
        kb = InlineKeyboardMarkup([[
            btn("✅ قبول می‌کنم", "success", callback_data=f"at:ok:{src}:{dst}"),
            btn("❌ رد", "danger", callback_data=f"at:no:{src}:{dst}"),
        ]])
        try:
            await context.bot.send_message(
                dst,
                B(f"🔁 درخواست انتقال اکانت\n\n{html.escape(s_row['name'] or 'کاربر')} ({src}) می‌خواد همه‌ی اطلاعات اکانتش (پول، بانک، جق‌ها، ماشین‌ها، خونه‌ها، سطح دست) "
                  "رو به این اکانت منتقل کنه.\nاگه خودت درخواست دادی قبول کن؛ وگرنه رد کن.\n(این درخواست ۱۰ دقیقه اعتبار داره)"),
                parse_mode="HTML", reply_markup=kb,
            )
        except Exception:
            await q.answer(); await q.edit_message_text("❌ نتونستم به اکانت مقصد پیام بدم. باید ربات رو استارت کرده و بلاک نکرده باشه."); return
        TRANSFER_PENDING[key] = time.time()
        await q.answer()
        await q.edit_message_text("✅ درخواست برای اکانت مقصد فرستاده شد. منتظر تاییدش باش (۱۰ دقیقه اعتبار داره).")
        return

    if act == "no":  # مبدا یا مقصد لغو/رد کرد
        if me not in (src, dst):
            await q.answer("این دکمه مال تو نیست 😅", show_alert=True); return
        TRANSFER_PENDING.pop(key, None)
        await q.answer()
        await q.edit_message_text("❌ انتقال اکانت لغو شد.")
        if me == dst:
            await tell(src, "❌ اکانت مقصد درخواست انتقال رو رد کرد.")
        return

    if act == "ok":  # مقصد قبول کرد → انتقال
        if me != dst:
            await q.answer("این دکمه مال تو نیست 😅", show_alert=True); return
        ts = TRANSFER_PENDING.pop(key, None)
        if ts is None or time.time() - ts > TRANSFER_TTL:
            await q.answer(); await q.edit_message_text("⌛ این درخواست منقضی شده؛ از اکانت مبدا دوباره درخواست بده."); return
        if src in BANNED and BANNED[src] > time.time():
            await q.answer(); await q.edit_message_text("❌ اکانت مبدا محروم شده و انتقال انجام نشد."); return
        err = await _transfer_target_error(src, dst)
        if err:
            await q.answer(); await q.edit_message_text(f"❌ {err}"); return
        err = await _do_account_transfer(src, dst)
        await q.answer()
        if err:
            await q.edit_message_text(f"❌ {err}"); return
        await q.edit_message_text("✅ انتقال اکانت انجام شد! برای دیدن دارایی‌هات بنویس «خودم».")
        await tell(src, "✅ انتقال اکانت انجام شد و اطلاعات اکانتت به اکانت مقصد رفت.")
        return


async def hand_upgrade_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; parts=q.data.split(":")
    if len(parts)!=4:
        await q.answer("درخواست نامعتبره.",show_alert=True); return
    try: uid=int(parts[2]); target=int(parts[3])
    except ValueError:
        await q.answer("درخواست نامعتبره.",show_alert=True); return
    if q.from_user.id!=uid:
        await q.answer("این ارتقا برای شما نیست.",show_alert=True); return
    if not 1<=target<=len(HAND_UPGRADES):
        await q.answer("سطح ارتقا نامعتبره.",show_alert=True); return
    cost,_=HAND_UPGRADES[target-1]
    async with pool.acquire() as conn:
        async with conn.transaction():
            row=await conn.fetchrow("SELECT balance,hand_level FROM users WHERE user_id=$1 FOR UPDATE",uid)
            level=int(row['hand_level'] or 0)
            if target!=level+1:
                await q.answer("اول باید ارتقای قبلی رو بخری.",show_alert=True); return
            if int(row['balance'])<cost:
                await q.answer(f"موجودی جیبت کافی نیست؛ {cost:,} تومان نیاز داری.",show_alert=True); return
            await conn.execute("UPDATE users SET balance=balance-$1, hand_level=hand_level+1 WHERE user_id=$2",cost,uid)
    await q.answer("ارتقای دست با موفقیت خریداری شد!",show_alert=True)
    row=await get_user(uid); level=int(row.get('hand_level',0) or 0); cooldown=COOLDOWN_SECONDS-level*5
    lines=[f"{HAND_CE} <b>ارتقای دست</b>", "\nبا هر ارتقا، ۵ ثانیه از زمان انتظار جق کم می‌شه.", f"سطح فعلی: <b>{level}/5</b>", f"زمان فعلی: <b>{_duration_fa(cooldown)}</b>", "\n<b>فروشگاه ارتقاها</b>"]
    rows=[]
    for i,(price,reduce) in enumerate(HAND_UPGRADES,1):
        status="✅ خریداری شده" if level>=i else (f"{price:,} تومان" if level==i-1 else "🔒 ابتدا سطح قبلی")
        lines.append(f"{i}. ارتقای {i}: <b>{status}</b>")
        if level==i-1: rows.append([btn(f"ارتقای {i} • {price:,} تومان", "success", icon=EID_HAND, callback_data=f"sp:upgrade:{uid}:{i}")])
    rows.append([btn("🔙 برگشت به پنل شخصی", "primary", icon=EID_BACK, callback_data=f"sp:home:{uid}")])
    lines.append(f"\n{HAND_DONE_CE} زمان نهایی پس از ۵ ارتقا: <b>۴ دقیقه و ۴۰ ثانیه</b>")
    await q.edit_message_text(B("\n".join(lines)),parse_mode="HTML",reply_markup=InlineKeyboardMarkup(rows))


async def guide_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query
    if q.data=="guide:back":
        await q.answer(); bot_username=(await context.bot.get_me()).username or ""; await q.edit_message_text(welcome_ok(),parse_mode="HTML",reply_markup=welcome_keyboard(bot_username))
    else:
        await guide_cb(update,context)


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
    bal = int(row["balance"] or 0) if row else 0
    bank = int(row["bank_balance"] or 0) if row else 0
    await update.effective_message.reply_text(
        B(f"{ce('wallet', 1, '👛')} پول داخل جیب: {bal:,} تومان\n{BANK_CE} پول داخل بانک: {bank:,} تومان\n{MONEY_CE} مجموع دارایی: {bal+bank:,} تومان"), parse_mode="HTML",
        reply_parameters=ReplyParameters(message_id=update.effective_message.message_id, allow_sending_without_reply=True),
    )


def _top_lines(rows, col: str, unit: str, show_ids: bool = False) -> str:
    out = []
    for i, r in enumerate(rows, 1):
        name = html.escape((r["name"] or "کاربر")[:30])
        mark = ce("top", 1, "👑") if i == 1 else f"{i}."
        out.append(
            f'{mark} <tg-emoji emoji-id="{TOP_NAME_EMOJI}">⭐</tg-emoji> '
            f'<a href="tg://user?id={r["user_id"]}">‏{name}‏</a> — {r[col]:,} {unit}'
            + (f'\n🆔 <code>{r["user_id"]}</code>' + (f' | @{html.escape(r["username"])}' if r["username"] else "") if show_ids else "")
        )
    return "\n\n".join(out)  # بین هر نفر یه خط فاصله


async def top(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/top یا «لیدربرد» / «برترین» / «برترین ها» — ۱۰ نفر اول جق و دارایی."""
    by_j = await pool.fetch("SELECT user_id, name, username, jaghs FROM users WHERE jaghs > 0 AND user_id <> ALL($1::bigint[]) ORDER BY jaghs DESC LIMIT 10", list(ADMIN_IDS))
    by_b = await pool.fetch("SELECT user_id, name, username, balance FROM users WHERE balance > 0 AND user_id <> ALL($1::bigint[]) ORDER BY balance DESC LIMIT 10", list(ADMIN_IDS))
    if not by_j and not by_b:
        await update.effective_message.reply_text(BP("هنوز کسی تو لیست نیست!"), parse_mode="HTML")
        return
    # آیدی عددی و یوزرنیم فقط برای پشتیبان‌ها و فقط تو پی‌وی ربات نشون داده میشه (تو گروه همه می‌بینن)
    ids = update.effective_user.id in ADMIN_IDS and update.effective_chat.type == "private"
    text = (
        f"{ce('top', 1, '👑')} <b>برترین‌ها</b>\n\n"
        f"{ce('jagh', 1, '🤛')} <b>بیشترین جق</b>\n\n{_top_lines(by_j, 'jaghs', 'جق', ids)}\n\n"
        f"〰️〰️〰️\n\n"
        f"{ce('wallet', 1, '💰')} <b>بیشترین دارایی</b>\n\n{_top_lines(by_b, 'balance', 'تومن', ids)}"
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


TRANSFER_DAILY_LIMIT = 200_000_000   # سقف انتقال پول به بقیه، برای هر کاربر در روز (صاحب‌های ربات معاف‌ان)


async def transfer_used_today(uid: int) -> int:
    return int(await pool.fetchval("SELECT total FROM transfer_daily WHERE user_id=$1 AND day=$2", uid, task_period_key("daily")) or 0)


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
    if user.id not in OWNER_IDS:
        used = await transfer_used_today(user.id)
        if used + amount > TRANSFER_DAILY_LIMIT:
            left = max(TRANSFER_DAILY_LIMIT - used, 0)
            await msg.reply_text(B(f"سقف انتقال پول در روز {TRANSFER_DAILY_LIMIT:,} تومنه. امروز {used:,} تومن منتقل کردی و فقط {left:,} تومن دیگه می‌تونی بفرستی."), parse_mode="HTML")
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


async def notify_primary_support(context, sender_id: int, recipient_id: int, amount: int, source: str):
    if sender_id not in STAFF:
        return
    sender = await pool.fetchval("SELECT name FROM users WHERE user_id=$1", sender_id) or str(sender_id)
    recipient = await pool.fetchval("SELECT name FROM users WHERE user_id=$1", recipient_id) or str(recipient_id)
    note = (f"🔔 <b>گزارش انتقال توسط پشتیبان</b>\nپشتیبان: {_ulink(sender_id,sender)}\n"
            f"مقصد: {_ulink(recipient_id,recipient)}\nمبلغ: <b>{amount:,} تومان</b>\nروش: {html.escape(source)}")
    for owner_id in OWNER_IDS:
        try: await context.bot.send_message(owner_id, note, parse_mode="HTML")
        except Exception: log.warning("could not notify owner %s about support transfer", owner_id)


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
                if frm not in OWNER_IDS:
                    got = None
                    if amt <= TRANSFER_DAILY_LIMIT:
                        got = await c.fetchval(
                            "INSERT INTO transfer_daily (user_id, day, total) VALUES ($1::bigint, $2, $3::bigint) "
                            "ON CONFLICT (user_id, day) DO UPDATE SET total = transfer_daily.total + EXCLUDED.total "
                            "WHERE transfer_daily.total + EXCLUDED.total <= $4::bigint RETURNING total",
                            frm, task_period_key("daily"), amt, TRANSFER_DAILY_LIMIT,
                        )
                    if got is None:
                        raise OverflowError("daily limit")
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
    except OverflowError:
        await q.answer("سقف انتقال روزانه پر شده.", show_alert=True)
        await q.edit_message_text(f"❌ سقف انتقال پول در روز {TRANSFER_DAILY_LIMIT:,} تومنه؛ این انتقال انجام نشد.")
        return
    except Exception:
        done.discard(key)
        log.exception("transfer failed")
        await q.answer("خطا، دوباره امتحان کن.", show_alert=True)
        return
    await q.answer("انجام شد ✅")
    await notify_primary_support(context, frm, to, amt, "انتقال پول بین کاربران")
    tname = await pool.fetchval("SELECT name FROM users WHERE user_id = $1", to) or "کاربر"
    await q.edit_message_text(
        f'✅ <b>{amt:,} تومن</b> از <a href="tg://user?id={frm}">{html.escape(q.from_user.full_name)}</a> '
        f'به <a href="tg://user?id={to}">{html.escape(tname)}</a> منتقل شد.',
        parse_mode="HTML",
    )


# ───────────────────────── تسک‌های روزانه و هفتگی (خودکار + جایزه‌ی پول) ─────────────────────────
# هر تسک یه «معیار» داره (جق، شیفت، بوس و بغل، ...) + تعداد هدف + جایزه. ربات خودش پیشرفت رو حساب می‌کنه؛
# تا کاربر تموم کنه جایزه یه‌جا میره تو حسابش و تو همون گپ پیام «تسک انجام شد» میاد.
# پشتیبان از /admin → افزودن تسک روزانه/هفتگی می‌تونه تسک جدید با جایزه‌ی دلخواه بسازه.
TASK_EMOJI_ID = "5221951509129872023"
TASK_DONE_EMOJI_ID = "5256156224948284902"
TASK_ICON = f'<tg-emoji emoji-id="{TASK_EMOJI_ID}">📋</tg-emoji>'
TASK_DONE_ICON = f'<tg-emoji emoji-id="{TASK_DONE_EMOJI_ID}">✅</tg-emoji>'

TASK_MAX_REWARD = {"daily": 1_000_000, "weekly": 5_000_000}   # سقف جایزه (تومن) — اینجا راحت عوضش کن

# معیار: (اسم فارسی, نوع (count/money), قالب عنوان, ایموجی پرمیوم)
TASK_METRICS = {
    "jagh":        ("جق زدن",             "count", "{n} بار «جق» بزن",                    lambda: HAND_CE),
    "work":        ("شیفت کاری",          "count", "{n} شیفت کاری انجام بده",             lambda: MAX_LEVEL_CE),
    "hug":         ("بوس و بغل",          "count", "{n} بار کسی رو بوس یا بغل کن",       lambda: ce("energy", 1, "⚡")),
    "gift":        ("هدیه فرستادن",       "count", "{n} هدیه برای بقیه بفرست",           lambda: GIFT_MONEY_CE),
    "duel_win":    ("بردن دوئل",          "count", "{n} دوئل جقی رو ببر",                lambda: SWORDS_CE),
    "casino":      ("بازی کازینو",        "count", "{n} بار تو کازینو بازی کن",          lambda: DICE_CE),
    "bank":        ("واریز به بانک",      "money", "{n} تومن به حساب بانکی واریز کن",   lambda: BANK_CE),
    "robbery":     ("خلاف موفق",          "count", "{n} خلاف موفق انجام بده",            lambda: SPY_CE),
    "car_up":      ("ارتقای ماشین",       "count", "{n} بار قطعه‌ی ماشین ارتقا بده",     lambda: MINES_GEM_CE),
    "car_collect": ("برداشت درآمد ماشین", "count", "{n} بار درآمد ماشین رو برداشت کن",   lambda: MONEY_CE),
    "msg":         ("پیام تو گروه",       "count", "{n} پیام تو گروه بفرست",             lambda: REF_CE),
}

# تسک‌های پیش‌فرض: (دوره, معیار, تعداد هدف, جایزه به تومن) — روزانه‌ها ۳۰۰ هزار تا ۱ میلیون، هفتگی‌ها سخت‌تر تا ۵ میلیون
TASK_DEFAULTS = [
    ("daily", "jagh", 10, 300_000),
    ("daily", "msg", 30, 300_000),
    ("daily", "work", 2, 400_000),
    ("daily", "hug", 3, 500_000),
    ("daily", "duel_win", 1, 700_000),
    ("daily", "robbery", 1, 1_000_000),
    ("weekly", "jagh", 150, 1_500_000),
    ("weekly", "work", 12, 2_000_000),
    ("weekly", "gift", 5, 2_500_000),
    ("weekly", "car_up", 3, 2_500_000),
    ("weekly", "duel_win", 5, 3_000_000),
    ("weekly", "bank", 10_000_000, 3_500_000),
    ("weekly", "robbery", 7, 5_000_000),
]


def task_auto_title(metric: str, target: int) -> str:
    m = TASK_METRICS.get(metric)
    return m[2].format(n=f"{int(target):,}") if m else "تسک"


def task_period_key(period: str) -> str:
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Tehran"))
    return now.strftime("%Y-%m-%d") if period == "daily" else f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"


def task_countdown() -> tuple[str, str]:
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("Asia/Tehran"))
    end_day = now.replace(hour=23, minute=59, second=59, microsecond=0)
    days_to_monday = 7 - now.weekday()
    end_week = (now + timedelta(days=days_to_monday)).replace(hour=23, minute=59, second=59, microsecond=0)
    def fmt(delta):
        sec = max(0, int(delta.total_seconds())); d, rem = divmod(sec, 86400); h, rem = divmod(rem, 3600); m, _ = divmod(rem, 60)
        return f"{d} روز و {h} ساعت و {m} دقیقه" if d else f"{h} ساعت و {m} دقیقه"
    return fmt(end_day-now), fmt(end_week-now)


_TASK_CACHE = {"ts": 0.0, "by_metric": {}}
_TASK_MSG_TS: dict[int, float] = {}


def task_cache_clear():
    _TASK_CACHE["ts"] = 0.0


async def _task_defs(metric: str):
    now = time.time()
    if now - _TASK_CACHE["ts"] > 20:
        rows = await pool.fetch("SELECT task_id, period, title, target, reward, metric FROM tasks WHERE active = TRUE AND metric IS NOT NULL")
        by: dict = {}
        for r in rows:
            by.setdefault(r["metric"], []).append(r)
        _TASK_CACHE["by_metric"] = by
        _TASK_CACHE["ts"] = now
    return _TASK_CACHE["by_metric"].get(metric, [])


async def task_event(bot, chat_id: int, uid: int, metric: str, n: int = 1, name: str | None = None):
    """پیشرفت تسک‌های این معیار رو برای کاربر ثبت می‌کنه. اگه تسکی کامل شد: جایزه رو میده و تو همون گپ خبر میده."""
    try:
        if pool is None or not uid or n <= 0:
            return
        defs = await _task_defs(metric)
        if not defs:
            return
        done = []
        for r in defs:
            key = task_period_key(r["period"])
            target = max(1, int(r["target"] or 1))
            reward = max(0, int(r["reward"] or 0))
            async with pool.acquire() as c:
                async with c.transaction():
                    prog = await c.fetchval(
                        "INSERT INTO task_completions(task_id,user_id,period_key,completed,progress) "
                        "VALUES($1,$2,$3,FALSE,LEAST($4::bigint,$5::bigint)) "
                        "ON CONFLICT(task_id,user_id,period_key) DO UPDATE "
                        "SET progress = LEAST(task_completions.progress + $4::bigint, $5::bigint) "
                        "WHERE task_completions.completed = FALSE RETURNING progress",
                        r["task_id"], uid, key, int(n), target)
                    if prog is None or int(prog) < target:
                        continue
                    ok = await c.fetchval(   # فقط یه‌بار جایزه (حتی اگه دو رویداد همزمان برسن)
                        "UPDATE task_completions SET completed = TRUE, rewarded = $4::bigint "
                        "WHERE task_id=$1 AND user_id=$2 AND period_key=$3 AND completed = FALSE RETURNING 1",
                        r["task_id"], uid, key, reward)
                    if not ok:
                        continue
                    if reward > 0:
                        await c.execute("INSERT INTO users(user_id,name) VALUES($1,'') ON CONFLICT(user_id) DO NOTHING", uid)
                        await c.execute("UPDATE users SET balance = balance + $1::bigint WHERE user_id = $2::bigint", reward, uid)
                    done.append((r["period"], r["title"], reward))
        if not done:
            return
        if name is None:
            name = await pool.fetchval("SELECT name FROM users WHERE user_id=$1", uid)
        who = _ulink(uid, name or "کاربر")
        total = sum(d[2] for d in done)
        parts = [f"{TROPHY_CE} <b>{who}</b> تبریک!"]
        for period, title, reward in done:
            lab = "روزانه" if period == "daily" else "هفتگی"
            line = f"{TASK_DONE_ICON} تسک {lab} «{html.escape(title)}» انجام شد!"
            if reward > 0:
                line += f"\n{MONEY_CE} <b>{reward:,} تومن</b> جایزه گرفتی {EVENT_CE}"
            parts.append(line)
        if len(done) > 1 and total > 0:
            parts.append(f"{MONEY_CE} مجموع جایزه: <b>{total:,} تومن</b>")
        await send_html(bot, chat_id, B("\n\n".join(parts)))
    except Exception:
        log.warning("task_event failed (%s)", metric, exc_info=True)


def _task_bar(p: int, t: int, width: int = 8) -> str:
    filled = max(0, min(width, int(width * p / max(1, t))))
    return "▰" * filled + "▱" * (width - filled)


def _task_emoji(metric) -> str:
    m = TASK_METRICS.get(metric)
    try:
        return m[3]() if m else TASK_ICON
    except Exception:
        return TASK_ICON


async def build_tasks_view(uid: int):
    rows = await pool.fetch("SELECT task_id,period,title,metric,target,reward FROM tasks WHERE active=TRUE "
                            "ORDER BY CASE period WHEN 'daily' THEN 0 ELSE 1 END, task_id")
    dk, wk = task_period_key("daily"), task_period_key("weekly")
    prog = await pool.fetch("SELECT task_id, period_key, progress, completed FROM task_completions "
                            "WHERE user_id=$1 AND period_key = ANY($2::text[])", uid, [dk, wk])
    pm = {(int(r["task_id"]), r["period_key"]): r for r in prog}
    daily_left, weekly_left = task_countdown()
    head = [f"{TASK_ICON} <b>تسک‌های روزانه و هفتگی</b>",
            f"{HG_CE} تا پایان روز: {daily_left}",
            f"{HG_CE} تا پایان هفته: {weekly_left}"]
    lines, buttons, size, hidden = [], [], sum(len(x) for x in head), 0
    last_period = None
    for r in rows:
        period = r["period"]; key = dk if period == "daily" else wk
        p = pm.get((int(r["task_id"]), key))
        done = bool(p and p["completed"])
        block = []
        if period != last_period:
            block.append("")
            block.append(f"{TASK_ICON} <b>{'تسک‌های روزانه' if period == 'daily' else 'تسک‌های هفتگی'}</b>")
        else:
            block.append("________")   # خط جداکننده‌ی بین هر تسک
        title, reward = html.escape(r["title"]), int(r["reward"] or 0)
        if r["metric"]:
            tgt = max(1, int(r["target"] or 1)); cur = min(int(p["progress"]), tgt) if p else 0
            kind = TASK_METRICS.get(r["metric"], ("", "count"))[1]
            fmt = (lambda v: f"{v:,}")
            if done:
                block.append(f"{TASK_DONE_ICON} <s>{title}</s>\n{MONEY_CE} {reward:,} تومن دریافت شد")
            else:
                block.append(f"{_task_emoji(r['metric'])} {title}\n{_task_bar(cur, tgt)} {fmt(cur)}/{fmt(tgt)}"
                             + (f" • {MONEY_CE} جایزه: {reward:,} تومن" if reward else ""))
        else:   # تسک متنی قدیمی: کاربر خودش تیک می‌زنه (بدون جایزه)
            block.append(f"{TASK_DONE_ICON if done else TASK_ICON} {title}")   # دکمه‌ی تیک حذف شد؛ فقط «بروزرسانی» می‌مونه
        chunk = "\n".join(block)
        if size + len(chunk) > 3300:
            hidden += 1; continue
        last_period = period
        size += len(chunk) + 1
        lines.append(chunk)
    if not rows:
        lines.append("\nهنوز تسکی اضافه نشده.")
    if hidden:
        lines.append(f"\n… و {hidden} تسک دیگه")
    buttons.append([btn("بروزرسانی", "primary", icon=TASK_EMOJI_ID, callback_data=f"tk:{uid}:0:r")])
    return B("\n".join(head) + "\n" + "\n".join(lines)), InlineKeyboardMarkup(buttons)


async def show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, user = update.effective_message, update.effective_user
    await upsert_user(user.id, user.full_name)
    text, kb = await build_tasks_view(user.id)
    try:
        await msg.reply_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception:
        log.exception("tasks view failed, retrying without premium emojis")
        await msg.reply_text(_strip_te(text), parse_mode="HTML", reply_markup=kb)


async def task_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, uid_s, task_s, key = q.data.split(":", 3)
    uid, task_id = int(uid_s), int(task_s)
    if q.from_user.id != uid:
        await q.answer("این تسک‌ها برای صاحب پیام هستند.", show_alert=True); return
    if task_id == 0:   # بروزرسانی
        await q.answer("بروز شد ✅")
        await show_tasks_from_callback(q, uid); return
    row = await pool.fetchrow("SELECT period, metric FROM tasks WHERE task_id=$1 AND active=TRUE", task_id)
    if not row or key != task_period_key(row["period"]):
        await q.answer("مهلت این تسک تموم شده یا تسک غیرفعاله.", show_alert=True); return
    if row["metric"]:
        await q.answer("این تسک خودکار حساب میشه؛ فقط کارش رو انجام بده تا جایزه بیاد.", show_alert=True); return
    await pool.execute("""INSERT INTO task_completions(task_id,user_id,period_key,completed) VALUES($1,$2,$3,TRUE)
        ON CONFLICT(task_id,user_id,period_key) DO UPDATE SET completed=NOT task_completions.completed""", task_id, uid, key)
    await q.answer("وضعیت تسک تغییر کرد.")
    await show_tasks_from_callback(q, uid)


async def show_tasks_from_callback(q, uid):
    text, kb = await build_tasks_view(uid)
    await edit_html(q, text, kb)


# ───────────────────── جاسوسی ─────────────────────
SPY_EID = "5805161437466726782"
SPY_CE = f'<tg-emoji emoji-id="{SPY_EID}">🕵️</tg-emoji>'
SPY_PRICE = 1_400_000
SPY_DAILY_LIMIT = 5

def _spy_day_key():
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("Asia/Tehran")).strftime("%Y-%m-%d")

async def spy_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    msg, user = update.effective_message, update.effective_user
    key = _word_key(text)
    if key in ("خریدجاسوسی", "خریدابزارجاسوسی"):
        await upsert_user(user.id, user.full_name)
        async with pool.acquire() as c:
            async with c.transaction():
                row = await c.fetchrow("SELECT balance, spy_owned FROM users WHERE user_id=$1 FOR UPDATE", user.id)
                if row["spy_owned"]:
                    answer = "ابزار جاسوسی رو قبلاً خریدی؛ برای استفاده روی پیام کاربر ریپلای کن و بنویس «جاسوسی»."
                elif int(row["balance"] or 0) < SPY_PRICE:
                    answer = f"موجودی کافی نیست. قیمت جاسوسی: {SPY_PRICE:,} تومان؛ موجودی جیب: {int(row['balance'] or 0):,} تومان."
                else:
                    await c.execute("UPDATE users SET balance=balance-$1, spy_owned=TRUE, spy_day=$2, spy_uses=0 WHERE user_id=$3", SPY_PRICE, _spy_day_key(), user.id)
                    answer = f"{SPY_CE} ابزار جاسوسی با موفقیت خریداری شد!\nهزینه: {SPY_PRICE:,} تومان\nروزانه {SPY_DAILY_LIMIT} بار می‌تونی استفاده کنی. برای جاسوسی روی پیام کاربر ریپلای کن و بنویس «جاسوسی»."
        await msg.reply_text(B(answer), parse_mode="HTML"); return True
    if key not in ("جاسوسی", "استعلامجاسوسی"):
        return False
    target = msg.reply_to_message.from_user if msg.reply_to_message else None
    if target is None or target.is_bot or target.id == user.id:
        await msg.reply_text(B("برای جاسوسی باید روی پیام یک کاربر ریپلای کنی؛ روی خودت یا ربات نمی‌شه."), parse_mode="HTML"); return True
    await upsert_user(user.id, user.full_name)
    day = _spy_day_key()
    async with pool.acquire() as c:
        async with c.transaction():
            own = await c.fetchrow("SELECT spy_owned, spy_day, spy_uses FROM users WHERE user_id=$1 FOR UPDATE", user.id)
            if not own or not own["spy_owned"]:
                await msg.reply_text(B(f"{SPY_CE} اول باید ابزار جاسوسی رو بخری. قیمت: {SPY_PRICE:,} تومان\nبرای خرید بنویس: خرید جاسوسی"), parse_mode="HTML"); return True
            used = int(own["spy_uses"] or 0) if own["spy_day"] == day else 0
            if used >= SPY_DAILY_LIMIT:
                await msg.reply_text(B(f"سهمیه‌ی امروزت تموم شده. روزانه فقط {SPY_DAILY_LIMIT} بار جاسوسی مجازه؛ فردا دوباره شارژ می‌شه."), parse_mode="HTML"); return True
            await c.execute("UPDATE users SET spy_day=$1, spy_uses=$2 WHERE user_id=$3", day, used+1, user.id)
            row = await c.fetchrow("SELECT user_id,name,balance,bank_balance,spouse_id,gender,jaghs,work_count,energy FROM users WHERE user_id=$1", target.id)
            if not row:
                await c.execute("INSERT INTO users(user_id,name) VALUES($1,$2) ON CONFLICT(user_id) DO NOTHING", target.id, target.full_name)
                row = await c.fetchrow("SELECT user_id,name,balance,bank_balance,spouse_id,gender,jaghs,work_count,energy FROM users WHERE user_id=$1", target.id)
            spouse = await c.fetchrow("SELECT name FROM users WHERE user_id=$1", row["spouse_id"]) if row["spouse_id"] else None
            acts = await c.fetchrow("SELECT (SELECT count(*) FROM group_msgs WHERE user_id=$1) AS messages, (SELECT count(*) FROM cars WHERE user_id=$1) AS cars, (SELECT count(*) FROM user_house WHERE user_id=$1) AS homes, (SELECT count(*) FROM robbery_state WHERE user_id=$1) AS robberies", target.id)
    job = current_job(row["gender"], int(row["jaghs"] or 0), int(row["work_count"] or 0)) if row["gender"] in JOBS else None
    job_name = job[1] if job else "بدون شغل"
    marital = f"متأهل با {html.escape(spouse['name'] or 'همسر') }" if spouse else "مجرد"
    activity = f"جق ثبت‌شده: {int(row['jaghs'] or 0):,}\nشیفت‌های کاری: {int(row['work_count'] or 0):,}\nپیام‌های ثبت‌شده در گروه‌ها: {int(acts['messages'] or 0):,}\nعملیات خلاف ثبت‌شده: {int(acts['robberies'] or 0):,}\nماشین‌ها: {int(acts['cars'] or 0)}\nخانه‌ها: {int(acts['homes'] or 0)}"
    remaining = SPY_DAILY_LIMIT-(used+1)
    report = (f"{SPY_CE} <b>گزارش جاسوسی</b>\nهدف: {_ulink(target.id,target.full_name)}\n\n"
              f"💵 پول داخل جیب: <b>{int(row['balance'] or 0):,} تومان</b>\n🏦 پول داخل بانک: <b>{int(row['bank_balance'] or 0):,} تومان</b>\n"
              f"💰 دارایی کل: <b>{int(row['balance'] or 0)+int(row['bank_balance'] or 0):,} تومان</b>\n"
              f"💍 وضعیت: <b>{marital}</b>\n💼 شغل: <b>{html.escape(job_name)}</b>\n⚡ انرژی: <b>{float(row['energy'] or 0):g}</b>\n\n"
              f"📋 <b>فعالیت‌ها</b>\n{activity}\n\nاستفاده‌ی باقی‌مانده‌ی امروز: <b>{remaining} از {SPY_DAILY_LIMIT}</b>")
    await msg.reply_text(report, parse_mode="HTML"); return True


async def on_group_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not msg.text or not user or user.is_bot:
        return
    # حذف فاصله و کاراکترهای نامرئی (کیبوردهای فارسی گاهی نیم‌فاصله اضافه می‌کنن)
    text = "".join(ch for ch in msg.text if ch not in "\u200c\u200d\u200e\u200f\u200b\ufeff").strip()
    log.info("group text from %s: %r", user.id, msg.text)
    _tm = time.time()
    if len(text) >= 2 and _tm - _TASK_MSG_TS.get(user.id, 0) >= 15:
        _TASK_MSG_TS[user.id] = _tm
        await task_event(context.bot, msg.chat_id, user.id, "msg", name=user.full_name)
    if await event_text(update, context, text):
        return
    if await spy_text(update, context, text):
        return
    if _word_key(text) in ("تسک", "تسکها", "تسکروزانه", "تسکهفتگی", "tasks"):
        await show_tasks(update, context)
        return
    if norm_word(text) == ME_WORD:
        await show_me(update, context)
        return
    if norm_word(text).replace(" ", "").lower() in JOBS_WORDS:
        await show_jobs(update, context)
        return
    if norm_word(text).replace(" ", "").lower() in SHIFT_WORDS:
        await show_shift(update, context)
        return
    if _word_key(text) in JM_WORDS:
        await jagh_machine_collect(update, context)
        return
    if _word_key(text) in TOP_WORDS:
        await top(update, context)
        return
    panel_key = _word_key(text)
    if panel_key in ("خودمایناروببین", "خودم", "پنلمن", "پنلشخصی") or panel_key.startswith("خودمایناروببین"):
        await show_self_panel(update, context)
        return
    if _word_key(text) == "دست":
        await show_self_panel(update, context)
        return
    if _word_key(text) == "بانک":
        await show_bank(update, context)
        return
    if _word_key(text) == "خونه" or _word_key(text) == "خانه" or _word_key(text) == "املاک":
        await show_homes(update, context)
        return
    if await marriage_text(update, context, text):
        return
    if await gift_hug_text(update, context, text):
        return
    if await handle_bank_transfer(update, context, text):
        return
    if _word_key(text) in WALLET_WORDS:
        await wallet(update, context)
        return
    if _word_key(text) in CAR_WORDS:
        await show_garage(update, context)
        return
    if norm_word(text).startswith("انتقال"):
        await on_transfer_request(update, context, text)
        return
    if _word_key(text) in ("دوئلجقی", "دوئلجق"):
        await duel_request(update, context)
        return
    if await casino_text(update, context, text):
        return
    if await theft_text(update, context, text):
        return
    if _word_key(text) in ("خلاف", "دزدی", "سرقت"):
        await show_robbery_menu(update, context)
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
    hand_level = int(row.get("hand_level", 0) or 0)
    user_cooldown = max(280, COOLDOWN_SECONDS - hand_level * 5)
    amount = random.randint(MIN_REWARD, MAX_REWARD)
    low_en = float(row.get("energy", 0) or 0) < ENERGY_LOW
    if low_en:  # انرژی کم = پول کمتر
        amount = max(1, int(amount * JAGH_LOW_MULT))
    base_amount = amount
    amount = max(1, int(amount * income_mult()))   # ایونت درآمد

    # آپدیت اتمیک: فقط اگه ۵ دقیقه گذشته باشه پول اضافه میشه
    updated = await pool.fetchrow(
        "WITH o AS (SELECT jagh_machine AS old_m FROM users WHERE user_id = $3::bigint FOR UPDATE) "
        "UPDATE users SET jagh_machine = LEAST(o.old_m + $1::bigint, $5::bigint), jaghs = jaghs + 1, last_ts = $2::double precision "
        "FROM o WHERE user_id = $3::bigint AND last_ts <= $2::double precision - $4::double precision "
        "RETURNING balance, jaghs, jagh_machine, o.old_m",
        amount,
        now,
        user.id,
        float(user_cooldown),
        JM_CAP,
    )
    if updated is None:
        row = await get_user(user.id)
        remaining = user_cooldown - (now - row["last_ts"])
        m, s = divmod(int(max(remaining, 0)) + 1, 60)
        await msg.reply_text(B(f'دستت هنوز خستس<tg-emoji emoji-id="5443048677406940863">🥱</tg-emoji>\nصبر کن {m} دقیقه و {s} ثانیه بعد... دوباره می‌تونی جق بزنی'), parse_mode="HTML")
        return

    text = pick_reply(amount, base_amount) + "\n\n" + energy_line(row.get("energy", 0))
    if low_en:
        text += "\n⚠️ انرژیت کمه؛ پول جق کمتر شد! با بغل، بوس یا هدیه انرژی بگیر."
    _jm_new = int(updated["jagh_machine"]); _jm_added = _jm_new - int(updated["old_m"])
    if _jm_added <= 0:
        text += f"\n\n{JM_CE} دستگاه جق پره و پولی بهش اضافه نشد! بنویس «دستگاه جق» تا برداشت کنی."
    else:
        text += f"\n\n{JM_CE} {_jm_added:,} تومن رفت تو دستگاه جق"
        if _jm_new >= JM_MIN:
            text += " — آماده‌ی برداشته ✅"
    text += event_banner()
    new_bal = updated["balance"]
    gender = row["gender"]
    new_jaghs = updated["jaghs"]
    if gender in CHARACTERS and level_for(new_jaghs) > level_for(new_jaghs - 1):
        title = html.escape(CHARACTERS[gender][level_for(new_jaghs) - 1][0])
        text += f"\n\n{ce('me', 1, '🎖')} ارتقا گرفتی! حالا «{title}» هستی. برای دیدنش بنویس «{ME_WORD}»"
    await msg.reply_text(B(text), parse_mode="HTML")
    await task_event(context.bot, msg.chat_id, user.id, "jagh", name=user.full_name)


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
    if not has_perm(update.effective_user.id, "emoji"):
        await msg.reply_text(BP("این دستور فقط برای پشتیبان‌هاست یا دسترسی‌اش بهت داده نشده. آیدی خودت رو با /myid بگیر و به صاحب ربات بده."), parse_mode="HTML")
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
def card_text(name: str, gender: str, balance: int, jaghs: int, work_count: int = 0) -> str:
    # سطح/تصویر کاراکتر با سطحی که برای شغل فعلی در جدول JOBS تعریف شده هماهنگ است.
    job = current_job(gender, jaghs, work_count) if gender in JOBS else None
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
        f"💼 کارهای انجام‌شده: {work_count:,}",
    ]
    if lvl == len(LEVEL_THRESHOLDS) - 1 and jaghs >= LEVEL_THRESHOLDS[-1] and work_count < 1200:
        lines.append(f"💼 {1200 - work_count:,} کار دیگه تا آخرین کاراکتر")
    elif lvl < len(LEVEL_THRESHOLDS):
        need = max(0, LEVEL_THRESHOLDS[lvl] - jaghs)
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


async def send_card(bot, chat_id: int, row, reply_to: int | None = None, text: str | None = None, reply_markup=None, img_balance: int | None = None):
    """کارت کاراکتر کاربر: عکس + کپشن (اسم، سطح، موجودی). اگه یه مرحله خطا بده میره سراغ مرحله‌ی بعد، ولی تا جایی که بشه عکس همراه کپشن میاد."""
    gender = row["gender"]
    job = current_job(gender, row["jaghs"], row["work_count"]) if row["gender"] else None
    lvl = JOBS[gender][job[0]][1] if job else level_for(row["jaghs"])
    text = text or card_text(row["name"] or "", gender, row["balance"], row["jaghs"], row["work_count"])
    rp = ReplyParameters(message_id=reply_to, allow_sending_without_reply=True) if reply_to else None

    # کپشن بدون ایموجی پرمیوم (برای وقتی که تلگرام ایموجی رو قبول نکنه)
    plain_text = re.sub(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", r"\1", text)

    photo = CHAR_PHOTOS.get((gender, lvl))  # عکس دلخواهِ پشتیبان (file_id) اولویت داره
    if not photo:
        try:
            photo = await asyncio.to_thread(render_card_image, gender, lvl, row["balance"] if img_balance is None else img_balance)
        except Exception:
            log.exception("render_card_image failed (level %s)", lvl)
            photo = None

    if photo:
        for caption in (text, plain_text):
            try:
                if hasattr(photo, "seek"):
                    photo.seek(0)
                await bot.send_photo(chat_id, photo, caption=caption, parse_mode="HTML", reply_parameters=rp, reply_markup=reply_markup)
                return
            except Exception:
                log.exception("send_photo failed (level %s)", lvl)
    for t in (text, plain_text):
        try:
            await bot.send_message(chat_id, t, parse_mode="HTML", reply_parameters=rp, reply_markup=reply_markup)
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


_PM_BLOCK_WORDS = {
    "خونه", "خانه", "املاک", "دست", "کازینو", "casino", "خلاف", "دزدی", "سرقت", "جاسوسی",
    "استعلامجاسوسی", "خریدجاسوسی", "خریدابزارجاسوسی", "دوئلجقی", "دوئلجق",
    "فروشگاه", "سکس", "موشک", "کراش",
}


def _pm_group_only_text(text: str) -> bool:
    """کلمه‌هایی که تو پی‌وی کار نمی‌کنن و باید تو گروه نوشته بشن."""
    key = _word_key(text)
    if key in _PM_BLOCK_WORDS or key in CAR_WORDS or key.startswith(("خریدجاسوسی",)):
        return True
    t = norm_word(text).translate(_DIG)
    return bool(re.fullmatch(r"(?:میز\s*)?(?:تاس|اسلات|مین|موشک|کراش)(?:\s+.+)?|(?:تاس|اسلات)\s*میز\s+.+", t, re.I))


async def on_private_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تو پی‌وی ربات هم «خودم» کار می‌کنه."""
    msg = update.effective_message
    if await support_add_input(update, context):
        return
    if await event_text(update, context, msg.text or ""):
        return
    if await spy_text(update, context, msg.text or ""):
        return
    if await admin_input(update, context):
        return
    if await account_transfer_input(update, context):
        return
    if msg and msg.text and _pm_group_only_text(msg.text):
        await msg.reply_text(B(PM_GROUP_ONLY_TEXT), parse_mode="HTML")
        return
    if await menu_text_router(update, context):
        return
    if msg and msg.text and context.user_data.get("await_jobs") and has_perm(update.effective_user.id, "jobs"):
        if msg.text.strip() == "لغو":
            context.user_data.pop("await_jobs", None)
            await msg.reply_text("لغو شد.")
        else:
            await save_custom_jobs(update, context, context.user_data["await_jobs"])
        return
    if msg and msg.text and _word_key(msg.text) in ("تسک", "تسکها", "تسکروزانه", "تسکهفتگی", "tasks"):
        await show_tasks(update, context)
    elif msg and msg.text and norm_word(msg.text) == ME_WORD:
        await show_me(update, context)
    elif msg and msg.text and norm_word(msg.text).replace(" ", "").lower() in JOBS_WORDS:
        await show_jobs(update, context)
    elif msg and msg.text and norm_word(msg.text).replace(" ", "").lower() in SHIFT_WORDS:
        await show_shift(update, context)
    elif msg and msg.text and _word_key(msg.text) in TOP_WORDS:
        await top(update, context)
    elif msg and msg.text and (_word_key(msg.text) in ("خودمایناروببین", "خودم", "پنلمن", "پنلشخصی") or _word_key(msg.text).startswith("خودمایناروببین")):
        await show_self_panel(update, context)
    elif msg and msg.text and _word_key(msg.text) == "دست":
        await show_self_panel(update, context)
    elif msg and msg.text and _word_key(msg.text) == "بانک":
        await show_bank(update, context)
    elif msg and msg.text and _word_key(msg.text) in ("خونه", "خانه", "املاک"):
        await show_homes(update, context)
    elif msg and msg.text and await marriage_text(update, context, msg.text):
        return
    elif msg and msg.text and await handle_bank_transfer(update, context, msg.text):
        return
    elif msg and msg.text and _word_key(msg.text) in WALLET_WORDS:
        await wallet(update, context)
    elif msg and msg.text and _word_key(msg.text) in CAR_WORDS:
        await show_garage(update, context)
    elif msg and msg.text and _word_key(msg.text) in GIFT_LIST_WORDS:
        await show_gifts(update, context)
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
    if not has_perm(update.effective_user.id, "photo"):
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
    if not has_perm(update.effective_user.id, "photo"):
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
    if not has_perm(update.effective_user.id, "jobs"):
        return
    context.user_data.pop("await_jobs", None)
    kb = InlineKeyboardMarkup([[
        btn("👨 جدول مردها", "primary", callback_data="sj:m"),
        btn("👩 جدول زن‌ها", "success", callback_data="sj:f"),
    ]])
    await update.effective_message.reply_text("جدول شغل‌های کدوم جنسیت رو می‌خوای عوض کنی؟", reply_markup=kb)


async def set_jobs_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not has_perm(q.from_user.id, "jobs"):
        await q.answer("فقط پشتیبان‌ها", show_alert=True)
        return
    g = q.data.split(":")[1]
    context.user_data["await_jobs"] = g
    await q.answer()
    await q.edit_message_text(SETJOBS_HELP.format(who=GENDER_FA[g]))


async def jobs_reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/jobsreset — برگشت به جدول خودکار (مرد، زن یا هر دو)."""
    if not has_perm(update.effective_user.id, "jobs"):
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
    if not has_perm(q.from_user.id, "jobs"):
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
    if await buy_receipt(update, context):
        return
    g = context.user_data.get("await_jobs")
    if msg and g and has_perm(update.effective_user.id, "jobs"):
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


def current_job(gender: str, jaghs: int, work_count: int = 0):
    """شغل بر اساس جق؛ شغل نهایی علاوه بر ۲۵۰۰ جق به ۱۲۰۰ شیفت انجام‌شده نیاز دارد."""
    best = None
    for i, (name, _lvl) in enumerate(JOBS[gender]):
        if jaghs >= JOB_REQUIRED_JAGHS[i]:
            best = (i, name)
    if best and best[0] == len(JOBS[gender]) - 1 and work_count < 1200:
        best = (len(JOBS[gender]) - 2, JOBS[gender][len(JOBS[gender]) - 2][0]) if len(JOBS[gender]) > 1 and jaghs >= JOB_REQUIRED_JAGHS[-2] else None
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
    job = current_job(row["gender"], row["jaghs"], row["work_count"])
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
    job = current_job(row["gender"], row["jaghs"], row["work_count"])
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
    pay = int(pay * income_mult())   # ایونت درآمد
    now = time.time()
    new_bal = await pool.fetchval(
        "UPDATE users SET balance = balance + $1::bigint, last_shift = $2::double precision, work_count = work_count + 1 "
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
    if (_eb := event_banner()):
        text += f"<b>{_eb}</b>"
    await q.answer()
    try:
        await q.edit_message_text(text, parse_mode="HTML")
    except Exception:
        log.exception("shift result failed, retrying without premium emojis")
        try:
            await q.edit_message_text(_strip_te(text), parse_mode="HTML")
        except Exception:
            log.exception("shift result edit failed again")
    await task_event(context.bot, q.message.chat_id, q.from_user.id, "work", name=q.from_user.full_name)


async def card_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cardcheck — فقط پشتیبان‌ها: نشون میده عکس‌ها و فونت رو سرور پیدا کرده یا نه."""
    if not has_perm(update.effective_user.id, "check"):
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
CASINO_OPEN = True    # False = بخش کازینو بسته‌ست
CASINO_CLOSED_EID = "5938149692423541565"
CASINO_CLOSED_TEXT = "بخش کازینو به دستور پاول دورف فعلا بسته‌ست."
CASINO_MIN_BET = 1_000
CASINO_MAX_BET = 50_000_000   # سقف هر شرط (همه‌ی بازی‌های کازینو)
_CASINO_MAX_MSG = f"سقف شرط {CASINO_MAX_BET:,} تومنه."
DICE_NET = 5          # حدس درست تاس: سود ۵ برابر شرط (کلاً ۶ برابر برمی‌گرده)
CASINO_WAIT = 3.8     # صبر برای تموم شدن انیمیشن تاس/اسلات
DICE_DELETE_AFTER = 5  # چند ثانیه بعد از اعلام نتیجه، تاس/اسلات‌های فرستاده‌شده پاک میشن
TABLE_TTL = 600       # میز بعد از ۱۰ دقیقه منقضی میشه
BET_CHOICES = [10_000, 50_000, 100_000, 500_000, 1_000_000, 5_000_000]
MINES_COUNT = 3
MINES_CELLS = 21
MINES_BOMB_EID = "5256147609243889028"
MINES_TILE_EID = "5256156224948284902"
MINES_GEM_EID = "6170417452162225096"
MINES_CASHOUT_EID = "5381992813896412558"
MINES_BOMB_CE = f'<tg-emoji emoji-id="{MINES_BOMB_EID}">💣</tg-emoji>'
MINES_TILE_CE = f'<tg-emoji emoji-id="{MINES_TILE_EID}">▢</tg-emoji>'
MINES_GEM_CE = f'<tg-emoji emoji-id="{MINES_GEM_EID}">💠</tg-emoji>'
MINES_CASHOUT_CE = f'<tg-emoji emoji-id="{MINES_CASHOUT_EID}">💰</tg-emoji>'

# اسلات: امتیاز هر نماد (BAR = 🔔). جمع امتیاز سه چرخ → ضریب پرداخت.
SLOT_POINTS = [0, 10, 15, 21]  # 🔔، 🍇، 🍋، 7️⃣
# (حداقل امتیاز، ضریب) از بالا به پایین؛ ضریب × شرط برمی‌گرده. ضریب‌ها به کاربر نشون داده نمیشن.
# ۱۰ و کمتر = ۰ | ۱۱ تا ۱۵ = ۰٫۵ | ۱۶ تا ۲۱ = ۱ | ۲۲ تا ۲۷ = ۱٫۲ | ۲۸ تا ۳۵ = ۱٫۳ | ۳۶ تا ۴۵ = ۱٫۵ | ۴۶ تا ۵۲ = ۱٫۷ | ۵۳ تا ۵۹ = ۱٫۸ | ۶۰ به بالا = ۲
SLOT_TIERS = [(60, 2.0), (53, 1.8), (46, 1.7), (36, 1.5), (28, 1.3), (22, 1.2), (16, 1.0), (11, 0.5)]

# استراحت بعد از هر بازی کازینو (تاس، اسلات، مین)
CASINO_COOLDOWN = 15

DICE_EID = "5123323069850452850"
DICE_CE = f'<tg-emoji emoji-id="{DICE_EID}">🎲</tg-emoji>'
SLOT_SYMS = [
    "🔔",
    '<tg-emoji emoji-id="4961065085217801230">🍇</tg-emoji>',
    '<tg-emoji emoji-id="5348476388062015170">🍋</tg-emoji>',
    '<tg-emoji emoji-id="5357158630091139922">7️⃣</tg-emoji>',
]
GAME_EMOJI = {"dice": "🎲", "slot": "🎰"}  # ایموجی خودِ تلگرام برای sendDice
GAME_FA = {"dice": f"تاس {DICE_CE}", "slot": "اسلات 🎰", "crash": "موشک کاغذی 🛩", "mines": f"مین {MINES_BOMB_CE}", "poison": "سم یا خوراکی ☠️"}


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


def slot_final_mult(v: int) -> float:
    """اگه چرخ سمت راست 🔔 بیاد همیشه ۰؛ وگرنه ضریب از جمع امتیاز."""
    if slot_reels(v)[2] == 0:
        return 0.0
    return slot_mult(slot_points(v))


def _cd_left(context, uid: int) -> int:
    ts = context.application.bot_data.setdefault("casino_cd", {}).get(uid, 0)
    left = CASINO_COOLDOWN - (time.time() - ts)
    return int(left) + 1 if left > 0 else 0


def _cd_set(context, uid: int):
    context.application.bot_data.setdefault("casino_cd", {})[uid] = time.time()


def _cd_plain(left: int) -> str:
    return f"انقدر دیگه جق زدی که خسته‌ای! یه نفس بگیر؛ {left} ثانیه دیگه می‌تونی بازی کنی."


def _cd_html(left: int) -> str:
    return B(f"{HG_CE} {html.escape(_cd_plain(left))}")


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


def win_lines(profit: int, total: int, balance: int) -> str:
    """بلوک نتیجه‌ی برد: سود، مبلغ کل برد از این بازی، دارایی کل (با ایموجی پرمیوم)."""
    return (f"\n{MONEY_CE} سود: <b>{profit:,}</b> تومن"
            f"\n{TROPHY_CE} مبلغ کل برد از این بازی: <b>{total:,}</b> تومن"
            f"\n{ce('wallet', 1, '💰')} دارایی کل: <b>{balance:,}</b> تومن")


def _uname(user) -> str:
    return html.escape(user.full_name)


def _ulink(uid: int, name: str) -> str:
    return f'<a href="tg://user?id={uid}">‏{html.escape(name or "کاربر")}‏</a>'


def _fa_bet(n: int) -> str:
    return f"{n // 1_000_000} میلیون" if n >= 1_000_000 else f"{n // 1000} هزار"


# ───── رکوردهای گروهی کازینو ─────
async def update_group_record(chat_id: int, game: str, user, profit: int) -> str:
    if chat_id >= 0:
        return ""
    if profit > 0:
        await pool.execute(
            """INSERT INTO casino_records(chat_id, game, user_id, name, record)
               VALUES($1,$2,$3,$4,$5)
               ON CONFLICT(chat_id, game) DO UPDATE SET user_id=EXCLUDED.user_id,
                 name=EXCLUDED.name, record=EXCLUDED.record
               WHERE EXCLUDED.record > casino_records.record""",
            chat_id, game, user.id, user.full_name, profit,
        )
    r = await pool.fetchrow("SELECT user_id,name,record FROM casino_records WHERE chat_id=$1 AND game=$2", chat_id, game)
    if not r:
        return "🏆 رکورد گروه: هنوز ثبت نشده"
    return f"🏆 رکورد برد گروه {GAME_FA[game]}: {_ulink(r['user_id'], r['name'] or 'کاربر')} — {r['record']:,} تومن"


# ───── پنل ─────
def panel_main(uid: int):
    text = B("🎰 کازینو\n\nیکی از بازی‌ها رو انتخاب کن 👇")
    kb = InlineKeyboardMarkup([[
        btn("تاس", "primary", icon=DICE_EID, callback_data=f"cz:g:{uid}:dice"),
        btn("🎰 اسلات", "primary", callback_data=f"cz:g:{uid}:slot"),
        btn("مین", "primary", icon=MINES_BOMB_EID, callback_data=f"cz:g:{uid}:mines"),
    ], ([btn("🛩 موشک کاغذی", "success", callback_data=f"cz:g:{uid}:crash")] if crash_allowed(uid) else [])
       + [btn("سم یا خوراکی", "danger", callback_data=f"cz:g:{uid}:poison")]])
    return text, kb


def panel_game(uid: int, game: str):
    if game == "dice":
        rules = (f"{DICE_CE} تاس\n\n۱ نفره: یه عدد ۱ تا ۶ حدس می‌زنی؛ درست بیاد {DICE_NET + 1} برابر پولت رو می‌گیری.\n"
                 f"۲ نفره: یه میز تو گروه باز می‌شه، نفر دوم میاد؛ هر کی تاس بالاتر بندازه کل پول رو می‌بره.")
    elif game == "poison":
        rules = "☠️ سم یا خوراکی — فقط دو نفره\n\n۲۴ خانه وجود داره. هر نفر در ۶۰ ثانیه سم خودش رو پشت یک خانه مخفی می‌کنه. بعد به نوبت خانه‌های حریف رو انتخاب می‌کنید؛ پیدا کردن سم حریف یعنی بردن کل شرط! انتخاب هر مرحله ۶۰ ثانیه مهلت داره."
    elif game == "crash":
        rules = ("🛩 موشک کاغذی (کراش) — بازی تک‌نفره\n\nموشک کاغذی بعد از شرط‌بندی شروع به اوج گرفتن می‌کنه و ضریبت لحظه‌به‌لحظه بالا می‌ره. "
                 "ضریب از ‎−۱٫۰۰ شروع می‌شه و به سمت مثبت‌ها می‌ره. قبل از شروع یه ضریب هدف (حداقل ۱٫۲×) انتخاب می‌کنی و اگه موشک بهش برسه خودکار برداشت می‌شه؛ ولی هر لحظه ممکنه سقوط کنه و شرطت بسوزه!\n"
                 "⚠️ سخته! لحظه‌ی سقوط غیرقابل پیش‌بینیه و خیلی وقت‌ها قبل از ۱٫۲ (حتی تو ضریب منفی) اتفاق می‌افته.\n🔒 فعلاً فقط برای پشتیبان‌ها فعاله.")
    elif game == "mines":
        rules = (f"{MINES_BOMB_CE} مین — بازی تک‌نفره\n\n۲۱ خانه وجود داره و ۳ خانه بمبه. هر خانه امن ضریب رو بیشتر می‌کنه. با پیدا کردن بمب شرطت از دست میره؛ هر زمان خواستی برداشت کن تا شرط × ضریب به حسابت برگرده. ضریب نهایی در خانه امن هجدهم ۳٫۰۵× است.")
    else:
        rules = ("🎰 اسلات\n\n۱ نفره: روی یکی از نمادها (7️⃣ 🔔 🍋 🍇) شرط می‌بندی و بعد خودت روی پیام ربات ریپلای می‌کنی و 🎰 می‌فرستی.\n"
                 "هر سه تا نماد تو یکی باشه ×۳ | دوتاش ×۱٫۷ | فقط یکیش ×۰٫۳ | هیچی نیاد می‌بازی.\n\n"
                 "۲ نفره: ربات اسلات می‌ندازه؛ نماد امتیاز داره (🔔 صفر | 🍇 ۱۰ | 🍋 ۱۵ | 7️⃣ ۲۱) و هر کی امتیاز بیشتر بیاره کل پول رو می‌بره.")
    kb = InlineKeyboardMarkup([
        ([btn("👤 ۱ نفره", "primary", callback_data=f"cz:a:{uid}:{game}:1")] if game in ("mines", "crash")
         else [btn("👥 ۲ نفره", "success", callback_data=f"cz:a:{uid}:poison:2")] if game == "poison"
         else [btn("👤 ۱ نفره", "primary", callback_data=f"cz:a:{uid}:{game}:1"),
               btn("👥 ۲ نفره", "success", callback_data=f"cz:a:{uid}:{game}:2")]),
        [btn("برگشت", None, icon=EID_BACK, callback_data=f"cz:m:{uid}")],
    ])
    return B(rules), kb


def panel_amounts(uid: int, game: str, mode: str):
    who = "۱ نفره" if mode == "1" else "۲ نفره"
    if mode == "1":
        pre = "n" if game == "dice" else ("pm" if game == "mines" else ("pc" if game == "crash" else "ps"))
    else:
        pre = "sy" if game == "poison" else ("td" if game == "dice" else "ts")
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
    if bet > CASINO_MAX_BET:
        await say(BP(_CASINO_MAX_MSG))
        return
    busy = _busy(context)
    if user.id in busy:
        await say(BP("صبر کن بازی قبلیت تموم شه 😅"))
        return
    if (left := _cd_left(context, user.id)) > 0:
        await say(_cd_html(left))
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
        _cd_set(context, user.id)
        name = _uname(user)
        if game == "dice":
            if v == pick:
                await _give(user.id, bet * (1 + DICE_NET))
                text = f"{DICE_CE} تاس: {v} (حدست {pick})\n🎉 {name} بردی! +{bet * DICE_NET:,} تومن سود"
            else:
                text = f"{DICE_CE} تاس: {v} (حدست {pick})\n😢 {name} باختی! -{bet:,} تومن"
        else:
            pts = slot_points(v)
            mult = slot_final_mult(v)
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
            text = f"🎰 {slot_text(v)}\nامتیاز: {pts}\n{res}"
        profit = (bet * (1 + DICE_NET) if game == "dice" and v == pick else 0) if game == "dice" else payout
        record_line = await update_group_record(chat_id, game, user, profit)
        row = await get_user(user.id)
        tail = win_lines(profit - bet, profit, row['balance']) if profit > bet else f"\n{ce('wallet', 1, '💰')} دارایی: {row['balance']:,}"
        await say(B(text + ("\n" + record_line if record_line else "") + tail))
        delete_later(bot, chat_id, [m.message_id])
        await task_event(bot, chat_id, user.id, "casino", name=user.full_name)
    finally:
        busy.discard(user.id)


# ───── اسلات ۱ نفره: روی یه نماد شرط می‌بنده و خودش 🎰 می‌فرسته ─────
SLOT_PICK_SYMS = [("7️⃣", 3), ("🔔", 0), ("🍋", 2), ("🍇", 1)]   # (نمایش، اندیس نماد تو SLOT_SYMS)
SLOT_BET_MULTS = {3: 3.0, 2: 1.7, 1: 0.3, 0: 0.0}                 # تعداد نمادِ درست → ضریب
SLOT_PENDING_TTL = 120
SLOT_USER_WAIT = 2.2     # ثانیه تا اعلام نتیجه‌ی اسلاتی که خود کاربر فرستاده (انیمیشن تلگرام ~۲ ثانیه‌ست)


def _slot_pending(context) -> dict:
    return context.application.bot_data.setdefault("slot_pending", {})


def panel_slot_symbol(uid: int, bet: int):
    row = [btn(e, "primary", callback_data=f"cz:sp:{uid}:{bet}:{idx}") for e, idx in SLOT_PICK_SYMS]
    kb = InlineKeyboardMarkup([row[:2], row[2:], [btn("برگشت", None, icon=EID_BACK, callback_data=f"cz:a:{uid}:slot:1")]])
    text = B(f"🎰 اسلات ۱ نفره — شرط {bet:,} تومن\n\nروی کدوم نماد شرط می‌بندی؟ 👇\n\n"
             "سه تا نمادت بیاد (مثلاً 🍇🍇🍇) ← ×۳\nدوتاش بیاد (مثلاً 🍇🍇🍋) ← ×۱٫۷\nفقط یکیش بیاد (مثلاً 🍇🍋🔔) ← ×۰٫۳\nهیچی نیاد ← می‌بازی")
    return text, kb


async def send_slot_picker(bot, chat_id: int, user, bet: int, reply_to: int | None = None):
    """از دستور متنی «اسلات 100هزار» یا مبلغ دلخواه: پیام انتخاب نماد رو می‌فرسته."""
    rp = ReplyParameters(message_id=reply_to, allow_sending_without_reply=True) if reply_to else None
    if chat_id > 0:
        await send_html(bot, chat_id, BP("کازینو فقط تو گروه کار می‌کنه."), reply_parameters=rp); return
    if bet < CASINO_MIN_BET:
        await send_html(bot, chat_id, BP(f"حداقل شرط {CASINO_MIN_BET:,} تومنه."), reply_parameters=rp); return
    if bet > CASINO_MAX_BET:
        await send_html(bot, chat_id, BP(_CASINO_MAX_MSG), reply_parameters=rp); return
    text, kb = panel_slot_symbol(user.id, bet)
    await send_html(bot, chat_id, text, reply_markup=kb, reply_parameters=rp)


async def slot_choose(bot, context, q, user, bet: int, sym: int):
    """بعد از انتخاب نماد: پیام رو آماده‌ی ریپلای 🎰 می‌کنه."""
    if bet < CASINO_MIN_BET or bet > CASINO_MAX_BET or sym not in (0, 1, 2, 3):
        await q.answer("مبلغ یا نماد نامعتبره.", show_alert=True); return
    row = await get_user(user.id)
    if not row or int(row["balance"] or 0) < bet:
        await q.answer("دارایی‌ت برای این شرط کافی نیست!", show_alert=True); return
    await q.answer()
    pend = _slot_pending(context)
    for k in [k for k, v in pend.items() if v["uid"] == user.id]:
        pend.pop(k, None)
    key = (q.message.chat_id, q.message.message_id)
    pend[key] = {"uid": user.id, "bet": bet, "sym": sym, "ts": time.time()}
    emo = next(e for e, i in SLOT_PICK_SYMS if i == sym)
    text = B(f"🎰 اسلات ۱ نفره — شرط {bet:,} تومن روی {emo}\n\n"
             f"حالا روی همین پیام <u>ریپلای</u> کن و ایموجی 🎰 رو خودت بفرست!\n{HG_CE} تا {SLOT_PENDING_TTL // 60} دقیقه وقت داری.")
    await edit_html(q, text, InlineKeyboardMarkup([[btn("برگشت", None, icon=EID_BACK, callback_data=f"cz:m:{user.id}")]]))


async def slot_dice_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """کاربر 🎰 رو ریپلای روی پیام شرط فرستاده → نتیجه حساب میشه."""
    msg, user, chat = update.effective_message, update.effective_user, update.effective_chat
    if not msg or not msg.dice or msg.dice.emoji != "🎰" or not msg.reply_to_message or not user or user.is_bot:
        return
    if getattr(msg, "forward_origin", None) or getattr(msg, "forward_date", None):
        return   # اسلات فورواردی قبول نیست
    key = (chat.id, msg.reply_to_message.message_id)
    pend = _slot_pending(context)
    info = pend.get(key)
    if not info or info["uid"] != user.id:
        return
    pend.pop(key, None)
    bot, bet, sym = context.bot, info["bet"], info["sym"]
    target_id = msg.reply_to_message.message_id

    async def edit_panel(text, kb=None):
        try:
            await bot.edit_message_text(text, chat_id=chat.id, message_id=target_id, parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            if "not modified" in str(e).lower():
                return
            try:
                await bot.edit_message_text(re.sub(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", r"\1", text), chat_id=chat.id, message_id=target_id, parse_mode="HTML", reply_markup=kb)
            except Exception:
                log.warning("slot panel edit failed", exc_info=True)

    again_kb = InlineKeyboardMarkup([
        [btn("🔄 دوباره", "success", callback_data=f"cz:a:{user.id}:slot:1")],
        [btn("برگشت", None, icon=EID_BACK, callback_data=f"cz:m:{user.id}")],
    ])
    if time.time() - info["ts"] > SLOT_PENDING_TTL:
        await edit_panel(B("⌛ زمانت تموم شد؛ دوباره شرط ببند."), again_kb); return
    busy = _busy(context)
    if user.id in busy:
        return
    if (left := _cd_left(context, user.id)) > 0:
        await edit_panel(_cd_html(left), again_kb); return
    await upsert_user(user.id, user.full_name)
    if await _take(user.id, bet) is None:
        row = await get_user(user.id)
        await edit_panel(B(f"{ce('wallet', 1, '💰')} دارایی‌ت کافی نیست! موجودی: {row['balance']:,} تومن"), again_kb); return
    busy.add(user.id)
    try:
        v = msg.dice.value
        await asyncio.sleep(SLOT_USER_WAIT)   # فقط به‌اندازه‌ی تموم شدن انیمیشن؛ پیام «می‌چرخه» هم دیگه ادیت نمی‌شه
        _cd_set(context, user.id)
        reels = slot_reels(v)
        hits = sum(1 for r in reels if r == sym)
        mult = SLOT_BET_MULTS[hits]
        payout = int(bet * mult)
        if payout > 0:
            await _give(user.id, payout)
        diff = payout - bet
        name = _uname(user)
        emo = next(e for e, i in SLOT_PICK_SYMS if i == sym)
        if diff > 0:
            res = f"🎉 {name} بردی! +{diff:,} تومن سود"
        elif diff == 0:
            res = f"🤝 {name} پولت برگشت"
        else:
            res = f"😢 {name} باختی! {diff:,} تومن"
        text = f"🎰 {slot_text(v)}\nشرطت روی {emo} بود: {hits} تا اومد (×{_mult_txt(mult)})\n{res}"
        record_line = await update_group_record(chat.id, "slot", user, payout)
        row = await get_user(user.id)
        tail = win_lines(diff, payout, row['balance']) if diff > 0 else f"\n{ce('wallet', 1, '💰')} دارایی: {row['balance']:,}"
        await edit_panel(B(text + ("\n" + record_line if record_line else "") + tail), again_kb)
        delete_later(bot, chat.id, [msg.message_id])
        await task_event(bot, chat.id, user.id, "casino", name=user.full_name)
    finally:
        busy.discard(user.id)


# ───── موشک (کراش): ضریب بالا می‌ره، هر لحظه ممکنه منفجر بشه ─────
CRASH_EDGE = 0.95      # بخش اصلی توزیع (۱/u) ضرب تو این؛ سخت‌گیری اصلی از انفجارهای زودهنگامه
CRASH_EARLY = 0.22     # ~۲۲٪ بازی‌ها زودهنگام می‌سوزن: یه جایی بین -۱٫۰۰ تا ۱٫۱۹ (حتی قبل از ۱٫۲)
CRASH_MIN = -1.0       # ضریب از -۱ شروع میشه
CRASH_MAX = 100.0
CRASH_RATE = 0.20      # ضریب = e^(RATE × ثانیه) − ۲  (تو ثانیه‌ی صفر دقیقاً -۱٫۰۰)
CRASH_TICK = 2.0       # هر چند ثانیه تصویر (با ضریب جدید) آپدیت میشه


def crash_allowed(uid: int) -> bool:
    """فعلاً موشک فقط برای صاحب‌ها و پشتیبان‌هاست."""
    return uid in OWNER_IDS or uid in STAFF


CRASH_STAFF_MSG = "🛩 فعلاً موشک کاغذی فقط برای پشتیبان‌ها فعاله."


def crash_point() -> float:
    if random.random() < CRASH_EARLY:
        return round(random.uniform(CRASH_MIN, 1.19), 2)
    u = max(random.random(), 1e-9)
    return max(CRASH_MIN, min(CRASH_MAX, math.floor(100 * CRASH_EDGE / u) / 100))


def crash_mult_at(t: float) -> float:
    return math.floor((math.exp(CRASH_RATE * max(0.0, t)) - 2.0) * 100) / 100


def crash_time_for(m: float) -> float:
    return math.log(max(m + 2.0, 1.0)) / CRASH_RATE


def _crash_frame(st, m: float):
    payout = max(0, int(st["bet"] * m))
    tgt = f"\n🎯 ضریب هدف: <b>{st['target']:.2f}×</b> (خودکار برداشت می‌شه)" if st.get("target") else ""
    text = B(f"🛩 موشک کاغذی در حال اوج گرفتنه!\nشرط: {st['bet']:,} تومن{tgt}\n\nضریب: <b>{m:.2f}×</b>\nمبلغ فعلی: {payout:,} تومن")
    return text, None   # دکمه‌ی برداشت نداره؛ فقط خودکار تو ضریب هدف برداشت می‌شه


CG_W, CG_H = 360, 240

def _cg_sky():
    im = Image.new("RGB", (CG_W, CG_H))
    d = ImageDraw.Draw(im)
    for y in range(CG_H):
        t = y / CG_H
        d.line([(0, y), (CG_W, y)], fill=(int(40 + 110 * t), int(90 + 110 * t), int(170 + 70 * t)))
    return im

def _cg_plane(angle, scale=1.0):
    S = 160
    lay = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    c = S // 2
    def P(x, y): return (c + x * scale, c + y * scale)
    nose, tt, tb, fold = P(60, 0), P(-48, -24), P(-48, 20), P(-18, 4)
    d.polygon([nose, tt, fold], fill=(255, 255, 255, 255), outline=(150, 165, 190, 255))
    d.polygon([nose, fold, tb], fill=(205, 215, 235, 255), outline=(150, 165, 190, 255))
    d.line([nose, fold], fill=(150, 165, 190, 255), width=2)
    d.polygon([fold, P(-48, 2), tb], fill=(180, 192, 215, 255))
    return lay.rotate(angle, resample=Image.BICUBIC, expand=False)

def _cg_clouds(im, shift):
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0)); d = ImageDraw.Draw(ov)
    for cx, cy, r in ((60, 50, 26), (210, 90, 20), (300, 40, 24), (150, 190, 22), (20, 170, 18)):
        x = (cx - shift) % (CG_W + 120) - 60
        for dx, dy, rr in ((0, 0, r), (r * .9, r * .2, r * .8), (-r * .9, r * .25, r * .7)):
            d.ellipse([x + dx - rr, cy + dy - rr * .6, x + dx + rr, cy + dy + rr * .6], fill=(255, 255, 255, 150))
    im.paste(ov, (0, 0), ov)

def _cg_fly():
    frames = []
    N = 24
    for i in range(N):
        im = _cg_sky(); _cg_clouds(im, i * (CG_W + 120) / N)
        d = ImageDraw.Draw(im)
        ph = 2 * math.pi * i / N
        x, y = 190 + 8 * math.sin(ph), 125 + 10 * math.sin(ph * 2 + 1)
        for k in range(1, 9):   # رد نقطه‌چین
            d.ellipse([x - 55 - k * 14, y + 6 + k * 3 - 2, x - 55 - k * 14 + 4, y + 6 + k * 3 + 2], fill=(255, 255, 255))
        pl = _cg_plane(12 + 5 * math.sin(ph))
        im.paste(pl, (int(x - 80), int(y - 80)), pl)
        frames.append(im)
    return frames

def _cg_fall():
    frames = []
    N = 22
    for i in range(N):
        im = _cg_sky(); _cg_clouds(im, 0)
        t = i / (N - 1)
        x = 190 - 40 * t; y = 120 + 110 * t * t
        pl = _cg_plane(12 - 560 * t, 1.0 - 0.15 * t)
        im.paste(pl, (int(x - 80), int(y - 80)), pl)
        d = ImageDraw.Draw(im)
        if i > N - 6:   # پوف
            r = (i - (N - 6)) * 9
            d.ellipse([x - r, 225 - r / 2, x + r, 225 + r / 2], outline=(255, 255, 255), width=3)
        frames.append(im)
    frames += [frames[-1]] * 6
    return frames

def _cg_gif(frames, ms, loop):
    out = BytesIO()
    pal = [f.convert("P", palette=Image.ADAPTIVE, colors=64) for f in frames]
    pal[0].save(out, "GIF", save_all=True, append_images=pal[1:], duration=ms, loop=loop, optimize=True, disposal=2)
    return out.getvalue()



_CRASH_GIFS: tuple | None = None
_CG_FLY_BASE: list | None = None


def _cg_text(im, m: float, target=None, crashed=False):
    """ضریب رو روی خودِ تصویر می‌نویسه. فونت داخلی فقط رقم داره؛ «-» «.» «x» دستی کشیده میشن."""
    d = ImageDraw.Draw(im, "RGBA")
    txt = f"{m:.2f}"
    f = load_font(56)
    col = (255, 90, 90) if (crashed or m < 0) else ((255, 220, 90) if m < 1.2 else (120, 255, 150))
    out = (0, 0, 0, 255)
    adv = {}
    for ch in txt:
        if ch.isdigit():
            adv[ch] = f.getlength(ch)
    dw = max(adv.values()) if adv else 32
    widths = [dw if c.isdigit() else (dw * 0.42 if c == "." else (dw * 0.6 if c == "-" else dw)) for c in txt] + [dw * 0.9]   # آخرش «x»
    total = sum(widths)
    x = (CG_W - total) / 2
    y = 22
    d.rounded_rectangle([x - 16, y - 8, x + total + 16, y + 72], radius=16, fill=(0, 0, 0, 130))
    for ch, w in zip(txt + "x", widths):
        if ch.isdigit():
            d.text((x + (w - f.getlength(ch)) / 2, y), ch, font=f, fill=col, stroke_width=2, stroke_fill=out)
        elif ch == ".":
            cx, cy = x + w / 2, y + 60
            d.ellipse([cx - 5, cy - 5, cx + 5, cy + 5], fill=col, outline=out)
        elif ch == "-":
            d.rectangle([x + 3, y + 33, x + w - 3, y + 43], fill=col, outline=out)
        else:   # x
            cx, cy = x + w / 2, y + 44
            for sx in (1, -1):
                d.line([cx - 14, cy - 14 * sx, cx + 14, cy + 14 * sx], fill=out, width=9)
            for sx in (1, -1):
                d.line([cx - 14, cy - 14 * sx, cx + 14, cy + 14 * sx], fill=col, width=5)
        x += w
    return im


def crash_fly_gif(m: float, target=None) -> bytes:
    """گیف پروازِ موشک با ضریب فعلی روی تصویر (هر آپدیت دوباره ساخته میشه)."""
    global _CG_FLY_BASE
    if _CG_FLY_BASE is None:
        _CG_FLY_BASE = _cg_fly()
    frames = [_cg_text(f.copy(), m, target) for f in _CG_FLY_BASE[::2]]
    return _cg_gif(frames, 140, 0)


def crash_fall_gif(m: float) -> bytes:
    frames = [_cg_text(f.copy(), m, None, crashed=True) for f in _cg_fall()]
    return _cg_gif(frames, 70, 1)


def _crash_gifs():
    """(گیف پرواز حلقه‌ای، گیف سقوط) موشک کاغذی؛ یه بار ساخته و کش میشه."""
    global _CRASH_GIFS
    if _CRASH_GIFS is None:
        _CRASH_GIFS = (_cg_gif(_cg_fly(), 70, 0), _cg_gif(_cg_fall(), 70, 1))
    return _CRASH_GIFS


def _gif_file(data: bytes, name: str) -> BytesIO:
    b = BytesIO(data); b.name = name
    return b


async def _crash_tick(bot, st, m: float, text: str, kb=None):
    """آپدیت حین پرواز: تصویر با ضریب جدید + کپشن."""
    if not st.get("anim"):
        await _crash_edit(bot, st, text, kb); return
    chat_id, mid = st["chat_id"], st["message_id"]
    try:
        data = await asyncio.to_thread(crash_fly_gif, m, st.get("target"))
    except Exception:
        log.warning("crash gif render failed", exc_info=True)
        await _crash_edit(bot, st, text, kb); return
    for t in (text, re.sub(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", r"\1", text)):
        try:
            await bot.edit_message_media(chat_id=chat_id, message_id=mid,
                                         media=InputMediaAnimation(_gif_file(data, "fly.gif"), caption=t, parse_mode="HTML"), reply_markup=kb)
            return
        except Exception as e:
            if "not modified" in str(e).lower():
                return
            ra = getattr(e, "retry_after", None)
            if ra:
                await asyncio.sleep(min(float(getattr(ra, "total_seconds", lambda: ra)()), 5)); return
    log.warning("crash media tick failed")


async def _crash_edit(bot, st, text: str, kb=None):
    """کپشنِ گیف رو (یا متن پیام، اگه گیف نشد) ادیت می‌کنه."""
    chat_id, mid = st["chat_id"], st["message_id"]
    async def once(t):
        if st.get("anim"):
            await bot.edit_message_caption(chat_id=chat_id, message_id=mid, caption=t, parse_mode="HTML", reply_markup=kb)
        else:
            await bot.edit_message_text(t, chat_id=chat_id, message_id=mid, parse_mode="HTML", reply_markup=kb)
    try:
        await once(text)
    except Exception as e:
        if "not modified" in str(e).lower():
            return
        ra = getattr(e, "retry_after", None)
        if ra:
            await asyncio.sleep(min(float(getattr(ra, "total_seconds", lambda: ra)()), 5)); return
        try:
            await once(re.sub(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", r"\1", text))
        except Exception:
            log.warning("crash edit failed", exc_info=True)


def _crash_end_kb(uid: int):
    return InlineKeyboardMarkup([[btn("🔄 دوباره", "success", callback_data=f"cz:a:{uid}:crash:1")],
                                 [btn("برگشت", None, icon=EID_BACK, callback_data=f"cz:m:{uid}")]])


async def _crash_lose(bot, context, st):
    if st["finished"]:
        return
    st["finished"] = True
    _busy(context).discard(st["uid"]); _cd_set(context, st["uid"])
    rec = await update_group_record(st["chat_id"], "crash", st["user"], 0)
    row = await get_user(st["uid"])
    tgt = f"\n🎯 هدفت {st['target']:.2f}× بود و بهش نرسید." if st.get("target") else ""
    text = B(f"💥 موشک کاغذی تو ضریب <b>{st['crash']:.2f}×</b> سقوط کرد!{tgt}\nشرط {st['bet']:,} تومن از دست رفت 😢\n{rec}\nموجودی: {row['balance']:,}")
    kb = _crash_end_kb(st["uid"])
    done = False
    if st.get("anim"):      # گیف سقوط جایگزین گیف پرواز میشه
        try:
            await bot.edit_message_media(chat_id=st["chat_id"], message_id=st["message_id"],
                                         media=InputMediaAnimation(_gif_file(await asyncio.to_thread(crash_fall_gif, st["crash"]), "fall.gif"), caption=text, parse_mode="HTML"), reply_markup=kb)
            done = True
        except Exception:
            log.warning("crash media swap failed", exc_info=True)
    if not done:
        await _crash_edit(bot, st, text, kb)
    context.application.bot_data.get("crash_games", {}).pop(st["gid"], None)


async def _crash_win(bot, context, st, m: float, user, auto: bool):
    """برداشت (دستی یا خودکار به ضریب هدف)."""
    if st["finished"]:
        return
    st["finished"] = True
    _busy(context).discard(st["uid"]); _cd_set(context, st["uid"])
    payout = int(st["bet"] * m)
    await _give(st["uid"], payout)
    rec = await update_group_record(st["chat_id"], "crash", user, payout)
    row = await get_user(st["uid"])
    diff = payout - st["bet"]
    res = f"🎉 +{diff:,} تومن سود" if diff > 0 else ("🤝 پولت برگشت" if diff == 0 else f"😢 {diff:,} تومن")
    head = "🎯 به ضریب هدفت رسیدی و خودکار برداشت شد!" if auto else "💰 موشک کاغذی رو به‌موقع فرود آوردی!"
    wl = win_lines(diff, payout, row['balance']) if diff > 0 else f"\nموجودی: {row['balance']:,}"
    await _crash_edit(bot, st, B(f"{head}\nضریب برداشت: <b>{m:.2f}×</b>\nدریافتی: {payout:,} تومن ({res}){wl}\n(تا ضریب {st['crash']:.2f}× اوج می‌گرفت)\n{rec}"), _crash_end_kb(st["uid"]))
    context.application.bot_data.get("crash_games", {}).pop(st["gid"], None)


async def _crash_loop(bot, context, st):
    try:
        t_crash = crash_time_for(st["crash"])
        target = st.get("target")
        t_target = crash_time_for(target) if target else float("inf")
        auto_win = bool(target) and st["crash"] >= target
        t_end = min(t_crash, t_target) if auto_win else t_crash
        while not st["finished"]:
            elapsed = time.monotonic() - st["start"]
            await asyncio.sleep(max(0.05, min(CRASH_TICK, t_end - elapsed)))
            if st["finished"]:
                return
            elapsed = time.monotonic() - st["start"]
            if auto_win and elapsed >= t_target:
                await _crash_win(bot, context, st, target, st["user"], True); return
            if elapsed >= t_crash or crash_mult_at(elapsed) >= st["crash"]:
                await _crash_lose(bot, context, st); return
            mm = crash_mult_at(elapsed)
            text, kb = _crash_frame(st, mm)
            await _crash_tick(bot, st, mm, text, kb)
    except Exception:
        log.exception("crash loop failed")
        if not st["finished"]:
            st["finished"] = True
            _busy(context).discard(st["uid"])
            await _give(st["uid"], st["bet"])   # خطای ربات: شرط برمی‌گرده


CRASH_MIN_TARGET = 1.2
CRASH_TARGET_CHOICES = (1.2, 1.5, 2.0, 3.0, 5.0, 10.0)


def panel_crash_target(uid: int, bet: int):
    rows, row = [], []
    for m in CRASH_TARGET_CHOICES:
        row.append(btn(f"{m:g}×", "primary", callback_data=f"cz:pt:{uid}:{bet}:{int(round(m * 100))}"))
        if len(row) == 3:
            rows.append(row); row = []
    rows.append([btn("✏️ ضریب دلخواه", "success", callback_data=f"cz:ptc:{uid}:{bet}")])
    rows.append([btn("برگشت", None, icon=EID_BACK, callback_data=f"cz:a:{uid}:crash:1")])
    text = B(f"🛩 موشک کاغذی — شرط {bet:,} تومن\n\nضریب هدفت رو انتخاب کن (حداقل {CRASH_MIN_TARGET:g}×).\n"
             "اگه موشک به این ضریب برسه خودکار برداشت می‌شه و شرط × ضریب رو می‌گیری؛ اگه قبلش سقوط کنه، شرطت می‌سوزه.")
    return text, InlineKeyboardMarkup(rows)


async def start_crash(bot, context, chat_id, user, bet, q=None, reply_to=None, target: float | None = None):
    async def out(text, kb=None):
        if q: await edit_html(q, text, kb)
        else: await send_html(bot, chat_id, text, reply_markup=kb, reply_parameters=ReplyParameters(message_id=reply_to, allow_sending_without_reply=True) if reply_to else None)
    back = InlineKeyboardMarkup([[btn("برگشت", None, icon=EID_BACK, callback_data=f"cz:m:{user.id}")]])
    if not crash_allowed(user.id):
        await out(BP(CRASH_STAFF_MSG), back); return
    if chat_id > 0:
        await out(BP("کازینو فقط تو گروه کار می‌کنه.")); return
    if bet < CASINO_MIN_BET or bet > CASINO_MAX_BET:
        await out(BP(f"حداقل شرط {CASINO_MIN_BET:,} و حداکثرش {CASINO_MAX_BET:,} تومنه."), back); return
    if target is None:      # اول ضریب هدف رو انتخاب کنه
        ptext, pkb = panel_crash_target(user.id, bet)
        await out(ptext, pkb); return
    target = round(float(target), 2)
    if target < CRASH_MIN_TARGET or target > CRASH_MAX:
        await out(BP(f"ضریب هدف باید بین {CRASH_MIN_TARGET:g} و {CRASH_MAX:g} باشه."), back); return
    busy = _busy(context)
    if user.id in busy:
        await out(BP("اول بازی قبلیت رو تموم کن.")); return
    if (left := _cd_left(context, user.id)) > 0:
        await out(_cd_html(left), back); return
    await upsert_user(user.id, user.full_name)
    if await _take(user.id, bet) is None:
        row = await get_user(user.id)
        await out(B(f"موجودی کافی نیست؛ موجودی فعلی: {row['balance']:,} تومن")); return
    import uuid
    gid = uuid.uuid4().hex[:8]
    st = {"gid": gid, "uid": user.id, "user": user, "bet": bet, "chat_id": chat_id, "message_id": None, "target": target,
          "crash": crash_point(), "start": time.monotonic(), "finished": False, "anim": False}
    context.application.bot_data.setdefault("crash_games", {})[gid] = st
    busy.add(user.id)
    text, kb = _crash_frame(st, CRASH_MIN)
    rp = ReplyParameters(message_id=reply_to, allow_sending_without_reply=True) if reply_to else None
    sent = None
    try:
        fly = await asyncio.to_thread(crash_fly_gif, CRASH_MIN, target)
        if q:
            try: await q.message.delete()
            except Exception: pass
        try:
            sent = await bot.send_animation(chat_id, animation=_gif_file(fly, "fly.gif"), caption=text, parse_mode="HTML", reply_markup=kb, reply_parameters=rp)
        except Exception:
            sent = await bot.send_animation(chat_id, animation=_gif_file(fly, "fly.gif"), caption=re.sub(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", r"\1", text), parse_mode="HTML", reply_markup=kb, reply_parameters=rp)
        st["anim"] = True
    except Exception:
        log.warning("crash gif failed; falling back to text", exc_info=True)
        try:
            sent = await send_html(bot, chat_id, text, reply_markup=kb, reply_parameters=rp)
        except Exception:
            log.exception("crash start failed")
            st["finished"] = True; busy.discard(user.id); await _give(user.id, bet)
            return
    st["message_id"] = sent.message_id
    st["start"] = time.monotonic()
    asyncio.create_task(_crash_loop(bot, context, st))


async def crash_cb(update: Update, context: ContextTypes.DEFAULT_TYPE, parts):
    q = update.callback_query
    _, _, uid_s, gid = parts[:4]
    if q.from_user.id != int(uid_s):
        await q.answer("این بازی مال تو نیست.", show_alert=True); return
    st = context.application.bot_data.get("crash_games", {}).get(gid)
    if not st or st["finished"]:
        await q.answer("این بازی تموم شده.", show_alert=True); return
    m = crash_mult_at(time.monotonic() - st["start"])
    if m >= st["crash"]:
        await q.answer("دیر شد! موشک کاغذی سقوط کرد 💥", show_alert=True)
        await _crash_lose(context.bot, context, st); return
    await q.answer("برداشت انجام شد!")
    await _crash_win(context.bot, context, st, m, q.from_user, False)


# ───── میز دو نفره ─────
async def create_table(bot, chat_id: int, user, game: str, bet: int, reply_to: int | None = None, q=None, context=None):
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
    if bet > CASINO_MAX_BET:
        await say(BP(_CASINO_MAX_MSG))
        return
    if user.id in _busy(context):
        await say(BP("اول کاری که دستته (بازی یا دوئل) رو تموم کن 😅"))
        return
    if (left := _cd_left(context, user.id)) > 0:
        await say(_cd_html(left))
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
    """ضریب پلکانی تا ۳٫۰۵ برابر در خانه امن هجدهم؛ ۳ بمب در ۲۱ خانه."""
    if safe_count <= 0:
        return 1.0
    return round(1.0 + 2.05 * (safe_count / (MINES_CELLS - MINES_COUNT)), 2)


_BLANK = "\u2800"  # متن نامرئی دکمه؛ فقط ایموجی پرمیوم دیده میشه


def mines_keyboard(uid: int, game_id: str, opened: set[int], bombs: set[int], finished=False, can_cash=True):
    rows = []
    for r in range(7):
        row = []
        for c in range(3):
            i = c * 7 + r
            if i in opened:
                row.append(btn(_BLANK, "danger" if i in bombs else "success", icon=MINES_BOMB_EID if i in bombs else MINES_GEM_EID, callback_data=f"cz:mx:{uid}:{game_id}:{i}"))
            elif finished and i in bombs:
                row.append(btn(_BLANK, "danger", icon=MINES_BOMB_EID, callback_data=f"cz:mx:{uid}:{game_id}:{i}"))
            elif finished:
                row.append(btn("·", None, callback_data=f"cz:mx:{uid}:{game_id}:{i}"))
            else:
                row.append(btn(_BLANK, "primary", icon=MINES_TILE_EID, callback_data=f"cz:mx:{uid}:{game_id}:{i}"))
        rows.append(row)
    if can_cash and not finished:
        rows.append([btn("برداشت", "success", icon=MINES_CASHOUT_EID, callback_data=f"cz:mc:{uid}:{game_id}")])
    return InlineKeyboardMarkup(rows)


async def start_mines(bot, context, chat_id, user, bet, q=None, reply_to=None):
    if bet < CASINO_MIN_BET or bet > CASINO_MAX_BET:
        text = BP(f"حداقل شرط {CASINO_MIN_BET:,} و حداکثرش {CASINO_MAX_BET:,} تومنه.")
        if q: await edit_html(q, text)
        else: await send_html(bot, chat_id, text)
        return
    busy = _busy(context)
    if user.id in busy:
        text = BP("اول بازی قبلیت رو تموم کن.")
        if q: await edit_html(q, text)
        else: await send_html(bot, chat_id, text)
        return
    if (left := _cd_left(context, user.id)) > 0:
        text = _cd_html(left)
        if q: await edit_html(q, text, InlineKeyboardMarkup([[btn("برگشت", None, icon=EID_BACK, callback_data=f"cz:m:{user.id}")]]))
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
    text = f"{MINES_GEM_CE} بازی مین\nشرط: {bet:,} تومن\n۳ بمب بین ۲۱ خانه پنهانه. خانه‌های امن رو باز کن و هر وقت خواستی برداشت بزن.\nضریب فعلی: ۱×"
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
            _cd_set(context, uid)
            kb = mines_keyboard(uid, game_id, st["opened"] | {idx}, st["bombs"], True, False)
            rec = await update_group_record(st["chat_id"], "mines", q.from_user, 0)
            await q.answer("بمب! بازی تموم شد.", show_alert=True)
            await edit_html(q, B(f"{MINES_BOMB_CE} بمب پیدا کردی و باختی!\nشرط {st['bet']:,} تومن از دست رفت.\n{rec}\nمحل هر سه بمب مشخص شد."), kb)
            return
        st["opened"].add(idx)
        count = len(st["opened"]); mult = mines_multiplier(count)
        if count >= MINES_CELLS - MINES_COUNT:
            st["finished"] = True; _busy(context).discard(uid); _cd_set(context, uid)
            payout = int(st["bet"] * 3.05)
            await _give(uid, payout)
            row = await get_user(uid)
            rec = await update_group_record(st["chat_id"], "mines", q.from_user, payout)
            await q.answer("همه خانه‌های امن باز شد!")
            await edit_html(q, B(f"🏆 همه ۱۸ خانه امن رو باز کردی!\nضریب نهایی: ۳٫۰۵×\nپرداخت: {payout:,} تومن{win_lines(payout - st['bet'], payout, row['balance'])}\n{rec}\nمحل بمب‌ها مشخص شد."), mines_keyboard(uid, game_id, st["opened"], st["bombs"], True, False))
            return
        await q.answer(f"خانه امن! ضریب {mult:g}×")
        await edit_html(q, B(f"{MINES_GEM_CE} مین\nشرط: {st['bet']:,} تومن\nخانه امن: {count} از ۱۸\nضریب برداشت: {mult:g}×\nمبلغ دریافتی: {int(st['bet']*mult):,} تومن"), mines_keyboard(uid, game_id, st["opened"], st["bombs"]))
    elif action == "mc":
        st["finished"] = True; _busy(context).discard(uid); _cd_set(context, uid)
        mult = mines_multiplier(len(st["opened"]))
        payout = int(st["bet"] * mult)
        await _give(uid, payout)
        row = await get_user(uid)
        rec = await update_group_record(st["chat_id"], "mines", q.from_user, payout)
        await q.answer("برداشت انجام شد.")
        wl = win_lines(payout - st['bet'], payout, row['balance']) if payout > st['bet'] else f"\nموجودی: {row['balance']:,}"
        await edit_html(q, B(f"{MINES_CASHOUT_CE} برداشت موفق!\nخانه امن: {len(st['opened'])}\nضریب: {mult:g}×\nشرط: {st['bet']:,} تومن\nدریافتی: {payout:,} تومن{wl}\n{rec}\nمحل هر سه بمب مشخص شد."), mines_keyboard(uid, game_id, st["opened"], st["bombs"], True, False))



# ───── بازی دونفره سم یا خوراکی (۲۴ خانه، مهلت هر انتخاب ۶۰ ثانیه) ─────
POISON_EID = "5805658107484835896"
FOOD_EID = "5201879022421688548"
POISON_CE = f'<tg-emoji emoji-id="{POISON_EID}">☠️</tg-emoji>'
FOOD_CE = f'<tg-emoji emoji-id="{FOOD_EID}">🍬</tg-emoji>'

def poison_board(game_id, st, reveal=False):
    """صفحه‌ی بازی؛ خانه‌های بازشده دیگر قابل انتخاب نیستند."""
    rows=[]
    opened=st.setdefault("opened", set())
    for base in range(0,24,6):
        row=[]
        for i in range(base,base+6):
            icon=None
            if reveal and i in st.get("hidden", {}).values():
                label="☠️"; icon=POISON_EID
            elif i in opened:
                label="🍬"; icon=FOOD_EID
            else:
                label="▫️"
            row.append(btn(label, "primary", icon=icon, callback_data=f"cz:po:{game_id}:{i}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)

async def create_poison_table(bot, context, chat_id, user, bet, reply_to=None):
    if bet < CASINO_MIN_BET or bet > CASINO_MAX_BET:
        await send_html(bot,chat_id,BP(f"حداقل شرط {CASINO_MIN_BET:,} و حداکثرش {CASINO_MAX_BET:,} تومنه.")); return
    await upsert_user(user.id,user.full_name)
    if await _take(user.id,bet) is None:
        row=await get_user(user.id); await send_html(bot,chat_id,B(f"موجودی کافی نیست: {row['balance']:,} تومن")); return
    import uuid
    gid=uuid.uuid4().hex[:9]
    st={"chat":chat_id,"creator":user.id,"names":{user.id:user.full_name},"bet":bet,"phase":"lobby","created":time.time(),"hidden":{},"turn":None,"tries":0,"message":None,"opened":set()}
    context.application.bot_data.setdefault("poison_games",{})[gid]=st
    kb=InlineKeyboardMarkup([[btn("🎮 ورود به بازی", "success", callback_data=f"cz:pjoin:{gid}")]])
    m=await send_html(bot,chat_id,B(f"{POISON_CE} <b>سم یا خوراکی</b>\n\nسازنده: {_ulink(user.id,user.full_name)}\nشرط هر نفر: {bet:,} تومان\nجایزه: {bet*2:,} تومان\n\nنفر دوم وارد بشه!"),reply_markup=kb,reply_parameters=ReplyParameters(message_id=reply_to,allow_sending_without_reply=True) if reply_to else None)
    st["message"]=m.message_id

async def _finish_poison(app, st, winner, loser, reason):
    if st.get("phase")=="done":
        return
    st["phase"]="done"
    if winner:
        await _give(winner,st["bet"]*2)
    gid=next((k for k,v in app.bot_data.setdefault("poison_games",{}).items() if v is st), "")
    kb=poison_board(gid,st,reveal=True) if gid else None
    wl=""
    if winner:
        wrow=await get_user(winner)
        wl=win_lines(st["bet"],st["bet"]*2,wrow["balance"] if wrow else 0)
    try:
        await app.bot.edit_message_text(
            f"{POISON_CE} {reason}\n🏆 برنده: {_ulink(winner,st['names'].get(winner,'بازیکن')) if winner else '—'}\nبازنده: {_ulink(loser,st['names'].get(loser,'بازیکن')) if loser else '—'}{wl}\nهر دو سم مشخص شدند.",
            chat_id=st["chat"], message_id=st["message"], parse_mode="HTML", reply_markup=kb)
    except Exception:
        log.exception("poison finish edit failed")

async def poison_timeout(app, gid, deadline):
    await asyncio.sleep(max(0, deadline-time.time()))
    st=app.bot_data.setdefault("poison_games",{}).get(gid)
    if not st or st.get("phase") not in ("hide","guess") or st.get("deadline") != deadline or time.time()<deadline:
        return
    loser=st.get("hide_turn") if st["phase"]=="hide" else st.get("turn")
    winner=next((x for x in st.get("players",[]) if x!=loser),None)
    await _finish_poison(app,st,winner,loser,"مهلت تمام شد؛ حریف برنده شد.")

async def poison_cb(update,context,parts):
    q=update.callback_query; user=q.from_user; games=context.application.bot_data.setdefault("poison_games",{})
    act=parts[1]
    if act=="pjoin":
        gid=parts[2]; st=games.get(gid)
        if not st or st["phase"]!="lobby": await q.answer("این میز فعال نیست.",show_alert=True); return
        if user.id==st["creator"]: await q.answer("خودت نمی‌تونی وارد میز خودت بشی.",show_alert=True); return
        if await _take(user.id,st["bet"]) is None: await q.answer("موجودی کافی نداری.",show_alert=True); return
        st["names"][user.id]=user.full_name; st["players"]=[st["creator"],user.id]; st["phase"]="hide"; st["hide_turn"]=st["creator"]; st["deadline"]=time.time()+60
        context.application.create_task(poison_timeout(context.application,gid,st["deadline"]))
        await q.answer("وارد بازی شدی!")
        await q.edit_message_text(f"{POISON_CE} <b>سم یا خوراکی</b>\n\nهر نفر سم خودش رو پشت یکی از ۲۴ خانه مخفی می‌کنه.\nنوبت اول: {_ulink(st['creator'],st['names'][st['creator']])}\n۶۰ ثانیه فرصت داری.",parse_mode="HTML",reply_markup=poison_board(gid,st))
        return
    if act=="po":
        gid=parts[2]
        try: cell=int(parts[3])
        except (ValueError,IndexError): await q.answer("خانه نامعتبره.",show_alert=True); return
        st=games.get(gid)
        if not st or st.get("phase") not in ("hide","guess"): await q.answer("این بازی تمام شده.",show_alert=True); return
        if not 0<=cell<24: await q.answer("خانه نامعتبره.",show_alert=True); return
        if time.time()>st["deadline"]:
            loser=st.get("hide_turn") if st["phase"]=="hide" else st.get("turn")
            winner=next((x for x in st["players"] if x!=loser),None)
            await _finish_poison(context.application,st,winner,loser,"مهلت تمام شد؛ حریف برنده شد.")
            await q.answer("مهلت تمام شد؛ حریف برنده شد.",show_alert=True); return
        if user.id not in st.get("players",[]): await q.answer("این میز برای بازیکنانشه.",show_alert=True); return
        if st["phase"]=="hide":
            if user.id!=st["hide_turn"]: await q.answer("هنوز نوبت تو نیست.",show_alert=True); return
            if cell in st["hidden"].values(): await q.answer("سم دوم باید پشت یک خانه‌ی متفاوت مخفی بشه.",show_alert=True); return
            st["hidden"][user.id]=cell
            if len(st["hidden"])==1:
                st["hide_turn"]=next(x for x in st["players"] if x!=user.id); st["deadline"]=time.time()+60
                context.application.create_task(poison_timeout(context.application,gid,st["deadline"]))
                await q.answer("سمت مخفی شد!")
                await q.edit_message_text(f"{_ulink(st['hide_turn'],st['names'][st['hide_turn']])} حالا سم خودش رو پشت یک خانه مخفی کنه. ۶۰ ثانیه فرصت!",parse_mode="HTML",reply_markup=poison_board(gid,st)); return
            st["phase"]="guess"; st["turn"]=st["creator"]; st["deadline"]=time.time()+60; st["opened"].clear()
            context.application.create_task(poison_timeout(context.application,gid,st["deadline"]))
            await q.answer("هر دو سم مخفی شد!")
            await q.edit_message_text(f"هر دو سم مخفی شد!\nنوبت {_ulink(st['turn'],st['names'][st['turn']])}: خانه‌ای رو انتخاب کن؛ سم خودت رو نمی‌تونی انتخاب کنی. پیدا کردن سم حریف یعنی باخت! ۶۰ ثانیه فرصت.",parse_mode="HTML",reply_markup=poison_board(gid,st)); return
        if user.id!=st["turn"]: await q.answer("نوبت حریفه.",show_alert=True); return
        if cell in st.setdefault("opened",set()): await q.answer("این خانه قبلاً باز شده؛ یکی دیگه رو انتخاب کن.",show_alert=True); return
        if cell==st["hidden"].get(user.id):
            await q.answer("رو سم خودت نمی‌تونی بزنی!",show_alert=True); return
        opponent=next(x for x in st["players"] if x!=user.id)
        if cell==st["hidden"].get(opponent):
            st["opened"].add(cell)
            st["phase"]="done"
            await _give(opponent,st["bet"]*2)
            await q.answer("سم حریف رو پیدا کردی؛ حریف برنده شد!",show_alert=True)
            wrow=await get_user(opponent)
            await q.edit_message_text(f"{POISON_CE} {_ulink(user.id,st['names'][user.id])} سم حریف رو پیدا کرد و باخت!\n🏆 برنده: {_ulink(opponent,st['names'][opponent])}{win_lines(st['bet'],st['bet']*2,wrow['balance'] if wrow else 0)}\nسم هر دو بازیکن مشخص شد.",parse_mode="HTML",reply_markup=poison_board(gid,st,reveal=True)); return
        st["opened"].add(cell)
        st["turn"]=opponent; st["deadline"]=time.time()+60
        context.application.create_task(poison_timeout(context.application,gid,st["deadline"]))
        await q.answer("خوراکی بود! نوبت حریف شد.")
        await q.edit_message_text(f"{FOOD_CE} خوراکی بود! خانه‌ی انتخابی باز شد.\nنوبت {_ulink(opponent,st['names'][opponent])} است؛ ۶۰ ثانیه فرصت داری.",parse_mode="HTML",reply_markup=poison_board(gid,st))


async def casino_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    bot = context.bot
    parts = q.data.split(":")
    act = parts[1]
    if act in ("pjoin", "po"):
        await poison_cb(update, context, parts); return
    if act in ("mx", "mc"):  # بازی مینِ نیمه‌کاره می‌تونه تموم/برداشت بشه
        await mines_cb(update, context, parts)
        return
    if act == "cx":          # برداشت موشک (بازی نیمه‌کاره)
        await crash_cb(update, context, parts)
        return
    if not CASINO_OPEN and act != "close":
        await q.answer(CASINO_CLOSED_TEXT, show_alert=True)
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
    if (act in ("pc", "pt", "ptc") or (act in ("g", "a", "custom") and len(parts) > 3 and parts[3] == "crash")) and not crash_allowed(user.id):
        await q.answer(CRASH_STAFF_MSG, show_alert=True)
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
        if game in ("mines", "crash") and mode != "1":
            await q.answer("این بازی فقط یک‌نفره است.", show_alert=True); return
        if game == "poison" and mode != "2":
            await q.answer("این بازی فقط دو نفره است.", show_alert=True); return
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
    elif act == "pc":
        await q.answer()
        context.user_data.pop("crash_target_custom", None)
        await start_crash(bot, context, chat_id, user, int(parts[3]), q=q)
    elif act == "pt":
        await q.answer()
        await start_crash(bot, context, chat_id, user, int(parts[3]), q=q, target=int(parts[4]) / 100)
    elif act == "ptc":
        await q.answer()
        context.user_data["crash_target_custom"] = int(parts[3])
        await edit_html(q, B(f"ضریب هدفت رو بفرست (عدد بین {CRASH_MIN_TARGET:g} تا {CRASH_MAX:g}). مثال: 2.5"),
                        InlineKeyboardMarkup([[btn("لغو", None, callback_data=f"cz:pc:{uid}:{parts[3]}")]]))
    elif act == "ps":
        await q.answer()
        bet = int(parts[3])
        if bet < CASINO_MIN_BET or bet > CASINO_MAX_BET:
            await edit_html(q, BP(f"مبلغ شرط باید بین {CASINO_MIN_BET:,} و {CASINO_MAX_BET:,} تومن باشه."), InlineKeyboardMarkup([[btn("برگشت", None, icon=EID_BACK, callback_data=f"cz:a:{uid}:slot:1")]]))
        else:
            text, kb = panel_slot_symbol(uid, bet)
            await edit_html(q, text, kb)
    elif act == "sp":
        await slot_choose(bot, context, q, user, int(parts[3]), int(parts[4]))
    elif act == "sy":
        await q.answer()
        await create_poison_table(bot, context, chat_id, user, int(parts[3]), q.message.message_id)
    elif act in ("td", "ts"):
        await q.answer()
        await create_table(bot, chat_id, user, "dice" if act == "td" else "slot", int(parts[3]), q=q, context=context)
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
    if await rift_left(user.id, creator) > 0:
        await q.answer("با صاحب این میز قهری! تا آشتی نمی‌تونی باهاش بازی کنی.", show_alert=True)
        return
    if key in done:
        await q.answer("این میز دیگه فعال نیست", show_alert=True)
        return
    if user.id in _busy(context):
        await q.answer("اول کاری که دستته (بازی یا دوئل) رو تموم کن 😅", show_alert=True)
        return
    if (left := _cd_left(context, user.id)) > 0:
        await q.answer(_cd_plain(left), show_alert=True)
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
    _cd_set(context, creator)
    _cd_set(context, user.id)
    if game == "dice":
        s1, s2 = v1, v2
        r1, r2 = f"{DICE_CE} {v1}", f"{DICE_CE} {v2}"
    else:
        s1, s2 = slot_points(v1), slot_points(v2)
        r1, r2 = f"🎰 {slot_text(v1)} ({s1} امتیاز)", f"🎰 {slot_text(v2)} ({s2} امتیاز)"
    if s1 == s2:
        await _give(creator, bet)
        await _give(user.id, bet)
        record_line = await update_group_record(chat_id, game, user, 0)
        result = "🤝 مساوی شد؛ پول هر دو نفر برگشت." + (f"\n{record_line}" if record_line else "")
    else:
        win_id, win_link = (creator, a) if s1 > s2 else (user.id, b)
        await _give(win_id, bet * 2)
        win_name = cname if win_id == creator else user.full_name
        rec_user = type("CasinoUser", (), {"id": win_id, "full_name": win_name})()
        record_line = await update_group_record(chat_id, game, rec_user, bet * 2)
        wrow = await get_user(win_id)
        result = f"{TROPHY_CE} برنده: {win_link}{win_lines(bet, bet * 2, wrow['balance'] if wrow else 0)}" + (f"\n{record_line}" if record_line else "")
    await edit_html(q, f"<b>{GAME_FA[game]} — نتیجه\n\n{a}: {r1}\n{b}: {r2}\n\n{result}</b>")
    delete_later(bot, chat_id, [m1.message_id, m2.message_id])
    await task_event(bot, chat_id, creator, "casino")
    await task_event(bot, chat_id, user.id, "casino", name=user.full_name)


async def casino_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """متن‌های کازینو رو تشخیص میده. True = مصرف شد."""
    msg, user = update.effective_message, update.effective_user
    t = norm_word(text).translate(_DIG)
    if not CASINO_OPEN:
        if re.fullmatch(r"کازینو|casino|(?:میز\s*)?(?:تاس|اسلات|مین|موشک|کراش)(?:\s+.+)?|(?:تاس|اسلات)\s*میز\s+.+", t, re.I):
            await send_html(
                context.bot, msg.chat_id,
                B(f'<tg-emoji emoji-id="{CASINO_CLOSED_EID}">🔒</tg-emoji> {CASINO_CLOSED_TEXT}'),
                reply_parameters=ReplyParameters(message_id=msg.message_id, allow_sending_without_reply=True),
            )
            return True
        context.user_data.pop("casino_custom_bet", None)
        context.user_data.pop("casino_custom_dice", None)
        return False
    bot, chat_id = context.bot, msg.chat_id
    rp = ReplyParameters(message_id=msg.message_id, allow_sending_without_reply=True)
    custom_choice = context.user_data.pop("casino_custom_bet", None)
    if custom_choice:
        custom_game, custom_mode = custom_choice
        amt = parse_amount(t)
        if amt < CASINO_MIN_BET or amt > CASINO_MAX_BET:
            context.user_data["casino_custom_bet"] = custom_choice
            await msg.reply_text(BP(f"مبلغ معتبر نیست؛ حداقل {CASINO_MIN_BET:,} و حداکثر {CASINO_MAX_BET:,} تومنه. دوباره بفرست."), parse_mode="HTML")
        elif custom_mode == "2" and custom_game == "poison":
            await create_poison_table(bot, context, chat_id, user, amt, msg.message_id)
        elif custom_mode == "2":
            await create_table(bot, chat_id, user, custom_game, amt, msg.message_id, context=context)
        elif custom_game == "mines":
            await start_mines(bot, context, chat_id, user, amt, reply_to=msg.message_id)
        elif custom_game == "crash":
            await start_crash(bot, context, chat_id, user, amt, reply_to=msg.message_id)
        elif custom_game == "dice":
            context.user_data["casino_custom_dice"] = amt
            await msg.reply_text(BP("حالا عدد حدسیت رو بفرست: ۱ تا ۶"), parse_mode="HTML")
        else:
            await send_slot_picker(bot, chat_id, user, amt, msg.message_id)
        return True
    ctb = context.user_data.get("crash_target_custom")
    if ctb:
        raw = t.replace("٫", ".").replace("،", ".").replace("×", "").replace("x", "").replace("X", "").strip()
        try:
            val = float(raw)
        except ValueError:
            val = None
        if val is None:
            if re.fullmatch(r"[\d.\s]+", raw or "x"):
                pass
            context.user_data.pop("crash_target_custom", None)
        elif val < CRASH_MIN_TARGET or val > CRASH_MAX:
            await msg.reply_text(BP(f"ضریب باید بین {CRASH_MIN_TARGET:g} و {CRASH_MAX:g} باشه. دوباره بفرست."), parse_mode="HTML"); return True
        else:
            context.user_data.pop("crash_target_custom", None)
            await start_crash(bot, context, chat_id, user, int(ctb), reply_to=msg.message_id, target=val)
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
            await create_table(bot, chat_id, user, game, amt, msg.message_id, context=context)
        return True
    m = re.fullmatch(r"تاس\s+([1-6])\s+(.+)", t)
    if m:
        amt = parse_amount(m.group(2))
        if amt <= 0:
            await msg.reply_text(BP("مبلغ رو نفهمیدم. مثلا: تاس 3 100هزار"), parse_mode="HTML")
        else:
            await play_single(bot, context, chat_id, user, "dice", amt, int(m.group(1)), msg.message_id)
        return True
    m = re.fullmatch(r"(?:موشک|کراش)\s+(.+?)\s+(?:[x×]\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*[x×]|(\d+\.\d+))", t)
    if m:
        amt = parse_amount(m.group(1)); tg = next((float(g) for g in m.groups()[1:] if g), None)
        if amt > 0 and tg is not None: await start_crash(bot, context, chat_id, user, amt, reply_to=msg.message_id, target=tg)
        else: await msg.reply_text(BP("مبلغ رو نفهمیدم. مثال: موشک 100هزار x2.5"), parse_mode="HTML")
        return True
    m = re.fullmatch(r"(?:موشک|کراش)\s+(.+)", t)
    if m:
        amt = parse_amount(m.group(1))
        if amt > 0: await start_crash(bot, context, chat_id, user, amt, reply_to=msg.message_id)
        else: await msg.reply_text(BP("مبلغ رو نفهمیدم. مثال: موشک 100هزار"), parse_mode="HTML")
        return True
    if re.fullmatch(r"موشک|کراش", t):
        if not crash_allowed(user.id):
            await send_html(bot, chat_id, BP(CRASH_STAFF_MSG), reply_parameters=rp)
            return True
        ptext, kb = panel_amounts(user.id, "crash", "1")
        await send_html(bot, chat_id, ptext, reply_markup=kb, reply_parameters=rp)
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
            await send_slot_picker(bot, chat_id, user, amt, msg.message_id)
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


def _duel_busy(context, uid: int) -> bool:
    d = context.application.bot_data.setdefault("duel_busy", {})
    ent = d.get(uid)
    if ent and ent[0] < time.time():
        d.pop(uid, None); ent = None
    return ent is not None


def _duel_hold(context, frm: int, to: int, secs: float):
    d = context.application.bot_data.setdefault("duel_busy", {})
    for u in (frm, to):
        d[u] = (time.time() + secs, (frm, to))


def _duel_release(context, frm: int, to: int):
    d = context.application.bot_data.setdefault("duel_busy", {})
    for u in (frm, to):
        ent = d.get(u)
        if ent and ent[1] == (frm, to):
            d.pop(u, None)
    busy = _busy(context)
    busy.discard(frm); busy.discard(to)


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
    if (rl := await rift_left(user.id, target.id)) > 0:
        await msg.reply_text(rift_block_html(rl), parse_mode="HTML")
        return
    if _duel_busy(context, user.id):
        await msg.reply_text(BP("تو الان یه دوئل در جریان یا منتظر جواب داری؛ اول اون تموم شه 😅"), parse_mode="HTML")
        return
    if _duel_busy(context, target.id):
        await msg.reply_text(BP("حریفت الان درگیر یه دوئل دیگه‌ست؛ بعداً امتحان کن 😅"), parse_mode="HTML")
        return
    if user.id in _busy(context):
        await msg.reply_text(BP("اول بازی کازینوت رو تموم کن 😅"), parse_mode="HTML")
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
    _duel_hold(context, user.id, target.id, DUEL_TTL + 30)
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
    try:
        await _duel_run(update, context, q, frm, to, act, key, done)
    finally:
        _duel_release(context, frm, to)


async def _duel_run(update, context, q, frm, to, act, key, done):
    if act == "no":
        await q.answer()
        await q.edit_message_text("❌ دوئل رد شد.")
        return
    if await rift_left(frm, to) > 0:
        await q.answer("با هم قهرید!", show_alert=True)
        await q.edit_message_text("💔 باهم قهرید؛ دوئل لغو شد.")
        return
    try:
        age = time.time() - q.message.date.timestamp()
    except Exception:
        age = 0
    if age > DUEL_TTL:
        await q.answer("دعوت منقضی شده", show_alert=True)
        await q.edit_message_text("⌛ دعوت دوئل منقضی شد.")
        return
    busy = _busy(context)
    if frm in busy or to in busy:
        done.discard(key)
        await q.answer("یکی از شما الان درگیر بازیه؛ بعد از تموم شدنش قبول کن.", show_alert=True)
        _duel_hold(context, frm, to, DUEL_TTL)
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
    busy.add(frm); busy.add(to)
    _duel_hold(context, frm, to, DUEL_WAIT + 60)
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
    await task_event(context.bot, q.message.chat_id, win_id, "duel_win")


# ───────────────────────── قرعه‌کشی (ساخت از پنل پشتیبانی) ─────────────────────────
# پشتیبان از /admin ← «🎁 قرعه‌کشی» یه قرعه‌کشی می‌سازه: نام، مدت، کانال‌ها، جایزه، ظرفیت، تعداد برنده، تعداد دایره‌های کپچا.
# کاربر با دکمه‌ی پست وارد ربات میشه ← عضویت تو کانال‌ها ← کپچای دایره‌ای ← ثبت‌نام. هر نفری که با لینک اختصاصی یکی وارد بشه
# برای دعوت‌کننده ۱ تیکت اضافه می‌شه؛ اگه اون نفر از کانال‌ها لفت بده تیکت حذف میشه. برنده‌ها کسایی‌ان که بیشترین تیکت رو دارن (تساوی: زودتر ثبت‌نام‌کرده).
GW_CHECK_BATCH = 120          # هر دور چند نفر از ثبت‌نامی‌ها برای لفت‌دادن چک بشن
GW_LOOP_SECS = 30
GW_CAPTCHA_TTL = 180
GW_CAPTCHA_TRIES = 3
GW_COLORS = [
    ("قرمز", (230, 57, 70)), ("آبی", (52, 120, 246)), ("سبز", (46, 184, 92)), ("زرد", (250, 204, 21)),
    ("بنفش", (155, 89, 182)), ("نارنجی", (255, 140, 0)), ("صورتی", (255, 105, 180)), ("فیروزه‌ای", (20, 190, 200)),
]
_GW_BOT_UN: str | None = None
GW_TICKET_EID = "5438521232121149744"   # تیکت
GW_WIN_EID = "5832692422647226240"      # برنده
GW_TEDDY_EID = "5206502842478638898"    # جایزه‌ی تدی
GW_TICKET_CE = f'<tg-emoji emoji-id="{GW_TICKET_EID}">🎟</tg-emoji>'
GW_WIN_CE = f'<tg-emoji emoji-id="{GW_WIN_EID}">🏆</tg-emoji>'
GW_TEDDY_CE = f'<tg-emoji emoji-id="{GW_TEDDY_EID}">🧸</tg-emoji>'


def gw_prize_ce(prize: str) -> str:
    p = (prize or "").lower()
    return GW_TEDDY_CE if ("تدی" in p or "teddy" in p) else TROPHY_CE


async def _gw_bot_username(bot) -> str:
    global _GW_BOT_UN
    if not _GW_BOT_UN:
        _GW_BOT_UN = (await bot.get_me()).username or ""
    return _GW_BOT_UN


def _gw_fmt_time(ts: float) -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.fromtimestamp(ts, ZoneInfo("Asia/Tehran")).strftime("%Y/%m/%d %H:%M")
    except Exception:
        return datetime.fromtimestamp(ts).strftime("%Y/%m/%d %H:%M")


def _gw_fmt_dur(sec: float) -> str:
    sec = int(max(sec, 0))
    d, r = divmod(sec, 86400)
    h, r = divmod(r, 3600)
    m = r // 60
    parts = []
    if d: parts.append(f"{d} روز")
    if h: parts.append(f"{h} ساعت")
    if m or not parts: parts.append(f"{m} دقیقه")
    return " و ".join(parts)


def _gw_channels(g) -> list[str]:
    return [c for c in (g["channels"] or "").split(",") if c]


async def gw_missing(bot, channels: list[str], uid: int):
    """لیست کانال‌هایی که کاربر توشون نیست؛ None = بررسی ممکن نبود (ربات ادمین نیست)."""
    missing = []
    for ch in channels:
        try:
            m = await bot.get_chat_member(ch, uid)
        except Exception:
            log.warning("giveaway member check failed for %s", ch, exc_info=True)
            return None
        st = str(getattr(m, "status", ""))
        ok = st in ("creator", "administrator", "member") or (st == "restricted" and getattr(m, "is_member", False))
        if not ok:
            missing.append(ch)
    return missing


async def gw_has_left(bot, channels: list[str], uid: int) -> bool:
    """فقط وقتی True که مطمئنیم از یکی از کانال‌ها خارج شده (خطا = نه)."""
    for ch in channels:
        try:
            m = await bot.get_chat_member(ch, uid)
        except Exception:
            return False
        st = str(getattr(m, "status", ""))
        if st in ("left", "kicked") or (st == "restricted" and not getattr(m, "is_member", True)):
            return True
    return False


async def gw_tickets(gid: int, uid: int):
    """(تعداد تیکت، تعداد دعوت‌های فعال). تیکت = ۱ (خودش) + دعوت‌های فعال."""
    own = await pool.fetchval("SELECT 1 FROM giveaway_entries WHERE gid=$1 AND user_id=$2 AND status='active'", gid, uid)
    inv = await pool.fetchval("SELECT count(*) FROM giveaway_entries WHERE gid=$1 AND inviter_id=$2 AND status='active'", gid, uid) or 0
    return ((1 + inv) if own else 0), inv


async def gw_top(gid: int, limit: int = 10):
    return await pool.fetch(
        "SELECT e.user_id, u.name, 1 + coalesce(c.n, 0) AS tickets, coalesce(c.n, 0) AS invited "
        "FROM giveaway_entries e LEFT JOIN users u ON u.user_id = e.user_id "
        "LEFT JOIN (SELECT inviter_id, count(*) AS n FROM giveaway_entries WHERE gid=$1 AND status='active' AND inviter_id IS NOT NULL GROUP BY inviter_id) c "
        "ON c.inviter_id = e.user_id WHERE e.gid=$1 AND e.status='active' ORDER BY tickets DESC, e.joined_at LIMIT $2", gid, limit)


async def gw_top_text(gid: int) -> str:
    rows = await gw_top(gid)
    if not rows:
        return "🏆 هنوز کسی ثبت‌نام نکرده."
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"{medals[i] if i < 3 else str(i + 1) + '.'} {_ulink(r['user_id'], r['name'] or 'کاربر')} — {r['tickets']} {GW_TICKET_CE} ({r['invited']} دعوت)" for i, r in enumerate(rows)]
    return f"{GW_WIN_CE} <b>برترین‌های قرعه‌کشی</b>\n\n" + "\n".join(lines)


def gw_post(g, bot_un: str, entries: int | None = None, plain: bool = False):
    """متن و دکمه‌ی پستی که تو گپ‌ها/کانال‌ها/پیوی‌ها فرستاده میشه."""
    chs = _gw_channels(g)
    cap = f"{entries:,}/{g['max_users']:,}" if (g["max_users"] and entries is not None) else (f"{g['max_users']:,} نفر" if g["max_users"] else "نامحدود")
    text = (f"🎁 <b>قرعه‌کشی: {html.escape(g['name'])}</b>\n\n"
            f"{gw_prize_ce(g['prize'])} جایزه: <b>{html.escape(g['prize'])}</b>\n"
            f"{GW_WIN_CE} تعداد برنده: <b>{g['winners']}</b>\n"
            f"{REF_CE} ظرفیت: <b>{cap}</b>\n"
            f"{HG_CE} پایان: <b>{_gw_fmt_time(g['ends_at'])}</b> (ساعت تهران)\n")
    if chs:
        text += "\n📢 شرط شرکت: عضویت در " + "، ".join(chs) + "\n"
    text += (f"\n{GW_TICKET_CE} با لینک اختصاصی‌ت دوستات رو دعوت کن؛ هر نفری که وارد شه یه تیکت بیشتر برات می‌شه (اگه از کانال‌ها لفت بده تیکتش حذف می‌شه)."
             f"\n{GW_WIN_CE} هر کس بیشترین تیکت رو جمع کنه برنده‌ست!")
    if plain:   # کانال‌ها ایموجی پرمیوم/آیکن دکمه رو قبول نمی‌کنن؛ نسخه‌ی ساده
        text = re.sub(r'<tg-emoji emoji-id="\d+">(.*?)</tg-emoji>', r"\1", text)
        rows = [[InlineKeyboardButton("🎁 شرکت در قرعه‌کشی", url=f"https://t.me/{bot_un}?start=gw_{g['id']}")]]
        if g["top_public"]:
            rows.append([InlineKeyboardButton("🏆 لیست برترین‌ها", url=f"https://t.me/{bot_un}?start=gwtop_{g['id']}")])
        return text, InlineKeyboardMarkup(rows)
    rows = [[btn("🎁 شرکت در قرعه‌کشی", "success", url=f"https://t.me/{bot_un}?start=gw_{g['id']}")]]
    if g["top_public"]:
        rows.append([btn("لیست برترین‌ها", "primary", icon=GW_WIN_EID, url=f"https://t.me/{bot_un}?start=gwtop_{g['id']}")])
    return text, InlineKeyboardMarkup(rows)


# ───── کپچای دایره‌ای ─────
def gw_captcha_image(n: int, target_idx: int, colors: list):
    """تصویر n دایره‌ی رنگی با شماره؛ جواب = شماره‌ی دایره‌ای که رنگش target هست."""
    W, H = 420, 220 if n <= 4 else 300
    im = Image.new("RGB", (W, H), (245, 247, 250))
    d = ImageDraw.Draw(im)
    for _ in range(6):   # خط‌های مزاحم
        d.line([(random.randint(0, W), random.randint(0, H)), (random.randint(0, W), random.randint(0, H))],
               fill=(random.randint(170, 220),) * 3, width=2)
    per_row = n if n <= 4 else (n + 1) // 2
    rows = 1 if n <= 4 else 2
    r = min(46, (W // per_row) // 2 - 6)
    f = load_font(int(r * 1.0))
    for i in range(n):
        row, col = divmod(i, per_row)
        cnt = per_row if row < rows - 1 else n - per_row * (rows - 1)
        cx = (W / (cnt + 1)) * (col + 1) + random.randint(-6, 6)
        cy = (H / (rows + 1)) * (row + 1) + random.randint(-8, 8)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=colors[i][1], outline=(40, 40, 40), width=3)
        t = str(i + 1)
        d.text((cx, cy), t, font=f, fill=(255, 255, 255), anchor="mm", stroke_width=3, stroke_fill=(30, 30, 30))
    out = BytesIO(); out.name = "captcha.jpg"
    im.save(out, "JPEG", quality=88)
    out.seek(0)
    return out


async def gw_send_captcha(bot, chat_id: int, context, g, uid: int, inviter: int, tries: int = 0):
    n = max(3, min(8, int(g["circles"])))
    colors = random.sample(GW_COLORS, n)
    ans = random.randrange(n)
    context.application.bot_data.setdefault("gw_cap", {})[uid] = {
        "gid": g["id"], "ans": ans, "inv": inviter, "exp": time.time() + GW_CAPTCHA_TTL, "tries": tries}
    img = await asyncio.to_thread(gw_captcha_image, n, ans, colors)
    kb = InlineKeyboardMarkup([[btn(str(i + 1), "primary", callback_data=f"gw:c:{g['id']}:{i}") for i in range(n)]])
    await bot.send_photo(
        chat_id, photo=img, parse_mode="HTML", reply_markup=kb,
        caption=f"<b>🤖 برای اینکه مطمئن شیم ربات نیستی:\n\nروی دایره‌ی «{colors[ans][0]}» بزن (شماره‌ی همون دایره رو انتخاب کن).</b>")


# ───── ثبت‌نام ─────
async def gw_join(g_id: int, uid: int, inviter: int):
    """('ok', inviter_ok) | ('already',) | ('full',) | ('closed',)"""
    async with pool.acquire() as c:
        async with c.transaction():
            g = await c.fetchrow("SELECT id, status, max_users FROM giveaways WHERE id=$1 FOR UPDATE", g_id)
            if not g or g["status"] != "active":
                return ("closed",)
            ex = await c.fetchrow("SELECT status FROM giveaway_entries WHERE gid=$1 AND user_id=$2", g_id, uid)
            if ex and ex["status"] == "active":
                return ("already",)
            if g["max_users"]:
                n = await c.fetchval("SELECT count(*) FROM giveaway_entries WHERE gid=$1 AND status='active'", g_id)
                if n >= g["max_users"]:
                    return ("full",)
            now = time.time()
            inv_ok = None
            if ex:   # قبلاً لفت داده بود؛ دوباره فعال میشه و دعوت‌کننده‌ی قبلی‌ش همون می‌مونه
                await c.execute("UPDATE giveaway_entries SET status='active', joined_at=$3, last_check=$3 WHERE gid=$1 AND user_id=$2", g_id, uid, now)
                inv_ok = await c.fetchval("SELECT inviter_id FROM giveaway_entries WHERE gid=$1 AND user_id=$2", g_id, uid)
            else:
                if inviter and inviter != uid and await c.fetchval(
                        "SELECT 1 FROM giveaway_entries WHERE gid=$1 AND user_id=$2 AND status='active'", g_id, inviter):
                    inv_ok = inviter
                await c.execute(
                    "INSERT INTO giveaway_entries (gid, user_id, inviter_id, status, joined_at, last_check) VALUES ($1,$2,$3,'active',$4,$4)",
                    g_id, uid, inv_ok, now)
            return ("ok", inv_ok)


async def gw_status_view(bot, g, uid: int):
    tickets, invited = await gw_tickets(g["id"], uid)
    un = await _gw_bot_username(bot)
    link = f"https://t.me/{un}?start=gw_{g['id']}_{uid}"
    from urllib.parse import quote
    share = f"https://t.me/share/url?url={quote(link)}&text={quote('🎁 تو قرعه‌کشی «' + g['name'] + '» شرکت کن!')}"
    text = (f"✅ <b>تو تو قرعه‌کشی «{html.escape(g['name'])}» ثبت‌نام شدی!</b>\n\n"
            f"{GW_TICKET_CE} تیکت‌های تو: <b>{tickets}</b> (دعوت‌های فعال: {invited})\n"
            f"{HG_CE} پایان: {_gw_fmt_time(g['ends_at'])} (ساعت تهران)\n"
            f"{GW_WIN_CE} هر کس بیشترین تیکت رو داشته باشه برنده‌ست.\n\n"
            f"🔗 لینک اختصاصی تو:\n<code>{link}</code>\n\n"
            "هر نفری که با این لینک وارد بشه، کپچا رو رد کنه و تو کانال‌ها عضو بمونه، یه تیکت برات اضافه می‌کنه. اگه لفت بده تیکتش حذف می‌شه.")
    rows = [[btn("📤 اشتراک‌گذاری لینک", "success", url=share)]]
    if g["top_public"]:
        rows.append([btn("لیست برترین‌ها", "primary", icon=GW_WIN_EID, callback_data=f"gw:top:{g['id']}")])
    rows.append([btn("🔄 تازه‌سازی", None, callback_data=f"gw:me:{g['id']}")])
    return text, InlineKeyboardMarkup(rows)


async def gw_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start gw_<id>[_<inviter>] یا /start gwtop_<id>"""
    msg, user, bot = update.effective_message, update.effective_user, context.bot
    payload = (msg.text or "").split(" ", 1)[1].strip() if " " in (msg.text or "") else ""
    m = re.fullmatch(r"gw(top)?_(\d+)(?:_(\d+))?", payload)
    if not m:
        return
    is_top, gid, inviter = bool(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
    g = await pool.fetchrow("SELECT * FROM giveaways WHERE id=$1", gid)
    if not g or g["status"] == "draft":
        await msg.reply_text("این قرعه‌کشی وجود نداره.")
        return
    await upsert_user(user.id, user.full_name)
    if is_top:
        if g["top_public"] or has_perm(user.id, "giveaway"):
            await msg.reply_text(await gw_top_text(gid), parse_mode="HTML")
        else:
            await msg.reply_text("لیست برترین‌ها برای این قرعه‌کشی فعال نیست.")
        return
    if g["status"] != "active":
        winners = (g["winner_ids"] or "").split(",") if g["winner_ids"] else []
        await msg.reply_text("🏁 این قرعه‌کشی تموم شده." + (f"\nبرنده‌ها: {len(winners)} نفر {GW_WIN_CE}" if winners else ""))
        return
    if time.time() >= g["ends_at"]:
        await msg.reply_text("⌛ زمان این قرعه‌کشی تموم شده؛ نتیجه به‌زودی اعلام می‌شه.")
        return
    ex = await pool.fetchval("SELECT status FROM giveaway_entries WHERE gid=$1 AND user_id=$2", gid, user.id)
    if ex == "active":
        text, kb = await gw_status_view(bot, g, user.id)
        await msg.reply_text(text, parse_mode="HTML", reply_markup=kb)
        return
    if g["max_users"] and (await pool.fetchval("SELECT count(*) FROM giveaway_entries WHERE gid=$1 AND status='active'", gid)) >= g["max_users"]:
        await msg.reply_text("😔 ظرفیت این قرعه‌کشی تکمیل شده.")
        return
    await gw_check_and_captcha(bot, context, msg.chat_id, g, user.id, inviter)


async def gw_check_and_captcha(bot, context, chat_id: int, g, uid: int, inviter: int):
    chs = _gw_channels(g)
    missing = await gw_missing(bot, chs, uid) if chs else []
    if missing is None:
        await bot.send_message(chat_id, "⚠️ الان نمی‌تونم عضویتت رو بررسی کنم (ربات تو کانال‌ها ادمین نیست). به پشتیبانی خبر بده.")
        return
    if missing:
        rows = [[btn(f"📢 عضویت در {ch}", "primary", url=f"https://t.me/{ch.lstrip('@')}")] for ch in missing if ch.startswith("@")]
        rows.append([btn("✅ عضو شدم", "success", callback_data=f"gw:chk:{g['id']}:{inviter}")])
        await bot.send_message(chat_id, "📢 برای شرکت تو قرعه‌کشی باید تو این کانال‌ها عضو باشی:\n" + "\n".join(missing)
                               + "\n\nبعد از عضویت دکمه‌ی «عضو شدم» رو بزن.", reply_markup=InlineKeyboardMarkup(rows))
        return
    await gw_send_captcha(bot, chat_id, context, g, uid, inviter)


async def gw_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    user, bot = q.from_user, context.bot
    parts = q.data.split(":")
    act = parts[1]
    try:
        gid = int(parts[2])
    except (IndexError, ValueError):
        await q.answer(); return
    g = await pool.fetchrow("SELECT * FROM giveaways WHERE id=$1", gid)
    if not g or g["status"] == "draft":
        await q.answer("این قرعه‌کشی وجود نداره.", show_alert=True); return
    if act == "top":
        if not (g["top_public"] or has_perm(user.id, "giveaway")):
            await q.answer("لیست برترین‌ها برای این قرعه‌کشی فعال نیست.", show_alert=True); return
        await q.answer()
        await bot.send_message(q.message.chat_id, await gw_top_text(gid), parse_mode="HTML")
        return
    if act == "me":
        ex = await pool.fetchval("SELECT status FROM giveaway_entries WHERE gid=$1 AND user_id=$2", gid, user.id)
        if ex != "active":
            await q.answer("تو تو این قرعه‌کشی ثبت‌نام نیستی.", show_alert=True); return
        text, kb = await gw_status_view(bot, g, user.id)
        await q.answer("تازه شد ✅")
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass
        return
    if g["status"] != "active" or time.time() >= g["ends_at"]:
        await q.answer("این قرعه‌کشی تموم شده.", show_alert=True); return
    if act == "chk":
        inviter = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 0
        chs = _gw_channels(g)
        missing = await gw_missing(bot, chs, user.id) if chs else []
        if missing is None:
            await q.answer("الان نمی‌تونم عضویتت رو بررسی کنم. به پشتیبانی خبر بده.", show_alert=True); return
        if missing:
            await q.answer("❌ هنوز تو همه‌ی کانال‌ها عضو نشدی!", show_alert=True); return
        await q.answer("✅ عضویتت تایید شد")
        try:
            await q.message.delete()
        except Exception:
            pass
        await gw_send_captcha(bot, q.message.chat_id, context, g, user.id, inviter)
        return
    if act == "c":
        cap = context.application.bot_data.setdefault("gw_cap", {}).get(user.id)
        if not cap or cap["gid"] != gid or cap["exp"] < time.time():
            context.application.bot_data.get("gw_cap", {}).pop(user.id, None)
            await q.answer("کپچا منقضی شده؛ دوباره از لینک وارد شو.", show_alert=True)
            try: await q.message.delete()
            except Exception: pass
            return
        pick = int(parts[3])
        if pick != cap["ans"]:
            cap["tries"] += 1
            if cap["tries"] >= GW_CAPTCHA_TRIES:
                context.application.bot_data["gw_cap"].pop(user.id, None)
                await q.answer("❌ زیاد اشتباه زدی! بعداً دوباره از لینک امتحان کن.", show_alert=True)
                try: await q.message.delete()
                except Exception: pass
                return
            await q.answer(f"❌ اشتباه بود! یه کپچای جدید فرستادم ({GW_CAPTCHA_TRIES - cap['tries']} فرصت مونده).", show_alert=True)
            try: await q.message.delete()
            except Exception: pass
            await gw_send_captcha(bot, q.message.chat_id, context, g, user.id, cap["inv"], cap["tries"])
            return
        context.application.bot_data["gw_cap"].pop(user.id, None)
        # عضویت دوباره چک میشه (بین کپچا و ثبت ممکنه لفت داده باشه)
        chs = _gw_channels(g)
        if chs and await gw_has_left(bot, chs, user.id):
            await q.answer("از یکی از کانال‌ها خارج شدی! دوباره عضو شو.", show_alert=True); return
        await upsert_user(user.id, user.full_name)
        res = await gw_join(gid, user.id, cap["inv"])
        if res[0] == "full":
            await q.answer("😔 ظرفیت تکمیل شد.", show_alert=True)
            try: await q.message.delete()
            except Exception: pass
            return
        if res[0] == "closed":
            await q.answer("این قرعه‌کشی تموم شده.", show_alert=True); return
        await q.answer("✅ ثبت‌نام شدی!")
        try: await q.message.delete()
        except Exception: pass
        text, kb = await gw_status_view(bot, g, user.id)
        await bot.send_message(q.message.chat_id, text, parse_mode="HTML", reply_markup=kb)
        if res[0] == "ok" and res[1]:
            t, _ = await gw_tickets(gid, res[1])
            try:
                await bot.send_message(res[1], f"{GW_TICKET_CE} {_ulink(user.id, user.full_name)} با لینک تو وارد قرعه‌کشی «{html.escape(g['name'])}» شد!\nتیکت‌های تو: <b>{t}</b>", parse_mode="HTML")
            except Exception:
                pass


# ───── پایان، قرعه و چک لفت‌دادن ─────
async def gw_sweep_left(bot, g, only_old: float | None = None, limit: int | None = None):
    """ثبت‌نامی‌هایی که از کانال‌ها خارج شدن رو 'left' می‌کنه (تیکتشون و تیکتِ دعوت‌کننده حذف میشه)."""
    chs = _gw_channels(g)
    if not chs:
        return
    q = "SELECT user_id, inviter_id FROM giveaway_entries WHERE gid=$1 AND status='active'"
    args = [g["id"]]
    if only_old is not None:
        q += " AND last_check < $2"; args.append(only_old)
    q += " ORDER BY last_check"
    if limit:
        q += f" LIMIT {int(limit)}"
    rows = await pool.fetch(q, *args)
    for r in rows:
        uid = r["user_id"]
        try:
            if await gw_has_left(bot, chs, uid):
                ok = await pool.fetchval("UPDATE giveaway_entries SET status='left' WHERE gid=$1 AND user_id=$2 AND status='active' RETURNING 1", g["id"], uid)
                if ok:
                    try:
                        await bot.send_message(uid, f"⚠️ از کانال‌های قرعه‌کشی «{html.escape(g['name'])}» خارج شدی و تیکتت حذف شد. برای برگشتن دوباره از لینک وارد شو.", parse_mode="HTML")
                    except Exception:
                        pass
                    inv = r["inviter_id"]
                    if inv:
                        t, _ = await gw_tickets(g["id"], inv)
                        try:
                            await bot.send_message(inv, f"💔 یکی از دعوتی‌هات از کانال‌ها لفت داد و یه تیکتت حذف شد. تیکت‌های فعلی تو «{html.escape(g['name'])}»: <b>{t}</b>", parse_mode="HTML")
                        except Exception:
                            pass
            else:
                await pool.execute("UPDATE giveaway_entries SET last_check=$3 WHERE gid=$1 AND user_id=$2", g["id"], uid, time.time())
        except Exception:
            log.warning("giveaway sweep failed", exc_info=True)
        await asyncio.sleep(0.05)


async def gw_draw(bot, g) -> list[int]:
    """برنده‌ها = بیشترین تیکت‌ها (تساوی: هر کی زودتر ثبت‌نام کرده)."""
    rows = await pool.fetch(
        "SELECT e.user_id, 1 + coalesce(c.n, 0) AS t FROM giveaway_entries e "
        "LEFT JOIN (SELECT inviter_id, count(*) AS n FROM giveaway_entries WHERE gid=$1 AND status='active' AND inviter_id IS NOT NULL GROUP BY inviter_id) c "
        "ON c.inviter_id = e.user_id WHERE e.gid=$1 AND e.status='active' ORDER BY t DESC, e.joined_at, e.user_id", g["id"])
    winners: list[int] = []
    chs = _gw_channels(g)
    for r in rows:
        if len(winners) >= g["winners"]:
            break
        uid = r["user_id"]
        if chs and await gw_has_left(bot, chs, uid):   # برنده باید هنوز تو کانال‌ها باشه
            await pool.execute("UPDATE giveaway_entries SET status='left' WHERE gid=$1 AND user_id=$2", g["id"], uid)
            continue
        winners.append(uid)
    return winners


async def gw_finish(bot, gid: int, by_staff: int | None = None):
    won = await pool.fetchval("UPDATE giveaways SET status='ended' WHERE id=$1 AND status='active' RETURNING 1", gid)
    if not won:
        return
    g = await pool.fetchrow("SELECT * FROM giveaways WHERE id=$1", gid)
    try:
        await gw_sweep_left(bot, g, only_old=time.time() - 600)
        winners = await gw_draw(bot, g)
    except Exception:
        log.exception("giveaway draw failed")
        winners = []
    await pool.execute("UPDATE giveaways SET winner_ids=$2 WHERE id=$1", gid, ",".join(str(w) for w in winners))
    names = {r["user_id"]: r["name"] for r in await pool.fetch("SELECT user_id, name FROM users WHERE user_id = ANY($1::bigint[])", winners)} if winners else {}
    if winners:
        tk = {w: (await gw_tickets(gid, w))[0] for w in winners}
        lines = "\n".join(f"{GW_WIN_CE} {i + 1}. {_ulink(w, names.get(w) or 'کاربر')} — {tk[w]} {GW_TICKET_CE}" for i, w in enumerate(winners))
        result = (f"🏁 <b>قرعه‌کشی «{html.escape(g['name'])}» تموم شد!</b>\n\n{gw_prize_ce(g['prize'])} جایزه: <b>{html.escape(g['prize'])}</b>\n\n"
                  f"{GW_WIN_CE} برنده‌ها (بیشترین تیکت):\n{lines}\n\nمبارک باشه! برای دریافت جایزه با پشتیبانی هماهنگ کنید.")
    else:
        result = f"🏁 <b>قرعه‌کشی «{html.escape(g['name'])}» تموم شد!</b>\n\nمتأسفانه شرکت‌کننده‌ی معتبری نبود و برنده‌ای انتخاب نشد."
    # پست‌های قبلی: دکمه‌ها برداشته میشه و نتیجه پایینشون میاد
    posts = await pool.fetch("SELECT chat_id, message_id FROM giveaway_posts WHERE gid=$1", gid)
    for p in posts:
        try:
            await bot.edit_message_reply_markup(p["chat_id"], p["message_id"], reply_markup=None)
        except Exception:
            pass
        try:   # پست پین‌شده‌ی قرعه‌کشی (اگه پین شده باشه) برداشته میشه
            await bot.unpin_chat_message(p["chat_id"], message_id=p["message_id"])
        except Exception:
            pass
        try:
            await bot.send_message(p["chat_id"], result, parse_mode="HTML",
                                   reply_parameters=ReplyParameters(message_id=p["message_id"], allow_sending_without_reply=True))
        except Exception:
            log.warning("giveaway result post failed", exc_info=True)
        await asyncio.sleep(0.06)
    for w in winners:
        try:
            await bot.send_message(w, f"{GW_WIN_CE} تبریک! تو برنده‌ی قرعه‌کشی «{html.escape(g['name'])}» شدی!\n{gw_prize_ce(g['prize'])} جایزه: <b>{html.escape(g['prize'])}</b>\nبرای دریافت جایزه به پشتیبانی پیام بده.", parse_mode="HTML")
        except Exception:
            pass
    others = [r["user_id"] for r in await pool.fetch("SELECT user_id FROM giveaway_entries WHERE gid=$1 AND status='active'", gid) if r["user_id"] not in winners]
    for uid in others:
        try:
            await bot.send_message(uid, result, parse_mode="HTML")
        except Exception:
            pass
        await asyncio.sleep(0.06)
    if g["created_by"]:
        try:
            await bot.send_message(g["created_by"], result, parse_mode="HTML")
        except Exception:
            pass


async def giveaway_loop(app):
    await asyncio.sleep(10)
    while True:
        try:
            for r in await pool.fetch("SELECT id FROM giveaways WHERE status='active' AND ends_at <= $1", time.time()):
                await gw_finish(app.bot, r["id"])
            for g in await pool.fetch("SELECT * FROM giveaways WHERE status='active'"):
                await gw_sweep_left(app.bot, g, only_old=time.time() - 120, limit=GW_CHECK_BATCH)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("giveaway loop failed")
        await asyncio.sleep(GW_LOOP_SECS)


# ───── انتشار ─────
async def gw_send_to(bot, gid: int, admin_chat: int, kinds: set, resend: bool = False):
    """kinds ⊆ {"channels", "groups", "users"}. resend=True: فقط به مقصدهایی که هنوز پست بهشون نرفته."""
    g = await pool.fetchrow("SELECT * FROM giveaways WHERE id=$1", gid)
    if not g:
        return
    un = await _gw_bot_username(bot)
    variants = [gw_post(g, un), gw_post(g, un, plain=True)]
    done = {r["chat_id"] for r in await pool.fetch("SELECT chat_id FROM giveaway_sent WHERE gid=$1", gid)} if resend else set()
    targets: list[tuple] = []   # (chat_id, ثبت‌شود برای پایان؟، گپ هست؟ → پین بشه، نوع)
    if "channels" in kinds:
        targets += [(c, True, False, "channels") for c in _gw_channels(g)]
    if "groups" in kinds:
        targets += [(r["chat_id"], True, True, "groups") for r in await pool.fetch("SELECT chat_id FROM chats")]
    if "users" in kinds:
        targets += [(r["user_id"], False, False, "users") for r in await pool.fetch("SELECT user_id FROM users WHERE started")]
    stats = {k: {"ok": 0, "fail": 0, "skip": 0} for k in ("channels", "groups", "users")}
    errors: list[str] = []
    pinned = 0
    seen = set()
    hard = ("chat not found", "not enough rights", "need administrator", "have no rights", "not a member", "forbidden", "kicked")
    for cid, keep, is_group, kind in targets:
        if cid in seen:
            continue
        seen.add(cid)
        st = stats[kind]
        if resend:
            real = cid
            if isinstance(cid, str):   # @یوزرنیم کانال → آیدی عددی
                try:
                    real = (await bot.get_chat(cid)).id
                except Exception:
                    real = None
            if real in done:
                st["skip"] += 1
                continue
        sent, err, vi = None, None, 0
        for _ in range(4):
            text, kb = variants[vi]
            try:
                sent = await bot.send_message(cid, text, parse_mode="HTML", reply_markup=kb)
                break
            except RetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
            except BadRequest as e:
                err = e
                if vi == 0 and not any(h in str(e).lower() for h in hard):
                    vi = 1   # احتمالاً ایموجی پرمیوم/آیکن دکمه؛ با نسخه‌ی ساده دوباره تلاش میشه
                    continue
                break
            except Exception as e:
                err = e
                break
        if sent is None:
            st["fail"] += 1
            if kind != "users" and len(errors) < 10:
                errors.append(f"• {cid}: {err}")
            await asyncio.sleep(0.06)
            continue
        st["ok"] += 1
        try:
            if keep:
                await pool.execute("INSERT INTO giveaway_posts (gid, chat_id, message_id) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING", gid, sent.chat_id, sent.message_id)
            await pool.execute("INSERT INTO giveaway_sent (gid, chat_id) VALUES ($1,$2) ON CONFLICT DO NOTHING", gid, sent.chat_id)
        except Exception:
            log.warning("giveaway post save failed", exc_info=True)
        if is_group:   # اگه ربات تو اون گپ ادمینه و دسترسی پین داره، همون لحظه پین میشه
            try:
                await bot.pin_chat_message(sent.chat_id, sent.message_id, disable_notification=False)
                pinned += 1
            except Exception:
                pass
        await asyncio.sleep(0.06)
    names = {"channels": "کانال‌ها", "groups": "گپ‌ها", "users": "پیوی کاربرها"}
    lines = [(f"✅ قرعه‌کشی «{g['name']}» منتشر شد." if not resend else f"📤 ارسال قرعه‌کشی «{g['name']}» تموم شد.")]
    for k in ("channels", "groups", "users"):
        if k in kinds:
            x = stats[k]
            lines.append(f"{names[k]}: ✅ {x['ok']:,} • ❌ {x['fail']:,}" + (f" • ⏭ {x['skip']:,} (قبلاً رفته بود)" if x["skip"] else ""))
    if "groups" in kinds:
        lines.append(f"پین‌شده تو گپ‌ها: {pinned:,}")
    if errors:
        lines.append("\nخطاها:\n" + "\n".join(errors))
        if any(any(h in e.lower() for h in hard[:5]) for e in errors):
            lines.append("\n⚠️ برای کانال‌ها: ربات باید تو کانال ادمین باشه و دسترسی «ارسال پیام» داشته باشه (یوزرنیم کانال هم باید درست باشه).")
    lines.append(f"\nپایان: {_gw_fmt_time(g['ends_at'])} (تهران)\nمدیریتش: /admin ← 🎁 قرعه‌کشی")
    try:
        await bot.send_message(admin_chat, "\n".join(lines))
    except Exception:
        pass


async def gw_publish(bot, gid: int, admin_chat: int):
    g = await pool.fetchrow("SELECT * FROM giveaways WHERE id=$1", gid)
    kinds = set()
    if g["send_channels"]:
        kinds.add("channels")
    if g["send_groups"]:
        kinds.add("groups")
    if g["send_users"]:
        kinds.add("users")
    await gw_send_to(bot, gid, admin_chat, kinds)


# ───── پنل پشتیبانی ─────
def gw_menu_kb():
    return InlineKeyboardMarkup([
        [btn("➕ قرعه‌کشی جدید", "success", callback_data="ad:gw:new"), btn("📋 قرعه‌کشی‌ها", "primary", callback_data="ad:gw:list")],
        [btn("🔙 پنل", callback_data="ad:menu")],
    ])


def gw_draft_text(d: dict) -> str:
    yn = lambda v: "✅" if v else "❌"
    return ("🎁 پیش‌نمایش قرعه‌کشی\n\n"
            f"نام: {d['name']}\nمدت: {_gw_fmt_dur(d['duration'])}\nکانال‌ها: {', '.join(d['channels']) or '—'}\nجایزه: {d['prize']}\n"
            f"ظرفیت: {d['max_users'] or 'نامحدود'} • برنده: {d['winners']} • دایره‌های کپچا: {d['circles']}\n\n"
            f"ارسال به: گروه‌ها {yn(d['send_groups'])} • پیوی کاربرها {yn(d['send_users'])} • کانال‌ها {yn(d['send_channels'])}\n"
            f"لیست برترین‌ها برای همه: {yn(d['top_public'])} (خاموش = فقط پشتیبان‌ها)\nبرنده‌ها: کسایی که بیشترین تیکت رو دارن • تو گپ‌هایی که ربات ادمینه پین میشه\n\nتنظیمات رو عوض کن و «انتشار» رو بزن.")


def gw_draft_kb(d: dict):
    yn = lambda v: "✅" if v else "❌"
    return InlineKeyboardMarkup([
        [btn(f"{yn(d['send_groups'])} ارسال به گروه‌ها", "primary", callback_data="ad:gw:t:send_groups"),
         btn(f"{yn(d['send_users'])} ارسال به پیوی کاربرها", "primary", callback_data="ad:gw:t:send_users")],
        [btn(f"{yn(d['send_channels'])} ارسال به کانال‌ها", "primary", callback_data="ad:gw:t:send_channels"),
         btn(f"{yn(d['top_public'])} لیست برترین‌ها برای همه", "primary", callback_data="ad:gw:t:top_public")],
        [btn("🚀 انتشار", "success", callback_data="ad:gw:pub"), btn("❌ لغو", "danger", callback_data="ad:cancel")],
    ])


async def gw_view(g, bot=None):
    n = await pool.fetchval("SELECT count(*) FROM giveaway_entries WHERE gid=$1 AND status='active'", g["id"])
    left = await pool.fetchval("SELECT count(*) FROM giveaway_entries WHERE gid=$1 AND status='left'", g["id"])
    st = {"active": "🟢 فعال", "ended": "🏁 تموم‌شده", "cancelled": "⛔ لغوشده", "draft": "✏️ پیش‌نویس"}.get(g["status"], g["status"])
    un = _GW_BOT_UN
    if not un and bot is not None:
        try:
            un = await _gw_bot_username(bot)
        except Exception:
            un = ""
    link = f"https://t.me/{un}?start=gw_{g['id']}" if un else ""
    e = html.escape
    text = (f"🎁 {e(g['name'])} (#{g['id']})\nوضعیت: {st}\nجایزه: {e(g['prize'])}\nبرنده: {g['winners']} • ظرفیت: {g['max_users'] or 'نامحدود'}\n"
            f"ثبت‌نامی فعال: {n:,} • لفت‌داده: {left:,}\nپایان: {_gw_fmt_time(g['ends_at'])} (تهران)\n"
            f"کانال‌ها: {e(', '.join(_gw_channels(g)) or '—')}\nلیست برترین‌ها برای همه: {'✅' if g['top_public'] else '❌'}")
    if link:
        text += f"\n\n🔗 لینک قرعه‌کشی:\n<code>{link}</code>"
    rows = []
    if link and g["status"] == "active":
        rows.append([btn("🔗 باز کردن لینک قرعه‌کشی", "primary", url=link)])
    rows.append([btn("🏆 لیست برترین‌ها", "primary", callback_data=f"ad:gw:top:{g['id']}"),
                 btn(("🌐 برترین‌ها: همه ✅" if g["top_public"] else "🌐 برترین‌ها: فقط پشتیبان"), "primary", callback_data=f"ad:gw:pt:{g['id']}")])
    if g["status"] == "active":
        rows.append([btn("📨 ارسال به پیوی کاربرها", "primary", callback_data=f"ad:gw:snd:{g['id']}:u"),
                     btn("👥 ارسال به گپ‌ها", "primary", callback_data=f"ad:gw:snd:{g['id']}:g")])
        rows.append([btn("📢 ارسال به کانال‌های اجباری", "primary", callback_data=f"ad:gw:snd:{g['id']}:c")])
        rows.append([btn("🎲 پایان و قرعه‌کشی همین الان", "success", callback_data=f"ad:gw:end:{g['id']}"),
                     btn("🗑 لغو قرعه‌کشی", "danger", callback_data=f"ad:gw:cx:{g['id']}")])
    rows.append([btn("🔙 قرعه‌کشی‌ها", callback_data="ad:gw:list")])
    return text, InlineKeyboardMarkup(rows)


async def gw_admin_cb(update: Update, context: ContextTypes.DEFAULT_TYPE, parts: list[str]):
    """ad:gw[:sub[:arg]]"""
    q = update.callback_query
    sub = parts[2] if len(parts) > 2 else "menu"
    if sub == "menu":
        context.user_data.pop("adm", None)
        await q.edit_message_text("🎁 قرعه‌کشی\n\nاز اینجا قرعه‌کشی جدید بساز یا قبلی‌ها رو مدیریت کن.", reply_markup=gw_menu_kb())
    elif sub == "new":
        context.user_data["adm"] = {"act": "gw", "step": "name", "d": {
            "send_groups": True, "send_users": False, "send_channels": True, "top_public": False}}
        await q.edit_message_text("🎁 قرعه‌کشی جدید — مرحله‌ی ۱ از ۷\n\nاسم قرعه‌کشی رو بفرست.\n(لغو: «لغو»)", reply_markup=CANCEL_KB)
    elif sub == "list":
        rows = await pool.fetch("SELECT * FROM giveaways WHERE status <> 'draft' ORDER BY id DESC LIMIT 20")
        kb = [[btn(f"{'🟢' if r['status'] == 'active' else '🏁'} #{r['id']} {r['name'][:28]}", "primary", callback_data=f"ad:gw:v:{r['id']}")] for r in rows]
        kb.append([btn("🔙 قرعه‌کشی", callback_data="ad:gw")])
        await q.edit_message_text("📋 قرعه‌کشی‌ها" if rows else "هنوز قرعه‌کشی‌ای نساختی.", reply_markup=InlineKeyboardMarkup(kb))
    elif sub in ("v", "top", "pt", "end", "cx"):
        gid = int(parts[3])
        g = await pool.fetchrow("SELECT * FROM giveaways WHERE id=$1", gid)
        if not g:
            await q.edit_message_text("پیدا نشد.", reply_markup=gw_menu_kb()); return
        if sub == "top":
            await q.edit_message_text(re.sub(r"<[^>]+>", "", await gw_top_text(gid)), reply_markup=InlineKeyboardMarkup([[btn("🔙 برگشت", callback_data=f"ad:gw:v:{gid}")]]))
            return
        if sub == "pt":
            await pool.execute("UPDATE giveaways SET top_public = NOT top_public WHERE id=$1", gid)
        elif sub == "end":
            if g["status"] == "active":
                await q.edit_message_text("🎲 در حال قرعه‌کشی...")
                await gw_finish(context.bot, gid, q.from_user.id)
        elif sub == "cx":
            await pool.execute("UPDATE giveaways SET status='cancelled' WHERE id=$1 AND status='active'", gid)
            for p in await pool.fetch("SELECT chat_id, message_id FROM giveaway_posts WHERE gid=$1", gid):
                try: await context.bot.edit_message_reply_markup(p["chat_id"], p["message_id"], reply_markup=None)
                except Exception: pass
        g = await pool.fetchrow("SELECT * FROM giveaways WHERE id=$1", gid)
        text, kb = await gw_view(g, context.bot)
        try:
            await q.edit_message_text(text, parse_mode="HTML", reply_markup=kb)
        except BadRequest as e:
            if "not modified" not in str(e).lower():
                raise
    elif sub == "snd":
        gid = int(parts[3])
        kind = {"u": "users", "g": "groups", "c": "channels"}.get(parts[4] if len(parts) > 4 else "")
        g = await pool.fetchrow("SELECT * FROM giveaways WHERE id=$1", gid)
        if not g or g["status"] != "active" or not kind:
            await context.bot.send_message(q.message.chat_id, "این قرعه‌کشی فعال نیست.")
            return
        running = context.application.bot_data.setdefault("gw_sending", set())
        if (gid, kind) in running:
            await context.bot.send_message(q.message.chat_id, "⏳ همین الان داره ارسال میشه؛ صبر کن تا گزارشش بیاد.")
            return
        running.add((gid, kind))
        fa = {"users": "پیوی کاربرها", "groups": "گپ‌ها", "channels": "کانال‌های اجباری"}[kind]
        await context.bot.send_message(q.message.chat_id, f"📤 ارسال قرعه‌کشی #{gid} به {fa} شروع شد (فقط به کسایی که قبلاً نرفته)؛ وقتی تموم شد گزارش می‌دم.")

        async def _run():
            try:
                await gw_send_to(context.bot, gid, q.message.chat_id, {kind}, resend=True)
            finally:
                running.discard((gid, kind))

        task = asyncio.create_task(_run())
        context.application.bot_data.setdefault("tasks", set()).add(task)
        task.add_done_callback(lambda t: context.application.bot_data["tasks"].discard(t))
    elif sub == "t":
        st = context.user_data.get("adm")
        if not st or st.get("act") != "gw" or st.get("step") != "confirm":
            return
        f = parts[3]
        if f in ("send_groups", "send_users", "send_channels", "top_public"):
            st["d"][f] = not st["d"][f]
        await q.edit_message_text(gw_draft_text(st["d"]), reply_markup=gw_draft_kb(st["d"]))
    elif sub == "pub":
        st = context.user_data.get("adm")
        if not st or st.get("act") != "gw" or st.get("step") != "confirm":
            await q.edit_message_text("چیزی برای انتشار نیست.", reply_markup=gw_menu_kb()); return
        d = st["d"]
        context.user_data.pop("adm", None)
        now = time.time()
        gid = await pool.fetchval(
            "INSERT INTO giveaways (name, prize, channels, ends_at, duration, max_users, winners, circles, top_public, send_groups, send_users, send_channels, status, created_by, created_at) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,'active',$13,$14) RETURNING id",
            d["name"], d["prize"], ",".join(d["channels"]), now + d["duration"], float(d["duration"]), d["max_users"], d["winners"], d["circles"],
            d["top_public"], d["send_groups"], d["send_users"], d["send_channels"], q.from_user.id, now)
        await q.edit_message_text(f"{HG_CE} قرعه‌کشی #{gid} ساخته شد و ارسال شروع شد؛ وقتی تموم شد خبرت می‌کنم.", parse_mode="HTML")
        task = asyncio.create_task(gw_publish(context.bot, gid, q.message.chat_id))
        context.application.bot_data.setdefault("tasks", set()).add(task)
        task.add_done_callback(lambda t: context.application.bot_data["tasks"].discard(t))


async def gw_admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE, st: dict) -> bool:
    msg = update.effective_message
    text = (msg.text or "").strip()
    d = st["d"]
    step = st["step"]
    if step == "confirm":
        await msg.reply_text("از دکمه‌های بالا استفاده کن.", reply_markup=gw_draft_kb(d))
        return True
    if not text:
        await msg.reply_text("متن بفرست.", reply_markup=CANCEL_KB)
        return True
    num = text.translate(_DIG)
    if step == "name":
        d["name"] = text[:80]
        st["step"] = "time"
        await msg.reply_text("مرحله‌ی ۲ از ۷ — مدت قرعه‌کشی (از لحظه‌ی انتشار)\nمثال: 2 ساعت، 30 دقیقه، 1 روز", reply_markup=CANCEL_KB)
    elif step == "time":
        sec = parse_duration(text)
        if not sec or sec < 60:
            await msg.reply_text("مدت نامعتبره (حداقل ۱ دقیقه). مثال: 2 ساعت، 30 دقیقه، 1 روز", reply_markup=CANCEL_KB)
            return True
        d["duration"] = sec
        st["step"] = "channels"
        await msg.reply_text(
            "مرحله‌ی ۳ از ۷ — کانال‌های اجباری قرعه‌کشی\n@یوزرنیم کانال‌ها رو با فاصله یا خط جدا بفرست (ربات باید تو کانال‌ها ادمین باشه).\n"
            f"برای استفاده از کانال‌های اجباری خود ربات بنویس: پیش‌فرض ({' '.join(FORCE_CHANNELS)})\nبدون کانال: هیچ", reply_markup=CANCEL_KB)
    elif step == "channels":
        if text in ("پیش‌فرض", "پیشفرض", "default"):
            chs = list(FORCE_CHANNELS)
        elif text in ("هیچ", "هیچی", "none"):
            chs = []
        else:
            chs = []
            for tok in re.split(r"[\s,،]+", text):
                if not tok:
                    continue
                tok = tok.replace("https://t.me/", "@").replace("t.me/", "@")
                if not re.fullmatch(r"@[A-Za-z0-9_]{4,32}", tok):
                    await msg.reply_text(f"«{tok}» یوزرنیم کانال معتبر نیست. به شکل @channel بفرست.", reply_markup=CANCEL_KB)
                    return True
                chs.append(tok)
        bad = []
        for ch in chs:
            try:
                me = await context.bot.get_chat_member(ch, context.bot.id)
                if str(getattr(me, "status", "")) not in ("administrator", "creator"):
                    bad.append(ch)
            except Exception:
                bad.append(ch)
        if bad:
            await msg.reply_text("ربات تو این کانال‌ها ادمین نیست یا کانال پیدا نشد: " + " ".join(bad) + "\nاول ربات رو ادمین کن و دوباره کانال‌ها رو بفرست.", reply_markup=CANCEL_KB)
            return True
        d["channels"] = list(dict.fromkeys(chs))
        st["step"] = "prize"
        await msg.reply_text("مرحله‌ی ۴ از ۷ — جایزه چیه؟ (متن آزاد، مثلاً «۵۰۰ هزار تومن»)", reply_markup=CANCEL_KB)
    elif step == "prize":
        d["prize"] = text[:200]
        st["step"] = "max"
        await msg.reply_text("مرحله‌ی ۵ از ۷ — حداکثر چند نفر می‌تونن شرکت کنن؟ (عدد؛ برای نامحدود 0 بفرست)", reply_markup=CANCEL_KB)
    elif step == "max":
        if not num.isdigit() or int(num) > 10_000_000:
            await msg.reply_text("یه عدد بفرست (0 = نامحدود).", reply_markup=CANCEL_KB); return True
        d["max_users"] = int(num)
        st["step"] = "winners"
        await msg.reply_text("مرحله‌ی ۶ از ۷ — چند برنده داشته باشه؟ (عدد)", reply_markup=CANCEL_KB)
    elif step == "winners":
        if not num.isdigit() or not (1 <= int(num) <= 1000):
            await msg.reply_text("یه عدد بین ۱ تا ۱۰۰۰ بفرست.", reply_markup=CANCEL_KB); return True
        d["winners"] = int(num)
        st["step"] = "circles"
        await msg.reply_text("مرحله‌ی ۷ از ۷ — کپچای ضد ربات: اول چند تا دایره بیاد؟ (۳ تا ۸) — کاربر باید روی دایره‌ای که بهش گفته می‌شه بزنه.", reply_markup=CANCEL_KB)
    elif step == "circles":
        if not num.isdigit() or not (3 <= int(num) <= 8):
            await msg.reply_text("یه عدد بین ۳ تا ۸ بفرست.", reply_markup=CANCEL_KB); return True
        d["circles"] = int(num)
        st["step"] = "confirm"
        await msg.reply_text(gw_draft_text(d), reply_markup=gw_draft_kb(d))
    return True


# ───────────────────────── پنل پشتیبانی /admin ─────────────────────────
BOT_ENABLED = True
KNOWN_CHATS: set = set()
FORCE_CHANNELS = ["@jagh_game", "@selfsaz_iran"]  # کانال‌های عضویت اجباری
FORCE_CHANNEL_EID = "5861505966666695398"    # ایموجی پرمیوم پیام عضویت
_MEMBER_OK: dict[int, float] = {}            # user_id -> تا کی نیازی به چک دوباره نیست
_JOIN_PROMPTED: dict[tuple[int, int], float] = {}
BANNED: dict[int, float] = {}   # user_id -> زمان پایان محرومیت (timestamp)
BAN_PERM = 32503680000.0        # محرومیت دائمی (سال ۳۰۰۰)
_DUR_UNITS = {
    "ثانیه": 1, "دقیقه": 60, "ساعت": 3600, "روز": 86400, "هفته": 604800, "ماه": 2592000, "سال": 31536000,
    "s": 1, "min": 60, "m": 60, "h": 3600, "d": 86400, "w": 604800,
}


def parse_duration(text: str):
    """«30 دقیقه»، «2 ساعت»، «یک روز»، «3d»، «دائمی» → ثانیه (-1 = دائمی، None = نامعتبر)."""
    t = norm_word(text).translate(_DIG).lower()
    if t in ("دائمی", "دایمی", "همیشگی", "همیشه", "ابد", "permanent", "perm"):
        return -1
    m = re.fullmatch(r"([\d.]+|[^\W\d_]+)\s*([^\W\d_]+)", t)
    if not m:
        return None
    g1, unit = m.groups()
    try:
        num = float(g1) if re.fullmatch(r"[\d.]+", g1) else _NUM_WORDS.get(g1)
    except ValueError:
        return None
    mult = _DUR_UNITS.get(unit)
    if not num or not mult or num <= 0:
        return None
    return int(num * mult)


def _fmt_left(sec: float) -> str:
    sec = max(0, int(sec))
    d, rem = divmod(sec, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    parts = ([f"{d} روز"] if d else []) + ([f"{h} ساعت"] if h else []) + ([f"{m} دقیقه"] if m or not (d or h) else [])
    return " و ".join(parts)


def _ban_until_txt(until: float) -> str:
    if until >= BAN_PERM:
        return "برای همیشه"
    from zoneinfo import ZoneInfo
    return "تا " + datetime.fromtimestamp(until, ZoneInfo("Asia/Tehran")).strftime("%Y-%m-%d %H:%M")


# ───────────────────────── 🚗 رندر عکس ماشین‌ها ─────────────────────────
_W, _H, _SS = 1000, 625, 2


def _chaikin(pts, n):
    for _ in range(n):
        new = []
        for i in range(len(pts)):
            p, q = pts[i], pts[(i + 1) % len(pts)]
            new.append((0.75 * p[0] + 0.25 * q[0], 0.75 * p[1] + 0.25 * q[1]))
            new.append((0.25 * p[0] + 0.75 * q[0], 0.25 * p[1] + 0.75 * q[1]))
        pts = new
    return pts


def _mix(c, t, k):
    return tuple(max(0, min(255, int(c[i] + (t[i] - c[i]) * k))) for i in range(3))


def _sc(pts):
    return [(x * _SS, y * _SS) for x, y in pts]


# شکل هر ماشین: بدنه، شیشه‌ها، چرخ‌ها، چراغ‌ها (مختصات روی بوم ۱۰۰۰×۶۲۵، ماشین رو به راست)
_SHAPES = {
    0: dict(  # پراید ۱۳۱
        smooth=2,
        body=[(165, 445), (160, 345), (215, 252), (240, 236), (565, 236), (665, 328), (825, 345), (852, 385), (852, 445)],
        glass=[[(250, 258), (400, 258), (400, 322), (232, 322)], [(414, 258), (556, 258), (632, 322), (414, 322)]],
        wheels=[(285, 448, 52), (730, 448, 52)],
        head=[(830, 352), (852, 366), (852, 384), (826, 376)], tail=[(160, 352), (176, 352), (176, 400), (160, 400)],
        extra="pride",
    ),
    1: dict(  # تویوتا سوپرا
        smooth=2,
        body=[(150, 445), (144, 372), (150, 346), (310, 318), (420, 248), (565, 240), (705, 330), (830, 352), (872, 388), (872, 445)],
        glass=[[(342, 312), (432, 258), (545, 258), (548, 318)], [(560, 258), (586, 258), (664, 318), (560, 318)]],
        wheels=[(280, 450, 54), (748, 450, 54)],
        head=[(826, 358), (868, 372), (868, 388), (820, 380)], tail=[(146, 356), (170, 350), (172, 378), (146, 382)],
        extra="supra",
    ),
    2: dict(  # بی‌ام‌و M4
        smooth=2,
        body=[(152, 445), (148, 362), (162, 338), (300, 324), (404, 250), (590, 244), (712, 326), (852, 346), (874, 392), (872, 445)],
        glass=[[(330, 320), (414, 258), (522, 258), (524, 320)], [(536, 258), (582, 258), (672, 320), (536, 320)]],
        wheels=[(282, 450, 55), (748, 450, 55)],
        head=[(828, 352), (868, 366), (868, 384), (820, 376)], tail=[(150, 346), (178, 342), (180, 372), (150, 376)],
        extra="m4",
    ),
    3: dict(  # لامبورگینی هوراکان
        smooth=1,
        body=[(138, 448), (142, 392), (330, 348), (410, 296), (520, 278), (760, 378), (886, 408), (890, 448)],
        glass=[[(420, 304), (506, 288), (668, 346), (430, 346)]],
        wheels=[(262, 452, 56), (770, 452, 52)],
        head=[(800, 392), (880, 404), (884, 418), (790, 410)], tail=[(140, 396), (190, 384), (192, 404), (140, 414)],
        extra="lambo",
    ),
    4: dict(  # بوگاتی شیرون
        smooth=3,
        body=[(150, 445), (142, 372), (188, 300), (350, 246), (555, 240), (700, 300), (815, 346), (880, 372), (888, 408), (882, 445)],
        glass=[[(250, 286), (356, 254), (500, 254), (500, 306), (270, 306)], [(516, 254), (556, 254), (640, 300), (516, 306)]],
        wheels=[(278, 450, 56), (760, 450, 56)],
        head=[(828, 356), (872, 372), (874, 388), (822, 380)], tail=[(146, 350), (166, 346), (170, 392), (146, 394)],
        extra="bugatti",
    ),
}


def _bg():
    im = Image.new("RGB", (_W * _SS, _H * _SS))
    d = ImageDraw.Draw(im)
    top, bot = (28, 32, 48), (118, 124, 146)
    hor = 470 * _SS
    for y in range(hor):
        k = y / hor
        d.line([(0, y), (im.width, y)], fill=_mix(top, bot, k))
    for y in range(hor, im.height):
        k = (y - hor) / (im.height - hor)
        d.line([(0, y), (im.width, y)], fill=_mix((70, 72, 84), (34, 35, 42), k))
    d.line([(0, hor), (im.width, hor)], fill=(150, 154, 170), width=3)
    return im


def _wheel(im, cx, cy, r):
    d = ImageDraw.Draw(im)
    cx, cy, r = cx * _SS, cy * _SS, r * _SS
    d.ellipse([cx - r * 1.12, cy - r * 1.12, cx + r * 1.12, cy + r * 1.12], fill=(8, 8, 10))  # گلگیر
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(22, 22, 24))
    rr = r * 0.66
    d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=(190, 194, 205))
    for i in range(5):
        a = math.radians(i * 72 - 90)
        d.line([(cx, cy), (cx + math.cos(a) * rr * 0.95, cy + math.sin(a) * rr * 0.95)], fill=(95, 98, 110), width=int(r * 0.12))
    d.ellipse([cx - rr * 0.28, cy - rr * 0.28, cx + rr * 0.28, cy + rr * 0.28], fill=(70, 72, 82))


def render_car_image(idx: int, rgb: tuple) -> BytesIO:
    sh = _SHAPES[idx]
    im = _bg()
    W, H = im.size
    # سایه‌ی زمین
    shadow = Image.new("L", im.size, 0)
    ImageDraw.Draw(shadow).ellipse([120 * _SS, 440 * _SS, 900 * _SS, 505 * _SS], fill=170)
    shadow = shadow.filter(ImageFilter.GaussianBlur(14 * _SS))
    im.paste(Image.new("RGB", im.size, (5, 5, 8)), (0, 0), shadow)

    body = _sc(_chaikin(sh["body"], sh["smooth"]))
    mask = Image.new("L", im.size, 0)
    ImageDraw.Draw(mask).polygon(body, fill=255)
    layer = Image.new("RGBA", im.size, rgb + (255,))
    # سایه‌روشن عمودی روی بدنه
    ov = Image.new("RGBA", im.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    y0, y1 = 225 * _SS, 450 * _SS
    for y in range(int(y0), int(y1)):
        k = (y - y0) / (y1 - y0)
        if k < 0.35:
            od.line([(0, y), (W, y)], fill=(255, 255, 255, int(70 * (1 - k / 0.35))))
        elif k > 0.55:
            od.line([(0, y), (W, y)], fill=(0, 0, 0, int(120 * (k - 0.55) / 0.45)))
    layer = Image.alpha_composite(layer, ov)
    im.paste(layer.convert("RGB"), (0, 0), mask)
    d = ImageDraw.Draw(im)

    # شیشه‌ها
    for g in sh["glass"]:
        gp = _sc(_chaikin(g, 1))
        d.polygon(gp, fill=(24, 34, 48))
        gm = Image.new("L", im.size, 0)
        ImageDraw.Draw(gm).polygon(gp, fill=255)
        gl = Image.new("RGBA", im.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(gl)
        xs = [p[0] for p in gp]
        x0 = min(xs)
        gd.polygon([(x0 + 40 * _SS, 200 * _SS), (x0 + 90 * _SS, 200 * _SS), (x0 + 20 * _SS, 340 * _SS), (x0 - 30 * _SS, 340 * _SS)], fill=(190, 215, 240, 70))
        gl.putalpha(ImageChops.multiply(gl.getchannel("A"), gm))
        im.paste(gl, (0, 0), gl)
    d = ImageDraw.Draw(im)

    # جزئیات هر مدل
    ex = sh["extra"]
    dark = _mix(rgb, (0, 0, 0), 0.45)
    light = _mix(rgb, (255, 255, 255), 0.35)
    if ex == "pride":
        d.line(_sc([(400, 258), (400, 345)]), fill=dark, width=5 * _SS)  # ستون
        d.rectangle(_sc([(820, 392), (853, 412)])[0] + _sc([(820, 392), (853, 412)])[1], fill=(210, 210, 215))  # سپر جلو
    if ex == "supra":
        d.polygon(_sc([(138, 346), (206, 336), (206, 346), (138, 358)]), fill=dark)  # اسپویلر
        d.line(_sc([(330, 372), (840, 392)]), fill=dark, width=3 * _SS)
    if ex == "m4":
        for x in (812, 836):
            d.rounded_rectangle(_sc([(x, 366), (x + 20, 402)])[0] + _sc([(x, 366), (x + 20, 402)])[1], radius=5 * _SS, fill=(14, 14, 16))
        for i, c in enumerate([(70, 150, 255), (30, 60, 150), (230, 40, 40)]):
            d.line(_sc([(300 + i * 8, 400), (352 + i * 8, 400)]), fill=c, width=4 * _SS)
    if ex == "lambo":
        d.polygon(_sc([(440, 296), (520, 284), (760, 380), (700, 392), (520, 330)]), fill=_mix(rgb, (0, 0, 0), 0.25))
        d.line(_sc([(330, 372), (780, 400)]), fill=dark, width=3 * _SS)
        d.polygon(_sc([(830, 420), (890, 424), (890, 440), (830, 436)]), fill=(14, 14, 16))
    if ex == "bugatti":
        d.line(_sc(_chaikin([(560, 262), (640, 300), (610, 372), (520, 392)], 2)[:-3]), fill=light if sum(rgb) > 120 else (80, 80, 90), width=5 * _SS)
        d.rounded_rectangle(_sc([(838, 370), (872, 414)])[0] + _sc([(838, 370), (872, 414)])[1], radius=12 * _SS, fill=(18, 18, 22))
    # خط زیر بدنه
    d.line(_sc([(165, 440), (850, 440)]), fill=(10, 10, 12), width=6 * _SS)
    # چرخ‌ها
    for cx, cy, r in sh["wheels"]:
        _wheel(im, cx, cy, r)
    d = ImageDraw.Draw(im)
    d.polygon(_sc(sh["head"]), fill=(255, 244, 190))
    d.polygon(_sc(sh["tail"]), fill=(220, 20, 30))
    # ته‌رنگ نور
    out = im.resize((_W, _H), Image.LANCZOS)
    buf = BytesIO()
    out.save(buf, "JPEG", quality=90)
    buf.seek(0)
    return buf


# ───────────────────────── 🚗 گاراژ ماشین ─────────────────────────
CAR_WORDS = {"ماشین", "ماشینم", "ماشینمن", "ماشینها", "ماشینهام", "ماشینهایم", "گاراژ", "گاراژم", "گاراژمن", "گاراژماشین", "cars", "garage"}
CAR_MAX_LEVEL = 5
MAX_LEVEL_EID = "5458653586378469798"   # ایموجی «لول آخر» (فقط ماشین)
MAX_LEVEL_CE = f'<tg-emoji emoji-id="{MAX_LEVEL_EID}">🏆</tg-emoji>'
CAR_PAY_EVERY = 3600
CAR_VAULT_CAP = 100_000_000   # سقف صندوق هر ماشین؛ پر که شد درآمد جمع نمیشه تا برداشت کنی
CAR_COLOR_PRICE = 20_000_000
CARS = [
    ("پراید ۱۳۱", "Pride 131", "🚗", 100_000_000, 500_000),
    ("Toyota Supra MK5", "Toyota Supra MK5", "🏎️", 1_400_000_000, 1_000_000),
    ("BMW M4 Competition", "BMW M4 Competition", "🚘", 2_000_000_000, 1_200_000),
    ("Lamborghini Huracán", "Lamborghini Huracán", "🏎️", 3_500_000_000, 1_500_000),
    ("Bugatti Chiron", "Bugatti Chiron", "👑", 5_000_000_000, 2_000_000),
]
# ترتیب: سفید (پیش‌فرض)، مشکی، قرمز، آبی، صورتی
CAR_COLORS = [
    ("سفید", "⚪", (240, 242, 246)),
    ("مشکی", "⚫", (22, 22, 26)),
    ("قرمز", "🔴", (210, 35, 42)),
    ("آبی", "🔵", (35, 105, 220)),
    ("صورتی", "🌸", (245, 105, 175)),
]
CAR_PARTS = {
    "engine": ("موتور", "⚙️", "up_engine"),
    "body": ("بدنه", "🛡️", "up_body"),
    "tires": ("لاستیک‌ها", "🛞", "up_tires"),
    "glass_rims": ("شیشه‌ها و رینگ", "🪟", "up_glass_rims"),
}
CAR_IMAGE_FILES = ["pride.jpg", "supra.jpg", "bmw.jpg", "lamborghini.jpg", "bugatti.jpg"]
_CAR_IMG_CACHE: dict[tuple[int, int], bytes] = {}

class _CarRollback(Exception):
    pass

def fmt_toman(n: int) -> str:
    parts = []
    b, rest = divmod(int(n), 1_000_000_000)
    m, rest = divmod(rest, 1_000_000)
    if b: parts.append(f"{b:,} میلیارد")
    if m: parts.append(f"{m:,} میلیون")
    if rest or not parts: parts.append(f"{rest:,}")
    return " و ".join(parts) + " تومن"

def fmt_short(n: int) -> str:
    """نسخه‌ی کوتاه برای جدول: 23200000 -> «23.2 میلیون»"""
    n = int(n)
    if n >= 1_000_000_000:
        return f"{n / 1_000_000_000:.2f}".rstrip("0").rstrip(".") + " میلیارد"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}".rstrip("0").rstrip(".") + " میلیون"
    return f"{n:,}"


_ZW = "\u200c\u200d\u200e\u200f"


def _vw(s: str) -> int:
    return len([ch for ch in str(s) if ch not in _ZW])


def mono_table(headers, rows) -> str:
    """جدول مونواسپیس داخل <pre> دقیقاً به سبک جدول شغل‌ها: سطر عنوان، خط ─┼─، بدون کادر بیرونی."""
    cols = len(headers)
    cells = [tuple(str(x) for x in headers)] + [tuple(str(x) for x in r) for r in rows]
    widths = [max(_vw(r[i]) for r in cells) for i in range(cols)]
    sep = "─┼─".join("─" * w for w in widths)
    lines = []
    for k, r in enumerate(cells):
        lines.append(" │ ".join(r[i] + " " * max(widths[i] - _vw(r[i]), 0) for i in range(cols)))
        if k == 0:
            lines.append(sep)
    return "<pre>" + html.escape("\n".join("\u200e" + l for l in lines)) + "</pre>"


def _bar(lv: int) -> str:
    lv = max(0, min(CAR_MAX_LEVEL, int(lv)))
    return "■" * lv + "□" * (CAR_MAX_LEVEL - lv)


def _car_parts_total(row) -> int:
    return sum(int(row[k] or 0) for k in ("up_engine", "up_body", "up_tires", "up_glass_rims"))

def car_profit(idx: int, owned_row_or_level) -> int:
    base = CARS[idx][4]
    if isinstance(owned_row_or_level, int):
        # سازگاری با فراخوانی‌های قدیمی
        total = max(0, owned_row_or_level - 1) * 4
    else:
        total = _car_parts_total(owned_row_or_level)
    return int(base * (1 + 0.15 * total))

def car_upgrade_cost(idx: int, level: int) -> int:
    return int(CARS[idx][3] * 0.08 * (level + 1))

_CAR_IMG_MAX_W = 1280


def _find_car_file(idx: int):
    here = Path(__file__).resolve().parent
    stem = Path(CAR_IMAGE_FILES[idx]).stem
    # اول پوشه‌ی cars، اگه نبود خود عکس‌ها کنار bot.py
    for base in (Path(os.environ.get("CARS_DIR") or (here / "cars")), here):
        for ext in (".jpg", ".jpeg", ".png", ".webp", ".JPG", ".JPEG", ".PNG", ".WEBP"):
            f = base / (stem + ext)
            if f.exists():
                return f
    return None


def _recolor_white_car_legacy(img: Image.Image, rgb: tuple) -> Image.Image:
    """بدنه‌ی سفید ماشین رو به رنگ دلخواه درمیاره؛ سایه‌روشن‌ها حفظ میشن، شیشه/لاستیک/پس‌زمینه‌ی بیرونی دست‌نخورده می‌مونن."""
    im = img.convert("RGB")
    if im.width > _CAR_IMG_MAX_W:
        im = im.resize((_CAR_IMG_MAX_W, int(im.height * _CAR_IMG_MAX_W / im.width)), Image.LANCZOS)
    if sum(rgb) >= 700:  # سفید = عکس اصلی
        return im
    _h, sat, val = im.convert("HSV").split()
    cand = ImageChops.multiply(sat.point(lambda x: 255 if x < 70 else 0), val.point(lambda x: 255 if x > 105 else 0))
    # پس‌زمینه‌ی سفید/روشنِ وصل به لبه‌ها حذف میشه تا رنگ نگیره (لبه‌ی بدنه مثل دیوار جلوی پخش‌شدنش رو می‌گیره)
    _g = im.convert("L").filter(ImageFilter.GaussianBlur(1.5))
    edge = ImageChops.subtract(_g.filter(ImageFilter.MaxFilter(5)), _g.filter(ImageFilter.MinFilter(5))).point(lambda x: 255 if x > 8 else 0)
    bgm = ImageChops.subtract(cand, edge)
    bgm = bgm.point(lambda x: 255 if x > 127 else 0)
    w, h = bgm.size
    seeds = [(x, 0) for x in range(0, w, max(1, w // 24))] + [(x, h - 1) for x in range(0, w, max(1, w // 24))] \
        + [(0, y) for y in range(0, h, max(1, h // 24))] + [(w - 1, y) for y in range(0, h, max(1, h // 24))]
    for pt in seeds:
        if bgm.getpixel(pt) == 255:
            ImageDraw.floodfill(bgm, pt, 128)
    body = bgm.point(lambda x: 255 if x == 255 else 0)
    if body.histogram()[255] < 0.02 * w * h:  # اگه تشخیص بدنه خراب شد
        body = cand
    body = body.filter(ImageFilter.GaussianBlur(1.2))
    gray = im.convert("L")

    def ch(t):
        return gray.point(lambda x: min(255, int(t * (0.35 + 0.65 * x / 255) + 255 * max(0.0, x / 255 - 0.86) * 0.9)))

    colored = Image.merge("RGB", tuple(ch(t) for t in rgb))
    return Image.composite(colored, im, body)


# ───── رنگ‌دهی عکس‌های گاراژ (بدنه‌ی سفید با انعکاس نارنجی/آبی) ─────
# برای هر ماشین چند نقطه‌ی مطمئنِ روی بدنه (کسری از عرض/ارتفاع) و کادری که بدنه توش قرار داره.
_CAR_SEEDS = {
    "pride":       ([(0.25, 0.43), (0.72, 0.50), (0.50, 0.33), (0.60, 0.56)], 0.27),
    "supra":       ([(0.28, 0.50), (0.50, 0.38), (0.80, 0.53), (0.55, 0.45)], 0.31),
    "bmw":         ([(0.35, 0.46), (0.60, 0.44), (0.80, 0.55), (0.20, 0.62)], 0.31),
    "lamborghini": ([(0.30, 0.52), (0.50, 0.45), (0.75, 0.50), (0.62, 0.40)], 0.34),
    "bugatti":     ([(0.35, 0.50), (0.55, 0.42), (0.80, 0.56), (0.20, 0.58)], 0.34),
}
_CAR_BODY_YMAX = 0.75


def _car_body_mask(im: Image.Image, stem: str):
    """ماسک بدنه: نقاط روشن و کم‌رنگ که به نقطه‌های مطمئن بدنه وصل‌اند (کف، دیوار و چراغ‌ها جدا می‌مونن)."""
    seeds, ymin = _CAR_SEEDS[stem]
    W = 640
    sm = im.resize((W, max(1, int(im.height * W / im.width))), Image.LANCZOS)
    _h, sat, val = sm.convert("HSV").split()
    cand = ImageChops.multiply(sat.point(lambda x: 255 if x < 185 else 0), val.point(lambda x: 255 if x > 95 else 0))
    cand = cand.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.MinFilter(9))
    m = cand.point(lambda x: 255 if x > 127 else 0)
    w, h = m.size
    clip = Image.new("L", (w, h), 0)
    ImageDraw.Draw(clip).rectangle([int(0.01 * w), int(ymin * h), int(0.98 * w), int(_CAR_BODY_YMAX * h)], fill=255)
    m = ImageChops.multiply(m, clip)
    for fx, fy in seeds:
        x, y = int(fx * w), int(fy * h)
        found = None
        for r in range(0, 30, 2):
            for dx in (-r, 0, r):
                for dy in (-r, 0, r):
                    xx, yy = min(w - 1, max(0, x + dx)), min(h - 1, max(0, y + dy))
                    if m.getpixel((xx, yy)) == 255:
                        found = (xx, yy)
                        break
                if found:
                    break
            if found:
                break
        if found:
            ImageDraw.floodfill(m, found, 128)
    return m.point(lambda x: 255 if x == 128 else 0)


def _recolor_white_car(img: Image.Image, rgb: tuple, stem: str = "") -> Image.Image:
    """بدنه‌ی سفید رو به رنگ دلخواه درمیاره؛ سایه‌روشن‌ها، شیشه‌ها، چرخ‌ها و گاراژ دست‌نخورده می‌مونن."""
    im = img.convert("RGB")
    if im.width > _CAR_IMG_MAX_W:
        im = im.resize((_CAR_IMG_MAX_W, int(im.height * _CAR_IMG_MAX_W / im.width)), Image.LANCZOS)
    if sum(rgb) >= 700:  # سفید = عکس اصلی
        return im
    if stem not in _CAR_SEEDS:
        return _recolor_white_car_legacy(img, rgb)
    body = _car_body_mask(im, stem)
    if body.histogram()[255] < 0.02 * body.width * body.height:  # تشخیص بدنه خراب شد
        return _recolor_white_car_legacy(img, rgb)
    body = body.resize(im.size, Image.BILINEAR).filter(ImageFilter.GaussianBlur(1.5))
    gray = im.convert("L")

    def ch(t):
        return gray.point(lambda x: min(255, int(t * (0.30 + 0.70 * x / 255) + 255 * max(0.0, x / 255 - 0.86) * 0.9)))

    colored = Image.merge("RGB", tuple(ch(t) for t in rgb))
    return Image.composite(colored, im, body)


def car_image_bytes(idx: int, color: int) -> bytes:
    key = (idx, color)
    if key not in _CAR_IMG_CACHE:
        rgb = CAR_COLORS[color][2]
        data = None
        f = _find_car_file(idx)
        if f is not None:
            try:
                with Image.open(f) as im:
                    out = _recolor_white_car(im, rgb, Path(CAR_IMAGE_FILES[idx]).stem)
                    buf = BytesIO()
                    out.save(buf, format="JPEG", quality=88, optimize=True)
                    data = buf.getvalue()
            except Exception:
                log.exception("car file render failed: %s", f)
        else:
            log.warning("cars/%s پیدا نشد؛ از رندر داخلی استفاده شد", CAR_IMAGE_FILES[idx])
        if data is None:  # عکس نبود/خراب بود → ماشین رو خود ربات می‌کشه تا همیشه عکس بیاد
            data = render_car_image(idx, rgb).getvalue()
        _CAR_IMG_CACHE[key] = data
    return _CAR_IMG_CACHE[key]

async def _car_photo(idx: int, color: int):
    try:
        data = await asyncio.to_thread(car_image_bytes, idx, color)
        bio = BytesIO(data)
        bio.name = f"car_{idx}_{color}.jpg"
        bio.seek(0)
        return bio
    except Exception:
        log.exception("car image load failed")
        return None

async def _owned_cars(uid: int) -> dict[int, asyncpg.Record]:
    rows = await pool.fetch("SELECT * FROM cars WHERE user_id = $1 ORDER BY car_idx", uid)
    return {r["car_idx"]: r for r in rows}

def _upgrade_btn(car_row, uid: int, idx: int, style: str):
    """اگه همه‌ی قطعه‌های ماشین به لول آخر رسیده باشن، ایموجی لول آخر نشون داده میشه."""
    if car_row is not None and all(int(car_row[col] or 0) >= CAR_MAX_LEVEL for _l, _i, col in CAR_PARTS.values()):
        return btn("ماشین در آخرین سطح", "success", icon=MAX_LEVEL_EID, callback_data=f"car:upmenu:{uid}:{idx}:0")
    return btn("⬆️ ارتقای ماشین", style, callback_data=f"car:upmenu:{uid}:{idx}:0")


def _car_keyboard(uid: int, idx: int, row, balance: int):
    rows = [
        [_upgrade_btn(row, uid, idx, "primary")],
        [btn("🎨 رنگ ماشین", "primary", callback_data=f"car:colors:{uid}:{idx}:0")],
        [btn("💰 جمع‌آوری درآمد", "success", callback_data=f"car:collect:{uid}:{idx}:0")],
        [btn("🔙 گاراژ", callback_data=f"car:garage:{uid}:{idx}:0")],
    ]
    return InlineKeyboardMarkup(rows)

def garage_main(uid: int, balance: int, owned: dict):
    lines = ["🚗 گاراژ ماشین‌ها", f"💰 دارایی شما: {balance:,} تومن", ""]
    rows = []
    next_idx = next((i for i in range(len(CARS)) if i not in owned), len(CARS))
    for i, (name, _en, emo, price, _p) in enumerate(CARS):
        if i in owned:
            r = owned[i]
            lines.append(f"✅ {emo} {name} | درآمد ساعتی: {fmt_toman(car_profit(i, r))}")
            rows.append([btn(f"🚘 {name} • ماشین من", "success", callback_data=f"car:view:{uid}:{i}:{r['color']}")])
        elif i == next_idx:
            lines.append(f"🔓 {emo} {name} | قیمت: {fmt_toman(price)}")
            rows.append([btn(f"🛒 خرید {name} • {fmt_toman(price)}", "primary", callback_data=f"car:view:{uid}:{i}:0")])
        else:
            lines.append(f"🔒 {emo} {name} | ابتدا ماشین قبلی را بخر")
    if not owned:
        lines += ["", "برای شروع باید اولین ماشین را بخری."]
    return B("\n".join(lines)), InlineKeyboardMarkup(rows)

GARAGE_NEED_PREV = "ابتدا ماشین قبلی"


def garage_rows(owned: dict):
    """[(ایموجی، اسم، قیمت، درآمد ساعتی، وضعیت: 'own' | 'buy' | 'lock')]"""
    next_idx = next((i for i in range(len(CARS)) if i not in owned), len(CARS))
    rows = []
    for i, (name, _en, emo, price, base) in enumerate(CARS):
        if i in owned:
            rows.append((emo, name, "—", fmt_short(car_profit(i, owned[i])), "own"))
        elif i == next_idx:
            rows.append((emo, name, fmt_short(price), fmt_short(base), "buy"))
        else:
            rows.append((emo, name, fmt_short(price), fmt_short(base), "lock"))
    return rows


def garage_rich_html(balance: int, owned: dict) -> str:
    """جدول واقعی تلگرام (Rich Message) مثل جدول شغل‌ها: قفل باز برای ماشین‌هایی که داری، قفل بسته برای بقیه."""
    out = ["<table><tr><th>ماشین</th><th>قیمت</th><th>درآمد ساعتی</th><th>وضعیت</th></tr>"]
    for emo, name, price, income, st in garage_rows(owned):
        if st == "own":
            status = f"{lock_open()} داری"
        elif st == "buy":
            status = "🛒 قابل خرید"
        else:
            status = f"{lock_closed()} {GARAGE_NEED_PREV}"
        out.append(f"<tr><td>{emo} {html.escape(name)}</td><td>{price}</td><td>{income}</td><td>{status}</td></tr>")
    out.append("</table>")
    out.append(f"<p>{ce('wallet', 1, '💰')} دارایی شما: {balance:,} تومن</p>")
    return "".join(out)


def garage_pre_text(balance: int, owned: dict) -> str:
    """نسخه‌ی جایگزین: جدول مونواسپیس (اگه Rich Message کار نکرد)."""
    rows = []
    for emo, name, price, income, st in garage_rows(owned):
        status = "داری ✅" if st == "own" else ("قابل خرید 🛒" if st == "buy" else "قفل 🔒")
        rows.append((f"{emo} {name}", price, income, status))
    table = mono_table(("ماشین", "قیمت", "درآمد ساعتی", "وضعیت"), rows)
    return f"<b>🚗 گاراژ ماشین‌ها</b>\n{table}\n" + B(f"{ce('wallet', 1, '💰')} دارایی شما: {balance:,} تومن")


async def send_garage_list(bot, chat_id: int, uid: int, reply_to: int | None = None):
    """لیست گاراژ: اول جدول واقعی تلگرام، زیرش پیام دکمه‌ها؛ اگه جدول واقعی نشد، جدول مونواسپیس + دکمه‌ها تو یه پیام."""
    owned = await _owned_cars(uid)
    row = await get_user(uid)
    bal = row["balance"] if row else 0
    _t, kb = garage_main(uid, bal, owned)
    if owned:   # فقط جدول؛ بدون دکمه‌ی «ماشین من» و بقیه (خرید ماشین بعدی از پنل ماشین)
        kb = None
    rp = ReplyParameters(message_id=reply_to, allow_sending_without_reply=True) if reply_to else None
    rich_ok = False
    try:
        payload = {"chat_id": chat_id, "rich_message": {"html": garage_rich_html(bal, owned)}}
        if rp:
            payload["reply_parameters"] = rp.to_dict()
        await bot._post("sendRichMessage", payload)
        rich_ok = True
    except Exception:
        log.warning("garage sendRichMessage failed, using <pre> table", exc_info=True)
    if rich_ok:
        if kb is not None:   # هنوز ماشینی نداره: دکمه‌ی خرید اولین ماشین لازمه
            await send_html(bot, chat_id, B("🚗 برای خرید، اولین ماشین رو انتخاب کن 👇"), reply_markup=kb)
        return
    await send_html(bot, chat_id, garage_pre_text(bal, owned), reply_markup=kb, reply_parameters=rp)


def garage_car(uid: int, idx: int, color: int, owned_row, balance: int):
    name, _en, emo, price, base = CARS[idx]
    cname = CAR_COLORS[color][0]
    if owned_row:
        profit = car_profit(idx, owned_row)
        vault = int(owned_row["pending_profit"] or 0)
        head = B(f"🔑 ماشین شما: {emo} {html.escape(name)}\n🎨 رنگ فعلی: {cname}")
        income = mono_table(("💵 درآمد", "مقدار (تومن)"), [
            ("پایه", fmt_short(base)),
            ("پاداش قطعات", "+" + fmt_short(profit - base)),
            ("ساعتی", fmt_short(profit)),
            ("💰 صندوق", fmt_short(vault)),
        ])
        parts = mono_table(("🧰 قطعه", "سطح", "پیشرفت"), [
            (label, f"{int(owned_row[col] or 0)}/{CAR_MAX_LEVEL}", _bar(owned_row[col] or 0))
            for _k, (label, _icon, col) in CAR_PARTS.items()
        ])
        text = f"{head}\n{income}\n{parts}\n" + B(f"👛 کیف پول: {balance:,} تومن")
        kb = _car_keyboard(uid, idx, owned_row, balance)
    else:
        head = B(f"🚗 {html.escape(name)}")
        tbl = mono_table(("مشخصات", "مقدار"), [
            ("قیمت خرید", fmt_short(price)),
            ("درآمد پایه ساعتی", fmt_short(base)),
            ("رنگ اولیه", cname),
        ])
        text = f"{head}\n{tbl}\n" + B(f"👛 موجودی شما: {balance:,} تومن")
        kb = InlineKeyboardMarkup([[btn(f"🛒 بخرش به {fmt_toman(price)}", "success", callback_data=f"car:buy:{uid}:{idx}:0")], [btn("🔙 برگشت به گاراژ", callback_data=f"car:garage:{uid}:{idx}:0")]])
    return text, kb


PANEL_COMP: dict[tuple[int, int], list[int]] = {}   # (chat, پیام دکمه‌ها) -> پیام‌های همراه (عکس/جدول) که باید با اون پاک بشن


async def _drop_panel(bot, msg):
    """پیام پنل + عکس و جدول همراهش رو پاک می‌کنه."""
    for mid in PANEL_COMP.pop((msg.chat_id, msg.message_id), []):
        try:
            await bot.delete_message(msg.chat_id, mid)
        except Exception:
            pass
    try:
        await bot.delete_message(msg.chat_id, msg.message_id)
    except Exception:
        pass


async def _edit_cap(q, text: str, kb):
    """کپشن/متن رو ادیت می‌کنه؛ اگه پیام جدول واقعی بود (ادیت‌پذیر نیست) پنل رو پاک و پیام جدید می‌فرسته."""
    for fn, kw in ((q.edit_message_caption, {"caption": text}), (q.edit_message_text, {"text": text})):
        try:
            await fn(parse_mode="HTML", reply_markup=kb, **kw)
            return
        except Exception as e:
            if "not modified" in str(e).lower():
                return
    bot = q.message.get_bot()
    await _drop_panel(bot, q.message)
    await send_html(bot, q.message.chat_id, text, reply_markup=kb)


def _next_car_idx(owned: dict) -> int:
    return next((i for i in range(len(CARS)) if i not in owned), len(CARS))


def car_panel_rows(idx: int, owned: dict, balance: int):
    """سطرهای جدول پنل ماشین: [(عنوان، مقدار)]"""
    car = owned[idx]
    total_income = sum(car_profit(i, r) for i, r in owned.items())
    total_vault = sum(int(r["pending_profit"] or 0) for r in owned.values())
    rows = [
        ("⏱ درآمد ساعتی", f"{fmt_short(total_income)} تومن"),
        ("💰 توی صندوق", f"{total_vault:,} تومن"),
    ]
    for _k, (label, icon, col) in CAR_PARTS.items():
        lv = int(car[col] or 0)
        rows.append((f"{icon} {label}", f"{_bar(lv)} {lv}/{CAR_MAX_LEVEL}"))
    nxt = _next_car_idx(owned)
    if nxt < len(CARS):
        n = CARS[nxt]
        rows.append(("⬆️ ماشین بعدی", f"{n[2]} {n[0]} · {fmt_short(n[3])}"))
    else:
        rows.append(("🏆 ماشین بعدی", "همه‌ی ماشین‌ها رو داری"))
    rows.append(("👛 کیف پول", f"{balance:,} تومن"))
    return rows


def car_panel_rich_html(idx: int, owned: dict, balance: int) -> str:
    name, _en, emo, _price, _base = CARS[idx]
    cname = CAR_COLORS[int(owned[idx]["color"])][0]
    out = [f"<p>🔑 ماشین شما: {emo} {html.escape(name)} · 🎨 {html.escape(cname)}</p><table>"]
    for k, v in car_panel_rows(idx, owned, balance):
        out.append(f"<tr><td>{html.escape(k)}</td><td>{html.escape(v)}</td></tr>")
    out.append("</table>")
    return "".join(out)


# ایموجی‌های پرمیوم پنل ماشین (آیدی هر کدوم)
CP_E = {
    "income": ("5787192063099408213", "⏱"),
    "vault": ("6095712904139510450", "💰"),
    "engine": ("5775888804392996347", "⚙️"),
    "body": ("5037414171923186776", "🛡️"),
    "tires": ("5377521684221815300", "🛞"),
    "glass": ("5037614751190879049", "🪟"),
    "rims": ("5037668897843578129", "🛞"),
    "next": ("5364027807510832333", "⬆️"),
    "wallet": ("5334806881548379091", "👛"),
}
CP_SEP = "──────────────"
CP_MAX_EID = "5458653586378469798"   # ایموجی «سطح آخر»


def _cpe(key: str) -> str:
    eid, fb = CP_E[key]
    return f'<tg-emoji emoji-id="{eid}">{fb}</tg-emoji>'


def car_panel_pre(idx: int, owned: dict, balance: int) -> str:
    """پنل ماشین به‌صورت متن ساده + ایموجی پرمیوم و خط جداکننده (بدون <pre>؛ دیگه با زدن روش کپی نمیشه)."""
    name, _en, emo, _price, _base = CARS[idx]
    car = owned[idx]
    cname = CAR_COLORS[int(car["color"])][0]
    total_income = sum(car_profit(i, r) for i, r in owned.items())
    total_vault = sum(int(r["pending_profit"] or 0) for r in owned.values())

    def lvl(col: str) -> str:
        lv = int(car[col] or 0)
        if lv >= CAR_MAX_LEVEL:   # سطح آخر: فقط ایموجی مخصوص، بدون خونه‌ها
            return f'<tg-emoji emoji-id="{CP_MAX_EID}">✅</tg-emoji>'
        return f"{_bar(lv)} {lv}/{CAR_MAX_LEVEL}"

    nxt = _next_car_idx(owned)
    if nxt < len(CARS):
        n = CARS[nxt]
        nxt_txt = f"{n[2]} {html.escape(n[0])} · {fmt_short(n[3])}"
    else:
        nxt_txt = "همه‌ی ماشین‌ها رو داری 🏆"
    lines = [
        f"🔑 ماشین شما: {emo} {html.escape(name)} · 🎨 {html.escape(cname)}",
        f"درآمد ساعتی {_cpe('income')}: {fmt_short(total_income)} تومن",
        f"تو صندوق {_cpe('vault')}: {total_vault:,} تومن",
        f"موتور {_cpe('engine')}: {lvl('up_engine')}",
        f"بدنه {_cpe('body')}: {lvl('up_body')}",
        f"لاستیک‌ها {_cpe('tires')}: {lvl('up_tires')}",
        f"شیشه و رینگ: {_cpe('glass')}{_cpe('rims')} {lvl('up_glass_rims')}",
        f"ماشین بعدی {_cpe('next')}: {nxt_txt}",
        f"کیف پول {_cpe('wallet')}: {balance:,} تومن",
    ]
    return B(f"\n{CP_SEP}\n".join(lines))


def _panel_keyboard(uid: int, idx: int, owned: dict):
    rows = [
        [btn("💰 جمع کن", "success", callback_data=f"car:collect:{uid}:{idx}:0"),
         btn("🚗 گاراژ", "danger", callback_data=f"car:garage:{uid}:{idx}:0")],
        [_upgrade_btn(owned.get(idx), uid, idx, "success"),
         btn("🎨 رنگ", "primary", callback_data=f"car:colors:{uid}:{idx}:0")],
    ]
    nxt = _next_car_idx(owned)
    if nxt < len(CARS):
        n = CARS[nxt]
        rows.append([btn(f"⬆️ ماشین بعدی: {n[2]} {n[0]} · {fmt_short(n[3])}", "primary", callback_data=f"car:view:{uid}:{nxt}:0")])
    return InlineKeyboardMarkup(rows)


async def _send_car_panel(chat, context, uid: int, idx: int, reply_to: int | None = None):
    """پنل ماشین: عکس ماشین + جدول واقعی تلگرام (مثل جدول شغل‌ها) با دکمه‌ها؛ ماشین بعدی رو از همین‌جا میشه خرید."""
    bot = context.bot
    owned = await _owned_cars(uid)
    row_user = await get_user(uid)
    bal = row_user["balance"] if row_user else 0
    if idx not in owned:
        await send_garage_list(bot, chat, uid, reply_to)
        return
    car = owned[idx]
    color = int(car["color"])
    rp = ReplyParameters(message_id=reply_to, allow_sending_without_reply=True) if reply_to else None
    kb = _panel_keyboard(uid, idx, owned)
    comp: list[int] = []
    photo = await _car_photo(idx, color)
    cap = car_panel_pre(idx, owned, bal)
    if photo is not None and len(re.sub(r"<[^>]+>", "", cap)) <= 1000:
        # همه‌چیز تو یه پیام: عکس + جدول (کپشن) + دکمه‌ها زیرش
        try:
            photo.seek(0)
            await bot.send_photo(chat_id=chat, photo=photo, caption=cap, parse_mode="HTML", reply_markup=kb, reply_parameters=rp)
            return
        except Exception:
            log.warning("car single-message panel failed, falling back", exc_info=True)
            photo = await _car_photo(idx, color)
    if photo is not None:
        try:
            photo.seek(0)
            m = await bot.send_photo(chat_id=chat, photo=photo, reply_parameters=rp)
            comp.append(m.message_id)
            rp = None
        except Exception:
            log.exception("car photo send failed for car_idx=%s", idx)
    key_id = None
    try:
        payload = {"chat_id": chat, "rich_message": {"html": car_panel_rich_html(idx, owned, bal)}, "reply_markup": kb.to_dict()}
        if rp:
            payload["reply_parameters"] = rp.to_dict()
        res = await bot._post("sendRichMessage", payload)
        rich_id = res.get("message_id") if isinstance(res, dict) else None
        if isinstance(res, dict) and not res.get("reply_markup"):
            # این نسخه‌ی تلگرام دکمه رو به جدول نچسبوند → دکمه‌ها تو یه پیام جدا زیرش
            m2 = await send_html(bot, chat, B("👇 گزینه‌ها"), reply_markup=kb)
            key_id = m2.message_id
            if rich_id:
                comp.append(rich_id)
        else:
            key_id = rich_id
    except Exception:
        log.warning("car sendRichMessage failed, using <pre> table", exc_info=True)
    if key_id is None:
        m = await send_html(bot, chat, car_panel_pre(idx, owned, bal), reply_markup=kb, reply_parameters=rp)
        key_id = m.message_id
    if key_id and comp:
        if len(PANEL_COMP) > 3000:
            PANEL_COMP.clear()
        PANEL_COMP[(chat, key_id)] = comp


async def _repanel(q, context, uid: int, idx: int):
    """پنل قدیمی رو پاک می‌کنه و پنل تازه می‌فرسته."""
    chat = q.message.chat_id
    await _drop_panel(context.bot, q.message)
    await _send_car_panel(chat, context, uid, idx)

async def show_garage(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """«ماشین/ماشینم» ماشین خریداری‌شده را با عکس نشان می‌دهد؛ «گاراژ» فهرست خرید را."""
    msg, user = update.effective_message, update.effective_user
    await upsert_user(user.id, user.full_name)
    owned = await _owned_cars(user.id)
    text_key = _word_key(msg.text or "") if msg and msg.text else ""
    if owned and text_key in {_word_key("ماشین"), _word_key("ماشینم"), _word_key("ماشینمن")}:
        idx = max(owned.keys())
        await _send_car_panel(msg.chat_id, context, user.id, idx, msg.message_id)
        return
    await send_garage_list(context.bot, msg.chat_id, user.id, msg.message_id)

async def _garage_show_car(q, context, uid: int, idx: int, color: int = 0):
    owned = await _owned_cars(uid)
    row = await get_user(uid)
    car = owned.get(idx)
    color = int(car["color"]) if car else color
    text, kb = garage_car(uid, idx, color, car, row["balance"])
    photo = await _car_photo(idx, color)
    try:
        if photo:
            photo.seek(0)
            await q.edit_message_media(InputMediaPhoto(photo, caption=text, parse_mode="HTML"), reply_markup=kb)
        else:
            await q.edit_message_caption(caption=text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        if "not modified" not in str(e).lower():
            log.exception("car panel update failed")
            try:
                await q.message.delete()
            except Exception:
                pass
            if photo:
                photo.seek(0)
                await context.bot.send_photo(q.message.chat_id, photo=photo, caption=text, parse_mode="HTML", reply_markup=kb)
            else:
                await context.bot.send_message(q.message.chat_id, text=text, parse_mode="HTML", reply_markup=kb)

async def _swap_photo(q, context, idx: int, color: int, text: str, kb):
    """عکس ماشین (با رنگ داده‌شده) و کپشن پیام فعلی رو عوض می‌کنه؛ اگه نشد پیام جدید می‌فرسته."""
    photo = await _car_photo(idx, color)
    if photo is not None:
        try:
            await q.edit_message_media(InputMediaPhoto(photo, caption=text, parse_mode="HTML"), reply_markup=kb)
            return
        except Exception as e:
            if "not modified" in str(e).lower():
                return
            log.warning("swap photo failed, resending", exc_info=True)
        photo = await _car_photo(idx, color)
    try:
        await q.message.delete()
    except Exception:
        pass
    if photo is not None:
        await context.bot.send_photo(q.message.chat_id, photo=photo, caption=text, parse_mode="HTML", reply_markup=kb)
    else:
        await send_html(context.bot, q.message.chat_id, text, reply_markup=kb)


def _color_list_kb(uid: int, idx: int):
    colors = [[btn(f"{ico} {name}", "primary", callback_data=f"car:colorask:{uid}:{idx}:{c}")] for c, (name, ico, _rgb) in enumerate(CAR_COLORS)]
    colors.append([btn("🔙 برگشت", callback_data=f"car:refresh:{uid}:{idx}:0")])
    return InlineKeyboardMarkup(colors)


def _color_list_text() -> str:
    return B(f"🎨 انتخاب رنگ ماشین\nهزینه هر رنگ: {fmt_toman(CAR_COLOR_PRICE)}\nرنگ موردنظر را انتخاب کن تا اول ببینی:")


async def _show_upgrade_menu(q, uid: int, idx: int):
    owned = await _owned_cars(uid)
    car = owned.get(idx)
    if not car:
        await q.answer("اول این ماشین را بخر.", show_alert=True); return
    rows, trs = [], []
    for key, (label, icon, col) in CAR_PARTS.items():
        lv = int(car[col] or 0)
        cost = car_upgrade_cost(idx, lv)
        trs.append((label, f"{lv}/{CAR_MAX_LEVEL}", fmt_short(cost) if lv < CAR_MAX_LEVEL else "حداکثر"))
        if lv >= CAR_MAX_LEVEL:
            rows.append([btn(f"{label} • آخرین سطح {lv}/{CAR_MAX_LEVEL}", "success", icon=MAX_LEVEL_EID, callback_data=f"car:upgrade:{uid}:{idx}:{key}")])
        else:
            rows.append([btn(f"{icon} ارتقای {label} • سطح {lv}/{CAR_MAX_LEVEL} • {fmt_toman(cost)}", "primary", callback_data=f"car:upgrade:{uid}:{idx}:{key}")])
    rows.append([btn("🔙 برگشت", callback_data=f"car:refresh:{uid}:{idx}:0")])
    cap = B(f"⬆️ ارتقای ماشین {html.escape(CARS[idx][0])}\nهر قطعه جداگانه تا سطح {CAR_MAX_LEVEL} ارتقا می‌یابد.") + "\n" + mono_table(("🧰 قطعه", "سطح", "هزینه بعدی"), trs)
    await _edit_cap(q, cap, InlineKeyboardMarkup(rows))

async def car_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    p = q.data.split(":")
    try:
        act, uid, idx = p[1], int(p[2]), int(p[3])
    except (IndexError, ValueError):
        await q.answer(); return
    if q.from_user.id != uid:
        await q.answer("این پنل متعلق به شما نیست.", show_alert=True); return
    if idx not in range(len(CARS)):
        await q.answer(); return

    if act in ("view", "refresh"):
        await q.answer()
        if idx in await _owned_cars(uid):
            await _repanel(q, context, uid, idx)
        else:
            await _garage_show_car(q, context, uid, idx)
    elif act == "garage":
        await q.answer()
        try: await q.message.delete()
        except Exception: pass
        await send_garage_list(context.bot, q.message.chat_id, uid)
    elif act == "buy":
        price = CARS[idx][3]
        owned = await _owned_cars(uid)
        if idx in owned:
            await q.answer("این ماشین رو قبلاً خریدی؛ امکان خرید دوباره‌اش وجود نداره.", show_alert=True); return
        next_idx = next((i for i in range(len(CARS)) if i not in owned), len(CARS))
        if idx != next_idx:
            await q.answer("ماشین‌ها باید به ترتیب خریداری بشن؛ فقط ماشین بعدی قابل خریده.", show_alert=True); return
        try:
            async with pool.acquire() as c:
                async with c.transaction():
                    ok = await c.fetchval("UPDATE users SET balance=balance-$1 WHERE user_id=$2 AND balance >= $1 RETURNING 1", price, uid)
                    if not ok: raise _CarRollback()
                    await c.execute("INSERT INTO cars(user_id,car_idx,color,level,last_pay,pending_profit) VALUES($1,$2,0,1,$3,0) ON CONFLICT(user_id,car_idx) DO NOTHING", uid, idx, time.time())
        except _CarRollback:
            await q.answer(f"موجودی کافی نیست. قیمت: {fmt_toman(price)}", show_alert=True); return
        await q.answer("ماشین با موفقیت خریداری شد!", show_alert=True)
        await _repanel(q, context, uid, idx)
    elif act == "upmenu":
        await q.answer(); await _show_upgrade_menu(q, uid, idx)
    elif act == "upgrade":
        if len(p) < 5 or p[4] not in CAR_PARTS:
            await q.answer(); return
        key = p[4]; label, icon, col = CAR_PARTS[key]
        car = (await _owned_cars(uid)).get(idx)
        if not car:
            await q.answer("اول ماشین را بخر.", show_alert=True); return
        lv = int(car[col] or 0)
        if lv >= CAR_MAX_LEVEL:
            await q.answer("این قطعه به آخرین سطح رسیده است.", show_alert=True); return
        cost = car_upgrade_cost(idx, lv)
        try:
            async with pool.acquire() as c:
                async with c.transaction():
                    paid = await c.fetchval("UPDATE users SET balance=balance-$1 WHERE user_id=$2 AND balance >= $1 RETURNING 1", cost, uid)
                    if not paid: raise _CarRollback()
                    await c.execute(f"UPDATE cars SET {col}={col}+1, level=GREATEST(level,1) WHERE user_id=$1 AND car_idx=$2", uid, idx)
        except _CarRollback:
            await q.answer(f"پول کافی نیست؛ هزینه: {fmt_toman(cost)}", show_alert=True); return
        await q.answer(f"ارتقای {label} انجام شد!")
        await task_event(context.bot, q.message.chat_id, uid, "car_up")
        await _show_upgrade_menu(q, uid, idx)
    elif act == "colors":
        await q.answer()
        cur = (await _owned_cars(uid)).get(idx)
        if cur:
            await _swap_photo(q, context, idx, int(cur["color"]), _color_list_text(), _color_list_kb(uid, idx))
        else:
            await _edit_cap(q, _color_list_text(), _color_list_kb(uid, idx))
    elif act == "colorcancel":
        await q.answer("برگشت به رنگ قبلی")
        cur = (await _owned_cars(uid)).get(idx)
        if cur:
            await _swap_photo(q, context, idx, int(cur["color"]), _color_list_text(), _color_list_kb(uid, idx))
    elif act == "colorask":
        try: color = int(p[4])
        except (IndexError, ValueError): await q.answer(); return
        if color not in range(len(CAR_COLORS)): await q.answer(); return
        name = CAR_COLORS[color][0]
        cur = (await _owned_cars(uid)).get(idx)
        if cur and int(cur["color"]) == color:
            await q.answer(f"ماشینت همین الان {name} هست.", show_alert=True); return
        kb = InlineKeyboardMarkup([[btn("✅ تأیید و پرداخت ۲۰ میلیون", "success", callback_data=f"car:colorbuy:{uid}:{idx}:{color}")], [btn("❌ انصراف (برگشت به رنگ قبلی)", "danger", callback_data=f"car:colorcancel:{uid}:{idx}:0")]])
        await q.answer()
        # اول رنگ جدید روی ماشین اعمال و نشون داده میشه؛ تا تایید نکنه چیزی ذخیره/پرداخت نمیشه
        await _swap_photo(q, context, idx, color, B(f"👀 پیش‌نمایش رنگ {name}\n💰 هزینه: {fmt_toman(CAR_COLOR_PRICE)}\nاگه خوشت اومد تأیید کن، وگرنه انصراف بزن تا رنگ قبلی برگرده."), kb)
    elif act == "colorbuy":
        try: color = int(p[4])
        except (IndexError, ValueError): await q.answer(); return
        if color not in range(len(CAR_COLORS)): await q.answer(); return
        cur = (await _owned_cars(uid)).get(idx)
        if not cur:
            await q.answer("اول ماشین را بخر.", show_alert=True); return
        if int(cur["color"]) == color:
            await q.answer("ماشینت همین رنگ رو داره.", show_alert=True); return
        try:
            async with pool.acquire() as c:
                async with c.transaction():
                    paid = await c.fetchval("UPDATE users SET balance=balance-$1 WHERE user_id=$2 AND balance >= $1 RETURNING 1", CAR_COLOR_PRICE, uid)
                    if not paid: raise _CarRollback()
                    await c.execute("UPDATE cars SET color=$1 WHERE user_id=$2 AND car_idx=$3", color, uid, idx)
        except _CarRollback:
            await q.answer("موجودی کافی نیست؛ برای تغییر رنگ ۲۰ میلیون لازم است.", show_alert=True); return
        await q.answer(f"رنگ {CAR_COLORS[color][0]} خریداری و اعمال شد!", show_alert=True)
        await _repanel(q, context, uid, idx)
    elif act == "collect":
        async with pool.acquire() as c:
            async with c.transaction():
                # صندوق همه‌ی ماشین‌ها قفل میشه، صفر میشه و به کیف پول میره
                vrows = await c.fetch("SELECT pending_profit FROM cars WHERE user_id=$1 FOR UPDATE", uid)
                amount = sum(int(r["pending_profit"] or 0) for r in vrows)
                if amount > 0:
                    await c.execute("UPDATE cars SET pending_profit=0 WHERE user_id=$1", uid)
                    await c.execute("UPDATE users SET balance=balance+$1 WHERE user_id=$2", amount, uid)
        if not amount:
            await q.answer("فعلاً درآمدی در صندوق جمع نشده است.", show_alert=True); return
        await q.answer(f"{fmt_toman(amount)} به کیف پولت واریز شد!", show_alert=True)
        await task_event(context.bot, q.message.chat_id, uid, "car_collect")
        await _repanel(q, context, uid, idx)
    else:
        await q.answer()

async def car_profit_loop():
    """درآمد ساعتی را فقط داخل صندوق همان ماشین ذخیره می‌کند؛ برداشت با دکمه کاربر است."""
    while True:
        try:
            now = time.time()
            due = await pool.fetch("SELECT * FROM cars WHERE last_pay <= $1", now - CAR_PAY_EVERY)
            for r in due:
                hours = int((now - r["last_pay"]) // CAR_PAY_EVERY)
                if hours <= 0 or r["car_idx"] not in range(len(CARS)): continue
                profit = car_profit(r["car_idx"], r) * hours
                _en = await pool.fetchval("SELECT energy FROM users WHERE user_id = $1", r["user_id"])
                if _en is not None and float(_en) < ENERGY_LOW:  # انرژی کم = درآمد ماشین ۲۵٪ کمتر
                    profit = int(profit * CAR_LOW_MULT)
                async with pool.acquire() as c:
                    async with c.transaction():
                        moved = await c.fetchval("UPDATE cars SET last_pay=last_pay+$1,pending_profit=LEAST(pending_profit+$2, $6::bigint) WHERE user_id=$3 AND car_idx=$4 AND last_pay=$5 RETURNING 1", hours*CAR_PAY_EVERY, profit, r["user_id"], r["car_idx"], r["last_pay"], CAR_VAULT_CAP)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("car profit loop failed")
        await asyncio.sleep(60)


MSG_LOG_DAYS = int(os.environ.get("MSG_LOG_DAYS", "365"))   # پیام‌های گروه چند روز نگه داشته بشن (۰ = همیشه)
_LAST_PRUNE = 0.0
_BG_TASKS: set = set()
_PROFILE_SEEN: dict = {}

_MSG_KINDS = (("photo", "📷 عکس"), ("video", "🎞 ویدیو"), ("voice", "🎤 ویس"), ("video_note", "⭕ ویدیو‌مسیج"),
              ("sticker", "🎭 استیکر"), ("animation", "GIF"), ("audio", "🎵 آهنگ"), ("document", "📎 فایل"),
              ("poll", "📊 نظرسنجی"), ("contact", "👤 مخاطب"), ("location", "📍 لوکیشن"), ("dice", "🎲 تاس"))
_KIND_TAG = dict(_MSG_KINDS)


def _msg_label(msg) -> str:
    """متن پیام (یا برچسب نوع رسانه) برای نمایش به پشتیبان."""
    text = (msg.text or msg.caption or "").strip()
    tag = next((lab for attr, lab in _MSG_KINDS if getattr(msg, attr, None)), "")
    return (f"{tag} {text}".strip() if tag else text)[:4096]


def _msg_media(msg):
    """(نوع پیام, file_id عکس/تامبنیل برای نمایش تو مینی‌اپ)"""
    kind = next((k for k, _lab in _MSG_KINDS if getattr(msg, k, None)), None)
    fid = None
    try:
        if kind == "photo":
            ph = sorted(msg.photo, key=lambda p: p.width)
            fid = next((p.file_id for p in reversed(ph) if p.width <= 900), ph[0].file_id)
        elif kind == "sticker":
            st = msg.sticker
            fid = st.file_id if not (st.is_animated or st.is_video) else (st.thumbnail.file_id if st.thumbnail else None)
        elif kind in ("video", "animation", "video_note", "audio", "document"):
            th = getattr(msg, kind).thumbnail
            fid = th.file_id if th else None
    except Exception:
        fid = None
    return kind, fid


async def _touch_profile(user):
    """مشخصات تلگرامی کاربر (اسم، یوزرنیم، پرمیوم، زبان) برای صفحه‌ی پروفایل مینی‌اپ."""
    try:
        sig = (user.first_name, user.last_name, (user.username or "").lower(), bool(user.is_premium), user.language_code)
        now = time.time()
        old = _PROFILE_SEEN.get(user.id)
        if old and old[0] == sig and now - old[1] < 600:
            return
        _PROFILE_SEEN[user.id] = (sig, now)
        if len(_PROFILE_SEEN) > 50000:
            _PROFILE_SEEN.clear()
        await pool.execute(
            "INSERT INTO tg_profiles (user_id, first_name, last_name, username, is_premium, lang, first_seen, last_seen) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$7) ON CONFLICT (user_id) DO UPDATE SET first_name = EXCLUDED.first_name, "
            "last_name = EXCLUDED.last_name, username = EXCLUDED.username, is_premium = EXCLUDED.is_premium, "
            "lang = EXCLUDED.lang, last_seen = EXCLUDED.last_seen",
            user.id, user.first_name, user.last_name, user.username, bool(user.is_premium), user.language_code, now)
    except Exception:
        log.warning("profile save failed", exc_info=True)


async def _log_group_msg(chat_id: int, user, msg, edited: bool = False):
    global _LAST_PRUNE
    try:
        label = _msg_label(msg)
        if not label:
            return
        if edited:
            et = msg.edit_date.timestamp() if getattr(msg, "edit_date", None) else time.time()
            res = await pool.execute(
                "UPDATE group_msgs SET edited_text = $3, edited_ts = $4 WHERE chat_id = $1 AND msg_id = $2 AND text IS DISTINCT FROM $3",
                chat_id, msg.message_id, label, et)
            if res.endswith(" 1"):
                return
        kind, file_id = _msg_media(msg)
        ts = msg.date.timestamp() if getattr(msg, "date", None) else time.time()
        rm = msg.reply_to_message
        reply_to = reply_name = reply_text = None
        if rm is not None and not getattr(rm, "forum_topic_created", None):
            reply_to = rm.message_id
            ru = rm.from_user
            reply_name = (ru.full_name if ru else None) or (rm.sender_chat.title if rm.sender_chat else None)
            reply_text = _msg_label(rm)[:160] or None
        fo = getattr(msg, "forward_origin", None)
        fwd = None
        if fo is not None:
            fwd = (getattr(getattr(fo, "sender_user", None), "full_name", None) or getattr(fo, "sender_user_name", None)
                   or getattr(getattr(fo, "sender_chat", None), "title", None) or getattr(getattr(fo, "chat", None), "title", None) or "؟")
        await pool.execute(
            "INSERT INTO group_msgs (chat_id, msg_id, user_id, name, username, text, ts, kind, file_id, reply_to, reply_name, reply_text, fwd) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13) ON CONFLICT (chat_id, msg_id) DO NOTHING",
            chat_id, msg.message_id, user.id, user.full_name, user.username, label, ts, kind, file_id, reply_to, reply_name, reply_text, fwd)
        now = time.time()
        if MSG_LOG_DAYS > 0 and now - _LAST_PRUNE > 3600:
            _LAST_PRUNE = now
            await pool.execute("DELETE FROM group_msgs WHERE ts < $1", now - MSG_LOG_DAYS * 86400)
    except Exception:
        log.warning("group msg log failed", exc_info=True)


def _gm_fmt(rows, chat_id: int, budget: int = 3600) -> str:
    """پیام‌ها (جدیدترین اول ورودی) → متن مرتب قدیمی→جدید که تو یه پیام تلگرام جا بشه."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Tehran")
    out, used = [], 0
    for r in rows:
        t = datetime.fromtimestamp(r["ts"], tz).strftime("%m/%d %H:%M")
        un = f" @{html.escape(r['username'])}" if r["username"] else ""
        link = f' <a href="https://t.me/c/{str(chat_id)[4:]}/{r["msg_id"]}">↗</a>' if str(chat_id).startswith("-100") else ""
        block = (f"🕒 {t} • <b>{html.escape(r['name'] or str(r['user_id']))}</b>{un} • <code>{r['user_id']}</code>{link}\n"
                 f"{html.escape((r['text'] or '')[:400])}")
        if used + len(block) + 2 > budget:
            break
        out.append(block)
        used += len(block) + 2
    return "\n\n".join(reversed(out)) if out else "پیامی ثبت نشده."


# ───────────────────────── سیستم خلاف و سرقت گروهی ─────────────────────────
ROBBERIES = {
    "club": {"name": "کلاب", "cooldown": 25*60, "payout": (200_000, 500_000), "chance": 0.62, "penalty": 5, "lock": 30*60, "bail": 1_000_000, "fail": "وسط کلاب در حال جق زدن بودی که پلیس‌ها سر رسیدن و گیر افتادی!"},
    "cinema": {"name": "سینما", "cooldown": 35*60, "payout": (500_000, 850_000), "chance": 0.48, "penalty": 10, "lock": 60*60, "bail": 1_200_000, "fail": "در سینما مشغول تماشا بودی و تمرکزت رو از دست دادی؛ پلیس‌ها سر رسیدن و دستگیرت کردن!"},
    "shop": {"name": "سوپری", "cooldown": 45*60, "payout": (850_000, 1_000_000), "chance": 0.36, "penalty": 10, "lock": 90*60, "bail": 1_500_000, "fail": "در سوپری در حال جق زدن بودی که صاحب مغازه پلیس رو خبر کرد و گیر افتادی!"},
    "bank": {"name": "بانک مرکزی", "cooldown": 60*60, "payout": (1_000_000, 1_500_000), "chance": 0.18, "penalty": 15, "lock": 120*60, "bail": 2_000_000, "fail": "آژیرهای بانک مرکزی فعال شد؛ نیروهای ویژه محاصره‌ات کردن و دستگیرت کردن!"},
}
# سه سطح ریسک برای هر سرقت: کم‌ریسک (درآمد کم)، متوسط، پرریسک (درآمد بالا)
# chance: "low" = شانس موفقیت پایه + نصفِ فاصله تا ۱۰۰٪ (ولی هیچ‌وقت صددرصد نیست!)، "high" = ۶۰٪ شانس پایه
ROB_TIERS = {
    "low":  {"label": "کم‌ریسک", "dot": "🟢", "style": "success", "pay": 0.55, "penalty": 0.6, "lock": 0.6, "bail": 0.6, "cd": 0.7, "trace": 8,  "hint": "شانس موفقیت بالا، غنیمت کم"},
    "mid":  {"label": "متوسط",   "dot": "🟡", "style": "primary", "pay": 1.0,  "penalty": 1.0, "lock": 1.0, "bail": 1.0, "cd": 1.0, "trace": 12, "hint": "ریسک و درآمد معمولی"},
    "high": {"label": "پرریسک",  "dot": "🔴", "style": "danger",  "pay": 1.8,  "penalty": 1.5, "lock": 1.5, "bail": 1.5, "cd": 1.3, "trace": 18, "hint": "غنیمت خفن، ولی خیلی راحت گیر میفتی"},
}

# متن هر عملیات: (اسم عملیات، متن موفقیت، متن شکست)
ROB_TEXTS = {
    "club": {
        "low":  ("کیف‌قاپی از پیست رقص", "وسط شلوغیِ پیست رقص، ماهرانه یه کیف رو قاپیدی و قبل از اینکه DJ بیس رو بندازه، تو تاریکی گم شدی! 🕺", "وسط کیف‌قاپی، یکی از بادیگاردها مچ دستتو گرفت و بین موزیک و نور قرمز، صدای دستبند اومد! 🚨"),
        "mid":  ("خالی‌کردن صندوق بار", "با لباس گارسون رفتی پشت بار، صندوق رو خالی کردی و با یه لبخند از در پشتی رفتی بیرون! 🍸", "وسط شمردن اسکناس‌ها، چراغ‌ها روشن شد و پلیس‌های لباس‌شخصی دورتو گرفتن!"),
        "high": ("سرقت از گاوصندوق VIP", "گاوصندوق اتاق VIP رو با دست خالی باز کردی و قبل از اینکه آژیر کلاب بخونه، با کیسه‌ی پول ناپدید شدی! 💎🔥", "گاوصندوق VIP دوربین مخفی داشت! آژیر کلاب بلند شد، درها قفل شد و تیم ویژه دستگیرت کرد!"),
    },
    "cinema": {
        "low":  ("ترکوندن گیشه‌ی بلیط", "وقتی همه محو سکانس آخر فیلم بودن، آروم پولای گیشه رو جمع کردی و قبل از پایان فیلم رفتی بیرون! 🍿", "فروشنده‌ی گیشه یهو برگشت و جیب پر از پولت رو دید؛ دقیقاً وسط صحنه‌ی هیجانی فیلم گیر افتادی!"),
        "mid":  ("دزدی از بوفه و صندوق", "تو تاریکی سالن، بوفه و صندوق رو جارو کردی و فقط یه بسته پاپ‌کورن جا گذاشتی! 🎬", "نور پروژکتور افتاد روی صورتت؛ تمام سالن برگشتن سمتت و نگهبان‌ها دستگیرت کردن!"),
        "high": ("سرقت از اتاق پروژکتور و گاوصندوق مدیر", "از راهروی پشتی رفتی بالا، گاوصندوق مدیر رو خالی کردی و قبل از تیتراژ پایانی غیبت زد! 🎞️💰", "آژیر سالن وسط اکران فعال شد؛ سالن پر از نور شد و صدای پلیس‌ها از بلندگوها پیچید!"),
    },
    "shop": {
        "low":  ("کش‌رفتن از پیشخون", "تا فروشنده برگرده، چند تا اسکناس از پیشخون ناپدید شد و تو با یه آدامس تو دهنت از مغازه رفتی بیرون! 🍬", "فروشنده‌ی پیر چشم‌غره رفت، مچتو گرفت و تا پلیس برسه جلوی در مغازه نگهت داشت!"),
        "mid":  ("تخلیه‌ی صندوق مغازه", "حواس صاحب مغازه رو با یه سوال الکی پرت کردی و صندوق رو خالی کردی! 🛒", "صاحب مغازه با چماق از پشت اومد و پلیس رو خبر کرد؛ گیر افتادی!"),
        "high": ("سرقت مسلحانه‌ی سوپری", "کلاه‌کپ رو کشیدی روی صورتت، با یه نگاه سرد صاحب مغازه رو ساکت کردی و صندوق و گاوصندوق رو جارو کردی! 😎🔫", "دوربین مداربسته همه‌چیز رو ضبط کرد و دو دقیقه بعد ماشین پلیس جلوی در بود!"),
    },
    "bank": {
        "low":  ("حمله به باجه‌ی خودپرداز", "با یه دستگاه کوچیک، خودپرداز کنار بانک رو خالی کردی و قبل از اینکه امنیت بفهمه، محو شدی! 🏧", "خودپرداز آژیر کشید و دوربین‌های بانک مرکزی صورتت رو ثبت کردن؛ پلیس تا چند دقیقه بعد رسید!"),
        "mid":  ("نفوذ به صندوق داخلی بانک", "با کارت جعلی رفتی تو صندوق داخلی، چند کیف پول جمع کردی و بی‌سروصدا برگشتی! 🏦", "کد دسترسی جعلی لو رفت؛ درها بسته شد و نیروهای امنیتی محاصره‌ات کردن!"),
        "high": ("سرقت از طاق بانک مرکزی", "طاق بانک مرکزی رو با نقشه‌ی بی‌نقصت باز کردی، کیسه‌ها رو پر کردی و از کانال فاضلاب فرار کردی؛ از فردا اسم تو تو اخباره! 🏆💎", "آژیرهای بانک مرکزی بلند شد؛ نیروهای ویژه ساختمون رو محاصره کردن و فراری برات نموند!"),
    },
}


def tier_cfg(kind: str, tier: str) -> dict:
    base = ROBBERIES[kind]
    t = ROB_TIERS.get(tier) or ROB_TIERS["mid"]
    tier = tier if tier in ROB_TIERS else "mid"
    c = dict(base)
    if tier == "low":
        c["chance"] = min(0.92, base["chance"] + (1 - base["chance"]) * 0.5)
    elif tier == "high":
        c["chance"] = base["chance"] * 0.6
    c["payout"] = (int(base["payout"][0] * t["pay"]), int(base["payout"][1] * t["pay"]))
    c["penalty"] = min(30, max(1, round(base["penalty"] * t["penalty"])))
    c["lock"] = int(base["lock"] * t["lock"])
    c["bail"] = int(base["bail"] * t["bail"])
    c["cooldown"] = int(base["cooldown"] * t["cd"])
    c["trace_step"] = t["trace"]
    c["tier"] = tier
    c["op"], c["win_text"], c["fail"] = ROB_TEXTS[kind][tier]
    return c


ROBBERY_TRACE_STEP = 12
ROBBERY_TRACE_LIMIT = 100
ROBBERY_TRACE_CLEAN_COST = 1_300_000
ROBBERY_TRACE_CLEAN_AMOUNT = 12
ROBBERY_TRACE_LOCK = 2 * 60 * 60


def _duration_fa(seconds: int) -> str:
    seconds = max(0, int(seconds)); h, rem = divmod(seconds, 3600); m, sec = divmod(rem, 60)
    parts = []
    if h: parts.append(f"{h} ساعت")
    if m: parts.append(f"{m} دقیقه")
    if sec and not h: parts.append(f"{sec} ثانیه")
    return " و ".join(parts) if parts else "کمتر از یک ثانیه"


ROBBERY_CLEAN_PCT = 0.30   # هزینه‌ی «پاک‌کردن ردِ همین خلاف» = ۳۰٪ غنیمتی که همین الان گرفتی


def robbery_keyboard(uid: int, trace_available: bool = False) -> InlineKeyboardMarkup:
    """منوی انتخاب سرقت (فقط وقتی هیچ قفلی نداری نشون داده میشه)."""
    rows = [
        [btn("سرقت از کلاب", "primary", icon=EID_SWORDS, callback_data=f"rb:pick:{uid}:club")],
        [btn("سرقت از سینما", "primary", icon=EID_TROPHY, callback_data=f"rb:pick:{uid}:cinema")],
        [btn("سرقت از سوپری", "primary", icon=EID_MONEY, callback_data=f"rb:pick:{uid}:shop")],
        [btn("سرقت از بانک مرکزی", "danger", icon=EID_SWORDS, callback_data=f"rb:pick:{uid}:bank")],
    ]
    rows.append([btn("🔙 برگشت به پنل شخصی", "primary", icon=EID_BACK, callback_data=f"sp:home:{uid}")])
    return InlineKeyboardMarkup(rows)


def rob_view(owner: int, profile, now: float):
    """(متن، کیبورد) درست بر اساس وضعیت: بازداشت → فقط وثیقه | محرومیت رد → ردگم‌کن+تایمر | تایمر فعال → تایمر (+پاک‌کردن ردِ آخرین خلاف) | آزاد → گزینه‌های خلاف."""
    trace = int(profile['trace'] or 0)
    head = f"{SWORDS_CE} <b>بازار خلاف</b>\n\n{HG_CE} رد خلاف: <b>{trace}٪</b> از ۱۰۰٪\n"
    lock = f"<tg-emoji emoji-id=\"{CASINO_CLOSED_EID}\">🔒</tg-emoji>"
    timer_btn = btn("تایمر", "primary", icon=EID_HOURGLASS, callback_data=f"rb:timer:{owner}:club")
    if profile['arrest_until'] > now:
        bail = int(profile['bail_amount'] or 0) or ROBBERIES['club']['bail']
        text = (head + f"\n{lock} بازداشتی! تا آزادی یا پرداخت وثیقه نمی‌تونی خلاف کنی.\n"
                f"{HG_CE} زمان باقی‌مانده از بازداشت: <b>{_duration_fa(profile['arrest_until']-now)}</b>\nوثیقه آزادی: <b>{bail:,} تومان</b>")
        kb = [[btn(f"پرداخت وثیقه ({bail:,}) و آزادی", "success", icon=EID_MONEY, callback_data=f"rb:bail:{owner}:club")]]
        return text, InlineKeyboardMarkup(kb)
    if profile['trace_until'] > now:
        text = (head + f"\n{lock} ردت لو رفته؛ <b>{_duration_fa(profile['trace_until']-now)}</b> از همه‌ی خلاف‌ها محرومی؛ یا ردگم‌کن بخر.")
        kb = [[btn(f"ردگم‌کن | {ROBBERY_TRACE_CLEAN_COST:,} تومان", "danger", icon=EID_SWORDS, callback_data=f"rb:trace:{owner}:club")], [timer_btn]]
        return text, InlineKeyboardMarkup(kb)
    if profile['cooldown_until'] > now:
        text = head + f"\n{HG_CE} زمان باقی‌مانده تا خلاف بعدی: <b>{_duration_fa(profile['cooldown_until']-now)}</b>"
        kb = []
        cc = int(profile['clean_cost'] or 0)
        if cc > 0 and int(profile['clean_step'] or 0) > 0:
            text += f"\n\nمی‌تونی ردِ همین خلافت رو پاک کنی ({profile['clean_step']}٪ از رد کم می‌شه)."
            kb.append([btn(f"پاک‌کردن ردِ خودت | {cc:,} تومان", "danger", icon=EID_SWORDS, callback_data=f"rb:clean:{owner}:club")])
        kb.append([timer_btn])
        return text, InlineKeyboardMarkup(kb)
    text = (head + "\nیکی از سرقت‌ها رو انتخاب کن، بعد سطح ریسکش رو (کم‌ریسک / متوسط / پرریسک) خودت تعیین می‌کنی؛ هر اقدام به رد خلاف اضافه می‌کنه.\n"
            "بعد از هر عملیات، تایمر مشترک همه‌ی سرقت‌ها فعال می‌شه.")
    return text, robbery_keyboard(owner)


async def _rob_profile(uid: int):
    await pool.execute("INSERT INTO robbery_profile(user_id) VALUES($1) ON CONFLICT DO NOTHING", uid)
    return await pool.fetchrow("SELECT * FROM robbery_profile WHERE user_id=$1", uid)


async def show_robbery_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await upsert_user(user.id, user.full_name)
    text, kb = rob_view(user.id, await _rob_profile(user.id), time.time())
    await update.effective_message.reply_text(B(text), parse_mode="HTML", reply_markup=kb)


async def robbery_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; parts = q.data.split(":")
    if len(parts) not in (4, 5):
        await q.answer("درخواست نامعتبره.", show_alert=True); return
    action, uid_s, kind = parts[1], parts[2], parts[3]
    tier = parts[4] if len(parts) == 5 and parts[4] in ROB_TIERS else "mid"
    try: owner = int(uid_s)
    except ValueError: await q.answer("درخواست نامعتبره.", show_alert=True); return
    if q.from_user.id != owner:
        await q.answer("این منوی خلاف برای شخص دیگه‌ایه!", show_alert=True); return
    if kind not in ROBBERIES: await q.answer("این سرقت وجود نداره.", show_alert=True); return
    cfg = tier_cfg(kind, tier)
    if action == "pick":
        await q.answer()
        lines = [f"{SWORDS_CE} <b>سرقت از {ROBBERIES[kind]['name']}</b>", "", "چطوری می‌خوای بزنی؟ هرچی ریسک بیشتر، غنیمت بیشتر... ولی شانس کار دستته 🎲", ""]
        rows = []
        for tk, t in ROB_TIERS.items():
            c = tier_cfg(kind, tk)
            lines.append(f"{t['dot']} <b>{t['label']} — {c['op']}</b>\n    💰 {fmt_short(c['payout'][0])} تا {fmt_short(c['payout'][1])} تومان • {t['hint']}")
            rows.append([btn(f"{t['dot']} {t['label']} • {c['op']}", t["style"], callback_data=f"rb:do:{owner}:{kind}:{tk}")])
        lines += ["", "⚠️ حتی کم‌ریسک هم صددرصد امن نیست؛ ممکنه یهو گیر بیفتی!"]
        rows.append([btn("بازگشت به خلاف", "primary", icon=EID_BACK, callback_data=f"rb:menu:{owner}:{kind}")])
        await edit_html(q, B("\n".join(lines)), InlineKeyboardMarkup(rows)); return
    if action in ("menu", "timer"):
        text, kb = rob_view(owner, await _rob_profile(owner), time.time())
        await q.answer()
        await edit_html(q, B(text), kb); return

    now = time.time(); await upsert_user(owner, q.from_user.full_name)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("INSERT INTO robbery_profile(user_id) VALUES($1) ON CONFLICT DO NOTHING", owner)
            profile = await conn.fetchrow("SELECT * FROM robbery_profile WHERE user_id=$1 FOR UPDATE", owner)
            bal = await conn.fetchval("SELECT balance FROM users WHERE user_id=$1 FOR UPDATE", owner)
            if action == "trace":
                if profile['trace'] < ROBBERY_TRACE_LIMIT or profile['trace_until'] <= now:
                    await q.answer("الان نیازی به ردگم‌کن نداری.", show_alert=True); return
                if bal < ROBBERY_TRACE_CLEAN_COST:
                    await q.answer(f"برای ردگم‌کن {ROBBERY_TRACE_CLEAN_COST:,} تومان لازم داری.", show_alert=True); return
                new_balance = bal - ROBBERY_TRACE_CLEAN_COST
                new_trace = max(0, profile['trace'] - ROBBERY_TRACE_CLEAN_AMOUNT)
                await conn.execute("UPDATE users SET balance=balance-$1 WHERE user_id=$2", ROBBERY_TRACE_CLEAN_COST, owner)
                await conn.execute("UPDATE robbery_profile SET trace=$1, trace_until=0 WHERE user_id=$2", new_trace, owner)
            elif action == "clean":
                cc, cs = int(profile['clean_cost'] or 0), int(profile['clean_step'] or 0)
                if cc <= 0 or cs <= 0:
                    await q.answer("ردی برای پاک‌کردن نمونده.", show_alert=True); return
                if bal < cc:
                    await q.answer(f"برای پاک‌کردن رد {cc:,} تومان لازم داری.", show_alert=True); return
                new_balance = bal - cc
                new_trace = max(0, profile['trace'] - cs)
                await conn.execute("UPDATE users SET balance=balance-$1 WHERE user_id=$2", cc, owner)
                await conn.execute("UPDATE robbery_profile SET trace=$1, clean_cost=0, clean_step=0, trace_until=CASE WHEN $1 < $3 THEN 0 ELSE trace_until END WHERE user_id=$2", new_trace, owner, ROBBERY_TRACE_LIMIT)
            elif action == "bail":
                if profile['arrest_until'] <= now:
                    await q.answer("در بازداشت نیستی.", show_alert=True); return
                bail_cost = int(profile['bail_amount'] or 0) or cfg['bail']
                if bal < bail_cost:
                    await q.answer(f"وثیقه {bail_cost:,} تومنه؛ موجودیت کافی نیست.", show_alert=True); return
                new_balance = bal - bail_cost
                await conn.execute("UPDATE users SET balance=balance-$1 WHERE user_id=$2", bail_cost, owner)
                await conn.execute("UPDATE robbery_profile SET arrest_until=0, bail_amount=0 WHERE user_id=$1", owner)
            elif action == "do":
                if profile['trace'] >= ROBBERY_TRACE_LIMIT and profile['trace_until'] <= now:
                    await conn.execute("UPDATE robbery_profile SET trace=0 WHERE user_id=$1", owner)
                    profile = dict(profile)
                    profile['trace'] = 0
                active_lock = max(profile['arrest_until'], profile['trace_until'])
                if active_lock > now:
                    if profile['trace_until'] >= profile['arrest_until'] and profile['trace_until'] > now:
                        await q.answer("ردت لو رفته! ۲ ساعت محرومی یا ردگم‌کن ۱٫۳ میلیونی بخر.", show_alert=True); return
                    await q.answer(f"بازداشتی! تا آزادی یا پرداخت وثیقه نمی‌تونی خلاف کنی. زمان: {_duration_fa(active_lock-now)}", show_alert=True); return
                if profile['cooldown_until'] > now:
                    await q.answer(f"هنوز وقت خلاف بعدی نرسیده؛ {_duration_fa(profile['cooldown_until']-now)} صبر کن.", show_alert=True); return
                _en = await conn.fetchval("SELECT energy FROM users WHERE user_id=$1", owner)
                _chance = cfg['chance'] * (ROB_LOW_MULT if float(_en or 0) < ENERGY_LOW else 1)  # انرژی کم = بیشتر گیر میفته
                success = random.random() < _chance
                new_trace = min(ROBBERY_TRACE_LIMIT, profile['trace'] + cfg['trace_step'])
                trace_until = now + ROBBERY_TRACE_LOCK if new_trace >= ROBBERY_TRACE_LIMIT else 0
                arrest_until = now + cfg['lock'] if not success else 0
                await conn.execute("UPDATE robbery_profile SET trace=$1, cooldown_until=$2, arrest_until=GREATEST(arrest_until,$3), trace_until=GREATEST(trace_until,$4) WHERE user_id=$5",
                                   new_trace, now+cfg['cooldown'], arrest_until, trace_until, owner)
                if success:
                    payout = random.randint(*cfg['payout']); new_balance = bal + payout
                    await conn.execute("UPDATE users SET balance=balance+$1 WHERE user_id=$2", payout, owner)
                    await conn.execute("UPDATE robbery_profile SET clean_cost=$2, clean_step=$3, bail_amount=0 WHERE user_id=$1",
                                       owner, max(1, int(payout * ROBBERY_CLEAN_PCT)), cfg['trace_step'])
                else:
                    payout = 0; penalty = min(bal, int(bal * cfg['penalty'] / 100)); new_balance = bal - penalty
                    await conn.execute("UPDATE users SET balance=GREATEST(0,balance-$1) WHERE user_id=$2", penalty, owner)
                    await conn.execute("UPDATE robbery_profile SET bail_amount=$2, clean_cost=0, clean_step=0 WHERE user_id=$1", owner, cfg['bail'])
            else:
                await q.answer("درخواست نامعتبره.", show_alert=True); return

    prof_now = await _rob_profile(owner)
    _, view_kb = rob_view(owner, prof_now, time.time())
    if action == "trace":
        await q.answer("ردگم‌کن خریدی؛ ۱۲٪ از ردت پاک شد.", show_alert=True)
        text = (f"{SWORDS_CE} <b>ردگم‌کن فعال شد!</b>\n\n{MONEY_CE} هزینه: <b>{ROBBERY_TRACE_CLEAN_COST:,} تومان</b>\n"
                f"رد خلاف: <b>{new_trace}٪</b>\nموجودی جدید: <b>{new_balance:,} تومان</b>\n{HG_CE} تایمر انتظار قبلی همچنان پابرجاست.")
        await edit_html(q, B(text), view_kb); return
    if action == "clean":
        await q.answer("ردت پاک شد.", show_alert=True)
        text = (f"{SWORDS_CE} <b>ردت گم شد!</b>\n\n{MONEY_CE} هزینه: <b>{cc:,} تومان</b>\n"
                f"رد خلاف: <b>{new_trace}٪</b>\nموجودی جدید: <b>{new_balance:,} تومان</b>")
        await edit_html(q, B(text), view_kb); return
    if action == "bail":
        await q.answer("وثیقه پرداخت شد؛ از بازداشت آزاد شدی!", show_alert=True)
        text = (f"{ce('lock',2,'🔓')} <b>آزادی با وثیقه</b>\nوثیقه پرداختی: <b>{bail_cost:,} تومان</b>\n"
                f"موجودی جدید: <b>{new_balance:,} تومان</b>\nرد خلاف و تایمر عمومی همچنان پابرجاست.")
        await edit_html(q, B(text), view_kb); return
    if success:
        result = (f"{TROPHY_CE} <b>عملیات {html.escape(cfg['op'])} موفق بود!</b>\n\n<blockquote>{html.escape(cfg['win_text'])}</blockquote>\n\n"
                  f"{MONEY_CE} غنیمت: <b>+{payout:,} تومان</b>\nموجودی جدید: <b>{new_balance:,} تومان</b>\n"
                  f"{HG_CE} قفل مشترک تمام خلاف‌ها: <b>{_duration_fa(cfg['cooldown'])}</b>\nرد خلاف: <b>{new_trace}٪</b>\n\n"
                  f"ردِ خودت رو پاک کن تا لو نری!")
        if new_trace >= ROBBERY_TRACE_LIMIT:
            result += f"\n\n<tg-emoji emoji-id=\"{CASINO_CLOSED_EID}\">🔒</tg-emoji> ردت به ۱۰۰٪ رسید؛ ۲ ساعت محرومی مگه اینکه ردت رو پاک کنی."
        await q.answer("عملیات موفق بود؛ جایزه به حسابت نشست.")
        await edit_html(q, B(result), view_kb)
        await task_event(context.bot, q.message.chat_id, owner, "robbery")
    else:
        result = (f"<tg-emoji emoji-id=\"{CASINO_CLOSED_EID}\">🔒</tg-emoji> <b>عملیات {html.escape(cfg['op'])} لو رفت؛ دستگیر شدی!</b>\n\n<blockquote>{html.escape(cfg['fail'])}</blockquote>\n\n"
                  f"جریمه: <b>{cfg['penalty']}٪</b> موجودی ({penalty:,} تومان)\nموجودی باقی‌مانده: <b>{new_balance:,} تومان</b>\n"
                  f"مدت بازداشت: <b>{_duration_fa(cfg['lock'])}</b>\nوثیقه آزادی: <b>{cfg['bail']:,} تومان</b>\nرد خلاف: <b>{new_trace}٪</b>")
        await q.answer("گیر افتادی! پلیس دستگیرت کرد.", show_alert=True)
        await edit_html(q, B(result), view_kb)


async def is_channel_member(bot, uid: int):
    """True = عضو هر دو کانال، False = حداقل عضو یکی نیست، None = خطای بررسی."""
    if _MEMBER_OK.get(uid, 0) > time.time():
        return True
    for channel in FORCE_CHANNELS:
        try:
            m = await bot.get_chat_member(channel, uid)
        except Exception:
            log.warning("force-join check failed for %s (ربات باید در کانال ادمین باشد)", channel, exc_info=True)
            return None
        st = str(getattr(m, "status", ""))
        ok = st in ("creator", "administrator", "member") or (st == "restricted" and getattr(m, "is_member", False))
        if not ok:
            _MEMBER_OK.pop(uid, None)
            return False
    _MEMBER_OK[uid] = time.time() + 15
    if len(_MEMBER_OK) > 20000:
        _MEMBER_OK.clear()
    return True


def _join_kb():
    rows = [[btn(f"📢 عضویت در {ch}", "primary", url=f"https://t.me/{ch.lstrip('@')}")] for ch in FORCE_CHANNELS]
    rows.append([btn("✅ عضو شدم", "success", callback_data="fj:check")])
    return InlineKeyboardMarkup(rows)


def _join_text() -> str:
    channels = "\n".join(FORCE_CHANNELS)
    return B(f'<tg-emoji emoji-id="{FORCE_CHANNEL_EID}">📢</tg-emoji> برای استفاده از ربات باید در هر دو کانال زیر عضو باشی:\n{channels}\n\nبعد از عضویت دکمه‌ی «عضو شدم» رو بزن.')

def _is_bot_text_command(text: str) -> bool:
    """تشخیص کلمه‌ها/عبارت‌هایی که واقعاً یکی از قابلیت‌های ربات را اجرا می‌کنند."""
    raw = (text or "").strip()
    if not raw:
        return False
    if raw.startswith("/"):
        return True
    key = _word_key(raw)
    exact = {
        TRIGGER_WORD, ME_WORD, "تسک", "تسکها", "تسکروزانه", "تسکهفتگی", "tasks",
        "خودمایناروببین", "خودم", "پنلمن", "پنلشخصی", "دست", "بانک",
        "خونه", "خانه", "املاک", "ازدواج", "خواستگاری", "همسرم", "خانواده",
        "خانوادهزنم", "خانوادههمسرم", "خلاف", "دزدی", "سرقت", "دوئلجقی", "دوئلجق",
        "جاسوسی", "استعلامجاسوسی", "خریدجاسوسی", "خریدابزارجاسوسی", "کازینو", "casino", "فروشگاه", "سکس", "موشک", "کراش",
        "قهر", "آشتی", "اشتی", "بغل", "بقل", "بوس", "بوش", "هدیه", "هدیهها", "gifts", "gift",
    }
    if key in exact or key in WALLET_WORDS or key in TOP_WORDS or key in JOBS_WORDS or key in SHIFT_WORDS or key in CAR_WORDS or key in GIFT_LIST_WORDS or key in HUG_WORDS or key in RIFT_WORDS or key in PEACE_WORDS or key in PAIR_FUN_WORDS:
        return True
    if key.startswith(("واریز", "واريز", "برداشت", "انتقال", "هدیه", "خریدجاسوسی", "ایونت", "پایانایونت")):
        return True
    t = norm_word(raw).translate(_DIG)
    return bool(re.fullmatch(r"(?:میز\s*)?(?:تاس|اسلات|مین|موشک|کراش)(?:\s+.+)?|(?:تاس|اسلات)\s*میز\s+.+", t, re.I))


async def gate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """قبل از همه‌ی هندلرها: ثبت گروه‌ها + اگه ربات خاموش باشه فقط پشتیبان‌ها کار می‌کنن."""
    user, chat = update.effective_user, update.effective_chat
    if user and user.username and not user.is_bot:
        try:  # یوزرنیم ذخیره میشه تا پشتیبان بتونه با @یوزرنیم هم کاربر رو پیدا کنه
            await pool.execute(
                "UPDATE users SET username = $1 WHERE user_id = $2 AND username IS DISTINCT FROM $1",
                user.username.lower(), user.id)
        except Exception:
            log.warning("username save failed", exc_info=True)
    _gm = update.message or update.edited_message
    if user and not user.is_bot and chat and chat.type in ("group", "supergroup") and _gm:
        _t = asyncio.create_task(_log_group_msg(chat.id, user, _gm, edited=update.edited_message is not None))
        _BG_TASKS.add(_t)
        _t.add_done_callback(_BG_TASKS.discard)
    if user and not user.is_bot:
        _t2 = asyncio.create_task(_touch_profile(user))
        _BG_TASKS.add(_t2)
        _t2.add_done_callback(_BG_TASKS.discard)
    if user and not user.is_bot and user.id not in ADMIN_IDS and user.id in BANNED:
        until = BANNED[user.id]
        if until <= time.time():  # محرومیت تموم شده
            BANNED.pop(user.id, None)
            try:
                await pool.execute("UPDATE users SET banned_until = 0 WHERE user_id = $1", user.id)
            except Exception:
                log.warning("unban save failed", exc_info=True)
        else:
            if update.callback_query:
                try:
                    await update.callback_query.answer(f"⛔ شما {_ban_until_txt(until)} از ربات محروم هستید.", show_alert=True)
                except Exception:
                    pass
            elif chat and chat.type == "private" and update.effective_message:
                try:
                    await update.effective_message.reply_text(f"⛔ شما {_ban_until_txt(until)} از ربات محروم هستید.")
                except Exception:
                    pass
            raise ApplicationHandlerStop
    # عضویت اجباری فقط هنگام اجرای دستورهای اسلش یا فشردن دکمه‌های ربات بررسی می‌شود؛
    # پیام‌های عادی کاربران در گروه/پی‌وی نباید پیام عضویت تولید کنند.
    _msg_for_join = update.effective_message
    # ثبت دعوت زیرمجموعه: /start ref_<id> (قبل از بررسی عضویت، چون نامعضو از اینجا جلوتر نمیره)
    if (user and not user.is_bot and chat and chat.type == "private" and _msg_for_join and _msg_for_join.text
            and _msg_for_join.text.startswith("/start ref_")):
        await upsert_user(user.id, user.full_name)
        await register_referral(user, _msg_for_join.text.split(" ", 1)[1].strip())
    _is_bot_command = bool(_msg_for_join and _msg_for_join.text and _is_bot_text_command(_msg_for_join.text))
    # قرعه‌کشی عضویتِ کانال‌های خودش رو چک می‌کنه؛ عضویت اجباری ربات جلوش رو نمی‌گیره
    _gw_exempt = bool((update.callback_query and (update.callback_query.data or "").startswith("gw:")) or (
        _msg_for_join and _msg_for_join.text and re.match(r"/start(?:@\w+)? gw(?:top)?_\d+", _msg_for_join.text)))
    if (user and not user.is_bot and user.id not in ADMIN_IDS and chat and chat.type != "channel"
            and not _gw_exempt and (update.callback_query or _is_bot_command)):
        cq = update.callback_query
        if cq and cq.data == "fj:check":
            ok = await is_channel_member(context.bot, user.id)
            if ok is not True:
                await cq.answer("❌ هنوز عضویتت در هر دو کانال تایید نشده! اول عضو شو و دوباره بزن.", show_alert=True)
            else:
                await cq.answer("✅ عضویتت تایید شد، حالا می‌تونی از ربات استفاده کنی.", show_alert=True)
                await process_referral(context.bot, user.id)
                try:
                    await cq.message.delete()
                except Exception:
                    pass
            raise ApplicationHandlerStop
        ok = await is_channel_member(context.bot, user.id)
        if ok is not True:  # اگر عضویت قابل تأیید نباشد، دسترسی داده نمی‌شود
            if cq:
                try:
                    await cq.answer("📢 برای استفاده باید عضو هر دو کانال @jagh_game و @selfsaz_iran باشی.", show_alert=True)
                except Exception:
                    pass
            else:
                now = time.time()
                k = (chat.id, user.id)
                gap = 3 if chat.type == "private" else 120   # تو گروه هر ۲ دقیقه یه بار پیام عضویت (اسپم نشه)
                if now - _JOIN_PROMPTED.get(k, 0) >= gap:
                    _JOIN_PROMPTED[k] = now
                    if len(_JOIN_PROMPTED) > 20000:
                        _JOIN_PROMPTED.clear()
                    msg = update.effective_message
                    try:
                        await send_html(context.bot, chat.id, _join_text(), reply_markup=_join_kb(),
                                        reply_parameters=ReplyParameters(message_id=msg.message_id, allow_sending_without_reply=True))
                    except Exception:
                        log.warning("join prompt failed", exc_info=True)
            raise ApplicationHandlerStop
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


def admin_menu_kb(user_id: int | None = None) -> InlineKeyboardMarkup:
    uid = user_id
    hp = lambda p: uid is not None and has_perm(uid, p)
    rows = []
    # هر دکمه فقط برای کسی که دسترسیش رو داره نشون داده میشه (صاحب‌ها همه‌شون رو دارن)
    if WEBAPP_URL.startswith("https://") and hp("miniapp"):
        rows.append([btn("📱 مینی‌اپ پنل پشتیبانی", "success", web_app=WebAppInfo(url=WEBAPP_URL))])
    if uid in OWNER_IDS:
        rows.append([btn("➕ افزودن پشتیبانی", "success", callback_data="sup:add"),
                     btn("📋 لیست پشتیبان‌ها", "primary", callback_data="sup:list")])
    elif uid is not None and has_perm(uid, "staff") and STAFF_MANAGE.get(uid):
        rows.append([btn("🛡 دسترسی پشتیبان‌ها", "primary", callback_data="sup:list")])
    elif hp("staff_list"):
        rows.append([btn("📋 لیست پشتیبانی", "primary", callback_data="ad:slist")])

    def add(*items):
        r = [b for p, b in items if hp(p)]
        if r:
            rows.append(r)

    add(("money_sub", btn("💸 کم کردن پول", "primary", callback_data="ad:money")),
        ("money_add", btn("➕ افزودن پول", "success", callback_data="ad:money_add")))
    add(("jagh_sub", btn("🤛 کم کردن جق", "primary", callback_data="ad:jagh")),
        ("jagh_add", btn("➕ افزودن جق", "success", callback_data="ad:jagh_add")))
    add(("ban", btn("🚫 محروم کردن کاربر", "danger", callback_data="ad:ban")),
        ("unban", btn("✅ رفع محرومیت", "success", callback_data="ad:unban")))
    add(("task_daily", btn("📋 افزودن تسک روزانه", "primary", callback_data="ad:task_daily")),
        ("task_weekly", btn("📆 افزودن تسک هفتگی", "primary", callback_data="ad:task_weekly")))
    add(("bot_toggle", btn("🔴 خاموش کردن ربات", "danger", callback_data="ad:toggle") if BOT_ENABLED
         else btn("🟢 روشن کردن ربات", "success", callback_data="ad:toggle")))
    add(("broadcast", btn("📢 پیام همگانی", "primary", callback_data="ad:bc")),
        ("stats", btn("📊 آمار", "primary", callback_data="ad:stats")))
    add(("group_msgs", btn("👁 پیام‌های گروه‌ها", "primary", callback_data="ad:gm")))
    add(("giveaway", btn("🎁 قرعه‌کشی", "success", callback_data="ad:gw")))
    if not rows:
        rows.append([btn("🔒 هنوز دسترسی‌ای برات فعال نشده", callback_data="ad:noop")])
    return InlineKeyboardMarkup(rows)


# دکمه‌ی هر عملیات /admin → دسترسی لازم
_ADMIN_ACT_PERM = {
    "money": "money_sub", "money_add": "money_add", "jagh": "jagh_sub", "jagh_add": "jagh_add",
    "gm": "group_msgs", "gl": "group_msgs", "gu0": "group_msgs", "gu": "group_msgs",
    "ban": "ban", "unban": "unban", "task_daily": "task_daily", "task_weekly": "task_weekly",
    "toggle": "bot_toggle", "stats": "stats", "bc": "broadcast", "bcgo": "broadcast", "slist": "staff_list", "gw": "giveaway",
}


def _adm_state_perm(st: dict):
    """دسترسی لازم برای مرحله‌ی در حال انجام (اگه وسط کار دسترسی گرفته بشن، ادامه نمی‌دن)."""
    act = st.get("act")
    if act == "task_add":
        return "task_daily" if st.get("period") == "daily" else "task_weekly"
    return {"money": "money_sub", "money_add": "money_add", "jagh": "jagh_sub", "jagh_add": "jagh_add",
            "gmu": "group_msgs", "ban": "ban", "unban": "unban", "bc": "broadcast", "gw": "giveaway"}.get(act)


def admin_menu_text() -> str:
    return f"🛠 پنل پشتیبانی\n\nوضعیت ربات: {'🟢 روشن' if BOT_ENABLED else '🔴 خاموش'}"


CANCEL_KB = InlineKeyboardMarkup([[btn("❌ لغو", "danger", callback_data="ad:cancel")]])


async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/admin — فقط پشتیبان‌ها، تو پی‌وی ربات."""
    if not has_perm(update.effective_user.id, "panel"):
        return
    context.user_data.pop("adm", None)
    context.user_data.pop("await_jobs", None)
    await update.effective_message.reply_text(admin_menu_text(), reply_markup=admin_menu_kb(update.effective_user.id))



# ───────────────────────── مدیریت پشتیبان‌ها و دسترسی‌هاشون (فقط صاحب‌ها) ─────────────────────────
async def _staff_name(uid: int) -> str:
    try:
        r = await pool.fetchrow("SELECT name, username FROM users WHERE user_id=$1", uid)
    except Exception:
        r = None
    if not r:
        return str(uid)
    return (r["name"] or str(uid)) + (f" (@{r['username']})" if r["username"] else "")


async def _save_staff(uid: int):
    await pool.execute(
        "INSERT INTO support_staff(user_id, perms) VALUES($1,$2) ON CONFLICT(user_id) DO UPDATE SET perms=EXCLUDED.perms",
        uid, ",".join(sorted(STAFF.get(uid, set()))),
    )


def is_managed_support(uid: int) -> bool:
    """پشتیبانی که یه پشتیبانِ مدیر انتخابش کرده: مینی‌اپ (و پیام‌های گروه‌ها) براش فعال نیست. صاحب‌ها هیچ‌وقت شامل نمیشن."""
    return uid not in OWNER_IDS and any(uid in v for v in STAFF_MANAGE.values())


def can_manage_staff(actor: int, target: int) -> bool:
    """صاحب‌ها همه‌ی پشتیبان‌ها رو مدیریت می‌کنن؛ پشتیبانِ دارای دسترسی «staff» فقط پشتیبان‌هایی که صاحب براش انتخاب کرده."""
    if target in OWNER_IDS or target not in STAFF:
        return False
    if actor in OWNER_IDS:
        return True
    return "staff" in STAFF.get(actor, ()) and target in STAFF_MANAGE.get(actor, ()) and target != actor


def grantable_perms(actor: int) -> dict:
    """دسترسی‌هایی که actor می‌تونه روشن/خاموش کنه. پشتیبان فقط چیزهایی رو که خودش داره (و نه «staff») می‌تونه بده/بگیره."""
    if actor in OWNER_IDS:
        return dict(SUPPORT_PERMS)
    mine = STAFF.get(actor, set())
    return {k: v for k, v in SUPPORT_PERMS.items() if k != "staff" and k in mine}


def can_toggle_perm(actor: int, uid: int, perm: str) -> bool:
    """صاحب‌ها همه‌چیز؛ پشتیبانِ مدیر: هر دسترسیِ روشن رو می‌تونه از پشتیبان انتخاب‌شده بگیره، ولی فقط دسترسی‌هایی رو می‌تونه بده که خودش داره (و «staff» رو هیچ‌وقت)."""
    if actor in OWNER_IDS:
        return True
    if perm == "staff" or perm not in SUPPORT_PERMS:
        return False
    if perm in OWNER_GRANT_ONLY:   # مینی‌اپ و پیام‌های گروه رو پشتیبانِ مدیر فقط می‌تونه بگیره، نه بده
        return perm in STAFF.get(uid, set())
    return perm in STAFF.get(uid, set()) or perm in STAFF.get(actor, set())


async def _staff_list_view(actor: int | None = None):
    owner = actor is None or actor in OWNER_IDS
    rows = []
    for uid, perms in sorted(STAFF.items()):
        if not owner and not can_manage_staff(actor, uid):
            continue
        rows.append([InlineKeyboardButton(f"👤 {await _staff_name(uid)} — {len(perms)} دسترسی", callback_data=f"sup:u:{uid}")])
    if owner:
        text = "🛡 پشتیبان‌ها\n\n" + ("روی هر پشتیبان بزن تا دسترسی‌هاش رو عوض کنی." if rows else "هنوز پشتیبانی اضافه نکردی.")
        rows.append([InlineKeyboardButton("➕ افزودن پشتیبانی", callback_data="sup:add")])
        text += "\n\nبرای افزودن پشتیبان جدید، دکمه‌ی افزودن پشتیبانی رو بزن."
    else:
        text = "🛡 پشتیبان‌های قابل مدیریت\n\n" + ("روی هر پشتیبان بزن تا دسترسی‌هاش رو بدی یا بگیری." if rows else "هنوز صاحب ربات پشتیبانی برات انتخاب نکرده.")
        rows.append([InlineKeyboardButton("⬅️ پنل", callback_data="ad:menu")])
    return text, InlineKeyboardMarkup(rows)


async def _staff_user_view(uid: int, actor: int | None = None):
    owner = actor is None or actor in OWNER_IDS
    perms = STAFF.get(uid, set())

    def perm_btn(k: str, fa: str) -> InlineKeyboardButton:
        mark = "✅ " if k in perms else "❌ "
        if owner or can_toggle_perm(actor, uid, k):
            return InlineKeyboardButton(mark + fa, callback_data=f"sup:t:{uid}:{k}")
        return InlineKeyboardButton(mark + fa + " 🔒", callback_data="sup:noop")   # فقط نمایش؛ دست‌نخوردنی

    rows = [[perm_btn(k, fa)] for k, fa in SUPPORT_PERMS.items() if k not in ADMIN_PERMS]
    btns = [perm_btn(k, fa) for k, fa in ADMIN_PERMS.items()]
    rows += [btns[i:i + 2] for i in range(0, len(btns), 2)]   # دکمه‌های /admin دوتایی‌دوتایی
    if owner:
        rows.append([InlineKeyboardButton("✅ روشن کردن همه‌ی دکمه‌ها", callback_data=f"sup:all:{uid}:1"),
                     InlineKeyboardButton("❌ خاموش کردن همه", callback_data=f"sup:all:{uid}:0")])
        if "staff" in perms:
            rows.append([InlineKeyboardButton(f"👥 پشتیبان‌های قابل مدیریت ({len(STAFF_MANAGE.get(uid, ()))})", callback_data=f"sup:mg:{uid}")])
        rows.append([InlineKeyboardButton("حذف از پشتیبانی 🗑", callback_data=f"sup:del:{uid}")])
    rows.append([InlineKeyboardButton("⬅️ برگشت", callback_data="sup:list")])
    hint = "هر دسترسی رو با زدن روش روشن/خاموش کن:" if owner else "هر دسترسیِ روشن رو می‌تونی بگیری؛ فقط دسترسی‌هایی که خودت داری رو می‌تونی بدی (🔒 = دست‌نخوردنی):"
    return (f"🛡 {await _staff_name(uid)}\nآیدی: {uid}\n\n{hint}\n"
            "(بالا: دسترسی‌های اصلی • پایین: دسترسی تک‌تک دکمه‌های /admin و مینی‌اپ)"), InlineKeyboardMarkup(rows)


async def _staff_manage_view(manager: int):
    """انتخاب اینکه این پشتیبان روی کدوم پشتیبان‌ها اجازه‌ی مدیریت دسترسی داره (فقط صاحب‌ها)."""
    sel = STAFF_MANAGE.get(manager, set())
    rows = []
    for uid in sorted(STAFF):
        if uid == manager:
            continue
        rows.append([InlineKeyboardButton(("✅ " if uid in sel else "❌ ") + await _staff_name(uid), callback_data=f"sup:mt:{manager}:{uid}")])
    rows.append([InlineKeyboardButton("⬅️ برگشت", callback_data=f"sup:u:{manager}")])
    text = f"👥 پشتیبان‌های قابل مدیریت برای {await _staff_name(manager)}\n\nهر کدوم که ✅ باشه، این پشتیبان می‌تونه دسترسی‌هاش رو بده یا بگیره:"
    if len(rows) == 1:
        text += "\n\n(پشتیبان دیگه‌ای وجود نداره.)"
    return text, InlineKeyboardMarkup(rows)


async def support_add_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    msg=update.effective_message; user=update.effective_user
    if not msg or not msg.text or not context.user_data.get("support_add_wait"):
        return False
    if user.id not in OWNER_IDS:
        context.user_data.pop("support_add_wait",None); return False
    value=msg.text.strip()
    if value=="لغو":
        context.user_data.pop("support_add_wait",None); await msg.reply_text("افزودن پشتیبان لغو شد."); return True
    uid=None
    if value.lstrip("-").isdigit(): uid=int(value)
    elif re.fullmatch(r"@?[A-Za-z0-9_]{3,32}",value):
        uid=await pool.fetchval("SELECT user_id FROM users WHERE lower(username)=lower($1)",value.lstrip("@"))
    if uid is None:
        await msg.reply_text("کاربر پیدا نشد؛ آیدی عددی یا @یوزرنیم معتبر بفرست."); return True
    if uid in OWNER_IDS:
        await msg.reply_text("این کاربر صاحب رباته و از قبل دسترسی کامل داره."); return True
    STAFF.setdefault(uid,set()); ADMIN_IDS.add(uid); await _save_staff(uid)
    context.user_data.pop("support_add_wait",None)
    text,kb=await _staff_user_view(uid)
    await msg.reply_text("✅ پشتیبان اضافه شد. دسترسی‌هاش رو مشخص کن:\n\n"+text,reply_markup=kb)
    return True


async def support_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/support — لیست پشتیبان‌ها و تنظیم دسترسی‌هاشون (فقط صاحب‌ها، تو پی‌وی)."""
    if update.effective_user.id not in OWNER_IDS:
        return
    text, kb = await _staff_list_view()
    await update.effective_message.reply_text(text, reply_markup=kb)


async def add_support_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/addsupport <آیدی عددی یا @یوزرنیم>"""
    msg = update.effective_message
    if update.effective_user.id not in OWNER_IDS:
        return
    arg = (context.args[0] if context.args else "").strip()
    uid = None
    if arg.isdigit():
        uid = int(arg)
    elif arg.startswith("@") and len(arg) > 1:
        uid = await pool.fetchval("SELECT user_id FROM users WHERE lower(username)=lower($1)", arg[1:])
        if uid is None:
            await msg.reply_text("این یوزرنیم رو تو ربات نمی‌شناسم. با آیدی عددی اضافه‌اش کن (از /myid).")
            return
    else:
        await msg.reply_text("مثال: /addsupport 123456789  یا  /addsupport @username")
        return
    uid = int(uid)
    if uid in OWNER_IDS:
        await msg.reply_text("این کاربر صاحب رباته و همه‌ی دسترسی‌ها رو داره.")
        return
    if uid not in STAFF:
        STAFF[uid] = set()
        ADMIN_IDS.add(uid)
        await _save_staff(uid)
    text, kb = await _staff_user_view(uid)
    await msg.reply_text("✅ پشتیبان اضافه شد. دسترسی‌هاش رو انتخاب کن:\n\n" + text, reply_markup=kb)


async def support_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    actor = q.from_user.id
    owner = actor in OWNER_IDS
    manager = (not owner) and has_perm(actor, "staff") and has_perm(actor, "panel")
    if not owner and not manager:
        await q.answer("فقط صاحب ربات", show_alert=True)
        return
    parts = q.data.split(":")
    act = parts[1]
    if act == "noop":
        await q.answer("این دسترسی رو خودت نداری یا اجازه‌ی تغییرش رو نداری.", show_alert=True)
        return
    if not owner and act not in ("list", "u", "t"):
        await q.answer("فقط صاحب ربات", show_alert=True)
        return
    if act in ("t", "u", "mg", "mt", "del") and len(parts) > 2 and not owner and not can_manage_staff(actor, int(parts[2])):
        await q.answer("این پشتیبان برای تو انتخاب نشده.", show_alert=True)
        return
    if act == "t" and len(parts) > 3 and not can_toggle_perm(actor, int(parts[2]), parts[3]):
        await q.answer("این دسترسی رو نمی‌تونی تغییر بدی.", show_alert=True)
        return
    await q.answer()
    if act == "add":
        context.user_data["support_add_wait"] = True
        await q.message.reply_text("آیدی عددی یا @یوزرنیم پشتیبان جدید رو بفرست. کاربر باید قبلاً ربات رو استارت کرده باشه. برای لغو بنویس «لغو».")
        return
    if act == "list":
        text, kb = await _staff_list_view(actor)
    elif act == "mg":
        manager_id = int(parts[2])
        text, kb = await _staff_manage_view(manager_id) if manager_id in STAFF else await _staff_list_view(actor)
    elif act == "mt":
        manager_id, target = int(parts[2]), int(parts[3])
        if manager_id in STAFF and target in STAFF and target != manager_id:
            if target in STAFF_MANAGE.get(manager_id, set()):
                STAFF_MANAGE[manager_id].discard(target)
                await pool.execute("DELETE FROM support_manage WHERE manager_id=$1 AND target_id=$2", manager_id, target)
            else:
                STAFF_MANAGE.setdefault(manager_id, set()).add(target)
                await pool.execute("INSERT INTO support_manage(manager_id, target_id) VALUES($1,$2) ON CONFLICT DO NOTHING", manager_id, target)
            text, kb = await _staff_manage_view(manager_id)
        else:
            text, kb = await _staff_list_view(actor)
    else:
        uid = int(parts[2])
        if act == "t" and uid in STAFF and can_toggle_perm(actor, uid, parts[3]):
            STAFF[uid] ^= {parts[3]}
            await _save_staff(uid)
            if parts[3] == "staff" and "staff" not in STAFF[uid]:   # دسترسی مدیریت گرفته شد → لیست مدیریت‌شونده‌ها پاک
                STAFF_MANAGE.pop(uid, None)
                await pool.execute("DELETE FROM support_manage WHERE manager_id=$1", uid)
        elif act == "all" and owner and uid in STAFF and len(parts) > 3:   # روشن/خاموش کردنِ یک‌جای همه‌ی دکمه‌های /admin
            if parts[3] == "1":
                STAFF[uid] |= set(ADMIN_PERMS)
            else:
                STAFF[uid] -= set(ADMIN_PERMS)
            await _save_staff(uid)
        elif act == "del" and owner:
            STAFF.pop(uid, None)
            STAFF_MANAGE.pop(uid, None)
            for v in STAFF_MANAGE.values():
                v.discard(uid)
            if uid not in OWNER_IDS:
                ADMIN_IDS.discard(uid)
            await pool.execute("DELETE FROM support_staff WHERE user_id=$1", uid)
            await pool.execute("DELETE FROM support_manage WHERE manager_id=$1 OR target_id=$1", uid)
            text, kb = await _staff_list_view(actor)
            await q.edit_message_text("🗑 پشتیبان حذف شد.\n\n" + text, reply_markup=kb)
            return
        if uid not in STAFF:
            text, kb = await _staff_list_view(actor)
        else:
            text, kb = await _staff_user_view(uid, actor)
    try:
        await q.edit_message_text(text, reply_markup=kb)
    except Exception:
        pass


async def admin_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global BOT_ENABLED
    q = update.callback_query
    if not has_perm(q.from_user.id, "panel"):
        await q.answer("فقط پشتیبان‌ها", show_alert=True)
        return
    parts = q.data.split(":")
    act = parts[1]
    if act == "noop":
        await q.answer("صاحب ربات هنوز دسترسی‌ای برات روشن نکرده.", show_alert=True)
        return
    _need_perm = _ADMIN_ACT_PERM.get(act)
    if _need_perm and not has_perm(q.from_user.id, _need_perm):
        await q.answer("این دسترسی برات فعال نیست.", show_alert=True)
        return
    await q.answer()
    if act in ("menu", "cancel"):
        context.user_data.pop("adm", None)
        await q.edit_message_text(admin_menu_text(), reply_markup=admin_menu_kb(q.from_user.id))
    elif act in ("money", "jagh", "money_add", "jagh_add"):
        context.user_data["adm"] = {"act": act, "step": "target"}
        what = "پول" if act.startswith("money") else "جق"
        operation = "اضافه کردن" if act.endswith("_add") else "کم کردن"
        await q.edit_message_text(
            f"{operation} {what}\n\nآیدی عددی یا @یوزرنیم کاربر رو بفرست، یا یه پیام ازش اینجا فوروارد کن.\n(لغو: بنویس «لغو»)",
            reply_markup=CANCEL_KB,
        )
    elif act == "gw":
        await gw_admin_cb(update, context, parts)
    elif act == "slist":   # لیست فقط‌خواندنیِ پشتیبان‌ها
        names = [f"• {await _staff_name(u)} — {u}" for u in sorted(STAFF)]
        await q.edit_message_text("📋 لیست پشتیبانی\n\n" + ("\n".join(names) if names else "هنوز پشتیبانی اضافه نشده."),
                                  reply_markup=InlineKeyboardMarkup([[btn("🔙 پنل", callback_data="ad:menu")]]))
    elif act == "gm":   # لیست گروه‌هایی که ربات توشونه
        context.user_data.pop("adm", None)
        rows = await pool.fetch(
            "SELECT c.chat_id, c.title, (SELECT count(*) FROM group_msgs g WHERE g.chat_id = c.chat_id) AS n "
            "FROM chats c ORDER BY n DESC, c.title LIMIT 30")
        kb = [[btn(f"{(r['title'] or str(r['chat_id']))[:30]} • {r['n']}", "primary", callback_data=f"ad:gl:{r['chat_id']}")] for r in rows]
        kb.append([btn("🔙 پنل", callback_data="ad:menu")])
        await q.edit_message_text(
            f"👁 پیام‌های گروه‌ها (ثبت‌شده تو {MSG_LOG_DAYS} روز اخیر)\nیه گروه رو انتخاب کن:" if rows else "هنوز گروهی ثبت نشده.",
            reply_markup=InlineKeyboardMarkup(kb))
    elif act in ("gl", "gu0"):  # آخرین پیام‌های یه گروه
        try:
            cid = int(parts[2])
        except (IndexError, ValueError):
            return
        rows = await pool.fetch("SELECT msg_id, user_id, name, username, text, ts FROM group_msgs WHERE chat_id = $1 ORDER BY ts DESC LIMIT 40", cid)
        title = await pool.fetchval("SELECT title FROM chats WHERE chat_id = $1", cid) or cid
        kb = InlineKeyboardMarkup([
            [btn("👤 پیام‌های یه کاربر", "primary", callback_data=f"ad:gu:{cid}"), btn("🔄 تازه‌سازی", "success", callback_data=f"ad:gl:{cid}")],
            [btn("🔙 گروه‌ها", callback_data="ad:gm")],
        ])
        try:
            await q.edit_message_text(f"👁 <b>{html.escape(str(title))}</b>\n\n{_gm_fmt(rows, cid)}", parse_mode="HTML", reply_markup=kb)
        except Exception as e:
            if "not modified" not in str(e).lower():
                raise
    elif act == "gu":   # پیام‌های یه کاربر تو یه گروه
        try:
            cid = int(parts[2])
        except (IndexError, ValueError):
            return
        context.user_data["adm"] = {"act": "gmu", "step": "target", "cid": cid}
        await q.edit_message_text(
            "👤 آیدی عددی یا @یوزرنیم کاربر رو بفرست، یا یه پیام ازش اینجا فوروارد کن.\n(لغو: بنویس «لغو»)",
            reply_markup=CANCEL_KB)
    elif act in ("ban", "unban"):
        context.user_data["adm"] = {"act": act, "step": "target"}
        title = "🚫 محروم کردن کاربر" if act == "ban" else "✅ رفع محرومیت"
        await q.edit_message_text(
            f"{title}\n\nآیدی عددی یا @یوزرنیم کاربر رو بفرست، یا یه پیام ازش اینجا فوروارد کن.\n(لغو: بنویس «لغو»)",
            reply_markup=CANCEL_KB,
        )
    elif act in ("task_daily", "task_weekly"):
        period = "daily" if act == "task_daily" else "weekly"
        context.user_data["adm"] = {"act": "task_add", "period": period, "step": "metric"}
        _mk = list(TASK_METRICS.items())
        _rows = [[btn(v[0], "primary", callback_data=f"ad:tm:{k}") for k, v in _mk[i:i + 2]] for i in range(0, len(_mk), 2)]
        _rows.append([btn("✍️ تسک متنی (بدون جایزه)", callback_data="ad:tm:_text")])
        _rows.append([btn("❌ لغو", "danger", callback_data="ad:cancel")])
        await q.edit_message_text(
            f"{'📋 تسک روزانه' if period == 'daily' else '📆 تسک هفتگی'} جدید\n\nمعیار تسک رو انتخاب کن؛ ربات خودش پیشرفت رو حساب می‌کنه و تو همون گپ جایزه می‌ده.\nسقف جایزه: {TASK_MAX_REWARD[period]:,} تومن",
            reply_markup=InlineKeyboardMarkup(_rows))
    elif act == "tm":   # انتخاب معیار تسک
        st = context.user_data.get("adm") or {}
        if st.get("act") != "task_add" or st.get("step") != "metric":
            await q.edit_message_text("این مرحله منقضی شده؛ دوباره از پنل شروع کن.", reply_markup=admin_menu_kb(q.from_user.id)); return
        if not has_perm(q.from_user.id, "task_daily" if st.get("period") == "daily" else "task_weekly"):
            await q.edit_message_text("این دسترسی برات فعال نیست.", reply_markup=admin_menu_kb(q.from_user.id)); return
        metric = parts[2] if len(parts) > 2 else ""
        if metric == "_text":
            st["step"] = "title"
            await q.edit_message_text("متن تسک را بفرست تا برای همه کاربران اضافه شود (بدون جایزه؛ کاربر خودش تیک می‌زنه).\nبرای لغو بنویس «لغو».", reply_markup=CANCEL_KB); return
        if metric not in TASK_METRICS:
            return
        st.update(step="target", metric=metric)
        _label, _kind = TASK_METRICS[metric][0], TASK_METRICS[metric][1]
        _ask = "مبلغ هدف به تومن (مثلا 5m یا 10 میلیون)" if _kind == "money" else "تعداد هدف (مثلا 10)"
        await q.edit_message_text(f"معیار: {_label}\n\n{_ask} رو بفرست.\nبرای لغو بنویس «لغو».", reply_markup=CANCEL_KB)
    elif act == "toggle":
        BOT_ENABLED = not BOT_ENABLED
        await pool.execute(
            "INSERT INTO bot_settings (key, value) VALUES ('bot_enabled', $1) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            "1" if BOT_ENABLED else "0",
        )
        await q.edit_message_text(admin_menu_text(), reply_markup=admin_menu_kb(q.from_user.id))
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
            await q.edit_message_text("چیزی برای ارسال نیست.", reply_markup=admin_menu_kb(q.from_user.id))
            return
        context.user_data.pop("adm", None)
        await q.edit_message_text(f"{HG_CE} ارسال شروع شد؛ وقتی تموم شد خبرت می‌کنم.", parse_mode="HTML")
        task = asyncio.create_task(do_broadcast(context.bot, q.message.chat_id, st))
        context.application.bot_data.setdefault("tasks", set()).add(task)
        task.add_done_callback(lambda t: context.application.bot_data["tasks"].discard(t))


async def do_broadcast(bot, admin_chat: int, st: dict, job: dict | None = None):
    targets = []
    if st["target"] in ("u", "a"):
        targets += [r["user_id"] for r in await pool.fetch("SELECT user_id FROM users WHERE started")]
    if st["target"] in ("g", "a"):
        targets += [r["chat_id"] for r in await pool.fetch("SELECT chat_id FROM chats")]
    uniq = list(dict.fromkeys(targets))
    if job is not None:
        job["total"] = len(uniq)
    ok = fail = 0
    try:
        for cid in uniq:
            for attempt in range(2):
                try:
                    if st.get("text") is not None:
                        await bot.send_message(chat_id=cid, text=st["text"], parse_mode=st.get("parse"))
                    else:
                        await bot.copy_message(chat_id=cid, from_chat_id=st["src_chat"], message_id=st["src_msg"])
                    ok += 1
                    break
                except RetryAfter as e:
                    await asyncio.sleep(e.retry_after + 1)
                except (Forbidden, BadRequest) as e:
                    fail += 1
                    if cid < 0 and (isinstance(e, Forbidden) or "chat not found" in str(e).lower()):
                        await pool.execute("DELETE FROM chats WHERE chat_id = $1", cid)
                        KNOWN_CHATS.discard(cid)
                    break
                except Exception:
                    fail += 1
                    break
            else:
                fail += 1
            if job is not None:
                job["ok"], job["fail"] = ok, fail
            await asyncio.sleep(0.06)
    finally:
        if job is not None:
            job["running"] = False
    try:
        await bot.send_message(admin_chat, f"✅ پیام همگانی تموم شد.\nموفق: {ok:,}\nناموفق: {fail:,}")
    except Exception:
        log.exception("broadcast report failed")


async def admin_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """پیام‌های پشتیبان وقتی تو مرحله‌ی پنل هست. True یعنی مصرف شد."""
    msg, user = update.effective_message, update.effective_user
    st = context.user_data.get("adm")
    if not st or not msg or not has_perm(user.id, "panel"):
        return False
    if msg.text and msg.text.strip() == "لغو":
        context.user_data.pop("adm", None)
        await msg.reply_text("لغو شد.", reply_markup=admin_menu_kb(user.id))
        return True
    _need_perm = _adm_state_perm(st)
    if _need_perm and not has_perm(user.id, _need_perm):
        context.user_data.pop("adm", None)
        await msg.reply_text("این دسترسی برات فعال نیست.", reply_markup=admin_menu_kb(user.id))
        return True
    act = st["act"]
    if act == "gw":
        return await gw_admin_input(update, context, st)
    if act == "gmu":
        uid = None
        fo = getattr(msg, "forward_origin", None)
        if fo is not None and getattr(fo, "sender_user", None):
            uid = fo.sender_user.id
        elif msg.text and msg.text.strip().lstrip("-").isdigit():
            uid = int(msg.text.strip())
        elif msg.text and re.fullmatch(r"@?[A-Za-z0-9_]{3,32}", msg.text.strip()):
            uid = await pool.fetchval("SELECT user_id FROM users WHERE username = $1", msg.text.strip().lstrip("@").lower())
        if uid is None:
            await msg.reply_text("کاربر پیدا نشد. آیدی عددی یا @یوزرنیم بفرست، یا پیامش رو فوروارد کن.", reply_markup=CANCEL_KB)
            return True
        cid = st["cid"]
        rows = await pool.fetch(
            "SELECT msg_id, user_id, name, username, text, ts FROM group_msgs WHERE chat_id = $1 AND user_id = $2 ORDER BY ts DESC LIMIT 40",
            cid, uid)
        context.user_data.pop("adm", None)
        await msg.reply_text(
            f"👁 پیام‌های کاربر <code>{uid}</code>\n\n{_gm_fmt(rows, cid)}", parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[btn("🔙 گروه", callback_data=f"ad:gl:{cid}")]]))
        return True
    if act in ("ban", "unban"):
        if st["step"] == "target":
            uid = None
            fo = getattr(msg, "forward_origin", None)
            if fo is not None and getattr(fo, "sender_user", None):
                uid = fo.sender_user.id
            elif msg.text and msg.text.strip().lstrip("-").isdigit():
                uid = int(msg.text.strip())
            elif msg.text and re.fullmatch(r"@?[A-Za-z0-9_]{3,32}", msg.text.strip()):
                uid = await pool.fetchval("SELECT user_id FROM users WHERE username = $1", msg.text.strip().lstrip("@").lower())
            if uid is None:
                await msg.reply_text("کاربر پیدا نشد. آیدی عددی یا @یوزرنیم (کسی که بعد از آپدیت با ربات کار کرده) بفرست، یا پیامش رو فوروارد کن.", reply_markup=CANCEL_KB)
                return True
            row = await get_user(uid)
            if not row:
                await msg.reply_text("این کاربر تو دیتابیس نیست (باید قبلاً با ربات کار کرده باشه).", reply_markup=CANCEL_KB)
                return True
            if uid in ADMIN_IDS:
                await msg.reply_text("نمی‌تونی یه پشتیبان رو محروم کنی.", reply_markup=CANCEL_KB)
                return True
            who = f"{row['name'] or uid} ({uid})"
            if act == "unban":
                await pool.execute("UPDATE users SET banned_until = 0 WHERE user_id = $1", uid)
                BANNED.pop(uid, None)
                context.user_data.pop("adm", None)
                await msg.reply_text(f"✅ محرومیت {who} برداشته شد.", reply_markup=admin_menu_kb(user.id))
                return True
            st.update(step="duration", uid=uid, who=who)
            await msg.reply_text(
                f"👤 {who}\n\nمدت محرومیت رو بفرست. مثال:\n30 دقیقه\n2 ساعت\n3 روز\n1 هفته\n1 ماه\nدائمی",
                reply_markup=CANCEL_KB,
            )
            return True
        if st["step"] == "duration":
            dur = parse_duration(msg.text or "")
            if dur is None:
                await msg.reply_text("مدت رو نفهمیدم. مثلا: 30 دقیقه، 2 ساعت، 3 روز، دائمی", reply_markup=CANCEL_KB)
                return True
            until = BAN_PERM if dur == -1 else time.time() + dur
            await pool.execute("UPDATE users SET banned_until = $1::double precision WHERE user_id = $2::bigint", until, st["uid"])
            BANNED[st["uid"]] = until
            context.user_data.pop("adm", None)
            await msg.reply_text(f"🚫 {st['who']} {_ban_until_txt(until)} محروم شد.", reply_markup=admin_menu_kb(user.id))
            return True
    if act in ("money", "jagh", "money_add", "jagh_add"):
        if st["step"] == "target":
            uid = None
            fo = getattr(msg, "forward_origin", None)
            if fo is not None and getattr(fo, "sender_user", None):
                uid = fo.sender_user.id
            elif msg.text and msg.text.strip().lstrip("-").isdigit():
                uid = int(msg.text.strip())
            elif msg.text and re.fullmatch(r"@?[A-Za-z0-9_]{3,32}", msg.text.strip()):
                uid = await pool.fetchval("SELECT user_id FROM users WHERE username = $1", msg.text.strip().lstrip("@").lower())
                if uid is None:
                    await msg.reply_text("این یوزرنیم تو دیتابیس نیست (کاربر باید بعد از آپدیت یه بار با ربات کار کرده باشه). آیدی عددی بفرست.", reply_markup=CANCEL_KB)
                    return True
            if uid is None:
                await msg.reply_text("آیدی عددی یا @یوزرنیم بفرست، یا یه پیام از کاربر فوروارد کن (اگه فوروارد بسته‌ست آیدی عددی لازمه).", reply_markup=CANCEL_KB)
                return True
            row = await get_user(uid)
            if not row:
                await msg.reply_text("این کاربر تو دیتابیس نیست (باید قبلاً با ربات کار کرده باشه).", reply_markup=CANCEL_KB)
                return True
            st.update(step="amount", uid=uid)
            what = "پول (مثلا 50000 یا 100 هزار)" if act.startswith("money") else "جق (مثلا 20)"
            await msg.reply_text(
                f"👤 {row['name'] or uid}\nدارایی: {row['balance']:,} تومن | جق‌ها: {row['jaghs']:,}\n\nچقدر {what} {'اضافه' if act.endswith('_add') else 'کم'} بشه؟",
                reply_markup=CANCEL_KB,
            )
            return True
        if st["step"] == "amount":
            amt = parse_amount(msg.text or "")
            if amt <= 0:
                await msg.reply_text("عدد معتبر بفرست.", reply_markup=CANCEL_KB)
                return True
            col = "balance" if act.startswith("money") else "jaghs"
            if act.endswith("_add"):
                new = await pool.fetchval(f"UPDATE users SET {col} = {col} + $1::bigint WHERE user_id=$2 RETURNING {col}", amt, st["uid"])
                verb = "اضافه شد"
            else:
                new = await pool.fetchval(f"UPDATE users SET {col} = GREATEST({col} - $1::bigint, 0) WHERE user_id = $2::bigint RETURNING {col}", amt, st["uid"])
                verb = "کم شد"
            context.user_data.pop("adm", None)
            if act.startswith("money"):
                await notify_primary_support(context, user.id, st["uid"], amt if act.endswith("_add") else -amt, "تغییر موجودی از پنل پشتیبانی")
            unit = "تومن" if act.startswith("money") else "جق"
            await msg.reply_text(f"✅ {amt:,} {unit} {verb}. مقدار جدید: {new:,} {unit}", reply_markup=admin_menu_kb(user.id))
            return True
    if act == 'task_add' and st['step'] == 'target':
        n = parse_amount(msg.text or '')
        _kind = TASK_METRICS.get(st.get('metric'), ('', 'count'))[1]
        if n <= 0 or n > (100_000_000_000 if _kind == 'money' else 100_000):
            await msg.reply_text('عدد معتبر بفرست.', reply_markup=CANCEL_KB); return True
        st.update(step='reward', target=n)
        await msg.reply_text(f"🎯 هدف: {n:,}\n\nجایزه چقدر باشه؟ (به تومن؛ مثلا 300 هزار یا 1m)\nسقف: {TASK_MAX_REWARD[st['period']]:,} تومن", reply_markup=CANCEL_KB)
        return True
    if act == 'task_add' and st['step'] == 'reward':
        r = parse_amount(msg.text or '')
        cap = TASK_MAX_REWARD[st['period']]
        if r <= 0 or r > cap:
            await msg.reply_text(f'جایزه باید بین ۱ تا {cap:,} تومن باشه.', reply_markup=CANCEL_KB); return True
        title = task_auto_title(st['metric'], st['target'])
        await pool.execute('INSERT INTO tasks(period,title,metric,target,reward) VALUES($1,$2,$3,$4,$5)', st['period'], title, st['metric'], st['target'], r)
        task_cache_clear()
        context.user_data.pop('adm', None)
        period_label = 'روزانه' if st['period'] == 'daily' else 'هفتگی'
        await msg.reply_text(f'✅ تسک {period_label} اضافه شد:\n«{title}»\n💰 جایزه: {r:,} تومن', reply_markup=admin_menu_kb(user.id))
        return True
    if act == 'task_add' and st['step'] == 'title':
        title = (msg.text or '').strip()[:300]
        if not title:
            await msg.reply_text('متن تسک خالیه؛ دوباره بفرست.', reply_markup=CANCEL_KB); return True
        await pool.execute('INSERT INTO tasks(period,title) VALUES($1,$2)', st['period'], title)
        context.user_data.pop('adm', None)
        period_label = 'روزانه' if st['period'] == 'daily' else 'هفتگی'
        await msg.reply_text(f'✅ تسک {period_label} برای همه کاربران اضافه شد.', reply_markup=admin_menu_kb(user.id))
        return True
    if act == 'bc' and st['step'] == 'msg':
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


# ───────────────────────── انرژی، هدیه، بغل و بوس ─────────────────────────
# انرژی هر کاربر (۰ تا ENERGY_MAX) تو ستون users.energy ذخیره میشه و به‌صورت نوار تو پنل «خودم» و زیر پیام جق نشون داده میشه.
# راه‌های گرفتن انرژی: هدیه گرفتن از بقیه، بغل و بوس (ریپلای روی کاربر).
# ایموجی نوار با /setemoji انرژی ... قابل تغییره (اسلت ۱ = پر، اسلت ۲ = خالی).
ENERGY_MAX = 100.0   # سقف انرژی
ENERGY_CELLS = 10    # تعداد خونه‌های نوار
ENERGY_START = 50.0  # انرژی اولیه‌ی همه
ENERGY_LOW = 30.0    # زیر این عدد «انرژی کم» حساب میشه
JAGH_LOW_MULT = 0.5  # انرژی کم: پول جق نصف
CAR_LOW_MULT = 0.75  # انرژی کم: درآمد ماشین ۲۵٪ کمتر
ROB_LOW_MULT = 0.8   # انرژی کم: شانس موفقیت خلاف ۲۰٪ کمتر (یعنی بیشتر گیر میفته)


def _fmt_en(x) -> str:
    return f"{float(x):.2f}".rstrip("0").rstrip(".")


def energy_bar(energy) -> str:
    e = max(0.0, min(ENERGY_MAX, float(energy or 0)))
    filled = min(ENERGY_CELLS, math.ceil(e / ENERGY_MAX * ENERGY_CELLS - 1e-9)) if e > 0 else 0
    return ce("energy", 1, "⚡") * filled + ce("energy", 2, "▫️") * (ENERGY_CELLS - filled)


def energy_line(energy) -> str:
    e = max(0.0, min(ENERGY_MAX, float(energy or 0)))
    return f"{ce('energy', 1, '⚡')} انرژی\n{energy_bar(e)} {_fmt_en(e / ENERGY_MAX * 100)}٪"


# ───────── قدرت‌ها و دزدی ─────────
POWERS_EID = "5204118276635892650"
POWERS_CE = f'<tg-emoji emoji-id="{POWERS_EID}">💪</tg-emoji>'
THEFT_EID = "5316700038162817675"
THEFT_CE = f'<tg-emoji emoji-id="{THEFT_EID}">🥷</tg-emoji>'
THEFT_COOLDOWN = 3 * 3600
THEFT_MIN = 100_000
THEFT_MAX = 350_000


async def theft_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """ریپلای + «دزدی»: از جیب (نه بانک) طرف ۱۰۰ تا ۳۵۰ هزار تومن؛ هر ۳ ساعت یه بار."""
    if _word_key(text) != "دزدی":
        return False
    msg, user = update.effective_message, update.effective_user
    target = msg.reply_to_message.from_user if msg.reply_to_message else None
    if target is None or target.is_bot or target.id == user.id:
        return False   # بدون ریپلای همون منوی خلاف باز میشه
    if target.id in ADMIN_IDS or target.id in OWNER_IDS or target.id in STAFF:
        await msg.reply_text(B(f"{THEFT_CE} از حساب‌های پشتیبانی نمی‌شه دزدی کرد 🛡"), parse_mode="HTML",
                             reply_parameters=ReplyParameters(message_id=msg.message_id, allow_sending_without_reply=True))
        return True
    await upsert_user(user.id, user.full_name)
    await pool.execute("INSERT INTO users(user_id,name) VALUES($1,$2) ON CONFLICT(user_id) DO NOTHING", target.id, target.full_name)
    rp = ReplyParameters(message_id=msg.message_id, allow_sending_without_reply=True)
    now = time.time()
    outcome, amount, left = "ok", 0, 0.0
    async with pool.acquire() as c:
        async with c.transaction():
            rows = await c.fetch("SELECT user_id, balance, last_steal FROM users WHERE user_id = ANY($1::bigint[]) ORDER BY user_id FOR UPDATE", [user.id, target.id])
            by = {int(r["user_id"]): r for r in rows}
            left = THEFT_COOLDOWN - (now - float(by[user.id]["last_steal"] or 0))
            pocket = int(by[target.id]["balance"] or 0)
            if left > 0:
                outcome = "cool"
            elif pocket < THEFT_MIN:
                outcome = "poor"
            else:
                amount = min(random.randint(THEFT_MIN, THEFT_MAX), pocket)
                await c.execute("UPDATE users SET balance=balance-$1 WHERE user_id=$2", amount, target.id)
                await c.execute("UPDATE users SET balance=balance+$1, last_steal=$2 WHERE user_id=$3", amount, now, user.id)
    if outcome == "cool":
        await msg.reply_text(B(f"{THEFT_CE} هنوز نمی‌تونی دزدی کنی؛ {_duration_fa(int(left) + 1)} دیگه صبر کن."), parse_mode="HTML", reply_parameters=rp)
    elif outcome == "poor":
        await msg.reply_text(B(f"{THEFT_CE} این بدبخت تو خط فقره💔"), parse_mode="HTML", reply_parameters=rp)
    else:
        await msg.reply_text(B(f"{THEFT_CE} {_ulink(user.id, user.full_name)} از جیب {_ulink(target.id, target.full_name)} {amount:,} تومن دزدید!"),
                             parse_mode="HTML", reply_parameters=rp)
    return True


# ───────── دستگاه جق: پول جق، بوس و بغل میره تو دستگاه؛ با نوشتن «دستگاه جق» برداشت میشه ─────────
JM_EID = "5190526908462292507"
JM_CE = f'<tg-emoji emoji-id="{JM_EID}">🎰</tg-emoji>'
JM_CAP = 1_200_000      # ظرفیت دستگاه (تومن)
JM_MIN = 50_000         # حداقل موجودی برای برداشت
JM_WORDS = {"دستگاهجق", "دستگاهجقی"}


def jm_status_line(amount: int) -> str:
    """وضعیت دستگاه برای پنل «خودم»."""
    amount = int(amount or 0)
    if amount >= JM_CAP:
        st = "پره! بنویس «دستگاه جق» ✅"
    elif amount >= JM_MIN:
        st = "آماده‌ی برداشته ✅"
    else:
        st = f"هنوز آماده نیست ❌ (حداقل {JM_MIN:,} تومن)"
    return f"{JM_CE} دستگاه جق: {amount:,} / {JM_CAP:,} تومن — {st}"


async def jm_add(c, uid: int, amount: int):
    """پول رو به دستگاه کاربر اضافه می‌کنه (سقف JM_CAP). برمی‌گردونه: (موجودی جدید، مقدار واقعاً اضافه‌شده)."""
    r = await c.fetchrow(
        "WITH o AS (SELECT jagh_machine AS old_m FROM users WHERE user_id=$2::bigint FOR UPDATE) "
        "UPDATE users SET jagh_machine = LEAST(o.old_m + $1::bigint, $3::bigint) FROM o "
        "WHERE user_id=$2::bigint RETURNING jagh_machine, o.old_m",
        int(amount), uid, JM_CAP)
    if not r:
        return 0, 0
    return int(r["jagh_machine"]), int(r["jagh_machine"]) - int(r["old_m"])


async def jagh_machine_collect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """«دستگاه جق»: اگه حداقل ۵۰ هزار توش باشه، همه‌ش میره تو کیف پول."""
    msg, user = update.effective_message, update.effective_user
    await upsert_user(user.id, user.full_name)
    rp = ReplyParameters(message_id=msg.message_id, allow_sending_without_reply=True)
    async with pool.acquire() as c:
        async with c.transaction():
            cur = int(await c.fetchval("SELECT jagh_machine FROM users WHERE user_id=$1 FOR UPDATE", user.id) or 0)
            if cur >= JM_MIN:
                new_bal = await c.fetchval(
                    "UPDATE users SET balance=balance+$1, jagh_machine=0 WHERE user_id=$2 RETURNING balance", cur, user.id)
    if cur < JM_MIN:
        await msg.reply_text(B(f"{JM_CE} دستگاه جق هنوز آماده نیست!\n"
                               f"موجودی: {cur:,} / {JM_CAP:,} تومن\n"
                               f"حداقل {JM_MIN:,} تومن باید توش جمع بشه تا بتونی برداشت کنی."),
                             parse_mode="HTML", reply_parameters=rp)
        return
    await msg.reply_text(B(f"{JM_CE} برداشت از دستگاه جق انجام شد!\n"
                           f"{MONEY_CE} {cur:,} تومن به کیف پولت اضافه شد.\n"
                           f"👛 موجودی کیف پول: {int(new_bal):,} تومن"),
                         parse_mode="HTML", reply_parameters=rp)


GIFT_MONEY_CE = '<tg-emoji emoji-id="5289850733011693663">💰</tg-emoji>'
AFFAIR_ENERGY_PENALTY = 3.0      # انرژی منفی برای کسی که رو زوجین بوس/بغل میده
HUG_COOLDOWN = 5 * 60            # هر کاربر رو هر ۵ دقیقه یه بار می‌شه بغل/بوس کرد
HUG_MONEY = (30, 50)             # تومن
HUG_ENERGY = (1.0, 2.5)          # شانسی بین این دو عدد

# هر هدیه: اسم، ایموجی پرمیوم (None = ایموجی معمولی)، قیمت (تومن)، انرژی، اسم‌های قابل قبول، جمله
GIFTS = [
    dict(name="شکلات", eid="5298787404608479427", fb="🍫", price=50_000, energy=1, al=("شکلات",),
         line="شیرینی این شکلات تموم میشه، ولی طعم کاری که کردی تا ته دل می‌مونه."),
    dict(name="گل", eid="5875255995227182813", fb="🌹", price=108_000, energy=2.5, al=("گل",),
         line="گل رو که بدی، خارش با خودت؛ ولی این یکی فقط عطر داره."),
    dict(name="قهوه", eid=None, fb="☕️", price=150_000, energy=2.75, al=("قهوه",),
         line="قهوه‌ی تلخ و داغ، برای شبی که قراره تا صبح بیدار بمونی."),
    dict(name="آبمیوه", eid="5201741892705861301", fb="🧃", price=180_000, energy=3, al=("آبمیوه",),
         line="یه لیوان خنک برای دلی که از این گروه داغ کرده."),
    dict(name="بستنی", eid="5201787741481746161", fb="🍦", price=202_000, energy=3.5, al=("بستنی",),
         line="بستنی آب میشه ولی این حس نه؛ تا آخرین قاشق تو دلت می‌مونه."),
    dict(name="ادکلن", eid="5411282253177170935", fb="🧴", price=350_000, energy=4, al=("ادکلن",),
         line="بوی این ادکلن بعد از رفتنت هم تو هوا می‌مونه؛ یه رد خطرناک."),
    dict(name="کیف", eid="5411253296507660747", fb="👜", price=470_000, energy=4.5, al=("کیف",),
         line="یه کیف شیک؛ جا برای همه‌چی داره جز دلتنگی."),
    dict(name="گردنبند", eid="5411590683368632723", fb="📿", price=500_000, energy=5, al=("گردنبند",),
         line="هر بار که این گردنبند برق بزنه، یکی یادت میاد که کی بهت داده."),
    dict(name="انگشتر", eid="5262922516426420894", fb="💍", price=750_000, energy=6, al=("انگشتر",),
         line="این انگشتر قول و قرار نیست، ولی یه ارادت خفنه که از دست درنمیاد."),
    dict(name="پنج آهنگ", eid="5852687200512119580", fb="🎵", price=890_000, energy=7.5, al=("آهنگ",),
         line="پنج تا آهنگ، پنج تا حرف ناگفته؛ پلی کن و بذار دلت جواب بده."),
    dict(name="تاج", eid=None, fb="👑", price=1_000_000, energy=8, al=("تاج",),
         line="تاج رو گذاشتم رو سرت؛ از امروز شاه این گروهی و هیشکی جرات نداره تکون بخوره."),
]
GIFT_LIST_WORDS = {"هدیه", "هدیهها", "هدیه‌ها", "gifts", "gift"}
GIFT_FOOTER = (
    "🎁 برای فرستادن هدیه روی پیام کاربر ریپلای کن و بنویس:\n<code>هدیه گل</code>\n\n"
    "🤗 بغل و بوس رایگانه (برای هر کاربر هر ۵ دقیقه یه بار). ریپلای کن و بنویس:\n"
    "<code>بغل</code> یا <code>بوس</code>\n\n"
    "💔 قهر و آشتی (ریپلای کن و بنویس): <code>قهر</code> یا <code>آشتی</code>"
)
HUG_WORDS = {"بغل": "hug", "بقل": "hug", "بوس": "kiss", "بوش": "kiss"}
# قالب‌ها: {a} = کسی که بغل/بوس کرده، {b} = طرف مقابل
HUG_LINES = {
    "hug": [
        "{a} آروم نزدیک {b} شد و محکم بغلش کرد و کل گپ ساکت شد 🤗",
        "{a} دستاشو باز کرد، {b} رو تو آغوش گرفت و دنیا یه لحظه وایساد 🫂",
        "{a} بی‌صدا رفت سمت {b} و یه بغل گرم بهش داد؛ همه‌ی گروه آه کشیدن 🤗",
        "{a} {b} رو انقدر محکم بغل کرد که گروه قلب قلب شد 💞",
        "{a} از پشت اومد و {b} رو تو بغلش گرفت؛ گپ یهو پر از سکوت شد 🫂",
    ],
    "kiss": [
        "{a} آروم نزدیک {b} شد و یه بوس داد و کل گپ ساکت شد 💋",
        "{a} یه بوس آبدار از لپ {b} کرد و همه‌ی گروه سرخ شدن 😘",
        "{a} {b} رو بوسید؛ یه لحظه حتی پیام‌ها هم وایسادن 💋",
        "{a} یواشکی رفت سمت {b} و یه بوس بلند و صدادار تقدیمش کرد 😚",
        "{a} با یه نگاه شیطون {b} رو بوسید و گپ آتیش گرفت 🔥",
    ],
}
HUG_ENERGY_LINES = [
    "⚡ انرژیش {eb} و انرژی خودت {ea}",
    "⚡ انرژیش {eb}، انرژی خودت {ea}",
]
HUG_MONEY_LINES = [
    "{m} {n} تومن پول، هر دوتون گرفتید! (رفت تو دستگاه جق)",
    "{m} هر کدومتون {n} تومن جایزه گرفتید! (رفت تو دستگاه جق)",
    "{m} {n} تومن به دستگاه جق هر دو نفرتون رفت!",
]


def _gift_glyph(g: dict) -> str:
    return _tge(g["eid"], g["fb"])


def _find_gift(rest: str):
    rest = rest.replace("‌", "")
    for i, g in enumerate(GIFTS):
        if any(a in rest for a in g["al"]):
            return i
    return None


def _mention(uid: int, name: str) -> str:
    return f'<a href="tg://user?id={uid}">{html.escape(name or "کاربر")}</a>'


def _ltr_name(uid: int, name: str) -> str:
    """اسم کاربر داخل ایزوله‌ی راست‌به‌چپ."""
    return f"\u2067{_mention(uid, name)}\u2069"


def _ltr_pair(a_id: int, a_name: str, b_id: int, b_name: str) -> str:
    """«فرستنده ⬅️ گیرنده» از راست به چپ (فرستنده سمت راست، گیرنده سمت چپ)."""
    return f"\u2067{_mention(a_id, a_name)} ⬅️ {_mention(b_id, b_name)}\u2069"


def gifts_rich_html() -> str:
    out = ["<table><tr><th>هدیه</th><th>قیمت (تومن)</th><th>انرژی</th></tr>"]
    for g in GIFTS:
        out.append(f"<tr><td>{_gift_glyph(g)} {html.escape(g['name'])}</td><td>{g['price']:,}</td><td>{_fmt_en(g['energy'])}</td></tr>")
    out.append("</table>")
    out.append("<p><br>" + GIFT_FOOTER.replace("\n", "<br>") + "</p>")  # راهنما تو همون پیام، یکم پایین‌تر از جدول
    return "".join(out)


def gifts_text() -> str:
    rows = [(f"{g['fb']} {g['name']}", f"{g['price']:,}", _fmt_en(g["energy"])) for g in GIFTS]
    table = mono_table(("هدیه", "قیمت", "انرژی"), rows)
    return f"{ce('me', 1, '🎁')} <b>هدیه‌ها</b>\n{table}\n\n{GIFT_FOOTER}"


async def show_gifts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """«هدیه» / «هدیه ها» — جدول هدیه‌ها (مثل جدول شغل‌ها)؛ راهنمای تک‌فاصله با یکم فاصله زیر همون جدول، تو همون پیام."""
    msg = update.effective_message
    rp = ReplyParameters(message_id=msg.message_id, allow_sending_without_reply=True)
    try:
        await context.bot._post("sendRichMessage", {
            "chat_id": msg.chat_id,
            "rich_message": {"html": gifts_rich_html()},
            "reply_parameters": rp.to_dict(),
        })
        return
    except Exception:
        log.warning("gifts sendRichMessage failed, using <pre> table", exc_info=True)
    text = gifts_text()
    try:
        await msg.reply_text(text, parse_mode="HTML", reply_parameters=rp)
    except Exception:
        log.exception("gifts message failed, retrying without premium emojis")
        await msg.reply_text(re.sub(r"<tg-emoji[^>]*>(.*?)</tg-emoji>", r"\1", text), parse_mode="HTML")


async def gift_request(update: Update, context: ContextTypes.DEFAULT_TYPE, rest: str):
    msg, user = update.effective_message, update.effective_user
    target = msg.reply_to_message.from_user if msg.reply_to_message else None
    if target is None:
        await msg.reply_text("<b>برای فرستادن هدیه روی پیام اون کاربر ریپلای کن و بنویس:</b>\n<code>هدیه گل</code>", parse_mode="HTML")
        return
    if target.is_bot or target.id == user.id:
        await msg.reply_text(BP("نمی‌تونی به خودت یا ربات هدیه بدی 😅"), parse_mode="HTML")
        return
    if (rl := await rift_left(user.id, target.id)) > 0:
        await msg.reply_text(rift_block_html(rl), parse_mode="HTML")
        return
    idx = _find_gift(rest)
    if idx is None:
        await msg.reply_text("<b>این هدیه رو نمی‌شناسم. برای دیدن لیست بنویس:</b>\n<code>هدیه</code>", parse_mode="HTML")
        return
    g = GIFTS[idx]
    await upsert_user(user.id, user.full_name)
    row = await get_user(user.id)
    if int(row["balance"] or 0) < g["price"]:
        await msg.reply_text(B(f"{ce('wallet', 1, '💰')} دارایی‌ت کافی نیست! موجودی: {int(row['balance'] or 0):,} تومن"), parse_mode="HTML")
        return
    kb = InlineKeyboardMarkup([[
        btn("✅ تایید", "success", callback_data=f"gf:ok:{user.id}:{target.id}:{idx}"),
        btn("❌ لغو", "danger", callback_data=f"gf:no:{user.id}:{target.id}:{idx}"),
    ]])
    context.application.bot_data.setdefault("gf_names", {})[(msg.chat_id, target.id)] = target.full_name
    await msg.reply_text(
        f"<b>{_ltr_pair(user.id, user.full_name, target.id, target.full_name)}\n\n"
        f"مطمئنی می‌خوای {_gift_glyph(g)} {html.escape(g['name'])} (<u>{g['price']:,} تومن</u>) بفرستی؟\n\n"
        f"{ce('energy', 1, '⚡')} {_fmt_en(g['energy'])} انرژی برای گیرنده</b>",
        parse_mode="HTML", reply_markup=kb,
        reply_parameters=ReplyParameters(message_id=msg.message_id, allow_sending_without_reply=True),
    )


async def gift_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, act, frm, to, idx = q.data.split(":")
    frm, to, idx = int(frm), int(to), int(idx)
    if q.from_user.id != frm:
        await q.answer("این تایید مال تو نیست 😅", show_alert=True)
        return
    if not 0 <= idx < len(GIFTS):
        await q.answer("هدیه نامعتبره.", show_alert=True)
        return
    key = (q.message.chat_id, q.message.message_id)
    done = context.bot_data.setdefault("gf_done", set())
    if key in done:
        await q.answer("قبلاً انجام شده.", show_alert=True)
        return
    done.add(key)
    if act == "no":
        await q.answer("لغو شد")
        await q.edit_message_text("❌ هدیه لغو شد.")
        return
    try:
        age = time.time() - q.message.date.timestamp()
    except Exception:
        age = 0
    if age > 300:
        await q.answer("زمان تایید تموم شده، دوباره درخواست بده.", show_alert=True)
        await q.edit_message_text("⌛ زمان تایید تموم شد.")
        return
    g = GIFTS[idx]
    if await rift_left(frm, to) > 0:
        await q.answer("با هم قهرید!", show_alert=True)
        await q.edit_message_text("💔 باهم قهرید؛ هدیه فرستاده نشد.")
        return
    try:
        async with pool.acquire() as c:
            async with c.transaction():
                ok = await c.fetchval(
                    "UPDATE users SET balance = balance - $1::bigint WHERE user_id = $2::bigint AND balance >= $1::bigint RETURNING 1",
                    g["price"], frm,
                )
                if not ok:
                    raise ValueError("insufficient")
                await c.execute("INSERT INTO users (user_id, name) VALUES ($1, '') ON CONFLICT (user_id) DO NOTHING", to)
                new_en = await c.fetchval(
                    "UPDATE users SET energy = LEAST(energy + $1::double precision, $3::double precision) WHERE user_id = $2::bigint RETURNING energy",
                    float(g["energy"]), to, ENERGY_MAX,
                )
    except ValueError:
        await q.answer("دارایی‌ت کافی نیست!", show_alert=True)
        await q.edit_message_text("❌ دارایی کافی نبود؛ هدیه فرستاده نشد.")
        return
    except Exception:
        done.discard(key)
        log.exception("gift failed")
        await q.answer("خطا، دوباره امتحان کن.", show_alert=True)
        return
    await q.answer("هدیه فرستاده شد 🎁")
    tname = (context.application.bot_data.get("gf_names", {}).get((q.message.chat_id, to))
             or await pool.fetchval("SELECT name FROM users WHERE user_id = $1", to) or "کاربر")
    await q.edit_message_text(
        f"<b>{_ltr_pair(frm, q.from_user.full_name, to, tname)}\n\n"
        f"{_gift_glyph(g)} {html.escape(g['name'])} فرستاد!\n\n"
        f"«{html.escape(g['line'])}»\n\n"
        f"{ce('energy', 1, '⚡')} +{_fmt_en(g['energy'])} انرژی برای گیرنده\n"
        f"{energy_bar(new_en)} {_fmt_en(float(new_en) / ENERGY_MAX * 100)}٪</b>",
        parse_mode="HTML",
    )
    await task_event(context.bot, q.message.chat_id, frm, "gift", name=q.from_user.full_name)


async def marriage_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    key=_word_key(text); msg=update.effective_message; user=update.effective_user
    if key in ("همسرم","خانوادهزنم","خانوادههمسرم","خانواده"):
        row=await get_user(user.id)
        if not row or not row["spouse_id"]:
            await msg.reply_text("هنوز ازدواج نکردی."); return True
        spouse=await get_user(int(row["spouse_id"]))
        if not spouse:
            await msg.reply_text("اطلاعات همسرت پیدا نشد."); return True
        rel, _ = await couple_info(user.id, int(spouse['user_id']))
        kb = None
        if update.effective_chat.type != "private":
            kb = InlineKeyboardMarkup([[btn("سکس", "danger", icon=SEX_EIDS[0], callback_data=f"sx:ask:{user.id}")],
                                       [btn("فروشگاه", "primary", icon=SHOP_EID, callback_data=f"shp:no:{user.id}:condom")]])
        await send_html(context.bot, msg.chat_id, B(f"💍 <b>خانواده‌ی تو</b>\nهمسر: {_ulink(spouse['user_id'],spouse['name'] or 'همسر')}\n{energy_line(spouse['energy'])}\n💞 میزان رابطه: <b>{rel}</b>"),
                        reply_markup=kb, reply_parameters=ReplyParameters(message_id=msg.message_id, allow_sending_without_reply=True)); return True
    if key == "سکس":
        await upsert_user(user.id, user.full_name)
        await sex_request(context.bot, context, msg.chat_id, user.id, reply_to=msg.message_id); return True
    if key == "فروشگاه":
        await show_shop(update, context); return True
    if key not in ("ازدواج","خواستگاری"):
        return False
    target=msg.reply_to_message.from_user if msg.reply_to_message else None
    if not target or target.is_bot or target.id==user.id:
        await msg.reply_text("برای خواستگاری، روی پیام فرد موردنظرت ریپلای کن و «ازدواج» بنویس."); return True
    if user.id in ADMIN_IDS or target.id in ADMIN_IDS:
        await msg.reply_text("🛡 حساب‌های پشتیبانی امکان ازدواج ندارن."); return True
    await upsert_user(user.id,user.full_name)
    await pool.execute("INSERT INTO users(user_id,name) VALUES($1,$2) ON CONFLICT(user_id) DO NOTHING",target.id,target.full_name)
    a,b=await pool.fetch("SELECT user_id,gender,spouse_id FROM users WHERE user_id=ANY($1::bigint[])",[user.id,target.id])
    by={int(x['user_id']):x for x in (a,b)}; me=by[user.id]; other=by[target.id]
    if not me['gender'] or not other['gender']:
        await msg.reply_text("هر دو نفر باید اول جنسیتشون رو از تنظیمات انتخاب کنن."); return True
    if me['gender'] != 'm' or other['gender'] != 'f':
        await msg.reply_text("💍 خواستگاری فقط از طرف مرد به زن امکان‌پذیره؛ مرد باید روی پیام زن ریپلای کنه و «ازدواج» بنویسه."); return True
    if me['spouse_id'] or other['spouse_id']:
        await msg.reply_text("یکی از شما در حال حاضر متأهله."); return True
    bride=target
    groom=user
    await pool.execute("INSERT INTO marriage_proposals(bride_id,groom_id,chat_id,created_at) VALUES($1,$2,$3,$4) ON CONFLICT(bride_id) DO UPDATE SET groom_id=EXCLUDED.groom_id,chat_id=EXCLUDED.chat_id,created_at=EXCLUDED.created_at",bride.id,groom.id,msg.chat_id,time.time())
    kb=InlineKeyboardMarkup([[btn("💍 قبول می‌کنم", "success", callback_data=f"mar:yes:{bride.id}:{groom.id}"),btn("رد می‌کنم", "danger", callback_data=f"mar:no:{bride.id}:{groom.id}")]])
    await msg.reply_text(f"💍 {html.escape(bride.full_name)}، {html.escape(groom.full_name)} ازت خواستگاری کرده! عروس باید جواب بده.",reply_markup=kb); return True

async def marriage_cb(update,context):
    q=update.callback_query; _,act,bride_s,groom_s=q.data.split(":"); bride=int(bride_s); groom=int(groom_s)
    if q.from_user.id!=bride: await q.answer("فقط عروس می‌تونه جواب بده.",show_alert=True); return
    prop=await pool.fetchrow("SELECT groom_id,chat_id FROM marriage_proposals WHERE bride_id=$1",bride)
    if not prop or int(prop['groom_id'])!=groom: await q.answer("درخواست منقضی شده.",show_alert=True); return
    if bride in ADMIN_IDS or groom in ADMIN_IDS:
        await pool.execute("DELETE FROM marriage_proposals WHERE bride_id=$1",bride)
        await q.answer("حساب‌های پشتیبانی امکان ازدواج ندارن.",show_alert=True); return
    if act=="no":
        await pool.execute("DELETE FROM marriage_proposals WHERE bride_id=$1",bride)
        await q.answer("درخواست رد شد."); await q.edit_message_text("💔 درخواست ازدواج رد شد."); return
    async with pool.acquire() as c:
        async with c.transaction():
            rows=await c.fetch("SELECT user_id,gender,spouse_id FROM users WHERE user_id=ANY($1::bigint[]) FOR UPDATE",[bride,groom])
            by={int(r['user_id']):r for r in rows}
            if len(rows)!=2 or by[bride]['gender']!='f' or by[groom]['gender']!='m':
                await q.answer("ازدواج فقط بین مردِ خواستگار و زنِ پاسخ‌دهنده ممکنه.",show_alert=True); return
            if any(r['spouse_id'] for r in rows):
                await q.answer("یکی از شما قبلاً ازدواج کرده.",show_alert=True); return
            await c.execute("DELETE FROM marriage_proposals WHERE bride_id=$1",bride)
            await c.execute("UPDATE users SET spouse_id=CASE WHEN user_id=$1 THEN $2 ELSE $1 END WHERE user_id IN ($1,$2)",bride,groom)
    await q.answer("مبارکه! 💍"); await q.edit_message_text("💍 ازدواجتون ثبت شد! از این به بعد با نوشتن «همسرم» می‌تونید خانواده و انرژی همسرتون رو ببینید.")

async def affair_cb(update,context):
    q=update.callback_query; _,act,cheater_s,spouse_s,chat_s=q.data.split(":"); cheater=int(cheater_s); spouse=int(spouse_s)
    if q.from_user.id!=spouse: await q.answer("این تصمیم فقط برای همسرته.",show_alert=True); return
    if act=="forgive": await q.answer("بخشیدی ❤️"); await q.edit_message_text("❤️ خیانت رو بخشیدی. زندگی مشترک ادامه داره."); return
    async with pool.acquire() as c:
        async with c.transaction():
            row=await c.fetchrow("SELECT balance,bank_balance FROM users WHERE user_id=$1 FOR UPDATE",cheater)
            if not row: await q.answer("کاربر پیدا نشد.",show_alert=True); return
            amount=int((int(row['balance'])+int(row['bank_balance']))*0.10)
            from_bank=min(int(row['bank_balance']),amount); from_pocket=amount-from_bank
            await c.execute("UPDATE users SET bank_balance=bank_balance-$1,balance=balance-$2,spouse_id=NULL WHERE user_id=$3",from_bank,from_pocket,cheater)
            await c.execute("UPDATE users SET spouse_id=NULL,balance=balance+$1 WHERE user_id=$2",amount,spouse)
            await c.execute("DELETE FROM couple_relation WHERE pair_key=$1",_pair_key(cheater,spouse))
    await q.answer("طلاق ثبت شد."); await q.edit_message_text(f"💔 طلاق ثبت شد. {amount:,} تومان (۱۰٪ دارایی همسر سابقت) به حسابت منتقل شد.")

async def hug_kiss(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str) -> bool:
    msg,user=update.effective_message,update.effective_user
    target=msg.reply_to_message.from_user if msg.reply_to_message else None
    if target is None or target.is_bot or target.id==user.id: return False
    if (rl:=await rift_left(user.id,target.id))>0:
        await msg.reply_text(rift_block_html(rl),parse_mode="HTML"); return True
    now=time.time(); await upsert_user(user.id,user.full_name)
    await pool.execute("INSERT INTO users(user_id,name) VALUES($1,$2) ON CONFLICT(user_id) DO NOTHING",target.id,target.full_name)
    rows=await pool.fetch("SELECT user_id,spouse_id,last_affection,energy FROM users WHERE user_id=ANY($1::bigint[])",[user.id,target.id]); by={int(r['user_id']):r for r in rows}; me=by[user.id]; them=by[target.id]
    last_pair=await pool.fetchval("SELECT last_ts FROM affection_cooldowns WHERE actor_id=$1 AND target_id=$2",user.id,target.id) or 0
    left=HUG_COOLDOWN-(now-float(last_pair))
    if left>0:
        await msg.reply_text(B(f"{HG_CE} باید {_duration_fa(int(left)+1)} صبر کنی تا دوباره بوس یا بغل کنی."),parse_mode="HTML"); return True
    married=bool(me['spouse_id']) or bool(them['spouse_id'])
    affair=False; cheater_id=None; spouse_id=None
    if me['spouse_id'] and int(me['spouse_id'])!=target.id:
        affair=True; cheater_id=user.id; spouse_id=int(me['spouse_id'])
    poach=bool(them['spouse_id']) and int(them['spouse_id'])!=user.id   # طرف مقابل همسر دارد (و همسرش خود بوس‌کننده نیست)
    energy_gain=2.0 if married else round(random.uniform(*HUG_ENERGY),1)
    money=random.randint(*HUG_MONEY)
    async with pool.acquire() as c:
        async with c.transaction():
            await c.execute("INSERT INTO affection_cooldowns(actor_id,target_id,last_ts) VALUES($1,$2,$3) ON CONFLICT(actor_id,target_id) DO UPDATE SET last_ts=EXCLUDED.last_ts",user.id,target.id,now)
            s_en=await c.fetchval("UPDATE users SET energy=LEAST(energy+$1,$2) WHERE user_id=$3 RETURNING energy",energy_gain,ENERGY_MAX,user.id)
            t_en=await c.fetchval("UPDATE users SET energy=LEAST(energy+$1,$2) WHERE user_id=$3 RETURNING energy",energy_gain,ENERGY_MAX,target.id)
            s_jm,_s_add=await jm_add(c,user.id,money)   # پول بوس/بغل میره تو دستگاه جق
            t_jm,_t_add=await jm_add(c,target.id,money)
    rel_note=""
    if kind=="kiss" and me['spouse_id'] and int(me['spouse_id'])==target.id:
        _rel=await add_relation(pool,user.id,target.id,KISS_RELATION)
        rel_note=f"\n\n💞 میزان رابطه: {_rel} (+{KISS_RELATION})"
    a=_ltr_name(user.id,user.full_name); b=_ltr_name(target.id,target.full_name)
    pct=lambda e: f"{_fmt_en(float(e)/ENERGY_MAX*100)}٪"
    RLM="\u200f"
    line=RLM+random.choice(HUG_LINES[kind]).format(a=a,b=b)
    en=RLM+random.choice(HUG_ENERGY_LINES).format(ea=pct(s_en),eb=pct(t_en))
    mn=RLM+random.choice(HUG_MONEY_LINES).format(m=f"{GIFT_MONEY_CE}{GIFT_MONEY_CE}",n=f"{money:,}")
    text=f"<b>{line}\n\n{en}\n\n{mn}{rel_note}</b>"
    await msg.reply_text(text,parse_mode="HTML")
    await task_event(context.bot, msg.chat_id, user.id, "hug", name=user.full_name)
    if poach:
        await pool.execute("UPDATE users SET energy=GREATEST(energy-$1::double precision,0) WHERE user_id=$2",AFFAIR_ENERGY_PENALTY,user.id)
        await msg.reply_text(B(f"{html.escape(user.full_name)} تو حواست هست؟ {html.escape(target.full_name)} همسر داره؛ {_fmt_en(AFFAIR_ENERGY_PENALTY)} انرژی منفی می‌گیری تا دیگه رو زوجین لاو ندی 😤"),parse_mode="HTML")
    if affair:
        cheater_name=user.full_name
        affair_target=target.full_name
        await msg.reply_text(B(f"⚠️ {html.escape(cheater_name)} داری به همسرت خیانت می‌کنی!"),parse_mode="HTML")
        kb=InlineKeyboardMarkup([[btn("طلاق", "danger", callback_data=f"af:divorce:{cheater_id}:{spouse_id}:{msg.chat_id}"),btn("می‌بخشمش", "success", callback_data=f"af:forgive:{cheater_id}:{spouse_id}:{msg.chat_id}")]])
        notice=B(f"💔 {html.escape(cheater_name)} به {html.escape(affair_target)} در گروه {html.escape(msg.chat.title or 'گروه')} نزدیک شد و داره بهت خیانت می‌کنه!\nمی‌تونی طلاق بگیری و ۱۰٪ دارایی‌ش رو به غنیمت ببری یا ببخشیش.")
        try:
            await context.bot.send_message(spouse_id,notice,parse_mode="HTML",reply_markup=kb)
        except Exception:
            await msg.reply_text(f"پیام به همسر ارسال نشد؛ { _ulink(spouse_id, 'همسر') }، {cheater_name} به {affair_target} نزدیک شد.",parse_mode="HTML",reply_markup=kb)
    return True


# ───────────────────────── فروشگاه، سکس و میزان رابطه‌ی زوجین ─────────────────────────
SHOP_EID = "5294385600000956892"
CONDOM_EID = "5211074985489092120"
PILL_EID = "5372902640168482479"
SEX_EIDS = ("5927155469498978151", "5927257496447093992", "5834455158829879029")
SHOP_CE = f'<tg-emoji emoji-id="{SHOP_EID}">🛍</tg-emoji>'
CONDOM_CE = f'<tg-emoji emoji-id="{CONDOM_EID}">🛡</tg-emoji>'
PILL_CE = f'<tg-emoji emoji-id="{PILL_EID}">💊</tg-emoji>'
SEX_CE = "".join(f'<tg-emoji emoji-id="{e}">💞</tg-emoji>' for e in SEX_EIDS)
CONDOM_PRICE = 15_000
PILL_PRICE = 15_000
SEX_COOLDOWN = 120 * 60       # هر زوج هر ۱۲۰ دقیقه یه بار
SEX_ENERGY = 15.0             # انرژی هر دو نفر
SEX_RELATION = 5              # میزان رابطه‌ی هر سکس
KISS_RELATION = 2             # میزان رابطه‌ی هر بوس بین زوجین
SEX_REQ_TTL = 300
SHOP_QTYS = (1, 5, 10)
# (کلید، ستون موجودی، اسم، قیمت، ایموجی پرمیوم، جنسیت خریدار)
SHOP_ITEMS = {
    "condom": ("condoms", "کاندوم", CONDOM_PRICE, CONDOM_EID, "m"),
    "pill": ("pills", "قرص ضدبارداری", PILL_PRICE, PILL_EID, "f"),
}


def _pair_key(a: int, b: int) -> str:
    return f"{min(a, b)}:{max(a, b)}"


async def couple_info(a: int, b: int):
    row = await pool.fetchrow("SELECT relation, last_sex FROM couple_relation WHERE pair_key=$1", _pair_key(a, b))
    return (int(row["relation"]), float(row["last_sex"])) if row else (0, 0.0)


async def add_relation(conn_or_pool, a: int, b: int, amount: int) -> int:
    return await conn_or_pool.fetchval(
        "INSERT INTO couple_relation(pair_key, relation) VALUES($1,$2) ON CONFLICT(pair_key) DO UPDATE SET relation=couple_relation.relation+EXCLUDED.relation RETURNING relation",
        _pair_key(a, b), amount)


# ───── فروشگاه ─────
async def _shop_panel(uid: int):
    row = await get_user(uid)
    g = row["gender"] if row else None
    if g not in ("m", "f"):
        return B("اول باید جنسیتت رو از تنظیمات انتخاب کنی تا فروشگاه برات باز بشه."), None
    key = "condom" if g == "m" else "pill"
    col, name, price, eid, _ = SHOP_ITEMS[key]
    ce_ = CONDOM_CE if key == "condom" else PILL_CE
    text = B(f"{SHOP_CE} <b>فروشگاه</b>\n\n{ce_} {name} — {price:,} تومان (هر عدد)\n"
             f"برای هر سکس یک عدد از این وسیله مصرف می‌شه؛ اگه درخواست سکس از طرف تو باشه، مال تو مصرف می‌شه.\n\n"
             f"داشته‌ی تو: <b>{int(row[col] or 0)}</b> عدد\nموجودی جیبت: <b>{int(row['balance']):,}</b>")
    kb = InlineKeyboardMarkup([[btn(f"خرید {name}", "primary", icon=eid, callback_data=f"shp:ask:{uid}:{key}:1")]])
    return text, kb


async def show_shop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await upsert_user(user.id, user.full_name)
    text, kb = await _shop_panel(user.id)
    await send_html(context.bot, update.effective_message.chat_id, text, reply_markup=kb,
                    reply_parameters=ReplyParameters(message_id=update.effective_message.message_id, allow_sending_without_reply=True))


async def shop_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    try:
        act, uid, key = parts[1], int(parts[2]), parts[3]
        qty = int(parts[4]) if len(parts) > 4 else 1
    except (IndexError, ValueError):
        await q.answer("درخواست نامعتبره.", show_alert=True); return
    if q.from_user.id != uid:
        await q.answer("این فروشگاه برای تو نیست؛ خودت بنویس «فروشگاه».", show_alert=True); return
    if act == "no":
        await q.answer()
        text, kb = await _shop_panel(uid)
        await edit_html(q, text, kb); return
    item = SHOP_ITEMS.get(key)
    if not item or qty not in SHOP_QTYS:
        await q.answer("درخواست نامعتبره.", show_alert=True); return
    col, name, price, eid, need_gender = item
    row = await get_user(uid)
    if not row or row["gender"] != need_gender:
        await q.answer(f"{name} فقط برای {'آقایون' if need_gender == 'm' else 'خانم‌ها'}ست.", show_alert=True); return
    if act == "ask":
        await q.answer()
        rows = [[btn(f"✅ تایید خرید {n} عدد | {price * n:,}", "success", callback_data=f"shp:ok:{uid}:{key}:{n}")] for n in SHOP_QTYS]
        rows.append([btn("❌ انصراف", "danger", callback_data=f"shp:no:{uid}:{key}")])
        await edit_html(q, B(f"{SHOP_CE} <b>تاییدیه خرید</b>\n\n{name} — هر عدد {price:,} تومان\nتعداد رو انتخاب و خرید رو تایید کن 👇\nموجودی جیبت: {int(row['balance']):,}"), InlineKeyboardMarkup(rows)); return
    if act == "ok":
        total = price * qty
        async with pool.acquire() as c:
            async with c.transaction():
                bal = await c.fetchval(f"UPDATE users SET balance=balance-$1::bigint, {col}={col}+$3 WHERE user_id=$2::bigint AND balance>=$1::bigint RETURNING balance", total, uid, qty)
        if bal is None:
            await q.answer(f"برای خرید {qty} عدد {name} به {total:,} تومان نیاز داری.", show_alert=True); return
        await q.answer(f"{qty} عدد {name} خریداری شد ✅")
        text, kb = await _shop_panel(uid)
        await edit_html(q, text.replace("<b>فروشگاه</b>", "<b>فروشگاه</b>\n✅ خرید انجام شد!", 1), kb); return
    await q.answer("درخواست نامعتبره.", show_alert=True)


# ───── سکس ─────
async def _sex_check(uid: int):
    """(خطا یا None، ردیف خودم، ردیف همسر)."""
    me = await get_user(uid)
    if not me or not me["spouse_id"]:
        return "برای این کار باید ازدواج کرده باشی.", None, None
    if me["gender"] not in ("m", "f"):
        return "اول جنسیتت رو از تنظیمات انتخاب کن.", None, None
    sp = await get_user(int(me["spouse_id"]))
    if not sp or not sp["spouse_id"] or int(sp["spouse_id"]) != uid:
        return "اطلاعات همسرت پیدا نشد.", None, None
    _, last = await couple_info(uid, int(sp["user_id"]))
    left = SEX_COOLDOWN - (time.time() - last)
    if left > 0:
        return f"زوج شما تازه با هم بوده؛ {_duration_fa(int(left) + 1)} دیگه می‌تونید دوباره.", me, sp
    if me["gender"] == "m" and int(me["condoms"] or 0) < 1:
        return "کاندوم نداری! اول با نوشتن «فروشگاه» کاندوم بخر.", me, sp
    if me["gender"] == "f" and int(me["pills"] or 0) < 1:
        return "قرص ضدبارداری نداری! اول با نوشتن «فروشگاه» قرص بخر.", me, sp
    return None, me, sp


async def sex_request(bot, context, chat_id: int, user_id: int, reply_to: int | None = None, q=None):
    """درخواست سکس به همسر. q = اگه از دکمه اومده برای جواب دادن به callback."""
    async def fail(text):
        if q: await q.answer(text[:190], show_alert=True)
        else: await send_html(bot, chat_id, B(html.escape(text)), reply_parameters=ReplyParameters(message_id=reply_to, allow_sending_without_reply=True) if reply_to else None)
    if chat_id > 0:
        await fail("این بخش فقط تو گروه کار می‌کنه؛ برو تو گروه و همونجا انجامش بده."); return
    err, me, sp = await _sex_check(user_id)
    if err:
        await fail(err); return
    if q: await q.answer()
    a = _ulink(me["user_id"], me["name"] or "همسر"); b = _ulink(sp["user_id"], sp["name"] or "همسر")
    item = f"{CONDOM_CE} کاندومِ" if me["gender"] == "m" else f"{PILL_CE} قرصِ"
    kb = InlineKeyboardMarkup([[btn("قبول می‌کنم", "success", icon=SEX_EIDS[0], callback_data=f"sx:yes:{me['user_id']}:{sp['user_id']}"),
                               btn("الان نه", "danger", callback_data=f"sx:no:{me['user_id']}:{sp['user_id']}")]])
    m = await send_html(bot, chat_id, f"<b>{SEX_CE}\n{a} از {b} درخواست یه شب عاشقانه داده 💞\n({item} {a} مصرف می‌شه)\n\n{b}، جواب بده 👇</b>",
                        reply_markup=kb)
    context.application.bot_data.setdefault("sex_req", {})[(chat_id, m.message_id)] = time.time()


async def sex_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    try:
        act, a_id = parts[1], int(parts[2])
        b_id = int(parts[3]) if len(parts) > 3 else 0
    except (IndexError, ValueError):
        await q.answer("درخواست نامعتبره.", show_alert=True); return
    if act == "ask":       # از دکمه‌ی پنل خانواده؛ فقط صاحب پنل
        if q.from_user.id != a_id:
            await q.answer("این پنل مال تو نیست.", show_alert=True); return
        await sex_request(context.bot, context, q.message.chat_id, a_id, q=q); return
    if q.from_user.id != b_id:
        await q.answer("فقط همسرت می‌تونه جواب بده.", show_alert=True); return
    reqs = context.application.bot_data.setdefault("sex_req", {})
    key = (q.message.chat_id, q.message.message_id)
    ts = reqs.pop(key, None)
    if ts is None or time.time() - ts > SEX_REQ_TTL:
        await q.answer("این درخواست منقضی شده.", show_alert=True)
        try: await q.edit_message_text("⌛ این درخواست منقضی شد.")
        except Exception: pass
        return
    if act == "no":
        await q.answer("رد شد.")
        await edit_html(q, B("🚫 درخواست رد شد؛ شاید یه وقت دیگه 😅")); return
    # قبول: همه‌چیز دوباره و اتمیک چک میشه
    now = time.time()
    async with pool.acquire() as c:
        async with c.transaction():
            rows = await c.fetch("SELECT * FROM users WHERE user_id=ANY($1::bigint[]) ORDER BY user_id FOR UPDATE", [a_id, b_id])
            by = {int(r["user_id"]): r for r in rows}
            me, sp = by.get(a_id), by.get(b_id)
            err = None
            if not me or not sp or not me["spouse_id"] or int(me["spouse_id"]) != b_id or not sp["spouse_id"] or int(sp["spouse_id"]) != a_id:
                err = "شما دیگه زوج نیستید."
            elif me["gender"] not in ("m", "f"):
                err = "جنسیت درخواست‌دهنده مشخص نیست."
            else:
                relrow = await c.fetchrow("SELECT last_sex FROM couple_relation WHERE pair_key=$1 FOR UPDATE", _pair_key(a_id, b_id))
                left = SEX_COOLDOWN - (now - float(relrow["last_sex"] if relrow else 0))
                col = "condoms" if me["gender"] == "m" else "pills"
                if left > 0:
                    err = f"هنوز {_duration_fa(int(left) + 1)} مونده."
                elif int(me[col] or 0) < 1:
                    err = "دیگه وسیله‌ی محافظتش تموم شده؛ از «فروشگاه» بخر."
            if err is None:
                await c.execute(f"UPDATE users SET {col}={col}-1 WHERE user_id=$1", a_id)
                en_a = await c.fetchval("UPDATE users SET energy=LEAST(energy+$1,$2) WHERE user_id=$3 RETURNING energy", SEX_ENERGY, ENERGY_MAX, a_id)
                en_b = await c.fetchval("UPDATE users SET energy=LEAST(energy+$1,$2) WHERE user_id=$3 RETURNING energy", SEX_ENERGY, ENERGY_MAX, b_id)
                rel = await add_relation(c, a_id, b_id, SEX_RELATION)
                await c.execute("UPDATE couple_relation SET last_sex=$2 WHERE pair_key=$1", _pair_key(a_id, b_id), now)
    if err:
        await q.answer(err[:190], show_alert=True)
        await edit_html(q, B(f"❌ {html.escape(err)}")); return
    await q.answer("💞")
    an = _ulink(a_id, me["name"] or "همسر"); bn = _ulink(b_id, sp["name"] or "همسر")
    prot = (f"{CONDOM_CE} {an} از کاندومش استفاده کرد" if me["gender"] == "m" else f"{PILL_CE} {an} قرص ضدبارداریش رو مصرف کرد")
    pct = lambda e: f"{_fmt_en(float(e) / ENERGY_MAX * 100)}٪"
    text = (f"<b>{SEX_CE}\n{an} و {bn} یه شب پر از عشق و آرامش کنار هم گذروندن 🌙💞\n\n{prot}\n"
            f"{ce('energy', 1, '⚡')} انرژی هر دو +{_fmt_en(SEX_ENERGY)} شد ({pct(en_a)} | {pct(en_b)})\n"
            f"💞 میزان رابطه: {rel} (+{SEX_RELATION})\n{HG_CE} دفعه‌ی بعد {SEX_COOLDOWN // 60} دقیقه دیگه</b>")
    await edit_html(q, text)


# ───────────────────────── گی و لز (بین کاراکترهای هم‌جنس، با ریپلای) ─────────────────────────
# مردها با «گی»/«لواط» و دخترها با «لز»/«قیچی» روی پیامِ یه کاربرِ هم‌جنس ریپلای می‌کنن.
# هر کاربر هر ۳۰ دقیقه فقط یه بار تو گی/لز شرکت می‌کنه (چه خودش شروع کرده باشه چه طرف مقابل بوده)؛
# تا ۳۰ دقیقه با هیچ‌کس دیگه‌ای نمی‌تونه. هر دو نفر شانسی ۵ تا ۷ هزار تومن می‌گیرن.
# اگه هر کدوم از دو طرف همسر داشته باشه انجام نمیشه.
PAIR_FUN_COOLDOWN = 30 * 60
PAIR_FUN_MONEY = (5_000, 7_000)
PAIR_FUN = {
    "gay": dict(
        gender="m", only="مرد", mate="زن", do="گی کنی", name="گی کردن",
        eid="5253687731149630615", fb="🏳️‍🌈",
        words=("گی", "گیکن", "گیکردن", "لواط"),
        lines=[
            "{a} با یه نگاه معنی‌دار {b} رو برد گوشه‌ی گپ؛ هر چی شد، هر دو با لبخند برگشتن 😏",
            "{a} و {b} همه‌ی دنیا رو فراموش کردن و فقط به هم خیره شدن؛ گروه یه لحظه سرخ شد 🌈",
            "{a} دل {b} رو برد و {b} هم بی‌مقاومت تسلیم شد؛ عاشقانه‌ترین لحظه‌ی امشبِ گپ 💞",
            "هیچکس نفهمید پشت پرده چی گذشت، ولی {a} و {b} با چهره‌ی راضی برگشتن 😌",
            "{a} با یه شاخه گل رفت سراغ {b} و {b} هم بی‌خیال همه‌چی شد؛ گروه غرق سکوت شد 🌹",
            "{a} و {b} امشب یه اتحاد جدید ساختن؛ تا صبح همه درباره‌شون حرف می‌زنن 🔥",
            "{a} به {b} چشمک زد و {b} هم جواب داد؛ همین کافی بود تا گپ آتیش بگیره 💥",
            "{a} و {b} قرارشون رو گذاشتن و با هم رفتن؛ ما موندیم و کنجکاوی 😂",
        ],
    ),
    "lez": dict(
        gender="f", only="دختر", mate="شوهر", do="لز کنی", name="لز کردن",
        eid="5328040156278576417", fb="🏳️‍🌈",
        words=("لز", "لزکن", "لزکردن", "قیچی"),
        lines=[
            "{a} و {b} یه نگاه رد و بدل کردن که کل گپ رو ساکت کرد؛ هیچکس نفهمید بعدش چی شد 🌸",
            "{a} با یه لبخند شیطون {b} رو برد گوشه‌ی گپ و هر دو با گونه‌های سرخ برگشتن 😏",
            "{a} دست {b} رو گرفت و گفت «امشب مال ماست»؛ گروه نفسش رو حبس کرد 💞",
            "{a} و {b} امشب یه قرار مخفی گذاشتن و ستاره‌ها هم شاهد بودن ✨",
            "{a} یه دسته گل برای {b} آورد و {b} هم دلش رو تقدیمش کرد 🌹",
            "{a} به {b} چشمک زد؛ همین یه لحظه کافی بود تا گپ آتیش بگیره 🔥",
            "{a} و {b} با هم رفتن یه گوشه‌ی دنج و برگشتنشون خیلی مشکوک بود 😂",
            "{a} و {b} یه جفت جدید ساختن و کل گروه از خوشحالی کف زدن 👏",
        ],
    ),
}
PAIR_FUN_WORDS = {w: k for k, cfg in PAIR_FUN.items() for w in cfg["words"]}


async def pair_fun(update: Update, context: ContextTypes.DEFAULT_TYPE, kind: str) -> bool:
    cfg = PAIR_FUN[kind]
    msg, user = update.effective_message, update.effective_user
    target = msg.reply_to_message.from_user if msg.reply_to_message else None
    if target is None or target.is_bot or target.id == user.id:
        return False
    if (rl := await rift_left(user.id, target.id)) > 0:   # قهر بودن مثل بغل و بوس جلوشو می‌گیره
        await msg.reply_text(rift_block_html(rl), parse_mode="HTML")
        return True
    now = time.time()
    await upsert_user(user.id, user.full_name)
    await pool.execute("INSERT INTO users(user_id,name) VALUES($1,$2) ON CONFLICT(user_id) DO NOTHING", target.id, target.full_name)
    err, m1, m2 = None, 0, 0
    async with pool.acquire() as c:
        async with c.transaction():   # قفل ردیف‌ها: دو پیام همزمان نمی‌تونن کولداون رو دور بزنن
            rows = await c.fetch("SELECT user_id, gender, spouse_id FROM users WHERE user_id = ANY($1::bigint[]) ORDER BY user_id FOR UPDATE",
                                 [user.id, target.id])
            by = {int(r["user_id"]): r for r in rows}
            me, other = by.get(user.id), by.get(target.id)
            if not me or not other or not me["gender"] or not other["gender"]:
                err = "هر دو نفر باید اول جنسیتشون رو از تنظیمات انتخاب کنن."
            elif me["gender"] != cfg["gender"] or other["gender"] != cfg["gender"]:
                err = f"{cfg['name']} فقط بین کاراکترهای {cfg['only']} انجام میشه."
            elif me["spouse_id"]:
                err = f"داری چکار می‌کنی؟ تو {cfg['mate']} داری!"
            elif other["spouse_id"]:
                err = f"داری چکار می‌کنی؟ این کاربر {cfg['mate']} داره!"
            else:
                # تایمر هر دو نفر (تو جدول actor_cooldowns با kind='pair_fun'؛ ردیف users قفله پس همزمانی امنه)
                waits = {}
                for uid_ in (user.id, target.id):
                    last = await c.fetchval("SELECT last_ts FROM actor_cooldowns WHERE actor_id = $1 AND kind = 'pair_fun'", uid_) or 0
                    waits[uid_] = PAIR_FUN_COOLDOWN - (now - float(last))
                if waits[user.id] > 0:
                    err = f"{HG_CE} هر کاربر هر {_duration_fa(PAIR_FUN_COOLDOWN)} فقط یه بار می‌تونه گی یا لز کنه؛ {_duration_fa(int(waits[user.id]) + 1)} دیگه صبر کن."
                elif waits[target.id] > 0:
                    err = f"{HG_CE} این کاربر تازه تو {cfg['name']} شرکت کرده؛ {_duration_fa(int(waits[target.id]) + 1)} دیگه باید صبر کنه."
                else:
                    m1, m2 = random.randint(*PAIR_FUN_MONEY), random.randint(*PAIR_FUN_MONEY)
                    await c.execute("UPDATE users SET balance = balance + $1::bigint WHERE user_id = $2::bigint", m1, user.id)
                    await c.execute("UPDATE users SET balance = balance + $1::bigint WHERE user_id = $2::bigint", m2, target.id)
                    for uid_ in (user.id, target.id):
                        await c.execute("INSERT INTO actor_cooldowns(actor_id,kind,last_ts) VALUES($1,'pair_fun',$2::double precision) "
                                        "ON CONFLICT(actor_id,kind) DO UPDATE SET last_ts = EXCLUDED.last_ts", uid_, now)
    if err:
        await msg.reply_text(B(err), parse_mode="HTML")
        return True
    a = _ltr_name(user.id, user.full_name)
    b = _ltr_name(target.id, target.full_name)
    RLM = "\u200f"
    emo = f'<tg-emoji emoji-id="{cfg["eid"]}">{cfg["fb"]}</tg-emoji>'
    line = random.choice(cfg["lines"]).format(a=a, b=b)
    text = (f"<b>{RLM}{emo} {line}\n\n"
            f"{RLM}{GIFT_MONEY_CE} {a}: {m1:,} تومن\n"
            f"{RLM}{GIFT_MONEY_CE} {b}: {m2:,} تومن</b>")
    await msg.reply_text(text, parse_mode="HTML")
    return True


# ───────────────────────── افت انرژی با زمان + قهر و آشتی ─────────────────────────
ENERGY_DECAY_PER_HOUR = 1.5      # هر ساعت این‌قدر از انرژی همه کم میشه
ENERGY_DECAY_EVERY = 10 * 60     # هر چند ثانیه یه بار کم کنه (به نسبت)
RIFT_SECONDS = 3 * 3600          # مدت قهر
RIFT_DAILY_LIMIT = 2             # هر کاربر در روز فقط دو بار با یه نفر می‌تونه قهر کنه
RIFT_ENERGY_COST = 3.0           # انرژی که از هر دو نفر کم میشه
RIFT_CE = '<tg-emoji emoji-id="5330572782528915278">💔</tg-emoji>'
PEACE_CE = '<tg-emoji emoji-id="5447109659179640411">🤝</tg-emoji>'
RIFT_WORDS = {"قهر"}
PEACE_WORDS = {"آشتی", "اشتی"}


async def energy_decay_loop():
    step = ENERGY_DECAY_PER_HOUR * ENERGY_DECAY_EVERY / 3600
    while True:
        await asyncio.sleep(ENERGY_DECAY_EVERY)
        try:
            await pool.execute("UPDATE users SET energy = GREATEST(energy - $1::double precision, 0) WHERE energy > 0", step)
            await pool.execute("DELETE FROM rifts WHERE ends_at < $1::double precision", time.time())
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("energy decay loop failed")


async def rift_left(a: int, b: int) -> float:
    """ثانیه‌ی باقی‌مانده از قهر بین دو نفر (۰ = قهر نیستن)."""
    lo, hi = sorted((int(a), int(b)))
    e = await pool.fetchval("SELECT ends_at FROM rifts WHERE user_lo = $1 AND user_hi = $2", lo, hi)
    return max(0.0, float(e) - time.time()) if e else 0.0


def rift_block_html(left: float) -> str:
    return (f"<b>{RIFT_CE} با هم قهرید! {_duration_fa(int(left) + 1)} دیگه مونده.\n"
            f"برای آشتی ریپلای کن و بنویس:</b> <code>آشتی</code>")


async def rift_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    msg, user = update.effective_message, update.effective_user
    target = msg.reply_to_message.from_user if msg.reply_to_message else None
    if target is None or target.is_bot or target.id == user.id:
        return False
    await upsert_user(user.id, user.full_name)
    await pool.execute("INSERT INTO users (user_id, name) VALUES ($1, $2) ON CONFLICT (user_id) DO NOTHING", target.id, target.full_name)
    if (left := await rift_left(user.id, target.id)) > 0:
        await msg.reply_text(f"<b>{RIFT_CE} از قبل باهم قهرید! {_duration_fa(int(left) + 1)} دیگه مونده.</b>", parse_mode="HTML")
        return True
    day = task_period_key("daily")
    now = time.time()
    lo, hi = sorted((user.id, target.id))
    async with pool.acquire() as c:
        async with c.transaction():
            cnt = await c.fetchval(
                "INSERT INTO rift_log (initiator, target, day, cnt) VALUES ($1, $2, $3, 1) "
                "ON CONFLICT (initiator, target, day) DO UPDATE SET cnt = rift_log.cnt + 1 WHERE rift_log.cnt < $4 RETURNING cnt",
                user.id, target.id, day, RIFT_DAILY_LIMIT,
            )
            if cnt is None:
                s_en = t_en = None
            else:
                await c.execute(
                    "INSERT INTO rifts (user_lo, user_hi, ends_at) VALUES ($1, $2, $3) "
                    "ON CONFLICT (user_lo, user_hi) DO UPDATE SET ends_at = EXCLUDED.ends_at",
                    lo, hi, now + RIFT_SECONDS,
                )
                s_en = await c.fetchval("UPDATE users SET energy = GREATEST(energy - $1::double precision, 0) WHERE user_id = $2::bigint RETURNING energy", RIFT_ENERGY_COST, user.id)
                t_en = await c.fetchval("UPDATE users SET energy = GREATEST(energy - $1::double precision, 0) WHERE user_id = $2::bigint RETURNING energy", RIFT_ENERGY_COST, target.id)
    if cnt is None:
        await msg.reply_text(B(f"{RIFT_CE} امروز {RIFT_DAILY_LIMIT} بار با این کاربر قهر کردی؛ دیگه نمی‌تونی."), parse_mode="HTML")
        return True

    def _bar_line(uid, name, e):
        return f"{_ltr_name(uid, name)}\n{energy_bar(e)} {_fmt_en(float(e) / ENERGY_MAX * 100)}٪"

    await msg.reply_text(
        f"<b>{_ltr_pair(user.id, user.full_name, target.id, target.full_name)}\n\n"
        f"{RIFT_CE} قهر کردن!\n"
        f"تا {_duration_fa(RIFT_SECONDS)} نمی‌تونن بغل، بوس، هدیه، دوئل جقی و کازینو با هم داشته باشن.\n\n"
        f"{ce('energy', 1, '⚡')} {_fmt_en(RIFT_ENERGY_COST)} انرژی از هر دو کم شد\n\n"
        f"{_bar_line(user.id, user.full_name, s_en)}\n{_bar_line(target.id, target.full_name, t_en)}\n\n"
        f"برای آشتی ریپلای کن و بنویس:</b> <code>آشتی</code>",
        parse_mode="HTML",
    )
    return True


async def peace_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    msg, user = update.effective_message, update.effective_user
    target = msg.reply_to_message.from_user if msg.reply_to_message else None
    if target is None or target.is_bot or target.id == user.id:
        return False
    if await rift_left(user.id, target.id) <= 0:
        await msg.reply_text(B("باهم قهر نیستید 😊"), parse_mode="HTML")
        return True
    kb = InlineKeyboardMarkup([[
        btn("✅ آشتی می‌کنم", "success", callback_data=f"ps:ok:{user.id}:{target.id}"),
        btn("❌ نه", "danger", callback_data=f"ps:no:{user.id}:{target.id}"),
    ]])
    await msg.reply_text(
        f"<b>{_ltr_pair(user.id, user.full_name, target.id, target.full_name)}\n\n"
        f"{PEACE_CE} {_mention(user.id, user.full_name)} می‌خواد باهات آشتی کنه.\n"
        f"{_mention(target.id, target.full_name)}، قبول می‌کنی؟</b>",
        parse_mode="HTML", reply_markup=kb,
        reply_parameters=ReplyParameters(message_id=msg.message_id, allow_sending_without_reply=True),
    )
    return True


async def peace_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, act, frm, to = q.data.split(":")
    frm, to = int(frm), int(to)
    if q.from_user.id != to:
        await q.answer("این درخواست آشتی مال تو نیست 😅", show_alert=True)
        return
    done = context.application.bot_data.setdefault("ps_done", set())
    key = (q.message.chat_id, q.message.message_id)
    if key in done:
        await q.answer("قبلاً جواب داده شده.", show_alert=True)
        return
    done.add(key)
    if act == "no":
        await q.answer()
        await q.edit_message_text("❌ آشتی رد شد؛ قهر ادامه داره.")
        return
    try:
        age = time.time() - q.message.date.timestamp()
    except Exception:
        age = 0
    if age > 600:
        await q.answer("درخواست منقضی شده.", show_alert=True)
        await q.edit_message_text("⌛ درخواست آشتی منقضی شد.")
        return
    lo, hi = sorted((frm, to))
    await pool.execute("DELETE FROM rifts WHERE user_lo = $1 AND user_hi = $2", lo, hi)
    await q.answer("آشتی شد 🤝")
    tname = q.from_user.full_name
    fname = context.application.bot_data.get("gf_names", {}).get((q.message.chat_id, frm)) or await pool.fetchval("SELECT name FROM users WHERE user_id = $1", frm) or "کاربر"
    await q.edit_message_text(
        f"<b>{_ltr_pair(frm, fname, to, tname)}\n\n{PEACE_CE} آشتی کردن! حالا دوباره می‌تونن بغل، بوس، هدیه، دوئل و کازینو داشته باشن.</b>",
        parse_mode="HTML",
    )


async def gift_hug_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    key = _word_key(text)
    if key in GIFT_LIST_WORDS or key.replace("‌", "") in GIFT_LIST_WORDS:
        await show_gifts(update, context)
        return True
    if key.startswith("هدیه") and len(key) <= 40:
        await gift_request(update, context, key[4:])
        return True
    if key in RIFT_WORDS:
        return await rift_start(update, context)
    if key in PEACE_WORDS:
        return await peace_request(update, context)
    kind = HUG_WORDS.get(key)
    if kind:
        return await hug_kiss(update, context, kind)
    fun = PAIR_FUN_WORDS.get(key)
    if fun:
        return await pair_fun(update, context, fun)
    return False



# ───────────────────────── زیرمجموعه‌گیری ─────────────────────────
# هر کاربر یه لینک اختصاصی داره: https://t.me/<ربات>?start=ref_<آیدی>
# کاربر جدیدی که با لینک بیاد و عضو کانال‌های اجباری بشه، ۱m پول بازی به دعوت‌کننده میده.
# اگه بعداً از کانال‌ها لفت بده، همون مبلغ از دعوت‌کننده کم میشه و بهش خبر داده میشه.
REF_EID = "5769289093221454192"
REF_CE = f'<tg-emoji emoji-id="{REF_EID}">👥</tg-emoji>'
REFERRAL_REWARD = 1_000_000
REF_CHECK_EVERY = 10 * 60   # هر چند ثانیه یه دسته از زیرمجموعه‌ها دوباره چک بشن
REF_CHECK_BATCH = 40


async def register_referral(user, payload: str):
    """ثبت دعوت (فقط برای کاربرِ جدیدی که هنوز استارت نزده)."""
    try:
        if not payload or not payload.startswith("ref_"):
            return
        ref = int(payload[4:])
        if ref == user.id:
            return
        me = await pool.fetchrow("SELECT started FROM users WHERE user_id = $1", user.id)
        if me and me["started"]:
            return
        r = await pool.fetchrow("SELECT started FROM users WHERE user_id = $1", ref)
        if not r or not r["started"]:
            return
        await pool.execute(
            "INSERT INTO referrals (referred_id, referrer_id, status, created_at) VALUES ($1, $2, 'pending', $3) "
            "ON CONFLICT (referred_id) DO NOTHING", user.id, ref, time.time())
    except Exception:
        log.warning("register_referral failed", exc_info=True)


async def process_referral(bot, uid: int):
    """وقتی کاربر عضو کانال‌ها شد، پاداش دعوت‌کننده پرداخت میشه (فقط یک‌بار)."""
    try:
        async with pool.acquire() as c:
            async with c.transaction():
                r = await c.fetchrow("SELECT referrer_id FROM referrals WHERE referred_id = $1 AND status = 'pending' FOR UPDATE", uid)
                if not r:
                    return
                now = time.time()
                await c.execute("UPDATE referrals SET status = 'rewarded', rewarded_at = $2, last_check = $2 WHERE referred_id = $1", uid, now)
                await c.execute("UPDATE users SET balance = balance + $1::bigint WHERE user_id = $2::bigint", REFERRAL_REWARD, r["referrer_id"])
        name = await pool.fetchval("SELECT name FROM users WHERE user_id = $1", uid) or "کاربر"
        await send_html(
            bot, r["referrer_id"],
            B(f"{REF_CE} زیرمجموعه جدید!\n\n{_ulink(uid, name)} با لینک تو وارد ربات شد و عضو کانال‌ها شد.\n"
              f"{MONEY_CE} +{REFERRAL_REWARD:,} تومن پول بازی به حسابت اضافه شد!\n"
              "⚠️ اگه از کانال‌ها لفت بده، همین مبلغ ازت کم میشه."))
    except Exception:
        log.warning("process_referral failed", exc_info=True)


async def revoke_referral(bot, referred_id: int, referrer_id: int):
    async with pool.acquire() as c:
        async with c.transaction():
            ok = await c.fetchval("UPDATE referrals SET status = 'revoked' WHERE referred_id = $1 AND status = 'rewarded' RETURNING 1", referred_id)
            if not ok:
                return
            row = await c.fetchrow("SELECT balance, bank_balance FROM users WHERE user_id = $1 FOR UPDATE", referrer_id)
            if not row:
                return
            from_pocket = min(int(row["balance"] or 0), REFERRAL_REWARD)
            from_bank = min(int(row["bank_balance"] or 0), REFERRAL_REWARD - from_pocket)
            await c.execute("UPDATE users SET balance = balance - $1::bigint, bank_balance = bank_balance - $2::bigint WHERE user_id = $3::bigint",
                            from_pocket, from_bank, referrer_id)
    name = await pool.fetchval("SELECT name FROM users WHERE user_id = $1", referred_id) or "کاربر"
    try:
        await send_html(
            bot, referrer_id,
            B(f"{REF_CE} یکی از زیرمجموعه‌هات لفت داد!\n\n{_ulink(referred_id, name)} از کانال‌های اجباری خارج شد.\n"
              f"{MONEY_CE} -{from_pocket + from_bank:,} تومن پول بازی ازت کم شد."))
    except Exception:
        log.warning("revoke notify failed", exc_info=True)


async def referral_check_loop(app):
    while True:
        await asyncio.sleep(REF_CHECK_EVERY)
        try:
            rows = await pool.fetch(
                "SELECT referred_id, referrer_id FROM referrals WHERE status = 'rewarded' ORDER BY last_check LIMIT $1", REF_CHECK_BATCH)
            for r in rows:
                ok = await is_channel_member(app.bot, r["referred_id"])
                if ok is False:
                    await revoke_referral(app.bot, r["referred_id"], r["referrer_id"])
                else:
                    await pool.execute("UPDATE referrals SET last_check = $2 WHERE referred_id = $1", r["referred_id"], time.time())
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("referral check loop failed")


async def referral_view(bot, uid: int):
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=ref_{uid}"
    st = await pool.fetchrow(
        "SELECT count(*) FILTER (WHERE status = 'rewarded') AS ok, count(*) FILTER (WHERE status = 'revoked') AS left_, "
        "count(*) FILTER (WHERE status = 'pending') AS pend FROM referrals WHERE referrer_id = $1", uid)
    ok_n, left_n, pend_n = int(st["ok"] or 0), int(st["left_"] or 0), int(st["pend"] or 0)
    import urllib.parse
    share = "https://t.me/share/url?" + urllib.parse.urlencode({"url": link, "text": "بیا تو ربات جق‌بازی، با لینک من وارد شو و پول بگیر! 🔥"})
    text = (f"{REF_CE} <b>زیرمجموعه‌گیری</b>\n\n"
            f"{MONEY_CE} به ازای هر کاربر جدیدی که با لینک اختصاصی تو وارد ربات بشه و عضو کانال‌ها بشه، "
            f"<b>{REFERRAL_REWARD:,} تومن</b> پول بازی می‌گیری!\n"
            "⚠️ اگه زیرمجموعه‌ات از کانال‌ها لفت بده، همین مبلغ ازت کم میشه.\n\n"
            f"🔗 لینک اختصاصی تو (روش بزن تا کپی بشه):\n<code>{link}</code>\n\n"
            f"✅ زیرمجموعه فعال: <b>{ok_n:,}</b>\n"
            f"{MONEY_CE} درآمد فعلی: <b>{ok_n * REFERRAL_REWARD:,} تومن</b>\n"
            f"⏳ منتظر تایید عضویت: <b>{pend_n:,}</b>\n"
            f"💔 لفت‌داده: <b>{left_n:,}</b>")
    kb = InlineKeyboardMarkup([
        [btn("📤 ارسال لینک به دوستان", "success", icon=REF_EID, url=share)],
        [btn("برگشت", "primary", icon=EID_BACK, callback_data="bu:home")],
    ])
    return text, kb


async def referral_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    text, kb = await referral_view(context.bot, q.from_user.id)
    await edit_html(q, text, kb)


async def referral_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await upsert_user(user.id, user.full_name)
    text, kb = await referral_view(context.bot, user.id)
    await send_html(context.bot, user.id, text, reply_markup=kb)


async def buy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("buy_wait", None)
    text, kb = buy_menu_view()
    await send_html(context.bot, update.effective_user.id, text, reply_markup=kb)


def kbtn(text: str, style: str | None = None, icon: str | None = None) -> KeyboardButton:
    extra = {}
    if style:
        extra["style"] = style
    if icon:
        extra["icon_custom_emoji_id"] = icon
    if extra:
        try:
            return KeyboardButton(text, api_kwargs=extra)
        except TypeError:
            pass
    return KeyboardButton(text)


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [kbtn("خرید پول بازی", "success", EID_MONEY)],
            [kbtn("زیرمجموعه‌گیری", "primary", REF_EID), kbtn("خودم", "danger", EID_TROPHY)],
            [kbtn("بانک", None, BANK_EMOJIS[0]), kbtn("تسک‌ها", None, EID_TROPHY)],
        ],
        resize_keyboard=True, is_persistent=True, input_field_placeholder="از دکمه‌های پایین انتخاب کن...",
    )


async def send_main_menu(bot, chat_id: int):
    try:
        await send_html(bot, chat_id, B(f"{REF_CE} منوی ربات پایین فعاله؛ از دکمه‌ها انتخاب کن 👇"), reply_markup=main_menu_keyboard())
    except Exception:
        log.warning("send_main_menu failed", exc_info=True)


async def menu_text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """دکمه‌های منوی پایین (کیبورد) تو پی‌وی."""
    msg = update.effective_message
    t = (msg.text or "").replace("\u200c", "").strip() if msg else ""
    if t.replace(" ", "") == "خریدپولبازی":
        await buy_cmd(update, context)
        return True
    if t == "زیرمجموعهگیری":
        await referral_cmd(update, context)
        return True
    return False


async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_username = (await context.bot.get_me()).username or ""
    await send_html(context.bot, update.effective_user.id, welcome_ok(), reply_markup=welcome_keyboard(bot_username))
    await send_main_menu(context.bot, update.effective_user.id)


# ───────────────────────── خرید پول بازی ─────────────────────────
BUY_CARD = "6219861851160068"
BUY_CARD_OWNER = "ظریفی"
# (پول بازی، قیمت به تومن، عنوان)
BUY_PACKAGES = [
    (100_000_000, 100_000, "۱۰۰m"),
    (350_000_000, 347_000, "۳۵۰m"),
    (500_000_000, 489_000, "۵۰۰m"),
    (1_000_000_000, 860_000, "۱ میلیارد"),
]
BUY_MAX_PENDING = 3
PURCHASE_MSGS: dict[int, list[tuple[int, int]]] = {}   # order_id -> پیام‌هایی که برای پشتیبان‌ها رفته


def buy_menu_view():
    lines = [f"{MONEY_CE} <b>خرید پول بازی</b>", "", "یکی از بسته‌ها رو انتخاب کن 👇", ""]
    rows = []
    for i, (amount, price, title) in enumerate(BUY_PACKAGES):
        lines.append(f"{TROPHY_CE if i == len(BUY_PACKAGES) - 1 else '💎'} <b>{title}</b> پول بازی ← <b>{price:,} تومن</b>")
        rows.append([btn(f"{title} پول بازی • {price:,} تومن", "success" if i == len(BUY_PACKAGES) - 1 else "primary", icon=EID_MONEY, callback_data=f"bu:pk:{i}")])
    lines.append("\n🔥 هرچی بسته بزرگ‌تر، ارزون‌تر!")
    rows.append([btn("برگشت", "primary", icon=EID_BACK, callback_data="bu:home")])
    return B("\n".join(lines)), InlineKeyboardMarkup(rows)


async def buy_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    parts = q.data.split(":")
    act = parts[1]
    if q.message.chat.type != "private":
        await q.answer("خرید فقط تو پی‌وی ربات ممکنه.", show_alert=True)
        return
    await q.answer()
    if act == "home":
        context.user_data.pop("buy_wait", None)
        bot_username = (await context.bot.get_me()).username or ""
        await edit_html(q, welcome_ok(), welcome_keyboard(bot_username))
    elif act == "menu":
        context.user_data.pop("buy_wait", None)
        text, kb = buy_menu_view()
        await edit_html(q, text, kb)
    elif act == "cancel":
        context.user_data.pop("buy_wait", None)
        text, kb = buy_menu_view()
        await edit_html(q, text, kb)
    elif act == "pk":
        try:
            i = int(parts[2])
            amount, price, title = BUY_PACKAGES[i]
        except (IndexError, ValueError):
            return
        context.user_data["buy_wait"] = i
        text = (f"{MONEY_CE} <b>خرید {title} پول بازی</b>\n\n"
                f"💳 مبلغ قابل پرداخت: <b>{price:,} تومن</b>\n\n"
                f"لطفاً مبلغ رو به این شماره کارت واریز کن:\n<code>{BUY_CARD}</code>\n"
                f"👤 به نام: <b>{BUY_CARD_OWNER}</b>\n\n"
                "📸 بعد از پرداخت، <b>عکس رسید</b> رو همینجا بفرست تا برای پشتیبانی ارسال بشه. "
                f"بعد از تایید، {title} پول بازی به حسابت میاد.")
        kb = InlineKeyboardMarkup([
            [btn("❌ انصراف", "danger", callback_data="bu:cancel")],
            [btn("برگشت", "primary", icon=EID_BACK, callback_data="bu:menu")],
        ])
        await edit_html(q, text, kb)


async def buy_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """عکس رسید کاربر. True یعنی پیام مصرف شد."""
    msg, user = update.effective_message, update.effective_user
    i = context.user_data.get("buy_wait")
    if i is None or not msg:
        return False
    if not msg.photo:
        await msg.reply_text("📸 لطفاً رسید رو به‌صورت عکس بفرست (یا از دکمه‌ی انصراف استفاده کن).")
        return True
    amount, price, title = BUY_PACKAGES[i]
    await upsert_user(user.id, user.full_name)
    pend = await pool.fetchval("SELECT count(*) FROM purchases WHERE user_id = $1 AND status = 'pending'", user.id)
    if pend >= BUY_MAX_PENDING:
        await msg.reply_text(f"تا الان {pend} درخواست در انتظار تایید داری؛ صبر کن پشتیبانی بررسی کنه.")
        return True
    file_id = msg.photo[-1].file_id
    oid = await pool.fetchval(
        "INSERT INTO purchases (user_id, amount, price, status, created_at, file_id) VALUES ($1,$2,$3,'pending',$4,$5) RETURNING id",
        user.id, amount, price, time.time(), file_id)
    context.user_data.pop("buy_wait", None)
    caption = (f"🧾 <b>رسید خرید پول بازی #{oid}</b>\n\n"
               f"👤 کاربر: {_ulink(user.id, user.full_name)}"
               + (f" (@{html.escape(user.username)})" if user.username else "") + f"\n🆔 <code>{user.id}</code>\n"
               f"💎 بسته: <b>{title}</b> ({amount:,} پول بازی)\n"
               f"💳 مبلغ: <b>{price:,} تومن</b>")
    kb = InlineKeyboardMarkup([[
        btn("✅ تایید", "success", callback_data=f"bp:ok:{oid}"),
        btn("❌ رد", "danger", callback_data=f"bp:no:{oid}"),
    ]])
    sent = []
    for aid in [a for a in (set(OWNER_IDS) | set(STAFF)) if has_perm(a, "pay")]:
        try:
            m = await context.bot.send_photo(aid, file_id, caption=caption, parse_mode="HTML", reply_markup=kb)
            sent.append((aid, m.message_id))
        except Exception:
            log.warning("purchase notify failed for %s", aid, exc_info=True)
    PURCHASE_MSGS[oid] = sent
    if len(PURCHASE_MSGS) > 500:
        PURCHASE_MSGS.pop(next(iter(PURCHASE_MSGS)), None)
    await send_html(context.bot, user.id,
                    B(f"✅ رسید شماره #{oid} ثبت شد و برای پشتیبانی ارسال شد.\n{HG_CE} بعد از بررسی، نتیجه همینجا بهت اعلام میشه."))
    return True


async def purchase_review_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not has_perm(q.from_user.id, "pay"):
        await q.answer("دسترسی بررسی خرید رو نداری.", show_alert=True)
        return
    _, act, oid_s = q.data.split(":")
    oid = int(oid_s)
    approve = act == "ok"
    async with pool.acquire() as c:
        async with c.transaction():
            r = await c.fetchrow(
                "UPDATE purchases SET status = $2, reviewer = $3, reviewed_at = $4 WHERE id = $1 AND status = 'pending' "
                "RETURNING user_id, amount, price", oid, "approved" if approve else "rejected", q.from_user.id, time.time())
            if not r:
                await q.answer("این رسید قبلاً بررسی شده.", show_alert=True)
                try:
                    await q.edit_message_reply_markup(reply_markup=None)
                except Exception:
                    pass
                return
            if approve:
                await c.execute("UPDATE users SET balance = balance + $1::bigint WHERE user_id = $2::bigint", r["amount"], r["user_id"])
    await q.answer("تایید شد ✅" if approve else "رد شد ❌")
    verdict = f"\n\n{'✅ تایید شد' if approve else '❌ رد شد'} توسط {html.escape(q.from_user.full_name)}"
    try:
        await q.edit_message_caption(caption=(q.message.caption_html or "") + verdict, parse_mode="HTML", reply_markup=None)
    except Exception:
        log.warning("purchase caption edit failed", exc_info=True)
    for chat_id, mid in PURCHASE_MSGS.pop(oid, []):
        if (chat_id, mid) == (q.message.chat_id, q.message.message_id):
            continue
        try:
            await context.bot.edit_message_caption(chat_id, mid, caption=(q.message.caption_html or "") + verdict, parse_mode="HTML", reply_markup=None)
        except Exception:
            pass
    if approve:
        text = B(f"{TROPHY_CE} خریدت تایید شد!\n\n{MONEY_CE} <b>+{r['amount']:,}</b> پول بازی به حسابت اضافه شد.\nرسید شماره #{oid} • مرسی از خریدت 🔥")
    else:
        text = B(f"❌ رسید شماره #{oid} تایید نشد.\nاگه پرداخت کردی و فکر می‌کنی اشتباهه، با پشتیبانی تماس بگیر.")
    try:
        await send_html(context.bot, r["user_id"], text)
    except Exception:
        log.warning("purchase result notify failed", exc_info=True)


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(startup)
        .post_shutdown(close_db)
        .build()
    )
    app.add_handler(TypeHandler(Update, gate), group=-1)
    app.add_handler(CallbackQueryHandler(self_panel_cb, pattern=r"^sp:(home|bank|robbery|cars|powers|spy|theft|hand|casino|settings|gender|guide|guide2|transfer):\d+$"))
    app.add_handler(CallbackQueryHandler(hand_upgrade_cb, pattern=r"^sp:upgrade:\d+:\d+$"))
    app.add_handler(CallbackQueryHandler(account_transfer_cb, pattern=r"^at:(go|ok|no):\d+:\d+$"))
    app.add_handler(CallbackQueryHandler(guide_callback, pattern=r"^guide:(open|back|p1)$"))
    app.add_handler(CallbackQueryHandler(robbery_cb, pattern=r"^rb:"))
    app.add_handler(CallbackQueryHandler(home_cb, pattern=r"^hm:"))
    app.add_handler(CallbackQueryHandler(bank_cb, pattern=r"^bk:(dep|wd):\d+$"))
    app.add_handler(CallbackQueryHandler(marriage_cb, pattern=r"^mar:(yes|no):\d+:\d+$"))
    app.add_handler(CallbackQueryHandler(affair_cb, pattern=r"^af:(divorce|forgive):\d+:\d+:\-?\d+$"))
    app.add_handler(CallbackQueryHandler(casino_cb, pattern=r"^cz:"))
    app.add_handler(CallbackQueryHandler(sex_cb, pattern=r"^sx:"))
    app.add_handler(CallbackQueryHandler(shop_cb, pattern=r"^shp:"))
    app.add_handler(MessageHandler(filters.Dice.SLOT_MACHINE & filters.ChatType.GROUPS, slot_dice_msg))
    app.add_handler(CallbackQueryHandler(duel_answer, pattern=r"^du:(ok|no):\d+:\d+$"))
    app.add_handler(CallbackQueryHandler(referral_cb, pattern=r"^rf:open$"))
    app.add_handler(CallbackQueryHandler(buy_cb, pattern=r"^bu:(home|menu|cancel|pk:\d+)$"))
    app.add_handler(CallbackQueryHandler(purchase_review_cb, pattern=r"^bp:(ok|no):\d+$"))
    app.add_handler(CommandHandler("admin", admin_cmd, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("support", support_cmd, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("addsupport", add_support_cmd, filters=filters.ChatType.PRIVATE))
    app.add_handler(CallbackQueryHandler(support_cb, pattern=r"^sup:"))
    app.add_handler(CallbackQueryHandler(admin_cb, pattern=r"^ad:"))
    app.add_handler(CallbackQueryHandler(task_cb, pattern=r"^tk:"))
    app.add_handler(CallbackQueryHandler(car_cb, pattern=r"^car:"))
    app.add_handler(CommandHandler("garage", show_garage))
    app.add_handler(CommandHandler("start", gw_start, filters=filters.ChatType.PRIVATE & filters.Regex(r"^/start(?:@\w+)? gw(?:top)?_\d+")))
    app.add_handler(CallbackQueryHandler(gw_cb, pattern=r"^gw:"))
    app.add_handler(CommandHandler("start", start, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("menu", menu_cmd, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("referral", referral_cmd, filters=filters.ChatType.PRIVATE))
    app.add_handler(CommandHandler("buy", buy_cmd, filters=filters.ChatType.PRIVATE))
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
    app.add_handler(CallbackQueryHandler(gift_answer, pattern=r"^gf:(ok|no):\d+:\d+:\d+$"))
    app.add_handler(CallbackQueryHandler(peace_answer, pattern=r"^ps:(ok|no):\d+:\d+$"))
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

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
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, MessageEntity, ReplyParameters, Update, WebAppInfo
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


BOT_VERSION = "پنل شخصی یکپارچه + خوش‌آمد جدید + افزودن به گروه v3"


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
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS bank_balance BIGINT NOT NULL DEFAULT 0")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS hand_level INT NOT NULL DEFAULT 0")
        await c.execute("CREATE TABLE IF NOT EXISTS home_assets (asset_id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE, house_type TEXT NOT NULL, upgrade_level INT NOT NULL DEFAULT 0, last_claim DOUBLE PRECISION NOT NULL DEFAULT 0, UNIQUE(user_id, house_type))")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS gender TEXT")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS banned_until DOUBLE PRECISION NOT NULL DEFAULT 0")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS jaghs BIGINT NOT NULL DEFAULT 0")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_shift DOUBLE PRECISION NOT NULL DEFAULT 0")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS shift_seen TEXT NOT NULL DEFAULT ''")
        await c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS work_count BIGINT NOT NULL DEFAULT 0")
        await c.execute("CREATE TABLE IF NOT EXISTS casino_records (chat_id BIGINT NOT NULL, game TEXT NOT NULL, user_id BIGINT NOT NULL, name TEXT, record BIGINT NOT NULL DEFAULT 0, PRIMARY KEY(chat_id, game))")
        await c.execute("CREATE TABLE IF NOT EXISTS robbery_state (user_id BIGINT NOT NULL, robbery_type TEXT NOT NULL, cooldown_until DOUBLE PRECISION NOT NULL DEFAULT 0, blocked_until DOUBLE PRECISION NOT NULL DEFAULT 0, PRIMARY KEY (user_id, robbery_type))")
        await c.execute("CREATE TABLE IF NOT EXISTS robbery_profile (user_id BIGINT PRIMARY KEY, trace INTEGER NOT NULL DEFAULT 0, cooldown_until DOUBLE PRECISION NOT NULL DEFAULT 0, arrest_until DOUBLE PRECISION NOT NULL DEFAULT 0, trace_until DOUBLE PRECISION NOT NULL DEFAULT 0)")
        await c.execute("CREATE TABLE IF NOT EXISTS tasks (task_id BIGSERIAL PRIMARY KEY, period TEXT NOT NULL CHECK(period IN ('daily', 'weekly')), title TEXT NOT NULL, active BOOLEAN NOT NULL DEFAULT TRUE, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")
        await c.execute("CREATE TABLE IF NOT EXISTS task_completions (task_id BIGINT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE, user_id BIGINT NOT NULL, period_key TEXT NOT NULL, completed BOOLEAN NOT NULL DEFAULT FALSE, PRIMARY KEY(task_id, user_id, period_key))")
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
        # یک‌بار: رنگ «سفید» شد رنگ شماره‌ی ۰ (پیش‌فرض). قرمز/آبی/صورتی یکی می‌رن جلو تا رنگ خریده‌شده‌ی کاربرها حفظ بشه
        if not await c.fetchval("SELECT 1 FROM bot_settings WHERE key = 'car_white_migr_1'"):
            await c.execute("UPDATE cars SET color = color + 1 WHERE color >= 1")
            await c.execute("INSERT INTO bot_settings (key, value) VALUES ('car_white_migr_1', '1') ON CONFLICT (key) DO NOTHING")
        # یک‌بار: ماشین همه‌ی کاربرها گرفته میشه (سیستم جدید پنل ماشین). با ری‌استارت‌های بعدی دوباره اجرا نمیشه.
        if not await c.fetchval("SELECT 1 FROM bot_settings WHERE key = 'cars_wipe_2026_11_06'"):
            await c.execute("DELETE FROM cars")
            await c.execute("INSERT INTO bot_settings (key, value) VALUES ('cars_wipe_2026_11_06', '1') ON CONFLICT (key) DO NOTHING")
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
  </nav>
</div>
<div id="toast"></div>
<script>
'use strict';
const tg=window.Telegram&&Telegram.WebApp;
const tgv=v=>!!(tg&&tg.isVersionAtLeast&&tg.isVersionAtLeast(v));
if(tg){try{tg.ready();tg.expand();if(tgv('7.7'))tg.disableVerticalSwipes();tg.setHeaderColor&&tg.setHeaderColor('secondary_bg_color')}catch(e){}}
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
  if(r.status===403)throw new Error('دسترسی نداری یا نشست منقضی شده. مینی‌اپ رو ببند و دوباره از /admin باز کن.');
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
    if(!rows.length&&data.length)list.append(h

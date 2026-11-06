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
ME_WORD = "خودم"  # تو گروه این کلمه رو بنویسی کاراکترت نشون داده میشه

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
  const head=h('div',{class:'top row'},backBtn(),all?h('div',{class:'av',style:'width:38px;height:38px;background:var(--btn)'},'📚'):avatar('chat',chat.chat_id,chat.title,38),h('div',{class:'grow'},h('b',{class:'ct',style:'display:block'},chat.title||chat.chat_id),sub),h('button',{class:'ib',onclick:()=>reload()},'↻'),h('button',{class:'ib',onclick:()=>{qbar.classList.toggle('hid');if(!qbar.classList.contains('hid'))qin.focus()}},'🔍'),liveB);
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
    let me,st;try{[me,st]=await Promise.all([api('/api/me'),api('/api/stats')])}catch(e){body.replaceChildren(h('div',{class:'empty err'},e.message));return}
    const sw=h('div',{class:'sw'+(me.bot_enabled?' on':''),onclick:async()=>{
      if(!await ask(me.bot_enabled?'ربات خاموش بشه؟ (فقط پشتیبان‌ها می‌تونن ازش استفاده کنن)':'ربات روشن بشه؟'))return;
      try{const r=await api('/api/toggle',{});toast(r.enabled?'🟢 ربات روشن شد':'🔴 ربات خاموش شد');load()}catch(e){toast(e.message,1)}}});
    const S=[['👥 کاربران',st.users],['✅ استارت‌کرده',st.started],['💬 گروه‌ها',st.chats],['✉️ پیام‌های ثبت‌شده',st.msgs],['🔥 پیام ۲۴ ساعت اخیر',st.msgs24],['🧑‍🤝‍🧑 فعال ۲۴ ساعت اخیر',st.active24],['💰 مجموع دارایی',st.balance],['🤛 مجموع جق‌ها',st.jaghs],['🚫 محروم‌ها',st.banned],['🪪 اعضای دیده‌شده',st.members]];
    const mx=Math.max(1,...st.hours);
    body.replaceChildren(
      h('div',{class:'card'},h('div',{class:'row2',style:'padding:10px 0'},h('div',{},h('b',{},'وضعیت ربات'),h('div',{class:'sub'},me.bot_enabled?'🟢 روشن':'🔴 خاموش')),sw)),
      h('div',{class:'grid'},S.map(([l,v])=>h('div',{class:'stat'},h('b',{},num(v)),h('span',{},l)))),
      h('div',{class:'card'},h('h3',{},'پیام‌های ۲۴ ساعت اخیر (هر ستون = یک ساعت)'),h('div',{class:'bars'},st.hours.map((n,i)=>h('i',{style:'height:'+Math.round(n/mx*100)+'%',title:num(n)}))),st.top.length?h('div',{style:'margin-top:8px'},h('div',{class:'sub'},'پرحرف‌های ۲۴ ساعت اخیر'),st.top.map(t=>h('div',{class:'kv cp',onclick:()=>openProfile(t.user_id)},h('span',{},t.name||t.user_id),h('span',{},num(t.n)+' پیام')))):null),
      h('div',{class:'card'},h('h3',{},'پول و جق'),h('div',{class:'grid',style:'margin:6px 0 10px'},
        h('button',{class:'act red',onclick:()=>adjustSheet('balance','sub',null)},'💸 کم کردن پول'),h('button',{class:'act ok',onclick:()=>adjustSheet('balance','add',null)},'➕ افزودن پول'),
        h('button',{class:'act red',onclick:()=>adjustSheet('jaghs','sub',null)},'🤛 کم کردن جق'),h('button',{class:'act ok',onclick:()=>adjustSheet('jaghs','add',null)},'➕ افزودن جق'))),
      h('div',{class:'card'},h('h3',{},'مدیریت'),h('div',{class:'grid',style:'margin:6px 0 10px'},
        h('button',{class:'act red',onclick:()=>banSheet(null)},'🚫 محروم کردن'),h('button',{class:'act ok',onclick:()=>pickUser(u=>doUnban(u))},'✅ رفع محرومیت'),
        h('button',{class:'act',onclick:()=>push(UsersScreen(true))},'📛 لیست محروم‌ها'),h('button',{class:'act',onclick:()=>push(TasksScreen())},'📋 تسک‌ها'),
        h('button',{class:'act',onclick:()=>push(BroadcastScreen())},'📢 پیام همگانی'),h('button',{class:'act',onclick:()=>push(LogScreen())},'🧾 گزارش عملیات'))),
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
  const list=h('div',{class:'card'});let period='daily';
  const inp=h('input',{class:'inp',placeholder:'متن تسک جدید…',maxlength:'300'});
  const seg=h('div',{class:'seg'});
  const drawSeg=()=>seg.replaceChildren(h('button',{class:period==='daily'?'on':'',onclick:()=>{period='daily';drawSeg()}},'روزانه'),h('button',{class:period==='weekly'?'on':'',onclick:()=>{period='weekly';drawSeg()}},'هفتگی'));drawSeg();
  async function load(){
    try{const d=await api('/api/tasks');
      list.replaceChildren(h('h3',{},'تسک‌های فعال'),...(d.tasks.length?d.tasks.map(t=>h('div',{class:'tk'},h('span',{class:'badge '+(t.period==='daily'?'':'gr')},t.period==='daily'?'روزانه':'هفتگی'),h('div',{class:'grow'},t.title,h('div',{class:'sub'},num(t.done)+' نفر انجام داده')),h('button',{class:'ib',onclick:async()=>{if(!await ask('این تسک حذف بشه؟'))return;try{await api('/api/task/delete',{task_id:t.task_id});toast('حذف شد');load()}catch(e){toast(e.message,1)}}},'🗑'))):[h('div',{class:'sub',style:'padding:10px 0'},'تسکی نیست')]));
    }catch(e){list.replaceChildren(h('div',{class:'empty err'},e.message))}
  }
  const add=h('button',{class:'btn',onclick:async()=>{const t=inp.value.trim();if(!t)return toast('متن تسک خالیه',1);try{await api('/api/task/add',{period,title:t});inp.value='';toast('✅ تسک اضافه شد');load()}catch(e){toast(e.message,1)}}},'➕ افزودن تسک');
  load();
  return h('div',{class:'scr'},h('div',{class:'top row'},backBtn(),h('b',{class:'grow'},'📋 تسک‌ها')),h('div',{class:'scroll'},list,h('div',{class:'card',style:'padding-bottom:14px'},h('h3',{},'تسک جدید'),seg,inp,add)));
}

function LogScreen(){
  const list=h('div',{class:'card'});
  (async()=>{try{const d=await api('/api/log');list.replaceChildren(...(d.length?d.map(x=>h('div',{class:'lg'},h('div',{class:'row2'},h('b',{},ACT[x.action]||x.action),h('span',{class:'sub'},fFull.format(x.ts*1000))),h('div',{class:'sub'},'توسط '+(x.admin_name||x.admin_id)+(x.target?' • روی '+x.target:'')),x.details?h('div',{},x.details):null)):[h('div',{class:'empty'},'هنوز عملیاتی ثبت نشده')]))}catch(e){list.replaceChildren(h('div',{class:'empty err'},e.message))}})();
  return h('div',{class:'scr'},h('div',{class:'top row'},backBtn(),h('b',{class:'grow'},'🧾 گزارش عملیات')),h('div',{class:'scroll'},list));
}

/* ───── شروع ───── */
const TABS={chats:ChatsScreen,users:()=>UsersScreen(false),panel:PanelScreen},tabEls={};
function openTab(k){
  document.querySelectorAll('#tabs button').forEach(b=>b.classList.toggle('on',b.dataset.k===k));
  if(!tabEls[k])tabEls[k]=TABS[k]();push(tabEls[k],true);
}
document.querySelectorAll('#tabs button').forEach(b=>b.onclick=()=>openTab(b.dataset.k));
openTab('chats');
</script></body></html>
"""


# ───────────── ابزارهای کوچک ─────────────
class _ApiError(Exception):
    def __init__(self, msg: str, status: int = 400):
        super().__init__(msg)
        self.status = status


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
               "version": BOT_VERSION, "retention_days": MSG_LOG_DAYS})


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
        "SELECT t.task_id, t.period, t.title, (SELECT count(*) FROM task_completions tc WHERE tc.task_id = t.task_id AND tc.completed "
        "AND tc.period_key = CASE t.period WHEN 'daily' THEN $1 ELSE $2 END) AS done "
        "FROM tasks t WHERE t.active = TRUE ORDER BY CASE t.period WHEN 'daily' THEN 0 ELSE 1 END, t.task_id", dk, wk)
    return _j({"tasks": [dict(r) for r in rows]})


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
    if period not in ("daily", "weekly") or not title:
        raise _ApiError("متن تسک خالیه")
    await pool.execute("INSERT INTO tasks (period, title) VALUES ($1, $2)", period, title)
    await _audit(user, "task_add", None, f"{'روزانه' if period == 'daily' else 'هفتگی'}: {title}")
    return _j({"ok": True})


async def _p_task_del(b, user):
    tid = _int(b.get("task_id"))
    if tid is None:
        raise _ApiError("درخواست نامعتبر")
    title = await pool.fetchval("UPDATE tasks SET active = FALSE WHERE task_id = $1 RETURNING title", tid)
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


_GET = {
    "/api/me": _g_me, "/api/stats": _g_stats, "/api/groups": _g_groups, "/api/messages": _g_messages,
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
    if user is None or int(user["id"]) not in ADMIN_IDS:
        return _j({"error": "forbidden"}, 403)
    if pool is None:
        return _j({"error": "دیتابیس هنوز آماده نیست"}, 503)
    try:
        if method in ("GET", "HEAD"):
            fn = _GET.get(path)
            if fn:
                return await fn(qs, user)
        elif method == "POST":
            fn = _POST.get(path)
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
        elif clen > 200_000:
            status, ctype, body, extra = 413, "text/plain", b"too large", {}
        else:
            raw = await asyncio.wait_for(reader.readexactly(clen), 15) if clen else b""
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
        "🎮 برای بانک، خلاف، ماشین و ارتقای دست بنویس «خودم اینارو ببین».\n\n"
        "🚀 آماده‌ای؟ ربات رو به گروهت ببر و بازی رو شروع کن!"
    )


def welcome_keyboard(bot_username: str = "") -> InlineKeyboardMarkup:
    rows = [[btn("📖 راهنما", "primary", callback_data="guide:open")]]
    if bot_username:
        rows.append([btn("👥 منو ببر به گروهت", "success", url=f"https://t.me/{bot_username}?startgroup=true")])
    return InlineKeyboardMarkup(rows)


def guide_text() -> str:
    return (
        "📖 <b>راهنمای ربات</b>\n\n"
        "• «خودم اینارو ببین» — پنل شخصی: بانک، خلاف، ماشین، دست، کازینو و تنظیمات\n"
        "• «بانک» — مشاهده حساب؛ برای واریز بنویس: <code>واریز 100m</code>\n"
        "• «برداشت 100m» — انتقال از بانک به جیب\n"
        "• «دارایی» — نمایش پول جیب و بانک\n"
        "• «خلاف» — عملیات خلاف در گروه\n"
        "• «ماشینم» — مشاهده ماشین‌ها و گاراژ\n"
        "• «دست» — مشاهده و خرید ارتقاهای کاهش زمان جق\n"
        "در گروه هم با نوشتن «جق» پس از پایان زمان انتظار، جایزه می‌گیری. زمان انتظار با ارتقای دست کمتر می‌شود."
    )


async def guide_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    guide = guide_text()
    await q.answer()
    await q.edit_message_text(B(guide), parse_mode="HTML", reply_markup=InlineKeyboardMarkup([[btn("🔙 برگشت", "primary", callback_data="guide:back")]]))

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
    bot_username = (await context.bot.get_me()).username or ""
    await update.message.reply_text(welcome_ok(), parse_mode="HTML", reply_markup=welcome_keyboard(bot_username))


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
    bot_username = (await context.bot.get_me()).username or ""
    await q.edit_message_text(welcome_ok(), parse_mode="HTML", reply_markup=welcome_keyboard(bot_username))


# ───────────────────────── بانک و املاک ─────────────────────────
BANK_EMOJIS = ("5445355530111437729", "5445353829304387411", "5443127283898405358")
BANK_ICON_ID = "5332455502917949981"
BANK_CE = "".join(f'<tg-emoji emoji-id="{eid}">🏦</tg-emoji>' for eid in BANK_EMOJIS)
BANK_FEE = 5_000

HOUSES = {
    "room": {"name": "اتاق اجاره‌ای", "price": 500_000, "income": 30_000},
    "small": {"name": "آپارتمان کوچک", "price": 2_000_000, "income": 100_000},
    "lux": {"name": "آپارتمان لوکس", "price": 8_000_000, "income": 350_000},
    "villa": {"name": "ویلای شخصی", "price": 20_000_000, "income": 800_000},
    "mansion": {"name": "عمارت سلطنتی", "price": 75_000_000, "income": 2_000_000},
}
# هزینه ارتقا عمداً سنگین است؛ هر سطح درآمد پایه ملک را ۳۰٪ افزایش می‌دهد.
HOUSE_UPGRADE_COSTS = (5_000_000, 15_000_000, 40_000_000, 100_000_000, 250_000_000)
HOUSE_MAX_LEVEL = len(HOUSE_UPGRADE_COSTS)

_FA_DIGITS_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
def parse_money_amount(raw: str) -> int | None:
    value = raw.translate(_FA_DIGITS_MAP).strip().lower().replace(",", "").replace("٬", "")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(m|میلیون|میلیارد|k|هزار)?", value)
    if not m:
        return None
    number = float(m.group(1)); unit = m.group(2) or ""
    multiplier = 1_000_000_000 if unit == "میلیارد" else 1_000_000 if unit in ("m", "میلیون") else 1_000 if unit in ("k", "هزار") else 1
    amount = int(number * multiplier)
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
            f"<b>دستورات بانک</b>\n<code>واریز 100m</code> — واریز ۱۰۰ میلیون\n"
            f"<code>برداشت 100m</code> — برداشت ۱۰۰ میلیون\n\n"
            f"کارمزد هر واریز: <b>{BANK_FEE:,} تومان</b>\nکارمزد برداشت: <b>۰ تومان</b>")
    await update.effective_message.reply_text(B(text), parse_mode="HTML")

async def handle_bank_transfer(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    user=update.effective_user; key=_word_key(text)
    m=re.match(r"^(واریز|واريز|برداشت)\s+(.+)$", text.strip())
    if not m:
        return False
    action=m.group(1); amount=parse_money_amount(m.group(2))
    if amount is None:
        await update.effective_message.reply_text(B("مبلغ معتبر نیست. نمونه: <code>واریز 100m</code> یا <code>برداشت 100m</code>"),parse_mode="HTML"); return True
    await upsert_user(user.id,user.full_name)
    async with pool.acquire() as conn:
        async with conn.transaction():
            row=await conn.fetchrow("SELECT balance, bank_balance FROM users WHERE user_id=$1 FOR UPDATE",user.id)
            pocket,bank=int(row['balance']),int(row['bank_balance'])
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
    return True

async def show_homes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user=update.effective_user
    await upsert_user(user.id,user.full_name)
    rows=await pool.fetch("SELECT house_type, upgrade_level, last_claim FROM home_assets WHERE user_id=$1 ORDER BY asset_id",user.id)
    lines=[f"🏠 <b>املاک و مستغلات</b>","خونه بخر، ارتقاش بده و هر ۲۴ ساعت درآمد جمع کن.",""]
    buttons=[]
    now=time.time(); total_income=0
    for r in rows:
        cfg=HOUSES.get(r['house_type'])
        if not cfg: continue
        level=int(r['upgrade_level']); daily=int(cfg['income']*(1+0.30*level)); total_income+=daily
        accrued=max(0,min(7*86400,now-float(r['last_claim'] or now)))
        pending=int(daily*accrued/86400)
        lines.append(f"🏡 <b>{cfg['name']}</b> | سطح ارتقا: <b>{level}/{HOUSE_MAX_LEVEL}</b>\nدرآمد روزانه: <b>{daily:,}</b> | درآمد ذخیره‌شده: <b>{pending:,}</b> تومان")
        buttons.append([btn(f"ارتقای {cfg['name']} | {HOUSE_UPGRADE_COSTS[level]:,}" if level < HOUSE_MAX_LEVEL else f"{cfg['name']} (حداکثر ارتقا)","primary",icon=EID_TROPHY,callback_data=f"hm:up:{user.id}:{r['house_type']}")])
    if rows:
        buttons.append([btn("دریافت درآمد املاک","success",icon=EID_MONEY,callback_data=f"hm:claim:{user.id}:all")])
    lines.append(f"\n{MONEY_CE} درآمد روزانه کل: <b>{total_income:,} تومان</b>")
    lines.append("\n<b>فروشگاه خرید ملک</b>")
    for key,cfg in HOUSES.items():
        owned=any(r['house_type']==key for r in rows)
        lines.append(f"• {cfg['name']}: <b>{cfg['price']:,} تومان</b> — درآمد پایه روزانه {cfg['income']:,}" + (" (خریداری شده)" if owned else ""))
        if not owned: buttons.append([btn(f"خرید {cfg['name']} | {cfg['price']:,}","primary",icon=EID_MONEY,callback_data=f"hm:buy:{user.id}:{key}")])
    await update.effective_message.reply_text(B("\n".join(lines)),parse_mode="HTML",reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)

async def home_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; parts=q.data.split(":")
    if len(parts)!=4: await q.answer("درخواست نامعتبر است.",show_alert=True); return
    _,action,uid_s,key=parts
    try: uid=int(uid_s)
    except ValueError: await q.answer("درخواست نامعتبر است.",show_alert=True); return
    if q.from_user.id!=uid: await q.answer("این پنل املاک برای شما نیست.",show_alert=True); return
    now=time.time(); message=""; ok=False
    async with pool.acquire() as conn:
        async with conn.transaction():
            row=await conn.fetchrow("SELECT balance FROM users WHERE user_id=$1 FOR UPDATE",uid)
            if action=="buy" and key in HOUSES:
                cfg=HOUSES[key]; owned=await conn.fetchval("SELECT 1 FROM home_assets WHERE user_id=$1 AND house_type=$2",uid,key)
                if owned: message="این ملک رو قبلاً خریدی."
                elif row['balance']<cfg['price']: message=f"برای خرید {cfg['name']} به {cfg['price']:,} تومان نیاز داری."
                else:
                    await conn.execute("UPDATE users SET balance=balance-$1 WHERE user_id=$2",cfg['price'],uid)
                    await conn.execute("INSERT INTO home_assets(user_id,house_type,last_claim) VALUES($1,$2,$3)",uid,key,now)
                    message=f"ملک {cfg['name']} با موفقیت به نامت ثبت شد!"; ok=True
            elif action=="up" and key in HOUSES:
                asset=await conn.fetchrow("SELECT upgrade_level FROM home_assets WHERE user_id=$1 AND house_type=$2 FOR UPDATE",uid,key)
                if not asset: message="این ملک رو نداری."
                elif asset['upgrade_level']>=HOUSE_MAX_LEVEL: message="این ملک به آخرین سطح ارتقا رسیده."
                else:
                    level=int(asset['upgrade_level']); cost=HOUSE_UPGRADE_COSTS[level]
                    if row['balance']<cost: message=f"برای ارتقای بعدی {cost:,} تومان لازم داری؛ موجودی جیبت کافى نیست."
                    else:
                        await conn.execute("UPDATE users SET balance=balance-$1 WHERE user_id=$2",cost,uid)
                        await conn.execute("UPDATE home_assets SET upgrade_level=upgrade_level+1 WHERE user_id=$1 AND house_type=$2",uid,key)
                        message=f"ارتقای ملک انجام شد! سطح جدید: {level+1}/{HOUSE_MAX_LEVEL}؛ درآمد روزانه ۳۰٪ بیشتر شد."; ok=True
            elif action=="claim" and key=="all":
                assets=await conn.fetch("SELECT asset_id,house_type,upgrade_level,last_claim FROM home_assets WHERE user_id=$1 FOR UPDATE",uid)
                payout=0
                for a in assets:
                    cfg=HOUSES[a['house_type']]; daily=int(cfg['income']*(1+0.30*int(a['upgrade_level'])))
                    elapsed=max(0,min(7*86400,now-float(a['last_claim'] or now)))
                    earned=int(daily*elapsed/86400); payout+=earned
                    if earned>0: await conn.execute("UPDATE home_assets SET last_claim=$1 WHERE asset_id=$2",now,a['asset_id'])
                if payout>0:
                    await conn.execute("UPDATE users SET balance=balance+$1 WHERE user_id=$2",payout,uid)
                    message=f"درآمد املاکت دریافت شد: +{payout:,} تومان!"; ok=True
                else: message="هنوز درآمدی برای دریافت جمع نشده؛ درآمد هر ملک به مرور و تا سقف ۷ روز ذخیره می‌شه."
            else: message="درخواست نامعتبر است."
    await q.answer("انجام شد!" if ok else message[:180],show_alert=not ok)
    # پنل را دوباره بساز تا موجودی، سطح و درآمد تازه دیده شود.
    await q.message.reply_text(B(message),parse_mode="HTML")
    class _Proxy: pass
    proxy=_Proxy(); proxy.effective_user=q.from_user; proxy.effective_message=q.message
    await show_homes(proxy,context)


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


def self_panel_keyboard(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [btn("بانک", "primary", icon=BANK_ICON_ID, callback_data=f"sp:bank:{uid}"), btn("خلاف", "danger", icon=EID_SWORDS, callback_data=f"sp:robbery:{uid}")],
        [btn("ماشینم", "primary", icon=EID_TROPHY, callback_data=f"sp:cars:{uid}"), btn("دست", "success", icon=EID_HAND, callback_data=f"sp:hand:{uid}")],
        [btn("کازینو", "primary", icon=DICE_EID, callback_data=f"sp:casino:{uid}"), btn("⚙️ تنظیمات", None, callback_data=f"sp:settings:{uid}")],
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
    pocket = int(row["balance"] or 0); bank = int(row["bank_balance"] or 0)
    level = int(row.get("hand_level", 0) or 0)
    cooldown = COOLDOWN_SECONDS - level * 5
    return (f"👤 <b>پنل شخصی {html.escape(name or row['name'] or 'کاربر')}</b>\n\n"
            f"{BANK_CE} بانک: <b>{bank:,} تومان</b>\n"
            f"{MONEY_CE} پول داخل جیب: <b>{pocket:,} تومان</b>\n"
            f"🚘 ماشین‌ها: برای مشاهده وارد بخش ماشینم شو\n"
            f"{HAND_CE} سطح دست: <b>{level}/5</b>\n"
            f"{HG_CE} زمان فعلی جق: <b>{_duration_fa(cooldown)}</b>\n\n"
            "یکی از بخش‌ها رو انتخاب کن:")


async def show_self_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await upsert_user(user.id, user.full_name)
    await update.effective_message.reply_text(B(await self_panel_text(user.id, user.full_name)), parse_mode="HTML", reply_markup=self_panel_keyboard(user.id))


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
    if action == "transfer" and q.message.chat.type != "private":
        await q.answer("انتقال اکانت فقط تو پی‌وی ربات ممکنه؛ بیا پی‌وی و بنویس «خودم».", show_alert=True); return
    await q.answer()
    back = [[btn("🔙 برگشت به پنل شخصی", "primary", icon=EID_BACK, callback_data=f"sp:home:{uid}")]]
    back_settings = [[btn("🔙 برگشت به تنظیمات", "primary", icon=EID_BACK, callback_data=f"sp:settings:{uid}")]]
    if action != "transfer":
        context.user_data.pop("acc_transfer", None)
    if action == "home":
        await q.edit_message_text(B(await self_panel_text(uid, q.from_user.full_name)), parse_mode="HTML", reply_markup=self_panel_keyboard(uid)); return
    if action == "bank":
        row = await get_user(uid); pocket=int(row['balance'] or 0); bank=int(row['bank_balance'] or 0)
        text=(f"{BANK_CE} <b>بانک مرکزی</b>\n\nموجودی بانک: <b>{bank:,} تومان</b>\nپول جیب: <b>{pocket:,} تومان</b>\nمجموع دارایی: <b>{bank+pocket:,} تومان</b>\n\n"
              f"واریز: <code>واریز 100m</code>\nبرداشت: <code>برداشت 100m</code>\nکارمزد هر واریز: <b>۵٬۰۰۰ تومان</b>؛ برداشت بدون کارمزد.")
        await q.edit_message_text(B(text), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(back)); return
    if action == "robbery":
        await pool.execute("INSERT INTO robbery_profile(user_id) VALUES($1) ON CONFLICT DO NOTHING", uid)
        profile=await pool.fetchrow("SELECT trace,cooldown_until,arrest_until,trace_until FROM robbery_profile WHERE user_id=$1",uid)
        now=time.time(); text=(f"{SWORDS_CE} <b>بازار خلاف</b>\n\nرد خلاف: <b>{profile['trace']}٪</b>\n")
        if profile['cooldown_until']>now: text += f"زمان تا عملیات بعدی: <b>{_duration_fa(profile['cooldown_until']-now)}</b>\n"
        if profile['arrest_until']>now: text += f"بازداشت: <b>{_duration_fa(profile['arrest_until']-now)}</b>\n"
        if profile['trace_until']>now: text += f"ردت لو رفته: <b>{_duration_fa(profile['trace_until']-now)}</b>\n"
        rows=robbery_keyboard(uid, profile['trace_until']>now).inline_keyboard
        await q.edit_message_text(B(text+"\nیکی از عملیات‌ها رو انتخاب کن:"),parse_mode="HTML",reply_markup=InlineKeyboardMarkup(rows)); return
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
        await q.edit_message_text(B(text),parse_mode="HTML",reply_markup=InlineKeyboardMarkup(car_rows+back)); return
    if action == "hand":
        row=await get_user(uid); level=int(row.get('hand_level',0) or 0); cooldown=COOLDOWN_SECONDS-level*5
        lines=[f"{HAND_CE} <b>ارتقای دست</b>","\nبا هر ارتقا، ۵ ثانیه از زمان انتظار جق کم می‌شه.",f"سطح فعلی: <b>{level}/5</b>",f"زمان فعلی: <b>{_duration_fa(cooldown)}</b>","\n<b>فروشگاه ارتقاها</b>"]
        rows=[]
        for i,(cost,reduce) in enumerate(HAND_UPGRADES,1):
            status="✅ خریداری شده" if level>=i else (f"{cost:,} تومان" if level==i-1 else "🔒 ابتدا سطح قبلی")
            lines.append(f"{i}. ارتقای {i}: <b>{status}</b>")
            if level==i-1: rows.append([btn(f"ارتقای {i} • {cost:,} تومان", "success", icon=EID_HAND, callback_data=f"sp:upgrade:{uid}:{i}")])
        rows += back
        lines.append(f"\n{HAND_DONE_CE} زمان نهایی پس از ۵ ارتقا: <b>۴ دقیقه و ۴۰ ثانیه</b>")
        await q.edit_message_text(B("\n".join(lines)),parse_mode="HTML",reply_markup=InlineKeyboardMarkup(rows)); return
    if action == "casino":
        text, kb = panel_main(uid)
        await edit_html(q, text, InlineKeyboardMarkup(list(kb.inline_keyboard) + back)); return
    if action == "settings":
        await q.edit_message_text(B("⚙️ تنظیمات\n\nیکی از گزینه‌ها رو انتخاب کن:"), parse_mode="HTML", reply_markup=settings_keyboard(uid)); return
    if action == "gender":
        rows = list(gender_keyboard(uid).inline_keyboard) + back_settings
        await q.edit_message_text(BP("جنسیتت رو انتخاب کن تا کاراکترت عوض بشه 👇"), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(rows)); return
    if action == "guide":
        await q.edit_message_text(B(guide_text()), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(back_settings)); return
    if action == "transfer":
        context.user_data["acc_transfer"] = {"ts": time.time()}
        await q.edit_message_text(
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
    extra = await pool.fetchval("SELECT (SELECT count(*) FROM cars WHERE user_id=$1) + (SELECT count(*) FROM home_assets WHERE user_id=$1)", dst)
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
            if await conn.fetchval("SELECT (SELECT count(*) FROM cars WHERE user_id=$1) + (SELECT count(*) FROM home_assets WHERE user_id=$1)", dst):
                return "اکانت مقصد دیگه خالی نیست."
            await conn.execute(
                "UPDATE users SET balance=$2, bank_balance=$3, jaghs=$4, hand_level=$5, work_count=$6, gender=COALESCE($7, gender) WHERE user_id=$1",
                dst, int(s_row["balance"] or 0), int(s_row["bank_balance"] or 0), int(s_row["jaghs"] or 0),
                int(s_row["hand_level"] or 0), int(s_row["work_count"] or 0), s_row["gender"],
            )
            await conn.execute("UPDATE users SET balance=0, bank_balance=0, jaghs=0, hand_level=0, work_count=0, gender=NULL WHERE user_id=$1", src)
            await conn.execute("UPDATE cars SET user_id=$2 WHERE user_id=$1", src, dst)
            await conn.execute("UPDATE home_assets SET user_id=$2 WHERE user_id=$1", src, dst)
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


# ───────────────────────── تسک‌های روزانه و هفتگی ─────────────────────────
TASK_EMOJI_ID = "5221951509129872023"
TASK_DONE_EMOJI_ID = "5256156224948284902"
TASK_ICON = f'<tg-emoji emoji-id="{TASK_EMOJI_ID}">📋</tg-emoji>'
TASK_DONE_ICON = f'<tg-emoji emoji-id="{TASK_DONE_EMOJI_ID}">✅</tg-emoji>'


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


async def show_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, user = update.effective_message, update.effective_user
    await upsert_user(user.id, user.full_name)
    rows = await pool.fetch("SELECT task_id,period,title FROM tasks WHERE active=TRUE ORDER BY CASE period WHEN 'daily' THEN 0 ELSE 1 END, task_id")
    daily_left, weekly_left = task_countdown()
    lines = [f"{TASK_ICON} <b>تسک‌های روزانه و هفتگی</b>", f"⏳ تا پایان روز: {daily_left}", f"⏳ تا پایان هفته: {weekly_left}", ""]
    buttons = []
    for r in rows:
        period = r["period"]
        key = task_period_key(period)
        done = await pool.fetchval("SELECT completed FROM task_completions WHERE task_id=$1 AND user_id=$2 AND period_key=$3", r["task_id"], user.id, key)
        mark = TASK_DONE_ICON if done else TASK_ICON
        label = ("روزانه" if period == "daily" else "هفتگی")
        lines.append(f"{mark} <b>{label}:</b> {html.escape(r['title'])}")
        buttons.append([btn(("☑️ " if done else "⬜ ") + (r["title"][:45]), "success" if done else "primary", callback_data=f"tk:{user.id}:{r['task_id']}:{key}")])
    if not rows:
        lines.append("هنوز پشتیبانی تسکی اضافه نکرده.")
    if buttons:
        await msg.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons))
    else:
        await msg.reply_text("\n".join(lines), parse_mode="HTML")


async def task_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    _, uid_s, task_s, key = q.data.split(":")
    uid, task_id = int(uid_s), int(task_s)
    if q.from_user.id != uid:
        await q.answer("این تسک‌ها برای صاحب پیام هستند.", show_alert=True); return
    period = await pool.fetchval("SELECT period FROM tasks WHERE task_id=$1 AND active=TRUE", task_id)
    if not period or key != task_period_key(period):
        await q.answer("مهلت این تسک تموم شده یا تسک غیرفعاله.", show_alert=True); return
    await pool.execute("""INSERT INTO task_completions(task_id,user_id,period_key,completed) VALUES($1,$2,$3,TRUE)
        ON CONFLICT(task_id,user_id,period_key) DO UPDATE SET completed=NOT task_completions.completed""", task_id, uid, key)
    await q.answer("وضعیت تسک تغییر کرد.")
    await show_tasks_from_callback(q, uid)


async def show_tasks_from_callback(q, uid):
    rows = await pool.fetch("SELECT task_id,period,title FROM tasks WHERE active=TRUE ORDER BY CASE period WHEN 'daily' THEN 0 ELSE 1 END, task_id")
    daily_left, weekly_left = task_countdown()
    lines = [f"{TASK_ICON} <b>تسک‌های روزانه و هفتگی</b>", f"⏳ تا پایان روز: {daily_left}", f"⏳ تا پایان هفته: {weekly_left}", ""]
    buttons=[]
    for r in rows:
        key=task_period_key(r['period'])
        done=await pool.fetchval("SELECT completed FROM task_completions WHERE task_id=$1 AND user_id=$2 AND period_key=$3",r['task_id'],uid,key)
        lines.append(f"{TASK_DONE_ICON if done else TASK_ICON} <b>{'روزانه' if r['period']=='daily' else 'هفتگی'}:</b> {html.escape(r['title'])}")
        buttons.append([btn(("☑️ " if done else "⬜ ")+r['title'][:45],"success" if done else "primary",callback_data=f"tk:{uid}:{r['task_id']}:{key}")])
    await q.edit_message_text("\n".join(lines),parse_mode="HTML",reply_markup=InlineKeyboardMarkup(buttons) if buttons else None)


async def on_group_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not msg.text or not user or user.is_bot:
        return
    # حذف فاصله و کاراکترهای نامرئی (کیبوردهای فارسی گاهی نیم‌فاصله اضافه می‌کنن)
    text = "".join(ch for ch in msg.text if ch not in "\u200c\u200d\u200e\u200f\u200b\ufeff").strip()
    log.info("group text from %s: %r", user.id, msg.text)
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

    # آپدیت اتمیک: فقط اگه ۵ دقیقه گذشته باشه پول اضافه میشه
    updated = await pool.fetchrow(
        "UPDATE users SET balance = balance + $1::bigint, jaghs = jaghs + 1, last_ts = $2::double precision "
        "WHERE user_id = $3::bigint AND last_ts <= $2::double precision - $4::double precision "
        "RETURNING balance, jaghs",
        amount,
        now,
        user.id,
        float(user_cooldown),
    )
    if updated is None:
        row = await get_user(user.id)
        remaining = user_cooldown - (now - row["last_ts"])
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


async def send_card(bot, chat_id: int, row, reply_to: int | None = None):
    """کارت کاراکتر کاربر: عکس + کپشن (اسم، سطح، موجودی). اگه یه مرحله خطا بده میره سراغ مرحله‌ی بعد، ولی تا جایی که بشه عکس همراه کپشن میاد."""
    gender = row["gender"]
    job = current_job(gender, row["jaghs"], row["work_count"]) if row["gender"] else None
    lvl = JOBS[gender][job[0]][1] if job else level_for(row["jaghs"])
    text = card_text(row["name"] or "", gender, row["balance"], row["jaghs"], row["work_count"])
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
    if await account_transfer_input(update, context):
        return
    if msg and msg.text and context.user_data.get("await_jobs") and update.effective_user.id in ADMIN_IDS:
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
    elif msg and msg.text and await handle_bank_transfer(update, context, msg.text):
        return
    elif msg and msg.text and _word_key(msg.text) in WALLET_WORDS:
        await wallet(update, context)
    elif msg and msg.text and _word_key(msg.text) in CAR_WORDS:
        await show_garage(update, context)
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
CASINO_OPEN = False   # False = بخش کازینو بسته‌ست (برای باز کردن True کن)
CASINO_CLOSED_EID = "5938149692423541565"
CASINO_CLOSED_TEXT = "بخش کازینو به دستور پاول دورف فعلا بسته‌ست."
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
MINES_GEM_EID = "6170417452162225096"
MINES_CASHOUT_EID = "5381992813896412558"
MINES_BOMB_CE = f'<tg-emoji emoji-id="{MINES_BOMB_EID}">💣</tg-emoji>'
MINES_TILE_CE = f'<tg-emoji emoji-id="{MINES_TILE_EID}">▢</tg-emoji>'
MINES_GEM_CE = f'<tg-emoji emoji-id="{MINES_GEM_EID}">💠</tg-emoji>'
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
    ]])
    return text, kb


def panel_game(uid: int, game: str):
    if game == "dice":
        rules = (f"{DICE_CE} تاس\n\n۱ نفره: یه عدد ۱ تا ۶ حدس می‌زنی؛ درست بیاد {DICE_NET + 1} برابر پولت رو می‌گیری.\n"
                 f"۲ نفره: یه میز تو گروه باز می‌شه، نفر دوم میاد؛ هر کی تاس بالاتر بندازه کل پول رو می‌بره.")
    elif game == "mines":
        rules = (f"{MINES_BOMB_CE} مین — بازی تک‌نفره\n\n۲۱ خانه وجود داره و ۳ خانه بمبه. هر خانه امن ضریب رو بیشتر می‌کنه. با پیدا کردن بمب شرطت از دست میره؛ هر زمان خواستی برداشت کن تا شرط × ضریب به حسابت برگرده. ضریب نهایی در خانه امن هجدهم ۳٫۰۵× است.")
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
        profit = (bet * (1 + DICE_NET) if game == "dice" and v == pick else 0) if game == "dice" else payout
        record_line = await update_group_record(chat_id, game, user, profit)
        row = await get_user(user.id)
        await say(B(text + ("\n" + record_line if record_line else "") + f"\n{ce('wallet', 1, '💰')} دارایی: {row['balance']:,}"))
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
    """ضریب پلکانی تا ۳٫۰۵ برابر در خانه امن هجدهم؛ ۳ بمب در ۲۱ خانه."""
    if safe_count <= 0:
        return 1.0
    return round(1.0 + 2.05 * (safe_count / (MINES_CELLS - MINES_COUNT)), 2)


def mines_keyboard(uid: int, game_id: str, opened: set[int], bombs: set[int], finished=False, can_cash=True):
    rows = []
    for r in range(7):
        row = []
        for c in range(3):
            i = c * 7 + r
            if i in opened:
                label = "💣" if i in bombs else "💠"
                row.append(btn(label, "danger" if i in bombs else "success", icon=MINES_BOMB_EID if i in bombs else MINES_GEM_EID, callback_data=f"cz:mx:{uid}:{game_id}:{i}"))
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
    text = f"💠 بازی مین\nشرط: {bet:,} تومن\n۳ بمب بین ۲۱ خانه پنهانه. خانه‌های امن رو باز کن و هر وقت خواستی برداشت بزن.\nضریب فعلی: ۱×"
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
            rec = await update_group_record(st["chat_id"], "mines", q.from_user, 0)
            await q.answer("بمب! بازی تموم شد.", show_alert=True)
            await edit_html(q, B(f"{MINES_BOMB_CE} بمب پیدا کردی و باختی!\nشرط {st['bet']:,} تومن از دست رفت.\n{rec}\nمحل هر سه بمب مشخص شد."), kb)
            return
        st["opened"].add(idx)
        count = len(st["opened"]); mult = mines_multiplier(count)
        if count >= MINES_CELLS - MINES_COUNT:
            st["finished"] = True; _busy(context).discard(uid)
            payout = int(st["bet"] * 3.05)
            await _give(uid, payout)
            row = await get_user(uid)
            rec = await update_group_record(st["chat_id"], "mines", q.from_user, payout)
            await q.answer("همه خانه‌های امن باز شد!")
            await edit_html(q, B(f"🏆 همه ۱۸ خانه امن رو باز کردی!\nضریب نهایی: ۳٫۰۵×\nپرداخت: {payout:,} تومن\nموجودی: {row['balance']:,}\n{rec}\nمحل بمب‌ها مشخص شد."), mines_keyboard(uid, game_id, st["opened"], st["bombs"], True, False))
            return
        await q.answer(f"خانه امن! ضریب {mult:g}×")
        await edit_html(q, B(f"💠 مین\nشرط: {st['bet']:,} تومن\nخانه امن: {count} از ۱۸\nضریب برداشت: {mult:g}×\nمبلغ دریافتی: {int(st['bet']*mult):,} تومن"), mines_keyboard(uid, game_id, st["opened"], st["bombs"]))
    elif action == "mc":
        st["finished"] = True; _busy(context).discard(uid)
        mult = mines_multiplier(len(st["opened"]))
        payout = int(st["bet"] * mult)
        await _give(uid, payout)
        row = await get_user(uid)
        rec = await update_group_record(st["chat_id"], "mines", q.from_user, payout)
        await q.answer("برداشت انجام شد.")
        await edit_html(q, B(f"{MINES_CASHOUT_CE} برداشت موفق!\nخانه امن: {len(st['opened'])}\nضریب: {mult:g}×\nشرط: {st['bet']:,} تومن\nدریافتی: {payout:,} تومن\nموجودی: {row['balance']:,}\n{rec}\nمحل هر سه بمب مشخص شد."), mines_keyboard(uid, game_id, st["opened"], st["bombs"], True, False))


async def casino_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    bot = context.bot
    parts = q.data.split(":")
    act = parts[1]
    if act in ("mx", "mc"):  # بازی مینِ نیمه‌کاره می‌تونه تموم/برداشت بشه
        await mines_cb(update, context, parts)
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
        record_line = await update_group_record(chat_id, game, user, 0)
        result = "🤝 مساوی شد؛ پول هر دو نفر برگشت." + (f"\n{record_line}" if record_line else "")
    else:
        win_id, win_link = (creator, a) if s1 > s2 else (user.id, b)
        await _give(win_id, bet * 2)
        win_name = cname if win_id == creator else user.full_name
        rec_user = type("CasinoUser", (), {"id": win_id, "full_name": win_name})()
        record_line = await update_group_record(chat_id, game, rec_user, bet * 2)
        result = f"{TROPHY_CE} برنده: {win_link}\n{MONEY_CE} {bet * 2:,} تومن به حسابش رفت!" + (f"\n{record_line}" if record_line else "")
    await edit_html(q, f"<b>{GAME_FA[game]} — نتیجه\n\n{a}: {r1}\n{b}: {r2}\n\n{result}</b>")
    delete_later(bot, chat_id, [m1.message_id, m2.message_id])


async def casino_text(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    """متن‌های کازینو رو تشخیص میده. True = مصرف شد."""
    msg, user = update.effective_message, update.effective_user
    t = norm_word(text).translate(_DIG)
    if not CASINO_OPEN:
        if re.fullmatch(r"کازینو|casino|(?:میز\s*)?(?:تاس|اسلات|مین)(?:\s+.+)?|(?:تاس|اسلات)\s*میز\s+.+", t, re.I):
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
CAR_PAY_EVERY = 3600
CAR_COLOR_PRICE = 20_000_000
CARS = [
    ("پراید ۱۳۱", "Pride 131", "🚗", 800_000_000, 2_000_000),
    ("Toyota Supra MK5", "Toyota Supra MK5", "🏎️", 1_400_000_000, 3_800_000),
    ("BMW M4 Competition", "BMW M4 Competition", "🚘", 2_000_000_000, 5_800_000),
    ("Lamborghini Huracán", "Lamborghini Huracán", "🏎️", 3_500_000_000, 10_500_000),
    ("Bugatti Chiron", "Bugatti Chiron", "👑", 5_000_000_000, 16_000_000),
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

def _car_keyboard(uid: int, idx: int, row, balance: int):
    rows = [
        [btn("⬆️ ارتقای ماشین", "primary", callback_data=f"car:upmenu:{uid}:{idx}:0")],
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
        await send_html(bot, chat_id, B("🚗 برای دیدن یا خرید، یکی از ماشین‌ها رو انتخاب کن 👇"), reply_markup=kb)
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
        kb = InlineKeyboardMarkup([[btn(f"🛒 خرید ماشین ({fmt_toman(price)})", "success", callback_data=f"car:buy:{uid}:{idx}:0")], [btn("🔙 برگشت به گاراژ", callback_data=f"car:garage:{uid}:{idx}:0")]])
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


def car_panel_pre(idx: int, owned: dict, balance: int) -> str:
    name, _en, emo, _price, _base = CARS[idx]
    cname = CAR_COLORS[int(owned[idx]["color"])][0]
    head = B(f"🔑 ماشین شما: {emo} {html.escape(name)} · 🎨 {html.escape(cname)}")
    return head + "\n" + mono_table(("مشخصات", "مقدار"), car_panel_rows(idx, owned, balance))


def _panel_keyboard(uid: int, idx: int, owned: dict):
    rows = [
        [btn("💰 جمع کن", "success", callback_data=f"car:collect:{uid}:{idx}:0"),
         btn("🚗 گاراژ", "danger", callback_data=f"car:garage:{uid}:{idx}:0")],
        [btn("⬆️ ارتقای ماشین", "success", callback_data=f"car:upmenu:{uid}:{idx}:0"),
         btn("🎨 رنگ", "primary", callback_data=f"car:colors:{uid}:{idx}:0")],
    ]
    nxt = _next_car_idx(owned)
    if nxt < len(CARS):
        n = CARS[nxt]
        rows.append([btn(f"⬆️ ماشین بعدی: {n[2]} {n[0]} · {fmt_short(n[3])}", "primary", callback_data=f"car:buy:{uid}:{nxt}:0")])
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
        next_idx = next((i for i in range(len(CARS)) if i not in owned), len(CARS))
        if idx != next_idx:
            await q.answer("ماشین‌ها باید به ترتیب خریداری شوند؛ اول ماشین قبلی را بخر.", show_alert=True); return
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
            await q.answer("این قطعه به حداکثر سطح رسیده است.", show_alert=True); return
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
        await _show_upgrade_menu(q, uid, idx)
    elif act == "colors":
        colors = [[btn(f"{ico} {name}", "primary", callback_data=f"car:colorask:{uid}:{idx}:{c}")] for c, (name, ico, _rgb) in enumerate(CAR_COLORS)]
        colors.append([btn("🔙 برگشت", callback_data=f"car:refresh:{uid}:{idx}:0")])
        await q.answer()
        await _edit_cap(q, B(f"🎨 انتخاب رنگ ماشین\nهزینه هر رنگ: {fmt_toman(CAR_COLOR_PRICE)}\nرنگ موردنظر را انتخاب کن:"), InlineKeyboardMarkup(colors))
    elif act == "colorask":
        try: color = int(p[4])
        except (IndexError, ValueError): await q.answer(); return
        if color not in range(len(CAR_COLORS)): await q.answer(); return
        name = CAR_COLORS[color][0]
        cur = (await _owned_cars(uid)).get(idx)
        if cur and int(cur["color"]) == color:
            await q.answer(f"ماشینت همین الان {name} هست.", show_alert=True); return
        kb = InlineKeyboardMarkup([[btn("✅ تأیید و پرداخت ۲۰ میلیون", "success", callback_data=f"car:colorbuy:{uid}:{idx}:{color}")], [btn("❌ انصراف", "danger", callback_data=f"car:colors:{uid}:{idx}:0")]])
        await q.answer()
        await _edit_cap(q, B(f"🎨 تغییر رنگ به {name}\n💰 هزینه: {fmt_toman(CAR_COLOR_PRICE)}\nآیا خرید این رنگ را تأیید می‌کنی؟"), kb)
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
                async with pool.acquire() as c:
                    async with c.transaction():
                        moved = await c.fetchval("UPDATE cars SET last_pay=last_pay+$1,pending_profit=pending_profit+$2 WHERE user_id=$3 AND car_idx=$4 AND last_pay=$5 RETURNING 1", hours*CAR_PAY_EVERY, profit, r["user_id"], r["car_idx"], r["last_pay"])
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


def robbery_keyboard(uid: int, trace_available: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [btn("سرقت از کلاب | ۲۰۰ تا ۵۰۰ هزار", "primary", icon=EID_SWORDS, callback_data=f"rb:do:{uid}:club")],
        [btn("سرقت از سینما | ۵۰۰ تا ۸۵۰ هزار", "primary", icon=EID_TROPHY, callback_data=f"rb:do:{uid}:cinema")],
        [btn("سرقت از سوپری | ۸۵۰ هزار تا ۱ میلیون", "primary", icon=EID_MONEY, callback_data=f"rb:do:{uid}:shop")],
        [btn("سرقت از بانک مرکزی | ۱ تا ۱.۵ میلیون", "danger", icon=EID_SWORDS, callback_data=f"rb:do:{uid}:bank")],
    ]
    if trace_available:
        rows.append([btn(f"ردگم‌کن | {ROBBERY_TRACE_CLEAN_COST:,} تومان", "danger", icon=EID_SWORDS, callback_data=f"rb:trace:{uid}:club")])
    rows.append([btn("🔙 برگشت به پنل شخصی", "primary", icon=EID_BACK, callback_data=f"sp:home:{uid}")])
    return InlineKeyboardMarkup(rows)


async def show_robbery_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await upsert_user(user.id, user.full_name)
    await pool.execute("INSERT INTO robbery_profile(user_id) VALUES($1) ON CONFLICT DO NOTHING", user.id)
    profile = await pool.fetchrow("SELECT trace, cooldown_until, arrest_until, trace_until FROM robbery_profile WHERE user_id=$1", user.id)
    now = time.time()
    text = (f"{SWORDS_CE} <b>بازار خلاف</b>\n\nیکی از سرقت‌ها رو انتخاب کن؛ هر بار اقدام، ۱۲٪ به رد خلاف اضافه می‌کنه.\n"
            f"{HG_CE} رد خلاف: <b>{profile['trace']}٪</b> از ۱۰۰٪\n"
            "بعد از هر عملیات، تایمر مشترک همه‌ی سرقت‌ها فعال می‌شه. اگر دستگیر یا ردت کامل بشه، تا آزادی یا پاک‌کردن رد نمی‌تونی خلاف کنی.")
    if profile['cooldown_until'] > now:
        text += f"\n\n{HG_CE} زمان باقی‌مانده تا خلاف بعدی: <b>{_duration_fa(profile['cooldown_until']-now)}</b>"
    if profile['arrest_until'] > now:
        text += f"\n{f'<tg-emoji emoji-id="{CASINO_CLOSED_EID}">🔒</tg-emoji>'} بازداشت: <b>{_duration_fa(profile['arrest_until']-now)}</b>"
    if profile['trace_until'] > now:
        text += f"\n{f'<tg-emoji emoji-id="{CASINO_CLOSED_EID}">🔒</tg-emoji>'} ردت لو رفته؛ تا <b>{_duration_fa(profile['trace_until']-now)}</b> محرومی یا می‌تونی ردگم‌کن بخری."
    await update.effective_message.reply_text(B(text), parse_mode="HTML", reply_markup=robbery_keyboard(user.id, profile['trace_until'] > now))


async def robbery_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; parts = q.data.split(":")
    if len(parts) != 4:
        await q.answer("درخواست نامعتبره.", show_alert=True); return
    action, uid_s, kind = parts[1], parts[2], parts[3]
    try: owner = int(uid_s)
    except ValueError: await q.answer("درخواست نامعتبره.", show_alert=True); return
    if q.from_user.id != owner:
        await q.answer("این منوی خلاف برای شخص دیگه‌ایه!", show_alert=True); return
    cfg = ROBBERIES.get(kind)
    if not cfg: await q.answer("این سرقت وجود نداره.", show_alert=True); return
    if action == "menu":
        prof = await pool.fetchrow("SELECT trace_until FROM robbery_profile WHERE user_id=$1", owner)
        await q.answer(); await edit_html(q, B(f"{SWORDS_CE} <b>بازار خلاف</b>\nیکی از عملیات‌ها رو انتخاب کن:"), robbery_keyboard(owner, bool(prof and prof['trace_until'] > time.time()))); return

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
            elif action == "bail":
                if profile['arrest_until'] <= now:
                    await q.answer("در بازداشت نیستی.", show_alert=True); return
                if bal < cfg['bail']:
                    await q.answer(f"وثیقه {cfg['bail']:,} تومنه؛ موجودیت کافی نیست.", show_alert=True); return
                new_balance = bal - cfg['bail']
                await conn.execute("UPDATE users SET balance=balance-$1 WHERE user_id=$2", cfg['bail'], owner)
                await conn.execute("UPDATE robbery_profile SET arrest_until=0 WHERE user_id=$1", owner)
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
                success = random.random() < cfg['chance']
                new_trace = min(ROBBERY_TRACE_LIMIT, profile['trace'] + ROBBERY_TRACE_STEP)
                trace_until = now + ROBBERY_TRACE_LOCK if new_trace >= ROBBERY_TRACE_LIMIT else 0
                arrest_until = now + cfg['lock'] if not success else 0
                await conn.execute("UPDATE robbery_profile SET trace=$1, cooldown_until=$2, arrest_until=GREATEST(arrest_until,$3), trace_until=GREATEST(trace_until,$4) WHERE user_id=$5",
                                   new_trace, now+cfg['cooldown'], arrest_until, trace_until, owner)
                if success:
                    payout = random.randint(*cfg['payout']); new_balance = bal + payout
                    await conn.execute("UPDATE users SET balance=balance+$1 WHERE user_id=$2", payout, owner)
                else:
                    payout = 0; penalty = min(bal, int(bal * cfg['penalty'] / 100)); new_balance = bal - penalty
                    await conn.execute("UPDATE users SET balance=GREATEST(0,balance-$1) WHERE user_id=$2", penalty, owner)
            else:
                await q.answer("درخواست نامعتبره.", show_alert=True); return

    if action == "trace":
        await q.answer("ردگم‌کن خریدی؛ ۱۲٪ از ردت پاک شد.", show_alert=True)
        text = (f"{SWORDS_CE} <b>ردگم‌کن فعال شد!</b>\n\n{MONEY_CE} هزینه: <b>{ROBBERY_TRACE_CLEAN_COST:,} تومان</b>\n"
                f"رد خلاف: <b>{new_trace}٪</b>\nموجودی جدید: <b>{new_balance:,} تومان</b>\n{HG_CE} تایمر انتظار قبلی همچنان پابرجاست.")
        await edit_html(q, B(text), robbery_keyboard(owner)); return
    if action == "bail":
        await q.answer("وثیقه پرداخت شد؛ از بازداشت آزاد شدی!", show_alert=True)
        text = (f"{ce('lock',2,'🔓')} <b>آزادی با وثیقه</b>\nوثیقه پرداختی: <b>{cfg['bail']:,} تومان</b>\n"
                f"موجودی جدید: <b>{new_balance:,} تومان</b>\nرد خلاف و تایمر عمومی همچنان پابرجاست.")
        await edit_html(q, B(text), robbery_keyboard(owner)); return
    if success:
        result = (f"{TROPHY_CE} <b>عملیات با موفقیت انجام شد!</b>\n\nبا یک نقشه‌ی حساب‌شده از سرقت {cfg['name']} فرار کردی و غنیمت رو برداشتی!\n"
                  f"{MONEY_CE} غنیمت: <b>+{payout:,} تومان</b>\nموجودی جدید: <b>{new_balance:,} تومان</b>\n"
                  f"{HG_CE} قفل مشترک تمام خلاف‌ها: <b>{_duration_fa(cfg['cooldown'])}</b>\nرد خلاف: <b>{new_trace}٪</b>")
        if new_trace >= ROBBERY_TRACE_LIMIT:
            result += f"\n\n{f'<tg-emoji emoji-id="{CASINO_CLOSED_EID}">🔒</tg-emoji>'} ردت به ۱۰۰٪ رسید؛ ۲ ساعت محرومی یا ردگم‌کن ۱٫۳ میلیونی بخر و ۱۲٪ از رد رو پاک کن."
        await q.answer("عملیات موفق بود؛ جایزه به حسابت نشست.")
        await edit_html(q, B(result), robbery_keyboard(owner, new_trace >= ROBBERY_TRACE_LIMIT))
    else:
        result = (f"<tg-emoji emoji-id=\"{CASINO_CLOSED_EID}\">🔒</tg-emoji> <b>عملیات شکست خورد؛ دستگیر شدی!</b>\n\n<blockquote>{html.escape(cfg['fail'])}</blockquote>\n\n"
                  f"جریمه: <b>{cfg['penalty']}٪</b> موجودی ({penalty:,} تومان)\nموجودی باقی‌مانده: <b>{new_balance:,} تومان</b>\n"
                  f"مدت بازداشت: <b>{_duration_fa(cfg['lock'])}</b>\nوثیقه آزادی: <b>{cfg['bail']:,} تومان</b>\nرد خلاف: <b>{new_trace}٪</b>")
        rows = [[btn(f"پرداخت وثیقه ({cfg['bail']:,}) و آزادی", "success", icon=EID_MONEY, callback_data=f"rb:bail:{owner}:{kind}")]]
        if new_trace >= ROBBERY_TRACE_LIMIT:
            rows.append([btn(f"ردگم‌کن ({ROBBERY_TRACE_CLEAN_COST:,} تومان)", "danger", icon=EID_SWORDS, callback_data=f"rb:trace:{owner}:{kind}")])
            result += f"\n\nردت به ۱۰۰٪ رسیده؛ ۲ ساعت از همه‌ی خلاف‌ها محرومی یا با ردگم‌کن ۱٫۳ میلیونی، ۱۲٪ از رد رو پاک کن."
        rows.append([btn("بازگشت به خلاف", "primary", icon=EID_BACK, callback_data=f"rb:menu:{owner}:{kind}")])
        kb = InlineKeyboardMarkup(rows)
        await q.answer("گیر افتادی! پلیس دستگیرت کرد.", show_alert=True)
        await edit_html(q, B(result), kb)


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
    # عضویت اجباری: تا عضو کانال نشده، ربات هیچ کاری برای کاربر نمی‌کنه (پشتیبان‌ها معافن)
    if (user and not user.is_bot and user.id not in ADMIN_IDS and chat and chat.type != "channel"
            and (update.callback_query or update.effective_message)):
        cq = update.callback_query
        if cq and cq.data == "fj:check":
            ok = await is_channel_member(context.bot, user.id)
            if ok is not True:
                await cq.answer("❌ هنوز عضویتت در هر دو کانال تایید نشده! اول عضو شو و دوباره بزن.", show_alert=True)
            else:
                await cq.answer("✅ عضویتت تایید شد، حالا می‌تونی از ربات استفاده کنی.", show_alert=True)
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


def admin_menu_kb() -> InlineKeyboardMarkup:
    web_row = [[btn("📱 مینی‌اپ پنل پشتیبانی", "success", web_app=WebAppInfo(url=WEBAPP_URL))]] if WEBAPP_URL.startswith("https://") else []
    return InlineKeyboardMarkup(web_row + [
        [btn("💸 کم کردن پول", "primary", callback_data="ad:money"), btn("➕ افزودن پول", "success", callback_data="ad:money_add")],
        [btn("🤛 کم کردن جق", "primary", callback_data="ad:jagh"), btn("➕ افزودن جق", "success", callback_data="ad:jagh_add")],
        [btn("🚫 محروم کردن کاربر", "danger", callback_data="ad:ban"), btn("✅ رفع محرومیت", "success", callback_data="ad:unban")],
        [btn("📋 افزودن تسک روزانه", "primary", callback_data="ad:task_daily"), btn("📆 افزودن تسک هفتگی", "primary", callback_data="ad:task_weekly")],
        [btn("🔴 خاموش کردن ربات", "danger", callback_data="ad:toggle") if BOT_ENABLED
         else btn("🟢 روشن کردن ربات", "success", callback_data="ad:toggle")],
        [btn("📢 پیام همگانی", "primary", callback_data="ad:bc"), btn("📊 آمار", "primary", callback_data="ad:stats")],
        [btn("👁 پیام‌های گروه‌ها", "primary", callback_data="ad:gm")],
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
    elif act in ("money", "jagh", "money_add", "jagh_add"):
        context.user_data["adm"] = {"act": act, "step": "target"}
        what = "پول" if act.startswith("money") else "جق"
        operation = "اضافه کردن" if act.endswith("_add") else "کم کردن"
        await q.edit_message_text(
            f"{operation} {what}\n\nآیدی عددی یا @یوزرنیم کاربر رو بفرست، یا یه پیام ازش اینجا فوروارد کن.\n(لغو: بنویس «لغو»)",
            reply_markup=CANCEL_KB,
        )
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
        context.user_data["adm"] = {"act": "task_add", "period": period, "step": "title"}
        await q.edit_message_text("متن تسک را بفرست تا برای همه کاربران اضافه شود.\nبرای لغو بنویس «لغو»." , reply_markup=CANCEL_KB)
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
    if not st or not msg or user.id not in ADMIN_IDS:
        return False
    if msg.text and msg.text.strip() == "لغو":
        context.user_data.pop("adm", None)
        await msg.reply_text("لغو شد.", reply_markup=admin_menu_kb())
        return True
    act = st["act"]
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
                await msg.reply_text(f"✅ محرومیت {who} برداشته شد.", reply_markup=admin_menu_kb())
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
            await msg.reply_text(f"🚫 {st['who']} {_ban_until_txt(until)} محروم شد.", reply_markup=admin_menu_kb())
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
            unit = "تومن" if act.startswith("money") else "جق"
            await msg.reply_text(f"✅ {amt:,} {unit} {verb}. مقدار جدید: {new:,} {unit}", reply_markup=admin_menu_kb())
            return True
    if act == 'task_add' and st['step'] == 'title':
        title = (msg.text or '').strip()[:300]
        if not title:
            await msg.reply_text('متن تسک خالیه؛ دوباره بفرست.', reply_markup=CANCEL_KB); return True
        await pool.execute('INSERT INTO tasks(period,title) VALUES($1,$2)', st['period'], title)
        context.user_data.pop('adm', None)
        period_label = 'روزانه' if st['period'] == 'daily' else 'هفتگی'
        await msg.reply_text(f'✅ تسک {period_label} برای همه کاربران اضافه شد.', reply_markup=admin_menu_kb())
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


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(startup)
        .post_shutdown(close_db)
        .build()
    )
    app.add_handler(TypeHandler(Update, gate), group=-1)
    app.add_handler(CallbackQueryHandler(self_panel_cb, pattern=r"^sp:(home|bank|robbery|cars|hand|casino|settings|gender|guide|transfer):\d+$"))
    app.add_handler(CallbackQueryHandler(hand_upgrade_cb, pattern=r"^sp:upgrade:\d+:\d+$"))
    app.add_handler(CallbackQueryHandler(account_transfer_cb, pattern=r"^at:(go|ok|no):\d+:\d+$"))
    app.add_handler(CallbackQueryHandler(guide_callback, pattern=r"^guide:(open|back)$"))
    app.add_handler(CallbackQueryHandler(robbery_cb, pattern=r"^rb:"))
    app.add_handler(CallbackQueryHandler(home_cb, pattern=r"^hm:"))
    app.add_handler(CallbackQueryHandler(casino_cb, pattern=r"^cz:"))
    app.add_handler(CallbackQueryHandler(duel_answer, pattern=r"^du:(ok|no):\d+:\d+$"))
    app.add_handler(CommandHandler("admin", admin_cmd, filters=filters.ChatType.PRIVATE))
    app.add_handler(CallbackQueryHandler(admin_cb, pattern=r"^ad:"))
    app.add_handler(CallbackQueryHandler(task_cb, pattern=r"^tk:"))
    app.add_handler(CallbackQueryHandler(car_cb, pattern=r"^car:"))
    app.add_handler(CommandHandler("garage", show_garage))
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

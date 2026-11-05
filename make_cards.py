"""ساخت کارت کاراکترها از عکس‌های art/char_N.jpg  →  chars/char_N.png
اسم و لقب تو عکس پخته میشه؛ فقط عدد دارایی موقع ارسال تو ربات نوشته میشه. اجرا: python make_cards.py"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

W, H = 800, 1000
FONT = "fonts/DejaVuSans-Bold.ttf"
CARDS_M = [
    ("سرباز جقی", "تازه از پادگان اومده، هنوز دستش گرم نشده", (200, 170, 110)),
    ("دانشجو جقی", "جزوه‌ش خالیه ولی دستش همیشه پره", (130, 190, 240)),
    ("راننده تاکسی جقی", "کرایه نمی‌گیره، فقط می‌پیچونه", (250, 210, 80)),
    ("فروشنده جقی", "ساعت ۳ شبه و فقط یه مشتری داره", (210, 120, 240)),
    ("وکیل جقی", "پرونده‌ها رو ول کرده و خوابیده", (240, 190, 90)),
    ("فضانورد جقی", "تا مدار زمین رفته ولی دستش هنوز همونجاست", (120, 190, 250)),
    ("آقای رئیس خسته", "جلسه ۴ ساعته شد و هنوز ساعت یک ظهره", (170, 200, 150)),
    ("رئیس‌جمهور چرتی", "وسط جلسه خوابید، کسی بیدارش نمی‌کنه", (240, 100, 100)),
    ("رهبر خواب‌آلود", "از خستگیه یا از حوصله‌سررفتگی؟", (230, 90, 90)),
    ("میلیاردر بی‌خواب", "راکت داره، ربات داره، خواب نداره", (240, 200, 120)),
]


# خانوم‌ها. سطح ۹ فعلا عکس نداره (عکس قبلی رد شد) و با کارت متنی میاد تا عکس جایگزین بدی.
CARDS_F = [
    ("دختر دانشجو جقی", "جزوه‌ها رو باز کرده ولی فقط به یه چیز فکر می‌کنه", (240, 150, 120)),
    ("دختر بلاگر جقی", "لایو رو زده ولی فالوئرها تو ذهنشن، نه تو دوربین", (200, 130, 240)),
    ("دختر رقاص جقی", "روی صحنه می‌رقصه، پشت صحنه خسته‌ست", (140, 160, 250)),
    ("دختر ناخن‌کار جقی", "ناخن مشتری رو می‌کشه، حواسش جای دیگه‌ست", (230, 120, 170)),
    ("دختر دندان‌پزشک جقی", "ماسکش افتاده و کسی نمی‌پرسه چرا", (110, 200, 230)),
    ("دختر داروخونه‌ای جقی", "برای همه نسخه می‌پیچه، برای خودش نه", (130, 220, 190)),
    ("دختر وکیل جقی", "پرونده‌ها رو بسته و دلیلش رو فقط خودش می‌دونه", (240, 190, 90)),
    ("دختر فضانورد جقی", "تا مدار زمین رفته ولی دلش هنوز همینجاست", (120, 190, 250)),
    ("دختر رازآلود جقی", "منتظر عکس جدید", (200, 200, 200)),
    ("دختر میلیاردر جقی", "پول رو دور خودش ریخته و خوابش نمیاد", (240, 200, 120)),
]
CARDS = {"m": CARDS_M, "f": CARDS_F}

def fnt(s): return ImageFont.truetype(FONT, s, layout_engine=ImageFont.Layout.RAQM)
def rtl(d, xy, t, size, fill, max_w=None):
    """متن راست‌چین؛ اگه max_w بدی اندازه‌ی فونت اونقدر کم میشه که تو عرض جا بشه."""
    f = fnt(size)
    while max_w and f.getlength(t, direction="rtl", language="fa") > max_w and size > 14:
        size -= 2
        f = fnt(size)
    d.text(xy, t, font=f, fill=fill, anchor="ra", direction="rtl", language="fa")

def cover(im, w, h):
    r = max(w / im.width, h / im.height)
    im = im.resize((round(im.width * r), round(im.height * r)), Image.LANCZOS)
    x, y = (im.width - w) // 2, (im.height - h) // 3  # کمی بالاتر، برای صورت
    return im.crop((x, y, x + w, y + h))

def make(g, i, name, tag, accent):
    base = cover(Image.open(f"art/{g}/char_{i}.jpg").convert("RGB"), W, H).convert("RGBA")
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    for y in range(230):                       # سایه بالا
        od.line([(0, y), (W, y)], fill=(0, 0, 0, int(215 * (1 - y / 230))))
    for y in range(H - 190, H):                # سایه پایین
        od.line([(0, y), (W, y)], fill=(0, 0, 0, int(225 * (y - (H - 190)) / 190)))
    base = Image.alpha_composite(base, ov)
    d = ImageDraw.Draw(base)
    rtl(d, (765, 28), name, 58, (255, 255, 255), max_w=620)
    rtl(d, (765, 105), tag, 25, accent, max_w=700)
    d.text((40, 36), f"{i}/10", font=fnt(34), fill=(255, 255, 255), anchor="la")
    d.rounded_rectangle((30, 860, 770, 975), 26, fill=(10, 10, 16, 200), outline=accent, width=4)
    rtl(d, (740, 893), "دارایی کل", 38, (255, 215, 80))
    rtl(d, (200, 893), "تومن", 34, (255, 255, 255))
    base.convert("RGB").save(f"chars/{g}/char_{i}.png")


for g, cards in CARDS.items():
    for i, (name, tag, accent) in enumerate(cards, 1):
        if Path(f"art/{g}/char_{i}.jpg").exists():
            make(g, i, name, tag, accent)
print("done")

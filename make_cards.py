"""ساخت کارت کاراکترها از عکس‌های art/char_N.jpg  →  chars/char_N.png
اسم و لقب تو عکس پخته میشه؛ فقط عدد دارایی موقع ارسال تو ربات نوشته میشه. اجرا: python make_cards.py"""
from PIL import Image, ImageDraw, ImageFont

W, H = 800, 1000
FONT = "fonts/DejaVuSans-Bold.ttf"
CARDS = [
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

def fnt(s): return ImageFont.truetype(FONT, s, layout_engine=ImageFont.Layout.RAQM)
def rtl(d, xy, t, size, fill):
    d.text(xy, t, font=fnt(size), fill=fill, anchor="ra", direction="rtl", language="fa")

def cover(im, w, h):
    r = max(w / im.width, h / im.height)
    im = im.resize((round(im.width * r), round(im.height * r)), Image.LANCZOS)
    x, y = (im.width - w) // 2, (im.height - h) // 3  # کمی بالاتر، برای صورت
    return im.crop((x, y, x + w, y + h))

for i, (name, tag, accent) in enumerate(CARDS, 1):
    base = cover(Image.open(f"art/char_{i}.jpg").convert("RGB"), W, H).convert("RGBA")
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    for y in range(230):                       # سایه بالا
        od.line([(0, y), (W, y)], fill=(0, 0, 0, int(215 * (1 - y / 230))))
    for y in range(H - 190, H):                # سایه پایین
        od.line([(0, y), (W, y)], fill=(0, 0, 0, int(225 * (y - (H - 190)) / 190)))
    base = Image.alpha_composite(base, ov)
    d = ImageDraw.Draw(base)
    rtl(d, (765, 28), name, 58, (255, 255, 255))
    rtl(d, (765, 105), tag, 25, accent)
    d.text((40, 36), f"{i}/10", font=fnt(34), fill=(255, 255, 255), anchor="la")
    d.rounded_rectangle((30, 860, 770, 975), 26, fill=(10, 10, 16, 200), outline=accent, width=4)
    rtl(d, (740, 893), "دارایی کل", 38, (255, 215, 80))
    rtl(d, (200, 893), "تومن", 34, (255, 255, 255))
    base.convert("RGB").save(f"chars/char_{i}.png")
print("done")

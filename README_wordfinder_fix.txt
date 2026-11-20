رفع خطای «این میز دیگر فعال نیست» در کلمه‌یاب

1) فایل bot.py این بسته را جایگزین فایل bot.py پروژه کن.
2) فایل wordfinder.jpg را کنار bot.py نگه دار.
3) این نسخه فقط خطای مسیر دکمه «ساخت میز» را اصلاح می‌کند؛ به requirements.txt و متغیرهای دیگر دست نزن.

یوزربات پشتیبانی داخل همین bot.py مستقل است و برای فعال شدن باید:
- پکیج Telethon به requirements.txt فعلی پروژه اضافه شود (فقط یک خط: Telethon)
- در Railway Variables این سه متغیر وجود داشته باشند:
  TELEGRAM_API_ID
  TELEGRAM_API_HASH
  USERBOT_SESSION

USERBOT_SESSION باید StringSession معتبرِ همان اکانت تلگرام باشد. این مقادیر را در چت نفرست.

در لاگ Railway اگر پیام Support userbot disabled دیدی، یکی از سه متغیر بالا خالی است.
اگر پیام No module named telethon دیدی، Telethon نصب نشده است.
اگر session is not authorized دیدی، نشست معتبر نیست و باید StringSession تازه ساخته شود.
اگر پیام does not have both panel and miniapp permissions دیدی، شناسه همان اکانت باید در ADMIN_IDS باشد تا مجوزهای لازم را داشته باشد.

توجه: این یوزرباتِ پشتیبانی داخل ربات اصلی است؛ پروژه جداگانه tg-userbot که برای کنترل چت‌ها/کلیک دکمه‌ها ساخته شده، برنامه و تنظیمات مستقل خودش را دارد.

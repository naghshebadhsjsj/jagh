# Telegram bot - car garage update

Deploy with the existing Railway service and PostgreSQL database. Required environment variables already used by the bot must remain configured (at minimum `BOT_TOKEN` and `DATABASE_URL`; preserve the rest of the existing bot variables such as `ADMIN_IDS`).

Start command: `python bot.py`

The bot applies backward-compatible PostgreSQL migrations at startup. Put all five car photos in `cars/` exactly as packaged. Do not remove the `cars` directory.

Features: user car shortcut (`ماشین` / `ماشینم`), garage (`گاراژ`), manual earnings withdrawal, paid paint colors (20 million toman per change), and independent upgrades for tires, body, glass, computer, engine, and turbo.

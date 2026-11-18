"""Generate a Telethon StringSession for the support account.
Run locally on a trusted device; never share the printed session string.
"""
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession

async def main():
    api_id = int(input('TELEGRAM_API_ID: ').strip())
    api_hash = input('TELEGRAM_API_HASH: ').strip()
    phone = input('Phone number with country code: ').strip()
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.start(phone=phone)
    me = await client.get_me()
    print('\nLogged in as:', me.id, getattr(me, 'username', None))
    print('\nUSERBOT_SESSION (keep secret; do not send it to anyone):\n')
    print(client.session.save())
    await client.disconnect()

asyncio.run(main())

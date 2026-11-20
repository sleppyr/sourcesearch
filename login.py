"""Вход аккаунтов-проверяльщиков.
  python login.py              — войти во все SESSIONS из .env (файлы *.session)
  python login.py имя          — войти в одну сессию
  python login.py --string     — получить СТРОКУ сессии для Railway (переменная STRING_SESSIONS)
Нужен НОМЕР ТЕЛЕФОНА обычного аккаунта (не токен бота)."""
import asyncio
import sys

from telethon.sessions import StringSession

import config as cfg
from pool import login_interactive, parse_session


async def main():
    args = sys.argv[1:]
    if "--string" in args:
        c = await login_interactive("string", cfg.API_ID, cfg.API_HASH, session=StringSession())
        print("\nСТРОКА СЕССИИ (храни в секрете, это доступ к аккаунту):\n")
        print(c.session.save())
        print("\nВставь её в Railway: Variables → STRING_SESSIONS")
        await c.disconnect()
        return
    for spec in args or cfg.SESSIONS:
        name, aid, ah = parse_session(spec)
        c = await login_interactive(name, aid, ah)
        await c.disconnect()
    print("Готово. Теперь запускайте: python bot.py")


asyncio.run(main())

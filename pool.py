"""Пул воркеров Telethon: быстрая параллельная проверка занятости юзернеймов.

Двухступенчатая проверка:
  1) contacts.resolveUsername — дёшево и быстро, работает и с ботами. Если ник кем-то занят — сразу «занят».
  2) account.checkUsername   — точное подтверждение «свободен» (только обычные аккаунты).
Каждый дополнительный аккаунт или бот-токен увеличивает скорость: запросы идут параллельно.
"""
import asyncio
import getpass
import logging
import sys
import time

from telethon import TelegramClient, errors, functions
from telethon.sessions import StringSession

import config as cfg

log = logging.getLogger("pool")
_PURCHASE = getattr(errors, "UsernamePurchaseAvailableError", ())
TAKEN_TTL = 3600
FREE_TTL = 20


def parse_session(spec: str):
    p = spec.split(":")
    if len(p) == 3:
        return p[0], int(p[1]), p[2]
    return p[0], cfg.API_ID, cfg.API_HASH


async def login_interactive(name: str, api_id: int, api_hash: str, session=None) -> TelegramClient | None:
    """Вход под ОБЫЧНЫМ аккаунтом по номеру телефона. Токен бота здесь вводить нельзя."""
    c = TelegramClient(session if session is not None else name, api_id, api_hash, flood_sleep_threshold=0)
    await c.connect()
    if await c.is_user_authorized() and (await c.get_me()).bot:
        print(f"[{name}] сессия принадлежит боту, выполняю выход и вхожу заново")
        await c.log_out()
        await c.connect()
    if not await c.is_user_authorized():
        while True:
            phone = input(f"[{name}] номер телефона аккаунта (+79991234567): ").strip()
            if ":" in phone or not phone.lstrip("+").isdigit():
                print("Нужен именно НОМЕР ТЕЛЕФОНА, а не токен бота.")
                continue
            break
        await c.send_code_request(phone)
        try:
            await c.sign_in(phone, input("Код из Telegram: ").strip())
        except errors.SessionPasswordNeededError:
            await c.sign_in(password=getpass.getpass("Пароль 2FA: "))
    me = await c.get_me()
    print(f"[{name}] вход выполнен: {me.first_name} (id {me.id})")
    return c


class Worker:
    def __init__(self, name: str, client: TelegramClient, kind: str):
        self.name, self.client, self.kind = name, client, kind
        self.lock = asyncio.Semaphore(6 if kind == "bot" else 2)   # параллельных запросов на воркер
        self.cool = 0.0
        self.pending = 0
        self.ok = 0
        # у каждого воркера свой темп: FloodWait считается на аккаунт/бота,
        # поэтому скорость пула = сумма скоростей воркеров (больше воркеров — быстрее)
        mult = 1.5 if kind == "user" else 1.0
        self.base = {"resolve": cfg.RESOLVE_PACE * mult, "confirm": cfg.CONFIRM_PACE}
        self.pace = dict(self.base)
        self.next_at = {"resolve": 0.0, "confirm": 0.0}


class Pool:
    def __init__(self):
        self.workers: list[Worker] = []
        self.taken: dict[str, float] = {}
        self.fresh: dict[str, tuple[str, float]] = {}
        self.sem = asyncio.Semaphore(96)
        self.total = 0

    # ───────── запуск ─────────
    async def start(self):
        for i, s in enumerate(cfg.STRING_SESSIONS):
            await self._add_user(f"str{i}", StringSession(s), cfg.API_ID, cfg.API_HASH)
        for spec in cfg.SESSIONS if not cfg.STRING_SESSIONS else []:
            name, aid, ah = parse_session(spec)
            await self._add_user(name, name, aid, ah)
        if not self.users and cfg.SESSIONS and sys.stdin.isatty():
            name, aid, ah = parse_session(cfg.SESSIONS[0])
            c = await login_interactive(name, aid, ah)
            if c:
                self.workers.append(Worker(name, c, "user"))
        tokens = ([cfg.BOT_TOKEN] if cfg.USE_MAIN_BOT_AS_RESOLVER and cfg.BOT_TOKEN else []) + cfg.RESOLVER_BOTS
        for t in tokens:
            try:
                c = TelegramClient(StringSession(), cfg.API_ID, cfg.API_HASH, flood_sleep_threshold=0)
                await c.start(bot_token=t)
                self.workers.append(Worker(f"bot{t.split(':')[0]}", c, "bot"))
            except Exception as e:
                log.warning("резолвер-бот не запущен: %s", e)
        if not self.workers:
            raise SystemExit("Нет ни одного рабочего аккаунта. Выполните: python login.py")
        if not self.users:
            log.warning("Нет пользовательских сессий: найденные ники не подтверждаются точной проверкой. "
                        "Выполните python login.py")
        log.info("Пул запущен: %d аккаунт(ов), %d бот(ов)", len(self.users), len(self.workers) - len(self.users))

    async def _add_user(self, name: str, session, aid: int, ah: str):
        try:
            c = TelegramClient(session, aid, ah, flood_sleep_threshold=0)
            await c.connect()
            if not await c.is_user_authorized():
                log.warning("сессия %s не авторизована (python login.py %s)", name, name)
                await c.disconnect()
                return
            if (await c.get_me()).bot:
                log.warning("сессия %s принадлежит боту — удалите %s.session и войдите заново", name, name)
                await c.disconnect()
                return
            self.workers.append(Worker(name, c, "user"))
        except Exception as e:
            log.warning("сессия %s: %s", name, e)

    @property
    def users(self):
        return [w for w in self.workers if w.kind == "user"]

    # ───────── низкий уровень ─────────
    async def _call(self, kinds, factory, gate: str):
        """Запрос на воркере, который освободится раньше всех (с учётом его личного темпа).
        -> ('ok', result) | ('err', exc) | ('flood', None)"""
        for _ in range(8):
            cands = [w for w in self.workers if w.kind in kinds and w.cool <= time.time()]
            if not cands:
                soonest = min((w.cool for w in self.workers if w.kind in kinds), default=time.time())
                wait = soonest - time.time()
                if wait > 20:                       # все на длинной паузе — не висим, сообщаем о лимите
                    return "flood", None
                await asyncio.sleep(max(wait, 0.1))
                continue
            now = time.monotonic()
            w = min(cands, key=lambda x: max(now, x.next_at[gate]))
            slot = max(now, w.next_at[gate])          # бронируем слот сразу, без await между выбором и бронью
            w.next_at[gate] = slot + w.pace[gate]
            if slot > now:
                await asyncio.sleep(slot - now)
            w.pending += 1
            try:
                async with w.lock:
                    try:
                        r = await w.client(factory())
                        w.ok += 1
                        w.pace[gate] = max(w.base[gate], w.pace[gate] * 0.98)   # плавно возвращаемся к базовому темпу
                        return "ok", r
                    except errors.FloodWaitError as e:
                        w.cool = time.time() + e.seconds + 1
                        w.pace[gate] = min(w.pace[gate] * 1.5, 10.0)             # при флуде замедляемся
                        w.next_at[gate] = time.monotonic() + e.seconds + 1
                        log.warning("FloodWait %ss на %s", e.seconds, w.name)
                    except Exception as e:
                        return "err", e
            finally:
                w.pending -= 1
        return "flood", None

    async def _resolve(self, name: str) -> str:
        st, r = await self._call(("bot", "user"),
                                 lambda: functions.contacts.ResolveUsernameRequest(name), gate="resolve")
        if st == "flood":
            return "flood"
        if st == "ok":
            return "taken"
        if isinstance(r, errors.UsernameNotOccupiedError):
            return "maybe"
        if isinstance(r, errors.UsernameInvalidError):
            return "invalid"
        log.debug("resolve %s: %r", name, r)
        return "error"

    async def _confirm(self, name: str) -> str:
        if not self.users:
            return "free"
        st, r = await self._call(("user",),
                                 lambda: functions.account.CheckUsernameRequest(username=name), gate="confirm")
        if st == "flood":
            return "flood"
        if st == "ok":
            return "free" if r else "taken"
        if isinstance(r, errors.UsernameOccupiedError):
            return "taken"
        if isinstance(r, errors.UsernameInvalidError):
            return "invalid"
        if _PURCHASE and isinstance(r, _PURCHASE):
            return "fragment"
        log.debug("confirm %s: %r", name, r)
        return "error"

    # ───────── публичное API ─────────
    async def check(self, name: str, fresh: bool = False) -> str:
        """free | taken | invalid | fragment | flood | error. fresh=True игнорирует кэш (для трекера)."""
        now = time.time()
        if not fresh:
            if self.taken.get(name, 0) > now:
                return "taken"
            c = self.fresh.get(name)
            if c and c[1] > now:
                return c[0]
        self.total += 1
        async with self.sem:
            s = await self._resolve(name)
            if s == "taken":
                self._mark_taken(name)
                return "taken"
            if s in ("invalid", "error"):
                return s
            if s == "flood" and not self.users:
                return "flood"                      # без аккаунтов подтвердить нечем — не выдаём ложный «свободен»
            res = await self._confirm(name)  # maybe или flood резолверов -> точная проверка
        if res == "taken":
            self._mark_taken(name)
        elif res in ("free", "fragment"):
            self.fresh[name] = (res, time.time() + FREE_TTL)
        return res

    def _mark_taken(self, name: str):
        if len(self.taken) > 300_000:
            now = time.time()
            self.taken = {k: v for k, v in self.taken.items() if v > now}
        self.taken[name] = time.time() + TAKEN_TTL

    async def check_many(self, names, fresh: bool = False) -> list[str]:
        return await asyncio.gather(*[self.check(n, fresh) for n in names])

    async def _one(self, n: str):
        try:
            return n, await self.check(n)
        except Exception:
            log.exception("check %s", n)
            return n, "error"

    async def hunt(self, source, want: int = 1, max_checks: int = 3000, on_progress=None,
                   stop=None, max_seconds: float | None = None):
        """source: функция-генератор или список имён (по порядку). Результаты обрабатываются по мере готовности:
        нашли нужное число — сразу выходим. Ограничения: max_checks, max_seconds, stop().
        -> {'found': [...], 'checked': N, 'flood': bool, 'elapsed': сек}"""
        max_seconds = max_seconds or cfg.SEARCH_MAX_SECONDS
        t0 = time.perf_counter()
        timeup = lambda: time.perf_counter() - t0 > max_seconds or bool(stop and stop())
        batch = max(4, min(16, len(self.workers) * 3))
        found, seen, checked, flood = [], set(), 0, False
        idx = empty = 0
        while checked < max_checks and len(found) < want and not timeup():
            names = []
            if callable(source):
                tries = 0                                   # счётчик попыток — на каждую пачку отдельно
                while len(names) < batch and tries < batch * 40:
                    tries += 1
                    n = source()
                    if n not in seen and self.taken.get(n, 0) <= time.time():
                        seen.add(n)
                        names.append(n)
                if not names:
                    empty += 1
                    if empty >= 3:                          # генератор исчерпал новые варианты
                        break
                    continue
                empty = 0
            else:
                chunk = source[idx: idx + batch]
                idx += batch
                if not chunk:
                    break
                names = [n for n in chunk if self.taken.get(n, 0) <= time.time()]
                if not names:
                    continue
            tasks = [asyncio.ensure_future(self._one(n)) for n in names]
            done = fl = 0
            try:
                for fut in asyncio.as_completed(tasks):
                    n, r = await fut
                    checked += 1
                    done += 1
                    if r == "free":
                        found.append(n)
                    elif r == "flood":
                        fl += 1
                    if on_progress:
                        await on_progress(checked, len(found), checked / max(time.perf_counter() - t0, 0.01))
                    if len(found) >= want or timeup():
                        break
            finally:
                for t in tasks:
                    if not t.done():
                        t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            if done and fl == done:
                flood = True
                break
        return {"found": found[:want], "checked": checked, "flood": flood,
                "elapsed": time.perf_counter() - t0}

    def status(self) -> str:
        now = time.time()
        lines = []
        for w in self.workers:
            cd = f" · пауза {int(w.cool - now)}с" if w.cool > now else ""
            lines.append(f"{'👤' if w.kind == 'user' else '🤖'} {w.name} · запросов {w.ok}{cd}")
        return "\n".join(lines) or "нет воркеров"

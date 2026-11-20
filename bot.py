import asyncio
import datetime
import html
import logging
import random
import time
from pathlib import Path
from urllib.parse import quote

import aiohttp
from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (CallbackQuery, FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup,
                           LabeledPrice, Message, PreCheckoutQuery)

import config as cfg
import ui
from core import DB, WORDS, clean, gen_one, score, split_words, tier, ultra_candidates, valid, word_variants
from pool import Pool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

db = DB(cfg.DB_PATH)
pool = Pool()
router = Router()
BANNER = Path(__file__).with_name("banner.jpg")
BANNER_ID: str | None = None
BOT_USERNAME = ""

SEL: dict[int, str] = {}       # выбранная длина: "5" | "6" | "custom"
CUSTOM: dict[int, int] = {}    # своя длина 7–15
BUSY: set[int] = set()
AUTO: dict[int, asyncio.Task] = {}
_HAS_STYLE = "style" in InlineKeyboardButton.model_fields   # цветные кнопки (Bot API 9.4+)


class F_(StatesGroup):
    custom_len = State()
    word = State()
    trap = State()
    rate = State()
    cmp = State()
    pin = State()
    track = State()
    promo = State()
    adm = State()


# ───────────────────────── helpers ─────────────────────────
def B(text, data=None, style=None, url=None):
    kw = {"text": text}
    if data:
        kw["callback_data"] = data
    if url:
        kw["url"] = url
    # чёрно-белый стиль: цветные кнопки не используются (style игнорируется)
    return InlineKeyboardButton(**kw)


def KB(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[list(r) for r in rows])


async def edit(cb: CallbackQuery, text: str, kb=None):
    try:
        if cb.message.photo or cb.message.caption:
            await cb.message.delete()
            await cb.message.answer(text, reply_markup=kb)
        else:
            await cb.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest as e:
        if "not modified" not in str(e):
            log.warning("edit: %s", e)


def is_prem(uid: int) -> bool:
    return DB.is_premium(db.user(uid))


def quota(uid: int, bucket: str):
    """(осталось, лимит) или (None, None) — без лимита (Premium или NO_LIMITS)."""
    if cfg.NO_LIMITS or is_prem(uid):
        return None, None
    lim = cfg.FREE_LIMITS[bucket]
    return max(0, lim - db.used(uid, bucket)), lim


def qtxt(uid: int, bucket: str) -> str:
    left, lim = quota(uid, bucket)
    return "∞" if left is None else f"{left} из {lim}"


def cur_len(uid: int) -> int:
    b = SEL.get(uid, "5")
    return CUSTOM.get(uid, 7) if b == "custom" else int(b)


def flt(u) -> dict:
    return {"pin": u["pin"], "digits": bool(u["digits"]), "underscore": bool(u["underscore"])}


BACK_MENU = B("◂ В МЕНЮ", "menu")


def back(to="search", text="◂ НАЗАД"):
    return KB([B(text, to)])


def make_progress(msg: Message):
    last = [0.0]

    async def p(checked, found, rate):
        if time.monotonic() - last[0] < 1.0:
            return
        last[0] = time.monotonic()
        try:
            await msg.edit_text(ui.searching(checked, rate))
        except TelegramBadRequest:
            pass
    return p


async def ai_comment(name: str, sc: int) -> str:
    """Необязательная нейро-оценка через Claude API (если задан ANTHROPIC_API_KEY)."""
    if not cfg.ANTHROPIC_API_KEY:
        return ""
    prompt = (f"Telegram-юзернейм @{name}, эвристическая оценка {sc}/100. Дай короткий комментарий "
              f"(до 25 слов, по-русски): чем ник хорош или плох, кому подойдёт. Без вступлений.")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
            async with s.post("https://api.anthropic.com/v1/messages",
                              headers={"x-api-key": cfg.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01"},
                              json={"model": cfg.AI_MODEL, "max_tokens": 120,
                                    "messages": [{"role": "user", "content": prompt}]}) as r:
                data = await r.json()
                return html.escape(data["content"][0]["text"].strip())
    except Exception:
        return ""


# ───────────────────────── защита: бан и антиспам ─────────────────────────
class Guard(BaseMiddleware):
    last: dict[int, float] = {}

    async def __call__(self, handler, event, data):
        user = getattr(event, "from_user", None)
        if user and user.id not in cfg.ADMIN_IDS:
            if db.user(user.id)["banned"]:
                if isinstance(event, CallbackQuery):
                    await event.answer("Доступ закрыт", show_alert=True)
                return
            if isinstance(event, CallbackQuery):
                now = time.monotonic()
                if now - self.last.get(user.id, 0) < 0.3:
                    await event.answer()
                    return
                self.last[user.id] = now
        return await handler(event, data)


router.message.outer_middleware(Guard())
router.callback_query.outer_middleware(Guard())


# ───────────────────────── меню ─────────────────────────
def menu_kb(uid: int = 0):
    rows = [
        [B("ОЦЕНКА", "rate"), B("ПОИСК", "search")],
        [B("СРАВНЕНИЕ", "cmp"), B("ТРЕКЕР", "tracker")],
        [B("РОЗЫГРЫШИ", "gv"), B("БОНУСЫ", "bonus")],
        [B("ПРОФИЛЬ", "profile"), B("ПОДПИСКА", "tariffs")],
    ]
    if uid in cfg.ADMIN_IDS:
        rows.append([B("АДМИНКА", "admin")])
    return KB(*rows)


async def send_menu(m: Message, uid: int):
    global BANNER_ID
    if BANNER.exists() or BANNER_ID:
        photo = BANNER_ID or FSInputFile(BANNER)
        sent = await m.answer_photo(photo, caption=ui.main_caption(), reply_markup=menu_kb(uid))
        BANNER_ID = sent.photo[-1].file_id
    else:
        await m.answer(ui.main_caption(), reply_markup=menu_kb(uid))


@router.message(CommandStart())
async def start(m: Message, state: FSMContext, command: CommandObject):
    await state.clear()
    uid = m.from_user.id
    new = not db.exists(uid)
    db.user(uid)
    arg = command.args or ""
    if new and arg.startswith("ref") and arg[3:].isdigit():
        rid = int(arg[3:])
        if rid != uid and db.exists(rid):
            db.set(uid, ref_by=rid)
            db.add(rid, "diamonds", cfg.REF_REWARD)
            db.add(rid, "ref_count", 1)
            db.add(uid, "diamonds", cfg.REF_INVITEE_BONUS)
            try:
                await m.bot.send_message(rid, f"👥 По вашей ссылке пришёл новый пользователь\n"
                                              f"Начислено: 💎 <b>{cfg.REF_REWARD}</b>")
            except Exception:
                pass
    await send_menu(m, uid)


@router.callback_query(F.data == "menu")
async def to_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    try:
        await cb.message.delete()
    except TelegramBadRequest:
        pass
    await send_menu(cb.message, cb.from_user.id)


# ───────────────────────── раздел «Поиск» ─────────────────────────
def search_kb(uid: int):
    lock = "" if is_prem(uid) else "🔒 "
    return KB(
        [B("⚡ БЫСТРЫЙ ПОИСК", "fast")],
        [B("◼ ПОДБОР НИКА", "pick")],
        [B(f"{lock}ПО СЛОВУ", "gate:word"), B(f"{lock}ЛОВУШКА", "gate:trap")],
        [B("◆ УЛЬТРА-ПОИСК", "ultra")],
        [B(f"{lock}АВТОПОИСК", "gate:auto")],
        [B("⚙ ФИЛЬТРЫ", "filters"), B("★ ИЗБРАННОЕ", "favs")],
        [BACK_MENU],
    )


def search_text(uid: int) -> str:
    left, lim = quota(uid, SEL.get(uid, "5"))
    return ui.search_home(db.user(uid), left, lim, is_prem(uid))


@router.callback_query(F.data == "search")
async def search_menu(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    await edit(cb, search_text(cb.from_user.id), search_kb(cb.from_user.id))


GATES = {
    "word": ("🔤", "По слову", "Ники из твоего слова или имени: приставки, стилизация, замены букв."),
    "trap": ("🪤", "Ловушка", "Быстрый трекер: проверка занятого ника каждые 30 секунд и мгновенное уведомление."),
    "auto": ("🤖", "Автопоиск", "Бот сам ищет свободные ники в фоне и присылает находки, пока не нажмёшь «Стоп»."),
}


@router.callback_query(F.data.startswith("gate:"))
async def gate(cb: CallbackQuery, state: FSMContext):
    kind = cb.data.split(":")[1]
    uid = cb.from_user.id
    icon, title, desc = GATES[kind]
    await cb.answer()
    if not is_prem(uid):
        return await edit(cb, ui.head("🔒", title) + f"{desc}\n\n<blockquote>💎 Доступно только в Premium</blockquote>",
                          KB([B("◼ ОТКРЫТЬ ПОДПИСКУ", "tariffs")], [BACK_MENU]))
    if kind == "word":
        await state.set_state(F_.word)
        await edit(cb, ui.head(icon, title) + "Отправь слово или имя латиницей, я подберу свободные варианты.", back())
    elif kind == "trap":
        await state.set_state(F_.trap)
        await edit(cb, ui.head(icon, title) + "Отправь @username, который хочешь поймать.\nПроверяю каждые 30 секунд.", back())
    else:
        if uid in AUTO and not AUTO[uid].done():
            return await edit(cb, ui.head(icon, title) + "Автопоиск уже запущен.",
                              KB([B("✕ СТОП", "auto:stop")], [BACK_MENU]))
        AUTO[uid] = asyncio.create_task(auto_loop(cb.bot, uid, cb.message.chat.id))
        await edit(cb, ui.head(icon, title) + f"Запущен. Длина <b>{cur_len(uid)}</b>, находки приходят сюда.",
                   KB([B("✕ СТОП", "auto:stop")], [BACK_MENU]))


@router.callback_query(F.data == "auto:stop")
async def auto_stop(cb: CallbackQuery):
    t = AUTO.pop(cb.from_user.id, None)
    if t:
        t.cancel()
    await cb.answer("Остановлено")
    await edit(cb, search_text(cb.from_user.id), search_kb(cb.from_user.id))


# ───────────────────────── подбор ─────────────────────────
def pick_text(uid: int) -> str:
    u = db.user(uid)
    b = SEL.get(uid, "5")
    pin_lim = cfg.PREMIUM_PIN_LETTERS if is_prem(uid) else cfg.FREE_PIN_LETTERS
    left, lim = quota(uid, b)
    return (ui.head("🔥", "Подбор юзернейма") +
            f"▸ Длина          <b>{cur_len(uid)}</b> · {'буквы + цифры' if u['digits'] else 'только буквы'}\n"
            f"▸ Осталось      {ui.quota_line(left, lim)}\n"
            f"▸ Закреплено  <b>{u['pin'] or '—'}</b>  ({len(u['pin'])}/{pin_lim})")


def pick_kb(uid: int):
    b = SEL.get(uid, "5")

    def lbl(key, name):
        return f"✓ {name} · {qtxt(uid, key)}" if b == key else name

    custom = f"СВОЯ ДЛИНА ({CUSTOM[uid]})" if uid in CUSTOM else "СВОЯ ДЛИНА (7–15)"
    return KB(
        [B(lbl("5", "5 СИМВОЛОВ"), "len:5"), B(lbl("6", "6 СИМВОЛОВ"), "len:6")],
        [B(("✓ " if b == "custom" else "") + custom, "len:custom")],
        [B("◼ НАЧАТЬ ПОИСК", "go")],
        [B("◂ НАЗАД", "search")],
    )


@router.callback_query(F.data == "pick")
async def pick(cb: CallbackQuery):
    await cb.answer()
    await edit(cb, pick_text(cb.from_user.id), pick_kb(cb.from_user.id))


@router.callback_query(F.data.in_({"len:5", "len:6"}))
async def set_len(cb: CallbackQuery):
    SEL[cb.from_user.id] = cb.data.split(":")[1]
    await cb.answer()
    await edit(cb, pick_text(cb.from_user.id), pick_kb(cb.from_user.id))


@router.callback_query(F.data == "len:custom")
async def ask_len(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(F_.custom_len)
    await edit(cb, ui.head("↔", "Своя длина") + "Отправь число от 7 до 15.", back("pick"))


@router.message(F_.custom_len)
async def got_len(m: Message, state: FSMContext):
    if not (m.text or "").strip().isdigit() or not 7 <= int(m.text) <= 15:
        return await m.answer("Нужно число от 7 до 15.")
    SEL[m.from_user.id] = "custom"
    CUSTOM[m.from_user.id] = int(m.text)
    await state.clear()
    await m.answer(pick_text(m.from_user.id), reply_markup=pick_kb(m.from_user.id))


def result_kb(names):
    rows = [[B(f"ОЦЕНИТЬ @{n}", f"rate:{n}"), B("★", f"fav:{n}")] for n in names]
    rows.append([B("◼ ЕЩЁ", "go"), B("◂ НАЗАД", "search")])
    return KB(*rows)


@router.callback_query(F.data.in_({"go", "fast"}))
async def go(cb: CallbackQuery):
    uid = cb.from_user.id
    b = SEL.get(uid, "5")
    left, _ = quota(uid, b)
    if left is not None and left <= 0:
        return await cb.answer("Лимит на 24 часа исчерпан. Premium снимает лимиты.", show_alert=True)
    if uid in BUSY:
        return await cb.answer("Поиск уже идёт…")
    BUSY.add(uid)
    await cb.answer()
    try:
        await edit(cb, ui.searching(0))
        u = db.user(uid)
        f, length = flt(u), cur_len(uid)
        want = cfg.PREMIUM_RESULTS if is_prem(uid) else cfg.FREE_RESULTS
        res = await pool.hunt(lambda: gen_one(length, **f), want, 600, make_progress(cb.message))
    finally:
        BUSY.discard(uid)
    if res["flood"]:
        return await edit(cb, ui.head("⏳", "Лимит Telegram") +
                          "Проверки временно ограничены. Попробуй через пару минут.", back())
    if not res["found"]:
        return await edit(cb, ui.head("😕", "Ничего не найдено") +
                          f"Проверено <b>{res['checked']}</b> за {res['elapsed']:.1f} с. Попробуй ещё раз.",
                          KB([B("◼ ЕЩЁ", "go")], [B("◂ НАЗАД", "search")]))
    if left is not None:
        db.add_usage(uid, b)
    n = len(res["found"])
    db.add(uid, "found", n)
    db.add(uid, "coins", n)
    db.add(uid, "searches", 1)
    await edit(cb, ui.result_card(res["found"], res["checked"], res["elapsed"],
                                  qtxt(uid, b) if left is not None else ""), result_kb(res["found"]))


async def auto_loop(bot: Bot, uid: int, chat_id: int):
    found = 0
    try:
        while found < 10:
            f, length = flt(db.user(uid)), cur_len(uid)
            res = await pool.hunt(lambda: gen_one(length, **f), 1, 1500)
            if res["flood"]:
                await bot.send_message(chat_id, ui.head("⏳", "Автопоиск остановлен") + "Лимит Telegram, попробуй позже.")
                return
            for n in res["found"]:
                found += 1
                db.add(uid, "found", 1)
                sc = score(n)
                await bot.send_message(chat_id, f"🤖 <code>@{n}</code>\n{ui.bar(sc, 100, 10)} <b>{sc}</b> · {tier(sc)}",
                                       reply_markup=KB([B("⭐ В избранное", f"fav:{n}")]))
        await bot.send_message(chat_id, "🤖 Автопоиск завершён: найдено 10 ников.")
    except asyncio.CancelledError:
        pass
    finally:
        AUTO.pop(uid, None)


# ───────────────────────── по слову / ловушка ─────────────────────────
@router.message(F_.word)
async def got_word(m: Message, state: FSMContext):
    w = clean(m.text or "")
    if not w.isalpha() or not w.isascii() or not 2 <= len(w) <= 20:
        return await m.answer("Отправь одно слово латиницей (2–20 букв).")
    await state.clear()
    msg = await m.answer(ui.searching(0))
    variants = word_variants(w)[:60]
    t0 = time.perf_counter()
    res = await pool.check_many(variants)
    free = [v for v, r in zip(variants, res) if r == "free"]
    free.sort(key=score, reverse=True)
    text = ui.head("🔤", "Подбор по слову", w)
    if free:
        text += "\n".join(f"<code>@{v}</code>  ·  {score(v)}/100" for v in free[:10])
    else:
        text += "Свободных вариантов не нашлось. Попробуй другое слово."
    text += f"\n\n⚡ <i>проверено {len(variants)} за {time.perf_counter() - t0:.1f} с</i>"
    await msg.edit_text(text, reply_markup=KB(*[[B(f"⭐ @{v}", f"fav:{v}")] for v in free[:5]], [B("← Назад", "search")]))


@router.message(F_.trap)
async def got_trap(m: Message, state: FSMContext):
    await state.clear()
    await add_tracker(m, clean(m.text or ""), fast=True)


# ───────────────────────── ультра-поиск ─────────────────────────
@router.callback_query(F.data == "ultra")
async def ultra(cb: CallbackQuery):
    u = db.user(cb.from_user.id)
    await cb.answer()
    c6, c5 = cfg.ULTRA_COST[6], cfg.ULTRA_COST[5]
    await edit(cb,
               ui.head("◆", "Ультра-поиск") +
               "Один гарантированно сильный ник: брендовая склейка или стилизация. "
               "Свободен на момент выдачи.\n\n"
               f"▸ 6 букв · оценка от {cfg.ULTRA_MIN_SCORE[6]} · 💎 <b>{c6}</b>\n"
               f"▸ 5 букв · оценка от {cfg.ULTRA_MIN_SCORE[5]} · 💎 <b>{c5}</b>\n\n"
               f"Твои алмазы  💎 <b>{u['diamonds']}</b>"
               + ("\n<i>Админ: бесплатно</i>" if cb.from_user.id in cfg.ADMIN_IDS else ""),
               KB([B(f"6 БУКВ · {c6} 💎", "ultra:6"), B(f"5 БУКВ · {c5} 💎", "ultra:5")],
                  [B("КУПИТЬ АЛМАЗЫ", "buy:diamonds")],
                  [B("◂ НАЗАД", "search")]))


@router.callback_query(F.data.startswith("ultra:"))
async def ultra_go(cb: CallbackQuery):
    uid = cb.from_user.id
    n = int(cb.data.split(":")[1])
    cost = 0 if uid in cfg.ADMIN_IDS else cfg.ULTRA_COST[n]
    if db.user(uid)["diamonds"] < cost:
        return await cb.answer("Недостаточно алмазов", show_alert=True)
    if uid in BUSY:
        return await cb.answer("Поиск уже идёт…")
    BUSY.add(uid)
    await cb.answer()
    try:
        await edit(cb, ui.searching(0))
        cands = [c for c in ultra_candidates(n) if score(c) >= cfg.ULTRA_MIN_SCORE[n]]
        res = await pool.hunt(cands, 1, len(cands) or 1, make_progress(cb.message))
    finally:
        BUSY.discard(uid)
    kb_back = back("ultra", "◂ К УЛЬТРА")
    if not res["found"]:
        return await edit(cb, ui.head("◇", "Сейф пуст") +
                          "Свободных сильных ников сейчас нет, скоро пополнится.\n<i>Алмазы не списаны.</i>", kb_back)
    name = res["found"][0]
    if cost:
        db.add(uid, "diamonds", -cost)
    db.add(uid, "found", 1)
    sw = split_words(name)
    extra = f"\nсклейка: <b>{sw[0]} + {sw[1]}</b>" if sw else ("\nнастоящее слово" if name in WORDS else "")
    await edit(cb, ui.result_card([name], res["checked"], res["elapsed"], "", extra) +
               (f"\n▸ Списано  💎 <b>{cost}</b>" if cost else ""),
               KB([B(f"ОЦЕНИТЬ @{name}", f"rate:{name}"), B("★", f"fav:{name}")], [B("◂ НАЗАД", "ultra")]))


# ───────────────────────── фильтры ─────────────────────────
def filters_view(uid: int):
    u = db.user(uid)
    lim = cfg.PREMIUM_PIN_LETTERS if is_prem(uid) else cfg.FREE_PIN_LETTERS
    on = lambda v: "вкл" if v else "выкл"
    text = (ui.head("⚙", "Фильтры") +
            f"▸ Начало ника   <b>{u['pin'] or '—'}</b>  ({len(u['pin'])}/{lim})\n"
            f"▸ Цифры            <b>{on(u['digits'])}</b>\n"
            f"▸ Подчёркивание  <b>{on(u['underscore'])}</b>")
    kb = KB([B("ЗАКРЕПИТЬ БУКВЫ", "f:pin")],
            [B(f"ЦИФРЫ: {on(u['digits'])}", "f:digits"), B(f"ПОДЧЁРК.: {on(u['underscore'])}", "f:us")],
            [B("СБРОС", "f:reset")],
            [B("◂ НАЗАД", "search")])
    return text, kb


@router.callback_query(F.data == "filters")
async def filters(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    await edit(cb, *filters_view(cb.from_user.id))


@router.callback_query(F.data.startswith("f:"))
async def filters_act(cb: CallbackQuery, state: FSMContext):
    uid, act = cb.from_user.id, cb.data[2:]
    u = db.user(uid)
    await cb.answer()
    if act == "digits":
        db.set(uid, digits=1 - u["digits"])
    elif act == "us":
        db.set(uid, underscore=1 - u["underscore"])
    elif act == "reset":
        db.set(uid, pin="", digits=0, underscore=0)
    elif act == "pin":
        await state.set_state(F_.pin)
        return await edit(cb, ui.head("📌", "Закрепить буквы") +
                          "Отправь буквы, с которых должен начинаться ник.\nЧтобы убрать — отправь «-».", back("filters"))
    await edit(cb, *filters_view(uid))


@router.message(F_.pin)
async def got_pin(m: Message, state: FSMContext):
    uid = m.from_user.id
    p = (m.text or "").strip().lower()
    lim = cfg.PREMIUM_PIN_LETTERS if is_prem(uid) else cfg.FREE_PIN_LETTERS
    if p == "-":
        p = ""
    elif not p.isalpha() or not p.isascii() or len(p) > lim:
        return await m.answer(f"Только латинские буквы, не больше {lim}. Больше — в Premium.")
    db.set(uid, pin=p)
    await state.clear()
    text, kb = filters_view(uid)
    await m.answer(text, reply_markup=kb)


# ───────────────────────── оценка / сравнение ─────────────────────────
async def rate_text(name: str) -> str:
    if not valid(name):
        return ui.head("⚠", "Некорректный username") + "5–32 символа: латиница, цифры и «_», начинается с буквы."
    st, ai = await asyncio.gather(pool.check(name), ai_comment(name, score(name)))
    return ui.rate_card(name, st, ai)


@router.callback_query(F.data == "rate")
async def rate(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(F_.rate)
    await edit(cb, ui.head("📊", "Оценка юзернейма") + "Отправь @username, который нужно оценить.", back("menu"))


@router.callback_query(F.data.startswith("rate:"))
async def rate_btn(cb: CallbackQuery):
    await cb.answer()
    await cb.message.answer(await rate_text(cb.data[5:]), reply_markup=KB([BACK_MENU]))


@router.message(F_.rate)
async def got_rate(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(await rate_text(clean(m.text or "")), reply_markup=KB([BACK_MENU]))


@router.callback_query(F.data == "cmp")
async def cmp_(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(F_.cmp)
    await edit(cb, ui.head("⚖", "Сравнение") + "Отправь два юзернейма через пробел или с новой строки.", back("menu"))


@router.message(F_.cmp)
async def got_cmp(m: Message, state: FSMContext):
    names = [clean(x) for x in (m.text or "").split()][:2]
    if len(names) < 2 or not all(valid(n) for n in names):
        return await m.answer("Нужно два корректных юзернейма, например: <code>@alpha @bravo</code>")
    await state.clear()
    a, b = names
    sa, sb = score(a), score(b)
    st = await pool.check_many([a, b])
    win = a if sa > sb else b if sb > sa else None
    await m.answer(
        ui.head("⚖", "Сравнение") +
        f"<code>@{a}</code>\n{ui.bar(sa, 100, 10)}  <b>{sa}</b> · {tier(sa)}\n{ui.STATUS[st[0]]}\n\n"
        f"<code>@{b}</code>\n{ui.bar(sb, 100, 10)}  <b>{sb}</b> · {tier(sb)}\n{ui.STATUS[st[1]]}\n\n"
        + (f"🏅 Сильнее: <code>@{win}</code>" if win else "🤝 Ничья"), reply_markup=KB([BACK_MENU]))


# ───────────────────────── трекер ─────────────────────────
def tracker_view(uid: int):
    rows = db.track_list(uid)
    lim = cfg.PREMIUM_TRACK_SLOTS if is_prem(uid) else cfg.FREE_TRACK_SLOTS
    text = (ui.head("☁", "Трекер", "сообщу, когда ник освободится") +
            f"▸ Слоты  {ui.bar(len(rows), lim)}  <b>{len(rows)}</b>/{lim}\n\n" +
            ("\n".join(f"{'🪤' if r['fast'] else '•'} <code>@{r['username']}</code>" for r in rows) or "<i>Пока пусто</i>"))
    kb = KB(*[[B(f"✕ @{r['username']}", f"trk:del:{r['id']}")] for r in rows],
            [B("＋ ДОБАВИТЬ НИК", "trk:add")], [BACK_MENU])
    return text, kb


@router.callback_query(F.data == "tracker")
async def tracker(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    await edit(cb, *tracker_view(cb.from_user.id))


@router.callback_query(F.data == "trk:add")
async def trk_add(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(F_.track)
    await edit(cb, ui.head("☁", "Новый ник в трекере") + "Отправь @username, за которым следить.", back("tracker"))


@router.callback_query(F.data.startswith("trk:del:"))
async def trk_del(cb: CallbackQuery):
    db.track_del(cb.from_user.id, int(cb.data.split(":")[2]))
    await cb.answer("Удалено")
    await edit(cb, *tracker_view(cb.from_user.id))


async def add_tracker(m: Message, name: str, fast: bool):
    uid = m.from_user.id
    if not valid(name):
        return await m.answer("⚠ Некорректный username.", reply_markup=KB([BACK_MENU]))
    lim = cfg.PREMIUM_TRACK_SLOTS if is_prem(uid) else cfg.FREE_TRACK_SLOTS
    if len(db.track_list(uid)) >= lim:
        return await m.answer("Все слоты заняты. Удали лишнее или возьми Premium.", reply_markup=KB([BACK_MENU]))
    st = await pool.check(name, fresh=True)
    if st == "free":
        return await m.answer(f"🟢 <code>@{name}</code> уже свободен, занимай!", reply_markup=KB([BACK_MENU]))
    if not db.track_add(uid, name, fast):
        return await m.answer("Этот ник уже в трекере.", reply_markup=KB([BACK_MENU]))
    await m.answer(f"{'🪤' if fast else '☁'} Слежу за <code>@{name}</code>"
                   f"{' (ловушка, каждые 30 с)' if fast else ''}. Напишу, как освободится.",
                   reply_markup=KB([B("☁ Трекер", "tracker")], [BACK_MENU]))


@router.message(F_.track)
async def got_track(m: Message, state: FSMContext):
    await state.clear()
    await add_tracker(m, clean(m.text or ""), fast=False)


async def tracker_loop(bot: Bot):
    tick = 0
    while True:
        await asyncio.sleep(cfg.TRACK_INTERVAL)
        tick += 1
        try:
            rows = [r for r in db.track_all() if r["fast"] or tick % cfg.TRACK_SLOW_EVERY == 0]
            names = sorted({r["username"] for r in rows})
            res = dict(zip(names, await pool.check_many(names, fresh=True)))
            for r in rows:
                if res.get(r["username"]) == "free":
                    db.track_del(r["user_id"], r["id"])
                    try:
                        await bot.send_message(r["user_id"], f"🔔 <code>@{r['username']}</code> освободился!\nЗабирай скорее.")
                    except Exception:
                        log.warning("notify failed %s", r["user_id"])
        except Exception:
            log.exception("tracker loop")


# ───────────────────────── избранное ─────────────────────────
@router.callback_query(F.data.startswith("fav:"))
async def fav_add(cb: CallbackQuery):
    uid, name = cb.from_user.id, cb.data[4:]
    lim = cfg.PREMIUM_FAV_SLOTS if is_prem(uid) else cfg.FREE_FAV_SLOTS
    if len(db.fav_list(uid)) >= lim:
        return await cb.answer(f"Избранное заполнено ({lim}). Premium расширяет лимит.", show_alert=True)
    await cb.answer("Добавлено в избранное ⭐" if db.fav_add(uid, name) else "Уже в избранном")


@router.callback_query(F.data == "favs")
async def favs(cb: CallbackQuery):
    uid = cb.from_user.id
    items = db.fav_list(uid)
    await cb.answer()
    text = ui.head("⭐", "Избранное") + ("\n".join(f"<code>@{n}</code> · {score(n)}/100" for n in items[:30])
                                          or "<i>Пока пусто. Жми ⭐ под найденными никами.</i>")
    rows = [[B(f"ОЦЕНИТЬ @{n}", f"rate:{n}")] for n in items[:6]]
    if items:
        rows.append([B("ОЧИСТИТЬ", "favs:clear")])
    await edit(cb, text, KB(*rows, [B("◂ НАЗАД", "search")]))


@router.callback_query(F.data == "favs:clear")
async def favs_clear(cb: CallbackQuery):
    db.fav_clear(cb.from_user.id)
    await cb.answer("Очищено")
    await favs(cb)


# ───────────────────────── бонусы ─────────────────────────
def bonus_view(uid: int):
    u = db.user(uid)
    today = datetime.date.today().toordinal()
    streak = u["streak"] if u["last_daily"] >= today - 1 else 0
    pos = streak % 7 or (7 if streak else 0)
    done = u["last_daily"] == today
    text = (ui.head("🎁", "Бонусы") +
            f"▸ Серия     {ui.bar(pos, 7, 7)}  <b>{pos}</b>/7\n"
            f"▸ Монеты   🪙 <b>{u['coins']}</b>   Алмазы  💎 <b>{u['diamonds']}</b>\n"
            f"▸ Друзья   👥 <b>{u['ref_count']}</b>\n\n"
            f"<blockquote>На 7-й день серии — +1 💎. Обмен: {cfg.COINS_PER_DIAMOND} 🪙 = 1 💎</blockquote>")
    kb = KB([B("БОНУС ПОЛУЧЕН СЕГОДНЯ" if done else "ЗАБРАТЬ БОНУС ДНЯ", "daily")],
            [B("ПРИГЛАСИТЬ", "refs"), B("ПРОМОКОД", "promo")],
            [B(f"ОБМЕН {cfg.COINS_PER_DIAMOND} 🪙 → 1 💎", "exch")],
            [BACK_MENU])
    return text, kb


@router.callback_query(F.data == "bonus")
async def bonus(cb: CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.answer()
    await edit(cb, *bonus_view(cb.from_user.id))


@router.callback_query(F.data == "daily")
async def daily(cb: CallbackQuery):
    r = db.claim_daily(cb.from_user.id)
    if not r:
        return await cb.answer("Сегодня бонус уже получен. Приходи завтра!", show_alert=True)
    await cb.answer(f"+{r['coins']} 🪙" + (" и +1 💎" if r["diamonds"] else ""), show_alert=True)
    await edit(cb, *bonus_view(cb.from_user.id))


@router.callback_query(F.data == "exch")
async def exch(cb: CallbackQuery):
    uid = cb.from_user.id
    if db.user(uid)["coins"] < cfg.COINS_PER_DIAMOND:
        return await cb.answer(f"Нужно минимум {cfg.COINS_PER_DIAMOND} монет", show_alert=True)
    db.add(uid, "coins", -cfg.COINS_PER_DIAMOND)
    db.add(uid, "diamonds", 1)
    await cb.answer("Обмен выполнен: +1 💎")
    await edit(cb, *bonus_view(uid))


@router.callback_query(F.data == "refs")
async def refs(cb: CallbackQuery):
    uid = cb.from_user.id
    link = f"https://t.me/{BOT_USERNAME}?start=ref{uid}"
    await cb.answer()
    await edit(cb, ui.head("👥", "Пригласи друзей") +
               f"За каждого друга: тебе 💎 <b>{cfg.REF_REWARD}</b>, ему 💎 <b>{cfg.REF_INVITEE_BONUS}</b>.\n\n"
               f"▸ Приглашено  <b>{db.user(uid)['ref_count']}</b>\n\n"
               f"Твоя ссылка:\n<code>{link}</code>",
               KB([B("📤 Поделиться", url="https://t.me/share/url?url=" + quote(link) + "&text=" + quote("Ищу свободные юзернеймы"))],
                  [B("← Назад", "bonus")]))


@router.callback_query(F.data == "promo")
async def promo(cb: CallbackQuery, state: FSMContext):
    await cb.answer()
    await state.set_state(F_.promo)
    await edit(cb, ui.head("🎟", "Промокод") + "Отправь промокод одним сообщением.", back("bonus"))


@router.message(F_.promo)
async def got_promo(m: Message, state: FSMContext):
    await state.clear()
    r = db.promo_redeem(m.from_user.id, m.text or "")
    if r is None:
        return await m.answer("Промокод не найден или закончился. Попробуй другой.",
                              reply_markup=KB([B("← Бонусы", "bonus")]))
    if r == "used":
        return await m.answer("Ты уже активировал этот промокод.", reply_markup=KB([B("← Бонусы", "bonus")]))
    names = {"diamonds": "💎 алмазов", "coins": "🪙 монет", "premium": "дн. Premium 👑"}
    await m.answer(f"🎉 Промокод активирован: +{r['amount']} {names[r['kind']]}", reply_markup=KB([B("← Бонусы", "bonus")]))


# ───────────────────────── розыгрыши ─────────────────────────
def gv_card(g, uid: int) -> str:
    n = len(db.gv_parts(g["id"]))
    me = "  ✅ вы участвуете" if uid in db.gv_parts(g["id"]) else ""
    return (f"🏆 <b>{html.escape(g['title'])}</b>{me}\n"
            f"▸ Приз          <b>{html.escape(g['prize'])}</b>\n"
            f"▸ Участников  <b>{n}</b>\n"
            f"▸ До конца     <b>{ui.left_str(g['ends'] - time.time())}</b>")


@router.callback_query(F.data == "gv")
async def gv(cb: CallbackQuery):
    uid = cb.from_user.id
    items = db.gv_active()
    try:
        await cb.answer()
    except TelegramBadRequest:
        pass   # повторный ответ при обновлении после «Участвовать»
    text = ui.head("🏆", "Розыгрыши", "призы: NFT-юзернеймы и Premium") + (
        "\n\n".join(gv_card(g, uid) for g in items) or "<i>Сейчас активных розыгрышей нет. Загляни позже.</i>")
    rows = [[B(("✓ УЧАСТВУЕШЬ" if uid in db.gv_parts(g['id']) else "◼ УЧАСТВОВАТЬ") + f" · {g['title'][:22]}", f"gv:join:{g['id']}")]
            for g in items]
    await edit(cb, text, KB(*rows, [BACK_MENU]))


@router.callback_query(F.data.startswith("gv:join:"))
async def gv_join(cb: CallbackQuery):
    g = db.gv_get(int(cb.data.split(":")[2]))
    if not g or g["status"] != "active" or g["ends"] <= time.time():
        return await cb.answer("Розыгрыш уже завершён", show_alert=True)
    ok = db.gv_join(g["id"], cb.from_user.id)
    await cb.answer("Вы участвуете! Удачи 🍀" if ok else "Вы уже участвуете", show_alert=ok)
    await gv(cb)


async def giveaway_loop(bot: Bot):
    while True:
        await asyncio.sleep(20)
        try:
            for g in db.gv_due():
                parts = db.gv_parts(g["id"])
                winner = random.choice(parts) if parts else 0
                db.gv_finish(g["id"], winner)
                tag = ""
                if winner:
                    try:
                        ch = await bot.get_chat(winner)
                        tag = f" (@{ch.username})" if ch.username else ""
                    except Exception:
                        pass
                    try:
                        await bot.send_message(winner, ui.head("🎉", "Вы победили!") +
                                               f"Розыгрыш: <b>{html.escape(g['title'])}</b>\nПриз: <b>{html.escape(g['prize'])}</b>\n\n"
                                               "Администратор свяжется с вами для выдачи приза.")
                    except Exception:
                        pass
                for aid in cfg.ADMIN_IDS:
                    try:
                        await bot.send_message(aid, f"🏆 Розыгрыш «{html.escape(g['title'])}» завершён.\n"
                                                    f"Победитель: <code>{winner}</code>{tag}" if winner else
                                                    f"🏆 Розыгрыш «{html.escape(g['title'])}» завершён без участников.")
                    except Exception:
                        pass
                for uid in parts[:500]:
                    if uid != winner:
                        try:
                            await bot.send_message(uid, f"🏆 Розыгрыш «{html.escape(g['title'])}» завершён.\n"
                                                        f"Победитель: <code>{winner}</code>{tag}. Повезёт в следующий раз!")
                        except Exception:
                            pass
                        await asyncio.sleep(0.05)
        except Exception:
            log.exception("giveaway loop")


# ───────────────────────── профиль / тарифы / оплата ─────────────────────────
@router.callback_query(F.data == "profile")
async def profile(cb: CallbackQuery):
    uid = cb.from_user.id
    u = db.user(uid)
    prem = ("администратор" if uid in cfg.ADMIN_IDS else
            "навсегда" if u["premium_until"] > time.time() + 10 * 365 * 86400 else
            f"до {time.strftime('%d.%m.%Y', time.localtime(u['premium_until']))}" if DB.is_premium(u) else "нет")
    await cb.answer()
    await edit(cb, ui.head("👤", "Профиль", f"ID {uid}") +
               f"▸ Premium      <b>{prem}</b>\n▸ Алмазы       💎 <b>{u['diamonds']}</b>\n▸ Монеты       🪙 <b>{u['coins']}</b>\n"
               f"▸ Найдено      <b>{u['found']}</b>\n▸ Поисков       <b>{u['searches']}</b>\n"
               f"▸ В трекере    <b>{len(db.track_list(uid))}</b>\n▸ Избранное   <b>{len(db.fav_list(uid))}</b>\n"
               f"▸ Друзей        <b>{u['ref_count']}</b>",
               KB([B("💎 Тарифы", "tariffs"), B("🎁 Бонусы", "bonus")], [BACK_MENU]))


@router.callback_query(F.data == "tariffs")
async def tariffs(cb: CallbackQuery):
    await cb.answer()
    u = db.user(cb.from_user.id)
    text = (ui.head("▮", "Подписка") +
            "Премиум открывает всё: авто-подборы без ограничений, по слову, ловушка, автопоиск, "
            f"до {cfg.PREMIUM_RESULTS} ников за подбор.\n\n")
    for key, short, title, days, stars, tag in cfg.PREMIUM_PLANS:
        mark = f"  ◄ <i>{tag}</i>" if tag else ""
        if key == "trial" and u["trial_used"]:
            text += f"▸ {short} · {stars} ⭐ — <i>уже использована</i>\n"
        else:
            text += f"▸ {short} · <b>{stars} ⭐</b>{mark}\n"
    text += f"\nАлмазы для ультра-поиска: 💎 {cfg.DIAMOND_PACK} · {cfg.DIAMOND_PACK_STARS} ⭐"
    kb = KB(
        [B("ПРОБА · 1 ДЕНЬ · 19⭐", "buy:trial")] if not u["trial_used"] else [],
        [B("7 ДНЕЙ · 50⭐", "buy:d7"), B("14 ДНЕЙ · 75⭐", "buy:d14")],
        [B("30 ДНЕЙ · 100⭐", "buy:d30")],
        [B("3 МЕС · 350⭐", "buy:m3"), B("6 МЕС · 400⭐ ◄", "buy:m6")],
        [B("12 МЕС · 550⭐", "buy:m12"), B("НАВСЕГДА · 999⭐", "buy:life")],
        [B(f"💎 {cfg.DIAMOND_PACK} АЛМАЗОВ · {cfg.DIAMOND_PACK_STARS}⭐", "buy:diamonds")],
        [BACK_MENU],
    )
    await edit(cb, text, kb)


@router.callback_query(F.data.startswith("buy:"))
async def buy(cb: CallbackQuery):
    kind = cb.data.split(":")[1]
    await cb.answer()
    if kind == "diamonds":
        title, amount = f"{cfg.DIAMOND_PACK} алмазов", cfg.DIAMOND_PACK_STARS
    else:
        p = cfg.PREMIUM_PLANS_D.get(kind)
        if not p:
            return
        _, _, title, _, amount, _ = p
        if kind == "trial" and db.user(cb.from_user.id)["trial_used"]:
            return await cb.answer("Пробная подписка выдаётся один раз. Она уже использована.", show_alert=True)
    await cb.message.answer_invoice(title=title, description=f"SourceSearch · {title}", payload=kind,
                                    provider_token="", currency="XTR",
                                    prices=[LabeledPrice(label=title, amount=amount)])


@router.pre_checkout_query()
async def pre_checkout(q: PreCheckoutQuery):
    await q.answer(ok=True)


@router.message(F.successful_payment)
async def paid(m: Message):
    uid = m.from_user.id
    p = m.successful_payment.invoice_payload
    if p == "diamonds":
        db.add(uid, "diamonds", cfg.DIAMOND_PACK)
        return await m.answer(ui.head("▮", "Алмазы зачислены") + f"+{cfg.DIAMOND_PACK} 💎",
                              reply_markup=KB([BACK_MENU]))
    plan = cfg.PREMIUM_PLANS_D.get(p)
    if not plan:
        return
    _, _, title, days, _, _ = plan
    db.add_premium(uid, days)
    if p == "trial":
        db.set(uid, trial_used=1)
    life = days >= cfg.LIFETIME_DAYS
    await m.answer(ui.head("▮", "Подписка активирована") +
                   ("<b>НАВСЕГДА.</b> Все ограничения сняты. Пользуйся." if life else
                    f"<b>{title.upper()}</b>\nВсе ограничения сняты на {days} дн. Не подведи."),
                   reply_markup=KB([BACK_MENU]))


# ───────────────────────── админка ─────────────────────────
def admin_kb():
    return KB([B("СТАТИСТИКА", "adm:stats"), B("ВОРКЕРЫ", "adm:workers")],
              [B("ВЫДАТЬ PREMIUM", "adm:prem"), B("СНЯТЬ PREMIUM", "adm:rev")],
              [B("ВЫДАТЬ АЛМАЗЫ", "adm:dia"), B("ИНФО О ЮЗЕРЕ", "adm:info")],
              [B("БАН", "adm:ban"), B("РАЗБАН", "adm:unban")],
              [B("ПРОМОКОД", "adm:promo"), B("РОЗЫГРЫШ", "adm:gv")],
              [B("РАССЫЛКА", "adm:bc")],
              [BACK_MENU])


ADMIN_TEXT = ui.head("▮", "Админка", "полный доступ · без лимитов")
ADM_PROMPTS = {
    "prem": "Отправь: <code>ID дни</code>\nПример: <code>123456789 30</code>",
    "rev": "Отправь ID пользователя, у которого снять Premium.",
    "dia": "Отправь: <code>ID количество</code>\nПример: <code>123456789 10</code>",
    "info": "Отправь ID пользователя.",
    "ban": "Отправь ID пользователя для бана.",
    "unban": "Отправь ID пользователя для разбана.",
    "promo": "Отправь: <code>КОД тип количество активаций</code>\nТип: <code>diamonds</code>, <code>coins</code> или <code>premium</code> (дни)\n"
             "Пример: <code>SPRING diamonds 5 100</code>",
    "gv": "Отправь: <code>Название | Приз | Часов</code>\nПример: <code>NFT-ник | @king | 24</code>",
    "bc": "Отправь текст рассылки (HTML-разметка поддерживается).",
}


@router.message(Command("admin"))
async def admin_cmd(m: Message, state: FSMContext):
    if m.from_user.id in cfg.ADMIN_IDS:
        await state.clear()
        await m.answer(ADMIN_TEXT, reply_markup=admin_kb())


@router.callback_query(F.data.startswith("adm"))
async def admin_cb(cb: CallbackQuery, state: FSMContext):
    if cb.from_user.id not in cfg.ADMIN_IDS:
        return await cb.answer("Нет доступа", show_alert=True)
    act = cb.data.split(":")[1] if ":" in cb.data else ""
    await cb.answer()
    kb_back = KB([B("← Админка", "admin")])
    if act == "":
        await state.clear()
        return await edit(cb, ADMIN_TEXT, admin_kb())
    if act == "stats":
        s = db.stats()
        return await edit(cb, ui.head("📈", "Статистика") +
                          f"▸ Пользователей  <b>{s['users']}</b>  (+{s['today']} за 24 ч)\n▸ С Premium       <b>{s['premium']}</b>\n"
                          f"▸ Найдено ников  <b>{s['found']}</b>\n▸ В трекерах       <b>{s['tracked']}</b>\n"
                          f"▸ Забанено         <b>{s['banned']}</b>\n▸ Проверок          <b>{pool.total}</b>", kb_back)
    if act == "workers":
        return await edit(cb, ui.head("⚙", "Воркеры проверки") + pool.status() +
                          "\n\n<i>Больше аккаунтов и ботов = быстрее поиск (SESSIONS, RESOLVER_BOTS в .env)</i>", kb_back)
    await state.set_state(F_.adm)
    await state.update_data(act=act)
    await edit(cb, ui.head("🛠", "Админка") + ADM_PROMPTS[act], kb_back)


def _ids(txt: str, n: int) -> list[int]:
    parts = txt.split()
    if len(parts) < n:
        raise ValueError
    return [int(x) for x in parts[:n]]


async def _notify(bot: Bot, uid: int, text: str):
    try:
        await bot.send_message(uid, text)
    except Exception:
        pass


@router.message(F_.adm)
async def adm_input(m: Message, state: FSMContext):
    if m.from_user.id not in cfg.ADMIN_IDS:
        return
    act = (await state.get_data()).get("act")
    txt = (m.text or "").strip()
    try:
        if act == "prem":
            uid, days = _ids(txt, 2)
            db.add_premium(uid, days)
            await _notify(m.bot, uid, f"👑 Вам выдан Premium на {days} дн.")
            out = f"✅ Premium для <code>{uid}</code> на {days} дн."
        elif act == "rev":
            (uid,) = _ids(txt, 1)
            db.set(uid, premium_until=0)
            out = f"✅ Premium снят у <code>{uid}</code>."
        elif act == "dia":
            uid, n = _ids(txt, 2)
            db.add(uid, "diamonds", n)
            await _notify(m.bot, uid, f"💎 Вам начислено алмазов: {n}")
            out = f"✅ <code>{uid}</code>: {n:+d} 💎"
        elif act in ("ban", "unban"):
            (uid,) = _ids(txt, 1)
            db.set(uid, banned=1 if act == "ban" else 0)
            out = f"{'⛔ Забанен' if act == 'ban' else '✅ Разбанен'}: <code>{uid}</code>"
        elif act == "info":
            (uid,) = _ids(txt, 1)
            if not db.exists(uid):
                out = "Пользователь не найден."
            else:
                u = db.user(uid)
                pr = time.strftime("%d.%m.%Y", time.localtime(u["premium_until"])) if DB.is_premium(u) else "нет"
                out = (ui.head("🔎", "Пользователь", f"ID {uid}") +
                       f"▸ Premium  <b>{pr}</b>\n▸ 💎 {u['diamonds']}  🪙 {u['coins']}\n▸ Найдено  <b>{u['found']}</b>\n"
                       f"▸ Друзей  <b>{u['ref_count']}</b>  (пригласил: <code>{u['ref_by']}</code>)\n"
                       f"▸ Регистрация  <b>{time.strftime('%d.%m.%Y', time.localtime(u['created']))}</b>\n"
                       f"▸ Бан  <b>{'да' if u['banned'] else 'нет'}</b>")
        elif act == "promo":
            code, kind, amount, uses = txt.split()[:4]
            if kind not in ("diamonds", "coins", "premium"):
                raise ValueError
            db.promo_create(code, kind, int(amount), int(uses))
            out = f"✅ Промокод <code>{code.upper()}</code>: {kind} +{amount}, активаций {uses}"
        elif act == "gv":
            title, prize, hours = [x.strip() for x in txt.split("|")][:3]
            gid = db.gv_create(title, prize, float(hours.replace(",", ".")))
            out = f"✅ Розыгрыш #{gid} «{html.escape(title)}» создан на {hours} ч."
        elif act == "bc":
            await state.clear()
            ok = fail = 0
            for uid in db.all_ids():
                try:
                    await m.bot.send_message(uid, m.html_text)
                    ok += 1
                except Exception:
                    fail += 1
                await asyncio.sleep(0.05)
            return await m.answer(f"📣 Отправлено: {ok}, не доставлено: {fail}", reply_markup=admin_kb())
        else:
            raise ValueError
    except (ValueError, IndexError):
        return await m.answer("Неверный формат. Попробуй ещё раз или вернись в админку.\n\n" + ADM_PROMPTS.get(act, ""))
    await state.clear()
    await m.answer(out, reply_markup=admin_kb())


# ───────────────────────── запуск ─────────────────────────
async def main():
    global BOT_USERNAME
    await pool.start()
    bot = Bot(cfg.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    BOT_USERNAME = (await bot.get_me()).username
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    asyncio.create_task(tracker_loop(bot))
    asyncio.create_task(giveaway_loop(bot))
    log.info("SourceSearch запущен: @%s", BOT_USERNAME)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())

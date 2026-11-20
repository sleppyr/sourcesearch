"""Оформление сообщений: чёрно-белый стиль. Жёстко, ясно, без украшений."""
from core import WORDS, readability, score, split_words, tier

HR = "━" * 22
SPIN = "▖▘▝▗▚▞█"

STATUS = {"free": "✓ СВОБОДЕН", "taken": "✕ ЗАНЯТ",
          "fragment": "◇ ПРОДАЁТСЯ НА FRAGMENT",
          "invalid": "○ НЕДОПУСТИМЫЙ ФОРМАТ",
          "flood": "○ ЛИМИТ TELEGRAM, ПОВТОРИ ПОЗЖЕ",
          "error": "○ НЕ УДАЛОСЬ ПРОВЕРИТЬ"}


def bar(v: float, total: float, width: int = 8) -> str:
    total = max(total, 1)
    f = max(0, min(width, round(width * v / total)))
    return "█" * f + "░" * (width - f)


def head(icon: str, text: str, sub: str = "") -> str:
    """Иконка-аргумент игнорируется: монохром — маркер ▮ у всех заголовков."""
    s = f"▮ <b>{text.upper()}</b>"
    if sub:
        s += f"\n<i>{sub}</i>"
    return s + f"\n{HR}\n"


def quota_line(left, lim) -> str:
    return "∞ БЕЗ ЛИМИТА" if left is None else f"{bar(left, lim)}  <b>{left}</b>/{lim}"


def left_str(sec: float) -> str:
    sec = max(0, int(sec))
    d, h, m = sec // 86400, sec % 86400 // 3600, sec % 3600 // 60
    return f"{d} д {h} ч" if d else f"{h} ч {m} мин" if h else f"{max(m, 1)} мин"


def main_caption() -> str:
    return (head("▮", "SourceSearch", "система поиска юзернеймов") +
            "<blockquote>ПОИСК · ОЦЕНКА · ТРЕКЕР · УЛЬТРА-НИКИ\n"
            "БЕЗ ЛИМИТОВ. БЕЗ ЖАЛОСТИ.</blockquote>\n\n"
            "▼ Разделы ниже")


def search_home(u, left, lim, prem, fast_note="", interval_note="") -> str:
    return (head("▮", "Охота за юзернеймами") +
            f"▸ Подборы     {quota_line(left, lim)}\n"
            f"▸ Быстрый     {fast_note}\n"
            f"▸ Интервал     {interval_note or '—'}\n"
            f"▸ Алмазы        💎 <b>{u['diamonds']}</b>\n"
            f"▸ Монеты        🪙 <b>{u['coins']}</b>" + ("\n\n<i>Подписка активна</i>" if prem else ""))


def searching(checked: int, rate: float = 0.0) -> str:
    sp = SPIN[(checked // 10) % len(SPIN)]
    tail = f"  ·  {rate:.0f}/сек" if rate else ""
    return f"{sp} <b>ИДЁТ ПОИСК</b>\nПроверено  <b>{checked}</b>{tail}"


def result_card(names, checked, elapsed, left_text, extra="") -> str:
    out = [head("▮", "Результат")]
    for n in names:
        sc = score(n)
        out.append(f"<code>@{n}</code>\n{bar(sc, 100, 10)}  <b>{sc}</b> · {tier(sc).upper()}{extra}\n")
    out.append(f"<i>проверено {checked} за {elapsed:.1f} с</i>")
    if left_text:
        out.append(f"▸ Осталось подборов  <b>{left_text}</b>")
    return "\n".join(out)


def rate_card(name: str, status: str, ai: str = "") -> str:
    sc = score(name)
    sw = split_words(name)
    word = "да" if name in WORDS else f"склейка {sw[0]} + {sw[1]}" if sw else "нет"
    s = (head("▮", "Оценка", f"@{name}") +
         f"{bar(sc, 100, 10)}  <b>{sc}/100</b> · {tier(sc).upper()}\n\n"
         f"▸ Длина           <b>{len(name)}</b>\n"
         f"▸ Читаемость   <b>{round(readability(name) * 100)}%</b>\n"
         f"▸ Слова             <b>{word}</b>\n"
         f"▸ Статус            {STATUS.get(status, '—')}")
    if ai:
        s += f"\n\n<blockquote>{ai}</blockquote>"
    return s

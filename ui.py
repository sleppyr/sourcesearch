"""Оформление сообщений: единый стиль карточек."""
from core import WORDS, readability, score, split_words, tier

HR = "┈" * 18
SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

STATUS = {"free": "🟢 свободен", "taken": "🔴 занят", "fragment": "💠 продаётся на Fragment",
          "invalid": "⚪ недопустимый формат", "flood": "⚪ лимит Telegram, повтори позже",
          "error": "⚪ не удалось проверить"}


def bar(v: float, total: float, width: int = 8) -> str:
    total = max(total, 1)
    f = max(0, min(width, round(width * v / total)))
    return "▰" * f + "▱" * (width - f)


def head(icon: str, text: str, sub: str = "") -> str:
    s = f"{icon} <b>{text}</b>"
    if sub:
        s += f"\n<i>{sub}</i>"
    return s + f"\n{HR}\n"


def quota_line(left, lim) -> str:
    return "∞ без лимита" if left is None else f"{bar(left, lim)}  <b>{left}</b> из {lim}"


def left_str(sec: float) -> str:
    sec = max(0, int(sec))
    d, h, m = sec // 86400, sec % 86400 // 3600, sec % 3600 // 60
    return f"{d} д {h} ч" if d else f"{h} ч {m} мин" if h else f"{max(m, 1)} мин"


def main_caption() -> str:
    return (head("◆", "SourceSearch", "поиск свободных юзернеймов") +
            "<blockquote>⚡ быстрый поиск  ·  📊 оценка\n☁ трекер  ·  💎 ультра-ники  ·  🏆 розыгрыши</blockquote>\n\n"
            "Выбери раздел ниже")


def search_home(u, left, lim, prem) -> str:
    return (head("🎯", "Охота за юзернеймами") +
            f"▸ Подборов     {quota_line(left, lim)}\n"
            f"▸ Алмазы        💎 <b>{u['diamonds']}</b>\n"
            f"▸ Монеты        🪙 <b>{u['coins']}</b>" + ("\n\n👑 <i>Premium активен</i>" if prem else ""))


def searching(checked: int, rate: float = 0.0) -> str:
    sp = SPIN[(checked // 10) % len(SPIN)]
    tail = f"  ·  {rate:.0f}/сек" if rate else ""
    return f"{sp} <b>Идёт поиск</b>\nПроверено  <b>{checked}</b>{tail}"


def result_card(names, checked, elapsed, left_text, extra="") -> str:
    out = [head("✨", "Найдено")]
    for n in names:
        sc = score(n)
        out.append(f"<code>@{n}</code>\n{bar(sc, 100, 10)}  <b>{sc}</b> · {tier(sc)}{extra}\n")
    out.append(f"⚡ <i>проверено {checked} за {elapsed:.1f} с</i>")
    if left_text:
        out.append(f"▸ Осталось подборов  <b>{left_text}</b>")
    return "\n".join(out)


def rate_card(name: str, status: str, ai: str = "") -> str:
    sc = score(name)
    sw = split_words(name)
    word = "да" if name in WORDS else f"склейка {sw[0]} + {sw[1]}" if sw else "нет"
    s = (head("📊", "Оценка", f"@{name}") +
         f"{bar(sc, 100, 10)}  <b>{sc}/100</b> · {tier(sc)}\n\n"
         f"▸ Длина            <b>{len(name)}</b>\n"
         f"▸ Читаемость    <b>{round(readability(name) * 100)}%</b>\n"
         f"▸ Слова             <b>{word}</b>\n"
         f"▸ Статус            {STATUS.get(status, '—')}")
    if ai:
        s += f"\n\n<blockquote>{ai}</blockquote>"
    return s

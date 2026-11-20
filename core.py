"""БД, генератор юзернеймов, словари и оценка."""
import datetime
import os
import random
import re
import sqlite3
import time
from pathlib import Path

import config as cfg

USERNAME_RE = re.compile(r"^[a-z][a-z0-9_]{4,31}$")
CONS = "bcdfghjklmnprstvwz"
VOW = "aeiou"


def clean(s: str) -> str:
    s = (s or "").strip().lower()
    for p in ("https://t.me/", "http://t.me/", "t.me/", "@"):
        if s.startswith(p):
            s = s[len(p):]
    return s


def valid(name: str) -> bool:
    return bool(USERNAME_RE.match(name)) and "__" not in name and not name.endswith("_")


# ───────────────────────── словари ─────────────────────────
_BASE_WORDS = """
angel atlas blaze brave bravo cobra comet crown cyber delta ember focus ghost giant
glory grand honey ivory jewel karma laser lemon lunar magic mango mirage noble nova ocean
omega orbit pearl pilot pixel prime pulse quest rapid raven rebel royal scout shade
solar spark storm tiger titan ultra vapor viper vivid wizard zebra zenith
anchor beacon bishop bronze castle cipher dragon eagle falcon frozen galaxy golden hunter
jungle knight legend marble matrix meteor mirror mystic nebula online phoenix planet
rocket shadow silver spirit sunset thunder turbo velvet violet wander whisper yellow
""".split()

# короткие слова для «склейки» (ультра-ники из двух настоящих слов)
SHORT = """
go my up on in no so we be by do to ok hi me it is
air art ash bat bay bee big bit boy bro bug bus cat cub cup day dog dot eye fan fix fly fox fun gem
gun hat hot ice ink jet joy key lab lap log man map mud net new nut oak oil old one owl pen pig pro
ray red rep rid sea sky sun tea ten tin top toy war wax web win wolf zen zip
blue cool dark dust fast fire fish flow gold hero king leaf lion lord luck mind moon nova rain rock
sand snow star wave wild wind wise gray jade lime mint rose ruby sage
""".split()

SHORT = sorted({w for w in SHORT if w.isalpha()})


def _load_words() -> set[str]:
    words = {w for w in _BASE_WORDS if valid(w)}
    extra = Path(__file__).with_name("words.txt")  # свой словарь: по слову в строке
    if extra.exists():
        for line in extra.read_text(encoding="utf-8").splitlines():
            w = line.strip().lower()
            if valid(w):
                words.add(w)
    return words


WORDS = _load_words()
_SHORT_SET = set(SHORT)


def split_words(n: str) -> tuple[str, str] | None:
    """Если ник — склейка двух слов из SHORT, вернуть пару."""
    for i in range(2, len(n) - 1):
        if n[:i] in _SHORT_SET and n[i:] in _SHORT_SET:
            return n[:i], n[i:]
    return None


# ───────────────────────── генерация ─────────────────────────
# «брендовые» части: склейки дают ники вида voxset, zenlyx, luxora, apexon
BLEND_A = """
ax ex ix ox av ev az ez oz ze zo za va ky vy zy ly my ny sy
vox vex zex nex lex rex dex tex hex jax zax kax
aer neo zio xio kai zai lux luz viz vaz voz kaz koz zor vor kor zyr lyr myr
apex atom aura byte core cyan echo flux jade kryo lumi luna nano nova onyx opal pyro
volt wave zero zulu astra astro cyber crypt hyper lumen nexus pixel sonic titan
ultra vapor zenit luxor cobalt photon vertex vortex zenith aurora quantum
""".split()

BLEND_B = """
ix ax ex ox yx us is os on io ia al el ol ar er or an en in un yn ac ic oc
ry ly ny ty sy xy zy ky vy my by cy dy gy
son ran dan den tel vel nel mel xel zel kel rel
rix nix mix vix zix lux flux ryn lyn kyn vyn syn tyn
set lex vox zen ora vex nex zed xor zor var ver vil vol sal sel ton lon don mon
""".split()

# слоговые кластеры: согласные могут идти подряд (xset, ndr, str), как в реальных брендах
_ONSET = ("b c d f g j k l m n p r s t v x z br cr dr fr gr pr tr gl fl pl bl kl sc sp sk "
          "sl sm sn st str vr qu xy zy vy ky ly my sy ny ty").split()
_NUC = ("a e i o u y ai ei io ia ea eo au ou").split()
_CODA = ["", "", "", "x", "z", "n", "r", "l", "s", "t", "k", "d", "m",
         "st", "sk", "nt", "ns", "rt", "ls", "rx", "nx", "lt", "nd", "mp"]

_BLEND_FITS: dict[int, list[tuple[str, str]]] = {}


def _blend_fits(need: int) -> list[tuple[str, str]]:
    if need not in _BLEND_FITS:
        _BLEND_FITS[need] = [(a, b) for a in BLEND_A for b in BLEND_B
                             if len(a) + len(b) == need] if need >= 4 else []
    return _BLEND_FITS[need]


# все осмысленные брендовые склейки — для бонуса к оценке
_BLEND_NAMES = {a + b for a in BLEND_A for b in BLEND_B
                if 4 <= len(a) + len(b) <= 9 and valid(a + b)}


def _gen_blend(length: int, pin: str) -> str | None:
    fits = _blend_fits(length - len(pin))
    if not fits:
        return None
    a, b = random.choice(fits)
    return pin + a + b


def _gen_syllables(length: int, pin: str) -> str:
    out = pin
    for _ in range(40):
        rem = length - len(out)
        if rem <= 0:
            break
        chunk = random.choice(_ONSET) + random.choice(_NUC) + random.choice(_CODA)
        if len(chunk) > rem:                       # не влезает — берём короче
            chunk = chunk[:2] if rem >= 2 else chunk[:1]
        out += chunk
    return out[:length]


def gen_one(length: int, pin: str = "", digits: bool = False, underscore: bool = False) -> str:
    pin = pin[: length - 1]
    for _ in range(40):
        r = random.random()
        name = _gen_blend(length, pin) if r < 0.55 else _gen_syllables(length, pin)
        if name is None:
            continue
        s = list(name)
        if digits and length >= 5 and random.random() < 0.5:
            for i in range(random.choice((1, 2))):
                s[-1 - i] = random.choice("0123456789")
        if underscore and length >= 6 and random.random() < 0.4:
            s[random.randint(max(2, len(pin)), length - 3)] = "_"
        name = "".join(s)
        if valid(name):
            return name
    return "".join(random.choice(CONS if i % 2 == 0 else VOW) for i in range(length))


def word_variants(word: str) -> list[str]:
    w = word
    out = {w + "x", w + "z", w + "o", w + "ly", w + "io", w + w[-1], "my" + w, "the" + w,
           "get" + w, "its" + w, w + "hq", w + "app", w + "_", "_" + w,
           re.sub("[aeiou]", "", w), w[:-1], w[::-1],
           w.replace("a", "4").replace("e", "3").replace("o", "0").replace("i", "1")}
    out |= {w + str(i) for i in (1, 7, 24, 77, 99)}
    return sorted(v for v in out if valid(v) and v != w)


_GLUE_CACHE: dict[int, list[str]] = {}


def ultra_candidates(n: int, limit: int = 160) -> list[str]:
    """Кандидаты длины n: склейки настоящих слов и стилизации словарных слов. Лучшие по оценке — первыми."""
    if n not in _GLUE_CACHE:
        _GLUE_CACHE[n] = [a + b for a in SHORT for b in SHORT
                          if len(a) + len(b) == n and a != b and valid(a + b)]
    out = set(random.sample(_GLUE_CACHE[n], min(len(_GLUE_CACHE[n]), limit)))
    fits = [a + b for a, b in _blend_fits(n)]          # брендовые склейки вида voxset
    out |= set(random.sample(fits, min(len(fits), limit)))
    for w in WORDS:
        if len(w) == n:
            out.add(w)
        elif len(w) == n - 1:
            out |= {w + c for c in "xyzosk"} | {w + w[-1]}
        elif len(w) == n + 1:
            out.add(w[:-1])
            out.add(re.sub("[aeiou]", "", w, count=1))
    out = [x for x in out if valid(x) and len(x) == n]
    out.sort(key=lambda x: (-score(x), random.random()))
    return out


# ───────────────────────── оценка ─────────────────────────
def readability(n: str) -> float:
    pairs = [(a, b) for a, b in zip(n, n[1:]) if a.isalpha() and b.isalpha()]
    return sum((a in VOW) != (b in VOW) for a, b in pairs) / len(pairs) if pairs else 0.0


def score(n: str) -> int:
    L = len(n)
    s = {5: 48, 6: 40, 7: 30, 8: 22, 9: 15}.get(L, 8)
    s += round(readability(n) * 20)
    if n in WORDS:
        s += 25
    elif split_words(n):
        s += 20
    elif n in _BLEND_NAMES:
        s += 15
    if n == n[::-1]:
        s += 8
    if len(set(n)) <= L // 2 + 1:
        s += 5
    s += min(6, 2 * len(set(n) & set("xvzqkjy")))   # стильные «брендовые» буквы
    s -= 6 * sum(c.isdigit() for c in n)
    s -= 10 * ("_" in n)
    return max(1, min(100, s))


def tier(sc: int) -> str:
    return ("Топ" if sc >= 85 else "Сильный" if sc >= 70
            else "Нормальный" if sc >= 50 else "Слабый")


# ───────────────────────── БД ─────────────────────────
class DB:
    def __init__(self, path: str = "sourcesearch.db"):
        folder = os.path.dirname(path)
        if folder:
            try:
                os.makedirs(folder, exist_ok=True)
                if not os.access(folder, os.W_OK):
                    raise OSError("нет прав на запись")
            except OSError as e:
                print(f"[БД] папка {folder} недоступна ({e}). Использую локальный файл: данные пропадут "
                      f"при перезапуске. Подключите Volume на {folder}.")
                path = "sourcesearch.db"
        self.c = sqlite3.connect(path, check_same_thread=False)
        self.c.row_factory = sqlite3.Row
        self.c.executescript(f"""
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY, premium_until INTEGER DEFAULT 0,
            diamonds INTEGER DEFAULT {cfg.START_DIAMONDS}, coins INTEGER DEFAULT 0,
            pin TEXT DEFAULT '', digits INTEGER DEFAULT 0, underscore INTEGER DEFAULT 0,
            found INTEGER DEFAULT 0, created INTEGER);
        CREATE TABLE IF NOT EXISTS usage(user_id INTEGER, bucket TEXT, ts INTEGER);
        CREATE TABLE IF NOT EXISTS tracker(
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, username TEXT,
            fast INTEGER DEFAULT 0, UNIQUE(user_id, username));
        CREATE TABLE IF NOT EXISTS favs(user_id INTEGER, username TEXT, ts INTEGER, UNIQUE(user_id, username));
        CREATE TABLE IF NOT EXISTS promos(code TEXT PRIMARY KEY, kind TEXT, amount INTEGER, uses INTEGER);
        CREATE TABLE IF NOT EXISTS promo_used(code TEXT, user_id INTEGER, UNIQUE(code, user_id));
        CREATE TABLE IF NOT EXISTS giveaways(
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT, prize TEXT, ends INTEGER,
            status TEXT DEFAULT 'active', winner INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS gparts(gid INTEGER, user_id INTEGER, UNIQUE(gid, user_id));
        """)
        for col, ddl in (("ref_by", "INTEGER DEFAULT 0"), ("ref_count", "INTEGER DEFAULT 0"),
                         ("last_daily", "INTEGER DEFAULT 0"), ("streak", "INTEGER DEFAULT 0"),
                         ("banned", "INTEGER DEFAULT 0"), ("searches", "INTEGER DEFAULT 0"),
                         ("trial_used", "INTEGER DEFAULT 0")):
            try:
                self.c.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError:
                pass
        self.c.commit()

    # ── пользователи
    def exists(self, uid: int) -> bool:
        return self.c.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone() is not None

    def user(self, uid: int) -> sqlite3.Row:
        self.c.execute("INSERT OR IGNORE INTO users(id, created) VALUES(?,?)", (uid, int(time.time())))
        self.c.commit()
        return self.c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()

    @staticmethod
    def is_premium(u) -> bool:
        return u["id"] in cfg.ADMIN_IDS or u["premium_until"] > time.time()

    def set(self, uid: int, **kw):
        self.user(uid)
        cols = ", ".join(f"{k}=?" for k in kw)
        self.c.execute(f"UPDATE users SET {cols} WHERE id=?", (*kw.values(), uid))
        self.c.commit()

    def add(self, uid: int, col: str, n: int):
        assert col in ("diamonds", "coins", "found", "ref_count", "searches")
        self.user(uid)
        self.c.execute(f"UPDATE users SET {col}={col}+? WHERE id=?", (n, uid))
        self.c.commit()

    def add_premium(self, uid: int, days: int):
        u = self.user(uid)
        self.set(uid, premium_until=int(max(time.time(), u["premium_until"]) + days * 86400))

    def used(self, uid: int, bucket: str) -> int:
        return self.c.execute("SELECT COUNT(*) FROM usage WHERE user_id=? AND bucket=? AND ts>?",
                              (uid, bucket, time.time() - 86400)).fetchone()[0]

    def add_usage(self, uid: int, bucket: str):
        self.c.execute("INSERT INTO usage VALUES(?,?,?)", (uid, bucket, int(time.time())))
        self.c.commit()

    def stats(self) -> dict:
        q = lambda sql, *a: self.c.execute(sql, a).fetchone()[0]
        return {"users": q("SELECT COUNT(*) FROM users"),
                "premium": q("SELECT COUNT(*) FROM users WHERE premium_until>?", time.time()),
                "today": q("SELECT COUNT(*) FROM users WHERE created>?", time.time() - 86400),
                "found": q("SELECT COALESCE(SUM(found),0) FROM users"),
                "tracked": q("SELECT COUNT(*) FROM tracker"),
                "banned": q("SELECT COUNT(*) FROM users WHERE banned=1")}

    def all_ids(self) -> list[int]:
        return [r[0] for r in self.c.execute("SELECT id FROM users WHERE banned=0")]

    # ── ежедневный бонус
    def claim_daily(self, uid: int):
        u = self.user(uid)
        today = datetime.date.today().toordinal()
        if u["last_daily"] == today:
            return None
        streak = u["streak"] + 1 if u["last_daily"] == today - 1 else 1
        coins = cfg.DAILY_COINS[(streak - 1) % len(cfg.DAILY_COINS)]
        diamonds = 1 if streak % 7 == 0 else 0
        self.set(uid, last_daily=today, streak=streak)
        self.add(uid, "coins", coins)
        if diamonds:
            self.add(uid, "diamonds", diamonds)
        return {"streak": streak, "coins": coins, "diamonds": diamonds}

    # ── трекер
    def track_list(self, uid: int):
        return self.c.execute("SELECT * FROM tracker WHERE user_id=? ORDER BY id", (uid,)).fetchall()

    def track_add(self, uid: int, name: str, fast: bool) -> bool:
        try:
            self.c.execute("INSERT INTO tracker(user_id, username, fast) VALUES(?,?,?)", (uid, name, int(fast)))
            self.c.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def track_del(self, uid: int, tid: int):
        self.c.execute("DELETE FROM tracker WHERE id=? AND user_id=?", (tid, uid))
        self.c.commit()

    def track_all(self):
        return self.c.execute("SELECT * FROM tracker").fetchall()

    # ── избранное
    def fav_list(self, uid: int) -> list[str]:
        return [r[0] for r in self.c.execute(
            "SELECT username FROM favs WHERE user_id=? ORDER BY ts DESC", (uid,))]

    def fav_add(self, uid: int, name: str) -> bool:
        try:
            self.c.execute("INSERT INTO favs VALUES(?,?,?)", (uid, name, int(time.time())))
            self.c.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def fav_clear(self, uid: int):
        self.c.execute("DELETE FROM favs WHERE user_id=?", (uid,))
        self.c.commit()

    # ── промокоды
    def promo_create(self, code: str, kind: str, amount: int, uses: int):
        self.c.execute("INSERT OR REPLACE INTO promos VALUES(?,?,?,?)", (code.upper(), kind, amount, uses))
        self.c.commit()

    def promo_redeem(self, uid: int, code: str):
        code = code.strip().upper()
        p = self.c.execute("SELECT * FROM promos WHERE code=?", (code,)).fetchone()
        if not p or p["uses"] <= 0:
            return None
        try:
            self.c.execute("INSERT INTO promo_used VALUES(?,?)", (code, uid))
        except sqlite3.IntegrityError:
            return "used"
        self.c.execute("UPDATE promos SET uses=uses-1 WHERE code=?", (code,))
        self.c.commit()
        if p["kind"] == "premium":
            self.add_premium(uid, p["amount"])
        else:
            self.add(uid, p["kind"], p["amount"])
        return p

    # ── розыгрыши
    def gv_create(self, title: str, prize: str, hours: float) -> int:
        cur = self.c.execute("INSERT INTO giveaways(title, prize, ends) VALUES(?,?,?)",
                             (title, prize, int(time.time() + hours * 3600)))
        self.c.commit()
        return cur.lastrowid

    def gv_active(self):
        return self.c.execute("SELECT * FROM giveaways WHERE status='active' ORDER BY ends").fetchall()

    def gv_get(self, gid: int):
        return self.c.execute("SELECT * FROM giveaways WHERE id=?", (gid,)).fetchone()

    def gv_join(self, gid: int, uid: int) -> bool:
        try:
            self.c.execute("INSERT INTO gparts VALUES(?,?)", (gid, uid))
            self.c.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def gv_parts(self, gid: int) -> list[int]:
        return [r[0] for r in self.c.execute("SELECT user_id FROM gparts WHERE gid=?", (gid,))]

    def gv_due(self):
        return self.c.execute("SELECT * FROM giveaways WHERE status='active' AND ends<=?",
                              (int(time.time()),)).fetchall()

    def gv_finish(self, gid: int, winner: int):
        self.c.execute("UPDATE giveaways SET status='done', winner=? WHERE id=?", (winner, gid))
        self.c.commit()

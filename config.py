import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
ADMIN_IDS = {8517129681} | {int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}

# ── Пул проверки (чем больше воркеров, тем быстрее поиск) ──────────────────
# SESSIONS: аккаунты-проверяльщики (обычные аккаунты, НЕ боты), через запятую.
#   имя            — использует общие API_ID/API_HASH
#   имя:id:hash    — свои API-данные для этой сессии
# Вход выполняется один раз:  python login.py имя
# STRING_SESSIONS: готовые строки сессий (для Railway/серверов, где нельзя войти интерактивно)
STRING_SESSIONS = [s.strip() for s in os.getenv("STRING_SESSIONS", "").split(",") if s.strip()]
DB_PATH = os.getenv("DB_PATH", "sourcesearch.db")   # на Railway: /data/sourcesearch.db (Volume)
SESSIONS = [s.strip() for s in os.getenv("SESSIONS", "sourcesearch").split(",") if s.strip()]
# RESOLVER_BOTS: дополнительные токены ботов. Они очень быстро отсеивают занятые ники.
RESOLVER_BOTS = [s.strip() for s in os.getenv("RESOLVER_BOTS", "").split(",") if s.strip()]
USE_MAIN_BOT_AS_RESOLVER = os.getenv("USE_MAIN_BOT_AS_RESOLVER", "1") == "1"

# ── Необязательная нейро-оценка (Claude API) ─────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
AI_MODEL = os.getenv("AI_MODEL", "claude-haiku-5-5")

# ── Лимиты Free (за 24 часа) ─────────────────────────────────────────────
NO_LIMITS = os.getenv("NO_LIMITS", "1") == "1"   # 1 = отключить все лимиты подборов
FREE_LIMITS = {"5": 5, "6": 15, "custom": 5}     # используются только при NO_LIMITS=0
FREE_RESULTS = 3               # сколько ников за один подбор
PREMIUM_RESULTS = 3
FREE_PIN_LETTERS = 1
PREMIUM_PIN_LETTERS = 3
FREE_TRACK_SLOTS = 3
PREMIUM_TRACK_SLOTS = 50
FREE_FAV_SLOTS = 10
PREMIUM_FAV_SLOTS = 100

# ── Экономика ────────────────────────────────────────────────────────────
START_DIAMONDS = 5
ULTRA_COST = {5: 2, 6: 1}
ULTRA_MIN_SCORE = {5: 70, 6: 60}
DAILY_COINS = [1, 1, 2, 2, 3, 3, 5]   # награда по дням серии, на 7-й день ещё +1 алмаз
COINS_PER_DIAMOND = 20
REF_REWARD = 2                 # алмазов пригласившему
REF_INVITEE_BONUS = 1          # алмазов новичку

# ── Подписки (Telegram Stars, XTR) ───────────────────────────────────────
# (ключ, короткая метка, название, дней, звёзды, метка выгоды)
PREMIUM_PLANS = [
    ("trial", "1 ДЕНЬ",  "Проба на 1 день",        1,     19, "один раз"),
    ("d7",    "7 ДНЕЙ",  "Подписка на 7 дней",     7,     50, ""),
    ("d14",   "14 ДНЕЙ", "Подписка на 14 дней",    14,    75, ""),
    ("d30",   "30 ДНЕЙ", "Подписка на 30 дней",    30,   100, ""),
    ("m3",    "3 МЕС",   "Подписка на 3 месяца",   90,   350, ""),
    ("m6",    "6 МЕС",   "Подписка на 6 месяцев",  180,  400, "выгодно"),
    ("m12",   "12 МЕС",  "Подписка на 12 месяцев", 365,  550, "цена/день ниже всех"),
    ("life",  "НАВСЕГДА", "Пожизненный доступ",    36500, 999, "максимум"),
]
PREMIUM_PLANS_D = {p[0]: p for p in PREMIUM_PLANS}
LIFETIME_DAYS = 36500
DIAMOND_PACK = 10
DIAMOND_PACK_STARS = int(os.getenv("DIAMOND_PACK_STARS", "50"))

TRACK_INTERVAL = 30            # секунд между тиками трекера
TRACK_SLOW_EVERY = 6           # обычные ники проверяются раз в N тиков, «ловушка» — каждый тик

# ── Темп запросов (защита от FloodWait; при флуде растёт автоматически) ───
RESOLVE_PACE = float(os.getenv("RESOLVE_PACE", "1.0"))   # сек между resolveUsername по всему пулу
CONFIRM_PACE = float(os.getenv("CONFIRM_PACE", "1.5"))   # сек между account.checkUsername

# ── Быстрый поиск ────────────────────────────────────────────────────────
FAST_COST = 5                 # 💎 за прогон для тех, у кого нет права бесплатного
FAST_MIN_DAYS = 30            # тарифы от этого срока получают быстрый поиск бесплатно

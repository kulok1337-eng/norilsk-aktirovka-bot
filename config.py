"""Настройки бота. Значения читаются из переменных окружения хостинга."""
import os
from datetime import timedelta, timezone


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# ВНИМАНИЕ: значения ниже попали в публичный репозиторий.
# Любой, кто видит этот файл, может управлять ботом. Это осознанное решение владельца.
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip() or "9208172272:AAGd1_rqG9YKWlI4yW7G7B31nBENExE_bqc"
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip() or "-1001595469954"
ADMIN_ID = os.getenv("ADMIN_ID", "").strip() or "8079727075,1979046241"
ADMIN_IDS = [part.strip() for part in ADMIN_ID.replace(";", ",").split(",") if part.strip()]
DRY_RUN = os.getenv("DRY_RUN", "false").strip().lower() in ("1", "true", "yes", "on")
DAY_INTERVAL = max(60, _int("DAY_INTERVAL", 900))
NIGHT_INTERVAL = max(60, _int("NIGHT_INTERVAL", 3600))
ACTIVE_FROM = _int("ACTIVE_FROM", 5)
ACTIVE_TO = _int("ACTIVE_TO", 15)
DATA_DIR = os.getenv("DATA_DIR", "./data").strip() or "./data"

# Норильск живёт в UTC+7 круглый год, перехода на летнее время нет.
NORILSK_TZ = timezone(timedelta(hours=7))

# Порядок важен: в этом же порядке районы выводятся в сообщении.
DISTRICTS = [
    ("Центральный район", r"Центральный\s+район"),
    ("ж/о Оганер", r"(?:(?:ж/о|жилое\s+образование|жилой\s+район)\s*)?Оганер"),
    ("район Талнах", r"(?:район\s+)?Талнах"),
    ("район Кайеркан", r"(?:район\s+)?Кайеркан"),
]

SOURCES = [
    {
        "name": "Официальный сайт Норильска",
        "official": True,
        "url": os.getenv("OFFICIAL_URL", "https://xn--h1aecgfmj1g.xn--p1ai/weather/"),
        "display_url": "https://норильск.рф/weather/",
    },
    {
        "name": "Северный город",
        "official": False,
        "url": os.getenv("SECONDARY_URL", "https://sgnorilsk.ru/pogoda/"),
        "display_url": "https://sgnorilsk.ru/pogoda/",
    },
]

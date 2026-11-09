import os

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHANNEL_ID = os.environ.get("CHANNEL_ID", "").strip()
ADMIN_IDS = [
    int(v)
    for v in os.environ.get("ADMIN_IDS", "").replace(" ", ",").split(",")
    if v.strip().isdigit()
]
ADMIN_ID = (
    str(ADMIN_IDS[0]) if ADMIN_IDS else os.environ.get("ADMIN_ID", "").strip()
)

DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
NORILSK_TZ_OFFSET_HOURS = int(os.environ.get("NORILSK_TZ_OFFSET_HOURS", "7"))
DAY_INTERVAL = int(os.environ.get("DAY_INTERVAL", "900"))
NIGHT_INTERVAL = int(os.environ.get("NIGHT_INTERVAL", "3600"))
ACTIVE_FROM = int(os.environ.get("ACTIVE_FROM", "5"))
ACTIVE_TO = int(os.environ.get("ACTIVE_TO", "15"))
DATA_DIR = os.environ.get("DATA_DIR", "./data")
ACTIVE_HOURS = (ACTIVE_FROM, ACTIVE_TO)

OFFICIAL_URL = os.environ.get(
    "OFFICIAL_URL", "https://норильск.рф/weather/"
)
SECONDARY_URL = os.environ.get(
    "SECONDARY_URL", "https://sgnorilsk.ru/pogoda/"
)

SOURCES = [
    {"name": "Официальный сайт Норильска", "url": OFFICIAL_URL},
    {"name": "Северный город", "url": SECONDARY_URL},
]

DISTRICTS = [
    ("Центральный район", r"центральн\w*\s+район"),
    ("Оганер", r"(?:ж\s*/\s*о\s+)?оганер"),
    ("Талнах", r"(?:район\s+)?талнах"),
    ("Кайеркан", r"(?:район\s+)?кайеркан"),
]

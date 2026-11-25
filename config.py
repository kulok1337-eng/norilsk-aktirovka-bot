import os
from datetime import timezone, timedelta
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
CHANNEL_ID = os.getenv("CHANNEL_ID", "").strip()
ADMIN_ID = os.getenv("ADMIN_ID", "").strip()
DRY_RUN = os.getenv("DRY_RUN", "true").strip().lower() == "true"
DAY_INTERVAL = max(60, int(os.getenv("DAY_INTERVAL", "900")))
NIGHT_INTERVAL = max(60, int(os.getenv("NIGHT_INTERVAL", "3600")))
ACTIVE_FROM = int(os.getenv("ACTIVE_FROM", "5"))
ACTIVE_TO = int(os.getenv("ACTIVE_TO", "15"))
DATA_DIR = os.getenv("DATA_DIR", "./data")
NORILSK_TZ = timezone(timedelta(hours=7))
SOURCES = [
    {"name": "Официальный сайт Норильска", "official": True,
     "url": os.getenv("OFFICIAL_URL", "https://xn--h1aecgfmj1g.xn--p1ai/weather/")},
    {"name": "Северный город", "official": False,
     "url": os.getenv("SECONDARY_URL", "https://sgnorilsk.ru/pogoda/")},
]

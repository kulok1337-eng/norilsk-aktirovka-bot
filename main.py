import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
import requests
import config as cfg
from parser import parse_page, fingerprint, format_report

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

class Bot:
    def __init__(self):
        root = Path(cfg.DATA_DIR)
        root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(root / "state.sqlite3")
        self.db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.db.commit()
        self.web = requests.Session()
        self.web.headers["User-Agent"] = "NorilskAktirovkaBot/1.1"
        self.tg = requests.Session()

    def get(self, key):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def put(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, str(value)))
        self.db.commit()

    def api(self, method, payload):
        try:
            response = self.tg.post(
                f"https://api.telegram.org/bot{cfg.BOT_TOKEN}/{method}",
                json=payload, timeout=35,
            )
        except requests.RequestException:
            raise RuntimeError("Нет соединения с Telegram") from None
        try:
            data = response.json()
        except ValueError:
            raise RuntimeError(f"Telegram вернул HTTP {response.status_code}") from None
        if not data.get("ok"):
            raise RuntimeError("Telegram: " + str(data.get("description", "ошибка")))
        return data["result"]

    def send(self, chat, text):
        return self.api("sendMessage", {
            "chat_id": chat, "text": text[:4000],
            "link_preview_options": {"is_disabled": True},
        })

    def notify(self, key, text):
        key = "notice:" + key
        if time.time() - float(self.get(key) or 0) < 21600:
            return
        try:
            self.send(cfg.ADMIN_ID, text)
            self.put(key, time.time())
        except RuntimeError as error:
            logging.error("Уведомление не доставлено: %s", error)

    def check(self):
        now = datetime.now(cfg.NORILSK_TZ)
        today = now.date().isoformat()
        reports = []
        for source in cfg.SOURCES:
            try:
                response = self.web.get(source["url"], timeout=30)
                response.raise_for_status()
                report = parse_page(response.content, source)
                if report["date"] != today:
                    logging.info("%s: пропуск даты %s", source["name"], report["date"])
                    if cfg.ACTIVE_FROM <= now.hour < cfg.ACTIVE_TO and now.hour >= 8:
                        self.notify("stale:" + source["name"],
                            f"На сайте «{source['name']}» нет решения за сегодня. "
                            f"Найдена дата {report['date']}. Старые данные не опубликованы.")
                    continue
                reports.append(report)
            except (requests.RequestException, ValueError) as error:
                detail = str(error) if isinstance(error, ValueError) else type(error).__name__
                logging.warning("Источник %s: %s", source["name"], detail)
                self.notify("source:" + source["name"],
                    f"Не удалось проверить «{source['name']}».\nПричина: {detail}\n"
                    "Это не означает, что актировки нет.\n" + source["url"])
        if not reports:
            return
        chosen = reports[0]
        for other in reports[1:]:
            if other["shift"] == chosen["shift"] and fingerprint(other) != fingerprint(chosen):
                self.notify(f"conflict:{today}:{chosen['shift']}",
                    "Источники расходятся. Приоритет — официальный сайт.\n\n"
                    + format_report(chosen) + "\n\nВторой источник:\n" + format_report(other))
        mode = "test" if cfg.DRY_RUN else "live"
        key = f"report:{mode}:{cfg.CHANNEL_ID}:{today}:{chosen['shift']}"
        previous_raw = self.get(key)
        previous = json.loads(previous_raw) if previous_raw else None
        if previous and previous["official"] and not chosen["source"]["official"]:
            logging.info("Резерв не заменяет официальное решение")
            return
        digest = fingerprint(chosen)
        official = bool(chosen["source"]["official"])
        if previous and previous["digest"] == digest:
            if official and not previous["official"]:
                previous["official"] = True
                self.put(key, json.dumps(previous))
            logging.info("Изменений нет")
            return
        text = format_report(chosen, updated=previous is not None)
        target = cfg.ADMIN_ID if cfg.DRY_RUN else cfg.CHANNEL_ID
        if cfg.DRY_RUN:
            text = "ТЕСТ — в канал не отправлено\n\n" + text
        result = self.send(target, text)
        self.put(key, json.dumps({"digest": digest, "official": official,
                                  "message_id": result["message_id"]}))
        logging.info("Сводка отправлена, режим %s", mode)

    def run(self):
        me = self.api("getMe", {})
        logging.info("Бот @%s запущен. DRY_RUN=%s", me.get("username"), cfg.DRY_RUN)
        logging.info("База: %s", str(Path(cfg.DATA_DIR).resolve() / "state.sqlite3"))
        if not cfg.DRY_RUN:
            chat = self.api("getChat", {"chat_id": cfg.CHANNEL_ID})
            if chat.get("type") != "channel":
                raise RuntimeError("CHANNEL_ID должен указывать на канал, а не группу")
            member = self.api("getChatMember", {"chat_id": cfg.CHANNEL_ID, "user_id": me["id"]})
            if member.get("status") != "administrator" or not member.get("can_post_messages"):
                raise RuntimeError("У бота нет прав администратора на публикацию в канале")
        self.notify("startup:" + str(cfg.DRY_RUN),
            "Бот актировок запущен.\n" + (
                "Тестовый режим: сводки приходят только тебе."
                if cfg.DRY_RUN else "Рабочий режим: публикация в канал включена."
            ))
        while True:
            try:
                self.check()
            except Exception as error:
                logging.error("Ошибка цикла: %s", type(error).__name__)
                self.notify("cycle", "Ошибка проверки или отправки: " + str(error)[:500])
            now = datetime.now(cfg.NORILSK_TZ)
            active = cfg.ACTIVE_FROM <= now.hour < cfg.ACTIVE_TO
            interval = cfg.DAY_INTERVAL if active else cfg.NIGHT_INTERVAL
            morning = now.replace(hour=cfg.ACTIVE_FROM, minute=0, second=0, microsecond=0)
            if now < morning:
                interval = min(interval, max(1, int((morning - now).total_seconds())))
            logging.info("Следующая проверка через %s секунд", interval)
            time.sleep(interval)

if __name__ == "__main__":
    if not cfg.BOT_TOKEN or not cfg.CHANNEL_ID or not cfg.ADMIN_ID:
        raise SystemExit("Заполни BOT_TOKEN, CHANNEL_ID и ADMIN_ID в переменных BotHost")
    if not 0 <= cfg.ACTIVE_FROM < cfg.ACTIVE_TO <= 23:
        raise SystemExit("Требуется 0 <= ACTIVE_FROM < ACTIVE_TO <= 23")
    Bot().run()

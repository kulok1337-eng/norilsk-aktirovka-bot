"""Бот, который публикует в канал решения об актировках в Норильске."""
import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import requests

import config as cfg
from parser import fingerprint, format_report, load_source

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


class Bot:
    def __init__(self):
        root = Path(cfg.DATA_DIR)
        root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(root / "state.sqlite3")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self.db.commit()
        self.web = requests.Session()
        self.tg = requests.Session()

    # --- состояние -----------------------------------------------------
    def get(self, key):
        row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def put(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, str(value)))
        self.db.commit()

    # --- Telegram ------------------------------------------------------
    def api(self, method, payload):
        last_error = None
        for attempt in range(3):
            try:
                response = self.tg.post(
                    TELEGRAM_API.format(token=cfg.BOT_TOKEN, method=method),
                    json=payload,
                    timeout=35,
                )
            except requests.RequestException:
                last_error = "Нет соединения с Telegram"
                time.sleep(2 ** attempt)
                continue

            if response.status_code == 429:
                try:
                    retry_after = int(
                        response.json().get("parameters", {}).get("retry_after", 5)
                    )
                except (ValueError, TypeError, AttributeError):
                    retry_after = 5
                time.sleep(min(max(retry_after, 1), 60))
                last_error = "Telegram ограничил частоту запросов"
                continue

            try:
                data = response.json()
            except ValueError:
                raise RuntimeError(f"Telegram вернул HTTP {response.status_code}") from None
            if not data.get("ok"):
                raise RuntimeError("Telegram: " + str(data.get("description", "ошибка")))
            return data["result"]

        raise RuntimeError(last_error or "Telegram недоступен")

    def send(self, chat, text):
        return self.api(
            "sendMessage",
            {
                "chat_id": chat,
                "text": text[:4000],
                "link_preview_options": {"is_disabled": True},
            },
        )

    def notify(self, key, text):
        key = "notice:" + key
        if time.time() - float(self.get(key) or 0) < 21600:
            return
        delivered = False
        for admin in cfg.ADMIN_IDS:
            try:
                self.send(admin, text)
                delivered = True
            except RuntimeError as error:
                logging.error("Уведомление %s не доставлено: %s", admin, error)
        if delivered:
            self.put(key, time.time())

    # --- проверка ------------------------------------------------------
    def check(self):
        now = datetime.now(cfg.NORILSK_TZ)
        today = now.date().isoformat()
        reports = []
        for source in cfg.SOURCES:
            try:
                report = load_source(self.web, source)
            except (requests.RequestException, ValueError) as error:
                detail = str(error) if isinstance(error, ValueError) else type(error).__name__
                logging.warning("Источник %s: %s", source["name"], detail)
                self.notify(
                    "source:" + source["name"],
                    f"Не удалось проверить «{source['name']}».\n"
                    f"Причина: {detail}\n"
                    "Это не означает, что актировки нет.\n" + source["url"],
                )
                continue

            if report["date"] != today:
                logging.info("%s: пропуск даты %s", source["name"], report["date"])
                # Резерв часто обновляется с задержкой — не засоряем уведомления.
                # До 08:00 решение на сегодня может быть ещё не опубликовано.
                if (
                    source["official"]
                    and now.hour >= 8
                    and cfg.ACTIVE_FROM <= now.hour < cfg.ACTIVE_TO
                ):
                    self.notify(
                        "stale:" + source["name"],
                        f"На сайте «{source['name']}» нет решения за сегодня. "
                        f"Найдена дата {report['date']}. Старые данные не опубликованы.",
                    )
                continue
            reports.append(report)

        if not reports:
            return

        chosen = reports[0]
        for other in reports[1:]:
            if other["shift"] == chosen["shift"] and fingerprint(other) != fingerprint(chosen):
                self.notify(
                    f"conflict:{today}:{chosen['shift']}",
                    "Источники расходятся. Приоритет — официальный сайт.\n\n"
                    + format_report(chosen)
                    + "\n\nВторой источник:\n"
                    + format_report(other),
                )

        mode = "test" if cfg.DRY_RUN else "live"
        channel_key = cfg.CHANNEL_ID or "test"
        key = f"report:{mode}:{channel_key}:{today}:{chosen['shift']}"
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
        if cfg.DRY_RUN:
            text = "ТЕСТ — в канал не отправлено\n\n" + text
            targets = cfg.ADMIN_IDS
        else:
            targets = [cfg.CHANNEL_ID]
        message_id = None
        delivered = False
        for target in targets:
            try:
                result = self.send(target, text)
                message_id = result.get("message_id", message_id)
                delivered = True
            except RuntimeError as error:
                logging.error("Сводка не отправлена %s: %s", target, error)
        if not delivered:
            raise RuntimeError("Не удалось отправить сводку ни одному получателю")
        self.put(
            key,
            json.dumps(
                {"digest": digest, "official": official, "message_id": message_id}
            ),
        )
        logging.info("Сводка отправлена, режим %s", mode)

    # --- цикл ----------------------------------------------------------
    def run(self):
        me = self.api("getMe", {})
        logging.info("Бот @%s запущен. DRY_RUN=%s", me.get("username"), cfg.DRY_RUN)
        logging.info("База: %s", str(Path(cfg.DATA_DIR).resolve() / "state.sqlite3"))
        if not cfg.DRY_RUN:
            chat = self.api("getChat", {"chat_id": cfg.CHANNEL_ID})
            if chat.get("type") != "channel":
                raise RuntimeError("CHANNEL_ID должен указывать на канал, а не на группу")
            member = self.api(
                "getChatMember", {"chat_id": cfg.CHANNEL_ID, "user_id": me["id"]}
            )
            if member.get("status") not in ("administrator", "creator") or not member.get(
                "can_post_messages"
            ):
                raise RuntimeError("У бота нет прав администратора на публикацию в канале")
        self.notify(
            "startup:" + str(cfg.DRY_RUN),
            "Бот актировок запущен.\n"
            + (
                "Тестовый режим: сводки приходят только тебе."
                if cfg.DRY_RUN
                else "Рабочий режим: публикация в канал включена."
            ),
        )
        while True:
            try:
                self.check()
            except Exception as error:  # noqa: BLE001 - цикл не должен падать
                logging.exception("Ошибка цикла")
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
    if not cfg.BOT_TOKEN or not cfg.ADMIN_IDS:
        raise SystemExit("Не задан BOT_TOKEN или ADMIN_ID")
    if not cfg.DRY_RUN and not cfg.CHANNEL_ID:
        raise SystemExit("Для публикации в канал задай CHANNEL_ID")
    if not 0 <= cfg.ACTIVE_FROM < cfg.ACTIVE_TO <= 23:
        raise SystemExit("Требуется 0 <= ACTIVE_FROM < ACTIVE_TO <= 23")
    if not cfg.SOURCES:
        raise SystemExit("Список SOURCES пуст")
    Bot().run()

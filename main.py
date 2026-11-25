"""Бот, который публикует в канал решения об актировках в Норильске."""
import json
import logging
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

import config as cfg
from admin import AdminPanel
from parser import fingerprint, format_report, load_source

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


class Bot:
    def __init__(self):
        root = Path(cfg.DATA_DIR)
        root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(root / "state.sqlite3", check_same_thread=False)
        self.db_lock = threading.Lock()
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        self.db.commit()
        self.web = requests.Session()
        self.tg = requests.Session()
        self.wake = threading.Event()
        self.source_status: dict[str, dict] = {}
        self.panel: AdminPanel | None = None

    # --- состояние -----------------------------------------------------
    def get(self, key):
        with self.db_lock:
            row = self.db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def put(self, key, value):
        with self.db_lock:
            self.db.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, str(value)))
            self.db.commit()

    # --- настройки, изменяемые из админ-панели -------------------------
    def setting(self, key, default):
        value = self.get("setting:" + key)
        return default if value is None else value

    def set_setting(self, key, value):
        self.put("setting:" + key, value)
        self.wake.set()

    def dry_run(self) -> bool:
        default = "true" if cfg.DRY_RUN else "false"
        return self.setting("dry_run", default).strip().lower() in ("1", "true", "yes", "on")

    def channel_id(self) -> str:
        return self.setting("channel", cfg.CHANNEL_ID).strip()

    def admin_ids(self) -> list[str]:
        raw = self.setting("admins", ",".join(cfg.ADMIN_IDS))
        return [part.strip() for part in raw.replace(";", ",").split(",") if part.strip()]

    def paused(self) -> bool:
        return self.setting("paused", "false").strip().lower() in ("1", "true", "yes", "on")

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
        for admin in self.admin_ids():
            try:
                self.send(admin, text)
                delivered = True
            except RuntimeError as error:
                logging.error("Уведомление %s не доставлено: %s", admin, error)
        if delivered:
            self.put(key, time.time())

    # --- проверка ------------------------------------------------------
    def check(self):
        if self.paused():
            logging.info("Проверки на паузе")
            return
        now = datetime.now(cfg.NORILSK_TZ)
        today = now.date().isoformat()
        self.put("last_check", time.time())
        reports = []
        for source in cfg.SOURCES:
            try:
                report = load_source(self.web, source)
            except (requests.RequestException, ValueError) as error:
                detail = str(error) if isinstance(error, ValueError) else type(error).__name__
                logging.warning("Источник %s: %s", source["name"], detail)
                self.source_status[source["name"]] = {
                    "ok": False, "detail": detail, "at": time.time()
                }
                self.notify(
                    "source:" + source["name"],
                    f"Не удалось проверить «{source['name']}».\n"
                    f"Причина: {detail}\n"
                    "Это не означает, что актировки нет.\n" + source["url"],
                )
                continue

            self.source_status[source["name"]] = {
                "ok": True, "detail": report["date"], "at": time.time()
            }
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

        dry_run = self.dry_run()
        channel = self.channel_id()
        mode = "test" if dry_run else "live"
        channel_key = channel or "test"
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
        if dry_run:
            text = "ТЕСТ — в канал не отправлено\n\n" + text
            targets = self.admin_ids()
        else:
            targets = [channel]
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
        self.put("last_post", json.dumps({"at": time.time(), "text": text}))
        logging.info("Сводка отправлена, режим %s", mode)

    # --- цикл ----------------------------------------------------------
    def _interval(self):
        now = datetime.now(cfg.NORILSK_TZ)
        active = cfg.ACTIVE_FROM <= now.hour < cfg.ACTIVE_TO
        interval = cfg.DAY_INTERVAL if active else cfg.NIGHT_INTERVAL
        morning = now.replace(hour=cfg.ACTIVE_FROM, minute=0, second=0, microsecond=0)
        if now < morning:
            interval = min(interval, max(1, int((morning - now).total_seconds())))
        return interval

    def run(self):
        me = self.api("getMe", {})
        logging.info("Бот @%s запущен. DRY_RUN=%s", me.get("username"), self.dry_run())
        logging.info("Код: админ-панель v3, polling в основном цикле")
        logging.info("База: %s", str(Path(cfg.DATA_DIR).resolve() / "state.sqlite3"))
        if not self.admin_ids():
            raise RuntimeError("ADMIN_ID не задан")

        # Панель поднимаем до проверки канала: если канал недоступен,
        # администратор сможет исправить это командами /channel и /dry_run.
        self.panel = AdminPanel(self)
        try:
            self.panel.prepare()
        except Exception as error:  # noqa: BLE001
            logging.warning("Админ-панель: не удалось подготовить polling: %s", error)

        if not self.dry_run():
            try:
                channel = self.channel_id()
                if not channel:
                    raise RuntimeError("CHANNEL_ID не задан для публикации в канал")
                chat = self.api("getChat", {"chat_id": channel})
                if chat.get("type") != "channel":
                    raise RuntimeError("CHANNEL_ID должен указывать на канал, а не на группу")
                member = self.api(
                    "getChatMember", {"chat_id": channel, "user_id": me["id"]}
                )
                if member.get("status") not in ("administrator", "creator") or not member.get(
                    "can_post_messages"
                ):
                    raise RuntimeError(
                        "У бота нет прав администратора на публикацию в канале"
                    )
            except RuntimeError as error:
                logging.error("Канал: %s", error)
                self.notify(
                    "channel",
                    "Проблема с каналом публикации.\n"
                    f"Причина: {error}\n"
                    "Исправь командой /channel <id> или включи тест: /dry_run on",
                )

        self.notify(
            "startup:" + str(self.dry_run()),
            "Бот актировок запущен.\n"
            + (
                "Тестовый режим: сводки приходят только админам в личку."
                if self.dry_run()
                else "Рабочий режим: публикация в канал включена."
            )
            + "\n\nОткрой личку с ботом и отправь /start — там админ-панель.",
        )
        logging.info("Админ-панель: ожидаю команды в личке @%s", me.get("username"))

        next_check = 0.0
        while True:
            if self.wake.is_set():
                self.wake.clear()
                next_check = 0.0

            if next_check <= time.time() and not self.paused():
                try:
                    self.check()
                except Exception as error:  # noqa: BLE001 - цикл не должен падать
                    logging.exception("Ошибка цикла")
                    self.notify(
                        "cycle", "Ошибка проверки или отправки: " + str(error)[:500]
                    )
                next_check = time.time() + self._interval()
                logging.info("Следующая проверка через %s секунд", self._interval())

            try:
                self.panel.poll_once(timeout=5)
            except Exception as error:  # noqa: BLE001
                logging.warning("Админ-панель: getUpdates: %s", error)
                time.sleep(3)


if __name__ == "__main__":
    if not cfg.BOT_TOKEN:
        raise SystemExit("Не задан BOT_TOKEN")
    Bot().run()

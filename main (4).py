import hashlib
import json
import logging
import os
import re
import sqlite3
import time
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

import config as C
from parser import load_source

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

TZ = timezone(timedelta(hours=C.NORILSK_TZ_OFFSET_HOURS))
Path(C.DATA_DIR).mkdir(parents=True, exist_ok=True)
db = sqlite3.connect(Path(C.DATA_DIR) / "state.sqlite3", check_same_thread=False)
db.execute("CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
db.commit()
session = requests.Session()
session.headers["User-Agent"] = "NorilskAktirovkaBot/2.0"

source_status = {}   # name -> строка статуса
last_check = {"ts": None}


# ---------- состояние ----------
db_lock = threading.RLock()
job_lock = threading.Lock()
source_session = requests.Session()
source_session.headers["User-Agent"] = "NorilskAktirovkaBot/2.1"
cached_reports = []

def get_state(key, default=None):
    with db_lock:
        row = db.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

def set_state(key, value):
    with db_lock:
        db.execute("INSERT OR REPLACE INTO state(key,value) VALUES(?,?)", (key, str(value)))
        db.commit()

def is_dry():
    return get_state("dry_run", "true" if C.DRY_RUN else "false") == "true"


def is_paused():
    return get_state("paused", "false") == "true"


def now():
    return datetime.now(TZ)


# ---------- Telegram ----------
def tg(method, payload=None, timeout=35):
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{C.BOT_TOKEN}/{method}",
            json=payload or {}, timeout=timeout,
        )
    except requests.RequestException:
        raise RuntimeError("Ошибка соединения с Telegram") from None
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(f"Telegram вернул HTTP {r.status_code}") from None
    if not data.get("ok"):
        raise RuntimeError("Telegram: " + data.get("description", "ошибка"))
    return data["result"]


def send(chat_id, text, markup=None):
    p = {"chat_id": chat_id, "text": text[:4000],
         "link_preview_options": {"is_disabled": True}}
    if markup:
        p["reply_markup"] = markup
    return tg("sendMessage", p)


def edit(chat_id, message_id, text, markup=None):
    p = {"chat_id": chat_id, "message_id": message_id, "text": text[:4000],
         "link_preview_options": {"is_disabled": True}}
    if markup:
        p["reply_markup"] = markup
    try:
        tg("editMessageText", p)
    except RuntimeError as e:
        if "not modified" not in str(e):
            raise


def notify_admins(text, key=None, cooldown=6 * 3600):
    if key:
        if time.time() - float(get_state("n:" + key, 0)) < cooldown:
            return
        set_state("n:" + key, time.time())
    for a in C.ADMIN_IDS:
        try:
            send(a, text)
        except Exception as e:
            logging.error("Админ %s недоступен: %s", a, e)


# ---------- отчёты ----------
def norm(s):
    return re.sub(r"\s+", " ", s).strip()


def fingerprint(rep):
    data = {"date": rep["date"], "shift": rep["shift"],
            "d": {k: norm(v).lower().replace("ё", "е") for k, v in rep["districts"].items()}}
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def fmt(rep, updated=False):
    d = datetime.strptime(rep["date"], "%Y-%m-%d").strftime("%d.%m.%Y")
    lines = ["Уточнение об актировках" if updated else "Актировки в Норильске",
             f"Дата: {d}", f"Смена: {rep['shift']}", ""]
    for name, _ in C.DISTRICTS:
        v = rep["districts"][name]
        if v.lower().strip(" .") == "нет":
            v = "актировки нет"
        lines.append(f"{name} — {v}")
    lines += ["", f"Источник: {rep['source']['name']}", rep["source"]["url"]]
    return "\n".join(lines)


def collect():
    """Загружает оба источника. Возвращает список отчётов за сегодня."""
    global cached_reports
    today = now().date()
    reports = []
    for src in C.SOURCES:
        try:
            rep = load_source(source_session, src)
            rd = datetime.strptime(rep["date"], "%Y-%m-%d").date()
            if rd != today:
                source_status[src["name"]] = f"Ожидаем сводку за {today:%d.%m.%Y}; на сайте — {rd:%d.%m.%Y}"
                continue
            source_status[src["name"]] = f"ок: {rep['date']}, смена {rep['shift']}"
            reports.append(rep)
        except Exception as e:
            msg = str(e) if isinstance(e, ValueError) else type(e).__name__
            source_status[src["name"]] = f"ошибка: {msg}"
            logging.warning("%s: %s", src["name"], msg)
            notify_admins(
                f"Не удалось проверить «{src['name']}»: {msg}\n"
                "Это не означает, что актировки нет.", key="src:" + src["name"])
    cached_reports = reports
    return reports


def check_and_publish(force_to=None):
    last_check["ts"] = now()
    reports = collect()
    if not reports:
        return None
    chosen = reports[0]
    for other in reports[1:]:
        if other["shift"] == chosen["shift"] and fingerprint(other) != fingerprint(chosen):
            notify_admins("Источники расходятся.\nИспользуется официальный сайт.\n\n"
                          + fmt(chosen) + "\n\nВторой источник:\n" + fmt(other),
                          key=f"conf:{chosen['date']}:{chosen['shift']}")
    if force_to:
        send(force_to, fmt(chosen))
        return chosen

    dry = is_dry()
    mode = "test" if dry else "live"
    key = f"report:{mode}:{C.CHANNEL_ID}:{chosen['date']}:{chosen['shift']}"
    digest = fingerprint(chosen)
    prev = get_state(key)
    if prev == digest or get_state(f"{key}:seen:{digest}"):
        return chosen
    text = fmt(chosen, updated=prev is not None)
    if dry:
        for a in C.ADMIN_IDS:
            send(a, "ТЕСТ — в канал не отправлено\n\n" + text)
    else:
        send(C.CHANNEL_ID, text)
        notify_admins("Опубликовано в канал:\n\n" + text)
    set_state(key, digest)
    set_state(f"{key}:seen:{digest}", "1")
    return chosen


def start_check(chat_id=None, force_to=None):
    if not job_lock.acquire(blocking=False):
        if chat_id:
            send(chat_id, "Проверка уже идёт. Дождись результата.")
        return False
    def work():
        try:
            rep = check_and_publish(force_to=force_to)
            if chat_id:
                prefix = "Отправлено в канал.\n\n" if force_to and rep else "Проверка завершена.\n\n"
                send(chat_id, prefix + (fmt(rep) if rep else "Актуальной сводки нет.\n\n" + status_text()))
        except Exception:
            logging.exception("Ошибка фоновой проверки")
            if chat_id:
                try:
                    send(chat_id, "Ошибка проверки или отправки. Подробности в логах BotHost.")
                except Exception:
                    logging.exception("Не удалось отправить сообщение об ошибке")
        finally:
            job_lock.release()
    threading.Thread(target=work, daemon=True).start()
    return True

# ---------- админ-панель ----------
def kb(rows):
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in r] for r in rows]}


def panel_text():
    lines = ["Панель администратора", ""]
    lines.append("Режим: " + ("ТЕСТ (только админам)" if is_dry() else "РАБОЧИЙ (публикация в канал)"))
    lines.append("Автопроверка: " + ("на паузе" if is_paused() else "включена"))
    lc = last_check["ts"]
    lines.append("Последняя проверка: " + (lc.strftime("%d.%m %H:%M") if lc else "ещё не было"))
    lines.append("Время в Норильске: " + now().strftime("%d.%m %H:%M"))
    lines.append("Канал: " + C.CHANNEL_ID)
    return "\n".join(lines)


def panel_markup():
    return kb([
        [("Проверить сейчас", "check"), ("Статус сайтов", "status")],
        [("Показать актировки", "show"), ("Отправить в канал вручную", "push")],
        [("Режим: тест/рабочий", "mode"), ("Пауза/пуск", "pause")],
        [("Проверить доступ к каналу", "chan"), ("Сбросить историю", "reset")],
        [("Обновить панель", "panel")],
    ])


def status_text():
    lines = ["Состояние источников:", "Время Норильска: " + now().strftime("%d.%m.%Y %H:%M"), "Проверка: " + ("идёт" if job_lock.locked() else "не выполняется"), "Последний запуск: " + (last_check["ts"].strftime("%d.%m %H:%M") if last_check["ts"] else "ещё не было")]
    for s in C.SOURCES:
        lines.append(f"- {s['name']}: {source_status.get(s['name'], 'не проверялся')}")
    return "\n".join(lines)


def show_panel(chat_id, message_id=None):
    if message_id:
        edit(chat_id, message_id, panel_text(), panel_markup())
    else:
        send(chat_id, panel_text(), panel_markup())


def handle_callback(cb):
    uid = cb["from"]["id"]
    chat_id = cb["message"]["chat"]["id"]
    mid = cb["message"]["message_id"]
    if uid not in C.ADMIN_IDS:
        tg("answerCallbackQuery", {"callback_query_id": cb["id"], "text": "Нет доступа"})
        return
    try:
        tg("answerCallbackQuery", {"callback_query_id": cb["id"]})
    except Exception:
        logging.warning("Не удалось подтвердить нажатие", exc_info=True)
    data = cb["data"]
    answer = ""
    try:
        if data == "panel":
            pass
        elif data == "check":
            start_check(chat_id)
        elif data == "status":
            send(chat_id, status_text())
        elif data == "show":
            reps = [r for r in cached_reports if r["date"] == now().date().isoformat()]
            send(chat_id, fmt(reps[0]) if reps else "В кэше нет сегодняшней сводки. Нажми «Проверить сейчас».\n\n" + status_text())
        elif data == "push":
            edit(chat_id, mid, "Отправить актуальную сводку в канал сейчас?",
                 kb([[("Да, отправить", "push_yes"), ("Отмена", "panel")]]))
            return
        elif data == "push_yes":
            start_check(chat_id, force_to=C.CHANNEL_ID)
        elif data == "mode":
            set_state("dry_run", "false" if is_dry() else "true")
        elif data == "pause":
            set_state("paused", "false" if is_paused() else "true")
        elif data == "chan":
            me = tg("getMe")
            m = tg("getChatMember", {"chat_id": C.CHANNEL_ID, "user_id": me["id"]})
            ok = m.get("status") in ("administrator", "creator")
            answer = "Доступ есть: " + m.get("status") if ok else "Бот не админ канала"
        elif data == "reset":
            edit(chat_id, mid, "Сбросить историю публикаций? Бот может повторно опубликовать сегодняшнее решение.",
                 kb([[("Да, сбросить", "reset_yes"), ("Отмена", "panel")]]))
            return
        elif data == "reset_yes":
            if job_lock.locked():
                raise RuntimeError("Дождись завершения проверки")
            with db_lock:
                db.execute("DELETE FROM state WHERE key LIKE 'report:%'")
                db.commit()
            answer = "История сброшена"
    except Exception as e:
        answer = "Ошибка: " + str(e)[:150]
    if answer:
        send(chat_id, answer)
    edit(chat_id, mid, panel_text(), panel_markup())


def handle_message(msg):
    uid = msg.get("from", {}).get("id")
    chat = msg["chat"]["id"]
    text = (msg.get("text") or "").strip()
    if chat < 0:
        return
    if uid not in C.ADMIN_IDS:
        if text.startswith("/start"):
            send(chat, "Этот бот публикует актировки в канале. Управление доступно только администраторам.")
        return
    if text.startswith(("/start", "/panel", "/menu")):
        show_panel(chat)
    elif text.startswith("/status"):
        send(chat, status_text())
    elif text.startswith("/check"):
        start_check(chat)
    else:
        send(chat, "Команды: /panel — панель, /status — источники, /check — проверить сейчас.")


# ---------- главный цикл ----------
def interval():
    h = now().hour
    return C.DAY_INTERVAL if C.ACTIVE_FROM <= h < C.ACTIVE_TO else C.NIGHT_INTERVAL


def main():
    if not C.BOT_TOKEN or not C.CHANNEL_ID or not C.ADMIN_IDS:
        raise RuntimeError("Заполни BOT_TOKEN, CHANNEL_ID и ADMIN_IDS в переменных окружения")
    me = tg("getMe")
    logging.info("Бот @%s запущен, DRY_RUN=%s", me["username"], is_dry())
    try:
        tg("deleteWebhook")
    except Exception:
        pass
    try:
        m = tg("getChatMember", {"chat_id": C.CHANNEL_ID, "user_id": me["id"]})
        chan = "доступ к каналу есть (" + m.get("status", "?") + ")"
    except Exception as e:
        chan = "нет доступа к каналу: " + str(e)
    notify_admins("Бот запущен. " + chan + "\nОткрой /panel", key="startup", cooldown=60)

    offset = None
    next_check = 0
    while True:
        if not is_paused() and time.time() >= next_check:
            start_check()
            next_check = time.time() + interval()
        try:
            updates = tg("getUpdates", {"offset": offset, "timeout": 20,
                                        "allowed_updates": ["message", "callback_query"]}, timeout=40)
        except Exception as e:
            logging.warning("getUpdates: %s", e)
            time.sleep(5)
            continue
        for u in updates:
            offset = u["update_id"] + 1
            try:
                if "callback_query" in u:
                    handle_callback(u["callback_query"])
                elif "message" in u:
                    handle_message(u["message"])
            except Exception as e:
                logging.error("Ошибка обработки: %s", e)


if __name__ == "__main__":
    main()

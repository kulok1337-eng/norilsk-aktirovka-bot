"""Админ-панель в Telegram: команды и кнопки управления ботом."""
import json
import logging
import re
from datetime import datetime

import config as cfg

HELP_TEXT = (
    "Команды:\n"
    "/status — состояние бота и источников\n"
    "/check — проверить сайты сейчас\n"
    "/dry_run on|off — тест в личку или публикация в канал\n"
    "/channel <id> — сменить канал (-100... или @username)\n"
    "/pause — приостановить проверки\n"
    "/resume — возобновить проверки\n"
    "/admins — список администраторов\n"
    "/add_admin <id> — добавить администратора\n"
    "/del_admin <id> — удалить администратора\n"
    "/myid — показать ваш Telegram ID\n"
    "/help — эта справка"
)


class AdminPanel:
    def __init__(self, bot):
        self.bot = bot
        try:
            self.offset = int(bot.get("admin:offset") or 0)
        except (TypeError, ValueError):
            self.offset = 0

    # --- long polling, вызывается из основного цикла -------------------
    def prepare(self):
        self.bot.api("deleteWebhook", {"drop_pending_updates": True})
        self.offset = 0
        self.bot.put("admin:offset", 0)
        logging.info("Админ-панель: long polling запущен")

    def poll_once(self, timeout=5):
        payload = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if self.offset:
            payload["offset"] = self.offset
        updates = self.bot.api("getUpdates", payload)
        if updates:
            logging.info("Админ-панель: получено обновлений: %s", len(updates))
        for update in updates:
            self.offset = update["update_id"] + 1
            self.bot.put("admin:offset", self.offset)
            try:
                self.handle(update)
            except Exception:  # noqa: BLE001
                logging.exception("Админ-панель: ошибка обработки команды")
        return len(updates)

    # --- разбор обновлений ---------------------------------------------
    def handle(self, update):
        if "message" in update:
            self._on_message(update["message"])
        elif "callback_query" in update:
            self._on_callback(update["callback_query"])

    def _is_admin(self, user_id):
        return str(user_id) in self.bot.admin_ids()

    def _on_message(self, message):
        chat = message.get("chat", {})
        if chat.get("type") != "private":
            return
        text = (message.get("text") or "").strip()
        if not text.startswith("/"):
            return

        head, _, args = text.partition(" ")
        command = head[1:].split("@")[0].lower()
        args = args.strip()
        chat_id = chat.get("id")
        user_id = message.get("from", {}).get("id")
        is_admin = self._is_admin(user_id)
        logging.info("Админ-панель: /%s от %s", command, user_id)

        if command == "start":
            self._send_welcome(chat_id, is_admin)
            return
        if command == "help":
            self._send(chat_id, HELP_TEXT)
            return
        if command == "myid":
            self._send(chat_id, f"Ваш Telegram ID: {user_id}")
            return
        if not is_admin:
            self._send(chat_id, "Управление ботом доступно только администратору.")
            return

        if command == "status":
            self._send(chat_id, self._status_text(), self._menu())
        elif command == "check":
            if self.bot.paused():
                self._send(chat_id, "⏸ Проверки на паузе. Сначала /resume")
            else:
                self.bot.wake.set()
                self._send(
                    chat_id, "🔄 Проверка запущена. Через несколько секунд смотри /status."
                )
        elif command == "dry_run":
            self._cmd_dry_run(chat_id, args)
        elif command == "channel":
            self._cmd_channel(chat_id, args)
        elif command == "pause":
            self.bot.set_setting("paused", "true")
            self._send(chat_id, "⏸ Проверки приостановлены. Возобновить: /resume")
        elif command == "resume":
            self.bot.set_setting("paused", "false")
            self._send(chat_id, "▶️ Проверки возобновлены.")
        elif command == "admins":
            self._send(chat_id, "Администраторы:\n" + "\n".join(self.bot.admin_ids()))
        elif command == "add_admin":
            self._cmd_add_admin(chat_id, args)
        elif command == "del_admin":
            self._cmd_del_admin(chat_id, args)
        else:
            self._send(chat_id, "Неизвестная команда.\n\n" + HELP_TEXT)

    def _on_callback(self, callback):
        data = callback.get("data", "")
        chat_id = callback.get("message", {}).get("chat", {}).get("id")
        try:
            self.bot.api(
                "answerCallbackQuery", {"callback_query_id": callback["id"]}
            )
        except Exception:  # noqa: BLE001
            pass

        if not self._is_admin(callback.get("from", {}).get("id")):
            self._send(chat_id, "Нет доступа.")
            return

        if data == "status":
            self._send(chat_id, self._status_text(), self._menu())
        elif data == "check":
            if self.bot.paused():
                self._send(chat_id, "⏸ Проверки на паузе. Сначала /resume")
            else:
                self.bot.wake.set()
                self._send(chat_id, "🔄 Проверка запущена.")
        elif data == "toggle_dry":
            self.bot.set_setting("dry_run", "false" if self.bot.dry_run() else "true")
            self._send(chat_id, self._status_text(), self._menu())
        elif data == "toggle_pause":
            self.bot.set_setting("paused", "false" if self.bot.paused() else "true")
            self._send(chat_id, self._status_text(), self._menu())
        elif data == "help":
            self._send(chat_id, HELP_TEXT)
        elif data == "menu":
            self._send_welcome(chat_id, True)
        else:
            self._send(chat_id, "Кнопка устарела. Открой /start")

    # --- команды -------------------------------------------------------
    def _cmd_dry_run(self, chat_id, args):
        value = args.lower()
        if not value:
            self._send(chat_id, self._status_text(), self._menu())
            return
        if value in ("on", "вкл", "true", "1", "да"):
            self.bot.set_setting("dry_run", "true")
            self._send(chat_id, "🧪 Тестовый режим включён: сводки идут админам.")
        elif value in ("off", "выкл", "false", "0", "нет"):
            self.bot.set_setting("dry_run", "false")
            if not self.bot.channel_id():
                self._send(chat_id, "🚀 Публикация включена, но канал не задан: /channel -100...")
            else:
                self._send(chat_id, "🚀 Публикация в канал включена.")
        else:
            self._send(chat_id, "Используй: /dry_run on или /dry_run off")

    def _cmd_channel(self, chat_id, args):
        if not args:
            self._send(chat_id, "Текущий канал: " + (self.bot.channel_id() or "не задан"))
            return
        if not (re.fullmatch(r"-100\d{5,}", args) or re.fullmatch(r"@[A-Za-z0-9_]{4,}", args)):
            self._send(chat_id, "Нужен ID вида -100... или @username канала.")
            return
        try:
            chat = self.bot.api("getChat", {"chat_id": args})
        except Exception as error:  # noqa: BLE001
            self._send(
                chat_id,
                "Не удалось открыть канал.\n"
                f"Причина: {error}\n"
                "Добавь бота администратором канала и повтори.",
            )
            return
        if chat.get("type") != "channel":
            self._send(chat_id, "Это не канал. Нужен канал, а не группа.")
            return
        self.bot.set_setting("channel", args)
        self._send(chat_id, f"Канал изменён: {args}\nПроверить: /check")

    def _cmd_add_admin(self, chat_id, args):
        if not re.fullmatch(r"\d{5,15}", args):
            self._send(chat_id, "Нужен числовой ID пользователя.")
            return
        admins = self.bot.admin_ids()
        if args in admins:
            self._send(chat_id, "Такой администратор уже есть.")
            return
        admins.append(args)
        self.bot.set_setting("admins", ",".join(admins))
        self._send(chat_id, f"Администратор добавлен: {args}")

    def _cmd_del_admin(self, chat_id, args):
        admins = self.bot.admin_ids()
        if args not in admins:
            self._send(chat_id, "Такого администратора нет.")
            return
        if len(admins) == 1:
            self._send(chat_id, "Нельзя удалить последнего администратора.")
            return
        admins.remove(args)
        self.bot.set_setting("admins", ",".join(admins))
        self._send(chat_id, f"Администратор удалён: {args}")

    # --- тексты и кнопки -----------------------------------------------
    def _fmt_time(self, value):
        try:
            return datetime.fromtimestamp(float(value), cfg.NORILSK_TZ).strftime(
                "%d.%m.%Y %H:%M"
            )
        except (TypeError, ValueError, OSError):
            return "—"

    def _status_text(self):
        bot = self.bot
        mode = "тест (в личку)" if bot.dry_run() else "публикация в канал"
        last_check = bot.get("last_check")
        lines = [
            "📊 Статус бота",
            f"Режим: {mode}",
            f"Канал: {bot.channel_id() or 'не задан'}",
            f"Админы: {', '.join(bot.admin_ids())}",
            f"Проверки: {'на паузе' if bot.paused() else 'включены'}",
            f"Последняя проверка: {self._fmt_time(last_check) if last_check else 'ещё не было'}",
            "",
            "Источники:",
        ]
        for source in cfg.SOURCES:
            state = bot.source_status.get(source["name"])
            if not state:
                lines.append(f"• {source['name']}: нет данных")
            elif state.get("ok"):
                lines.append(f"• {source['name']}: дата {state['detail']}")
            else:
                lines.append(f"• {source['name']}: ошибка — {state['detail']}")

        last_post = bot.get("last_post")
        if last_post:
            try:
                posted = json.loads(last_post)
                lines += ["", f"Последний пост: {self._fmt_time(posted.get('at'))}"]
            except (ValueError, TypeError):
                pass

        lines += ["", f"Интервал: {cfg.DAY_INTERVAL} с днём / {cfg.NIGHT_INTERVAL} с ночью"]
        return "\n".join(lines)

    def _menu(self):
        dry = "✅ тест" if self.bot.dry_run() else "🚀 канал"
        pause = "▶️ продолжить" if self.bot.paused() else "⏸ пауза"
        return [
            [
                {"text": "📊 Статус", "callback_data": "status"},
                {"text": "🔄 Проверить", "callback_data": "check"},
            ],
            [
                {"text": f"Режим: {dry}", "callback_data": "toggle_dry"},
                {"text": pause, "callback_data": "toggle_pause"},
            ],
            [{"text": "⚙️ Команды", "callback_data": "help"}],
        ]

    def _send_welcome(self, chat_id, is_admin):
        if not is_admin:
            self._send(
                chat_id,
                "❄️ Бот актировок Норильска\n\n"
                "Публикует официальные решения об актировках по районам.\n"
                "Управление доступно только администратору.",
            )
            return
        text = (
            "❄️ Бот актировок Норильска\n\n"
            f"Режим: {'тест' if self.bot.dry_run() else 'публикация в канал'}\n"
            f"Канал: {self.bot.channel_id() or 'не задан'}\n\n"
            "Выбери действие:"
        )
        self._send(chat_id, text, self._menu())

    def _send(self, chat_id, text, keyboard=None):
        payload = {
            "chat_id": chat_id,
            "text": text[:4000],
            "link_preview_options": {"is_disabled": True},
        }
        if keyboard:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        try:
            self.bot.api("sendMessage", payload)
        except Exception as error:  # noqa: BLE001
            logging.error("Админ-панель: не удалось отправить ответ: %s", error)

"""Разовый помощник; не запускайте одновременно с другим polling-процессом этого бота."""
import os
import time
import requests

token = os.getenv("BOT_TOKEN", "").strip()
if not token:
    raise SystemExit("Добавь BOT_TOKEN в переменные окружения")

def api(method, payload):
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/{method}",
                          json=payload, timeout=40)
        data = r.json()
    except (requests.RequestException, ValueError):
        raise SystemExit("Ошибка соединения с Telegram; токен скрыт") from None
    if not data.get("ok"):
        raise SystemExit(str(data.get("description", "Ошибка Telegram")))
    return data["result"]

if api("getWebhookInfo", {}).get("url"):
    raise SystemExit("У бота включён webhook. Используй отдельного нового бота.")
print("Напиши боту в личку. Для ID частного канала добавь его администратором и опубликуй новый пост.", flush=True)
offset = None
while True:
    payload = {"timeout": 25, "allowed_updates": ["message", "channel_post", "my_chat_member"]}
    if offset is not None:
        payload["offset"] = offset
    for update in api("getUpdates", payload):
        offset = update["update_id"] + 1
        message = update.get("message") or update.get("channel_post") or update.get("my_chat_member")
        if message and "chat" in message:
            chat = message["chat"]
            label = "CHANNEL_ID" if chat["type"] == "channel" else "CHAT_ID / ADMIN_ID (если это твоя личка)"
            print(f"{label}: {chat['id']} | {chat.get('title', chat.get('first_name', ''))}", flush=True)
    time.sleep(1)

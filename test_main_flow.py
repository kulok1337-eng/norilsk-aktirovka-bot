"""Сквозная проверка цикла check() без обращения к настоящему Telegram."""
import json
import shutil
import threading
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import config as cfg
import main

ROOT = Path(__file__).resolve().parent
OFFICIAL = (ROOT / "fixtures" / "official_2026-11-25.html").read_bytes()
STALE = (ROOT / "fixtures" / "sgnorilsk_2026-11-01.html").read_bytes()
AKTIROVKA = OFFICIAL.replace("нет".encode("utf-8"), "1-6 классы".encode("utf-8"))


class Handler(BaseHTTPRequestHandler):
    pages: dict[str, bytes] = {}

    def do_GET(self):  # noqa: N802
        body = type(self).pages.get(self.path)
        if body is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 11, 25, 10, 0, tzinfo=tz)


class RecordingBot(main.Bot):
    def __init__(self):
        super().__init__()
        self.sent = []
        self.notices = []

    def send(self, chat, text):
        self.sent.append((chat, text))
        return {"message_id": len(self.sent)}

    def notify(self, key, text):
        self.notices.append((key, text))


class BotFlowTests(unittest.TestCase):
    def setUp(self):
        Handler.pages = {"/official": OFFICIAL, "/secondary": STALE}
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        port = self.server.server_address[1]
        self.tmp = ROOT / (".testdata-" + uuid4().hex)
        self.tmp.mkdir()
        self.old = (cfg.DATA_DIR, cfg.SOURCES, cfg.DRY_RUN, cfg.CHANNEL_ID, cfg.ADMIN_ID)
        cfg.DATA_DIR = str(self.tmp)
        cfg.DRY_RUN = False
        cfg.CHANNEL_ID = "@test_channel"
        cfg.ADMIN_ID = "42"
        cfg.SOURCES = [
            {
                "name": "Официальный",
                "official": True,
                "url": f"http://127.0.0.1:{port}/official",
                "display_url": "https://example.org/official",
            },
            {
                "name": "Резерв",
                "official": False,
                "url": f"http://127.0.0.1:{port}/secondary",
                "display_url": "https://example.org/secondary",
            },
        ]
        self.bot = RecordingBot()

    def tearDown(self):
        self.bot.db.close()
        self.server.shutdown()
        self.server.server_close()
        cfg.DATA_DIR, cfg.SOURCES, cfg.DRY_RUN, cfg.CHANNEL_ID, cfg.ADMIN_ID = self.old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self):
        with patch.object(main, "datetime", FixedDatetime):
            self.bot.check()

    def test_publish_once_and_dedupe(self):
        self.check()
        self.assertEqual(len(self.bot.sent), 1)
        chat, text = self.bot.sent[0]
        self.assertEqual(chat, "@test_channel")
        self.assertIn("25.11.2026", text)
        self.assertIn("2-я смена", text)
        self.assertNotIn("ТЕСТ", text)
        self.assertFalse(any(key.startswith("stale:") for key, _ in self.bot.notices))

        self.check()
        self.assertEqual(len(self.bot.sent), 1)

    def test_official_stale_is_reported(self):
        Handler.pages["/official"] = STALE
        self.check()
        self.assertEqual(self.bot.sent, [])
        self.assertTrue(any(key == "stale:Официальный" for key, _ in self.bot.notices))

    def test_correction_is_marked(self):
        self.check()
        Handler.pages["/official"] = AKTIROVKA
        self.check()
        self.assertEqual(len(self.bot.sent), 2)
        self.assertIn("Уточнение", self.bot.sent[1][1])
        self.assertIn("1-6 классы", self.bot.sent[1][1])

    def test_dry_run_goes_to_admin(self):
        cfg.DRY_RUN = True
        self.bot.db.close()
        self.bot = RecordingBot()
        self.check()
        self.assertEqual(len(self.bot.sent), 1)
        self.assertEqual(self.bot.sent[0][0], "42")
        self.assertTrue(self.bot.sent[0][1].startswith("ТЕСТ"))

    def test_reserve_does_not_replace_official(self):
        self.check()
        Handler.pages["/official"] = "<html><body>нет блока</body></html>".encode("utf-8")
        Handler.pages["/secondary"] = AKTIROVKA
        self.check()
        self.assertEqual(len(self.bot.sent), 1)

    def test_state_survives_restart(self):
        self.check()
        self.bot.db.close()
        self.bot = RecordingBot()
        self.check()
        self.assertEqual(len(self.bot.sent), 0)
        row = self.bot.db.execute("SELECT value FROM state WHERE key LIKE 'report:%'").fetchone()
        self.assertIn("digest", json.loads(row[0]))


if __name__ == "__main__":
    unittest.main()

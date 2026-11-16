import asyncio
import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
AI_ROOT = ROOT.parent / "ai-product"
SCOUT = ROOT / "scout"
FAKE_TOKEN = "123456789:AAFakeTokenForUnitTestOnly000"
SECRET = "unit-forward-secret-value-32ch"
WRONG_SECRET = "unit-forward-wrong-secret-value"
API_KEY = "unit-forward-api-key-value-32"
OWN = -1009101
FOREIGN = -1009109
ORDER = (
    "Нужен телеграм-бот для записи клиентов. Бюджет 25000 рублей. "
    "Пишите @secret_client_demo, звоните +7 900 555 10 20."
)


def channel_update(channel_id: int, message_id: int, text: str) -> dict:
    return {
        "update_id": message_id,
        "channel_post": {
            "message_id": message_id,
            "date": int(datetime.now(timezone.utc).timestamp()),
            "chat": {
                "id": channel_id,
                "type": "channel",
                "title": "Искусственный канал",
                "username": "demo_orders_channel",
            },
            "text": text,
        },
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        self.server.seen.append(
            {
                "path": urlparse(self.path).path,
                "header": self.headers.get("X-Telegram-Bot-Api-Secret-Token"),
                "body": body,
            }
        )
        if self.server.mode == "down":
            payload = b'{"detail":"boom"}'
            status = 500
        elif self.server.mode == "redirect":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_address[1]}/steal")
            self.end_headers()
            return
        else:
            payload = b'{"ok":true,"accepted":true,"reason":"saved"}'
            status = 200
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self.server.seen.append({"path": urlparse(self.path).path, "header": "", "body": b""})
        payload = b'{"stolen":true}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class ForwardUnitTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(ROOT))
        import channel_forward

        self.forward = channel_forward
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.seen = []
        self.server.mode = "ok"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.previous = {
            name: os.environ.get(name)
            for name in (
                "CHANNEL_FORWARD_ENABLED",
                "PRODUCT_API_BASE_URL",
                "TELEGRAM_BOT_WEBHOOK_SECRET",
            )
        }
        os.environ["PRODUCT_API_BASE_URL"] = f"http://127.0.0.1:{self.server.server_address[1]}"
        os.environ["TELEGRAM_BOT_WEBHOOK_SECRET"] = SECRET
        os.environ["CHANNEL_FORWARD_ENABLED"] = "false"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        for name, value in self.previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_disabled_mode_does_not_call_network(self):
        result = self.forward.forward_channel_update(channel_update(OWN, 1, ORDER))
        self.assertEqual(result["reason"], "disabled")
        self.assertEqual(self.server.seen, [])

    def test_user_message_is_not_forwarded(self):
        os.environ["CHANNEL_FORWARD_ENABLED"] = "true"
        result = self.forward.forward_channel_update(
            {"update_id": 2, "message": {"message_id": 2, "text": "привет", "chat": {"id": 1, "type": "private"}}}
        )
        self.assertEqual(result["reason"], "ignored")
        self.assertEqual(self.server.seen, [])

    def test_one_attempt_sends_secret_header_and_hides_it(self):
        os.environ["CHANNEL_FORWARD_ENABLED"] = "true"
        with self.assertLogs("vortex.channel_forward", level="INFO") as captured:
            self.server.mode = "down"
            failed = self.forward.forward_channel_update(channel_update(OWN, 3, ORDER))
        self.assertEqual(failed["status"], 500)
        self.assertEqual(len(self.server.seen), 1)
        self.assertEqual(self.server.seen[0]["path"], "/api/v1/telegram/webhook")
        self.assertTrue(self.server.seen[0]["header"] == SECRET)
        posted = json.loads(self.server.seen[0]["body"].decode("utf-8"))
        self.assertEqual(posted["channel_post"]["text"], ORDER)
        self.assertNotIn(SECRET, "\n".join(captured.output))
        self.assertNotIn(SECRET, json.dumps(failed))

        self.server.mode = "redirect"
        redirected = self.forward.forward_channel_update(channel_update(OWN, 4, ORDER))
        self.assertEqual(redirected["reason"], "unavailable")
        self.assertFalse(any(item["path"] == "/steal" for item in self.server.seen))

    def test_closed_port_does_not_raise_or_retry(self):
        os.environ["CHANNEL_FORWARD_ENABLED"] = "true"
        os.environ["PRODUCT_API_BASE_URL"] = "http://127.0.0.1:9"
        result = self.forward.forward_channel_update(channel_update(OWN, 5, ORDER))
        self.assertEqual(result, {"forwarded": False, "reason": "unavailable"})
        self.assertNotIn(SECRET, json.dumps(result))


class BotHandlerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["TELEGRAM_BOT_TOKEN"] = FAKE_TOKEN
        sys.path.insert(0, str(ROOT))
        import importlib.util

        spec = importlib.util.spec_from_file_location("vortex_scout_bot", SCOUT / "bot.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls.bot_module = module

    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.seen = []
        self.server.mode = "ok"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.previous = {
            name: os.environ.get(name)
            for name in ("CHANNEL_FORWARD_ENABLED", "PRODUCT_API_BASE_URL", "TELEGRAM_BOT_WEBHOOK_SECRET")
        }
        os.environ["PRODUCT_API_BASE_URL"] = f"http://127.0.0.1:{self.server.server_address[1]}"
        os.environ["TELEGRAM_BOT_WEBHOOK_SECRET"] = SECRET
        os.environ["CHANNEL_FORWARD_ENABLED"] = "true"
        self.session = _CaptureSession()
        self.bot_module.bot.session = self.session

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        for name, value in self.previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_private_commands_stay_and_are_not_parsed(self):
        source = (SCOUT / "bot.py").read_text(encoding="utf-8")
        self.assertIn("start_polling", source)
        self.assertNotIn("set_webhook", source)
        self.assertNotIn("start_webhook", source)
        self.assertNotIn(SECRET, source)
        self.assertNotIn("PRODUCT_API_KEY", (ROOT / "web" / "app.js").read_text(encoding="utf-8"))

        from aiogram.types import Update

        start = Update.model_validate(
            {
                "update_id": 11,
                "message": {
                    "message_id": 11,
                    "date": 1760000000,
                    "chat": {"id": 42, "type": "private"},
                    "from": {"id": 42, "is_bot": False, "first_name": "Тест"},
                    "text": "/start",
                },
            }
        )
        asyncio.run(self.bot_module.dp.feed_update(self.bot_module.bot, start))
        texts = [getattr(method, "text", "") for method in self.session.methods]
        self.assertTrue(any("Добро пожаловать в Vortex" in text for text in texts))
        self.assertEqual(self.server.seen, [])

        menu = self.bot_module.main_menu().inline_keyboard
        labels = [button.text for row in menu for button in row]
        self.assertEqual(
            labels,
            [
                "🚀 Открыть Vortex",
                "📋 Найти заказы",
                "🌐 Сайты и лендинги",
                "🤖 Telegram-боты",
                "🧠 ИИ и автоматизация",
                "🐍 Python и API",
            ],
        )

    def test_channel_post_is_forwarded_once_from_the_bot(self):
        from aiogram.types import Update

        update = Update.model_validate(channel_update(OWN, 21, ORDER))
        asyncio.run(self.bot_module.dp.feed_update(self.bot_module.bot, update))
        asyncio.run(self.bot_module.dp.feed_update(self.bot_module.bot, update))
        self.assertEqual(len(self.server.seen), 2)
        self.assertTrue(all(item["header"] == SECRET for item in self.server.seen))
        self.assertTrue(all(item["path"] == "/api/v1/telegram/webhook" for item in self.server.seen))


class LiveApiPathTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.live_db = AI_ROOT / "data" / "scout.db"
        cls.live_channels = AI_ROOT / "config" / "bot_channels.json"
        cls.live_db_stat = cls.live_db.stat() if cls.live_db.exists() else None
        cls.live_channels_text = cls.live_channels.read_text(encoding="utf-8")
        cls.root = Path(tempfile.mkdtemp())
        cls.db = cls.root / "forward.db"
        cls.grants = cls.root / "channels.json"
        cls.grants.write_text('{"channels": []}\n', encoding="utf-8")
        sys.path.insert(0, str(AI_ROOT))
        os.environ["PRODUCT_DEV_MODE"] = "true"
        import app.web as web
        from app.bot_channels import grant_channel
        from app.leadwatch.store import FoundLeadStore

        grant_channel(cls.grants, OWN, title="Искусственный канал", username="demo_orders_channel", scope="own_posts")
        cls.web = web
        cls.previous_settings = (
            web.settings.db_path,
            web.settings.product_api_key,
            web.settings.product_dev_mode,
            web.settings.bot_channels_path,
            web.settings.telegram_bot_webhook_secret,
            web.found_store,
        )
        web.settings.db_path = cls.db
        web.settings.product_api_key = API_KEY
        web.settings.product_dev_mode = True
        web.settings.bot_channels_path = cls.grants
        web.settings.telegram_bot_webhook_secret = SECRET
        web.found_store = FoundLeadStore(cls.db)
        cls.hits = []

        @web.app.middleware("http")
        async def count_webhooks(request, call_next):
            if request.url.path == "/api/v1/telegram/webhook":
                cls.hits.append(request.url.path)
            return await call_next(request)

        import uvicorn

        cls.server = uvicorn.Server(
            uvicorn.Config(web.app, host="127.0.0.1", port=0, log_level="error")
        )
        cls.thread = None

    @classmethod
    def tearDownClass(cls):
        if cls.thread is not None:
            cls.server.should_exit = True
            cls.thread.join(timeout=5)
        (
            cls.web.settings.db_path,
            cls.web.settings.product_api_key,
            cls.web.settings.product_dev_mode,
            cls.web.settings.bot_channels_path,
            cls.web.settings.telegram_bot_webhook_secret,
            cls.web.found_store,
        ) = cls.previous_settings

    def setUp(self):
        if LiveApiPathTests.thread is None:
            sock_port = _free_port()
            LiveApiPathTests.server.config.port = sock_port
            LiveApiPathTests.thread = threading.Thread(target=LiveApiPathTests.server.run, daemon=True)
            LiveApiPathTests.thread.start()
            _wait_until(f"http://127.0.0.1:{sock_port}/api/v1/health")
        self.port = LiveApiPathTests.server.config.port
        self.previous = {
            name: os.environ.get(name)
            for name in ("CHANNEL_FORWARD_ENABLED", "PRODUCT_API_BASE_URL", "TELEGRAM_BOT_WEBHOOK_SECRET")
        }
        os.environ["PRODUCT_API_BASE_URL"] = f"http://127.0.0.1:{self.port}"
        os.environ["TELEGRAM_BOT_WEBHOOK_SECRET"] = SECRET
        os.environ["CHANNEL_FORWARD_ENABLED"] = "false"
        sys.path.insert(0, str(ROOT))
        import channel_forward

        self.forward = channel_forward

    def tearDown(self):
        for name, value in self.previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_full_path_filters_duplicates_and_foreign_channels(self):
        self.assertEqual(json.loads(self.live_channels_text)["channels"], [])
        disabled = self.forward.forward_channel_update(channel_update(OWN, 41, ORDER))
        self.assertEqual(disabled["reason"], "disabled")
        self.assertEqual(self._jobs()["total"], 0)

        os.environ["CHANNEL_FORWARD_ENABLED"] = "true"
        os.environ["TELEGRAM_BOT_WEBHOOK_SECRET"] = WRONG_SECRET
        denied = self.forward.forward_channel_update(channel_update(OWN, 41, ORDER))
        self.assertEqual(denied["status"], 401)
        self.assertNotIn(WRONG_SECRET, json.dumps(denied))
        self.assertEqual(self._jobs()["total"], 0)

        os.environ["TELEGRAM_BOT_WEBHOOK_SECRET"] = SECRET
        foreign = self.forward.forward_channel_update(channel_update(FOREIGN, 7, ORDER))
        self.assertEqual(foreign["reason"], "not_allowed")
        accepted = self.forward.forward_channel_update(channel_update(OWN, 41, ORDER))
        repeated = self.forward.forward_channel_update(channel_update(OWN, 41, ORDER))
        self.assertEqual(accepted.get("accepted"), True)
        self.assertEqual(repeated.get("reason"), "duplicate")
        page = self._jobs()
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["items"][0]["category"], "telegram_bot")
        self.assertNotIn(SECRET, json.dumps(page))
        self.assertNotIn("@secret_client_demo", json.dumps(page))
        self.assertNotIn("+7 900 555 10 20", json.dumps(page, ensure_ascii=False))
        self.assertTrue(self.web.settings.product_dev_mode)
        self.assertEqual(self.live_channels.read_text(encoding="utf-8"), self.live_channels_text)
        if self.live_db_stat is not None:
            current = self.live_db.stat()
            self.assertEqual(current.st_size, self.live_db_stat.st_size)
            self.assertEqual(current.st_mtime_ns, self.live_db_stat.st_mtime_ns)

    def _jobs(self) -> dict:
        request_url = f"http://127.0.0.1:{self.port}/api/v1/jobs?limit=20&offset=0"
        from urllib.request import Request

        request = Request(request_url, headers={"Authorization": f"Bearer {API_KEY}"})
        with urlopen(request, timeout=8) as response:
            return json.loads(response.read().decode("utf-8"))


class _CaptureSession:
    def __init__(self):
        self.methods = []

    async def __call__(self, bot, method, timeout=None):
        self.methods.append(method)
        return True

    async def close(self):
        return None


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _wait_until(url: str) -> None:
    for _ in range(50):
        try:
            with urlopen(url, timeout=0.2) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError, OSError):
            pass
    raise RuntimeError("temporary api did not start")


if __name__ == "__main__":
    unittest.main()

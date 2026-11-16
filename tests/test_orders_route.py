import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

SECRET = "route-test-key-6c1e"
PHONE = "+79991112233"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        parsed = urlparse(self.path)
        self.server.seen.append(
            {
                "path": parsed.path,
                "query": parsed.query,
                "authorization": self.headers.get("Authorization"),
            }
        )
        if self.server.mode == "empty":
            payload = {"items": [], "limit": 20, "offset": 0, "total": 0}
            status = 200
        elif self.server.mode == "deny":
            payload = {"detail": "unauthorized", "key": SECRET, "phone": PHONE}
            status = 401
        else:
            payload = {
                "items": [
                    {
                        "id": 4,
                        "summary": "Телеграм-бот для записи",
                        "category": "telegram_bot",
                        "budget": 25000,
                        "currency": "RUB",
                        "source": "Доска заказов",
                        "url": "https://example.test/jobs/bot-4",
                        "published_at": "2026-10-16T08:00:00+00:00",
                        "phone": PHONE,
                        "text": "секретный полный текст",
                    }
                ],
                "limit": 20,
                "offset": 0,
                "total": 1,
            }
            status = 200
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class OrdersRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.seen = []
        cls.server.mode = "ok"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        os.environ["PRODUCT_API_BASE_URL"] = f"http://127.0.0.1:{cls.server.server_address[1]}"
        os.environ["PRODUCT_API_KEY"] = SECRET
        from main import app

        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)

    def test_route_returns_public_card(self):
        self.server.mode = "ok"
        response = self.client.get("/api/orders?limit=20&offset=0")
        self.assertEqual(response.status_code, 200)
        page = response.json()
        self.assertEqual(page["items"][0]["title"], "Телеграм-бот для записи")
        self.assertEqual(page["items"][0]["category"], "telegram")
        self.assertEqual(page["items"][0]["price"], "25 000 ₽")
        self.assertEqual(page["items"][0]["published_label"], "16 октября 2026")
        self.assertEqual(page["total"], 1)
        self.assertFalse(page["has_more"])
        encoded = response.text
        self.assertNotIn(SECRET, encoded)
        self.assertNotIn(PHONE, encoded)
        self.assertNotIn("секретный полный текст", encoded)
        self.assertNotIn("phone", encoded)
        seen = self.server.seen[-1]
        query = parse_qs(seen["query"])
        self.assertEqual(query["approved"], ["true"])
        self.assertEqual(query["limit"], ["20"])
        self.assertEqual(query["offset"], ["0"])
        self.assertEqual(seen["authorization"], f"Bearer {SECRET}")
        self.assertNotIn(SECRET, seen["query"])

    def test_empty_and_upstream_failure_are_safe(self):
        self.server.mode = "empty"
        empty = self.client.get("/api/orders")
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json()["items"], [])
        self.assertEqual(empty.json()["total"], 0)

        self.server.mode = "deny"
        failed = self.client.get("/api/orders")
        self.assertEqual(failed.status_code, 503)
        self.assertEqual(failed.json(), {"detail": "catalog unavailable"})
        self.assertNotIn(SECRET, failed.text)
        self.assertNotIn(PHONE, failed.text)

    def test_demo_mode_returns_local_test_orders_without_upstream(self):
        previous = os.environ.get("VORTEX_DEMO_MODE")
        os.environ["VORTEX_DEMO_MODE"] = "true"
        try:
            response = self.client.get("/api/orders?limit=3&offset=0")
        finally:
            if previous is None:
                os.environ.pop("VORTEX_DEMO_MODE", None)
            else:
                os.environ["VORTEX_DEMO_MODE"] = previous
        self.assertEqual(response.status_code, 200)
        page = response.json()
        self.assertEqual(len(page["items"]), 3)
        self.assertEqual(page["total"], 8)
        self.assertTrue(page["has_more"])
        self.assertTrue(all(item["source"] == "Тестовый заказ" for item in page["items"]))

    def test_pages_do_not_contain_the_key(self):
        for path in ("/", "/static/app.js", "/health"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertNotIn(SECRET, response.text)
            self.assertNotIn("PRODUCT_API_KEY", response.text)


if __name__ == "__main__":
    unittest.main()

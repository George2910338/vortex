import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from catalog import (
    CATEGORY_GROUPS,
    CatalogClient,
    CatalogUnavailable,
    public_item,
)

ROOT = Path(__file__).resolve().parent.parent
SECRET = "vortex-test-key-9f3a"
PHONE = "+79990001122"


def sample_jobs() -> list[dict]:
    rows = []
    stamp = 17
    for group, codes in CATEGORY_GROUPS.items():
        for index, code in enumerate(codes):
            rows.append(
                {
                    "id": len(rows) + 1,
                    "summary": f"{group} заказ {code}",
                    "category": code,
                    "budget": 1000 * (len(rows) + 1),
                    "currency": "RUB",
                    "source": "Доска заказов",
                    "url": f"https://example.test/jobs/{code}",
                    "published_at": f"2026-10-{stamp:02d}T08:00:00+00:00",
                    "priority": 9,
                    "fits": True,
                    "phone": PHONE,
                    "text": "полный текст объявления не должен уйти в Mini App",
                }
            )
            stamp = max(1, stamp - 1)
    rows.append(
        {
            "id": 99,
            "summary": "Неизвестная категория",
            "category": "other",
            "budget": None,
            "currency": None,
            "source": None,
            "url": "javascript:alert(1)",
            "published_at": None,
            "author": "hidden-author",
        }
    )
    return rows


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def _body(self, status: int, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        parsed = urlparse(self.path)
        self.server.seen.append(
            {
                "path": parsed.path,
                "query": parse_qs(parsed.query),
                "authorization": self.headers.get("Authorization"),
            }
        )
        mode = self.server.mode
        if mode == "redirect" and parsed.path == "/api/v1/jobs":
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{self.server.server_address[1]}/steal")
            self.end_headers()
            return
        if parsed.path == "/steal":
            self._body(200, b'{"stolen":true}')
            return
        if parsed.path != "/api/v1/jobs":
            self._body(404, b'{"detail":"not found"}')
            return
        if self.headers.get("Authorization") != f"Bearer {SECRET}":
            self._body(401, json.dumps({"detail": "unauthorized", "key": SECRET}).encode())
            return
        if mode == "down":
            self._body(500, json.dumps({"detail": SECRET, "phone": PHONE}).encode())
            return
        if mode == "empty":
            payload = {"items": [], "limit": 20, "offset": 0, "total": 0}
        elif mode == "unsorted":
            payload = {
                "items": [
                    {"id": 2, "summary": "Второй", "category": "landing", "budget": 10, "currency": "USD", "published_at": "2026-10-01T00:00:00+00:00"},
                    {"id": 5, "summary": "Пятый", "category": "website", "budget": 20, "currency": "USD", "published_at": "2026-10-18T00:00:00+00:00"},
                ],
                "limit": 20,
                "offset": 0,
                "total": 2,
            }
        else:
            query = parse_qs(parsed.query)
            limit = int(query.get("limit", ["20"])[0])
            offset = int(query.get("offset", ["0"])[0])
            rows = sample_jobs()
            payload = {
                "items": rows[offset:offset + limit],
                "limit": limit,
                "offset": offset,
                "total": len(rows),
            }
        self._body(200, json.dumps(payload).encode())


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.seen = []
        self.server.mode = "ok"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.client = CatalogClient(self.base, SECRET)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def test_one_request_keeps_order_and_maps_categories(self):
        page = self.client.fetch_jobs(limit=20, offset=0)
        self.assertEqual(len(self.server.seen), 1)
        seen = self.server.seen[0]
        self.assertEqual(seen["path"], "/api/v1/jobs")
        self.assertEqual(seen["query"]["approved"], ["true"])
        self.assertEqual(seen["query"]["limit"], ["20"])
        self.assertEqual(seen["query"]["offset"], ["0"])
        self.assertEqual(seen["authorization"], f"Bearer {SECRET}")
        self.assertNotIn(SECRET, seen["path"])
        self.assertNotIn("category", seen["query"])

        titles = [item["title"] for item in page["items"]]
        self.assertEqual(titles, [row["summary"] for row in sample_jobs()])
        groups = {item["category"] for item in page["items"]}
        self.assertEqual(groups, {"web", "telegram", "ai", "python", ""})
        self.assertEqual(page["total"], len(sample_jobs()))
        self.assertFalse(page["has_more"])
        self.assertEqual(page["offset"], 0)
        self.assertEqual(page["limit"], 20)

    def test_offset_page_and_preserved_sort(self):
        page = self.client.fetch_jobs(limit=2, offset=2)
        self.assertEqual([item["id"] for item in page["items"]], [3, 4])
        self.assertEqual(page["offset"], 2)
        self.assertEqual(page["next_offset"], 4)
        self.assertTrue(page["has_more"])
        self.assertEqual(self.server.seen[0]["query"]["offset"], ["2"])

        self.server.mode = "unsorted"
        ordered = self.client.fetch_jobs(limit=20, offset=0)
        self.assertEqual([item["id"] for item in ordered["items"]], [2, 5])

    def test_public_card_hides_private_fields_and_formats_money(self):
        card = public_item(sample_jobs()[0], SECRET)
        self.assertEqual(
            set(card),
            {"id", "title", "category", "price", "source", "published_label", "url"},
        )
        self.assertEqual(card["category"], "web")
        self.assertEqual(card["price"], "1 000 ₽")
        self.assertEqual(card["published_label"], "17 октября 2026")
        self.assertNotIn(PHONE, json.dumps(card))
        unknown = public_item(sample_jobs()[-1], SECRET)
        self.assertEqual(unknown["category"], "")
        self.assertEqual(unknown["price"], "Бюджет не указан")
        self.assertEqual(unknown["published_label"], "")
        self.assertEqual(unknown["url"], "")
        self.assertEqual(unknown["source"], "")

    def test_empty_catalog(self):
        self.server.mode = "empty"
        page = self.client.fetch_jobs(limit=20, offset=0)
        self.assertEqual(page["items"], [])
        self.assertEqual(page["total"], 0)
        self.assertFalse(page["has_more"])

    def test_errors_hide_upstream_body_and_secret(self):
        self.server.mode = "down"
        with self.assertRaises(CatalogUnavailable) as caught:
            self.client.fetch_jobs(limit=20, offset=0)
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertNotIn(PHONE, str(caught.exception))

        self.server.mode = "redirect"
        with self.assertRaises(CatalogUnavailable):
            self.client.fetch_jobs(limit=20, offset=0)
        self.assertFalse(any(item["path"] == "/steal" for item in self.server.seen))

        closed = CatalogClient("http://127.0.0.1:9", SECRET, timeout=1)
        with self.assertRaises(CatalogUnavailable) as refused:
            closed.fetch_jobs(limit=20, offset=0)
        self.assertNotIn(SECRET, str(refused.exception))

    def test_missing_key_does_not_call_api(self):
        client = CatalogClient(self.base, "")
        with self.assertRaises(CatalogUnavailable):
            client.fetch_jobs(limit=20, offset=0)
        self.assertEqual(self.server.seen, [])

    def test_secret_is_removed_from_public_text(self):
        card = public_item(
            {
                "id": 7,
                "summary": f"Описание {SECRET}",
                "category": "parser",
                "budget": 500,
                "currency": "USD",
                "source": SECRET,
                "url": f"https://example.test/{SECRET}",
            },
            SECRET,
        )
        encoded = json.dumps(card, ensure_ascii=False)
        self.assertNotIn(SECRET, encoded)
        self.assertEqual(card["category"], "python")
        self.assertEqual(card["price"], "500 $")

    def test_static_app_has_no_demo_orders_or_key_name(self):
        app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
        index = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
        for secret_text in (
            "PRODUCT_API_KEY",
            "Создать лендинг",
            "Разработать Telegram-бота",
            "Подключить ИИ к приложению",
            "Написать Python-скрипт",
            "Демо-заказ",
            "демонстрационные",
            SECRET,
        ):
            self.assertNotIn(secret_text, app_js)
            self.assertNotIn(secret_text, index)

    def test_env_override_is_explicit(self):
        previous = {
            "PRODUCT_API_BASE_URL": os.environ.get("PRODUCT_API_BASE_URL"),
            "PRODUCT_API_KEY": os.environ.get("PRODUCT_API_KEY"),
        }
        os.environ["PRODUCT_API_BASE_URL"] = self.base
        os.environ["PRODUCT_API_KEY"] = SECRET
        try:
            page = CatalogClient.from_env().fetch_jobs(limit=1, offset=0)
        finally:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        self.assertEqual(page["items"][0]["id"], 1)
        self.assertNotIn(SECRET, json.dumps(page))


if __name__ == "__main__":
    unittest.main()

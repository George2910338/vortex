"""Клиент каталога ai-product и публичные карточки для Mini App.

Ключ уходит только в заголовок запроса к API. В карточках и ошибках его нет.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

CATEGORY_GROUPS = {
    "web": ("website", "landing", "tilda", "wordpress", "frontend"),
    "telegram": ("telegram_bot", "telegram_mini_app"),
    "ai": ("ai_bot", "ai_agent"),
    "python": (
        "backend",
        "parser",
        "automation",
        "api_integration",
        "crm_integration",
        "mvp",
    ),
}

CATEGORY_OF_CODE = {
    code: group
    for group, codes in CATEGORY_GROUPS.items()
    for code in codes
}

CURRENCY_SYMBOLS = {
    "RUB": "₽",
    "USD": "$",
    "EUR": "€",
    "UAH": "₴",
    "PLN": "zł",
}

MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)

MAX_BODY_BYTES = 1_000_000
DEFAULT_BASE_URL = "http://127.0.0.1:8000"


class CatalogUnavailable(Exception):
    """Каталог недоступен. Текст исключения фиксированный и без секретов."""

    def __init__(self) -> None:
        super().__init__("catalog unavailable")


class _RefuseRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _clean(value: str, secret: str) -> str:
    if secret and len(secret) >= 8 and secret in value:
        return value.replace(secret, "[скрыто]")
    return value


def normalize_base_url(value: str) -> str:
    parsed = urlparse((value or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise CatalogUnavailable()
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise CatalogUnavailable()
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def safe_public_url(value: object, secret: str = "") -> str:
    if not isinstance(value, str):
        return ""
    candidate = _clean(value.strip(), secret)
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return ""
    if parsed.username or parsed.password:
        return ""
    return candidate


def format_price(budget: object, currency: object) -> str:
    if isinstance(budget, bool) or not isinstance(budget, int) or budget < 0:
        return "Бюджет не указан"
    amount = f"{budget:,}".replace(",", " ")
    code = currency.strip().upper() if isinstance(currency, str) else ""
    symbol = CURRENCY_SYMBOLS.get(code)
    if symbol:
        return f"{amount} {symbol}"
    if code:
        return f"{amount} {code}"
    return f"{amount}, валюта не указана"


def format_published(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return ""
    return f"{moment.day} {MONTHS[moment.month - 1]} {moment.year}"


def public_item(row: object, secret: str = "") -> dict | None:
    if not isinstance(row, dict):
        return None
    raw_id = row.get("id")
    if isinstance(raw_id, bool) or not isinstance(raw_id, int):
        return None
    summary = row.get("summary")
    title = summary.strip() if isinstance(summary, str) else ""
    title = _clean(title, secret) or "Заказ без описания"
    code = row.get("category") if isinstance(row.get("category"), str) else ""
    source = row.get("source").strip() if isinstance(row.get("source"), str) else ""
    return {
        "id": raw_id,
        "title": title,
        "category": CATEGORY_OF_CODE.get(code, ""),
        "price": format_price(row.get("budget"), row.get("currency")),
        "source": _clean(source, secret),
        "published_label": format_published(row.get("published_at")),
        "url": safe_public_url(row.get("url"), secret),
    }


def public_page(payload: object, *, limit: int, offset: int, secret: str = "") -> dict:
    if not isinstance(payload, dict):
        raise CatalogUnavailable()
    rows = payload.get("items")
    total = payload.get("total")
    if not isinstance(rows, list) or isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise CatalogUnavailable()
    items = [item for item in (public_item(row, secret) for row in rows) if item is not None]
    next_offset = offset + len(rows)
    has_more = bool(rows) and next_offset < total
    return {
        "items": items,
        "limit": limit,
        "offset": offset,
        "total": total,
        "next_offset": next_offset,
        "has_more": has_more,
    }


class CatalogClient:
    def __init__(self, base_url: str, api_key: str, timeout: float = 8.0) -> None:
        self.base_url = normalize_base_url(base_url)
        self.api_key = (api_key or "").strip()
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "CatalogClient":
        base_url = os.environ.get("PRODUCT_API_BASE_URL", DEFAULT_BASE_URL)
        api_key = os.environ.get("PRODUCT_API_KEY", "")
        return cls(base_url, api_key)

    def fetch_jobs(self, *, limit: int, offset: int) -> dict:
        if not self.api_key:
            raise CatalogUnavailable()
        query = urlencode(
            {
                "approved": "true",
                "limit": str(limit),
                "offset": str(offset),
            }
        )
        url = f"{self.base_url}/api/v1/jobs?{query}"
        request = Request(
            url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
            },
            method="GET",
        )
        opener = build_opener(_RefuseRedirect)
        try:
            response = opener.open(request, timeout=self.timeout)
        except HTTPError:
            raise CatalogUnavailable() from None
        except (URLError, TimeoutError, OSError, ValueError, TypeError):
            raise CatalogUnavailable() from None
        if response is None:
            raise CatalogUnavailable()
        try:
            status = getattr(response, "status", 200)
            body = response.read(MAX_BODY_BYTES + 1)
        except (URLError, TimeoutError, OSError, ValueError, TypeError):
            raise CatalogUnavailable() from None
        finally:
            response.close()
        if status != 200 or len(body) > MAX_BODY_BYTES:
            raise CatalogUnavailable()
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise CatalogUnavailable() from None
        return public_page(payload, limit=limit, offset=offset, secret=self.api_key)

"""Одна попытка передать channel_post в уже существующий вебхук ai-product.

Секрет уходит только заголовком. В журнал и в результат он не попадает.
Пока передача выключена, сеть не вызывается.
"""

from __future__ import annotations

import json
import logging
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

log = logging.getLogger("vortex.channel_forward")

HEADER = "X-Telegram-Bot-Api-Secret-Token"
DEFAULT_BASE_URL = "http://127.0.0.1:8000"
_ENABLED = {"1", "true", "yes", "on"}


class _RefuseRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def forwarding_enabled() -> bool:
    return os.environ.get("CHANNEL_FORWARD_ENABLED", "").strip().lower() in _ENABLED


def _clean(value: str) -> str:
    hidden = (
        os.environ.get("TELEGRAM_BOT_WEBHOOK_SECRET", "").strip(),
        os.environ.get("PRODUCT_API_KEY", "").strip(),
        os.environ.get("TELEGRAM_BOT_TOKEN", "").strip(),
    )
    for secret in hidden:
        if secret and len(secret) >= 8 and secret in value:
            value = value.replace(secret, "[скрыто]")
    return value


def _base_url() -> str:
    parsed = urlparse(os.environ.get("PRODUCT_API_BASE_URL", DEFAULT_BASE_URL).strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("bad base")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("bad base")
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def _public_result(status: int, body: bytes) -> dict:
    result = {"forwarded": status == 200, "status": status}
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return result
    if not isinstance(payload, dict):
        return result
    reason = payload.get("reason")
    if isinstance(reason, str) and 0 < len(reason) <= 80 and reason == _clean(reason):
        result["reason"] = reason
    if isinstance(payload.get("accepted"), bool):
        result["accepted"] = payload["accepted"]
    return result


def forward_channel_update(update: dict) -> dict:
    """Передаёт один channel_post. Не повторяет запрос и не поднимает ошибку наружу."""
    if not forwarding_enabled():
        return {"forwarded": False, "reason": "disabled"}
    if not isinstance(update, dict) or not isinstance(update.get("channel_post"), dict):
        return {"forwarded": False, "reason": "ignored"}
    secret = os.environ.get("TELEGRAM_BOT_WEBHOOK_SECRET", "").strip()
    if not secret:
        return {"forwarded": False, "reason": "not_configured"}
    try:
        url = f"{_base_url()}/api/v1/telegram/webhook"
        body = json.dumps(update, ensure_ascii=False).encode("utf-8")
        request = Request(
            url,
            data=body,
            headers={
                HEADER: secret,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        opener = build_opener(_RefuseRedirect)
        response = opener.open(request, timeout=8)
    except HTTPError as exc:
        if 300 <= exc.code < 400:
            exc.close()
            log.info("channel forward unavailable")
            return {"forwarded": False, "reason": "unavailable"}
        try:
            error_body = exc.read(8192)
        except (OSError, ValueError):
            error_body = b""
        finally:
            exc.close()
        log.info("channel forward failed status=%s", exc.code)
        return _public_result(exc.code, error_body)
    except (URLError, TimeoutError, OSError, ValueError, TypeError):
        log.info("channel forward unavailable")
        return {"forwarded": False, "reason": "unavailable"}
    if response is None:
        log.info("channel forward unavailable")
        return {"forwarded": False, "reason": "unavailable"}
    try:
        status = int(getattr(response, "status", 0) or 0)
        payload = response.read(65536)
    except (URLError, TimeoutError, OSError, ValueError, TypeError):
        log.info("channel forward unavailable")
        return {"forwarded": False, "reason": "unavailable"}
    finally:
        response.close()
    if 300 <= status < 400:
        log.info("channel forward unavailable")
        return {"forwarded": False, "reason": "unavailable"}
    if status != 200:
        log.info("channel forward failed status=%s", status)
    return _public_result(status, payload)

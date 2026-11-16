"""Local sample orders for developing the Vortex UI without ai-product.

Enabled only with VORTEX_DEMO_MODE=true. The returned page follows the existing
/api/orders response contract; no upstream service or second AI is involved.
"""

from __future__ import annotations

_DEMO_ORDERS = [
    {"id": 900001, "title": "Разработать лендинг для онлайн-школы", "category": "web", "price": "45 000 ₽", "source": "Тестовый заказ", "published_label": "16 ноября 2026", "url": "https://example.com/"},
    {"id": 900002, "title": "Создать Telegram-бота для записи клиентов", "category": "telegram", "price": "30 000 ₽", "source": "Тестовый заказ", "published_label": "15 ноября 2026", "url": "https://example.com/"},
    {"id": 900003, "title": "Автоматизировать обработку заявок с помощью AI", "category": "ai", "price": "60 000 ₽", "source": "Тестовый заказ", "published_label": "14 ноября 2026", "url": "https://example.com/"},
    {"id": 900004, "title": "Интеграция CRM с внешним API на Python", "category": "python", "price": "25 000 ₽", "source": "Тестовый заказ", "published_label": "13 ноября 2026", "url": "https://example.com/"},
    {"id": 900005, "title": "Доработать интернет-магазин и каталог", "category": "web", "price": "По договорённости", "source": "Тестовый заказ", "published_label": "12 ноября 2026", "url": "https://example.com/"},
    {"id": 900006, "title": "Бот уведомлений о новых заявках", "category": "telegram", "price": "18 000 ₽", "source": "Тестовый заказ", "published_label": "11 ноября 2026", "url": "https://example.com/"},
    {"id": 900007, "title": "Скрипт парсинга и выгрузки данных", "category": "python", "price": "12 000 ₽", "source": "Тестовый заказ", "published_label": "10 ноября 2026", "url": "https://example.com/"},
    {"id": 900008, "title": "AI-ассистент для поддержки пользователей", "category": "ai", "price": "80 000 ₽", "source": "Тестовый заказ", "published_label": "9 ноября 2026", "url": "https://example.com/"},
]


def demo_page(*, limit: int, offset: int) -> dict:
    items = _DEMO_ORDERS[offset : offset + limit]
    next_offset = offset + len(items)
    return {
        "items": items,
        "limit": limit,
        "offset": offset,
        "total": len(_DEMO_ORDERS),
        "next_offset": next_offset,
        "has_more": next_offset < len(_DEMO_ORDERS),
    }

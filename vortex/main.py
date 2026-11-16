from contextlib import asynccontextmanager
from pathlib import Path
import os
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl
from fastapi import Header, Depends
from sqlalchemy.orm import Session
from sqlalchemy import select, delete
from database.db import SessionLocal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from catalog import CatalogClient, CatalogUnavailable
from demo_catalog import demo_page
from database.db import Base, engine
from database import models

load_dotenv()


BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Vortex",
    description="Платформа поиска фриланс-заказов",
    version="0.3.0",
    lifespan=lifespan,
)


app.mount(
    "/static",
    StaticFiles(directory=WEB_DIR),
    name="static",
)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def telegram_user(init_data: str) -> dict:
    """Validate Telegram Mini App initData using the bot token; never trust initDataUnsafe."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token or not init_data:
        raise HTTPException(status_code=401, detail="Откройте Vortex через Telegram")
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
        received_hash = pairs.pop("hash")
        auth_date = int(pairs.get("auth_date", "0"))
        if time.time() - auth_date > 86400 or auth_date > time.time() + 60:
            raise ValueError("expired")
        data_check = "\n".join(f"{key}={value}" for key, value in sorted(pairs.items()))
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, received_hash):
            raise ValueError("invalid hash")
        user = json.loads(pairs["user"])
        if not isinstance(user.get("id"), int):
            raise ValueError("invalid user")
        return user
    except (KeyError, ValueError, TypeError, json.JSONDecodeError):
        raise HTTPException(status_code=401, detail="Не удалось подтвердить аккаунт Telegram") from None


def current_user(x_telegram_init_data: str = Header(default=""), db: Session = Depends(get_db)):
    # Development-only identity is disabled unless explicitly enabled; never enable on Render.
    if os.getenv("VORTEX_DEV_AUTH", "false").lower() in {"1", "true", "yes"}:
        raw_id = os.getenv("VORTEX_DEV_TELEGRAM_ID", "100000001")
        try:
            telegram = {"id": int(raw_id), "first_name": "Demo", "last_name": "User", "username": "vortex_demo"}
        except ValueError:
            raise HTTPException(status_code=500, detail="Invalid development user configuration")
    else:
        telegram = telegram_user(x_telegram_init_data)
    user = db.scalar(select(models.User).where(models.User.telegram_id == telegram["id"]))
    if user is None:
        user = models.User(
            telegram_id=telegram["id"], username=telegram.get("username"),
            first_name=telegram.get("first_name", ""), last_name=telegram.get("last_name", ""),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        user.username = telegram.get("username")
        user.first_name = telegram.get("first_name", "")
        user.last_name = telegram.get("last_name", "")
        db.commit()
    return user


def profile_payload(user: models.User, db: Session) -> dict:
    favorites_count = db.query(models.Favorite).filter(models.Favorite.user_id == user.id).count()
    return {
        "id": user.id, "telegram_id": user.telegram_id, "username": user.username,
        "first_name": user.first_name, "last_name": user.last_name,
        "specialization": user.specialization, "skills": user.skills,
        "categories": json.loads(user.categories or "[]"),
        "budget_min": user.budget_min, "budget_max": user.budget_max,
        "favorites_count": favorites_count, "created_at": user.created_at.isoformat(),
    }


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "message": "Vortex работает!",
    }


@app.get("/api/orders")
def api_orders(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    # Optional local-only fixtures let the UI be tested before ai-product is connected.
    # The response shape is identical to the existing /api/orders contract.
    if os.getenv("VORTEX_DEMO_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}:
        return demo_page(limit=limit, offset=offset)
    try:
        page = CatalogClient.from_env().fetch_jobs(limit=limit, offset=offset)
    except CatalogUnavailable:
        raise HTTPException(status_code=503, detail="catalog unavailable") from None
    return page


@app.get("/api/me")
def get_profile(user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    return profile_payload(user, db)


@app.put("/api/me")
def update_profile(payload: dict, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    allowed = {"specialization", "skills", "categories", "budget_min", "budget_max"}
    if set(payload) - allowed:
        raise HTTPException(status_code=400, detail="Недопустимые поля профиля")
    specialization = payload.get("specialization", user.specialization)
    skills = payload.get("skills", user.skills)
    categories = payload.get("categories", json.loads(user.categories or "[]"))
    low, high = payload.get("budget_min", user.budget_min), payload.get("budget_max", user.budget_max)
    if not isinstance(specialization, str) or len(specialization) > 200:
        raise HTTPException(status_code=422, detail="Некорректная специализация")
    if not isinstance(skills, str) or len(skills) > 4000:
        raise HTTPException(status_code=422, detail="Некорректные навыки")
    if not isinstance(categories, list) or len(categories) > 20 or any(not isinstance(x, str) or len(x) > 50 for x in categories):
        raise HTTPException(status_code=422, detail="Некорректные категории")
    for value in (low, high):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0 or value > 100000000):
            raise HTTPException(status_code=422, detail="Некорректный бюджет")
    if low is not None and high is not None and low > high:
        raise HTTPException(status_code=422, detail="Минимальный бюджет больше максимального")
    user.specialization, user.skills = specialization.strip(), skills.strip()
    user.categories, user.budget_min, user.budget_max = json.dumps(categories, ensure_ascii=False), low, high
    db.commit()
    db.refresh(user)
    return profile_payload(user, db)


@app.get("/api/favorites")
def get_favorites(user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(models.Favorite).where(models.Favorite.user_id == user.id).order_by(models.Favorite.created_at.desc())).all()
    return {"items": [json.loads(row.order_json) for row in rows], "total": len(rows)}


@app.post("/api/favorites")
def add_favorite(payload: dict, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    order = payload.get("order")
    if not isinstance(order, dict) or order.get("id") is None:
        raise HTTPException(status_code=422, detail="Нужен объект заказа с id")
    if len(json.dumps(order, ensure_ascii=False)) > 20000:
        raise HTTPException(status_code=413, detail="Карточка заказа слишком большая")
    order_id = str(order["id"])
    row = db.scalar(select(models.Favorite).where(models.Favorite.user_id == user.id, models.Favorite.order_id == order_id))
    if row is None:
        db.add(models.Favorite(user_id=user.id, order_id=order_id, order_json=json.dumps(order, ensure_ascii=False)))
        db.commit()
    return {"ok": True}


@app.delete("/api/favorites/{order_id}")
def remove_favorite(order_id: str, user: models.User = Depends(current_user), db: Session = Depends(get_db)):
    db.execute(delete(models.Favorite).where(models.Favorite.user_id == user.id, models.Favorite.order_id == order_id))
    db.commit()
    return {"ok": True}


@app.get("/")
def home():
    return FileResponse(WEB_DIR / "index.html")
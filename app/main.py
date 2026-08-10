"""AviaProofAI — аналитический цифровой двойник требований (Teamcenter 11).

FastAPI-сервис: ETL (TeamcenterSync) + LLM-анализ + REST API для Koseven.
Запуск:  uvicorn app.main:app --port 8080   (или python -m app.cli serve)
Документация API: /docs (OpenAPI).
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import analysis, requirements, settings, sync, twin
from app.api.deps import get_db, require_api_key
from app.config import get_settings
from app.db import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="AviaProofAI — цифровой двойник требований",
    description="Слой аналитики над Teamcenter 11: синхронизация требований, "
                "LLM-аудит качества, поиск противоречий, правки и миграция обратно в TC.",
    version="1.0.0",
    lifespan=lifespan,
)

# Koseven (PHP) и другие UI работают с сервисом из браузера
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (sync.router, requirements.router, analysis.router, twin.router, settings.router):
    app.include_router(r, prefix="/api/v1")


@app.get("/health", tags=["system"])
def health(db=Depends(get_db)):
    db.execute(text("SELECT 1"))
    s = get_settings()
    return {
        "status": "ok",
        "service": "aviaproofai",
        "llm": "external" if s.llm_enabled else "rule-based (offline)",
        "tc_url": s.tc_url,
        "tc_write_allowed_global": s.tc_write_allowed,
        "db": "ok",
    }


@app.get("/api/v1/ping", dependencies=[Depends(require_api_key)], tags=["system"])
def ping():
    return {"pong": True}

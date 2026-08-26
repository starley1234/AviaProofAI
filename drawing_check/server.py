"""API сервиса «Проверка чертежа» (FastAPI, порт 8502).

Эндпоинты:
    GET  /health                 — живость
    GET  /api/info               — диагностика: адреса, модель, OCR, файл логов
    POST /api/check              — загрузка чертежа + запуск проверки (multipart)
    GET  /api/logs?limit=N       — хвост логов (для страницы логов)
    POST /api/logs/clear         — стереть логи

Запуск: uvicorn drawing_check.server:app --host 0.0.0.0 --port 8502
"""
from __future__ import annotations

import os

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from drawing_check.config import settings
from drawing_check.logs import log_store
from drawing_check.ocr import ocr_ensemble
from drawing_check.service import check_drawing

app = FastAPI(title="Проверка чертежа (API)", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Streamlit UI на 8501 обращается с другого origin
    allow_methods=["*"],
    allow_headers=["*"],
)

ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".pdf", ".tif", ".tiff"}


@app.get("/health")
def health():
    return {"status": "ok", "service": "drawing-check", "port": settings.api_port}


@app.get("/api/info")
def info():
    """Диагностика «в чём дело»: какой адрес у API, какая модель, какие OCR-движки."""
    return {
        "api": {
            "internal": settings.api_url_internal,
            "external": settings.api_url_external,
            "listening_on": f"{settings.api_host}:{settings.api_port}",
        },
        "ui": {"port": settings.ui_port},
        "llm": {"enabled": settings.llm_enabled, "base_url": settings.model_base_url,
                "model": settings.model_name, "ctx": settings.model_ctx},
        "ocr": ocr_ensemble.engines_report,
        "logs": {"path": os.path.abspath(log_store._current_file()), "max_bytes": settings.log_max_bytes},
        "hint": "UI в браузере должен обращаться к ВНЕШНЕМУ адресу API (localhost:8502); "
                "имя backend доступно только внутри docker-сети",
    }


@app.post("/api/check")
async def api_check(file: UploadFile = File(...),
                    text: str | None = Form(default=None)):
    name = file.filename or "upload"
    ext = os.path.splitext(name)[1].lower()
    if ext not in ALLOWED_EXT:
        log_store.warn(f"Недопустимый тип файла: {ext or '(без расширения)'}", file=name)
        raise HTTPException(422, f"Недопустимый тип файла: {ext or '(без расширения)'}. "
                                 f"Разрешены: {sorted(ALLOWED_EXT)}")
    data = await file.read()
    result = check_drawing(data, image_name=name, text_override=text)
    if not result["ok"]:
        raise HTTPException(500, result["error"])
    return result


@app.get("/api/logs")
def get_logs(limit: int = settings.log_tail_default):
    limit = max(1, min(limit, 5000))
    return {"logs": log_store.tail(limit), "path": os.path.abspath(log_store._current_file())}


@app.post("/api/logs/clear")
def clear_logs(reason: str = "вручную"):
    erased = log_store.clear(reason=reason)
    return {"erased": erased, "logs": log_store.tail(20)}


@app.get("/api/logs/raw", response_class=PlainTextResponse)
def raw_logs(limit: int = 500):
    """Сырой текст логов — удобно копировать в тикет."""
    rows = []
    for r in log_store.tail(limit):
        rows.append(f"{r.get('ts','')} [{r.get('level','')}] {r.get('msg','')}")
    return "\n".join(rows)

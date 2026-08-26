"""Сервис «Проверка чертежа»: пайплайн загрузка -> OCR -> LLM -> замечания.

Каждый шаг логируется (LogStore) — страница логов показывает, где именно
застряла проверка (OCR? LLM? сеть? порт?).
"""
from __future__ import annotations

import time
import traceback

from drawing_check.config import settings
from drawing_check.llm import run_llm_or_heuristic
from drawing_check.logs import log_store
from drawing_check.ocr import OCREnsemble, OCRResult, ocr_ensemble


def check_drawing(image_bytes: bytes, image_name: str = "",
                  text_override: str | None = None,
                  ocr: OCREnsemble | None = None) -> dict:
    """Полный цикл проверки. Никогда не бросает исключений — ошибки в result.error."""
    started = time.monotonic()
    run_id = int(time.time() * 1000) % 10_000_000
    log_store.info(f"Проверка запущена: {image_name or '(без имени)'}",
                   run_id=run_id, bytes=len(image_bytes))

    # 1) размер файла
    if len(image_bytes) > 25 * 1024 * 1024:
        log_store.error("Файл слишком большой (>25 МБ)", run_id=run_id, bytes=len(image_bytes))
        return _result(run_id, error="Файл больше 25 МБ — загрузка отклонена")

    # 2) OCR
    ocr_text: OCRResult
    if text_override and text_override.strip():
        ocr_text = OCRResult(text=text_override.strip(), source="user_override")
        log_store.info("Текст чертежа взят из ручного ввода (OCR пропущен)",
                       run_id=run_id, chars=len(ocr_text.text))
    else:
        ocr_text = (ocr or ocr_ensemble).extract(image_bytes, image_name)
        for w in ocr_text.warnings:
            log_store.warn(w, run_id=run_id)
        log_store.info("OCR завершён",
                       run_id=run_id, engines=ocr_text.engines_used or [ocr_text.source],
                       chars=len(ocr_text.text), duration_ms=ocr_text.duration_ms)
        if not ocr_text.text:
            log_store.error("Текст чертежа не распознан (нет OCR-движков или пустой чертёж)",
                            run_id=run_id)
            return _result(run_id, error="Текст чертежа не распознан. Установите OCR-движок "
                                         "или вставьте текст вручную.",
                           ocr=ocr_text, duration_ms=int((time.monotonic() - started) * 1000))

    # 3) LLM / эвристики
    log_store.info("Запуск проверки по требованиям",
                   run_id=run_id, engine="llm" if settings.llm_enabled else "heuristic",
                   model=settings.model_name)
    try:
        findings, engine = run_llm_or_heuristic(ocr_text.text)
    except Exception:
        log_store.error("Сбой проверки требований: " + traceback.format_exc(limit=5), run_id=run_id)
        return _result(run_id, error="Сбой проверки требований (см. логи)",
                       ocr=ocr_text, duration_ms=int((time.monotonic() - started) * 1000))

    severity_counts: dict[str, int] = {}
    for f in findings:
        severity_counts[f.get("severity", "info")] = severity_counts.get(f.get("severity", "info"), 0) + 1
    log_store.info("Проверка завершена",
                   run_id=run_id, engine=engine, findings=len(findings),
                   severity=severity_counts, duration_ms=int((time.monotonic() - started) * 1000))

    return _result(run_id, findings=findings, engine=engine, ocr=ocr_text,
                   duration_ms=int((time.monotonic() - started) * 1000))


def _result(run_id: int, findings: list[dict] | None = None, engine: str | None = None,
            ocr: OCRResult | None = None, error: str | None = None,
            duration_ms: int = 0) -> dict:
    return {
        "run_id": run_id,
        "ok": error is None,
        "error": error,
        "engine": engine,
        "findings": findings or [],
        "ocr": {"source": ocr.source if ocr else None,
                "chars": len(ocr.text) if ocr else 0,
                "engines": ocr.engines_used if ocr else []},
        "duration_ms": duration_ms,
    }

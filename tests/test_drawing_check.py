"""Тесты сервиса «Проверка чертежа»: логи, OCR-заглушка, LLM-эвристики, API, порты."""
from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("LOG_DIR", tempfile.mkdtemp(prefix="dc_logs_"))


# ─────────────── логи ───────────────
def test_log_store_tail_and_clear(tmp_path):
    from drawing_check.logs import LogStore
    store = LogStore(path=str(tmp_path), max_bytes=10_000)
    store.info("шаг 1", run_id=1)
    store.warn("предупреждение")
    store.error("ошибка", trace="x")
    logs = store.tail(10)
    assert [l["level"] for l in logs] == ["INFO", "WARN", "ERROR"]
    assert logs[0]["msg"] == "шаг 1" and logs[0]["run_id"] == 1

    erased = store.clear()
    assert erased == 3
    # после очистки остаётся только запись о самой очистке
    assert store.tail(10)[-1]["msg"] == "Логи стёрты"


def test_log_store_rotation(tmp_path):
    from drawing_check.logs import LogStore
    store = LogStore(path=str(tmp_path), max_bytes=1_000)
    for i in range(300):
        store.info(f"запись номер {i} с каким-то содержимым для объёма")
    # файл не превышает лимит значительно (одно усечение -> <= max + одна строка)
    size = os.path.getsize(store._current_file())
    assert size <= 1_000 + 512
    # после усечения остались ПОСЛЕДНИЕ записи, а не первые
    store.info("ФИНАЛЬНАЯ ЗАПИСЬ")
    tail = store.tail(5)
    assert any("ФИНАЛЬНАЯ ЗАПИСЬ" in r["msg"] for r in tail)
    assert not any("запись номер 0" in r["msg"] for r in store.tail(50))


# ─────────────── OCR и пайплайн ───────────────
def test_ocr_stub_recognizes_sample_drawing():
    from drawing_check.ocr import OCREnsemble, SAMPLE_DRAWING_SHA, sha256_bytes
    data = open("drawing_check/fixtures/sample_drawing.png", "rb").read()
    assert sha256_bytes(data) == SAMPLE_DRAWING_SHA
    res = OCREnsemble(mode="none").extract(data, "sample_drawing.png")
    assert "120 Н" in res.text and "1400 м" in res.text
    assert res.source == "stub"


def test_ocr_stub_unknown_image_warns():
    from drawing_check.ocr import OCREnsemble
    res = OCREnsemble(mode="none").extract(b"not an image at all")
    assert res.text == "" and res.warnings


def test_heuristic_check_finds_violation():
    """Чертёж: 120 Н на педали — нарушение REQ-1201 (не более 100 Н)."""
    from drawing_check.llm import heuristic_check
    drawing = "Усилие на педали тормоза: 120 Н\nТормозной путь: 1400 м"
    findings = heuristic_check(drawing)
    assert len(findings) >= 1
    assert findings[0]["requirement_id"] == "REQ-1201"
    assert findings[0]["severity"] == "critical"
    assert "120" in findings[0]["detail"] and "100" in findings[0]["detail"]


def test_heuristic_check_clean_drawing():
    """Чертёж без спорных параметров усилия (требования REQ-1201/1301 сами
    противоречивы — это демо-фикстура, поэтому в чистом чертеже их нет)."""
    from drawing_check.llm import heuristic_check
    drawing = "Тормозной путь: 1200 м\nМасса изделия: 10 кг"
    assert heuristic_check(drawing) == []


def test_check_drawing_full_pipeline(tmp_path, monkeypatch):
    """Загрузка -> OCR(заглушка) -> эвристики -> замечания, всё логируется."""
    from drawing_check.logs import LogStore
    from drawing_check.service import check_drawing
    monkeypatch.setattr("drawing_check.service.log_store",
                        LogStore(path=str(tmp_path)))
    data = open("drawing_check/fixtures/sample_drawing.png", "rb").read()
    result = check_drawing(data, "sample_drawing.png")
    assert result["ok"] is True
    assert result["engine"] == "heuristic"
    assert any(f["requirement_id"] == "REQ-1201" for f in result["findings"])
    # в логах видны все шаги
    msgs = [r["msg"] for r in log_store_tail(tmp_path)]
    assert any("Проверка запущена" in m for m in msgs)
    assert any("OCR завершён" in m for m in msgs)
    assert any("Проверка завершена" in m for m in msgs)


def test_check_drawing_with_manual_text(tmp_path, monkeypatch):
    from drawing_check.logs import LogStore
    from drawing_check.service import check_drawing
    monkeypatch.setattr("drawing_check.service.log_store",
                        LogStore(path=str(tmp_path)))
    result = check_drawing(b"", "x.png", text_override="Усилие на педали: 120 Н")
    assert result["ok"] and result["ocr"]["source"] == "user_override"


def log_store_tail(path):
    from drawing_check.logs import LogStore
    return LogStore(path=str(path)).tail(100)


# ─────────────── API ───────────────
def test_api_health_and_info():
    from fastapi.testclient import TestClient
    from drawing_check.server import app
    with TestClient(app) as c:
        assert c.get("/health").json()["status"] == "ok"
        info = c.get("/api/info").json()
        assert info["api"]["external"] == "http://localhost:8502"
        assert info["api"]["internal"] == "http://backend:8502"
        assert info["llm"]["model"] == "unsloth/gemma-4-12b-it"


def test_api_check_and_logs(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from drawing_check.logs import LogStore
    from drawing_check import server
    from drawing_check import service as dc_service
    store = LogStore(path=str(tmp_path))
    monkeypatch.setattr(server, "log_store", store)
    monkeypatch.setattr(dc_service, "log_store", store)  # сервис использует свой импорт
    with TestClient(server.app) as c:
        data = open("drawing_check/fixtures/sample_drawing.png", "rb").read()
        r = c.post("/api/check", files={"file": ("чертёж.png", data, "image/png")})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] and body["findings"]
        # логи доступны
        logs = c.get("/api/logs?limit=100").json()["logs"]
        assert logs and any("Проверка завершена" in l["msg"] for l in logs)
        # очистка логов
        res = c.post("/api/logs/clear").json()
        assert res["erased"] >= 1
        remaining = c.get("/api/logs?limit=100").json()["logs"]
        assert all("Логи стёрты" in l["msg"] for l in remaining)


def test_api_rejects_bad_extension():
    from fastapi.testclient import TestClient
    from drawing_check.server import app
    with TestClient(app) as c:
        r = c.post("/api/check", files={"file": ("doc.exe", b"MZ", "application/octet-stream")})
        assert r.status_code == 422


def test_api_missing_ocr_returns_500_with_log(tmp_path, monkeypatch):
    """Чертёж без OCR-движков и без текста -> 500 + запись в логах с причиной."""
    from fastapi.testclient import TestClient
    from drawing_check.logs import LogStore
    from drawing_check import server
    from drawing_check import service as dc_service
    from drawing_check.ocr import OCREnsemble

    class EmptyOCR(OCREnsemble):
        def extract(self, *a, **kw):
            from drawing_check.ocr import OCRResult
            return OCRResult(warnings=["нет движков"])

    store = LogStore(path=str(tmp_path))
    monkeypatch.setattr(server, "ocr_ensemble", EmptyOCR(mode="none"))
    monkeypatch.setattr(dc_service, "ocr_ensemble", EmptyOCR(mode="none"))
    monkeypatch.setattr(server, "log_store", store)
    monkeypatch.setattr(dc_service, "log_store", store)
    with TestClient(server.app) as c:
        r = c.post("/api/check", files={"file": ("empty.png", b"\x89PNG", "image/png")})
        assert r.status_code == 500
        logs = c.get("/api/logs").json()["logs"]
        assert any("не распознан" in l["msg"] for l in logs)


# ─────────────── порты/конфиг ───────────────
def test_default_ports():
    from drawing_check.config import Settings
    s = Settings()
    assert s.api_port == 8502 and s.ui_port == 8501
    assert s.api_url_internal == "http://backend:8502"
    assert s.api_url_external == "http://localhost:8502"

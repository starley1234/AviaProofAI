"""Конфигурация сервиса «Проверка чертежа» (drawing_check).

Порты (как в окружении заказчика):
    UI   (Streamlit):  8501
    API  (FastAPI):    8502
Внутренний адрес API в docker-сети: http://backend:8502
Внешний адрес API:                   http://localhost:8502
"""
from __future__ import annotations

import os


class Settings:
    # --- порты и хосты ---
    api_host: str = os.getenv("API_HOST", "0.0.0.0")       # API должен слушать 0.0.0.0!
    api_port: int = int(os.getenv("API_PORT", "8502"))
    ui_host: str = os.getenv("UI_HOST", "0.0.0.0")
    ui_port: int = int(os.getenv("UI_PORT", "8501"))

    # --- адреса API для UI ---
    # Внутри docker-сети UI обращается к API по имени сервиса (backend),
    # из браузера — только по внешнему адресу (localhost).
    api_url_internal: str = os.getenv("API_URL_INTERNAL", "http://backend:8502")
    api_url_external: str = os.getenv("API_URL_EXTERNAL", "http://localhost:8502")
    in_docker: bool = os.getenv("DOCKER", "") == "1"

    # --- LLM (OpenAI-совместимый endpoint, модель gemma-4-12b-it) ---
    model_base_url: str = os.getenv("MODEL_BASE_URL", "")      # напр. http://localhost:11434/v1 или vLLM
    model_api_key: str = os.getenv("MODEL_API_KEY", "")
    model_name: str = os.getenv("MODEL_NAME", "unsloth/gemma-4-12b-it")
    model_ctx: int = int(os.getenv("MODEL_CTX", "8192"))
    model_timeout: float = float(os.getenv("MODEL_TIMEOUT", "120"))

    # --- OCR ---
    # auto: использовать доступные движки (tesseract -> easyocr -> paddleocr)
    # none: заглушка (без установленных движков)
    ocr_mode: str = os.getenv("OCR_MODE", "auto")
    ocr_ensemble: bool = os.getenv("OCR_ENSEMBLE", "true").lower() in ("1", "true", "yes")
    ocr_max_side: int = int(os.getenv("OCR_MAX_SIDE", "768"))  # VRAM 16GB · 768px · 4-bit

    # --- логи ---
    log_dir: str = os.getenv("LOG_DIR", "drawing_check/data/logs")
    log_max_bytes: int = int(os.getenv("LOG_MAX_BYTES", str(2 * 1024 * 1024)))
    log_tail_default: int = int(os.getenv("LOG_TAIL_DEFAULT", "200"))

    @property
    def llm_enabled(self) -> bool:
        return bool(self.model_base_url)

    @property
    def ui_api_url(self) -> str:
        """Какой адрес API использует UI: внутри docker — имя сервиса, иначе localhost."""
        return self.api_url_internal if self.in_docker else self.api_url_external


settings = Settings()

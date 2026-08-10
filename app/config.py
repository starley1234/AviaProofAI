"""Конфигурация AviaProofAI (pydantic-settings).

Все настройки читаются из переменных окружения / файла .env.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- HTTP API ---
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    api_keys: str = "dev-key-change-me"          # через запятую; заголовок X-Api-Key

    # --- PostgreSQL ---
    database_url: str = "postgresql+psycopg2://avia:avia@localhost:5432/avia"

    # --- Teamcenter 11 (SOA) ---
    tc_url: str = "http://org-tc2:8080/tc/services/"
    tc_user: str = "infodba"
    tc_password: str = "infodba"
    tc_spec_id: str = "SPEC-BRAKE-001"           # корневая спецификация требований
    tc_timeout: float = 30.0

    # --- Запись в Teamcenter: глобальный выключатель (безопасный дефолт: ВЫКЛ) ---
    tc_write_allowed: bool = False

    # --- LLM (OpenAI-совместимый) ---
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    llm_timeout: float = 60.0
    llm_temperature: float = 0.1

    # --- Аналитика ---
    conflict_top_k: int = 5                      # сколько ближайших соседей брать в RAG-скан
    conflict_min_similarity: float = 0.15       # порог похожести текстов для пары

    @property
    def api_key_list(self) -> list[str]:
        return [k.strip() for k in self.api_keys.split(",") if k.strip()]

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llm_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()

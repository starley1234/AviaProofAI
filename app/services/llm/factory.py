"""Фабрика LLM-клиента по настройкам сервиса."""
from __future__ import annotations

from app.config import Settings
from app.services.llm.base import LLMClient
from app.services.llm.http_client import HttpLLMClient
from app.services.llm.rule_based import RuleBasedClient


def build_llm(settings: Settings | None = None) -> LLMClient:
    """Реальный LLM (OpenAI-совместимый) если задан ключ, иначе эвристики."""
    from app.config import get_settings
    s = settings or get_settings()
    if s.llm_enabled:
        return HttpLLMClient(base_url=s.llm_base_url, api_key=s.llm_api_key,
                             model=s.llm_model, temperature=s.llm_temperature,
                             timeout=s.llm_timeout)
    return RuleBasedClient()

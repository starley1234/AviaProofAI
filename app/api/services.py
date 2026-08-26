"""Сборка сервисов с общим LLM-клиентом (из настроек)."""
from __future__ import annotations

from functools import lru_cache

from app.config import get_settings
from app.services.analysis_service import AnalysisService
from app.services.impact import ImpactService
from app.services.llm.base import LLMClient
from app.services.settings_service import SettingsService


@lru_cache
def get_llm() -> LLMClient:
    from app.services.llm.factory import build_llm
    return build_llm(get_settings())


def analysis_service(db):
    return AnalysisService(db, get_llm())


def impact_service(db):
    return ImpactService(db, get_llm())


def settings_service(db) -> SettingsService:
    return SettingsService(db)

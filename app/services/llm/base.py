"""Базовые абстракции LLM-ядра.

LLMClient — интерфейс к модели. Две реализации:
  HttpLLMClient    — любой OpenAI-совместимый endpoint (OpenAI, vLLM, Ollama, LM Studio);
  RuleBasedClient  — встроенный эвристический движок (без сети): работает всегда,
                     используется в тестах и как фолбэк, если LLM недоступен.

Агенты (agent.py) всегда вызывают complete_json() и парсят ответ как JSON.
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod


class LLMError(Exception):
    """Ошибка обращения к LLM."""


def parse_json_robust(text: str) -> dict | None:
    """Достаёт первый JSON-объект из ответа модели (даже если вокруг есть текст)."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
    return None


class LLMClient(ABC):
    """Интерфейс LLM: один метод complete_json (строгий JSON-ответ)."""

    name: str = "llm"

    @abstractmethod
    def complete_json(self, system: str, user: str) -> dict:
        """Возвращает dict — результат модели (уже распарсенный JSON).

        Реализация обязана вернуть dict или бросить LLMError.
        """
        raise NotImplementedError

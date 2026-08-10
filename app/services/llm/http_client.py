"""HttpLLMClient — OpenAI-совместимый клиент (httpx, без SDK).

Работает с любым endpoint'ом формата OpenAI Chat Completions:
OpenAI API, Azure, vLLM, Ollama (/v1), LM Studio, llama.cpp server.
"""
from __future__ import annotations

import httpx

from app.services.llm.base import LLMClient, LLMError, parse_json_robust


class HttpLLMClient(LLMClient):
    def __init__(self, base_url: str, api_key: str, model: str,
                 temperature: float = 0.1, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.temperature = temperature
        self.timeout = timeout
        self.name = f"http:{model}"

    def complete_json(self, system: str, user: str) -> dict:
        url = f"{self.base_url}/chat/completions"
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            # просим модель отвечать строго JSON (поддерживается большинством endpoint'ов)
            "response_format": {"type": "json_object"},
        }
        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=self.timeout)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
            raise LLMError(f"LLM endpoint {url}: {e}") from e
        parsed = parse_json_robust(content)
        if parsed is None:
            raise LLMError(f"LLM вернул не-JSON: {content[:300]}")
        return parsed

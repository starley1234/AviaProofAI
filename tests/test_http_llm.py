"""Тесты HttpLLMClient: OpenAI-совместимый endpoint (фейковый сервер) + фолбэк."""
from __future__ import annotations

import json
import socket
import threading

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.services.llm.agent import AuditAgent, ContradictionAgent
from app.services.llm.base import LLMError
from app.services.llm.http_client import HttpLLMClient


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _fake_llm_server(reply: dict, fail: bool = False):
    """OpenAI-совместимый endpoint: POST /v1/chat/completions."""
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def chat(req: Request):
        if fail:
            return JSONResponse({"error": "upstream down"}, status_code=503)
        body = await req.json()
        assert body["model"] == "test-model"
        assert body["response_format"] == {"type": "json_object"}
        messages = body["messages"]
        assert messages[0]["role"] == "system" and messages[1]["role"] == "user"
        return {"choices": [{"message": {"content": json.dumps(reply, ensure_ascii=False)}}]}

    return app


@pytest.fixture()
def fake_llm():
    import uvicorn
    app = _fake_llm_server({"contradiction": True, "type": "polarity",
                            "explanation": "от фейкового LLM", "suggestion": "согласовать"})
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    import time, httpx
    for _ in range(100):
        try:
            httpx.get(f"http://127.0.0.1:{port}/docs")
            break
        except Exception:
            time.sleep(0.05)
    yield HttpLLMClient(f"http://127.0.0.1:{port}/v1", api_key="k", model="test-model")
    server.should_exit = True
    t.join(timeout=10)


def test_http_client_returns_parsed_json(fake_llm):
    res = fake_llm.complete_json("system", "user")
    assert res["contradiction"] is True and res["type"] == "polarity"


def test_agent_uses_llm_when_available(fake_llm):
    agent = ContradictionAgent(fake_llm)
    res = agent.compare("Требование A", "Требование B")
    assert res["explanation"] == "от фейкового LLM"  # ответ пришёл от LLM, не из фолбэка


def test_agent_falls_back_when_llm_down():
    """LLM недоступен -> эвристический фолбэк, сервис продолжает работать."""
    client = HttpLLMClient("http://127.0.0.1:1/v1", api_key="", model="x", timeout=2)
    with pytest.raises(LLMError):
        client.complete_json("s", "u")
    agent = AuditAgent(client)
    res = agent.audit_requirement("Система должна быть достаточно надёжной.")
    assert any(i["type"] == "verifiability" for i in res["issues"])

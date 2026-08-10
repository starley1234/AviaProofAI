"""Общее окружение тестов: реальный PostgreSQL (pgserver) + заглушка Teamcenter.

Порядок важен: переменные окружения выставляются ДО импорта app.*,
т.к. конфиг кэшируется (lru_cache).
"""
from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "stub_tc" / "fixtures" / "brake_system.yaml"

# ─────────────── 1. PostgreSQL (pgserver: настоящий PG с jsonb) ───────────────
import pgserver  # noqa: E402

_PG_DIR = os.environ.get("AVIA_TEST_PGDATA", str(Path.home() / ".avia_test_pgdata"))
_pg = None


def _start_pg():
    global _pg
    if _pg is None:
        _pg = pgserver.get_server(_PG_DIR)
        os.environ["DATABASE_URL"] = _pg.get_uri().replace("postgresql://", "postgresql+psycopg2://")


# ─────────────── 2. Заглушка Teamcenter (uvicorn в потоке) ───────────────
def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_stub_thread = None
_stub_server = None


def _start_stub():
    global _stub_thread, _stub_server
    if _stub_server is not None:
        return
    import uvicorn
    from stub_tc.server import app as stub_app

    port = _free_port()
    os.environ["STUB_PORT"] = str(port)
    _stub_server = uvicorn.Server(uvicorn.Config(stub_app, host="127.0.0.1", port=port, log_level="error"))
    _stub_thread = threading.Thread(target=_stub_server.run, daemon=True)
    _stub_thread.start()
    import httpx
    for _ in range(100):
        try:
            httpx.get(f"http://127.0.0.1:{port}/admin")
            break
        except Exception:
            time.sleep(0.05)
    os.environ["TC_URL"] = f"http://127.0.0.1:{port}/tc/services/"
    os.environ["STUB_BASE"] = f"http://127.0.0.1:{port}"


def _stop_stub():
    if _stub_server is not None:
        _stub_server.should_exit = True
        _stub_thread.join(timeout=10)


def pytest_configure():
    os.environ.setdefault("API_KEYS", "test-key")
    os.environ.setdefault("TC_USER", "infodba")
    os.environ.setdefault("TC_PASSWORD", "infodba")
    os.environ.setdefault("TC_WRITE_ALLOWED", "false")
    os.environ.setdefault("LLM_API_KEY", "")  # пусто -> rule-based (детерминированные тесты)
    _start_pg()
    _start_stub()


def pytest_unconfigure():
    _stop_stub()


# ─────────────── фикстуры ───────────────
@pytest.fixture()
def db():
    """Чистая БД для каждого теста: готовая сессия SQLAlchemy."""
    from app.db import get_engine, get_session_factory, init_db
    init_db()
    engine = get_engine()
    session = get_session_factory()()
    yield session
    session.close()
    Base = __import__("app.db", fromlist=["Base"]).Base
    Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def tc_client():
    """SOAP-клиент к заглушке (уже залогинен)."""
    from app.config import get_settings
    from app.services.teamcenter.client import TeamcenterSoapClient
    s = get_settings()
    client = TeamcenterSoapClient(s.tc_url, timeout=10)
    client.login(s.tc_user, s.tc_password)
    yield client
    client.close()


@pytest.fixture()
def api_client(db):
    """HTTP-клиент к FastAPI (X-Api-Key: test-key)."""
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def api(db, api_client):
    """Удобная обёртка: api.get/post с заголовками."""

    class Api:
        def __init__(self, client):
            self.client = client
            self.headers = {"X-Api-Key": "test-key"}

        def as_user(self, login):
            return {**self.headers, "X-TC-User": login}

        def get(self, path, **kw):
            kw.setdefault("headers", self.headers)
            return self.client.get(path, **kw)

        def post(self, path, **kw):
            kw.setdefault("headers", self.headers)
            return self.client.post(path, **kw)

        def put(self, path, **kw):
            kw.setdefault("headers", self.headers)
            return self.client.put(path, **kw)

        def patch(self, path, **kw):
            kw.setdefault("headers", self.headers)
            return self.client.patch(path, **kw)

    return Api(api_client)


@pytest.fixture()
def seeded(api):
    """БД с синхронизированной спецификацией + пользователями."""
    r = api.post("/api/v1/sync/run", json={"spec_id": "SPEC-BRAKE-001", "wait": True})
    assert r.status_code == 200 and r.json()["status"] == "ok", r.json()
    for u in [dict(tc_login="petrov", koseven_login="petrov", full_name="Пётр Петров",
                   write_to_tc_allowed=True),
              dict(tc_login="sidorov", koseven_login="sidorov", full_name="Сидор Сидоров",
                   write_to_tc_allowed=False),
              dict(tc_login="infodba", koseven_login="infodba", full_name="Info DBA",
                   role="admin", write_to_tc_allowed=True)]:
        api.post("/api/v1/users", json=u)
    return api

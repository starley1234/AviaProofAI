"""Тесты REST-клиента (RestServices) — проверенный рабочий протокол заказчика:
RequestEnvelope/bodystring CDATA, cookie ASP.NET_SessionId, getItemAndRelatedObjects.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Requirement
from app.services.teamcenter import mapping as m
from app.services.teamcenter.rest import build_request_envelope, rest_schema_ns
from app.services.teamcenter.rest_client import TeamcenterRestClient
from app.services.teamcenter.soap import TcAuthError, TcSoapError


@pytest.fixture()
def rest_client():
    from app.config import get_settings
    s = get_settings()
    c = TeamcenterRestClient(s.tc_url, timeout=10)
    c.login(s.tc_user, s.tc_password)
    yield c
    c.close()


def test_rest_schema_ns():
    assert rest_schema_ns("Core-2008-06-DataManagement") == \
        "http://teamcenter.com/Schemas/Core/2008-06/DataManagement"
    assert rest_schema_ns("Core-2007-01-Session") == \
        "http://teamcenter.com/Schemas/Core/2007-01/Session"


def test_envelope_shape():
    env = build_request_envelope("Core-2008-06-DataManagement", "getItemAndRelatedObjects",
                                 "<GetItemAndRelatedObjectsInput/>")
    assert "RequestEnvelope" in env
    assert "http://teamcenter.com/Schemas/Soa/2006-09/ClientContext" in env
    assert "<![CDATA[<GetItemAndRelatedObjectsInput/>]]>" in env


def test_login_sets_session_cookie(rest_client):
    assert rest_client.session_id.startswith("session-")
    assert rest_client._http.cookies.get(m.REST_SESSION_COOKIE) == rest_client.session_id


def test_login_uses_json_rest_by_default(rest_client):
    """Авторизация — JsonRestServices (как в PHP-клиенте заказчика):
    сессия вида session-... (создаётся JSON-логином заглушки)."""
    assert rest_client.session_id.startswith("session-")


def test_json_login_direct_format(rest_client):
    """Прямой JSON REST login 1:1 с PHP-кодом: payload, cookie в Set-Cookie."""
    import httpx as hx
    from app.config import get_settings
    s = get_settings()
    url = f"{s.tc_url.rstrip('/')}/JsonRestServices/Core-2011-06-Session/login"
    payload = {
        "header": {"state": {}, "policy": {}},
        "body": {"credentials": {"user": s.tc_user, "password": s.tc_password,
                                 "role": "", "descrimator": "", "locale": "", "group": ""}},
    }
    r = hx.post(url, json=payload, headers={"Content-Type": "application/json"})
    assert r.status_code == 200
    set_cookie = r.headers.get("set-cookie", "")
    assert "ASP.NET_SessionId=session-" in set_cookie


def test_json_login_wrong_password():
    """Неверный пароль через JSON REST -> TcAuthError (не ретраится)."""
    from app.config import get_settings
    s = get_settings()
    c = TeamcenterRestClient(s.tc_url, timeout=10)
    with pytest.raises(TcAuthError):
        c.login("infodba", "wrong-password")
    c.close()


def test_auth_session_mode_uses_external_session():
    """TC_AUTH=session: сервис принимает готовую сессию (как PHP передаёт sessionId)."""
    from app.config import get_settings
    s = get_settings()
    # получаем сессию «извне» (как PHP-клиент)
    probe = TeamcenterRestClient(s.tc_url, timeout=10)
    external = probe.login(s.tc_user, s.tc_password)
    probe.close()
    c = TeamcenterRestClient(s.tc_url, timeout=10, auth="session", session_id=external)
    items = c.find_items("SPEC-BRAKE-001", m.TYPE_SPECIFICATION)
    assert len(items) == 1  # готовая сессия работает без логина
    c.close()


def test_auth_session_mode_without_session_fails():
    from app.config import get_settings
    s = get_settings()
    c = TeamcenterRestClient(s.tc_url, timeout=10, auth="session", session_id="")
    with pytest.raises(TcAuthError, match="TC_SESSION_ID"):
        c.login("infodba", "infodba")
    c.close()


def test_login_wrong_password():
    from app.config import get_settings
    s = get_settings()
    c = TeamcenterRestClient(s.tc_url, timeout=10)
    with pytest.raises(TcAuthError):
        c.login("infodba", "wrong-password")
    c.close()


def test_find_items_via_get_item_and_related(rest_client):
    """Проверенный формат заказчика: itemInfo/ids name=item_id."""
    items = rest_client.find_items("SPEC-BRAKE-001", m.TYPE_SPECIFICATION)
    assert len(items) == 1 and items[0].item_id == "SPEC-BRAKE-001"
    revs = rest_client.get_item_revisions(items[0].uid)
    assert [(r.item_id, r.item_revision_id) for r in revs] == [("SPEC-BRAKE-001", "A")]


def test_children_paging(rest_client, monkeypatch):
    """Пагинация getChildren: page_size=2 -> клиент делает несколько страниц."""
    rest_client.page_size = 2
    kids = rest_client.get_children("rev-SPEC-BRAKE-001-A")
    assert [k.uid for k in kids] == ["rev-SS-1100-A", "rev-SS-1200-A",
                                     "rev-SS-1300-A", "rev-SS-1400-A"]
    kids2 = rest_client.get_children("rev-SS-1200-A")
    assert [k.uid for k in kids2] == ["rev-REQ-1201-A", "rev-REQ-1203-A", "rev-REQ-1204-A"]


def test_rest_operations_surface(rest_client):
    sec = rest_client.get_properties("rev-SS-1200-A")
    assert sec.item_id == "SS-1200" and sec.object_name == "Основное торможение"
    reqs = rest_client.get_requirements(["rev-REQ-1201-A"])
    assert "не более 100 Н" in reqs[0].object_string
    ds = rest_client.find_datasets("rev-REQ-1201-A", m.REL_SPEC_CONTENT)
    assert ds and ds[0].type_name == m.DATASET_TYPE_HTML
    files = rest_client.get_contents(ds[0].uid)
    ticket = rest_client.get_file_read_ticket(files[0].uid)
    assert "tc/files/" in ticket
    assert b"<html>" in rest_client.download_file(ticket)
    rel = rest_client.find_relations("rev-REQ-1101-A", m.REL_TRACE, "out")
    assert {r.secondary for r in rel} == {"rev-REQ-1201-A", "rev-REQ-1302-A", "rev-REQ-1402-A"}


def test_rest_set_properties_write_back(rest_client):
    upd = rest_client.set_properties("rev-REQ-1203-A", {m.ATTR_OBJECT_STRING: "REST правка"})
    assert upd[m.ATTR_OBJECT_STRING] == "REST правка"
    again = rest_client.get_requirements(["rev-REQ-1203-A"])
    assert again[0].object_string == "REST правка"


def test_rest_full_sync_via_teamcenter_sync(db):
    """Полный ETL-цикл через REST-протокол (как в проде заказчика)."""
    from app.config import get_settings
    from app.services.teamcenter.sync import TeamcenterSync
    s = get_settings()
    c = TeamcenterRestClient(s.tc_url, timeout=10)
    try:
        run = TeamcenterSync(c, db).run("SPEC-BRAKE-001")
    finally:
        c.close()
    assert run.status == "ok", run.error
    assert run.stats["created"] == 16
    req = db.scalar(select(Requirement).where(Requirement.item_id == "REQ-1201"))
    assert "не более 100 Н" in req.text
    assert req.parent_uid == "rev-SS-1200-A"
    assert req.traceability_links["in"] == ["rev-REQ-1101-A"]


def test_rest_operations_require_session():
    from app.config import get_settings
    s = get_settings()
    c = TeamcenterRestClient(s.tc_url, timeout=10)
    with pytest.raises(TcSoapError, match="Authentication"):
        c.find_items("SPEC-BRAKE-001")
    c.close()


def test_download_file_ssrf_guard(rest_client):
    """Ticket на посторонний хост отклоняется (защита от SSRF)."""
    with pytest.raises(TcSoapError, match="посторонний хост"):
        rest_client.download_file("http://evil.example.com/tc/files/file-REQ-1201-content")


def test_retry_on_transient_network_error(rest_client, monkeypatch):
    """Сетевая ошибка -> ретрай с backoff -> успех."""
    original_post = rest_client._http.post
    calls = {"n": 0}

    def flaky_post(url, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise __import__("httpx").ConnectError("connection refused (имитация)")
        return original_post(url, **kw)

    monkeypatch.setattr(rest_client._http, "post", flaky_post)
    items = rest_client.find_items("SPEC-BRAKE-001")
    assert len(items) == 1 and calls["n"] == 2


def test_auto_relogin_on_expired_session(db, monkeypatch):
    """Сессия истекла (сервер отвергает cookie) -> автоперелогин и повтор."""
    from app.config import get_settings
    from app.services.teamcenter.sync import TeamcenterSync
    s = get_settings()
    c = TeamcenterRestClient(s.tc_url, timeout=10)
    c.login(s.tc_user, s.tc_password)
    # «протухаем» сессию на сервере
    from stub_tc.server import store
    store.tokens.pop(c.session_id, None)
    try:
        run = TeamcenterSync(c, db).run("SPEC-BRAKE-001")
    finally:
        c.close()
    assert run.status == "ok", run.error  # клиент сам перелогинился

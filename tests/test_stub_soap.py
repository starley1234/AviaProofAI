"""Тесты SOAP-заглушки Teamcenter: контракт операций (mapping.py -> stub -> клиент)."""
from __future__ import annotations

import pytest

from app.services.teamcenter import mapping as m
from app.services.teamcenter.client import TeamcenterSoapClient
from app.services.teamcenter.soap import TcAuthError, TcSoapError


def test_login_returns_token_and_user(tc_client):
    assert tc_client.token and tc_client.token.startswith("token-")


def test_login_wrong_password():
    from app.config import get_settings
    s = get_settings()
    c = TeamcenterSoapClient(s.tc_url, timeout=10)
    with pytest.raises(TcAuthError):
        c.login("infodba", "wrong-password")
    c.close()


def test_operations_require_token():
    from app.config import get_settings
    from app.services.teamcenter.soap import build_envelope, parse_soap_response
    s = get_settings()
    env = build_envelope(m.SVC_ITEM_FINDER, m.OP_FIND_ITEMS,
                         {"criteria": {"name": "SPEC-BRAKE-001"}})
    resp = __import__("httpx").post(s.tc_url, content=env,
                                    headers={"Content-Type": "text/xml"})
    with pytest.raises(TcSoapError, match="Authentication"):
        parse_soap_response(resp.text)


def test_find_items_and_revisions(tc_client):
    items = tc_client.find_items("SPEC-BRAKE-001", m.TYPE_SPECIFICATION)
    assert len(items) == 1
    assert items[0].item_id == "SPEC-BRAKE-001"
    revs = tc_client.get_item_revisions(items[0].uid)
    assert len(revs) == 1 and revs[0].item_revision_id == "A"


def test_get_properties_for_section(tc_client):
    """Core-2006-03-DataManagement/getProperties для разделов."""
    sec = tc_client.get_properties("rev-SS-1200-A")
    assert sec is not None
    assert sec.item_id == "SS-1200"
    assert sec.type_name == "SpecSection"
    assert sec.object_name == "Основное торможение"


def test_get_requirements_content(tc_client):
    reqs = tc_client.get_requirements(["rev-REQ-1201-A"])
    assert len(reqs) == 1
    r = reqs[0]
    assert r.item_id == "REQ-1201"
    assert "не более 100 Н" in r.object_string
    assert r.attrs["owning_user"] == "petrov"  # raw_data: все атрибуты


def test_structure_children(tc_client):
    kids = tc_client.get_children("rev-SPEC-BRAKE-001-A")
    assert [k.type_name for k in kids] == ["SpecSection"] * 4
    assert [k.sequence_no for k in kids] == [1, 2, 3, 4]
    reqs = tc_client.get_children("rev-SS-1200-A")
    assert [k.uid for k in reqs] == ["rev-REQ-1201-A", "rev-REQ-1203-A", "rev-REQ-1204-A"]


def test_dataset_content_roundtrip(tc_client):
    """IMAN_specification: findDatasets -> getContents -> ticket -> download."""
    ds = tc_client.find_datasets("rev-REQ-1201-A", m.REL_SPEC_CONTENT)
    assert len(ds) == 1 and ds[0].type_name == m.DATASET_TYPE_HTML
    files = tc_client.get_contents(ds[0].uid)
    assert files and files[0].object_name.endswith(".html")
    ticket = tc_client.get_file_read_ticket(files[0].uid)
    assert ticket.startswith("http") and "tc/files/" in ticket
    raw = tc_client.download_file(ticket)
    assert b"<html>" in raw and "не более 100 Н".encode() in raw


def test_trace_relations_in_out(tc_client):
    out = tc_client.find_relations("rev-REQ-1101-A", m.REL_TRACE, "out")
    assert {r.secondary for r in out} == {"rev-REQ-1201-A", "rev-REQ-1302-A", "rev-REQ-1402-A"}
    inn = tc_client.find_relations("rev-REQ-1201-A", m.REL_TRACE, "in")
    assert {r.primary for r in inn} == {"rev-REQ-1101-A"}


def test_set_properties_write_back(tc_client):
    new_text = "Изменённый текст требования."
    updated = tc_client.set_properties("rev-REQ-1203-A", {m.ATTR_OBJECT_STRING: new_text})
    assert updated[m.ATTR_OBJECT_STRING] == new_text
    # проверим, что заглушка реально сохранила
    again = tc_client.get_requirements(["rev-REQ-1203-A"])
    assert again[0].object_string == new_text


def test_unknown_operation_fault(tc_client):
    with pytest.raises(TcSoapError, match="Неизвестная операция"):
        tc_client.call("Some-Unknown-Service", "doSomething", {})

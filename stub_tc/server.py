"""Заглушка Teamcenter 11 (SOA) — FastAPI-сервер.

Запуск:
    uvicorn stub_tc.server:app --port 9080
    # либо: python -m stub_tc.server

Адрес должен совпадать с TC_URL сервиса (см. .env.example):
    TC_URL=http://127.0.0.1:9080/tc/services/

Эндпоинты:
    POST /tc/services/            — SOAP-точка (как у реального TC)
    GET  /tc/files/{file_uid}     — скачивание файлов контента (ticket)
    GET  /admin                   — состояние заглушки (HTML)
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

from app.services.teamcenter import mapping as m
from app.services.teamcenter.rest import rest_schema_ns
from app.services.teamcenter.soap import localname
from stub_tc.handlers import E, S, REST_OPERATIONS, dispatch, dispatch_rest
from stub_tc.store import TcStore

app = FastAPI(title="Teamcenter 11 Stub (AviaProofAI)", docs_url=None, redoc_url=None)

FIXTURE = os.environ.get("STUB_FIXTURE", "stub_tc/fixtures/brake_system.yaml")
store = TcStore(FIXTURE)


# ─────────────────────────── SOAP-точка ───────────────────────────
@app.post("/tc/services/")
async def tc_services(request: Request):
    raw = await request.body()
    base_url = str(request.base_url)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        return _fault_response(f"Некорректный SOAP-запрос: {e}")

    # операция = первый элемент внутри SOAP Body
    operation, body = None, None
    for el in root.iter():
        if localname(el.tag) == "Body":
            for child in list(el):
                operation, body = localname(child.tag), child
                break
            break
    if operation is None or body is None:
        return _fault_response("SOAP Body пуст")

    token = None
    for el in root.iter():
        if localname(el.tag) == m.AUTH_TOKEN_TAG and el.text:
            token = el.text.strip()
            break

    resp_el, header_token, error = dispatch(store, operation, body, base_url, token)
    if error:
        return _fault_response(error)

    # оборачиваем в SOAP-конверт (вручную, чтобы namespace был как у реального TC)
    header_xml = ""
    if header_token:
        header_xml = (f'<soapenv:Header><{m.AUTH_TOKEN_TAG} '
                      f'xmlns="{m.AUTH_TOKEN_NS}">{header_token}</{m.AUTH_TOKEN_TAG}></soapenv:Header>')
    body_xml = ET.tostring(resp_el, encoding="unicode")
    xml = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<soapenv:Envelope xmlns:soapenv="{m.SOAP_ENV_NS}">\n'
        f'{header_xml}\n'
        f'<soapenv:Body>{body_xml}</soapenv:Body>\n'
        f'</soapenv:Envelope>\n'
    )
    return Response(content=xml, media_type="text/xml; charset=utf-8")


def _fault_response(message: str) -> Response:
    fault = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<soapenv:Envelope xmlns:soapenv="{m.SOAP_ENV_NS}">\n'
        f'<soapenv:Body><soapenv:Fault><faultcode>soapenv:Server</faultcode>'
        f'<faultstring>{message}</faultstring></soapenv:Fault></soapenv:Body>\n'
        f'</soapenv:Envelope>\n'
    )
    return Response(content=fault, media_type="text/xml; charset=utf-8", status_code=500)


# ─────────────────────────── REST-точка (RestServices) ───────────────────────────
@app.post("/tc/services/RestServices/{service}/{operation}")
async def tc_rest_services(service: str, operation: str, request: Request):
    """REST-протокол TC: RequestEnvelope -> ResponseEnvelope (bodystring CDATA).

    Формат — по рабочему PHP-клиенту заказчика. Аутентификация — cookie
    ASP.NET_SessionId (выдаётся login'ом через Set-Cookie).
    """
    raw = await request.body()
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        return _rest_fault(f"Некорректный RequestEnvelope: {e}")

    bodystring = None
    for el in root.iter():
        if localname(el.tag) == "bodystring":
            bodystring = el.text or ""
            break
    if bodystring is None:
        return _rest_fault("В RequestEnvelope нет bodystring")
    try:
        body_el = ET.fromstring(bodystring.strip())
    except ET.ParseError as e:
        return _rest_fault(f"Некорректный bodystring: {e}")

    # REST-операции (getItemAndRelatedObjects и др.) могут иметь PascalCase-имена
    op = operation
    if op not in REST_OPERATIONS:
        return _rest_fault(f"Неизвестная REST-операция: {operation}")

    session_id = request.cookies.get(m.REST_SESSION_COOKIE, "")
    if op != m.REST_OP_LOGIN and store.auth_user(session_id) is None:
        return _rest_fault("Authentication failed: требуется cookie ASP.NET_SessionId")

    resp_el, header_token, error = dispatch_rest(store, op, body_el, str(request.base_url))
    if error:
        return _rest_fault(error)

    # корень ответа -> PascalCase (getChildrenResponse -> GetChildrenResponse)
    root_name = localname(resp_el.tag)[0].upper() + localname(resp_el.tag)[1:]
    resp_el.tag = f"{{{rest_schema_ns(service)}}}{root_name}"
    inner = ET.tostring(resp_el, encoding="unicode")
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<ResponseEnvelope xmlns="{m.REST_ENVELOPE_NS}">\n'
        "  <header/>\n"
        f"  <body><bodystring><![CDATA[{inner}]]></bodystring></body>\n"
        "</ResponseEnvelope>\n"
    )
    headers = {}
    if op == m.REST_OP_LOGIN and header_token:
        headers["Set-Cookie"] = f"{m.REST_SESSION_COOKIE}={header_token}; path=/"
    return Response(content=xml, media_type="application/xml; charset=utf-8", headers=headers)


def _rest_fault(message: str) -> Response:
    inner = f'<error xmlns="{m.REST_ENVELOPE_NS}">{message}</error>'
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<ResponseEnvelope xmlns="{m.REST_ENVELOPE_NS}">\n'
        "  <header/>\n"
        f"  <body><bodystring><![CDATA[{inner}]]></bodystring></body>\n"
        "</ResponseEnvelope>\n"
    )
    return Response(content=xml, media_type="application/xml; charset=utf-8", status_code=500)


# ─────────────────────────── файлы контента (ticket) ───────────────────────────
@app.get("/tc/files/{file_uid}")
async def tc_file(file_uid: str):
    f = store.file(file_uid)
    if f is None:
        return PlainTextResponse("File not found", status_code=404)
    return Response(content=f["content"], media_type=f["mime"])


# ─────────────────────────── состояние заглушки (для отладки) ───────────────────────────
@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    rows = []
    for item in store.items.values():
        rev = store.revisions[item["revisions"][0]]
        trace = sum(1 for r in rev["relations"] if r["relation_type"] == m.REL_TRACE)
        rows.append(
            f"<tr><td>{item['item_id']}</td><td>{item['type_name']}</td>"
            f"<td>{item['name']}</td><td>{len(item['revisions'])}</td><td>{trace}</td></tr>"
        )
    return f"""<html><head><meta charset="utf-8"><title>Teamcenter 11 Stub</title></head>
<body><h1>Заглушка Teamcenter 11 (AviaProofAI)</h1>
<p>SOAP-точка: <code>POST /tc/services/</code> &nbsp;|&nbsp; Файлы: <code>GET /tc/files/{{uid}}</code></p>
<table border="1" cellpadding="4"><tr><th>ID</th><th>Тип</th><th>Имя</th><th>Ревизий</th><th>Связей трассировки</th></tr>
{''.join(rows)}</table></body></html>"""


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("STUB_PORT", "9080")))

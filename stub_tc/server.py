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
from app.services.teamcenter.soap import localname
from stub_tc.handlers import E, S, dispatch
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

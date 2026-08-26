"""REST-транспорт Teamcenter (RestServices): конверт RequestEnvelope и разбор ответа.

Формат взят из рабочего PHP-клиента заказчика (см. koseven/README.md) и
документации Teamcenter:
  POST {tc_url}/RestServices/{Service}/{Operation}
  <RequestEnvelope xmlns="http://teamcenter.com/Schemas/Soa/2006-09/ClientContext">
    <header/><body><bodystring><![CDATA[ ...операция... ]]></bodystring></body>
  </RequestEnvelope>
Аутентификация — cookie ASP.NET_SessionId (выдаётся REST login'ом).

Разбор ответа переиспользует SoapResponse (поиск по localname без namespace).
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from app.services.teamcenter import mapping as m
from app.services.teamcenter.soap import SoapResponse, TcSoapError, localname


class TcRestError(TcSoapError):
    """Ошибка REST-взаимодействия с Teamcenter."""


def build_request_envelope(service: str, operation: str, inner_xml: str) -> str:
    """Оборачивает XML операции в RequestEnvelope (CDATA-секция bodystring)."""
    # защита от преждевременного закрытия CDATA (структурный XML — но подстрахуемся)
    inner_xml = inner_xml.replace("]]>", "]]]]><![CDATA[>")
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<RequestEnvelope xmlns="{m.REST_ENVELOPE_NS}">\n'
        "  <header/>\n"
        "  <body>\n"
        f"    <bodystring><![CDATA[{inner_xml}]]></bodystring>\n"
        "  </body>\n"
        "</RequestEnvelope>\n"
    )


def rest_schema_ns(service: str) -> str:
    """Namespace схемы REST-сервиса: 'Core-2008-06-DataManagement' ->
    'http://teamcenter.com/Schemas/Core/2008-06/DataManagement'.
    Версия сервиса содержит дефис (2008-06), поэтому рвём по первому и последнему."""
    name, rest = service.split("-", 1)
    version, tail = rest.rsplit("-", 1)
    return f"http://teamcenter.com/Schemas/{name}/{version}/{tail}"


def parse_response_envelope(xml_text: str) -> SoapResponse:
    """Разбирает ResponseEnvelope: достаёт inner-XML из CDATA bodystring.

    Ошибки: если внутри <error>/<Fault>/<ErrorStack> — поднимает TcRestError.
    """
    root = ET.fromstring(xml_text)
    bodystring = None
    for el in root.iter():
        if localname(el.tag) == "bodystring":
            bodystring = el.text or ""
            break
    if bodystring is None:
        raise TcRestError("Teamcenter REST: в ответе нет элемента bodystring")
    inner_text = bodystring.strip()
    if not inner_text:
        raise TcRestError("Teamcenter REST: пустой bodystring")
    try:
        inner = ET.fromstring(inner_text)
    except ET.ParseError as e:
        raise TcRestError(f"Teamcenter REST: не удалось разобрать bodystring: {e}") from e

    ln = localname(inner.tag)
    if ln in ("Fault", "error", "ErrorStack", "ErrorStackType"):
        msg = "".join(inner.itertext()).strip() or ln
        if "auth" in msg.lower() or "login" in msg.lower() or "session" in msg.lower() \
                or "парол" in msg.lower() or "пользовател" in msg.lower():
            from app.services.teamcenter.soap import TcAuthError
            raise TcAuthError(f"Teamcenter REST: {msg}")
        raise TcRestError(f"Teamcenter REST: {msg}")
    return SoapResponse(inner)

"""SOAP-транспорт для Teamcenter 11: сборка конверта и разбор ответа.

Намеренно без сторонних SOAP-библиотек: TC-ответы разбираются по localname
(без учёта namespace), а имена элементов заданы в mapping.py.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Any

from app.services.teamcenter import mapping as m


class TcSoapError(Exception):
    """Ошибка SOAP-взаимодействия с Teamcenter."""


class TcAuthError(TcSoapError):
    """Ошибка авторизации в Teamcenter."""


def localname(tag: str) -> str:
    """'http://ns}Element' -> 'Element'."""
    return tag.rsplit("}", 1)[-1]


# ─────────────────────────── Сборка запроса ───────────────────────────
def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&apos;")
    )


def _params_to_xml(params: dict[str, Any], indent: str = "    ") -> str:
    """Параметры -> XML-элементы. Скаляр -> <tag>value</tag>, список -> повторяющиеся <tag>."""
    out: list[str] = []
    for key, value in params.items():
        if value is None:
            out.append(f"{indent}<{key}/>")
        elif isinstance(value, list):
            for v in value:
                if isinstance(v, dict):
                    out.append(f"{indent}<{key}>")
                    out.append(_params_to_xml(v, indent + "    "))
                    out.append(f"{indent}</{key}>")
                else:
                    out.append(f"{indent}<{key}>{_xml_escape(str(v))}</{key}>")
        elif isinstance(value, dict):
            out.append(f"{indent}<{key}>")
            out.append(_params_to_xml(value, indent + "    "))
            out.append(f"{indent}</{key}>")
        else:
            out.append(f"{indent}<{key}>{_xml_escape(str(value))}</{key}>")
    return "\n".join(out)


def build_envelope(service: str, operation: str, params: dict, token: str | None = None) -> str:
    """SOAP 1.1 конверт для вызова операции сервиса."""
    header = ""
    if token:
        header = (
            f"  <soapenv:Header>\n"
            f"    <{m.AUTH_TOKEN_TAG} xmlns=\"{m.AUTH_TOKEN_NS}\">{_xml_escape(token)}</{m.AUTH_TOKEN_TAG}>\n"
            f"  </soapenv:Header>\n"
        )
    body_params = _params_to_xml(params)
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<soapenv:Envelope xmlns:soapenv="{m.SOAP_ENV_NS}" '
        f'xmlns:ser="http://www.teamcenter.com/soa/services/{service}">\n'
        f"{header}"
        f"  <soapenv:Body>\n"
        f"    <ser:{operation}>\n"
        f"{body_params}\n"
        f"    </ser:{operation}>\n"
        f"  </soapenv:Body>\n"
        f"</soapenv:Envelope>\n"
    )


# ─────────────────────────── Разбор ответа ───────────────────────────
class SoapResponse:
    """Обёртка над телом SOAP-ответа.

    path — кортеж localname'ов, например ("findItemsResponse", "found", "item").
    Пути соответствуют константам *_PATH из mapping.py.
    """

    def __init__(self, envelope: ET.Element):
        # пути *_PATH заданы от SOAP Body; токен ищем во всём конверте (Header)
        self._root = envelope
        self._body = envelope
        for el in envelope.iter():
            if localname(el.tag) == "Body":
                self._body = el
                break
        self.token = self._read_token()

    def _read_token(self) -> str | None:
        for el in self._root.iter():
            if localname(el.tag) == m.AUTH_TOKEN_TAG and el.text:
                return el.text.strip()
        return None

    @staticmethod
    def _children(elem: ET.Element, name: str) -> list[ET.Element]:
        return [c for c in list(elem) if localname(c.tag) == name]

    def _walk(self, path: tuple[str, ...], all_: bool):
        cur = [self._body]
        for step in path:
            nxt: list[ET.Element] = []
            for el in cur:
                nxt.extend(self._children(el, step))
            if not nxt:
                return [] if all_ else None
            cur = nxt
        return cur if all_ else cur[0]

    def find(self, path: tuple[str, ...]) -> ET.Element | None:
        return self._walk(path, all_=False)

    def find_all(self, path: tuple[str, ...]) -> list[ET.Element]:
        return self._walk(path, all_=True) or []

    def text(self, path: tuple[str, ...], default: str = "") -> str:
        el = self.find(path)
        return (el.text or "").strip() if el is not None else default

    def attr(self, path: tuple[str, ...], name: str, default: str = "") -> str:
        el = self.find(path)
        return el.attrib.get(name, default) if el is not None else default


def parse_soap_response(xml_text: str) -> SoapResponse:
    """Разбирает SOAP-ответ, проверяет наличие Fault."""
    root = ET.fromstring(xml_text)
    fault = _find_fault(root)
    if fault is not None:
        msg = "".join(fault.itertext()).strip() or "SOAP Fault"
        if "auth" in msg.lower() or "login" in msg.lower() or "token" in msg.lower():
            raise TcAuthError(f"Teamcenter: ошибка авторизации: {msg}")
        raise TcSoapError(f"Teamcenter SOAP Fault: {msg}")
    return SoapResponse(root)


def _find_fault(root: ET.Element) -> ET.Element | None:
    for el in root.iter():
        if localname(el.tag) == "Fault":
            return el
    return None

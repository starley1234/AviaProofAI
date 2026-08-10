"""TeamcenterRestClient — REST-клиент Teamcenter (RestServices).

Тот же интерфейс, что у TeamcenterSoapClient (client.py): TeamcenterSync
работает с любым из них. Формат запросов — по проверенному рабочему коду
PHP-клиента заказчика: getItemAndRelatedObjects, RequestEnvelope, cookie
ASP.NET_SessionId.

Все имена сервисов/операций/элементов — в mapping.py (единственное место правки).
"""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

import httpx

from app.services.teamcenter import mapping as m
from app.services.teamcenter.client import TcItem, TcRelation
from app.services.teamcenter.rest import (TcRestError, build_request_envelope,
                                          parse_response_envelope, rest_schema_ns)
from app.services.teamcenter.soap import SoapResponse, TcAuthError, TcSoapError, localname


def _inner(service: str, tag: str, **attrs) -> ET.Element:
    """Корневой элемент операции REST (например GetItemAndRelatedObjectsInput)."""
    return ET.Element(f"{{{rest_schema_ns(service)}}}{tag}",
                      {k: str(v) for k, v in attrs.items()})


def _child(parent: ET.Element, ns: str, tag: str, text: Any = None, **attrs) -> ET.Element:
    el = ET.SubElement(parent, f"{{{ns}}}{tag}", {k: str(v) for k, v in attrs.items()})
    if text is not None:
        el.text = str(text)
    return el


class TeamcenterRestClient:
    """REST-транспорт: знает mapping.py и умеет звать операции RestServices.

    Устойчивость: таймауты (connect/read), ретраи с backoff, автоперелогин при
    истечении сессии, пагинация getChildren, лимит размера контента, защита
    от скачивания файлов с посторонних хостов (SSRF).
    """

    def __init__(self, base_url: str, timeout: float = 60.0, connect_timeout: float = 10.0,
                 retries: int = 2, page_size: int = m.DEFAULT_PAGE_SIZE,
                 session_id: str = "", verify: bool = True,
                 max_content_bytes: int = 10 * 1024 * 1024,
                 allow_external_files: bool = False):
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.retries = retries
        self.page_size = page_size
        self.session_id = session_id
        self.max_content_bytes = max_content_bytes
        self.allow_external_files = allow_external_files
        self._http = httpx.Client(timeout=(connect_timeout, timeout), verify=verify,
                                  follow_redirects=False)
        self._credentials: tuple[str, str, str, str] | None = None
        self._allowed_hosts = {httpx.URL(base_url).host}

    # ─────────────── низкий уровень ───────────────
    def call(self, service: str, operation: str, inner: ET.Element,
             auth_required: bool = True) -> SoapResponse:
        url = f"{self.base_url}{m.REST_PATH}/{service}/{operation}"
        envelope = build_request_envelope(service, operation, ET.tostring(inner, encoding="unicode"))
        headers = {"Content-Type": "application/xml; charset=utf-8"}
        try:
            resp = self._post_with_retry(url, envelope, headers)
            parsed = self._parse(resp)
            return parsed
        except TcAuthError:
            if auth_required and self._credentials:
                # сессия истекла — перелогиниваемся и повторяем один раз
                self.login(*self._credentials)
                resp = self._post_with_retry(url, envelope, headers)
                return self._parse(resp)
            raise

    def _post_with_retry(self, url: str, content: str, headers: dict) -> httpx.Response:
        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self._http.post(url, content=content, headers=headers)
                if resp.status_code < 500:
                    return resp
                # XML-ошибка (<error>/<Fault>) — бизнес-ошибка, не ретраим:
                # пусть _parse поднимет типизированное исключение
                if resp.text.lstrip().startswith("<?xml"):
                    return resp
                last_err = TcRestError(f"HTTP {resp.status_code} от Teamcenter: {resp.text[:300]}")
            except httpx.TransportError as e:
                last_err = TcRestError(f"Сетевая ошибка Teamcenter: {e}")
            if attempt < self.retries:
                time.sleep(0.5 * (2 ** attempt))  # backoff: 0.5s, 1s
        raise last_err  # type: ignore[misc]

    def _parse(self, resp: httpx.Response) -> SoapResponse:
        if resp.status_code == 200:
            return parse_response_envelope(resp.text)
        # ошибки (в т.ч. Fault/error при HTTP 500) — пробуем разобрать тело
        if resp.text.lstrip().startswith("<?xml"):
            try:
                return parse_response_envelope(resp.text)
            except TcSoapError:  # TcAuthError и TcRestError — наследники TcSoapError
                raise
            except Exception:
                pass
        raise TcRestError(f"HTTP {resp.status_code} от Teamcenter: {resp.text[:300]}")

    # ─────────────── авторизация (cookie ASP.NET_SessionId) ───────────────
    def login(self, user: str, password: str, group: str = "", role: str = "") -> str:
        self._credentials = (user, password, group, role)
        inner = _inner(m.REST_SVC_SESSION, "LoginInput")
        for key, val in (("user", user), ("password", password),
                         ("group", group), ("role", role)):
            _child(inner, rest_schema_ns(m.REST_SVC_SESSION), key, val)
        resp = self._parse(self._post_with_retry(
            f"{self.base_url}{m.REST_PATH}/{m.REST_SVC_SESSION}/{m.REST_OP_LOGIN}",
            build_request_envelope(m.REST_SVC_SESSION, m.REST_OP_LOGIN,
                                   ET.tostring(inner, encoding="unicode")),
            {"Content-Type": "application/xml; charset=utf-8"}))
        sid = self._http.cookies.get(m.REST_SESSION_COOKIE) or ""
        if not sid:
            # фолбэк: токен из header (некоторые инсталляции не отдают cookie)
            sid = resp.token or ""
        if not sid:
            raise TcAuthError(f"Teamcenter REST не выдал сессию (cookie {m.REST_SESSION_COOKIE}) "
                              f"для пользователя {user}")
        self.session_id = sid
        return sid

    def logout(self) -> None:
        if self.session_id:
            try:
                inner = _inner(m.REST_SVC_SESSION, "LogoutInput")
                self.call(m.REST_SVC_SESSION, m.REST_OP_LOGOUT, inner)
            finally:
                self.session_id = ""

    # ─────────────── поиск и атрибуты (getItemAndRelatedObjects — проверено) ───────────────
    def _get_item_and_related(self, item_id: str = "", item_uid: str = "") -> SoapResponse:
        """getItemAndRelatedObjects: item + ревизии. Поиск по item_id или uid."""
        dm = rest_schema_ns(m.REST_SVC_DATA_MGMT)
        inner = _inner(m.REST_SVC_DATA_MGMT, "GetItemAndRelatedObjectsInput")
        infos = _child(inner, dm, "infos", clientId="sync")
        item_info = _child(infos, dm, "itemInfo",
                           clientId="sync",
                           useIdFirst="1" if item_id else "0",
                           uid=item_uid or "")
        if item_id:
            _child(item_info, dm, "ids", name="item_id", value=item_id)
        _child(infos, dm, "revInfo", clientId="sync", processing="All",
               useIdFirst="0", uid="", nRevs="2147483647", revisionRule="")
        _child(infos, dm, "datasetInfo", clientId="", uid="")
        return self.call(m.REST_SVC_DATA_MGMT, m.REST_OP_GET_ITEM_AND_RELATED, inner)

    @staticmethod
    def _descendants(el: ET.Element, name: str) -> list[ET.Element]:
        """Все потомки с localname == name (рекурсивно, в порядке документа)."""
        return [c for c in el.iter() if localname(c.tag) == name]

    def find_items(self, name: str, type_name: str | None = None) -> list[TcItem]:
        resp = self._get_item_and_related(item_id=name)
        items = [self._parse_item(el) for el in resp.find_all(m.REST_ITEM_PATH)]
        if type_name:
            items = [it for it in items if it.type_name == type_name]
        return items

    def get_item_revisions(self, item_uid: str) -> list[TcItem]:
        """Ревизии item'а: ищем item_revision среди потомков элементов item."""
        resp = self._get_item_and_related(item_uid=item_uid)
        revs: list[TcItem] = []
        for item_el in resp.find_all(m.REST_ITEM_PATH):
            for rev_el in self._descendants(item_el, "item_revision"):
                revs.append(self._parse_item(rev_el))
        return revs

    def _parse_item(self, el: ET.Element) -> TcItem:
        def t(name: str) -> str:
            for c in list(el):
                if localname(c.tag) == name and c.text:
                    return c.text.strip()
            return ""
        uid = el.attrib.get(m.ATTR_UID, "") or t(m.ATTR_UID)
        item = TcItem(
            uid=uid, item_id=t(m.ATTR_ITEM_ID) or t("item_id"),
            item_revision_id=t(m.ATTR_ITEM_REVISION_ID),
            object_name=t(m.ATTR_OBJECT_NAME) or t("name") or t("file_name"),
            object_string=t(m.ATTR_OBJECT_STRING),
            type_name=t(m.ATTR_TYPE_NAME) or t("dataset_type") or t("file_type"),
            owning_user=t(m.ATTR_OWNING_USER),
            last_modified=t(m.ATTR_LAST_MODIFIED), relation_name=t("relation_name"),
        )
        item.attrs = {localname(c.tag): (c.text or "").strip() for c in list(el)}
        item.attrs["uid"] = uid
        return item

    def get_properties(self, object_uid: str) -> TcItem | None:
        """Core-2008-06-DataManagement/getProperties (REST)."""
        dm = rest_schema_ns(m.REST_SVC_DATA_MGMT)
        inner = _inner(m.REST_SVC_DATA_MGMT, "GetPropertiesInput")
        inp = _child(inner, dm, "input")
        _child(inp, dm, "object", object_uid)
        resp = self.call(m.REST_SVC_DATA_MGMT, m.REST_OP_GET_PROPERTIES, inner)
        els = resp.find_all(m.REST_GET_PROPERTIES_PATH)
        return self._parse_item(els[0]) if els else None

    def get_requirements(self, revision_uids: list[str]) -> list[TcItem]:
        """Requirement-2011-06-Requirement/getRequirements (REST)."""
        if not revision_uids:
            return []
        ns = rest_schema_ns(m.REST_SVC_REQUIREMENT)
        inner = _inner(m.REST_SVC_REQUIREMENT, "GetRequirementsInput")
        for uid in revision_uids:
            _child(inner, ns, "requirement_revision", uid)
        resp = self.call(m.REST_SVC_REQUIREMENT, m.REST_OP_GET_REQUIREMENTS, inner)
        return [self._parse_item(el) for el in resp.find_all(m.REST_GET_REQUIREMENTS_PATH)]

    # ─────────────── структура с пагинацией ───────────────
    def get_children(self, node_uid: str) -> list[TcItem]:
        """Structure-2007-01-Structure/getChildren c пагинацией (page_size/start_index).

        Если сервер игнорирует пагинацию — вернёт всех детей сразу и цикл
        завершится после первой итерации.
        """
        ns = rest_schema_ns(m.REST_SVC_STRUCTURE)
        all_children: list[TcItem] = []
        start = 0
        while True:
            inner = _inner(m.REST_SVC_STRUCTURE, "GetChildrenInput")
            inp = _child(inner, ns, "input")
            _child(inp, ns, "child_uid", node_uid)
            _child(inp, ns, m.PAGE_SIZE_PARAM, self.page_size)
            _child(inp, ns, m.START_INDEX_PARAM, start)
            resp = self.call(m.REST_SVC_STRUCTURE, m.REST_OP_GET_CHILDREN, inner)
            page: list[TcItem] = []
            for el in resp.find_all(m.REST_CHILDREN_PATH):
                uid = el.attrib.get("uid") or next(
                    (c.text or "" for c in list(el) if localname(c.tag) == "child_uid"), "")
                child = TcItem(uid=uid, type_name=el.attrib.get("child_type", ""))
                for c in list(el):
                    name = localname(c.tag)
                    if c.text and name in ("relation_name", "child_uid", "child_type"):
                        setattr(child, name if name != "child_uid" else "uid", c.text.strip())
                    elif name == "sequence_no" and c.text:
                        child.sequence_no = int(c.text.strip())
                page.append(child)
            all_children.extend(page)
            if len(page) < self.page_size or not page:
                break
            start += len(page)
        return all_children

    # ─────────────── контент (IMAN_specification) ───────────────
    def find_datasets(self, object_uid: str, relation_name: str = m.REL_SPEC_CONTENT) -> list[TcItem]:
        ns = rest_schema_ns(m.REST_SVC_DATASET)
        inner = _inner(m.REST_SVC_DATASET, "FindDatasetsInput")
        inp = _child(inner, ns, "input")
        _child(inp, ns, "object", object_uid)
        _child(inp, ns, "relation_name", relation_name)
        resp = self.call(m.REST_SVC_DATASET, m.REST_OP_FIND_DATASETS, inner)
        return [self._parse_item(el) for el in resp.find_all(m.REST_DATASETS_PATH)]

    def get_contents(self, dataset_uid: str) -> list[TcItem]:
        ns = rest_schema_ns(m.REST_SVC_DATASET)
        inner = _inner(m.REST_SVC_DATASET, "GetContentsInput")
        inp = _child(inner, ns, "input")
        _child(inp, ns, "dataset", dataset_uid)
        resp = self.call(m.REST_SVC_DATASET, m.REST_OP_GET_CONTENTS, inner)
        files: list[TcItem] = []
        for el in resp.find_all(m.REST_CONTENTS_PATH):
            for f in list(el):
                if localname(f.tag) == "file":
                    files.append(self._parse_item(f))
        return files

    def get_file_read_ticket(self, file_uid: str, target: str = "Target") -> str:
        ns = rest_schema_ns(m.REST_SVC_FILE)
        inner = _inner(m.REST_SVC_FILE, "GetFileReadTicketInput")
        _child(inner, ns, "file", file_uid)
        _child(inner, ns, "target", target)
        resp = self.call(m.REST_SVC_FILE, m.REST_OP_GET_FILE_TICKET, inner)
        return resp.text(m.REST_TICKET_PATH)

    def download_file(self, ticket_url: str) -> bytes:
        """Скачивание файла контента по ticket (защита от SSRF + лимит размера)."""
        ticket = httpx.URL(ticket_url)
        if not self.allow_external_files and ticket.host not in self._allowed_hosts:
            raise TcRestError(f"Отказано: ticket ведёт на посторонний хост {ticket.host} "
                              f"(разрешены: {sorted(self._allowed_hosts)}); "
                              "отключить проверку: TC_ALLOW_EXTERNAL_FILES=true")
        r = self._http.get(ticket_url)
        if r.status_code != 200:
            raise TcRestError(f"HTTP {r.status_code} при скачивании файла контента: {r.text[:200]}")
        if len(r.content) > self.max_content_bytes:
            raise TcRestError(f"Файл контента больше лимита "
                              f"{self.max_content_bytes} байт (TC_MAX_CONTENT_BYTES)")
        return r.content

    # ─────────────── связи ───────────────
    def find_relations(self, object_uid: str, relation_type: str = m.REL_TRACE,
                       direction: str = "out") -> list[TcRelation]:
        ns = rest_schema_ns(m.REST_SVC_RELATION)
        inner = _inner(m.REST_SVC_RELATION, "FindRelationsInput")
        key = "primary_object" if direction == "out" else "secondary_object"
        _child(inner, ns, key, object_uid)
        _child(inner, ns, "relation_type", relation_type)
        resp = self.call(m.REST_SVC_RELATION, m.REST_OP_FIND_RELATIONS, inner)
        out: list[TcRelation] = []
        for el in resp.find_all(m.REST_RELATIONS_PATH):
            primary = secondary = ""
            for c in list(el):
                name = localname(c.tag)
                if name == "primary_object" and c.text:
                    primary = c.text.strip()
                elif name == "secondary_object" and c.text:
                    secondary = c.text.strip()
            out.append(TcRelation(primary=primary, secondary=secondary))
        return out

    # ─────────────── запись (write-back правки в TC) ───────────────
    def set_properties(self, object_uid: str, properties: dict[str, str]) -> dict:
        dm = rest_schema_ns(m.REST_SVC_DATA_MGMT)
        inner = _inner(m.REST_SVC_DATA_MGMT, "SetPropertiesInput")
        inp = _child(inner, dm, "input")
        _child(inp, dm, "object", object_uid)
        props = _child(inp, dm, "properties")
        for key, value in properties.items():
            _child(props, dm, key, value)
        resp = self.call(m.REST_SVC_DATA_MGMT, m.REST_OP_SET_PROPERTIES, inner)
        updated: dict[str, str] = {}
        el = resp.find(m.REST_SET_PROPERTIES_PATH)
        if el is not None:
            for c in list(el):
                if c.text:
                    updated[localname(c.tag)] = c.text.strip()
        return updated

    def close(self) -> None:
        self._http.close()

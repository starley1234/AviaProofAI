"""TeamcenterSoapClient — типизированный клиент SOA Teamcenter 11.

Каждая операция = один SOAP-вызов. Имена сервисов/операций/элементов —
в mapping.py (единственное место правки формата).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from app.services.teamcenter import mapping as m
from app.services.teamcenter.soap import TcAuthError, TcSoapError, build_envelope, localname, parse_soap_response


@dataclass
class TcItem:
    """Объект TC (Item / ItemRevision / Dataset / Relation)."""
    uid: str
    item_id: str = ""
    item_revision_id: str = ""
    object_name: str = ""
    object_string: str = ""          # текст требования (RMS: object_string)
    type_name: str = ""
    owning_user: str = ""
    last_modified: str = ""
    relation_name: str = ""          # для элементов структуры/связей
    sequence_no: int = 0
    attrs: dict = field(default_factory=dict)  # все атрибуты (пойдут в raw_data)


@dataclass
class TcRelation:
    primary: str
    secondary: str
    relation_type: str = m.REL_TRACE


class TeamcenterSoapClient:
    """Тонкий транспорт: знает mapping.py и умеет звать операции SOA.

    Устойчивость: таймауты (connect/read), ретраи с backoff, автоперелогин при
    истечении сессии, пагинация getChildren, лимит размера контента, защита
    от скачивания файлов с посторонних хостов (SSRF).
    """

    def __init__(self, base_url: str, timeout: float = 30.0, connect_timeout: float = 10.0,
                 retries: int = 2, page_size: int = m.DEFAULT_PAGE_SIZE,
                 verify: bool = True, max_content_bytes: int = 10 * 1024 * 1024,
                 allow_external_files: bool = False):
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.retries = retries
        self.page_size = page_size
        self.max_content_bytes = max_content_bytes
        self.allow_external_files = allow_external_files
        self._http = httpx.Client(timeout=(connect_timeout, timeout), verify=verify,
                                  follow_redirects=False)
        self.token: str | None = None
        self._credentials: tuple[str, str, str, str] | None = None
        self._allowed_hosts = {httpx.URL(base_url).host}

    # ─────────────── низкий уровень ───────────────
    def call(self, service: str, operation: str, params: dict | None = None) -> "SoapResponse":
        params = params or {}
        envelope = build_envelope(service, operation, params, token=self.token)
        try:
            resp = self._post_with_retry(envelope)
            return self._parse(resp)
        except TcAuthError:
            if operation != m.OP_LOGIN and self._credentials:
                # сессия истекла — перелогиниваемся и повторяем один раз
                self.login(*self._credentials)
                return self._parse(self._post_with_retry(
                    build_envelope(service, operation, params, token=self.token)))
            raise

    def _post_with_retry(self, envelope: str) -> httpx.Response:
        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                resp = self._http.post(self.base_url, content=envelope,
                                       headers={"Content-Type": "text/xml; charset=utf-8"})
                if resp.status_code < 500:
                    return resp
                # XML-ошибка (SOAP Fault/error) — бизнес-ошибка, не ретраим:
                # пусть _parse поднимет типизированное исключение
                if resp.text.lstrip().startswith("<?xml"):
                    return resp
                last_err = TcSoapError(f"HTTP {resp.status_code} от Teamcenter: {resp.text[:300]}")
            except httpx.TransportError as e:
                last_err = TcSoapError(f"Сетевая ошибка Teamcenter: {e}")
            if attempt < self.retries:
                time.sleep(0.5 * (2 ** attempt))  # backoff: 0.5s, 1s
        raise last_err  # type: ignore[misc]

    def _parse(self, resp: httpx.Response) -> "SoapResponse":
        # SOAP Fault'ы реальный TC отдаёт и с HTTP 500 — пробуем разобрать тело
        if resp.status_code != 200 and resp.text.lstrip().startswith("<?xml"):
            try:
                return parse_soap_response(resp.text)
            except (TcSoapError, TcAuthError):
                raise
            except Exception:
                pass
        if resp.status_code != 200:
            raise TcSoapError(f"HTTP {resp.status_code} от Teamcenter: {resp.text[:300]}")
        return parse_soap_response(resp.text)

    def _parse_item(self, el) -> TcItem:
        """<item_revision>/<item>/<dataset> элемент -> TcItem (localname-поля)."""
        def t(name: str) -> str:
            for c in list(el):
                if localname(c.tag) == name and c.text:
                    return c.text.strip()
            return ""
        uid = el.attrib.get(m.ATTR_UID, "") or t(m.ATTR_UID)
        item = TcItem(
            uid=uid, item_id=t(m.ATTR_ITEM_ID), item_revision_id=t(m.ATTR_ITEM_REVISION_ID),
            object_name=t(m.ATTR_OBJECT_NAME) or t("name") or t("file_name"),
            object_string=t(m.ATTR_OBJECT_STRING),
            type_name=t(m.ATTR_TYPE_NAME) or t("dataset_type") or t("file_type"),
            owning_user=t(m.ATTR_OWNING_USER),
            last_modified=t(m.ATTR_LAST_MODIFIED), relation_name=t("relation_name"),
        )
        # В raw_data уходят ВСЕ атрибуты элемента (см. ТЗ: хранить все атрибуты из TC)
        item.attrs = {localname(c.tag): (c.text or "").strip() for c in list(el)}
        item.attrs["uid"] = uid
        return item

    # ─────────────── авторизация ───────────────
    def login(self, user: str, password: str, group: str = "", role: str = "") -> str:
        self._credentials = (user, password, group, role)
        resp = self.call(m.SVC_SESSION, m.OP_LOGIN, {
            "user": user, "password": password, "group": group, "role": role, "discriminator": None,
        })
        token = resp.token
        if not token:
            raise TcAuthError(f"Teamcenter не вернул AuthenticationToken для пользователя {user}")
        self.token = token
        return token

    def logout(self) -> None:
        if self.token:
            try:
                self.call(m.SVC_SESSION, m.OP_LOGOUT)
            finally:
                self.token = None

    # ─────────────── поиск и атрибуты ───────────────
    def find_items(self, name: str, type_name: str | None = None) -> list[TcItem]:
        criteria: dict = {"name": name}
        if type_name:
            criteria["type"] = type_name
        resp = self.call(m.SVC_ITEM_FINDER, m.OP_FIND_ITEMS, {"criteria": criteria})
        return [self._parse_item(el) for el in resp.find_all(m.FIND_ITEMS_PATH)]

    def get_item_revisions(self, item_uid: str) -> list[TcItem]:
        resp = self.call(m.SVC_ITEM, m.OP_GET_ITEM_REVISIONS, {"input": {"item": item_uid}})
        return [self._parse_item(el) for el in resp.find_all(m.REVISIONS_PATH)]

    # ─────────────── атрибуты любого объекта (разделы, спецификация) ───────────────
    def get_properties(self, object_uid: str) -> TcItem | None:
        """Core-2006-03-DataManagement/getProperties: все атрибуты объекта."""
        resp = self.call(m.SVC_DATA_MGMT, m.OP_GET_PROPERTIES, {"input": {"object": object_uid}})
        els = resp.find_all(m.GET_PROPERTIES_PATH)
        return self._parse_item(els[0]) if els else None

    # ─────────────── требования (RMS: RequirementRevision) ───────────────
    def get_requirements(self, revision_uids: list[str]) -> list[TcItem]:
        """Атрибуты RequirementRevision (включая object_string — текст требования)."""
        if not revision_uids:
            return []
        resp = self.call(m.SVC_REQUIREMENT, m.OP_GET_REQUIREMENTS,
                         {"requirement_revision": revision_uids})
        return [self._parse_item(el) for el in resp.find_all(m.REQUIREMENTS_PATH)]

    # ─────────────── структура (дерево спецификации) ───────────────
    def get_children(self, node_uid: str) -> list[TcItem]:
        """Дети узла с пагинацией (page_size/start_index) — большие спецификации.

        Если сервер игнорирует пагинацию — вернёт всех детей сразу, цикл
        завершится после первой итерации.
        """
        children: list[TcItem] = []
        start = 0
        while True:
            resp = self.call(m.SVC_STRUCTURE, m.OP_GET_CHILDREN, {
                "input": {"child_uid": node_uid,
                          m.PAGE_SIZE_PARAM: self.page_size,
                          m.START_INDEX_PARAM: start}})
            page: list[TcItem] = []
            for el in resp.find_all(m.CHILDREN_PATH):
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
            children.extend(page)
            if len(page) < self.page_size or not page:
                break
            start += len(page)
        return children

    # ─────────────── контент (IMAN_specification) ───────────────
    def find_datasets(self, object_uid: str, relation_name: str = m.REL_SPEC_CONTENT) -> list[TcItem]:
        resp = self.call(m.SVC_DATASET, m.OP_FIND_DATASETS, {"input": {"object": object_uid, "relation_name": relation_name}})
        return [self._parse_item(el) for el in resp.find_all(m.DATASETS_PATH)]

    def get_contents(self, dataset_uid: str) -> list[TcItem]:
        """Файлы датасета: <output><file uid=..><file_name>..</file_name></file></output>."""
        resp = self.call(m.SVC_DATASET, m.OP_GET_CONTENTS, {"input": {"dataset": dataset_uid}})
        files: list[TcItem] = []
        for el in resp.find_all(m.CONTENTS_PATH):
            for f in list(el):
                if localname(f.tag) == "file":
                    files.append(self._parse_item(f))
        return files

    def get_file_read_ticket(self, file_uid: str, target: str = "Target") -> str:
        resp = self.call(m.SVC_FILE, m.OP_GET_FILE_TICKET, {"file": file_uid, "target": target})
        return resp.text(m.TICKET_PATH)

    def download_file(self, ticket_url: str) -> bytes:
        """Скачивание файла контента по ticket (защита от SSRF + лимит размера)."""
        ticket = httpx.URL(ticket_url)
        if not self.allow_external_files and ticket.host not in self._allowed_hosts:
            raise TcSoapError(f"Отказано: ticket ведёт на посторонний хост {ticket.host} "
                              f"(разрешены: {sorted(self._allowed_hosts)}); "
                              "отключить проверку: TC_ALLOW_EXTERNAL_FILES=true")
        r = self._http.get(ticket_url)
        if r.status_code != 200:
            raise TcSoapError(f"HTTP {r.status_code} при скачивании файла контента: {r.text[:200]}")
        if len(r.content) > self.max_content_bytes:
            raise TcSoapError(f"Файл контента больше лимита "
                              f"{self.max_content_bytes} байт (TC_MAX_CONTENT_BYTES)")
        return r.content

    # ─────────────── связи (трассируемость) ───────────────
    def find_relations(self, object_uid: str, relation_type: str = m.REL_TRACE,
                       direction: str = "out") -> list[TcRelation]:
        """Связи объекта.

        direction="out" — <primary_object>UID</primary_object> (требование -> связанное),
        direction="in"  — <secondary_object>UID</secondary_object> (связанное -> требование).
        """
        key = "primary_object" if direction == "out" else "secondary_object"
        resp = self.call(m.SVC_RELATION, m.OP_FIND_RELATIONS, {key: object_uid, "relation_type": relation_type})
        out: list[TcRelation] = []
        for el in resp.find_all(m.RELATIONS_PATH):
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
        """Обновляет атрибуты ревизии. Ответ: обновлённый объект (output)."""
        resp = self.call(m.SVC_ITEM, m.OP_SET_PROPERTIES, {"input": {"object": object_uid, "properties": properties}})
        updated: dict[str, str] = {}
        el = resp.find(m.SET_PROPERTIES_PATH)
        if el is not None:
            for c in list(el):
                if c.text:
                    updated[localname(c.tag)] = c.text.strip()
        return updated

    def close(self) -> None:
        self._http.close()

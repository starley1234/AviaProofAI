"""TeamcenterSoapClient — типизированный клиент SOA Teamcenter 11.

Каждая операция = один SOAP-вызов. Имена сервисов/операций/элементов —
в mapping.py (единственное место правки формата).
"""
from __future__ import annotations

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
    """Тонкий транспорт: знает mapping.py и умеет звать операции SOA."""

    def __init__(self, base_url: str, timeout: float = 30.0, verify: bool = True):
        self.base_url = base_url.rstrip("/") + "/"
        self.timeout = timeout
        self._http = httpx.Client(timeout=timeout, verify=verify)
        self.token: str | None = None

    # ─────────────── низкий уровень ───────────────
    def call(self, service: str, operation: str, params: dict | None = None) -> "SoapResponse":
        params = params or {}
        envelope = build_envelope(service, operation, params, token=self.token)
        resp = self._http.post(self.base_url, content=envelope, headers={"Content-Type": "text/xml; charset=utf-8"})
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
        """Дети узла: <output><child_uid/><relation_name/><child_type/><sequence_no/></output>."""
        resp = self.call(m.SVC_STRUCTURE, m.OP_GET_CHILDREN, {"input": {"child_uid": node_uid}})
        children: list[TcItem] = []
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
            children.append(child)
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
        r = self._http.get(ticket_url)
        if r.status_code != 200:
            raise TcSoapError(f"HTTP {r.status_code} при скачивании файла контента: {r.text[:200]}")
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

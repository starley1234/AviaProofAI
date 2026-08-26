"""Обработчики SOAP-операций заглушки Teamcenter.

Каждый обработчик строит XML-ответ по тем же константам mapping.py,
которыми пользуется клиент синхронизации, — поэтому тесты проверяют
контракт «клиент <-> Teamcenter» целиком.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from app.services.teamcenter import mapping as m
from app.services.teamcenter.soap import localname
from stub_tc.store import TcStore


def _ns(service: str) -> str:
    return f"http://www.teamcenter.com/soa/services/{service}"


def E(ns: str, name: str, text: str | None = None, **attrs) -> ET.Element:
    el = ET.Element(f"{{{ns}}}{name}", {k: str(v) for k, v in attrs.items()})
    if text is not None:
        el.text = str(text)
    return el


def S(parent: ET.Element, ns: str, name: str, text: str | None = None, **attrs) -> ET.Element:
    el = E(ns, name, text, **attrs)
    parent.append(el)
    return el


def _revision_element(ns: str, tag: str, rev: dict) -> ET.Element:
    """Элемент ревизии со всеми атрибутами (как в getItemRevisions/getRequirements)."""
    el = E(ns, tag, uid=rev["uid"])
    for key in (m.ATTR_ITEM_ID, m.ATTR_ITEM_REVISION_ID, m.ATTR_OBJECT_NAME, m.ATTR_OBJECT_STRING,
                m.ATTR_TYPE_NAME, m.ATTR_OWNING_USER, m.ATTR_LAST_MODIFIED):
        S(el, ns, key, rev.get(key, ""))
    return el


def _child_of(req: ET.Element, local: str) -> str:
    for c in list(req):
        if localname(c.tag) == local and c.text:
            return c.text.strip()
    return ""


# ─────────────────────────── обработчики ───────────────────────────
def _login(store: TcStore, req: ET.Element, base_url: str):
    user = _child_of(req, "user")
    password = _child_of(req, "password")
    result = store.login(user, password)
    if result is None:
        return None, "Authentication failed: неверное имя пользователя или пароль"
    ns = _ns(m.SVC_SESSION)
    resp = E(ns, "loginResponse")
    u = S(resp, ns, "user", uid=f"user-{user}")
    S(u, ns, "userid", result["userid"])
    S(u, ns, "first_name", result["first_name"])
    S(u, ns, "last_name", result["last_name"])
    S(u, ns, "name", user)
    return resp, result["token"]


def _logout(store: TcStore, req: ET.Element, base_url: str):
    ns = _ns(m.SVC_SESSION)
    return E(ns, "logoutResponse"), None


def _find_items(store: TcStore, req: ET.Element, base_url: str):
    criteria = next((c for c in list(req) if localname(c.tag) == "criteria"), None)
    name = _child_of(criteria, "name") if criteria is not None else ""
    type_name = _child_of(criteria, "type") if criteria is not None else None
    ns = _ns(m.SVC_ITEM_FINDER)
    resp = E(ns, "findItemsResponse")
    found = S(resp, ns, "found")
    for item in store.find_items(name, type_name):
        it = S(found, ns, "item", uid=item["uid"])
        S(it, ns, m.ATTR_ITEM_ID, item["item_id"])
        S(it, ns, m.ATTR_TYPE_NAME, item["type_name"])
        S(it, ns, m.ATTR_OBJECT_NAME, item["name"])
        rl = S(it, ns, "revision_list")
        for rev_uid in item["revisions"]:
            rl.append(_revision_element(ns, "item_revision", store.revisions[rev_uid]))
    return resp, None


def _get_item_revisions(store: TcStore, req: ET.Element, base_url: str):
    input_el = next((c for c in list(req) if localname(c.tag) == "input"), None)
    item_uid = _child_of(input_el, "item") if input_el is not None else ""
    ns = _ns(m.SVC_ITEM)
    resp = E(ns, "getItemRevisionsResponse")
    for rev in store.item_revisions(item_uid):
        resp.append(_revision_element(ns, "item_revision", rev))
    return resp, None


def _get_properties(store: TcStore, req: ET.Element, base_url: str):
    input_el = next((c for c in list(req) if localname(c.tag) == "input"), None)
    obj_uid = _child_of(input_el, "object") if input_el is not None else ""
    ns = _ns(m.SVC_DATA_MGMT)
    resp = E(ns, "getPropertiesResponse")
    rev = store.revisions.get(obj_uid)
    if rev is None:
        return None, f"Объект {obj_uid} не найден"
    out = S(resp, ns, "output")
    out.append(_revision_element(ns, "object", rev))
    return resp, None


def _get_requirements(store: TcStore, req: ET.Element, base_url: str):
    ns = _ns(m.SVC_REQUIREMENT)
    resp = E(ns, "getRequirementsResponse")
    for el in list(req):
        if localname(el.tag) == "requirement_revision" and el.text:
            rev = store.get_requirement(el.text.strip())
            if rev:
                resp.append(_revision_element(ns, "requirement", rev))
    return resp, None


def _get_children(store: TcStore, req: ET.Element, base_url: str):
    """getChildren c поддержкой пагинации (page_size/start_index) — как реальный TC."""
    input_el = next((c for c in list(req) if localname(c.tag) == "input"), None)
    node_uid = _child_of(input_el, "child_uid") if input_el is not None else ""
    page_size = int(_child_of(input_el, "page_size") or 0) if input_el is not None else 0
    start_index = int(_child_of(input_el, "start_index") or 0) if input_el is not None else 0
    children = store.children(node_uid)
    if page_size > 0:
        children = children[start_index:start_index + page_size]
    ns = _ns(m.SVC_STRUCTURE)
    resp = E(ns, "getChildrenResponse")
    for ch in children:
        out = S(resp, ns, "output", uid=ch["uid"], child_type=ch["type_name"])
        S(out, ns, "child_uid", ch["uid"])
        S(out, ns, "relation_name", ch["relation_name"])
        S(out, ns, "child_type", ch["type_name"])
        S(out, ns, "sequence_no", ch["sequence_no"])
    return resp, None


def _get_item_and_related(store: TcStore, req: ET.Element, base_url: str):
    """getItemAndRelatedObjects (REST, проверенный формат заказчика):
    <infos><itemInfo useIdFirst="1" uid=""><ids name="item_id" value="..."/></itemInfo>
           <revInfo processing="All" .../></infos>
    Ответ: <item> + вложенные <item_revision> в revision_list."""
    ns = _ns(m.SVC_DATA_MGMT)
    infos = next((c for c in list(req) if localname(c.tag) == "infos"), None)
    item_id, item_uid = "", ""
    for ii in list(infos or []):
        if localname(ii.tag) != "itemInfo":
            continue
        item_uid = ii.attrib.get("uid", "")
        for ids in list(ii):
            if localname(ids.tag) == "ids" and ids.attrib.get("name") == "item_id":
                item_id = ids.attrib.get("value", "")
    items = store.find_items(item_id) if item_id else \
        ([store.items[item_uid]] if item_uid in store.items else [])
    resp = E(ns, "GetItemAndRelatedObjectsResponse")
    for item in items:
        it = S(resp, ns, "item", uid=item["uid"])
        S(it, ns, "clientId", "sync")
        S(it, ns, m.ATTR_ITEM_ID, item["item_id"])
        S(it, ns, m.ATTR_TYPE_NAME, item["type_name"])
        S(it, ns, m.ATTR_OBJECT_NAME, item["name"])
        rl = S(it, ns, "revision_list")
        for rev_uid in item["revisions"]:
            rl.append(_revision_element(ns, "item_revision", store.revisions[rev_uid]))
    return resp, None


def _find_datasets(store: TcStore, req: ET.Element, base_url: str):
    input_el = next((c for c in list(req) if localname(c.tag) == "input"), None)
    obj_uid = _child_of(input_el, "object") if input_el is not None else ""
    rel = _child_of(input_el, "relation_name") if input_el is not None else None
    ns = _ns(m.SVC_DATASET)
    resp = E(ns, "findDatasetsResponse")
    out = S(resp, ns, "output")
    for ds in store.datasets_of(obj_uid, rel or None):
        d = S(out, ns, "dataset", uid=ds["uid"])
        S(d, ns, "name", ds["name"])
        S(d, ns, "type_name", ds["type_name"])
        S(d, ns, "relation_name", ds["relation_name"])
    return resp, None


def _get_contents(store: TcStore, req: ET.Element, base_url: str):
    input_el = next((c for c in list(req) if localname(c.tag) == "input"), None)
    ds_uid = _child_of(input_el, "dataset") if input_el is not None else ""
    ns = _ns(m.SVC_DATASET)
    resp = E(ns, "getContentsResponse")
    out = S(resp, ns, "output")
    for f in store.dataset_files(ds_uid):
        fe = S(out, ns, "file", uid=f["uid"])
        S(fe, ns, "file_name", f["file_name"])
        S(fe, ns, "object_name", f["file_name"])
        S(fe, ns, "file_type", f["file_type"])
    return resp, None


def _get_file_ticket(store: TcStore, req: ET.Element, base_url: str):
    file_uid = _child_of(req, "file")
    ns = _ns(m.SVC_FILE)
    resp = E(ns, "getFileReadTicketResponse")
    S(resp, ns, "ticket", f"{base_url}tc/files/{file_uid}")
    return resp, None


def _find_relations(store: TcStore, req: ET.Element, base_url: str):
    primary = _child_of(req, "primary_object")
    secondary = _child_of(req, "secondary_object")
    rel_type = _child_of(req, "relation_type") or m.REL_TRACE
    direction = "out" if primary else "in"
    obj_uid = primary or secondary
    ns = _ns(m.SVC_RELATION)
    resp = E(ns, "findRelationsResponse")
    out = S(resp, ns, "output")
    for rel in store.relations(obj_uid, rel_type, direction):
        r = S(out, ns, "relation", uid=rel["uid"])
        S(r, ns, "primary_object", rel["primary_object"])
        S(r, ns, "secondary_object", rel["secondary_object"])
        S(r, ns, "relation_type", rel["relation_type"])
    return resp, None


def _set_properties(store: TcStore, req: ET.Element, base_url: str):
    input_el = next((c for c in list(req) if localname(c.tag) == "input"), None)
    obj_uid = _child_of(input_el, "object") if input_el is not None else ""
    props_el = next((c for c in list(input_el or []) if localname(c.tag) == "properties"), None)
    properties = {localname(c.tag): (c.text or "") for c in list(props_el or [])}
    ns = _ns(m.SVC_ITEM)
    resp = E(ns, "setPropertiesResponse")
    rev = store.set_properties(obj_uid, properties)
    if rev is None:
        return None, f"Объект {obj_uid} не найден"
    out = S(resp, ns, "output")
    for key, value in rev.items():
        if key in ("uid", "datasets", "children", "relations"):
            continue
        S(out, ns, key, value)
    return resp, None


# реестр операций: localname операции -> (сервис, обработчик)
OPERATIONS: dict[str, tuple[str, object]] = {
    m.OP_LOGIN: (m.SVC_SESSION, _login),
    m.OP_LOGOUT: (m.SVC_SESSION, _logout),
    m.OP_GET_PROPERTIES: (m.SVC_DATA_MGMT, _get_properties),
    m.OP_FIND_ITEMS: (m.SVC_ITEM_FINDER, _find_items),
    m.OP_GET_ITEM_REVISIONS: (m.SVC_ITEM, _get_item_revisions),
    m.OP_GET_REQUIREMENTS: (m.SVC_REQUIREMENT, _get_requirements),
    m.OP_GET_CHILDREN: (m.SVC_STRUCTURE, _get_children),
    m.OP_FIND_DATASETS: (m.SVC_DATASET, _find_datasets),
    m.OP_GET_CONTENTS: (m.SVC_DATASET, _get_contents),
    m.OP_GET_FILE_TICKET: (m.SVC_FILE, _get_file_ticket),
    m.OP_FIND_RELATIONS: (m.SVC_RELATION, _find_relations),
    m.OP_SET_PROPERTIES: (m.SVC_ITEM, _set_properties),
}

# REST-операции: URL-имя операции -> обработчик (тот же, что у SOAP, где возможно)
REST_OPERATIONS: dict[str, tuple[str, object]] = {
    m.REST_OP_LOGIN: (m.REST_SVC_SESSION, _login),
    m.REST_OP_LOGOUT: (m.REST_SVC_SESSION, _logout),
    m.REST_OP_GET_ITEM_AND_RELATED: (m.REST_SVC_DATA_MGMT, _get_item_and_related),
    m.REST_OP_GET_PROPERTIES: (m.REST_SVC_DATA_MGMT, _get_properties),
    m.REST_OP_GET_REQUIREMENTS: (m.REST_SVC_REQUIREMENT, _get_requirements),
    m.REST_OP_GET_CHILDREN: (m.REST_SVC_STRUCTURE, _get_children),
    m.REST_OP_FIND_DATASETS: (m.REST_SVC_DATASET, _find_datasets),
    m.REST_OP_GET_CONTENTS: (m.REST_SVC_DATASET, _get_contents),
    m.REST_OP_GET_FILE_TICKET: (m.REST_SVC_FILE, _get_file_ticket),
    m.REST_OP_FIND_RELATIONS: (m.REST_SVC_RELATION, _find_relations),
    m.REST_OP_SET_PROPERTIES: (m.REST_SVC_DATA_MGMT, _set_properties),
}


def dispatch(store: TcStore, operation: str, req_body: ET.Element,
             base_url: str, token: str | None) -> tuple[ET.Element | None, str | None, str | None]:
    """Возвращает (response_element, header_token, ошибка). SOAP-диспетчер."""
    if operation not in OPERATIONS:
        return None, None, f"Неизвестная операция: {operation}"
    service, handler = OPERATIONS[operation]
    # все операции, кроме login, требуют токен
    if operation != m.OP_LOGIN and store.auth_user(token or "") is None:
        return None, None, "Authentication failed: требуется валидный AuthenticationToken"
    resp, header = handler(store, req_body, base_url)
    if resp is None:
        return None, None, header or f"Ошибка выполнения {operation}"
    return resp, header, None


def dispatch_rest(store: TcStore, operation: str, req_body: ET.Element,
                  base_url: str) -> tuple[ET.Element | None, str | None, str | None]:
    """REST-диспетчер: операции из REST_OPERATIONS, login выдаёт сессию (cookie)."""
    if operation not in REST_OPERATIONS:
        return None, None, f"Неизвестная REST-операция: {operation}"
    service, handler = REST_OPERATIONS[operation]
    resp, header = handler(store, req_body, base_url)
    if resp is None:
        return None, None, header or f"Ошибка выполнения {operation}"
    if operation == m.REST_OP_LOGIN:
        # header здесь — токен, уже сохранённый в store.tokens;
        # используем его как значение cookie ASP.NET_SessionId
        if not header:
            return None, None, "REST login не вернул сессию"
    return resp, header, None

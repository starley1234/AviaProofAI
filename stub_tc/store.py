"""In-memory «Teamcenter» заглушки: объекты, структура, датасеты, связи.

Данные загружаются из YAML-фикстуры (stub_tc/fixtures/*.yaml).
Каждый объект — dict, uid'ы читаемые: 'item-SPEC-BRAKE-001', 'rev-REQ-1101-A' и т.п.
"""
from __future__ import annotations

import html
import secrets
import uuid
from datetime import datetime, timezone

import yaml

from app.services.teamcenter import mapping as m


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


class TcStore:
    def __init__(self, fixture_path: str | None = None):
        self.users: dict[str, dict] = {}          # login -> {login,password,userid,first_name,last_name}
        self.tokens: dict[str, str] = {}          # token -> login
        self.items: dict[str, dict] = {}          # item_uid -> {uid,item_id,type_name,name,revisions:[rev_uid]}
        self.revisions: dict[str, dict] = {}      # rev_uid -> см. _make_revision
        self.datasets: dict[str, dict] = {}       # ds_uid -> {uid,type_name,name,relation_name,files:[file_uid]}
        self.files: dict[str, dict] = {}          # file_uid -> {uid,file_name,file_type,mime,content}
        self.spec_id: str = ""
        self.spec_rev_uid: str = ""
        if fixture_path:
            self.load(fixture_path)

    # ─────────────────────────── загрузка фикстуры ───────────────────────────
    def load(self, fixture_path: str) -> None:
        with open(fixture_path, encoding="utf-8") as f:
            fx = yaml.safe_load(f)

        for u in fx["users"]:
            self.users[u["login"]] = u

        # разделы и требования
        sections = {s["id"]: s for s in fx["sections"]}
        reqs_by_section: dict[str, list[dict]] = {sid: [] for sid in sections}
        for r in fx["requirements"]:
            reqs_by_section[r["section"]].append(r)
        for lst in reqs_by_section.values():
            lst.sort(key=lambda r: r["seq"])

        # 1) предметы/ревизии: спецификация, разделы, требования
        spec_item_uid = f"item-{fx['spec']['id']}"
        spec_rev_uid = f"rev-{fx['spec']['id']}-{fx['spec']['revision']}"
        self.spec_id = fx["spec"]["id"]
        self.spec_rev_uid = spec_rev_uid
        self._add_item(spec_item_uid, fx["spec"]["id"], m.TYPE_SPECIFICATION,
                       fx["spec"]["name"], [spec_rev_uid])
        self._add_revision(spec_rev_uid, fx["spec"]["id"], fx["spec"]["revision"],
                           fx["spec"]["name"], m.TYPE_SPECIFICATION, "infodba")

        for sid, s in sections.items():
            item_uid, rev_uid = f"item-{sid}", f"rev-{sid}-A"
            self._add_item(item_uid, sid, m.TYPE_SPEC_SECTION, s["name"], [rev_uid])
            self._add_revision(rev_uid, sid, "A", s["name"], m.TYPE_SPEC_SECTION, "infodba")

        for r in fx["requirements"]:
            item_uid, rev_uid = f"item-{r['id']}", f"rev-{r['id']}-A"
            self._add_item(item_uid, r["id"], m.TYPE_REQUIREMENT, r["name"], [rev_uid])
            self._add_revision(rev_uid, r["id"], "A", r["name"], m.TYPE_REQUIREMENT,
                               r.get("owner", "infodba"), object_string=r["text"].strip())
            self._add_content_dataset(rev_uid, r["id"], r["name"], r["text"].strip())

        # 2) структура: спецификация -> разделы -> требования
        for seq, sid in enumerate(sections, start=1):
            self._add_child(self.spec_rev_uid, f"rev-{sid}-A", m.REL_SPEC_REFERENCE, seq)
            for r in reqs_by_section[sid]:
                self._add_child(f"rev-{sid}-A", f"rev-{r['id']}-A", m.REL_SPEC_REFERENCE, r["seq"])

        # 3) связи трассируемости TC_Requirement_Trace_Relation
        for parent, child in fx.get("trace_links", []):
            self._add_relation(f"rev-{parent}-A", f"rev-{child}-A", m.REL_TRACE)

    # ─────────────────────────── примитивы ───────────────────────────
    def _add_item(self, uid: str, item_id: str, type_name: str, name: str, revisions: list[str]) -> None:
        self.items[uid] = {"uid": uid, "item_id": item_id, "type_name": type_name,
                           "name": name, "revisions": revisions}

    def _add_revision(self, uid: str, item_id: str, rev_id: str, name: str, type_name: str,
                      owner: str, object_string: str = "") -> None:
        self.revisions[uid] = {
            "uid": uid, "item_id": item_id, "item_revision_id": rev_id,
            "object_name": name, "object_string": object_string, "type_name": type_name,
            "owning_user": owner, "last_modified": self._now(),
            "datasets": [], "children": [], "relations": [],
        }

    def _add_child(self, parent_rev: str, child_rev: str, relation: str, seq: int) -> None:
        self.revisions[parent_rev]["children"].append(
            {"uid": child_rev, "relation_name": relation, "type_name": self.revisions[child_rev]["type_name"],
             "sequence_no": seq})

    def _add_relation(self, primary: str, secondary: str, relation_type: str) -> None:
        rel = {"uid": _uid("rel"), "primary_object": primary, "secondary_object": secondary,
               "relation_type": relation_type}
        self.revisions[primary]["relations"].append(rel)
        self.revisions[secondary]["relations"].append(rel)

    def _add_content_dataset(self, rev_uid: str, req_id: str, name: str, text: str) -> None:
        """Датасет IMAN_specification (HTML) с текстом требования."""
        ds_uid, file_uid = f"ds-{req_id}", f"file-{req_id}-content"
        body = "".join(f"<p>{html.escape(p.strip())}</p>" for p in text.splitlines() if p.strip())
        self.datasets[ds_uid] = {"uid": ds_uid, "type_name": m.DATASET_TYPE_HTML,
                                 "name": f"{name} (спецификация)", "relation_name": m.REL_SPEC_CONTENT,
                                 "files": [file_uid]}
        self.files[file_uid] = {"uid": file_uid, "file_name": f"{req_id}.html",
                                "file_type": "HTML", "mime": m.CONTENT_MIME_HTML,
                                "content": f"<html><head><title>{html.escape(name)}</title></head>"
                                           f"<body><h1>{html.escape(name)}</h1>{body}</body></html>".encode("utf-8")}
        self.revisions[rev_uid]["datasets"].append(ds_uid)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ─────────────────────────── операции (вызываются из handlers) ───────────────────────────
    def login(self, user: str, password: str) -> dict | None:
        u = self.users.get(user)
        if u is None or u["password"] != password:
            return None
        token = f"token-{user}-{secrets.token_hex(6)}"
        self.tokens[token] = user
        return {**u, "token": token}

    def create_session(self, user: str) -> str:
        """Сессия REST (значение cookie ASP.NET_SessionId)."""
        sid = f"session-{user}-{secrets.token_hex(6)}"
        self.tokens[sid] = user
        return sid

    def logout(self, token: str) -> None:
        self.tokens.pop(token, None)

    def auth_user(self, token: str) -> str | None:
        return self.tokens.get(token)

    def find_items(self, name: str, type_name: str | None = None) -> list[dict]:
        return [it for it in self.items.values()
                if it["item_id"] == name and (type_name is None or it["type_name"] == type_name)]

    def item_revisions(self, item_uid: str) -> list[dict]:
        it = self.items.get(item_uid)
        return [self.revisions[r] for r in (it["revisions"] if it else [])]

    def get_requirement(self, rev_uid: str) -> dict | None:
        return self.revisions.get(rev_uid)

    def children(self, rev_uid: str) -> list[dict]:
        return self.revisions.get(rev_uid, {}).get("children", [])

    def datasets_of(self, rev_uid: str, relation_name: str | None = None) -> list[dict]:
        out = []
        for ds_uid in self.revisions.get(rev_uid, {}).get("datasets", []):
            ds = self.datasets[ds_uid]
            if relation_name is None or ds["relation_name"] == relation_name:
                out.append(ds)
        return out

    def dataset_files(self, ds_uid: str) -> list[dict]:
        ds = self.datasets.get(ds_uid, {})
        return [self.files[f] for f in ds.get("files", [])]

    def file(self, file_uid: str) -> dict | None:
        return self.files.get(file_uid)

    def relations(self, rev_uid: str, relation_type: str, direction: str) -> list[dict]:
        """direction: out — где объект primary; in — где объект secondary."""
        out = []
        for rel in self.revisions.get(rev_uid, {}).get("relations", []):
            if rel["relation_type"] != relation_type:
                continue
            if direction == "out" and rel["primary_object"] == rev_uid:
                out.append(rel)
            elif direction == "in" and rel["secondary_object"] == rev_uid:
                out.append(rel)
        return out

    def set_properties(self, rev_uid: str, properties: dict[str, str]) -> dict | None:
        rev = self.revisions.get(rev_uid)
        if rev is None:
            return None
        for key, value in properties.items():
            rev[key] = value
        if "object_string" in properties:
            # как в реальном RMS: текст object_string синхронизируется
            # с HTML-датасетом IMAN_specification (контентом спецификации)
            self._sync_dataset_content(rev, properties["object_string"])
        rev["last_modified"] = self._now()
        return rev

    def _sync_dataset_content(self, rev: dict, text: str) -> None:
        for ds_uid in rev.get("datasets", []):
            ds = self.datasets.get(ds_uid)
            if not ds:
                continue
            for file_uid in ds.get("files", []):
                f = self.files.get(file_uid)
                if f and f.get("file_name", "").endswith(".html"):
                    name = rev.get("object_name", "Требование")
                    body = "".join(f"<p>{html.escape(p.strip())}</p>"
                                   for p in text.splitlines() if p.strip())
                    f["content"] = (f"<html><head><title>{html.escape(name)}</title></head>"
                                    f"<body><h1>{html.escape(name)}</h1>{body}</body></html>").encode("utf-8")

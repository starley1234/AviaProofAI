"""TeamcenterSync — ETL-модуль: Teamcenter 11 (SOA) -> PostgreSQL.

Поток (см. docs/teamcenter.md):
  1. Авторизация (Core-2007-01-Session/login) -> AuthenticationToken.
  2. Поиск корневой спецификации (ItemFinder/findItems).
  3. Рекурсивный обход дерева (Structure/getChildren):
       Specification -> SpecSection* -> (SpecSection | RequirementRevision)*
  4. Для каждой RequirementRevision:
       - атрибуты и текст  (Requirement/getRequirements: object_string);
       - контент из HTML/Text-датасета по связи IMAN_specification
         (Dataset/findDatasets -> getContents -> File/getFileReadTicket -> скачивание);
       - связи трассируемости (Relation/findRelations, TC_Requirement_Trace_Relation).
  5. Upsert в таблицу requirements (+ requirement_snapshots при изменении текста),
     журнал в sync_runs.

Запись обратно в TC (push_edit) — только если разрешено настройками
(глобальный выключатель + право пользователя, см. services/settings_service.py).
"""
from __future__ import annotations

import hashlib
import html as html_lib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (Requirement, RequirementSnapshot, SyncRun)
from app.services.settings_service import SettingsService, WriteToTcForbidden
from app.services.teamcenter import mapping as m
from app.services.teamcenter.client import TeamcenterSoapClient, TcItem
from app.services.teamcenter.soap import TcAuthError


class SyncError(Exception):
    """Ошибка синхронизации с Teamcenter."""


@dataclass
class RequirementRecord:
    """Узел дерева спецификации, выгруженный из TC (до записи в БД)."""
    uid: str
    item_id: str
    item_revision_id: str
    name: str
    type: str = m.TYPE_REQUIREMENT                  # Specification | SpecSection | RequirementRevision
    text: str = ""
    parent_uid: str | None = None
    section_path: str = ""
    sequence_no: int = 0
    attrs: dict = field(default_factory=dict)      # все атрибуты TC -> raw_data
    trace_in: list[str] = field(default_factory=list)
    trace_out: list[str] = field(default_factory=list)
    content_source: str = ""                        # object_string | IMAN_specification


# ─────────────────────────── извлечение текста из HTML-датасета ───────────────────────────
class _TextExtractor(HTMLParser):
    """Собирает текст из HTML, отбрасывая script/style."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "title"):
            self._skip += 1
        elif tag in ("p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "table"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "title") and self._skip:
            self._skip -= 1
        elif tag in ("p", "div", "li", "tr", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        return re.sub(r"[ \t\r\f\v]+", " ", raw).replace(" \n", "\n").strip()


def extract_text_from_html(raw: bytes) -> str:
    """HTML датасета IMAN_specification -> чистый текст требования."""
    for enc in ("utf-8", "cp1251", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    parser = _TextExtractor()
    try:
        parser.feed(text)
    except Exception:  # битый HTML не должен ронять синхронизацию
        return re.sub(r"<[^>]+>", " ", text)
    return parser.text()


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ─────────────────────────── синхронизация ───────────────────────────
class TeamcenterSync:
    def __init__(self, client: TeamcenterSoapClient, db: Session,
                 settings: SettingsService | None = None):
        self.client = client
        self.db = db
        self.settings = settings or SettingsService(db)
        self._spec_uid_cache: str | None = None

    # ---------- точка входа ----------
    def run(self, spec_id: str, max_depth: int = 50) -> SyncRun:
        """Полная синхронизация спецификации требований. Возвращает SyncRun."""
        run = SyncRun(status="running", spec_uid="")
        self.db.add(run)
        self.db.flush()
        try:
            self._login()
            spec = self._find_spec(spec_id)
            run.spec_uid = spec.uid
            records: list[RequirementRecord] = [
                # корень дерева: сама спецификация (для целостности parent_uid)
                RequirementRecord(uid=spec.uid, item_id=spec.item_id,
                                  item_revision_id=spec.item_revision_id, name=spec.object_name,
                                  type=m.TYPE_SPECIFICATION, attrs=spec.attrs),
            ]
            self._walk(spec.uid, parent_uid=None, path="", depth=0, max_depth=max_depth, out=records)
            stats = self._upsert_all(records)
            self._refresh_statuses()
            run.status = "ok"
            run.stats = stats
            run.finished_at = datetime.now(timezone.utc)
        except Exception as e:  # noqa: BLE001 — журналируем любую ошибку синхронизации
            run.status = "failed"
            run.error = f"{type(e).__name__}: {e}"
            run.finished_at = datetime.now(timezone.utc)
            self.db.rollback()
            self.db.add(run)
        return run

    # ---------- шаги ----------
    def _login(self) -> None:
        from app.config import get_settings
        s = get_settings()
        try:
            self.client.login(s.tc_user, s.tc_password)
        except Exception as e:
            raise SyncError(f"Авторизация в Teamcenter не удалась ({s.tc_user}): {e}") from e

    def _find_spec(self, spec_id: str) -> TcItem:
        items = self.client.find_items(spec_id, m.TYPE_SPECIFICATION)
        if not items:
            raise SyncError(f"Спецификация {spec_id!r} не найдена в Teamcenter")
        item = items[0]
        revs = self.client.get_item_revisions(item.uid)
        if not revs:
            raise SyncError(f"У спецификации {spec_id} нет ревизий")
        return revs[0]  # последняя ревизия (в заглушке — одна)

    def _walk(self, node_uid: str, parent_uid: str | None, path: str,
              depth: int, max_depth: int, out: list[RequirementRecord]) -> None:
        """Рекурсивный обход дерева спецификации.

        Узел -> дети (getChildren). SpecSection -> углубляемся, формируя section_path
        («1», «1.2», ...). RequirementRevision -> выгружаем запись.
        """
        if depth > max_depth:
            raise SyncError(f"Структура глубже max_depth={max_depth}; возможно, цикл связей")
        children = self.client.get_children(node_uid)
        for child in children:
            seq = child.sequence_no
            child_path = f"{path}.{seq}" if path else str(seq)
            if child.type_name == m.TYPE_SPEC_SECTION:
                # раздел спецификации храним в БД (дерево + parent_uid), затем уходим вглубь
                sec = self.client.get_properties(child.uid)
                if sec is not None:
                    out.append(RequirementRecord(
                        uid=sec.uid, item_id=sec.item_id, item_revision_id=sec.item_revision_id,
                        name=sec.object_name, type=m.TYPE_SPEC_SECTION,
                        parent_uid=node_uid, section_path=child_path, sequence_no=seq,
                        attrs=sec.attrs))
                self._walk(child.uid, parent_uid=node_uid, path=child_path,
                           depth=depth + 1, max_depth=max_depth, out=out)
            elif child.type_name == m.TYPE_REQUIREMENT:
                out.append(self._collect_requirement(child.uid, parent_uid=node_uid,
                                                     section_path=child_path, seq=seq))
            else:
                # неизвестный тип — пропускаем, но не роняем синхронизацию
                continue

    def _collect_requirement(self, rev_uid: str, parent_uid: str | None,
                             section_path: str, seq: int) -> RequirementRecord:
        """Выгрузка RequirementRevision: атрибуты + контент + связи."""
        reqs = self.client.get_requirements([rev_uid])
        if not reqs:
            raise SyncError(f"RequirementRevision {rev_uid} не найден")
        req = reqs[0]

        # 1) текст: приоритет — HTML-датасет IMAN_specification, фолбэк — object_string
        text, source = self._load_content(req)
        if not text.strip():
            text, source = req.object_string, "object_string"

        # 2) связи трассируемости (TC_Requirement_Trace_Relation)
        trace_out = [r.secondary for r in self.client.find_relations(rev_uid, m.REL_TRACE, "out") if r.secondary]
        trace_in = [r.primary for r in self.client.find_relations(rev_uid, m.REL_TRACE, "in") if r.primary]

        return RequirementRecord(
            uid=rev_uid, item_id=req.item_id, item_revision_id=req.item_revision_id,
            name=req.object_name, text=text, parent_uid=parent_uid,
            section_path=section_path, sequence_no=seq, attrs=req.attrs,
            trace_in=trace_in, trace_out=trace_out, content_source=source,
        )

    def _load_content(self, req: TcItem) -> tuple[str, str]:
        """Извлечение текста из датасета по связи IMAN_specification (HTML/Text)."""
        for ds in self.client.find_datasets(req.uid, m.REL_SPEC_CONTENT):
            ds_type = ds.type_name or ds.attrs.get("dataset_type", "")
            if ds_type not in (m.DATASET_TYPE_HTML, m.DATASET_TYPE_TEXT):
                continue
            for f in self.client.get_contents(ds.uid):
                fname = f.object_name or f.attrs.get("file_name", "")
                if not fname.lower().endswith((".html", ".htm", ".txt")):
                    continue
                ticket = self.client.get_file_read_ticket(f.uid)
                raw = self.client.download_file(ticket)
                text = extract_text_from_html(raw) if fname.lower().endswith((".html", ".htm")) \
                    else raw.decode("utf-8", errors="replace").strip()
                if text.strip():
                    return text, f"IMAN_specification:{fname}"
        return "", "none"

    # ---------- запись в БД ----------
    def _upsert_all(self, records: list[RequirementRecord]) -> dict:
        stats = {"created": 0, "updated": 0, "unchanged": 0, "errors": 0}
        run_id = self.db.execute(select(SyncRun.id).order_by(SyncRun.id.desc())).scalar()
        for rec in records:
            row = self.db.get(Requirement, rec.uid)
            old_hash = row.text_hash if row is not None else None
            try:
                self._upsert_one(rec, run_id)
            except Exception:  # noqa: BLE001 — одна запись не должна ронять весь прогон
                stats["errors"] += 1
                continue
            if row is None:
                stats["created"] += 1
            else:
                stats["updated" if old_hash != content_hash(rec.text) else "unchanged"] += 1
        return stats

    def _upsert_one(self, rec: RequirementRecord, run_id: int | None) -> None:
        """Upsert текущего состояния + снапшот истории при изменении текста."""
        h = content_hash(rec.text)
        row = self.db.get(Requirement, rec.uid)
        if row is None:
            row = Requirement(
                uid=rec.uid, item_id=rec.item_id, item_revision_id=rec.item_revision_id,
                name=rec.name, type=rec.type, text=rec.text, text_hash=h,
                parent_uid=rec.parent_uid, section_path=rec.section_path,
                spec_uid=self._spec_uid(), raw_data=rec.attrs,
                traceability_links={"out": rec.trace_out, "in": rec.trace_in},
            )
            self.db.add(row)
            self.db.add(self._snapshot(rec, run_id))
        else:
            changed = row.text_hash != h
            if changed:
                self.db.add(self._snapshot(rec, run_id))  # история «как было» — в снапшот
            row.item_id, row.item_revision_id = rec.item_id, rec.item_revision_id
            row.name, row.text, row.text_hash = rec.name, rec.text, h
            row.parent_uid, row.section_path = rec.parent_uid, rec.section_path
            row.spec_uid = self._spec_uid()
            row.raw_data = rec.attrs
            row.traceability_links = {"out": rec.trace_out, "in": rec.trace_in}
            row.last_synced_at = datetime.now(timezone.utc)
        self.db.flush()

    def _snapshot(self, rec: RequirementRecord, run_id: int | None) -> RequirementSnapshot:
        return RequirementSnapshot(requirement_uid=rec.uid, text=rec.text,
                                   raw_data=rec.attrs, sync_run_id=run_id)

    def _spec_uid(self) -> str:
        if self._spec_uid_cache is None:
            self._spec_uid_cache = self.db.execute(select(SyncRun.spec_uid)
                                                   .order_by(SyncRun.id.desc())).scalar() or ""
        return self._spec_uid_cache

    def _refresh_statuses(self) -> None:
        """Сброс устаревших статусов после синхронизации (анализ пересчитает заново)."""
        # статусы пересчитываются пайплайном анализа; здесь только чистим «правки»,
        # если текст в TC вернулся к исходному
        for row in self.db.scalars(select(Requirement)):
            if row.status in ("needs_edit", "conflict", "weak") and not row.status_reasons:
                row.status = "ok"

    # ---------- запись обратно в Teamcenter (write-back) ----------
    def push_edit(self, requirement_uid: str, new_text: str, user_login: str) -> dict:
        """Отправляет готовую правку в TC (setProperties object_string).

        Работает только при разрешении записи: глобальный выключатель И право
        пользователя (иначе WriteToTcForbidden).
        """
        if not self.settings.can_write_to_tc(user_login):
            raise WriteToTcForbidden(user_login)
        updated = self.client.set_properties(requirement_uid, {m.ATTR_OBJECT_STRING: new_text})
        # локальная копия и история
        row = self.db.get(Requirement, requirement_uid)
        if row is not None:
            h = content_hash(new_text)
            if row.text_hash != h:
                self.db.add(RequirementSnapshot(requirement_uid=row.uid, text=row.text,
                                                raw_data=row.raw_data))
            row.text, row.text_hash = new_text, h
            row.last_synced_at = datetime.now(timezone.utc)
        return updated

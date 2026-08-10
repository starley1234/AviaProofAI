"""Тесты ETL-модуля TeamcenterSync: обход, контент, иерархия, идемпотентность, снапшоты."""
from __future__ import annotations

from sqlalchemy import func, select

from app.models import Requirement, RequirementSnapshot, SyncRun
from app.services.teamcenter.client import TeamcenterSoapClient
from app.services.teamcenter.sync import TeamcenterSync


def _run_sync(db, spec_id="SPEC-BRAKE-001"):
    from app.config import get_settings
    s = get_settings()
    c = TeamcenterSoapClient(s.tc_url, timeout=10)
    try:
        return TeamcenterSync(c, db).run(spec_id)
    finally:
        c.close()


def test_full_sync_counts_and_hierarchy(db):
    run = _run_sync(db)
    assert run.status == "ok", run.error
    assert run.stats == {"created": 16, "updated": 0, "unchanged": 0, "errors": 0}

    rows = db.scalars(select(Requirement)).all()
    assert len(rows) == 16
    types = {r.type for r in rows}
    assert types == {"Specification", "SpecSection", "RequirementRevision"}

    # иерархия: требование -> раздел -> спецификация
    req = db.scalar(select(Requirement).where(Requirement.item_id == "REQ-1201"))
    assert req.parent_uid == "rev-SS-1200-A"
    sec = db.get(Requirement, req.parent_uid)
    assert sec.parent_uid == "rev-SPEC-BRAKE-001-A"
    assert req.section_path == "2.1"
    spec = db.get(Requirement, sec.parent_uid)
    assert spec.parent_uid is None and spec.item_id == "SPEC-BRAKE-001"


def test_content_extracted_from_html_dataset(db):
    """Текст берётся из HTML-датасета IMAN_specification (не из object_string как такового)."""
    _run_sync(db)
    req = db.scalar(select(Requirement).where(Requirement.item_id == "REQ-1201"))
    assert "не более 100 Н" in req.text
    assert "Назначение системы" not in req.text  # title/h1 датасета не попадают в текст
    assert "<" not in req.text and ">" not in req.text  # HTML вычищен
    # источник контента зафиксирован в raw_data
    assert req.raw_data["object_string"]  # object_string тоже сохранён


def test_traceability_links_stored(db):
    _run_sync(db)
    req = db.scalar(select(Requirement).where(Requirement.item_id == "REQ-1101"))
    assert req.traceability_links["out"] == ["rev-REQ-1201-A", "rev-REQ-1302-A", "rev-REQ-1402-A"]
    leaf = db.scalar(select(Requirement).where(Requirement.item_id == "REQ-1401"))
    assert leaf.traceability_links == {"out": [], "in": []}


def test_raw_data_jsonb_all_attributes(db):
    _run_sync(db)
    req = db.scalar(select(Requirement).where(Requirement.item_id == "REQ-1102"))
    assert req.raw_data["item_id"] == "REQ-1102"
    assert req.raw_data["type_name"] == "RequirementRevision"
    assert req.raw_data["owning_user"] == "petrov"
    assert "uid" in req.raw_data


def test_sync_is_idempotent(db):
    _run_sync(db)
    run2 = _run_sync(db)
    assert run2.stats["created"] == 0 and run2.stats["updated"] == 0
    assert run2.stats["unchanged"] == 16
    # снапшотов при неизменных данных нет
    assert db.scalar(select(func.count(RequirementSnapshot.id))) == 16  # только при создании


def test_change_detection_creates_snapshot(db):
    _run_sync(db)
    # меняем КОНТЕНТ (HTML-датасет IMAN_specification) в «Teamcenter» и синхронизируемся
    from stub_tc.server import store
    new_text = "НОВЫЙ текст требования REQ-1203."
    store.files["file-REQ-1203-content"]["content"] = (
        f"<html><body><p>{new_text}</p></body></html>").encode("utf-8")
    store.revisions["rev-REQ-1203-A"]["object_string"] = new_text
    run = _run_sync(db)
    assert run.stats["updated"] == 1
    req = db.scalar(select(Requirement).where(Requirement.item_id == "REQ-1203"))
    assert "НОВЫЙ" in req.text
    # история: 1 снапшот при создании + 1 при изменении
    snapshots = db.scalars(select(RequirementSnapshot)
                           .where(RequirementSnapshot.requirement_uid == req.uid)).all()
    assert len(snapshots) == 2
    assert "НОВЫЙ" not in snapshots[0].text  # в снапшоте — состояние ДО изменения


def test_push_edit_updates_dataset_content(db):
    """Write-back object_string синхронизирует HTML-датасет (контент спецификации)."""
    from app.services.settings_service import SettingsService
    from app.models import User
    _run_sync(db)
    db.add(User(tc_login="petrov", koseven_login="petrov", write_to_tc_allowed=True))
    SettingsService(db).set_write_allowed_service_wide(True, actor="test")
    db.commit()

    from app.config import get_settings
    s = get_settings()
    c = TeamcenterSoapClient(s.tc_url, timeout=10)
    try:
        sync = TeamcenterSync(c, db)
        sync.push_edit("rev-REQ-1203-A", "Тормозной путь не должен превышать 1200 м.", "petrov")
    finally:
        c.close()
    # повторная синхронизация подхватывает новый текст из датасета
    run = _run_sync(db)
    assert run.stats["updated"] == 1
    req = db.scalar(select(Requirement).where(Requirement.item_id == "REQ-1203"))
    assert "1200 м" in req.text


def test_sync_failure_recorded(db):
    run = _run_sync(db, spec_id="NOT-EXISTS")
    assert run.status == "failed"
    assert "не найдена" in run.error
    assert db.scalar(select(func.count(SyncRun.id))) >= 1


def test_sync_reports_auth_error(db):
    """Неверный пароль -> прогон помечается failed с внятной ошибкой."""
    from stub_tc.server import store
    old = store.users["infodba"]["password"]
    store.users["infodba"]["password"] = "changed-password"
    try:
        run = _run_sync(db)
    finally:
        store.users["infodba"]["password"] = old
    assert run.status == "failed"
    assert "Авторизация" in run.error

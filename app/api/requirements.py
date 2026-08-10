"""API: требования (дашборд конструктора), трассируемость, impact."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_api_key
from app.api.services import impact_service
from app.models import Analysis, Requirement, RequirementDraft

router = APIRouter(prefix="/requirements", tags=["requirements"],
                   dependencies=[Depends(require_api_key)])

STATUS_LABELS = {
    "ok": "Ок", "weak": "Слабое место", "conflict": "Конфликт связей",
    "needs_edit": "Требует правки", "cert_risk": "Риск сертификации",
}


def _req_summary(r: Requirement) -> dict:
    return {
        "uid": r.uid, "item_id": r.item_id, "item_revision_id": r.item_revision_id,
        "name": r.name, "type": r.type, "section_path": r.section_path,
        "status": r.status, "status_label": STATUS_LABELS.get(r.status, r.status),
        "status_reasons": r.status_reasons or [],
        "text_preview": r.text[:200], "parent_uid": r.parent_uid,
        "last_synced_at": r.last_synced_at,
    }


@router.get("")
def list_requirements(status: str | None = None, search: str | None = None,
                      section: str | None = None, limit: int = 500,
                      db: Session = Depends(get_db)):
    q = select(Requirement)
    if status:
        q = q.where(Requirement.status == status)
    if section:
        q = q.where(Requirement.section_path.like(f"{section}%"))
    if search:
        like = f"%{search}%"
        q = q.where(or_(Requirement.item_id.ilike(like), Requirement.name.ilike(like),
                        Requirement.text.ilike(like)))
    q = q.order_by(Requirement.section_path, Requirement.item_id).limit(limit)
    return [_req_summary(r) for r in db.scalars(q)]


@router.get("/{uid}")
def get_requirement(uid: str, db: Session = Depends(get_db)):
    r = db.get(Requirement, uid)
    if r is None:
        raise HTTPException(404, f"Требование {uid} не найдено (выполните синхронизацию)")
    drafts = db.scalars(select(RequirementDraft)
                        .where(RequirementDraft.requirement_uid == uid)
                        .order_by(RequirementDraft.version.desc())).all()
    analyses = db.scalars(select(Analysis)
                          .where(Analysis.requirement_uid == uid)
                          .order_by(Analysis.id.desc()).limit(50)).all()
    parent = db.get(Requirement, r.parent_uid) if r.parent_uid else None
    return {
        **_req_summary(r),
        "text": r.text, "raw_data": r.raw_data,
        "traceability_links": r.traceability_links,
        "parent": _req_summary(parent) if parent else None,
        "drafts": [{
            "id": d.id, "version": d.version, "source": d.source, "status": d.status,
            "original_text": d.original_text, "proposed_text": d.proposed_text,
            "current_text": d.current_text, "rationale": d.rationale,
            "issues": d.issues, "created_by": d.created_by, "created_at": d.created_at,
            "pushed_at": d.pushed_at,
        } for d in drafts],
        "analyses": [{
            "id": a.id, "kind": a.kind, "severity": a.severity, "status": a.status,
            "title": a.title, "details": a.details,
            "other_requirement_uid": a.other_requirement_uid, "created_at": a.created_at,
        } for a in analyses],
    }


@router.get("/{uid}/traceability")
def traceability(uid: str, db: Session = Depends(get_db)):
    """Связи требования: вверх (откуда пришли), вниз (куда ведут)."""
    r = db.get(Requirement, uid)
    if r is None:
        raise HTTPException(404, f"Требование {uid} не найдено")
    links = r.traceability_links or {}
    def _brief(u: str) -> dict | None:
        row = db.get(Requirement, u)
        return _req_summary(row) if row else {"uid": u, "name": "(не в БД)", "item_id": u}
    return {
        "uid": uid,
        "up": [_brief(u) for u in links.get("in", [])],
        "down": [_brief(u) for u in links.get("out", [])],
        "children": [_req_summary(c) for c in db.scalars(
            select(Requirement).where(Requirement.parent_uid == uid))],
    }


class ImpactRequest(BaseModel):
    new_text: str


@router.post("/{uid}/impact")
def impact(uid: str, req: ImpactRequest, db: Session = Depends(get_db)):
    """Impact-анализ: на какие связанные требования повлияет изменение."""
    r = db.get(Requirement, uid)
    if r is None:
        raise HTTPException(404, f"Требование {uid} не найдено")
    return impact_service(db).analyze(uid, req.new_text)

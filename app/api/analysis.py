"""API: анализ (аудит качества, RAG-скан, traceability-аудит), результаты."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_api_key
from app.api.services import analysis_service
from app.models import Analysis
from app.services.traceability import TraceabilityService

router = APIRouter(prefix="/analysis", tags=["analysis"],
                   dependencies=[Depends(require_api_key)])


@router.post("/audit")
def run_audit(db: Session = Depends(get_db)):
    """Аудит качества всех требований (атомарность/непротиворечивость/проверяемость)."""
    return analysis_service(db).run_quality_audit()


@router.post("/conflicts")
def run_conflicts(db: Session = Depends(get_db)):
    """RAG-скан: поиск противоречий между требованиями из разных разделов."""
    return analysis_service(db).run_conflict_scan()


@router.post("/traceability")
def run_traceability(db: Session = Depends(get_db)):
    """Traceability-аудит: требования без связей -> «Риск сертификации»."""
    return TraceabilityService(db).run()


@router.get("/results")
def list_results(kind: str | None = None, status: str | None = None,
                 requirement_uid: str | None = None, limit: int = 200,
                 db: Session = Depends(get_db)):
    q = select(Analysis).order_by(Analysis.id.desc()).limit(limit)
    if kind:
        q = q.where(Analysis.kind == kind)
    if status:
        q = q.where(Analysis.status == status)
    if requirement_uid:
        q = q.where(Analysis.requirement_uid == requirement_uid)
    return [{
        "id": a.id, "kind": a.kind, "severity": a.severity, "status": a.status,
        "title": a.title, "details": a.details,
        "requirement_uid": a.requirement_uid, "other_requirement_uid": a.other_requirement_uid,
        "created_at": a.created_at, "resolved_at": a.resolved_at,
    } for a in db.scalars(q)]


class ResolveRequest(BaseModel):
    resolution: str = "resolved"  # resolved | dismissed | acknowledged


@router.post("/results/{analysis_id}/resolve")
def resolve_result(analysis_id: int, req: ResolveRequest, db: Session = Depends(get_db)):
    a = analysis_service(db).resolve_analysis(analysis_id, req.resolution)
    if a is None:
        raise HTTPException(404, f"Замечание #{analysis_id} не найдено")
    return {"id": a.id, "status": a.status}

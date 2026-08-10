"""Traceability Audit (Guardrail №1).

Правило (DO-178C, трассируемость): каждое требование должно быть связано
связью TC_Requirement_Trace_Relation (с вышестоящим или нижестоящим).
Требование БЕЗ связей получает флаг «Риск сертификации» (cert_risk).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Analysis, Requirement
from app.services.analysis_service import AnalysisService


class TraceabilityService:
    def __init__(self, db: Session):
        self.db = db

    def run(self) -> dict:
        """Проверяет все требования, пишет Analysis(kind='traceability')."""
        reqs = list(self.db.scalars(
            select(Requirement).where(Requirement.type == "RequirementRevision")))
        flagged: list[str] = []
        for req in reqs:
            links = req.traceability_links or {}
            has_links = bool(links.get("out") or links.get("in"))
            # снимаем старые открытые флаги, чтобы не дублировать
            for old in self.db.scalars(select(Analysis).where(
                    Analysis.kind == "traceability", Analysis.requirement_uid == req.uid,
                    Analysis.status == "open")):
                old.status = "resolved"
                old.resolved_at = None
            if not has_links:
                flagged.append(req.uid)
                self.db.add(Analysis(
                    kind="traceability", requirement_uid=req.uid, severity="critical",
                    title="Риск сертификации: требование не имеет связей TC_Requirement_Trace_Relation",
                    details={"out": links.get("out", []), "in": links.get("in", []),
                             "rule": "DO-178C / трассируемость требований"},
                ))
        AnalysisService(self.db).refresh_statuses()
        return {"checked": len(reqs), "flagged": len(flagged), "flagged_uids": flagged}

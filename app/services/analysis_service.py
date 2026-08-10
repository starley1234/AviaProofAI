"""Пайплайн анализа: аудит качества, RAG-скан противоречий, трассируемость.

Обновляет таблицу analyses (kind: quality_audit | conflict | traceability)
и кэширует статусы требований для дашборда конструктора:
  ok | weak | conflict | needs_edit | cert_risk
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (Analysis, REQ_STATUS_CERT_RISK, REQ_STATUS_CONFLICT,
                        REQ_STATUS_NEEDS_EDIT, REQ_STATUS_OK, REQ_STATUS_WEAK,
                        Requirement, RequirementDraft)
from app.services.llm.agent import AuditAgent, ContradictionAgent
from app.services.llm.rag import ConflictScanner


class AnalysisService:
    def __init__(self, db: Session, llm=None):
        self.db = db
        self.audit_agent = AuditAgent(llm)
        self.scanner = ConflictScanner(ContradictionAgent(llm))

    # ─────────────── аудит качества (DO-178C) ───────────────
    def run_quality_audit(self) -> dict:
        reqs = self.db.scalars(select(Requirement).where(Requirement.type == "RequirementRevision"))
        stats = {"checked": 0, "issues": 0}
        for req in reqs:
            res = self.audit_agent.audit_requirement(req.text)
            stats["checked"] += 1
            issues = res["issues"]
            if not issues:
                continue
            stats["issues"] += len(issues)
            for iss in issues:
                self.db.add(Analysis(
                    kind="quality_audit", requirement_uid=req.uid,
                    severity=iss["severity"], title=iss["description"],
                    details={"type": iss["type"], "suggestion": iss["suggestion"],
                             "score": res["score"], "text": req.text[:500]},
                ))
        self.refresh_statuses()
        return stats

    # ─────────────── RAG-скан несостыковок ───────────────
    def run_conflict_scan(self) -> dict:
        reqs = self.db.scalars(select(Requirement).where(Requirement.type == "RequirementRevision"))
        candidates = [{"uid": r.uid, "text": r.text, "section_path": r.section_path} for r in reqs]
        conflicts = self.scanner.scan(candidates)
        for c in conflicts:
            self.db.add(Analysis(
                kind="conflict", requirement_uid=c["uid_a"], other_requirement_uid=c["uid_b"],
                severity="critical",
                title=f"Противоречие с {c['uid_b']} (раздел {c['section_b']})",
                details={"type": c["type"], "explanation": c["explanation"],
                         "suggestion": c["suggestion"],
                         "section_a": c["section_a"], "section_b": c["section_b"]},
            ))
        self.refresh_statuses()
        return {"pairs_checked": len(candidates) * min(self.scanner.top_k, max(len(candidates) - 1, 0)),
                "conflicts_found": len(conflicts)}

    # ─────────────── пересчёт статусов дашборда ───────────────
    def refresh_statuses(self) -> None:
        for req in self.db.scalars(select(Requirement)):
            reasons: list[str] = []
            status = REQ_STATUS_OK

            has_conflict = self.db.scalar(
                select(Analysis.id).where(Analysis.kind == "conflict",
                                          Analysis.requirement_uid == req.uid,
                                          Analysis.status == "open"))
            if has_conflict:
                status, reasons = REQ_STATUS_CONFLICT, ["Обнаружено противоречие с другим требованием"]

            has_cert = self.db.scalar(
                select(Analysis.id).where(Analysis.kind == "traceability",
                                          Analysis.requirement_uid == req.uid,
                                          Analysis.status == "open"))
            if has_cert:
                status, reasons = REQ_STATUS_CERT_RISK, ["Нет связей трассируемости TC_Requirement_Trace_Relation"]

            has_draft = self.db.scalar(
                select(RequirementDraft.id).where(RequirementDraft.requirement_uid == req.uid,
                                                  RequirementDraft.status.in_(
                                                      ("draft", "proposed", "reviewed", "ready"))))
            if has_draft and status == REQ_STATUS_OK:
                status, reasons = REQ_STATUS_NEEDS_EDIT, ["Есть открытая правка (двойник)"]

            has_weak = self.db.scalar(
                select(Analysis.id).where(Analysis.kind == "quality_audit",
                                          Analysis.requirement_uid == req.uid,
                                          Analysis.status == "open"))
            if has_weak and status == REQ_STATUS_OK:
                status, reasons = REQ_STATUS_WEAK, ["Аудит качества нашёл слабые места"]

            req.status, req.status_reasons = status, reasons

    # ─────────────── разрешение замечаний ───────────────
    def resolve_analysis(self, analysis_id: int, resolution: str = "resolved") -> Analysis | None:
        a = self.db.get(Analysis, analysis_id)
        if a is None:
            return None
        a.status = resolution
        a.resolved_at = datetime.now(timezone.utc)
        self.refresh_statuses()
        return a

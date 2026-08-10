"""Impact Analysis (Guardrail №2).

Если требование меняется в «двойнике», ИИ подсвечивает связанные
дочерние требования, на которые изменение повлияет.

Связанные = прямые дети по иерархии (parent_uid) + цели связей
TC_Requirement_Trace_Relation (out).
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Analysis, Requirement
from app.services.llm.agent import ImpactAgent


class ImpactService:
    def __init__(self, db: Session, llm=None):
        self.db = db
        self.agent = ImpactAgent(llm)

    def related_requirements(self, uid: str) -> list[Requirement]:
        """Дочерние/связанные требования (по иерархии и трассируемости)."""
        req = self.db.get(Requirement, uid)
        if req is None:
            return []
        uids: set[str] = set()
        # прямые дети по дереву
        for child in self.db.scalars(select(Requirement).where(Requirement.parent_uid == uid)):
            uids.add(child.uid)
        # цели связей трассируемости
        for target in (req.traceability_links or {}).get("out", []):
            uids.add(target)
        uids.discard(uid)
        return [r for r in (self.db.get(Requirement, u) for u in uids) if r is not None]

    def analyze(self, uid: str, new_text: str) -> dict:
        """Считает impact нового текста на связанные требования, пишет Analysis."""
        related = self.related_requirements(uid)
        result = self.agent.analyze(new_text,
                                    [{"uid": r.uid, "text": r.text} for r in related])
        affected = result.get("affected", [])
        for item in affected:
            ruid = item.get("uid", "")
            # защита от мусора в ответе LLM: uid обязан быть коротким идентификатором
            if not ruid or len(ruid) > 64 or not ruid.replace("-", "").replace("_", "").isalnum():
                continue
            self.db.add(Analysis(
                kind="impact", requirement_uid=uid, other_requirement_uid=ruid,
                severity="warning",
                title=f"Изменение {uid} влияет на {ruid}",
                details={"impact": item.get("impact", ""), "new_text": new_text[:500]},
            ))
        return {"requirement_uid": uid, "related": len(related),
                "affected": [a for a in affected if "не затрон" not in a.get("impact", "")]}

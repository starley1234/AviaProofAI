"""TwinService — «цифровой двойник»: жизненный цикл правки требования.

Поток (см. ТЗ): ИИ находит проблему -> конструктор подтверждает/правит
в двойнике -> правка «готова» -> миграция обратно в TC (если запись разрешена).

Статусы draft: draft -> proposed -> reviewed -> ready -> pushed | rejected.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (DRAFT_DRAFT, DRAFT_PROPOSED, DRAFT_PUSHED, DRAFT_READY,
                        DRAFT_REJECTED, DRAFT_REVIEWED, Requirement, RequirementDraft)
from app.services.llm.agent import AuditAgent, EditAgent


class TwinError(Exception):
    pass


class TwinService:
    def __init__(self, db: Session, llm=None, push_callback=None):
        """push_callback: callable(requirement_uid, new_text, user_login) -> dict
        — по умолчанию TeamcenterSync.push_edit (см. api/twin.py)."""
        self.db = db
        self.audit_agent = AuditAgent(llm)
        self.edit_agent = EditAgent(llm)
        self.push_callback = push_callback

    # ─────────────── создание правок ───────────────
    def _next_version(self, requirement_uid: str) -> int:
        cur = self.db.scalar(select(func.max(RequirementDraft.version))
                             .where(RequirementDraft.requirement_uid == requirement_uid))
        return (cur or 0) + 1

    def create_ai_draft(self, requirement_uid: str, actor: str) -> RequirementDraft:
        """ИИ анализирует требование и предлагает формулировку."""
        req = self.db.get(Requirement, requirement_uid)
        if req is None:
            raise TwinError(f"Требование {requirement_uid} не найдено")
        audit = self.audit_agent.audit_requirement(req.text)
        proposal = self.edit_agent.propose(req.text, audit["issues"])
        draft = RequirementDraft(
            requirement_uid=requirement_uid, version=self._next_version(requirement_uid),
            source="ai", original_text=req.text, proposed_text=proposal["proposed_text"],
            current_text=proposal["proposed_text"], rationale=proposal["rationale"],
            issues=audit["issues"], status=DRAFT_PROPOSED, created_by=actor,
        )
        self.db.add(draft)
        self.db.flush()
        self._refresh_statuses()
        return draft

    def create_user_draft(self, requirement_uid: str, text: str, rationale: str,
                          actor: str) -> RequirementDraft:
        """Конструктор создаёт правку вручную (или берёт за основу предложение ИИ)."""
        req = self.db.get(Requirement, requirement_uid)
        if req is None:
            raise TwinError(f"Требование {requirement_uid} не найдено")
        draft = RequirementDraft(
            requirement_uid=requirement_uid, version=self._next_version(requirement_uid),
            source="user", original_text=req.text, proposed_text="",
            current_text=text, rationale=rationale, issues=[], status=DRAFT_DRAFT,
            created_by=actor,
        )
        self.db.add(draft)
        self.db.flush()
        self._refresh_statuses()
        return draft

    # ─────────────── редактирование ───────────────
    def update_draft(self, draft_id: int, text: str | None = None,
                     rationale: str | None = None) -> RequirementDraft:
        draft = self.db.get(RequirementDraft, draft_id)
        if draft is None:
            raise TwinError(f"Правка #{draft_id} не найдена")
        if draft.status == DRAFT_PUSHED:
            raise TwinError("Правка уже отправлена в Teamcenter")
        if text is not None:
            draft.current_text = text
        if rationale is not None:
            draft.rationale = rationale
        if draft.status == DRAFT_PROPOSED:
            draft.status = DRAFT_REVIEWED  # конструктор посмотрел предложение ИИ
        return draft

    def submit_draft(self, draft_id: int) -> RequirementDraft:
        """«Состояние готовности»: правка готова к миграции в TC."""
        draft = self.db.get(RequirementDraft, draft_id)
        if draft is None:
            raise TwinError(f"Правка #{draft_id} не найдена")
        if not draft.current_text.strip():
            raise TwinError("Нельзя подтвердить пустую правку")
        draft.status = DRAFT_READY
        return draft

    def reject_draft(self, draft_id: int) -> RequirementDraft:
        draft = self.db.get(RequirementDraft, draft_id)
        if draft is None:
            raise TwinError(f"Правка #{draft_id} не найдена")
        draft.status = DRAFT_REJECTED
        return draft

    # ─────────────── миграция в Teamcenter ───────────────
    def push_draft(self, draft_id: int, actor: str) -> RequirementDraft:
        """Отправляет готовую правку в TC (проверка прав записи внутри push_callback)."""
        draft = self.db.get(RequirementDraft, draft_id)
        if draft is None:
            raise TwinError(f"Правка #{draft_id} не найдена")
        if draft.status != DRAFT_READY:
            raise TwinError(f"Правка #{draft_id} не в состоянии «ready» (сейчас {draft.status})")
        if self.push_callback is None:
            raise TwinError("Миграция в Teamcenter не настроена")
        updated = self.push_callback(draft.requirement_uid, draft.current_text, actor)
        draft.status = DRAFT_PUSHED
        draft.pushed_at = datetime.now(timezone.utc)
        self._refresh_statuses()
        return draft

    def _refresh_statuses(self) -> None:
        """Обновить статусы дашборда (needs_edit и т.п.) после изменения правок."""
        from app.services.analysis_service import AnalysisService
        AnalysisService(self.db).refresh_statuses()

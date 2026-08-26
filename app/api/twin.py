"""API: цифровой двойник — правки требований (draft) и миграция в TC."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import get_actor, get_db, require_api_key
from app.api.services import get_llm
from app.config import get_settings
from app.models import RequirementDraft
from app.services.teamcenter.sync import TeamcenterSync
from app.services.twin import TwinError, TwinService

router = APIRouter(tags=["twin"], dependencies=[Depends(require_api_key)])


def _twin(db: Session) -> TwinService:
    """TwinService с колбэком записи в Teamcenter (проверка прав внутри push_edit)."""
    from app.services.settings_service import SettingsService, WriteToTcForbidden
    from app.services.teamcenter.factory import build_tc_client
    s = get_settings()

    def push(uid: str, text: str, user: str) -> dict:
        # проверяем права ДО подключения, чтобы не логиниться зря
        if not SettingsService(db).can_write_to_tc(user):
            raise WriteToTcForbidden(user)
        client = build_tc_client(s)
        try:
            client.login(s.tc_user, s.tc_password)
            return TeamcenterSync(client, db).push_edit(uid, text, user)
        finally:
            client.close()

    return TwinService(db, get_llm(), push_callback=push)


class DraftCreateRequest(BaseModel):
    source: str = "ai"           # ai | user
    text: str | None = None      # для source=user
    rationale: str = ""


class DraftUpdateRequest(BaseModel):
    text: str | None = None
    rationale: str | None = None


@router.post("/requirements/{uid}/drafts")
def create_draft(uid: str, req: DraftCreateRequest, actor: str = Depends(get_actor),
                 db: Session = Depends(get_db)):
    """Создать правку: source=ai — ИИ предложит формулировку; source=user — вручную."""
    try:
        twin = _twin(db)
        if req.source == "ai":
            draft = twin.create_ai_draft(uid, actor)
        elif req.source == "user":
            if not req.text:
                raise HTTPException(422, "Для source=user обязателен text")
            draft = twin.create_user_draft(uid, req.text, req.rationale, actor)
        else:
            raise HTTPException(422, f"Неизвестный source={req.source}")
    except TwinError as e:
        raise HTTPException(404, str(e))
    return _draft_json(draft)


@router.get("/drafts/{draft_id}")
def get_draft(draft_id: int, db: Session = Depends(get_db)):
    d = db.get(RequirementDraft, draft_id)
    if d is None:
        raise HTTPException(404, f"Правка #{draft_id} не найдена")
    return _draft_json(d)


@router.patch("/drafts/{draft_id}")
def update_draft(draft_id: int, req: DraftUpdateRequest,
                 actor: str = Depends(get_actor), db: Session = Depends(get_db)):
    try:
        return _draft_json(_twin(db).update_draft(draft_id, req.text, req.rationale))
    except TwinError as e:
        raise HTTPException(409, str(e))


@router.post("/drafts/{draft_id}/submit")
def submit_draft(draft_id: int, actor: str = Depends(get_actor), db: Session = Depends(get_db)):
    """«Состояние готовности»: правка готова к миграции обратно в СУТ TC."""
    try:
        return _draft_json(_twin(db).submit_draft(draft_id))
    except TwinError as e:
        raise HTTPException(409, str(e))


@router.post("/drafts/{draft_id}/reject")
def reject_draft(draft_id: int, actor: str = Depends(get_actor), db: Session = Depends(get_db)):
    try:
        return _draft_json(_twin(db).reject_draft(draft_id))
    except TwinError as e:
        raise HTTPException(409, str(e))


@router.post("/drafts/{draft_id}/push")
def push_draft(draft_id: int, actor: str = Depends(get_actor), db: Session = Depends(get_db)):
    """Миграция правки в Teamcenter (setProperties object_string).

    Запрещена, если выключен глобальный выключатель записи или у пользователя
    нет права users.write_to_tc_allowed (см. /settings/write-access).
    """
    from app.services.settings_service import WriteToTcForbidden
    try:
        draft = _twin(db).push_draft(draft_id, actor)
    except TwinError as e:
        raise HTTPException(409, str(e))
    except WriteToTcForbidden as e:
        raise HTTPException(403, str(e))
    return _draft_json(draft)


def _draft_json(d: RequirementDraft) -> dict:
    return {
        "id": d.id, "requirement_uid": d.requirement_uid, "version": d.version,
        "source": d.source, "status": d.status, "original_text": d.original_text,
        "proposed_text": d.proposed_text, "current_text": d.current_text,
        "rationale": d.rationale, "issues": d.issues, "created_by": d.created_by,
        "created_at": d.created_at, "pushed_at": d.pushed_at,
    }

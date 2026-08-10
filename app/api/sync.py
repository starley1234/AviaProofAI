"""API: синхронизация с Teamcenter."""
from __future__ import annotations

import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_api_key
from app.config import get_settings
from app.db import session_scope
from app.models import SyncRun
from app.services.settings_service import SettingsService
from app.services.teamcenter.client import TeamcenterSoapClient
from app.services.teamcenter.sync import TeamcenterSync

router = APIRouter(prefix="/sync", tags=["sync"], dependencies=[Depends(require_api_key)])


class SyncRequest(BaseModel):
    spec_id: str | None = None   # по умолчанию TC_SPEC_ID из настроек
    wait: bool = False           # true — выполнить синхронно и вернуть результат


def _run_sync(run_id: int, spec_id: str) -> None:
    """Фоновая синхронизация (своя сессия БД и свой SOAP-клиент)."""
    from app.config import get_settings
    s = get_settings()
    with session_scope() as db:
        client = TeamcenterSoapClient(s.tc_url, timeout=s.tc_timeout)
        try:
            TeamcenterSync(client, db).run(spec_id)
        finally:
            client.close()


@router.post("/run")
def run_sync(req: SyncRequest, db: Session = Depends(get_db)):
    spec_id = req.spec_id or get_settings().tc_spec_id
    run = SyncRun(status="running", spec_uid=spec_id)
    db.add(run)
    db.flush()
    if req.wait:
        # синхронно: та же сессия
        client = TeamcenterSoapClient(get_settings().tc_url, timeout=get_settings().tc_timeout)
        try:
            result = TeamcenterSync(client, db).run(spec_id)
        finally:
            client.close()
        return {"run_id": result.id, "status": result.status, "stats": result.stats,
                "error": result.error}
    threading.Thread(target=_run_sync, args=(run.id, spec_id), daemon=True).start()
    return {"run_id": run.id, "status": "running"}


@router.get("/runs")
def list_runs(limit: int = 20, db: Session = Depends(get_db)):
    rows = db.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(limit)).all()
    return [{
        "id": r.id, "status": r.status, "spec_uid": r.spec_uid,
        "started_at": r.started_at, "finished_at": r.finished_at,
        "stats": r.stats, "error": r.error,
    } for r in rows]


@router.get("/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db)):
    run = db.get(SyncRun, run_id)
    if run is None:
        raise HTTPException(404, f"Прогон #{run_id} не найден")
    return {"id": run.id, "status": run.status, "spec_uid": run.spec_uid,
            "started_at": run.started_at, "finished_at": run.finished_at,
            "stats": run.stats, "error": run.error}

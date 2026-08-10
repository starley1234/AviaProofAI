"""API: права записи в Teamcenter и маппинг пользователей TC <-> Koseven."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_db, require_admin, require_api_key
from app.api.services import settings_service
from app.models import User

router = APIRouter(tags=["settings"], dependencies=[Depends(require_api_key)])


# ─────────────── запись в Teamcenter ───────────────
class WriteAccessRequest(BaseModel):
    enabled: bool


@router.get("/settings/write-access")
def write_access(user: str | None = None, x_tc_user: str = Header(default=""),
                 db: Session = Depends(get_db)):
    """Состояние записи в TC: сервис в целом / пользователь / итог (И)."""
    login = user or x_tc_user or None
    return settings_service(db).write_access_state(user_login=login)


@router.put("/settings/write-access")
def set_write_access(req: WriteAccessRequest, actor: str = Depends(require_admin),
                     db: Session = Depends(get_db)):
    """Глобальный выключатель записи в Teamcenter (только admin)."""
    settings_service(db).set_write_allowed_service_wide(req.enabled, actor=actor)
    return settings_service(db).write_access_state()


# ─────────────── пользователи (маппинг TC <-> Koseven) ───────────────
class UserCreate(BaseModel):
    tc_login: str
    koseven_login: str
    full_name: str = ""
    role: str = "engineer"
    write_to_tc_allowed: bool = False


class UserUpdate(BaseModel):
    koseven_login: str | None = None
    full_name: str | None = None
    role: str | None = None
    write_to_tc_allowed: bool | None = None
    is_active: bool | None = None


def _user_json(u: User) -> dict:
    return {"id": u.id, "tc_login": u.tc_login, "koseven_login": u.koseven_login,
            "full_name": u.full_name, "role": u.role,
            "write_to_tc_allowed": u.write_to_tc_allowed, "is_active": u.is_active}


@router.get("/users")
def list_users(db: Session = Depends(get_db)):
    return [_user_json(u) for u in db.scalars(select(User).order_by(User.tc_login))]


@router.post("/users")
def create_user(req: UserCreate, db: Session = Depends(get_db)):
    if db.scalar(select(User).where(User.tc_login == req.tc_login)):
        raise HTTPException(409, f"Пользователь {req.tc_login} уже существует")
    u = User(tc_login=req.tc_login, koseven_login=req.koseven_login, full_name=req.full_name,
             role=req.role, write_to_tc_allowed=req.write_to_tc_allowed)
    db.add(u)
    db.flush()
    return _user_json(u)


@router.patch("/users/{user_id}")
def update_user(user_id: int, req: UserUpdate, db: Session = Depends(get_db)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, f"Пользователь #{user_id} не найден")
    for field, value in req.model_dump(exclude_none=True).items():
        setattr(u, field, value)
    return _user_json(u)


# ─────────────── дашборд ───────────────
@router.get("/dashboard/summary")
def dashboard_summary(db: Session = Depends(get_db)):
    """Сводка статусов для рабочего места конструктора."""
    from sqlalchemy import func
    from app.models import Requirement, SyncRun
    by_status = dict(db.execute(
        select(Requirement.status, func.count()).group_by(Requirement.status)).all())
    last_run = db.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(1)).first()
    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
        "write_access": settings_service(db).write_access_state(),
        "last_sync": {"id": last_run.id, "status": last_run.status,
                      "finished_at": last_run.finished_at, "stats": last_run.stats} if last_run else None,
    }

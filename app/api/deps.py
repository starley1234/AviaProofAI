"""Общие зависимости API: БД-сессия, авторизация по ключу, пользователь."""
from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session_factory
from app.models import User


def get_db() -> Session:
    factory = get_session_factory()
    s: Session = factory()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def require_api_key(x_api_key: str = Header(default="")) -> None:
    if x_api_key not in get_settings().api_key_list:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный X-Api-Key")


def get_actor(x_tc_user: str = Header(default=""), db: Session = Depends(get_db)) -> str:
    """TC-логин пользователя из заголовка X-TC-User (маппинг TC<->Koseven)."""
    if not x_tc_user:
        return "system"
    user = db.scalar(__import__("sqlalchemy").select(User).where(User.tc_login == x_tc_user,
                                                                 User.is_active))
    if user is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            f"Пользователь TC {x_tc_user!r} не найден в маппинге users")
    return x_tc_user


def require_admin(x_tc_user: str = Header(default=""), db: Session = Depends(get_db)) -> str:
    from sqlalchemy import select
    if not x_tc_user:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Требуется X-TC-User с ролью admin")
    user = db.scalar(select(User).where(User.tc_login == x_tc_user, User.is_active))
    if user is None or user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            f"Пользователь {x_tc_user!r} не имеет роли admin")
    return x_tc_user

"""Права записи в Teamcenter: глобальный выключатель + право пользователя.

Логика: запись разрешена ТОЛЬКО если
    app_settings['teamcenter.write_allowed'] = true   (сервис в целом)
    И users.write_to_tc_allowed = true                 (конкретный пользователь)

Безопасный дефолт: запись ЗАПРЕЩЕНА (переменная окружения TC_WRITE_ALLOWED=false).
"""
from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models import SETTING_TC_WRITE_ALLOWED, AppSetting, User


class WriteToTcForbidden(Exception):
    """Запись в Teamcenter запрещена настройками (сервис или пользователь)."""

    def __init__(self, user_login: str | None = None):
        self.user_login = user_login
        super().__init__(
            "Запись в Teamcenter запрещена: включите глобальный выключатель "
            "(app_settings.teamcenter.write_allowed) и/или право пользователя "
            f"(users.write_to_tc_allowed для {user_login or 'пользователя'})."
        )


class SettingsService:
    def __init__(self, db: Session):
        self.db = db

    # ---------- глобальный выключатель записи в TC ----------
    def is_write_allowed_service_wide(self) -> bool:
        row = self.db.get(AppSetting, SETTING_TC_WRITE_ALLOWED)
        return bool(row and row.value.get("enabled", False))

    def set_write_allowed_service_wide(self, enabled: bool, actor: str = "") -> bool:
        row = self.db.get(AppSetting, SETTING_TC_WRITE_ALLOWED)
        if row is None:
            row = AppSetting(key=SETTING_TC_WRITE_ALLOWED, value={"enabled": enabled, "actor": actor})
            self.db.add(row)
        else:
            row.value = {"enabled": enabled, "actor": actor}
        return enabled

    # ---------- право пользователя ----------
    def user_can_write(self, user_login: str | None) -> bool:
        if not user_login:
            return False
        row = self.db.scalar(select(User).where(User.tc_login == user_login, User.is_active))
        return bool(row and row.write_to_tc_allowed)

    def set_user_write(self, user_login: str, allowed: bool) -> bool:
        self.db.execute(
            update(User).where(User.tc_login == user_login).values(write_to_tc_allowed=allowed)
        )
        return allowed

    # ---------- итоговое решение (сервис И пользователь) ----------
    def can_write_to_tc(self, user_login: str | None = None) -> bool:
        return self.is_write_allowed_service_wide() and self.user_can_write(user_login)

    # ---------- состояние для дашборда ----------
    def write_access_state(self, user_login: str | None = None) -> dict:
        return {
            "service_wide_allowed": self.is_write_allowed_service_wide(),
            "per_user_allowed": self.user_can_write(user_login),
            "effective": self.can_write_to_tc(user_login),
        }

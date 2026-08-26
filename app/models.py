"""Схема БД AviaProofAI (PostgreSQL).

Два слоя данных, как и требует ТЗ:

1. «Слой Teamcenter» (исходная правда):
     requirements             — текущее состояние требования из TC (upsert при каждой синхронизации)
     requirement_snapshots    — неизменяемая история состояний из TC (версионность «как было»)

2. «Аналитический слой» (двойник, до фиксации в TC):
     requirement_drafts       — версии правок: предложение ИИ и/или рабочий вариант конструктора
                                 со статусом готовности (draft -> proposed -> reviewed -> ready -> pushed)

Служебные:
     analyses                 — результаты проверок: аудит качества, конфликты, impact, traceability
     sync_runs                — журнал синхронизаций
     users                    — маппинг пользователей TC <-> Koseven + право записи в TC
     app_settings             — настройки сервиса в целом (например, глобальный выключатель записи)

Статусы требования (поле requirements.status), вычисляются анализом:
     ok | weak | conflict | needs_edit | cert_risk
"""
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

# jsonb в PostgreSQL, обычный JSON в SQLite (для тестов)
JSONType = JSONB().with_variant(JSON, "sqlite")

# --- Статусы требования (дашборд конструктора) ---
REQ_STATUS_OK = "ok"
REQ_STATUS_WEAK = "weak"             # «Слабое место» — аудит качества нашёл проблемы
REQ_STATUS_CONFLICT = "conflict"     # «Конфликт связей» — противоречие с другим требованием
REQ_STATUS_NEEDS_EDIT = "needs_edit" # «Требует правки» — есть открытый draft/предложение
REQ_STATUS_CERT_RISK = "cert_risk"   # «Риск сертификации» — нет связей трассируемости

# --- Статусы draft (состояние готовности перед миграцией в TC) ---
DRAFT_DRAFT = "draft"        # черновик (ИИ предложил или конструктор начал)
DRAFT_PROPOSED = "proposed"  # предложение ИИ готово
DRAFT_REVIEWED = "reviewed"  # конструктор посмотрел
DRAFT_READY = "ready"        # «состояние готовности» — правка готова к миграции в TC
DRAFT_PUSHED = "pushed"      # отправлено в TC
DRAFT_REJECTED = "rejected"  # отклонено конструктором


class Requirement(Base):
    """Текущее состояние требования/раздела спецификации из Teamcenter."""
    __tablename__ = "requirements"

    uid: Mapped[str] = mapped_column(String(64), primary_key=True)  # TC uid объекта
    item_id: Mapped[str] = mapped_column(String(128), index=True)   # REQ-1201
    item_revision_id: Mapped[str] = mapped_column(String(32))       # A
    name: Mapped[str] = mapped_column(String(256))
    type: Mapped[str] = mapped_column(String(32))                   # RequirementRevision | SpecSection
    text: Mapped[str] = mapped_column(Text, default="")             # извлечённый текст (из IMAN_specification / object_string)
    text_hash: Mapped[str] = mapped_column(String(64), default="")  # для детекции изменений

    parent_uid: Mapped[str | None] = mapped_column(ForeignKey("requirements.uid"), index=True)
    section_path: Mapped[str] = mapped_column(String(128), default="")  # «2.3» — путь в спецификации
    spec_uid: Mapped[str] = mapped_column(String(64), index=True)   # корень спецификации

    raw_data: Mapped[dict] = mapped_column(JSONType, default=dict)          # ВСЕ атрибуты из TC
    traceability_links: Mapped[dict] = mapped_column(JSONType, default=dict)  # {"out": [uid...], "in": [uid...]} по TC_Requirement_Trace_Relation

    # Статус на дашборде — вычисляется анализом, кэшируется здесь
    status: Mapped[str] = mapped_column(String(32), default=REQ_STATUS_OK, index=True)
    status_reasons: Mapped[dict] = mapped_column(JSONType, default=list)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RequirementSnapshot(Base):
    """Неизменяемая история состояний из TC (пишется при каждом изменении)."""
    __tablename__ = "requirement_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    requirement_uid: Mapped[str] = mapped_column(ForeignKey("requirements.uid"), index=True)
    text: Mapped[str] = mapped_column(Text)
    raw_data: Mapped[dict] = mapped_column(JSONType, default=dict)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sync_run_id: Mapped[int | None] = mapped_column(ForeignKey("sync_runs.id"))


class RequirementDraft(Base):
    """Аналитический слой: версии правок (ИИ/конструктор) до фиксации в TC."""
    __tablename__ = "requirement_drafts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    requirement_uid: Mapped[str] = mapped_column(ForeignKey("requirements.uid"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)     # 1, 2, 3... — история правок
    source: Mapped[str] = mapped_column(String(16))              # ai | user
    original_text: Mapped[str] = mapped_column(Text, default="") # текст из TC на момент правки
    proposed_text: Mapped[str] = mapped_column(Text, default="") # предложение ИИ
    current_text: Mapped[str] = mapped_column(Text, default="")  # рабочий вариант конструктора
    rationale: Mapped[str] = mapped_column(Text, default="")     # зачем правка
    issues: Mapped[dict] = mapped_column(JSONType, default=list) # какие проблемы закрывает
    status: Mapped[str] = mapped_column(String(32), default=DRAFT_DRAFT, index=True)
    created_by: Mapped[str] = mapped_column(String(64), default="")  # TC-логин пользователя
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    pushed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Analysis(Base):
    """Результаты проверок: аудит качества, конфликты, impact, traceability."""
    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)   # quality_audit | conflict | impact | traceability
    requirement_uid: Mapped[str | None] = mapped_column(ForeignKey("requirements.uid"), index=True)
    other_requirement_uid: Mapped[str | None] = mapped_column(ForeignKey("requirements.uid"), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="warning")  # info | warning | critical
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open | acknowledged | resolved | dismissed
    title: Mapped[str] = mapped_column(String(512), default="")
    details: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SyncRun(Base):
    """Журнал синхронизаций с Teamcenter."""
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | ok | failed
    spec_uid: Mapped[str] = mapped_column(String(64), default="")
    stats: Mapped[dict] = mapped_column(JSONType, default=dict)   # created/updated/unchanged/errors
    error: Mapped[str] = mapped_column(Text, default="")


class User(Base):
    """Маппинг пользователей TC <-> Koseven + права записи в Teamcenter."""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tc_login: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    koseven_login: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(256), default="")
    role: Mapped[str] = mapped_column(String(16), default="engineer")  # engineer | admin
    write_to_tc_allowed: Mapped[bool] = mapped_column(Boolean, default=False)  # право ЗАПИСИ в TC
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AppSetting(Base):
    """Настройки сервиса в целом (jsonb-значения)."""
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONType, default=dict)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# Ключ глобального выключателя записи в Teamcenter (см. services/settings_service.py)
SETTING_TC_WRITE_ALLOWED = "teamcenter.write_allowed"

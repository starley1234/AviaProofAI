"""Подключение к PostgreSQL (SQLAlchemy 2.0).

Продакшн: PostgreSQL (jsonb). Тесты могут использовать ту же схему на SQLite —
для этого jsonb-колонки объявлены через JSONB().with_variant(JSON, "sqlite").
"""
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_session_factory: sessionmaker | None = None


def get_engine(url: str | None = None):
    global _engine
    if _engine is None:
        _engine = create_engine(url or get_settings().database_url, pool_pre_ping=True)
    return _engine


def get_session_factory(url: str | None = None) -> sessionmaker:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(url), expire_on_commit=False)
    return _session_factory


@contextmanager
def session_scope(url: str | None = None):
    """Контекстный менеджер сессии: with session_scope() as s: ..."""
    factory = get_session_factory(url)
    s: Session = factory()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def init_db(url: str | None = None) -> None:
    """Создаёт все таблицы (используется при старте и в тестах)."""
    from app import models  # noqa: F401  — регистрация моделей

    Base.metadata.create_all(bind=get_engine(url))

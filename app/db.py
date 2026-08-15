"""DB 연결과 스키마 초기화."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from .models import Base

_engine = None
_SessionFactory = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            get_settings().database_url,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            future=True,
        )
    return _engine


def get_session_factory():
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _SessionFactory


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI 의존성."""
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


# 한글 부분일치·오타 허용 검색을 위한 trigram 인덱스.
# 이 인덱스 덕분에 `매일경졔`, `이길정` 같은 오타도 후보로 잡힌다.
TRIGRAM_STATEMENTS = [
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE INDEX IF NOT EXISTS ix_person_name_trgm ON person USING gin (name gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_outlet_name_trgm ON outlet USING gin (name gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_alias_trgm ON outlet_alias USING gin (alias gin_trgm_ops)",
    "CREATE INDEX IF NOT EXISTS ix_assignment_role_trgm "
    "ON assignment USING gin (role_label gin_trgm_ops)",
]


def create_schema() -> None:
    engine = get_engine()
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        for statement in TRIGRAM_STATEMENTS:
            connection.execute(text(statement))

"""DB가 필요한 테스트용 픽스처.

`DATABASE_URL_TEST` 가 설정돼 있지 않으면 DB 테스트는 건너뛴다.
(파서 테스트는 DB 없이도 전부 돈다)
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("SECRET_KEY", "test" * 10)

TEST_DB_URL = os.getenv("DATABASE_URL_TEST")
if TEST_DB_URL:
    os.environ["DATABASE_URL"] = TEST_DB_URL

FIXTURES = Path(__file__).resolve().parent / "fixtures"

requires_db = pytest.mark.skipif(
    not TEST_DB_URL, reason="DATABASE_URL_TEST 가 없어 DB 테스트를 건너뜁니다"
)


@pytest.fixture(scope="session", autouse=True)
def fixtures_exist():
    if not (FIXTURES / "sample_desk_matrix.xlsx").exists():
        from tests.make_fixtures import main

        main()


@pytest.fixture()
def db_session():
    """테이블을 비우고 매체 사전만 시드한 깨끗한 세션."""
    if not TEST_DB_URL:
        pytest.skip("DATABASE_URL_TEST 없음")

    from sqlalchemy import text

    from app.db import create_schema, get_engine, get_session_factory
    from app.models import Base

    engine = get_engine()
    Base.metadata.drop_all(engine)
    create_schema()
    with engine.begin() as connection:
        connection.execute(text("SELECT 1"))

    from tools.init_db import seed_outlets

    seed_outlets()

    session = get_session_factory()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()

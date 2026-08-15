"""메인 매트릭스 조회(directory.matrix)의 정렬 규칙."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.ingest.normalize import normalize_key
from app.models import Outlet
from app.services.directory import matrix
from app.services.edit import create_assignment, parse_form
from tests.conftest import requires_db

pytestmark = requires_db


def form(**overrides) -> dict:
    base = {
        "name": "홍길동",
        "phone": "010-1111-2222",
        "kind": "desk",
        "role_slot": "산업부장",
        "role_label": "산업부장",
        "dept": "산업부",
        "note": "",
    }
    base.update(overrides)
    return base


class TestEtcCategoryOrdering:
    """기타 분류: 데스크 있는 매체 가나다순 → 데스크 없는 매체 가나다순."""

    @pytest.fixture()
    def etc_outlets(self, db_session):
        phones = iter([f"010-9000-{n:04d}" for n in range(1, 10)])
        rows = {}
        for name in ("가가일보", "나나일보", "다다일보"):
            outlet = Outlet(
                name=name, name_key=normalize_key(name), category="기타", sort_order=9000
            )
            db_session.add(outlet)
            rows[name] = outlet
        db_session.flush()

        # 나나일보·다다일보에는 데스크, 가가일보에는 출입기자만.
        for name in ("다다일보", "나나일보"):
            create_assignment(
                db_session, rows[name],
                parse_form(form(name=f"{name}부장", phone=next(phones))),
                user=None,
            )
        create_assignment(
            db_session, rows["가가일보"],
            parse_form(form(name="가가기자", phone=next(phones), kind="reporter",
                            role_slot="", role_label="기자")),
            user=None,
        )
        db_session.commit()
        return rows

    def test_desk_outlets_come_first_in_gnada_order(self, db_session, etc_outlets):
        _, groups = matrix(db_session)
        etc = dict(groups).get("기타")
        assert etc is not None, "기타 분류가 매트릭스에 나와야 한다"
        names = [row.name for row in etc if row.name in etc_outlets]
        assert names == ["나나일보", "다다일보", "가가일보"]

    def test_reporter_only_outlet_has_badge_count(self, db_session, etc_outlets):
        _, groups = matrix(db_session)
        etc = {row.name: row for row in dict(groups)["기타"]}
        assert etc["가가일보"].reporter_count == 1
        assert etc["가가일보"].cells == {}

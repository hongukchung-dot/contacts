"""재구축 때 손으로 고친 내용을 백업했다가 되살리는 흐름."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Assignment, Outlet, Person
from app.services.edit import close_assignment, create_assignment, parse_form, update_assignment
from app.services.manual_backup import (
    collect_manual_state,
    load_backup,
    reapply_manual_state,
    save_backup,
)
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


def make_auto_seat(db_session, outlet, name, phone, *, kind="desk", slot="산업부장"):
    """파일 적재로 들어온 것 같은 (잠기지 않은) 자리."""
    fields = form(name=name, phone=phone, kind=kind)
    if kind == "reporter":
        fields.update(role_slot="", role_label="차장")
    create_assignment(db_session, outlet, parse_form(fields), user=None)
    db_session.flush()
    seat = db_session.scalars(
        select(Assignment).join(Person).where(Person.name == name)
    ).all()[-1]
    seat.locked = False
    seat.edited_at = None
    return seat


@pytest.fixture()
def seoul(db_session):
    return db_session.scalar(select(Outlet).where(Outlet.name == "서울신문"))


class TestRoundTrip:
    def test_hand_corrected_seat_survives_rebuild(self, db_session, seoul):
        """곽소영(오적재) → 이경주 로 고친 자리가 재구축 후에도 이경주로 남는다."""
        seat = make_auto_seat(db_session, seoul, "곽소영", "010-1000-0001")
        update_assignment(
            db_session, seat,
            parse_form(form(name="이경주", phone="010-5283-1856")),
            user=None,
        )
        db_session.commit()

        state = collect_manual_state(db_session)
        assert [s["name"] for s in state["active_seats"]] == ["이경주"]

        # 재구축: 전부 지우고 파일이 다시 곽소영을 만들어 놓은 상황
        db_session.query(Assignment).delete()
        db_session.query(Person).delete()
        db_session.commit()
        make_auto_seat(db_session, seoul, "곽소영", "010-1000-0001")
        db_session.commit()

        report = reapply_manual_state(db_session, state)
        db_session.commit()

        assert report["restored"] == ["서울신문 이경주"]
        lee = db_session.scalar(
            select(Assignment).join(Person).where(Person.name == "이경주", Assignment.valid_to.is_(None))
        )
        assert lee is not None and lee.locked and lee.role_slot == "산업부장"
        # 같은 칸에 돌아온 곽소영은 자동 삭제하지 않고 확인 목록에 올린다
        assert any("곽소영" in line for line in report["check"])

    def test_hand_closed_seat_is_reclosed(self, db_session, seoul):
        seat = make_auto_seat(db_session, seoul, "백민경", "010-1000-0002")
        close_assignment(seat, user=None)
        db_session.commit()

        state = collect_manual_state(db_session)
        assert [s["name"] for s in state["closed_seats"]] == ["백민경"]

        db_session.query(Assignment).delete()
        db_session.query(Person).delete()
        db_session.commit()
        make_auto_seat(db_session, seoul, "백민경", "010-1000-0002")
        db_session.commit()

        report = reapply_manual_state(db_session, state)
        db_session.commit()
        assert report["reclosed"] == ["서울신문 백민경"]
        active = db_session.scalar(
            select(Assignment).join(Person).where(Person.name == "백민경", Assignment.valid_to.is_(None))
        )
        assert active is None

    def test_email_memo_survive(self, db_session, seoul):
        seat = make_auto_seat(db_session, seoul, "김기자", "010-1000-0003")
        seat.person.email = "kim@example.com"
        seat.person.memo = "저녁 연락 선호"
        db_session.commit()

        state = collect_manual_state(db_session)
        assert state["person_extras"][0]["email"] == "kim@example.com"

        db_session.query(Assignment).delete()
        db_session.query(Person).delete()
        db_session.commit()
        make_auto_seat(db_session, seoul, "김기자", "010-1000-0003")
        db_session.commit()

        report = reapply_manual_state(db_session, state)
        db_session.commit()
        person = db_session.scalar(select(Person).where(Person.name == "김기자"))
        assert person.email == "kim@example.com"
        assert person.memo == "저녁 연락 선호"
        assert report["extras"] == ["김기자"]

    def test_hand_created_seat_is_recreated_when_missing(self, db_session, seoul):
        """직접 입력으로만 넣은 사람 — 파일에는 없으므로 재구축 후 새로 만들어진다."""
        create_assignment(
            db_session, seoul,
            parse_form(form(name="신입기자", phone="010-1000-0004", kind="reporter",
                            role_slot="", role_label="기자")),
            user=None,
        )
        db_session.commit()

        state = collect_manual_state(db_session)
        db_session.query(Assignment).delete()
        db_session.query(Person).delete()
        db_session.commit()

        report = reapply_manual_state(db_session, state)
        db_session.commit()
        assert report["restored"] == ["서울신문 신입기자"]
        seat = db_session.scalar(
            select(Assignment).join(Person).where(Person.name == "신입기자")
        )
        assert seat is not None and seat.kind == "reporter" and seat.locked

    def test_backup_file_roundtrip(self, db_session, seoul, tmp_path):
        make_auto_seat(db_session, seoul, "박부장", "010-1000-0005")
        db_session.commit()
        state = collect_manual_state(db_session)
        path = save_backup(state, tmp_path)
        assert load_backup(path) == state

    def test_deleted_seat_stays_deleted_after_rebuild(self, db_session, seoul):
        from app.services.edit import purge_assignment

        seat = make_auto_seat(db_session, seoul, "오입력", "010-1000-0006")
        purge_assignment(db_session, seat)
        db_session.commit()

        state = collect_manual_state(db_session)
        assert [s["name"] for s in state["deleted_seats"]] == ["오입력"]

        db_session.query(Assignment).delete()
        db_session.query(Person).delete()
        db_session.commit()
        make_auto_seat(db_session, seoul, "오입력", "010-1000-0006")
        db_session.commit()

        report = reapply_manual_state(db_session, state)
        db_session.commit()
        assert report["redeleted"] == ["서울신문 오입력"]
        active = db_session.scalar(
            select(Assignment).join(Person).where(Person.name == "오입력", Assignment.valid_to.is_(None))
        )
        assert active is None

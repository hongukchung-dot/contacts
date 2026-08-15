"""화면에서 직접 수정·삭제·합치기 하는 기능 테스트.

실제로 겪은 두 상황을 그대로 재현한다.
  · 서울신문 경제부장에 같은 사람이 두 명으로 들어감 → 합치기
  · 서울신문 산업부장이 엉뚱한 사람으로 잡힘        → 수정
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from app.models import Assignment, Outlet, Person, PersonPhone
from app.services.edit import (
    EditError,
    close_assignment,
    create_assignment,
    merge_candidates,
    merge_persons,
    parse_form,
    purge_assignment,
    update_assignment,
)
from tests.conftest import FIXTURES, requires_db

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


@pytest.fixture()
def seoul(db_session):
    """서울신문에 데스크 두 명이 있는 상태를 만든다. (매체는 사전 시드에 이미 있다)"""
    outlet = db_session.scalar(select(Outlet).where(Outlet.name == "서울신문"))
    assert outlet is not None

    create_assignment(
        db_session,
        outlet,
        parse_form(form(name="이경주", phone="010-5283-1856", role_slot="산업부장", role_label="산업부장")),
        user=None,
    )
    create_assignment(
        db_session,
        outlet,
        parse_form(
            form(name="곽소영", phone="010-8914-2165", role_slot="산업부장", role_label="산업부장")
        ),
        user=None,
    )
    db_session.commit()
    return outlet


def current(session, outlet, slot=None):
    stmt = select(Assignment).where(
        Assignment.outlet_id == outlet.id, Assignment.valid_to.is_(None)
    )
    if slot:
        stmt = stmt.where(Assignment.role_slot == slot)
    return session.scalars(stmt).all()


# ── 폼 검증 ─────────────────────────────────────────────────────────────────

class TestForm:
    def test_name_is_required(self):
        with pytest.raises(EditError, match="이름"):
            parse_form(form(name="  "))

    def test_spaced_name_is_normalized(self):
        assert parse_form(form(name="이 경 주")).name == "이경주"

    def test_phone_is_normalized(self):
        assert parse_form(form(phone="010 5283 1856")).phone == "01052831856"

    def test_bad_phone_is_rejected(self):
        with pytest.raises(EditError, match="전화번호"):
            parse_form(form(phone="1234"))

    def test_empty_phone_is_allowed(self):
        assert parse_form(form(phone="")).phone is None

    def test_kind_must_be_valid(self):
        with pytest.raises(EditError, match="구분"):
            parse_form(form(kind="누구게"))

    def test_blank_fields_become_none(self):
        parsed = parse_form(form(dept="", note="", role_slot=""))
        assert parsed.dept is None and parsed.note is None and parsed.role_slot is None


# ── 수정 ────────────────────────────────────────────────────────────────────

class TestUpdate:
    def test_wrong_role_can_be_corrected(self, db_session, seoul):
        """곽소영을 산업부장에서 빼고 출입기자로 되돌린다."""
        wrong = next(a for a in current(db_session, seoul) if a.person.name == "곽소영")
        changes = update_assignment(
            db_session,
            wrong,
            parse_form(
                form(name="곽소영", phone="010-8914-2165", kind="reporter", role_slot="", role_label="기자")
            ),
            user=None,
        )
        db_session.commit()

        assert any("직책 열" in c for c in changes)
        remaining = current(db_session, seoul, slot="산업부장")
        assert [a.person.name for a in remaining] == ["이경주"]

    def test_edit_marks_the_row_as_locked(self, db_session, seoul):
        assignment = current(db_session, seoul)[0]
        update_assignment(db_session, assignment, parse_form(form(name=assignment.person.name)), user=None)
        db_session.commit()
        assert assignment.locked is True
        assert assignment.edited_at is not None

    def test_name_change_applies_to_the_person(self, db_session, seoul):
        assignment = next(a for a in current(db_session, seoul) if a.person.name == "곽소영")
        update_assignment(db_session, assignment, parse_form(form(name="곽소연")), user=None)
        db_session.commit()
        assert db_session.get(Person, assignment.person_id).name == "곽소연"

    def test_phone_change_keeps_history(self, db_session, seoul):
        assignment = next(a for a in current(db_session, seoul) if a.person.name == "이경주")
        update_assignment(
            db_session, assignment, parse_form(form(name="이경주", phone="010-9999-0000")), user=None
        )
        db_session.commit()

        person = db_session.get(Person, assignment.person_id)
        assert person.phone == "01099990000"
        history = db_session.scalars(
            select(PersonPhone).where(PersonPhone.person_id == person.id)
        ).all()
        assert {p.phone for p in history} == {"01052831856", "01099990000"}
        assert any(p.valid_to is not None for p in history), "옛 번호는 닫혀야 한다"

    def test_rank_follows_the_new_role(self, db_session, seoul):
        assignment = current(db_session, seoul)[0]
        update_assignment(
            db_session,
            assignment,
            parse_form(form(name=assignment.person.name, kind="reporter", role_label="차장")),
            user=None,
        )
        assert assignment.rank == "차장"


# ── 삭제 ────────────────────────────────────────────────────────────────────

class TestDelete:
    def test_purge_hides_the_row_but_keeps_a_tombstone(self, db_session, seoul):
        target = next(a for a in current(db_session, seoul) if a.person.name == "곽소영")
        person_id = target.person_id
        assignment_id = target.id
        purge_assignment(db_session, target)
        db_session.commit()

        # 화면(활성 목록)에서는 사라진다
        assert [a.person.name for a in current(db_session, seoul)] == ["이경주"]
        # 그러나 '오류 삭제' 기록은 남는다 — 다음 업로드가 조용히 되살리지 못하게
        tombstone = db_session.get(Assignment, assignment_id)
        assert tombstone is not None
        assert tombstone.deleted_at is not None
        assert tombstone.locked is True
        assert db_session.get(Person, person_id) is not None

    def test_purge_keeps_person_with_other_seats(self, db_session, seoul):
        person = db_session.scalar(select(Person).where(Person.name == "곽소영"))
        create_assignment(
            db_session,
            seoul,
            parse_form(form(name="곽소영", phone="010-8914-2165", kind="reporter", role_slot="")),
            user=None,
        )
        db_session.commit()

        target = next(
            a for a in current(db_session, seoul) if a.person_id == person.id and a.kind == "desk"
        )
        purge_assignment(db_session, target)
        db_session.commit()
        assert db_session.get(Person, person.id) is not None

    def test_close_keeps_history(self, db_session, seoul):
        target = next(a for a in current(db_session, seoul) if a.person.name == "곽소영")
        close_assignment(target, user=None)
        db_session.commit()

        assert [a.person.name for a in current(db_session, seoul)] == ["이경주"]
        assert target.valid_to == date.today()
        assert db_session.get(Assignment, target.id) is not None, "행 자체는 남는다"


# ── 인물 합치기 ─────────────────────────────────────────────────────────────

class TestMerge:
    @pytest.fixture()
    def duplicated(self, db_session, seoul):
        """백민경이 두 번 등록된 상태 — 한 명은 번호가 비어 있다."""
        create_assignment(
            db_session,
            seoul,
            parse_form(
                form(name="백민경", phone="010-5564-8585", role_slot="경제부장", role_label="경제부장")
            ),
            user=None,
        )
        second = Person(name="백민경", name_key="백민경", phone=None)
        db_session.add(second)
        db_session.flush()
        db_session.add(
            Assignment(
                person_id=second.id,
                outlet_id=seoul.id,
                kind="desk",
                role_slot="경제부장",
                role_label="디지털금융부장",
                dept=None,
                valid_from=date.today(),
            )
        )
        db_session.commit()
        return seoul

    def test_candidates_are_found_by_name(self, db_session, duplicated):
        first = db_session.scalars(select(Person).where(Person.name == "백민경")).all()[0]
        assert [p.name for p in merge_candidates(db_session, first)] == ["백민경"]

    def test_merge_moves_seats_and_removes_the_duplicate(self, db_session, duplicated):
        people = db_session.scalars(
            select(Person).where(Person.name == "백민경").order_by(Person.id)
        ).all()
        keep, drop = people[0], people[1]
        drop_id = drop.id

        merge_persons(db_session, keep, drop, user=None)
        db_session.commit()

        assert db_session.get(Person, drop_id) is None
        remaining = db_session.scalars(select(Person).where(Person.name == "백민경")).all()
        assert len(remaining) == 1

        seats = [a for a in current(db_session, duplicated) if a.person_id == keep.id]
        assert {a.role_label for a in seats} >= {"경제부장", "디지털금융부장"}

    def test_merge_keeps_the_phone_number(self, db_session, duplicated):
        people = db_session.scalars(
            select(Person).where(Person.name == "백민경").order_by(Person.id)
        ).all()
        merge_persons(db_session, people[0], people[1], user=None)
        db_session.commit()
        assert people[0].phone == "01055648585"

    def test_identical_seats_are_deduplicated(self, db_session, seoul):
        """완전히 같은 자리가 양쪽에 있으면 하나만 남는다."""
        duplicate = Person(name="이경주", name_key="이경주", phone=None)
        db_session.add(duplicate)
        db_session.flush()
        db_session.add(
            Assignment(
                person_id=duplicate.id,
                outlet_id=seoul.id,
                kind="desk",
                role_slot="산업부장",
                role_label="산업부장",
                dept="산업부",
                valid_from=date.today(),
            )
        )
        db_session.commit()

        keep = db_session.scalar(select(Person).where(Person.name == "이경주", Person.phone.is_not(None)))
        merge_persons(db_session, keep, duplicate, user=None)
        db_session.commit()

        seats = [a for a in current(db_session, seoul, slot="산업부장") if a.person_id == keep.id]
        assert len(seats) == 1

    def test_cannot_merge_with_self(self, db_session, seoul):
        person = db_session.scalar(select(Person).where(Person.name == "이경주"))
        with pytest.raises(EditError):
            merge_persons(db_session, person, person, user=None)


# ── 손으로 고친 항목은 다음 업로드에 밀리지 않는다 ─────────────────────────

class TestLockedSurvivesUpload:
    @pytest.fixture(autouse=True)
    def _tmp(self, tmp_path):
        self.tmp = tmp_path

    def test_edited_row_is_not_auto_applied_over(self, db_session):
        from app.services.ingest import apply_change_set, stage_file

        change_set = stage_file(
            db_session, FIXTURES / "sample_desk_matrix.xlsx", filename="a.xlsx"
        )
        db_session.flush()
        apply_change_set(db_session, change_set, applied_by_id=None)
        db_session.commit()

        # 조선일보 편집국장 강경희의 번호를 손으로 고친다
        target = db_session.scalar(
            select(Assignment)
            .join(Person)
            .where(Person.name == "강경희", Assignment.valid_to.is_(None))
        )
        update_assignment(
            db_session,
            target,
            parse_form(
                form(name="강경희", phone="010-7777-8888", kind="desk", role_slot="편집국장", role_label="편집국장")
            ),
            user=None,
        )
        db_session.commit()

        # 같은 사람을 담은 새 파일을 올린다 (원래 번호로 되돌리려 함)
        from tests.test_ingest import _rewrite_phone

        later = _rewrite_phone(
            self.tmp, "sample_desk_matrix.xlsx", "010-3121-0012", "010-3121-9999"
        )
        second = stage_file(
            db_session, later, filename="(26-0901) 데스크.xlsx", as_of_override=date(2026, 9, 1)
        )
        db_session.flush()

        touching = [c for c in second.changes if c.person_name == "강경희"]
        assert touching, "번호를 되돌리려는 변경이 잡혀야 한다"
        assert all(not c.auto_apply for c in touching), "손으로 고친 항목은 자동 반영되면 안 된다"
        assert all("손으로 고친" in (c.reason or "") for c in touching)


class TestMergeOutlets:
    """같은 매체가 두 표기로 갈라져 들어온 경우 (헤럴드 / 헤럴드경제 등)."""

    @pytest.fixture()
    def split(self, db_session):
        """`헤럴드` 와 `헤럴드경제` 가 따로 등록된 상태를 만든다."""
        from app.ingest.normalize import normalize_key

        keep = db_session.scalar(select(Outlet).where(Outlet.name == "헤럴드경제"))
        assert keep is not None
        drop = Outlet(
            name="헤럴드", name_key=normalize_key("헤럴드"), category="기타", sort_order=9000
        )
        db_session.add(drop)
        db_session.flush()

        create_assignment(
            db_session, keep,
            parse_form(form(name="한석희", phone="010-4320-0792", role_slot="산업부장", role_label="산업부장")),
            user=None,
        )
        create_assignment(
            db_session, drop,
            parse_form(form(name="서경원", phone="010-7404-0130", kind="reporter", role_slot="", role_label="차장")),
            user=None,
        )
        # 양쪽에 같은 사람이 다른 인물로 들어간 경우
        create_assignment(
            db_session, drop,
            parse_form(form(name="한석희", phone="010-4320-0792", kind="reporter", role_slot="", role_label="국장")),
            user=None,
        )
        db_session.commit()
        return keep, drop

    def test_dictionary_now_maps_the_alias(self):
        from app.ingest.reference import get_reference

        reference = get_reference()
        for alias, canonical in (
            ("헤럴드", "헤럴드경제"),
            ("인사이트", "인사이트코리아"),
            ("시사저널e", "시사저널"),
            ("포브스", "포브스코리아"),
        ):
            outlet = reference.match_outlet(alias)
            assert outlet is not None and outlet.name == canonical, alias

    def test_merge_moves_every_seat(self, db_session, split):
        from app.services.edit import merge_outlets

        keep, drop = split
        drop_id = drop.id
        result = merge_outlets(db_session, keep, drop)
        db_session.commit()

        assert db_session.get(Outlet, drop_id) is None
        assert result["moved"] == 2
        names = {a.person.name for a in current(db_session, keep)}
        assert names == {"한석희", "서경원"}

    def test_same_person_on_both_sides_is_merged(self, db_session, split):
        from app.services.edit import merge_outlets

        keep, drop = split
        merge_outlets(db_session, keep, drop)
        db_session.commit()

        people = db_session.scalars(select(Person).where(Person.name == "한석희")).all()
        assert len(people) == 1, "같은 매체에 같은 이름이 둘로 남으면 안 된다"

    def test_dropped_name_becomes_an_alias(self, db_session, split):
        from app.models import OutletAlias
        from app.services.edit import merge_outlets

        keep, drop = split
        merge_outlets(db_session, keep, drop)
        db_session.commit()

        alias = db_session.scalar(select(OutletAlias).where(OutletAlias.alias_key == "헤럴드"))
        assert alias is not None and alias.outlet_id == keep.id

    def test_auto_pairs_are_found_from_the_dictionary(self, db_session, split):
        from tools.merge_outlets import find_auto_pairs

        assert ("헤럴드", "헤럴드경제") in find_auto_pairs()

    def test_cannot_merge_an_outlet_with_itself(self, db_session, split):
        from app.services.edit import EditError as _EditError
        from app.services.edit import merge_outlets

        keep, _ = split
        with pytest.raises(_EditError):
            merge_outlets(db_session, keep, keep)


class TestDedupeDeskReporter:
    """같은 매체·이름·번호가 데스크와 출입기자로 겹치면 출입기자를 지운다."""

    @pytest.fixture()
    def outlet(self, db_session):
        return db_session.scalar(select(Outlet).where(Outlet.name == "서울신문"))

    def _seat(self, db_session, outlet, name, phone, kind, *, locked=False):
        """파일 적재로 들어온 자리처럼 만든다 (직접 입력과 달리 잠기지 않음)."""
        if kind == "desk":
            fields = form(name=name, phone=phone, role_slot="산업부장", role_label="산업부장")
        else:
            fields = form(name=name, phone=phone, kind="reporter", role_slot="", role_label="차장")
        create_assignment(db_session, outlet, parse_form(fields), user=None)
        db_session.flush()
        seat = db_session.scalars(
            select(Assignment).join(Person).where(Person.name == name, Assignment.kind == kind)
        ).all()[-1]
        seat.locked = locked
        return seat

    def _desk(self, db_session, outlet, name, phone):
        return self._seat(db_session, outlet, name, phone, "desk")

    def _reporter(self, db_session, outlet, name, phone):
        return self._seat(db_session, outlet, name, phone, "reporter")

    def test_same_person_reporter_seat_is_removed(self, db_session, outlet):
        from app.services.edit import find_desk_reporter_overlaps, remove_desk_reporter_overlaps

        self._desk(db_session, outlet, "이경주", "010-5283-1856")
        self._reporter(db_session, outlet, "이경주", "010-5283-1856")
        db_session.commit()

        overlaps = find_desk_reporter_overlaps(db_session)
        assert [(o.outlet_name, o.person_name) for o in overlaps] == [("서울신문", "이경주")]

        removed, skipped = remove_desk_reporter_overlaps(db_session)
        db_session.commit()
        assert (removed, skipped) == (1, 0)

        kinds = db_session.scalars(
            select(Assignment.kind)
            .join(Person)
            .where(Person.name == "이경주", Assignment.valid_to.is_(None))
        ).all()
        assert kinds == ["desk"]

    def test_reporter_without_desk_is_kept(self, db_session, outlet):
        from app.services.edit import find_desk_reporter_overlaps

        self._reporter(db_session, outlet, "곽소영", "010-1234-0001")
        db_session.commit()
        assert find_desk_reporter_overlaps(db_session) == []

    def test_same_name_different_phone_is_kept(self, db_session, outlet):
        """번호가 다르면 동명이인일 수 있으므로 지우지 않는다."""
        from datetime import date

        from app.ingest.normalize import normalize_key
        from app.services.edit import find_desk_reporter_overlaps

        # 이름은 같고 번호가 다른 두 인물 행을 직접 만든다
        a = Person(name="김철수", name_key=normalize_key("김철수"), phone="01011110001")
        b = Person(name="김철수", name_key=normalize_key("김철수"), phone="01022220002")
        db_session.add_all([a, b])
        db_session.flush()
        db_session.add_all([
            Assignment(person_id=a.id, outlet_id=outlet.id, kind="desk",
                       role_slot="산업부장", role_label="산업부장", rank="부장", rank_order=30,
                       valid_from=date.today()),
            Assignment(person_id=b.id, outlet_id=outlet.id, kind="reporter",
                       role_label="차장", rank="차장", rank_order=40,
                       valid_from=date.today()),
        ])
        db_session.commit()
        assert find_desk_reporter_overlaps(db_session) == []

    def test_same_outlet_only(self, db_session):
        """다른 매체의 출입기자 자리는 겹침이 아니다 (데스크 겸 타사 출입 아님)."""
        from app.services.edit import find_desk_reporter_overlaps

        seoul = db_session.scalar(select(Outlet).where(Outlet.name == "서울신문"))
        hankook = db_session.scalar(select(Outlet).where(Outlet.name == "한국일보"))
        self._desk(db_session, seoul, "박영희", "010-3333-0003")
        self._reporter(db_session, hankook, "박영희", "010-3333-0003")
        db_session.commit()
        assert find_desk_reporter_overlaps(db_session) == []

    def test_locked_reporter_seat_is_skipped(self, db_session, outlet):
        from app.services.edit import remove_desk_reporter_overlaps

        self._desk(db_session, outlet, "최민수", "010-4444-0004")
        self._seat(db_session, outlet, "최민수", "010-4444-0004", "reporter", locked=True)
        db_session.commit()

        removed, skipped = remove_desk_reporter_overlaps(db_session)
        assert (removed, skipped) == (0, 1)


class TestFixEmbeddedPhones:
    """이름·직책 칸에 붙은 번호를 번호 칸으로 옮기는 정리 도구."""

    @pytest.fixture()
    def outlet(self, db_session):
        return db_session.scalar(select(Outlet).where(Outlet.name == "서울신문"))

    def _dirty_seat(self, db_session, outlet, name, role_label, phone=None):
        from datetime import date as date_cls

        from app.ingest.normalize import normalize_key

        person = Person(name=name, name_key=normalize_key(name), phone=phone)
        db_session.add(person)
        db_session.flush()
        seat = Assignment(
            person_id=person.id, outlet_id=outlet.id, kind="reporter",
            role_label=role_label, rank="기자", rank_order=50, valid_from=date_cls.today(),
        )
        db_session.add(seat)
        db_session.flush()
        return person, seat

    def test_number_in_role_moves_to_empty_phone(self, db_session, outlet):
        from tools.fix_embedded_phones import apply_fixes, find_dirty_rows

        person, seat = self._dirty_seat(db_session, outlet, "김혜연", "편집국장 02-393-0188")
        db_session.commit()

        rows = find_dirty_rows(db_session)
        assert [r["person"].name for r in rows] == ["김혜연"]
        counts = apply_fixes(db_session, rows)
        db_session.commit()

        assert counts["moved"] == 1
        assert person.phone == "023930188"
        assert seat.role_label == "편집국장"

    def test_number_kept_in_memo_when_phone_differs(self, db_session, outlet):
        from tools.fix_embedded_phones import apply_fixes, find_dirty_rows

        person, seat = self._dirty_seat(
            db_session, outlet, "박부장", "부장 02-777-8888", phone="01012345678"
        )
        db_session.commit()

        counts = apply_fixes(db_session, find_dirty_rows(db_session))
        db_session.commit()

        assert counts["noted"] == 1
        assert person.phone == "01012345678"
        assert "유선 02-777-8888" in person.memo
        assert seat.role_label == "부장"

    def test_clean_data_is_left_alone(self, db_session, outlet):
        from tools.fix_embedded_phones import find_dirty_rows

        self._dirty_seat(db_session, outlet, "정상인", "산업1부장", phone="01011112222")
        db_session.commit()
        assert find_dirty_rows(db_session) == []

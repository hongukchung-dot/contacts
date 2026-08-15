"""적재 → 비교 → 승인 → 반영 통합 테스트 (실제 PostgreSQL 사용)."""

from __future__ import annotations

import shutil
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.models import (
    APPLIED,
    APPROVED,
    CONFLICT,
    NEW,
    PENDING,
    REMOVE,
    UPDATE,
    Assignment,
    Person,
)
from app.services.ingest import DuplicateFileError, apply_change_set, stage_file
from tests.conftest import FIXTURES, requires_db

pytestmark = requires_db


def load(session, fixture_name: str, filename: str | None = None, as_of=None):
    path = FIXTURES / fixture_name
    change_set = stage_file(
        session, path, filename=filename or fixture_name, as_of_override=as_of
    )
    session.flush()
    return change_set


def apply(session, change_set):
    counts = apply_change_set(session, change_set, applied_by_id=None)
    session.commit()
    return counts


def current_assignments(session, outlet_name: str | None = None):
    from app.models import Outlet

    stmt = select(Assignment).where(Assignment.valid_to.is_(None))
    if outlet_name:
        stmt = stmt.join(Outlet).where(Outlet.name == outlet_name)
    return session.scalars(stmt).all()


def person_by_name(session, name: str) -> Person | None:
    return session.scalar(select(Person).where(Person.name == name))


# ── 최초 적재 ───────────────────────────────────────────────────────────────

class TestFirstLoad:
    def test_all_changes_are_new_and_auto_approved(self, db_session):
        change_set = load(db_session, "sample_reporter_list.xlsx")
        assert change_set.changes
        assert {c.change_type for c in change_set.changes} == {NEW}
        assert all(c.decision == APPROVED for c in change_set.changes)

    def test_nothing_is_written_before_apply(self, db_session):
        load(db_session, "sample_reporter_list.xlsx")
        assert current_assignments(db_session) == []

    def test_apply_creates_people_and_assignments(self, db_session):
        change_set = load(db_session, "sample_reporter_list.xlsx")
        counts = apply(db_session, change_set)

        assert counts[NEW] > 0
        assert change_set.status == APPLIED
        assert person_by_name(db_session, "이길성") is not None
        chosun = current_assignments(db_session, "조선일보")
        assert {a.person.name for a in chosun} >= {"이길성", "정한국", "전수용", "안별"}

    def test_source_records_preserve_original_text(self, db_session):
        change_set = load(db_session, "sample_reporter_list.xlsx")
        records = change_set.source_file.records
        assert records
        assert all(record.source_text for record in records)
        assert change_set.source_file.as_of == date(2026, 6, 9)


# ── 재업로드 차단 ───────────────────────────────────────────────────────────

class TestDuplicate:
    def test_same_file_twice_is_rejected(self, db_session):
        change_set = load(db_session, "sample_reporter_list.xlsx")
        apply(db_session, change_set)
        with pytest.raises(DuplicateFileError):
            load(db_session, "sample_reporter_list.xlsx", filename="다시올림.xlsx")


# ── 갱신 파일 반영 ──────────────────────────────────────────────────────────

class TestUpdateCycle:
    @pytest.fixture()
    def seeded(self, db_session):
        apply(db_session, load(db_session, "sample_reporter_list.xlsx"))
        return db_session

    def test_newer_file_adds_and_updates(self, seeded):
        change_set = load(seeded, "sample_combined.docx")
        types = {c.change_type for c in change_set.changes}
        assert NEW in types
        apply(seeded, change_set)

        # docx 에만 있는 인물이 들어와야 한다
        assert person_by_name(seeded, "최인준") is not None
        assert person_by_name(seeded, "박순찬") is not None

    def test_existing_person_is_matched_by_phone_not_duplicated(self, seeded):
        apply(seeded, load(seeded, "sample_combined.docx"))
        rows = seeded.scalars(select(Person).where(Person.name == "이길성")).all()
        assert len(rows) == 1, "같은 사람이 두 번 등록되면 안 된다"

    def test_desk_records_land_in_matrix_slot(self, seeded):
        apply(seeded, load(seeded, "sample_combined.docx"))
        desks = [a for a in current_assignments(seeded, "조선일보") if a.kind == "desk"]
        assert {a.person.name for a in desks} >= {"이길성", "전수용"}
        assert all(a.role_slot == "산업부장" for a in desks)

    def test_history_is_kept_not_overwritten(self, seeded):
        apply(seeded, load(seeded, "sample_combined.docx"))
        # 출입기자로 먼저 들어온 이길성은 데스크 자리가 새로 생겨도 이력이 남는다
        person = person_by_name(seeded, "이길성")
        rows = seeded.scalars(
            select(Assignment).where(Assignment.person_id == person.id)
        ).all()
        assert len(rows) >= 1
        assert any(a.valid_to is None for a in rows)


# ── 오타 의심 탐지 ──────────────────────────────────────────────────────────

class TestTypoDetection:
    def test_one_digit_difference_becomes_conflict(self, db_session, tmp_path):
        apply(db_session, load(db_session, "sample_reporter_list.xlsx"))

        # 손철의 번호를 한 자리만 바꾼 파일을 만든다 (실제 원본에서 발견된 유형)
        modified = _rewrite_phone(
            tmp_path, "sample_reporter_list.xlsx", "010-8601-0040", "010-8601-0049"
        )
        change_set = stage_file(
            db_session, modified, filename="(26-0801) 출입기자 현황.xlsx", as_of_override=date(2026, 8, 1)
        )
        db_session.flush()

        conflicts = [c for c in change_set.changes if c.change_type == CONFLICT]
        assert conflicts, "한 자리 차이는 오타 의심으로 잡혀야 한다"
        conflict = conflicts[0]
        assert conflict.person_name == "손철"
        assert conflict.auto_apply is False
        assert conflict.decision == PENDING
        assert "한 자리" in (conflict.reason or "")

    def test_conflict_is_not_applied_without_approval(self, db_session, tmp_path):
        apply(db_session, load(db_session, "sample_reporter_list.xlsx"))
        modified = _rewrite_phone(
            tmp_path, "sample_reporter_list.xlsx", "010-8601-0040", "010-8601-0049"
        )
        change_set = stage_file(
            db_session, modified, filename="수정본.xlsx", as_of_override=date(2026, 8, 1)
        )
        db_session.flush()
        apply(db_session, change_set)

        assert person_by_name(db_session, "손철").phone == "01086010040"

    def test_conflict_is_applied_after_approval(self, db_session, tmp_path):
        apply(db_session, load(db_session, "sample_reporter_list.xlsx"))
        modified = _rewrite_phone(
            tmp_path, "sample_reporter_list.xlsx", "010-8601-0040", "010-8601-0049"
        )
        change_set = stage_file(
            db_session, modified, filename="수정본.xlsx", as_of_override=date(2026, 8, 1)
        )
        db_session.flush()
        for change in change_set.changes:
            if change.change_type == CONFLICT:
                change.decision = APPROVED
        apply(db_session, change_set)

        assert person_by_name(db_session, "손철").phone == "01086010049"

    def test_completely_different_phone_is_plain_update(self, db_session, tmp_path):
        apply(db_session, load(db_session, "sample_reporter_list.xlsx"))
        modified = _rewrite_phone(
            tmp_path, "sample_reporter_list.xlsx", "010-8601-0040", "010-2222-7777"
        )
        change_set = stage_file(
            db_session, modified, filename="수정본.xlsx", as_of_override=date(2026, 8, 1)
        )
        db_session.flush()
        phone_changes = [c for c in change_set.changes if c.field == "phone"]
        assert phone_changes
        assert phone_changes[0].change_type == UPDATE
        assert phone_changes[0].auto_apply is True


# ── 삭제 탐지 ───────────────────────────────────────────────────────────────

class TestRemoval:
    def test_missing_person_becomes_removal_needing_approval(self, db_session, tmp_path):
        apply(db_session, load(db_session, "sample_reporter_list.xlsx"))
        trimmed = _drop_row(tmp_path, "sample_reporter_list.xlsx", "정한국")

        change_set = stage_file(
            db_session, trimmed, filename="(26-0801) 출입기자 현황.xlsx", as_of_override=date(2026, 8, 1)
        )
        db_session.flush()

        removals = [c for c in change_set.changes if c.change_type == REMOVE]
        assert [c.person_name for c in removals] == ["정한국"]
        assert removals[0].auto_apply is False

    def test_removal_closes_assignment_but_keeps_history(self, db_session, tmp_path):
        apply(db_session, load(db_session, "sample_reporter_list.xlsx"))
        trimmed = _drop_row(tmp_path, "sample_reporter_list.xlsx", "정한국")
        change_set = stage_file(
            db_session, trimmed, filename="축소본.xlsx", as_of_override=date(2026, 8, 1)
        )
        db_session.flush()
        for change in change_set.changes:
            change.decision = APPROVED
        apply(db_session, change_set)

        person = person_by_name(db_session, "정한국")
        assert person is not None, "사람 자체는 지우지 않는다"
        rows = db_session.scalars(
            select(Assignment).where(Assignment.person_id == person.id)
        ).all()
        assert rows and all(a.valid_to == date(2026, 8, 1) for a in rows)

    def test_out_of_scope_outlets_are_not_removed(self, db_session, tmp_path):
        """방송 시트만 담긴 파일이 종합지 인물을 지워서는 안 된다."""
        apply(db_session, load(db_session, "sample_reporter_list.xlsx"))
        broadcast_only = _keep_sheets(tmp_path, "sample_reporter_list.xlsx", {"방송"})

        change_set = stage_file(
            db_session, broadcast_only, filename="방송만.xlsx", as_of_override=date(2026, 8, 1)
        )
        db_session.flush()
        removals = [c for c in change_set.changes if c.change_type == REMOVE]
        assert not [c for c in removals if c.outlet_name == "조선일보"]


# ── 오래된 파일 방어 ────────────────────────────────────────────────────────

class TestStaleFile:
    def test_older_file_disables_auto_apply(self, db_session, tmp_path):
        apply(db_session, load(db_session, "sample_combined.docx"))  # 8/3

        older = tmp_path / "옛날파일.xlsx"
        shutil.copy(FIXTURES / "sample_reporter_list.xlsx", older)
        change_set = stage_file(
            db_session, older, filename="(26-0609) 출입기자 현황.xlsx", as_of_override=date(2026, 6, 9)
        )
        db_session.flush()

        assert change_set.changes
        assert all(not c.auto_apply for c in change_set.changes)
        assert all(c.decision == PENDING for c in change_set.changes)


# ── 헬퍼: 픽스처 변형 ───────────────────────────────────────────────────────

def _rewrite_phone(tmp_path: Path, fixture: str, old: str, new: str) -> Path:
    from openpyxl import load_workbook

    target = tmp_path / f"mod_{fixture}"
    shutil.copy(FIXTURES / fixture, target)
    workbook = load_workbook(target)
    for sheet in workbook.worksheets:
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and old in cell.value:
                    cell.value = cell.value.replace(old, new)
    workbook.save(target)
    return target


def _drop_row(tmp_path: Path, fixture: str, name: str) -> Path:
    from openpyxl import load_workbook

    target = tmp_path / f"drop_{fixture}"
    shutil.copy(FIXTURES / fixture, target)
    workbook = load_workbook(target)
    for sheet in workbook.worksheets:
        for idx in range(sheet.max_row, 0, -1):
            values = [c.value for c in sheet[idx]]
            if any(isinstance(v, str) and name in v for v in values):
                sheet.delete_rows(idx)
    workbook.save(target)
    return target


def _keep_sheets(tmp_path: Path, fixture: str, keep: set[str]) -> Path:
    from openpyxl import load_workbook

    target = tmp_path / f"keep_{fixture}"
    shutil.copy(FIXTURES / fixture, target)
    workbook = load_workbook(target)
    for title in [s.title for s in workbook.worksheets if s.title not in keep]:
        del workbook[title]
    workbook.save(target)
    return target


class TestRemovalFalsePositives:
    """부서·구분이 바뀐 것뿐인 사람을 삭제로 잡으면 안 된다."""

    @pytest.fixture()
    def seeded(self, db_session):
        apply(db_session, load(db_session, "sample_reporter_list.xlsx"))
        return db_session

    def test_reporter_promoted_to_desk_is_not_a_removal(self, seeded):
        """이길성은 6/9 파일엔 출입기자, 8/3 파일엔 데스크로 나온다."""
        change_set = load(seeded, "sample_combined.docx")
        removed = {c.person_name for c in change_set.changes if c.change_type == REMOVE}
        assert "이길성" not in removed
        assert "전수용" not in removed
        assert "정한국" not in removed

    def test_dept_change_is_not_a_removal(self, seeded):
        change_set = load(seeded, "sample_combined.docx")
        removed = {c.person_name for c in change_set.changes if c.change_type == REMOVE}
        assert "석민수" not in removed
        assert "박예원" not in removed

    def test_genuinely_absent_person_is_still_flagged(self, seeded, tmp_path):
        """반대로 같은 종류의 새 파일에서 정말 빠진 사람은 계속 잡아야 한다."""
        apply(seeded, load(seeded, "sample_desk_matrix.xlsx"))
        trimmed = _drop_row(tmp_path, "sample_desk_matrix.xlsx", "신 수 정")
        change_set = stage_file(
            seeded, trimmed, filename="(26-0901) 주요 데스크 현황.xlsx",
            as_of_override=date(2026, 9, 1),
        )
        seeded.flush()
        removed = {c.person_name for c in change_set.changes if c.change_type == REMOVE}
        assert "신수정" in removed


class TestRemovalScopeByFileKind:
    """수록 범위가 다른 파일끼리 서로의 인원을 지우면 안 된다."""

    @pytest.fixture(autouse=True)
    def _tmp(self, tmp_path):
        self.tmp = tmp_path

    def test_reporter_xlsx_does_not_remove_docx_people(self, db_session):
        apply(db_session, load(db_session, "sample_reporter_list.xlsx"))
        apply(db_session, load(db_session, "sample_combined.docx"))

        # docx 로만 들어온 인물들 (출입기자 xlsx 에는 없다)
        assert person_by_name(db_session, "김성민") is not None
        assert person_by_name(db_session, "최인준") is not None

        # 이제 출입기자 xlsx 계열의 새 파일을 올린다 (내용이 조금 달라야 중복이 아니다)
        newer = _rewrite_phone(
            self.tmp, "sample_reporter_list.xlsx", "010-2600-0039", "010-2600-9999"
        )
        change_set = stage_file(
            db_session, newer, filename="(26-0901) 출입기자 현황.xlsx",
            as_of_override=date(2026, 9, 1),
        )
        db_session.flush()
        removed = {c.person_name for c in change_set.changes if c.change_type == REMOVE}
        assert "김성민" not in removed
        assert "최인준" not in removed


class TestDuplicatePersonWithinFile:
    def test_same_person_in_two_columns_creates_one_person(self, db_session):
        """매일경제 강두순은 증권부장·사회부장② 두 칸에 함께 적혀 있다."""
        apply(db_session, load(db_session, "sample_desk_matrix.xlsx"))

        rows = db_session.scalars(select(Person).where(Person.name == "강두순")).all()
        assert len(rows) == 1

        assignments = db_session.scalars(
            select(Assignment).where(
                Assignment.person_id == rows[0].id, Assignment.valid_to.is_(None)
            )
        ).all()
        assert {a.role_slot for a in assignments} == {"증권부장", "사회부장"}

    def test_reupload_does_not_flag_them_as_removed(self, db_session, tmp_path):
        apply(db_session, load(db_session, "sample_desk_matrix.xlsx"))
        modified = _rewrite_phone(
            tmp_path, "sample_desk_matrix.xlsx", "010-7344-0001", "010-7344-9999"
        )
        change_set = stage_file(
            db_session, modified, filename="(26-0901) 주요 데스크 현황.xlsx",
            as_of_override=date(2026, 9, 1),
        )
        db_session.flush()
        removed = {c.person_name for c in change_set.changes if c.change_type == REMOVE}
        assert removed == set(), removed


class TestPersonMatchingRules:
    """이름만 같다고 같은 사람으로 묶으면 안 된다.

    출입기자 명단처럼 사람이 많은 파일에서 동명이인이 통째로 다른 회사로
    옮겨간 것처럼 기록되는 문제가 실제로 있었다.
    """

    def _put(self, session, outlet_name, name, phone, tmp_path, label, as_of):
        """한 사람만 담은 출입기자 파일을 만들어 적재한다."""
        from openpyxl import Workbook

        path = tmp_path / f"{label}.xlsx"
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "종합지"
        sheet.append(["매체", "이름", "직급", "전화번호"])
        sheet.append([outlet_name, name, "기자", phone])
        workbook.save(path)

        change_set = stage_file(session, path, filename=f"{label}.xlsx", as_of_override=as_of)
        session.flush()
        return change_set

    def test_same_name_different_phone_is_a_different_person(self, db_session, tmp_path):
        """동명이인 — 매체를 옮긴 게 아니다."""
        apply(db_session, self._put(
            db_session, "조선일보", "김민수", "010-1111-0001", tmp_path, "a", date(2026, 6, 1)
        ))
        change_set = self._put(
            db_session, "한국일보", "김민수", "010-2222-0002", tmp_path, "b", date(2026, 7, 1)
        )
        assert {c.change_type for c in change_set.changes} == {NEW}
        apply(db_session, change_set)

        people = db_session.scalars(select(Person).where(Person.name == "김민수")).all()
        assert len(people) == 2, "이름만 같으면 서로 다른 사람이어야 한다"

    def test_same_name_and_phone_is_a_move(self, db_session, tmp_path):
        """이름·번호가 모두 같으면 매체를 옮긴 것으로 본다."""
        apply(db_session, self._put(
            db_session, "조선일보", "박지훈", "010-3333-0003", tmp_path, "a", date(2026, 6, 1)
        ))
        change_set = self._put(
            db_session, "한국일보", "박지훈", "010-3333-0003", tmp_path, "b", date(2026, 7, 1)
        )
        apply(db_session, change_set)

        people = db_session.scalars(select(Person).where(Person.name == "박지훈")).all()
        assert len(people) == 1, "같은 사람으로 이어져야 한다"
        outlets = {a.outlet.name for a in people[0].assignments}
        assert outlets == {"조선일보", "한국일보"}

    def test_same_outlet_same_name_is_the_same_person_even_if_phone_changed(
        self, db_session, tmp_path
    ):
        """같은 매체 안에서는 번호가 바뀌어도 같은 사람으로 본다."""
        apply(db_session, self._put(
            db_session, "조선일보", "최수현", "010-4444-0004", tmp_path, "a", date(2026, 6, 1)
        ))
        change_set = self._put(
            db_session, "조선일보", "최수현", "010-9999-0009", tmp_path, "b", date(2026, 7, 1)
        )
        apply(db_session, change_set)

        people = db_session.scalars(select(Person).where(Person.name == "최수현")).all()
        assert len(people) == 1
        assert people[0].phone == "01099990009"

    def test_same_phone_different_name_needs_review(self, db_session, tmp_path):
        """번호는 같은데 이름이 다르면 담당 교체이거나 오기다. 자동 반영하지 않는다."""
        apply(db_session, self._put(
            db_session, "조선일보", "오로라", "010-4750-2723", tmp_path, "a", date(2026, 6, 1)
        ))
        change_set = self._put(
            db_session, "조선일보", "최인준", "010-4750-2723", tmp_path, "b", date(2026, 7, 1)
        )

        conflicts = [c for c in change_set.changes if c.change_type == CONFLICT]
        assert conflicts, "번호 충돌은 확인 대상으로 잡혀야 한다"
        assert "오로라" in (conflicts[0].reason or "")
        assert conflicts[0].auto_apply is False


class TestRebuild:
    """보관된 원본으로 데이터를 처음부터 다시 만든다."""

    def test_rebuild_reproduces_the_same_result(self, db_session, monkeypatch, tmp_path):
        from app.config import get_settings

        settings = get_settings()
        monkeypatch.setattr(settings, "upload_dir", tmp_path / "uploads")

        for name in ("sample_reporter_list.xlsx", "sample_desk_matrix.xlsx"):
            apply(db_session, load(db_session, name))

        before_people = db_session.scalar(select(func.count()).select_from(Person))
        before_seats = db_session.scalar(select(func.count()).select_from(Assignment))
        assert before_people > 0

        from tools.rebuild import collect_sources, reload_sources, wipe

        sources = collect_sources(None)
        assert len(sources) == 2, "보관본 두 개가 잡혀야 한다"
        assert [s[2] for s in sources] == sorted(s[2] for s in sources), "기준일 오름차순"

        wipe()
        db_session.expire_all()
        assert db_session.scalar(select(func.count()).select_from(Person)) == 0

        reload_sources(sources, approve_all=False)
        db_session.expire_all()

        assert db_session.scalar(select(func.count()).select_from(Person)) == before_people
        assert db_session.scalar(select(func.count()).select_from(Assignment)) == before_seats

    def test_wipe_keeps_accounts_and_outlets(self, db_session, monkeypatch, tmp_path):
        from app.config import get_settings
        from app.models import AppUser, Outlet
        from app.services.auth import hash_password

        monkeypatch.setattr(get_settings(), "upload_dir", tmp_path / "uploads")
        db_session.add(
            AppUser(username="keepme", display_name="유지", password_hash=hash_password("x" * 12))
        )
        db_session.commit()

        apply(db_session, load(db_session, "sample_reporter_list.xlsx"))

        from tools.rebuild import wipe

        wipe()
        db_session.expire_all()

        assert db_session.scalar(select(AppUser).where(AppUser.username == "keepme")) is not None
        assert db_session.scalar(select(func.count()).select_from(Outlet)) > 0

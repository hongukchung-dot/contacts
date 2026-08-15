"""화면에서 직접 고치고 지우는 기능.

파일이 항상 정확한 것은 아니다. 한 사람이 두 명으로 잡히거나(같은 이름이 두 칸에),
직책이 엉뚱하게 붙는 일이 실제로 생긴다. 그런 것을 사람이 바로잡을 수 있어야 한다.

두 가지 원칙을 지킨다.

1. **손으로 고친 자리는 잠근다.** 다음에 올라온 파일이 같은 자리를 조용히
   되돌리지 못하게 하고, 검토 화면으로 보낸다. 안 그러면 고쳐도 다음 업로드에 원복된다.
2. **'끝난 것'과 '잘못된 것'을 구분한다.**
   · 인사이동으로 자리를 떠난 것 → 이력으로 남긴다(valid_to 를 닫음)
   · 애초에 잘못 들어온 것    → 흔적 없이 지운다(이력에 남길 가치가 없음)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ingest.normalize import normalize_key, normalize_name, normalize_phone
from ..models import Assignment, AppUser, Outlet, OutletAlias, Person, PersonPhone


class EditError(Exception):
    """사용자에게 그대로 보여 줄 수 있는 오류."""


@dataclass
class AssignmentForm:
    name: str
    phone: str | None
    email: str | None
    memo: str | None
    kind: str
    role_slot: str | None
    role_label: str | None
    dept: str | None
    note: str | None
    concurrent: bool


def parse_form(data) -> AssignmentForm:
    """폼 입력을 검증해 구조체로 만든다."""

    def clean(key: str) -> str | None:
        value = (data.get(key) or "").strip()
        return value or None

    name = normalize_name(data.get("name") or "")
    if not name:
        raise EditError("이름을 입력해 주세요.")
    if len(name) > 20:
        raise EditError("이름이 너무 깁니다. 확인해 주세요.")

    phone_raw = clean("phone")
    phone = None
    if phone_raw:
        phone, warnings = normalize_phone(phone_raw)
        if phone is None:
            raise EditError(f"전화번호 형식이 올바르지 않습니다: {phone_raw}")
        del warnings

    email = clean("email")
    if email and ("@" not in email or " " in email or len(email) > 120):
        raise EditError(f"이메일 형식이 올바르지 않습니다: {email}")

    kind = (data.get("kind") or "").strip()
    if kind not in {"desk", "reporter"}:
        raise EditError("구분은 데스크 또는 출입기자여야 합니다.")

    return AssignmentForm(
        name=name,
        phone=phone,
        email=email.lower() if email else None,
        memo=clean("memo"),
        kind=kind,
        role_slot=clean("role_slot"),
        role_label=clean("role_label"),
        dept=clean("dept"),
        note=clean("note"),
        concurrent=bool(data.get("concurrent")),
    )


def _rank_from_label(label: str | None) -> tuple[str, int]:
    from ..ingest.reference import get_reference

    return get_reference().rank_of(label)


def _stamp(assignment: Assignment, user: AppUser | None) -> None:
    assignment.locked = True
    assignment.edited_at = datetime.now(timezone.utc)
    assignment.edited_by_id = user.id if user else None


# ── 자리 수정 ───────────────────────────────────────────────────────────────

def update_assignment(
    session: Session, assignment: Assignment, form: AssignmentForm, *, user: AppUser | None
) -> list[str]:
    """자리 하나를 고친다. 무엇이 바뀌었는지 사람이 읽을 목록으로 돌려준다."""
    changes: list[str] = []
    person = assignment.person

    if person.name != form.name:
        # 이 매체에서 이 사람 하나만 이름을 바꾸는 것이므로, 다른 자리에도 함께 반영된다.
        changes.append(f"이름 {person.name} → {form.name}")
        person.name = form.name
        person.name_key = normalize_key(form.name)

    if form.phone != person.phone:
        changes.append(f"번호 {person.phone or '(없음)'} → {form.phone or '(없음)'}")
        _set_phone(session, person, form.phone)

    if (form.email or None) != (person.email or None):
        changes.append(f"이메일 {person.email or '(없음)'} → {form.email or '(없음)'}")
        person.email = form.email

    if (form.memo or None) != (person.memo or None):
        changes.append("메모 수정")
        person.memo = form.memo

    for field, label in (
        ("kind", "구분"),
        ("role_slot", "직책 열"),
        ("role_label", "직책"),
        ("dept", "부서"),
        ("note", "담당"),
    ):
        before = getattr(assignment, field)
        after = getattr(form, field)
        if (before or None) != (after or None):
            changes.append(f"{label} {before or '(없음)'} → {after or '(없음)'}")
            setattr(assignment, field, after)

    if assignment.concurrent != form.concurrent:
        changes.append(f"겸직 {'해제' if not form.concurrent else '지정'}")
        assignment.concurrent = form.concurrent

    assignment.rank, assignment.rank_order = _rank_from_label(form.role_label)
    _stamp(assignment, user)
    return changes


def _set_phone(session: Session, person: Person, phone: str | None) -> None:
    today = date.today()
    records = list(
        session.scalars(select(PersonPhone).where(PersonPhone.person_id == person.id)).all()
    )
    for record in records:
        if record.valid_to is None and record.phone != phone:
            record.valid_to = today
    person.phone = phone
    if phone and not any(p.phone == phone and p.valid_to is None for p in records):
        session.add(PersonPhone(person_id=person.id, phone=phone, valid_from=today))


# ── 자리 추가 ───────────────────────────────────────────────────────────────

def create_assignment(
    session: Session, outlet: Outlet, form: AssignmentForm, *, user: AppUser | None
) -> Assignment:
    """명단에 없는 사람을 직접 넣는다. 같은 번호·이름이 있으면 그 사람에 붙인다."""
    name_key = normalize_key(form.name)
    # 인물 매칭은 적재 규칙과 같은 기준을 쓴다: 같은 매체+이름, 또는 이름+번호가 모두 일치.
    person = session.scalar(
        select(Person)
        .join(Assignment, Assignment.person_id == Person.id)
        .where(
            Person.name_key == name_key,
            Assignment.outlet_id == outlet.id,
            Assignment.valid_to.is_(None),
        )
        .limit(1)
    )
    if person is None and form.phone:
        person = session.scalar(
            select(Person).where(Person.phone == form.phone, Person.name_key == name_key)
        )
    if person is None:
        person = Person(
            name=form.name,
            name_key=normalize_key(form.name),
            phone=form.phone,
            email=form.email,
            memo=form.memo,
        )
        session.add(person)
        session.flush()
        if form.phone:
            session.add(
                PersonPhone(person_id=person.id, phone=form.phone, valid_from=date.today())
            )
    else:
        if form.phone and person.phone != form.phone:
            _set_phone(session, person, form.phone)
        if form.email:
            person.email = form.email
        if form.memo:
            person.memo = form.memo

    rank, rank_order = _rank_from_label(form.role_label)
    assignment = Assignment(
        person_id=person.id,
        outlet_id=outlet.id,
        kind=form.kind,
        role_slot=form.role_slot,
        role_label=form.role_label,
        dept=form.dept,
        rank=rank,
        rank_order=rank_order,
        note=form.note,
        concurrent=form.concurrent,
        valid_from=date.today(),
    )
    _stamp(assignment, user)
    session.add(assignment)
    session.flush()
    return assignment


# ── 자리 종료 / 삭제 ────────────────────────────────────────────────────────

def close_assignment(assignment: Assignment, *, user: AppUser | None) -> None:
    """인사이동 등으로 자리를 떠났다. 이력으로 남긴다."""
    if assignment.valid_to is None:
        assignment.valid_to = date.today()
    _stamp(assignment, user)


def purge_assignment(session: Session, assignment: Assignment) -> None:
    """애초에 잘못 들어온 자리. 화면에서 빼되 '오류 삭제' 기록은 남긴다.

    기록을 지우면 다음 업로드·재구축이 같은 데이터를 조용히 되살린다.
    실제 인사이동(퇴사·이동)은 close_assignment(마감)를 쓴다 — 그쪽은 정당한 이력이다.
    """
    if assignment.valid_to is None:
        assignment.valid_to = date.today()
    assignment.deleted_at = datetime.now(timezone.utc)
    _stamp(assignment, None)
    session.flush()


# ── 인물 합치기 ─────────────────────────────────────────────────────────────

def merge_candidates(session: Session, person: Person, limit: int = 20) -> list[Person]:
    """같은 사람일 가능성이 있는 인물 목록. 이름이 같거나 번호가 같은 경우."""
    from sqlalchemy.orm import joinedload

    stmt = select(Person).options(
        joinedload(Person.assignments).joinedload(Assignment.outlet)
    ).where(Person.id != person.id)
    conditions = [Person.name_key == person.name_key]
    if person.phone:
        conditions.append(Person.phone == person.phone)
    from sqlalchemy import or_

    return list(session.scalars(stmt.where(or_(*conditions)).limit(limit)).unique().all())


def merge_persons(
    session: Session, keep: Person, drop: Person, *, user: AppUser | None
) -> int:
    """`drop` 을 `keep` 으로 합친다. 옮긴 자리 수를 돌려준다.

    같은 자리(매체·구분·직책·부서)가 양쪽에 있으면 중복이므로 하나만 남긴다.
    """
    if keep.id == drop.id:
        raise EditError("같은 인물끼리는 합칠 수 없습니다.")

    # 관계 캐시(person.assignments)는 세션 상태에 따라 낡아 있을 수 있다.
    # 여기서 놓치면 옮겨야 할 자리가 그대로 삭제되므로 반드시 직접 조회한다.
    keep_seats = list(
        session.scalars(select(Assignment).where(Assignment.person_id == keep.id)).all()
    )
    drop_seats = list(
        session.scalars(select(Assignment).where(Assignment.person_id == drop.id)).all()
    )
    keep_phones = list(
        session.scalars(select(PersonPhone).where(PersonPhone.person_id == keep.id)).all()
    )
    drop_phones = list(
        session.scalars(select(PersonPhone).where(PersonPhone.person_id == drop.id)).all()
    )

    def seat(assignment: Assignment) -> tuple:
        return (
            assignment.outlet_id,
            assignment.kind,
            assignment.role_slot or "",
            assignment.dept or "",
            assignment.valid_to,
        )

    existing = {seat(a) for a in keep_seats}
    moved = 0
    for assignment in drop_seats:
        if seat(assignment) in existing:
            session.delete(assignment)  # 완전히 겹치는 중복
            continue
        assignment.person_id = keep.id
        _stamp(assignment, user)
        existing.add(seat(assignment))
        moved += 1

    keep_numbers = {p.phone for p in keep_phones}
    for record in drop_phones:
        if record.phone in keep_numbers:
            session.delete(record)
        else:
            record.person_id = keep.id
            keep_numbers.add(record.phone)

    if not keep.phone and drop.phone:
        keep.phone = drop.phone

    session.flush()
    session.delete(drop)
    session.flush()
    return moved


# ── 매체 합치기 ─────────────────────────────────────────────────────────────

def merge_outlets(session: Session, keep: Outlet, drop: Outlet) -> dict:
    """`drop` 매체를 `keep` 으로 합친다.

    같은 매체가 두 표기로 갈라져 들어온 경우에 쓴다
    (`헤럴드` / `헤럴드경제`, `시사저널` / `시사저널e` 처럼).

    자리를 옮긴 뒤, 같은 매체 안에 같은 이름이 둘이 되면 인물도 합친다.
    적재 규칙과 같은 기준이다 — 같은 매체에 같은 이름이면 같은 사람.
    """
    if keep.id == drop.id:
        raise EditError("같은 매체끼리는 합칠 수 없습니다.")

    moved = session.query(Assignment).filter(Assignment.outlet_id == drop.id).count()
    session.query(Assignment).filter(Assignment.outlet_id == drop.id).update(
        {Assignment.outlet_id: keep.id}, synchronize_session=False
    )

    # 별칭도 함께 옮겨 다음 업로드부터 바로 잡히게 한다.
    for alias in list(session.scalars(
        select(OutletAlias).where(OutletAlias.outlet_id == drop.id)
    ).all()):
        exists = session.scalar(
            select(OutletAlias).where(
                OutletAlias.alias_key == alias.alias_key, OutletAlias.outlet_id != drop.id
            )
        )
        if exists:
            session.delete(alias)
        else:
            alias.outlet_id = keep.id

    drop_key = normalize_key(drop.name)
    if drop_key and not session.scalar(
        select(OutletAlias).where(OutletAlias.alias_key == drop_key)
    ):
        session.add(OutletAlias(outlet_id=keep.id, alias=drop.name, alias_key=drop_key))

    session.flush()
    session.delete(drop)
    session.flush()

    merged_people = dedupe_people_in_outlet(session, keep)
    return {"moved": moved, "merged_people": merged_people}


def dedupe_people_in_outlet(session: Session, outlet: Outlet) -> int:
    """한 매체 안에 같은 이름이 여러 인물로 갈라져 있으면 합친다."""
    rows = session.execute(
        select(Person.name_key, Person.id)
        .join(Assignment, Assignment.person_id == Person.id)
        .where(Assignment.outlet_id == outlet.id, Assignment.valid_to.is_(None))
        .distinct()
    ).all()

    by_name: dict[str, list[int]] = {}
    for name_key, person_id in rows:
        by_name.setdefault(name_key, []).append(person_id)

    merged = 0
    for person_ids in by_name.values():
        if len(person_ids) < 2:
            continue
        people = [session.get(Person, pid) for pid in person_ids]
        people = [p for p in people if p is not None]
        # 번호가 있는 쪽을 남긴다. 둘 다 있으면 먼저 등록된 쪽.
        people.sort(key=lambda p: (p.phone is None, p.id))
        keep_person, *others = people
        for other in others:
            merge_persons(session, keep_person, other, user=None)
            merged += 1
    return merged


# ── 데스크·출입기자 중복 정리 ───────────────────────────────────────────────

@dataclass
class RoleOverlap:
    """같은 매체에 데스크와 출입기자로 겹쳐 등록된 사람의 출입기자 자리."""

    assignment_id: int
    outlet_name: str
    person_name: str
    role_label: str | None
    locked: bool


def find_desk_reporter_overlaps(session: Session) -> list[RoleOverlap]:
    """이름·전화번호·매체가 같은데 데스크와 출입기자로 모두 등록된 경우를 찾는다.

    같은 인물(person_id)이 두 자리를 쥔 경우가 대부분이고,
    인물이 둘로 갈라졌지만 이름·번호가 같은 경우도 잡는다.
    돌려주는 것은 지워야 할 **출입기자 쪽** 자리 목록이다.
    """
    rows = session.execute(
        select(Assignment, Person, Outlet)
        .join(Person, Assignment.person_id == Person.id)
        .join(Outlet, Assignment.outlet_id == Outlet.id)
        .where(Assignment.valid_to.is_(None))
    ).all()

    desk_by_person: set[tuple[int, int]] = set()
    desk_by_name_phone: set[tuple[int, str, str]] = set()
    for assignment, person, _outlet in rows:
        if assignment.kind == "desk":
            desk_by_person.add((assignment.outlet_id, person.id))
            if person.phone:
                desk_by_name_phone.add((assignment.outlet_id, person.name_key, person.phone))

    overlaps: list[RoleOverlap] = []
    for assignment, person, outlet in rows:
        if assignment.kind != "reporter":
            continue
        same_person = (assignment.outlet_id, person.id) in desk_by_person
        same_name_phone = bool(person.phone) and (
            (assignment.outlet_id, person.name_key, person.phone) in desk_by_name_phone
        )
        if same_person or same_name_phone:
            overlaps.append(
                RoleOverlap(
                    assignment_id=assignment.id,
                    outlet_name=outlet.name,
                    person_name=person.name,
                    role_label=assignment.role_label,
                    locked=assignment.locked,
                )
            )
    return overlaps


def remove_desk_reporter_overlaps(session: Session) -> tuple[int, int]:
    """겹치는 출입기자 자리를 지운다. 데스크 자리는 남긴다.

    손으로 고친(locked) 자리는 건드리지 않는다. (지운 수, 건너뛴 수)를 돌려준다.
    """
    removed = skipped = 0
    for overlap in find_desk_reporter_overlaps(session):
        if overlap.locked:
            skipped += 1
            continue
        assignment = session.get(Assignment, overlap.assignment_id)
        if assignment is None:
            continue
        purge_assignment(session, assignment)
        removed += 1
    return removed, skipped

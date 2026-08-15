"""화면에 뿌릴 데이터를 만드는 조회 계층."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from ..config import get_settings
from ..ingest.normalize import format_phone, role_display
from ..ingest.reference import get_reference
from ..models import (
    APPLIED,
    Assignment,
    Change,
    ChangeSet,
    Outlet,
    Person,
    SourceFile,
)

CATEGORY_ORDER = ["종합지", "경제지", "방송", "통신사", "전문지", "영자지", "기타"]


@dataclass
class Entry:
    assignment_id: int
    person_id: int
    name: str
    phone: str | None
    role_label: str | None
    role_slot: str | None
    dept: str | None
    rank: str
    note: str | None
    concurrent: bool
    since: date
    is_recent: bool
    locked: bool = False
    kind: str = "reporter"
    email: str | None = None
    memo: str | None = None

    @property
    def role_text(self) -> str:
        return role_display(self.role_label, self.role_slot, self.dept)

    @property
    def phone_display(self) -> str:
        return format_phone(self.phone)

    @property
    def phone_masked(self) -> str:
        if not self.phone:
            return "—"
        formatted = format_phone(self.phone)
        head, mid, tail = formatted.split("-")
        return f"{head}-{'●' * len(mid)}-{tail}"


@dataclass
class OutletRow:
    id: int
    name: str
    category: str
    sort_order: int = 0
    cells: dict[str, list[Entry]] = field(default_factory=dict)
    reporter_count: int = 0
    recent_change_count: int = 0


def _recent_cutoff() -> date:
    return date.today() - timedelta(days=get_settings().highlight_days)


def _seed_file_ids(session: Session) -> frozenset[int]:
    """비어 있던 DB를 처음 채운 적재의 파일 id 집합."""
    rows = session.scalars(
        select(ChangeSet.source_file_id).where(
            ChangeSet.status == APPLIED, ChangeSet.is_initial.is_(True)
        )
    ).all()
    return frozenset(rows)


def matrix(session: Session, *, category: str | None = None) -> tuple[list[str], list[tuple[str, list[OutletRow]]]]:
    """메인 화면용 (열 목록, [(분류, 매체행 목록)])."""
    slots = get_reference().slot_codes
    cutoff = _recent_cutoff()
    seed_files = _seed_file_ids(session)

    stmt = (
        select(Assignment)
        .options(joinedload(Assignment.person), joinedload(Assignment.outlet))
        .join(Outlet)
        .where(Assignment.valid_to.is_(None), Assignment.kind == "desk", Outlet.active.is_(True))
        # sort_order 가 같은 매체(기타 분류는 전부 9000)는 매체명 가나다순.
        .order_by(Outlet.sort_order, Outlet.name, Assignment.rank_order, Assignment.id)
    )
    if category:
        stmt = stmt.where(Outlet.category == category)

    rows: dict[int, OutletRow] = {}
    for assignment in session.scalars(stmt).unique().all():
        outlet = assignment.outlet
        row = rows.get(outlet.id)
        if row is None:
            row = OutletRow(
                id=outlet.id, name=outlet.name, category=outlet.category,
                sort_order=outlet.sort_order,
            )
            rows[outlet.id] = row
        slot = assignment.role_slot or "기타"
        entry = _entry(assignment, cutoff, seed_files)
        row.cells.setdefault(slot, []).append(entry)
        if entry.is_recent:
            row.recent_change_count += 1

    # 데스크가 한 명도 없는 매체(기타 분류에 흔하다)도 출입기자가 있으면 행으로 보여 준다.
    # 빈 행이 아니라 매체명 클릭 → 출입기자 팝업의 입구가 된다.
    reporter_only_stmt = (
        select(Outlet)
        .join(Assignment, Assignment.outlet_id == Outlet.id)
        .where(
            Assignment.valid_to.is_(None),
            Assignment.kind == "reporter",
            Outlet.active.is_(True),
        )
        .distinct()
    )
    if category:
        reporter_only_stmt = reporter_only_stmt.where(Outlet.category == category)
    for outlet in session.scalars(reporter_only_stmt).all():
        if outlet.id not in rows:
            rows[outlet.id] = OutletRow(
                id=outlet.id, name=outlet.name, category=outlet.category,
                sort_order=outlet.sort_order,
            )

    _attach_reporter_counts(session, rows)

    grouped: dict[str, list[OutletRow]] = {}
    for row in sorted(rows.values(), key=lambda r: (r.sort_order, r.name)):
        grouped.setdefault(row.category, []).append(row)

    ordered: list[tuple[str, list[OutletRow]]] = []
    for name in CATEGORY_ORDER:
        if name in grouped:
            ordered.append((name, grouped.pop(name)))
    for name in sorted(grouped):
        ordered.append((name, grouped[name]))

    return slots, ordered


def _entry(assignment: Assignment, cutoff: date, seed_files: frozenset[int] = frozenset()) -> Entry:
    # 초기 적재분은 '전부 신규'이므로 변동 음영을 넣지 않는다.
    recent = assignment.valid_from >= cutoff and assignment.source_file_id not in seed_files
    return Entry(
        assignment_id=assignment.id,
        person_id=assignment.person_id,
        name=assignment.person.name,
        phone=assignment.person.phone,
        role_label=assignment.role_label,
        role_slot=assignment.role_slot,
        dept=assignment.dept,
        rank=assignment.rank,
        note=assignment.note,
        concurrent=assignment.concurrent,
        since=assignment.valid_from,
        is_recent=recent,
        locked=assignment.locked,
        kind=assignment.kind,
        email=assignment.person.email,
        memo=assignment.person.memo,
    )


def _attach_reporter_counts(session: Session, rows: dict[int, OutletRow]) -> None:
    if not rows:
        return
    counts = session.execute(
        select(Assignment.outlet_id, func.count(Assignment.id))
        .where(
            Assignment.valid_to.is_(None),
            Assignment.kind == "reporter",
            Assignment.outlet_id.in_(rows.keys()),
        )
        .group_by(Assignment.outlet_id)
    ).all()
    for outlet_id, count in counts:
        rows[outlet_id].reporter_count = count


def outlet_detail(session: Session, outlet_id: int) -> dict | None:
    """매체명 클릭 시 뜨는 팝업 내용."""
    outlet = session.get(Outlet, outlet_id)
    if outlet is None:
        return None

    cutoff = _recent_cutoff()
    seed_files = _seed_file_ids(session)
    assignments = session.scalars(
        select(Assignment)
        .options(joinedload(Assignment.person))
        .where(Assignment.outlet_id == outlet_id, Assignment.valid_to.is_(None))
        .order_by(Assignment.rank_order, Assignment.id)
    ).unique().all()

    slot_rank = {code: idx for idx, code in enumerate(get_reference().slot_codes)}
    desks = sorted(
        (a for a in assignments if a.kind == "desk"),
        key=lambda a: (slot_rank.get(a.role_slot or "", 99), a.rank_order, a.id),
    )
    desks = [_entry(a, cutoff, seed_files) for a in desks]

    reporters_by_dept: dict[str, list[Entry]] = {}
    for assignment in assignments:
        if assignment.kind != "reporter":
            continue
        reporters_by_dept.setdefault(assignment.dept or "기타", []).append(
            _entry(assignment, cutoff, seed_files)
        )

    source = session.scalar(
        select(SourceFile)
        .join(ChangeSet, ChangeSet.source_file_id == SourceFile.id)
        .where(ChangeSet.status == APPLIED)
        .order_by(SourceFile.as_of.desc())
        .limit(1)
    )

    return {
        "outlet": outlet,
        "desks": desks,
        "reporters_by_dept": dict(sorted(reporters_by_dept.items())),
        "reporter_total": sum(len(v) for v in reporters_by_dept.values()),
        "source": source,
    }


def person_detail(session: Session, person_id: int) -> dict | None:
    person = session.get(Person, person_id)
    if person is None:
        return None
    history = session.scalars(
        select(Assignment)
        .options(joinedload(Assignment.outlet))
        .where(Assignment.person_id == person_id)
        .order_by(Assignment.valid_from.desc(), Assignment.id.desc())
    ).unique().all()
    return {
        "person": person,
        "current": [a for a in history if a.valid_to is None],
        "past": [a for a in history if a.valid_to is not None],
        "phones": sorted(person.phones, key=lambda p: p.valid_from, reverse=True),
    }


def recent_changes(session: Session, limit: int = 200) -> list[Change]:
    return list(
        session.scalars(
            select(Change)
            .join(ChangeSet)
            .options(joinedload(Change.change_set).joinedload(ChangeSet.source_file))
            .where(Change.applied.is_(True))
            .order_by(Change.id.desc())
            .limit(limit)
        ).unique().all()
    )


def stats(session: Session) -> dict:
    current = select(Assignment).where(Assignment.valid_to.is_(None)).subquery()
    return {
        "outlets": session.scalar(
            select(func.count(func.distinct(current.c.outlet_id))).select_from(current)
        )
        or 0,
        "desks": session.scalar(
            select(func.count()).select_from(current).where(current.c.kind == "desk")
        )
        or 0,
        "reporters": session.scalar(
            select(func.count()).select_from(current).where(current.c.kind == "reporter")
        )
        or 0,
        "people": session.scalar(select(func.count()).select_from(Person)) or 0,
        "last_file": session.scalar(
            select(SourceFile)
            .join(ChangeSet, ChangeSet.source_file_id == SourceFile.id)
            .where(ChangeSet.status == APPLIED)
            .order_by(SourceFile.as_of.desc())
            .limit(1)
        ),
    }


def outlet_roster(session: Session, outlet_id: int) -> list[Entry]:
    """직접 입력 화면용 — 한 매체의 현재 명단 전부 (데스크 + 출입기자)."""
    cutoff = _recent_cutoff()
    seed_files = _seed_file_ids(session)
    slot_rank = {code: idx for idx, code in enumerate(get_reference().slot_codes)}

    assignments = session.scalars(
        select(Assignment)
        .options(joinedload(Assignment.person))
        .where(Assignment.outlet_id == outlet_id, Assignment.valid_to.is_(None))
    ).unique().all()

    assignments = sorted(
        assignments,
        key=lambda a: (
            0 if a.kind == "desk" else 1,
            slot_rank.get(a.role_slot or "", 99),
            a.rank_order,
            a.id,
        ),
    )
    return [_entry(a, cutoff, seed_files) for a in assignments]


def outlets_by_category(session: Session) -> list[tuple[str, list[Outlet]]]:
    """매체 선택 드롭다운용 — 분류별로 묶은 매체 목록."""
    outlets = session.scalars(
        select(Outlet).where(Outlet.active.is_(True)).order_by(Outlet.sort_order, Outlet.name)
    ).all()
    grouped: dict[str, list[Outlet]] = {}
    for outlet in outlets:
        grouped.setdefault(outlet.category, []).append(outlet)

    ordered: list[tuple[str, list[Outlet]]] = []
    for name in CATEGORY_ORDER:
        if name in grouped:
            ordered.append((name, grouped.pop(name)))
    for name in sorted(grouped):
        ordered.append((name, grouped[name]))
    return ordered

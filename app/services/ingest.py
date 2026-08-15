"""업로드 → 파싱 → 현재 상태와 비교 → 승인 → 반영.

흐름
----
1. `stage_file`   파일을 저장하고 파싱해 `source_file` / `source_record` 에 원문째 남긴다.
                  이어서 현재 DB와 비교해 `change_set` / `change` 를 만든다. **DB 본체는 아직 안 바뀐다.**
2. (검토 화면)    사용자가 항목별로 승인/반려한다. 신규·단순변경은 처음부터 승인 상태.
3. `apply_change_set`  승인된 항목만 실제 반영한다. 기존 자리는 지우지 않고 `valid_to`만 닫는다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..ingest.normalize import format_phone, normalize_key
from ..ingest.parsers import detect_and_parse
from ..ingest.records import ContactRecord, ParseResult
from ..ingest.reference import get_reference
from ..models import (
    APPLIED,
    APPROVED,
    CONFLICT,
    NEW,
    PENDING,
    REJECTED,
    REMOVE,
    UPDATE,
    Assignment,
    Change,
    ChangeSet,
    Outlet,
    OutletAlias,
    Person,
    PersonPhone,
    SourceFile,
    SourceRecord,
)
from .matching import is_typo_suspect, match_person


class DuplicateFileError(Exception):
    """내용이 동일한 파일이 이미 반영되어 있음."""

    def __init__(self, existing: SourceFile) -> None:
        self.existing = existing
        super().__init__(
            f"같은 내용의 파일이 이미 등록되어 있습니다: "
            f"{existing.filename} ({existing.uploaded_at:%Y-%m-%d %H:%M})"
        )


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ── 1단계: 파일 등록 + 비교 ─────────────────────────────────────────────────

def stage_file(
    session: Session,
    path: Path,
    *,
    filename: str,
    uploaded_by_id: int | None = None,
    as_of_override: date | None = None,
) -> ChangeSet:
    checksum = sha256_of(path)
    existing = session.scalar(select(SourceFile).where(SourceFile.sha256 == checksum))
    if existing:
        raise DuplicateFileError(existing)

    result: ParseResult = detect_and_parse(path)
    as_of = as_of_override or result.as_of
    if as_of is None:
        raise ValueError("파일에서 기준일을 찾지 못했습니다. 업로드 시 기준일을 지정해 주세요.")

    stored = _store_upload(path, filename, checksum)

    source_file = SourceFile(
        filename=filename,
        file_kind=result.file_kind,
        as_of=as_of,
        sha256=checksum,
        stored_path=str(stored) if stored else None,
        record_count=len(result.records),
        parse_warnings=result.warnings or None,
        unparsed=result.unparsed or None,
        uploaded_by_id=uploaded_by_id,
    )
    session.add(source_file)
    session.flush()

    records = _dedupe(result.records)
    for record in records:
        session.add(
            SourceRecord(
                source_file_id=source_file.id,
                outlet_raw=record.outlet_raw[:80],
                outlet_name=record.outlet[:80],
                name=record.name[:40],
                phone=record.phone,
                kind=record.kind,
                role_slot=record.role_slot,
                role_label=(record.role_label or None) and record.role_label[:120],
                dept=record.dept,
                rank=record.rank,
                rank_order=record.rank_order,
                note=(record.note or None) and record.note[:120],
                concurrent=record.concurrent,
                source_ref=record.source_ref[:80],
                source_text=record.source_text,
                warnings=record.warnings or None,
            )
        )

    change_set = ChangeSet(source_file_id=source_file.id, status=PENDING)
    session.add(change_set)
    session.flush()

    _build_changes(session, change_set, source_file, records)
    session.flush()
    return change_set


def _store_upload(path: Path, filename: str, checksum: str) -> Path | None:
    """원본 파일을 보관한다. 파싱 규칙을 고쳐 재처리할 수 있어야 하기 때문."""
    settings = get_settings()
    try:
        target_dir = settings.upload_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"{checksum[:12]}_{Path(filename).name}"
        target.write_bytes(path.read_bytes())
        return target
    except OSError:
        return None


def _dedupe(records: list[ContactRecord]) -> list[ContactRecord]:
    seen: set[tuple] = set()
    out: list[ContactRecord] = []
    for record in records:
        key = record.identity()
        if key in seen:
            continue
        seen.add(key)
        out.append(record)
    return out


# ── 비교 로직 ───────────────────────────────────────────────────────────────

def _build_changes(
    session: Session,
    change_set: ChangeSet,
    source_file: SourceFile,
    records: list[ContactRecord],
) -> None:
    as_of = source_file.as_of
    stale = _is_stale(session, source_file, records)

    outlets = _ensure_outlet_rows(session, records)
    current = _load_current(session)

    covered_desk_slots: dict[int, set[str | None]] = {}
    covered_reporter_outlets: set[int] = set()
    matched_assignment_ids: set[int] = set()
    # 이 파일에 (그 매체 소속으로) 이름이 보인 사람들.
    # 부서·직책·구분이 바뀌었을 뿐인데 삭제로 잡히면 안 되므로 별도로 모은다.
    seen_person_outlet: set[tuple[int, int]] = set()

    for record in records:
        outlet = outlets[record.outlet]
        if record.kind == "desk":
            covered_desk_slots.setdefault(outlet.id, set()).add(record.role_slot)
        else:
            covered_reporter_outlets.add(outlet.id)

        person_id, basis = match_person(
            name_key=normalize_key(record.name),
            phone=record.phone,
            outlet_id=outlet.id,
            by_phone=current["by_phone"],
            by_outlet_name=current["by_outlet_name"],
            by_name=current["by_name"],
        )

        payload = _payload(record, outlet)

        if person_id is None:
            _add(
                change_set,
                change_type=NEW,
                outlet_name=outlet.name,
                person_name=record.name,
                field="assignment",
                old_value=None,
                new_value=_describe(record),
                reason="현재 명단에 없는 인물",
                auto_apply=not stale,
                outlet_id=outlet.id,
                payload=payload,
            )
            continue

        payload["person_id"] = person_id
        seen_person_outlet.add((person_id, outlet.id))
        person = current["persons"][person_id]

        # (1) 번호 변경
        if record.phone and person.phone and record.phone != person.phone:
            suspect, reason = is_typo_suspect(person.phone, record.phone)
            _add(
                change_set,
                change_type=CONFLICT if suspect else UPDATE,
                outlet_name=outlet.name,
                person_name=record.name,
                field="phone",
                old_value=format_phone(person.phone),
                new_value=format_phone(record.phone),
                reason=reason or f"{source_file.as_of} 파일 기준 번호 변경",
                auto_apply=(not suspect) and (not stale),
                person_id=person_id,
                outlet_id=outlet.id,
                payload=payload,
            )
        elif record.phone and not person.phone:
            _add(
                change_set,
                change_type=UPDATE,
                outlet_name=outlet.name,
                person_name=record.name,
                field="phone",
                old_value=None,
                new_value=format_phone(record.phone),
                reason="비어 있던 번호가 채워짐",
                auto_apply=not stale,
                person_id=person_id,
                outlet_id=outlet.id,
                payload=payload,
            )

        # (2) 자리(매체/부서/직책) 변경
        assignment = _find_assignment(current, person_id, outlet.id, record)
        if assignment is None:
            moved = current["assignments_by_person"].get(person_id) or []
            other = [a for a in moved if a.kind == record.kind and a.outlet_id != outlet.id]
            reason = (
                f"{other[0].outlet.name} → {outlet.name} 이동"
                if other
                else "새 직책/부서"
            )
            _add(
                change_set,
                change_type=NEW if not other else UPDATE,
                outlet_name=outlet.name,
                person_name=record.name,
                field="assignment",
                old_value=_describe_assignment(other[0]) if other else None,
                new_value=_describe(record),
                reason=reason,
                auto_apply=not stale,
                person_id=person_id,
                outlet_id=outlet.id,
                payload=payload,
            )
        else:
            matched_assignment_ids.add(assignment.id)
            if (assignment.role_label or "") != (record.role_label or ""):
                _add(
                    change_set,
                    change_type=UPDATE,
                    outlet_name=outlet.name,
                    person_name=record.name,
                    field="role",
                    old_value=assignment.role_label,
                    new_value=record.role_label,
                    reason="직책 표기 변경",
                    auto_apply=not stale,
                    person_id=person_id,
                    outlet_id=outlet.id,
                    payload={**payload, "assignment_id": assignment.id},
                )

    _detect_removals(
        change_set,
        current,
        covered_desk_slots,
        covered_reporter_outlets,
        matched_assignment_ids,
        seen_person_outlet,
        as_of,
        source_file.file_kind,
    )

    if stale:
        for change in change_set.changes:
            change.auto_apply = False
            change.decision = PENDING


def _is_stale(session: Session, source_file: SourceFile, records: list[ContactRecord]) -> bool:
    """이미 반영된 파일보다 기준일이 오래된 파일인가.

    오래된 파일을 그대로 반영하면 최신 정보가 과거 값으로 되돌아간다.
    이 경우 자동 반영을 모두 끄고 전부 검토 대상으로 돌린다.
    """
    kinds = {record.kind for record in records}
    latest = session.scalar(
        select(SourceFile.as_of)
        .join(ChangeSet, ChangeSet.source_file_id == SourceFile.id)
        .join(SourceRecord, SourceRecord.source_file_id == SourceFile.id)
        .where(ChangeSet.status == APPLIED, SourceRecord.kind.in_(kinds))
        .order_by(SourceFile.as_of.desc())
        .limit(1)
    )
    return bool(latest and source_file.as_of < latest)


def _ensure_outlet_rows(session: Session, records: list[ContactRecord]) -> dict[str, Outlet]:
    """파일에 나온 매체를 확보한다. 사전에 없으면 '미분류'로 새로 만든다."""
    reference = get_reference()
    outlets: dict[str, Outlet] = {}
    for record in records:
        if record.outlet in outlets:
            continue
        outlet = session.scalar(select(Outlet).where(Outlet.name == record.outlet))
        if outlet is None:
            match = reference.match_outlet(record.outlet)
            outlet = Outlet(
                name=record.outlet,
                name_key=normalize_key(record.outlet),
                category=match.category if match else record.category,
                sort_order=match.order if match else 9000,
            )
            session.add(outlet)
            session.flush()
            alias_key = normalize_key(record.outlet_raw)
            if alias_key and not session.scalar(
                select(OutletAlias).where(OutletAlias.alias_key == alias_key)
            ):
                session.add(
                    OutletAlias(outlet_id=outlet.id, alias=record.outlet_raw, alias_key=alias_key)
                )
        outlets[record.outlet] = outlet
    return outlets


def _load_current(session: Session) -> dict:
    persons = {p.id: p for p in session.scalars(select(Person)).all()}
    assignments = session.scalars(
        select(Assignment).where(Assignment.valid_to.is_(None))
    ).all()

    by_phone: dict[str, int] = {}
    by_name: dict[str, list[int]] = {}
    for person in persons.values():
        if person.phone:
            by_phone.setdefault(person.phone, person.id)
        by_name.setdefault(person.name_key, []).append(person.id)

    by_outlet_name: dict[tuple[int, str], int] = {}
    assignments_by_person: dict[int, list[Assignment]] = {}
    for assignment in assignments:
        person = persons.get(assignment.person_id)
        if person:
            by_outlet_name.setdefault((assignment.outlet_id, person.name_key), person.id)
        assignments_by_person.setdefault(assignment.person_id, []).append(assignment)

    file_kind_by_id = dict(
        session.execute(select(SourceFile.id, SourceFile.file_kind)).all()
    )

    return {
        "persons": persons,
        "assignments": assignments,
        "file_kind_by_id": file_kind_by_id,
        "assignments_by_person": assignments_by_person,
        "by_phone": by_phone,
        "by_name": by_name,
        "by_outlet_name": by_outlet_name,
    }


def _find_assignment(current: dict, person_id: int, outlet_id: int, record: ContactRecord):
    for assignment in current["assignments_by_person"].get(person_id, []):
        if (
            assignment.outlet_id == outlet_id
            and assignment.kind == record.kind
            and (assignment.role_slot or None) == (record.role_slot or None)
            and (assignment.dept or None) == (record.dept or None)
        ):
            return assignment
    return None


def _detect_removals(
    change_set: ChangeSet,
    current: dict,
    covered_desk_slots: dict[int, set[str | None]],
    covered_reporter_outlets: set[int],
    matched_assignment_ids: set[int],
    seen_person_outlet: set[tuple[int, int]],
    as_of: date,
    file_kind: str,
) -> None:
    """파일에서 사라진 사람을 찾는다.

    두 가지를 지킨다.

    1. 파일마다 다루는 범위가 다르므로(예: 7/31 파일에는 '대표' 열이 없다)
       **그 파일이 실제로 담고 있는 (매체, 구분, 직책) 범위 안에서만** 삭제로 본다.
    2. **그 매체에 이름이 보인 사람은 삭제로 보지 않는다.** 출입기자였다가 데스크로
       올라가거나 부서가 바뀐 것뿐인데 삭제로 잡히면 안 되기 때문이다.
       그런 이동은 이미 '변경'으로 따로 기록된다.
    3. **같은 종류의 파일이 넣은 값만 지울 수 있다.** 출입기자 xlsx 와 통합 docx 는
       둘 다 출입기자를 담지만 수록 범위가 다르다(docx는 산업·테크 중심).
       서로가 서로의 인원을 지우게 두면 업로드할 때마다 가짜 삭제가 쏟아진다.
       각 파일은 자기가 넣은 명단만 관리한다.
    """
    for assignment in current["assignments"]:
        if assignment.id in matched_assignment_ids:
            continue
        if (assignment.person_id, assignment.outlet_id) in seen_person_outlet:
            continue
        origin = current["file_kind_by_id"].get(assignment.source_file_id)
        if origin is not None and origin != file_kind:
            continue
        in_scope = False
        if assignment.kind == "desk":
            slots = covered_desk_slots.get(assignment.outlet_id)
            in_scope = bool(slots) and assignment.role_slot in slots
        else:
            in_scope = assignment.outlet_id in covered_reporter_outlets
        if not in_scope:
            continue

        person = current["persons"].get(assignment.person_id)
        if person is None:
            continue
        _add(
            change_set,
            change_type=REMOVE,
            outlet_name=assignment.outlet.name,
            person_name=person.name,
            field="assignment",
            old_value=_describe_assignment(assignment),
            new_value=None,
            reason=f"{as_of} 파일 해당 범위에서 빠짐 (퇴사·인사이동 추정)",
            auto_apply=False,  # 삭제는 항상 사람이 확인한다
            person_id=person.id,
            outlet_id=assignment.outlet_id,
            payload={"assignment_id": assignment.id},
        )


def _add(change_set: ChangeSet, **kwargs) -> None:
    auto = kwargs.pop("auto_apply", False)
    change = Change(
        change_set_id=change_set.id,
        auto_apply=auto,
        decision=APPROVED if auto else PENDING,
        **kwargs,
    )
    change_set.changes.append(change)


def _payload(record: ContactRecord, outlet: Outlet) -> dict:
    data = asdict(record)
    data["outlet_id"] = outlet.id
    data.pop("warnings", None)
    return json.loads(json.dumps(data, ensure_ascii=False, default=str))


def _describe(record: ContactRecord) -> str:
    bits = [record.role_label or record.role_slot or ("출입기자" if record.kind == "reporter" else "데스크")]
    if record.dept:
        bits.insert(0, record.dept)
    if record.phone:
        bits.append(format_phone(record.phone))
    return " · ".join(bits)


def _describe_assignment(assignment: Assignment) -> str:
    default = "출입기자" if assignment.kind == "reporter" else "데스크"
    bits = [assignment.role_label or assignment.role_slot or default]
    if assignment.dept:
        bits.insert(0, assignment.dept)
    return " · ".join(bits)


# ── 3단계: 승인된 변경 반영 ─────────────────────────────────────────────────

def apply_change_set(session: Session, change_set: ChangeSet, *, applied_by_id: int | None) -> dict:
    from datetime import datetime, timezone

    as_of = change_set.source_file.as_of
    counts = {NEW: 0, UPDATE: 0, REMOVE: 0, CONFLICT: 0, "skipped": 0}

    change_set.is_initial = _is_initial_load(session)

    for change in change_set.changes:
        if change.applied:
            continue
        if change.decision != APPROVED:
            counts["skipped"] += 1
            continue

        if change.change_type == REMOVE:
            _apply_removal(session, change, as_of)
        elif change.field == "phone":
            _apply_phone(session, change, as_of, change_set.source_file_id)
        elif change.field == "role":
            _apply_role(session, change, as_of, change_set.source_file_id)
        else:
            _apply_assignment(session, change, as_of, change_set.source_file_id)

        change.applied = True
        counts[change.change_type] = counts.get(change.change_type, 0) + 1

    change_set.status = APPLIED
    change_set.applied_at = datetime.now(timezone.utc)
    change_set.applied_by_id = applied_by_id
    session.flush()
    return counts


def _is_initial_load(session: Session) -> bool:
    """이 반영이 '처음 자리 잡는 적재'인가.

    설치할 때는 4개 파일을 연달아 올리게 되는데, 그 전부가 '신규'라서
    변동 음영을 넣으면 표 전체가 노랗게 된다. 그래서
      · DB가 비어 있거나
      · 첫 반영과 같은 날에 이뤄진 반영
    은 초기 적재로 보고 음영에서 제외한다. 그 이후의 갱신부터 음영이 붙는다.
    """
    if session.scalar(select(Assignment.id).limit(1)) is None:
        return True
    first_applied = session.scalar(
        select(ChangeSet.applied_at)
        .where(ChangeSet.status == APPLIED, ChangeSet.applied_at.is_not(None))
        .order_by(ChangeSet.applied_at)
        .limit(1)
    )
    if first_applied is None:
        return True
    from datetime import datetime, timezone

    return first_applied.date() == datetime.now(timezone.utc).date()


def _get_or_create_person(session: Session, change: Change) -> Person:
    payload = change.payload or {}
    person_id = change.person_id or payload.get("person_id")
    if person_id:
        person = session.get(Person, person_id)
        if person:
            return person

    # 한 파일 안에서 같은 사람이 여러 칸에 나오면(예: 증권부장 + 사회부장 겸임)
    # 모두 '신규'로 잡히므로, 만들기 전에 이미 만들어졌는지 다시 확인한다.
    name_key = normalize_key(change.person_name)
    phone = payload.get("phone")
    if phone:
        existing = session.scalar(select(Person).where(Person.phone == phone))
        if existing:
            change.person_id = existing.id
            return existing
    existing = session.scalar(select(Person).where(Person.name_key == name_key))
    if existing:
        change.person_id = existing.id
        return existing

    person = Person(
        name=change.person_name,
        name_key=name_key,
        phone=phone,
    )
    session.add(person)
    session.flush()
    change.person_id = person.id
    return person


def _apply_phone(session: Session, change: Change, as_of: date, source_file_id: int) -> None:
    person = _get_or_create_person(session, change)
    new_phone = (change.payload or {}).get("phone")
    if not new_phone or person.phone == new_phone:
        return
    for record in person.phones:
        if record.valid_to is None and record.phone != new_phone:
            record.valid_to = as_of
    person.phone = new_phone
    session.add(
        PersonPhone(
            person_id=person.id, phone=new_phone, valid_from=as_of, source_file_id=source_file_id
        )
    )


def _apply_role(session: Session, change: Change, as_of: date, source_file_id: int) -> None:
    payload = change.payload or {}
    assignment = session.get(Assignment, payload.get("assignment_id")) if payload.get("assignment_id") else None
    if assignment is None:
        _apply_assignment(session, change, as_of, source_file_id)
        return
    assignment.valid_to = as_of
    _apply_assignment(session, change, as_of, source_file_id)


def _apply_assignment(session: Session, change: Change, as_of: date, source_file_id: int) -> None:
    payload = change.payload or {}
    person = _get_or_create_person(session, change)

    if payload.get("phone") and person.phone != payload["phone"]:
        for record in person.phones:
            if record.valid_to is None:
                record.valid_to = as_of
        person.phone = payload["phone"]
        session.add(
            PersonPhone(
                person_id=person.id,
                phone=payload["phone"],
                valid_from=as_of,
                source_file_id=source_file_id,
            )
        )
    elif payload.get("phone") and not person.phones:
        session.add(
            PersonPhone(
                person_id=person.id,
                phone=payload["phone"],
                valid_from=as_of,
                source_file_id=source_file_id,
            )
        )

    outlet_id = change.outlet_id or payload.get("outlet_id")
    if not outlet_id:
        return

    # 같은 (매체·구분·직책·부서) 자리에 이미 다른 사람이 있으면 그 자리를 닫는다.
    if payload.get("kind") == "desk" and payload.get("role_slot"):
        dept = payload.get("dept")
        dept_filter = Assignment.dept.is_(None) if dept is None else Assignment.dept == dept
        occupants = session.scalars(
            select(Assignment).where(
                Assignment.outlet_id == outlet_id,
                Assignment.kind == "desk",
                Assignment.role_slot == payload["role_slot"],
                dept_filter,
                Assignment.valid_to.is_(None),
                Assignment.person_id != person.id,
            )
        ).all()
        for occupant in occupants:
            occupant.valid_to = as_of

    # 같은 자리(구분·직책·부서)를 다시 채우는 경우에만 이전 행을 닫는다.
    # 한 사람이 두 자리를 겸하는 경우(예: 매일경제 강두순 — 증권부장 + 사회부장)가
    # 실제로 있으므로, 매체·구분만 보고 닫으면 겸직이 사라진다.
    slot = payload.get("role_slot")
    dept = payload.get("dept")
    existing = session.scalar(
        select(Assignment).where(
            Assignment.person_id == person.id,
            Assignment.outlet_id == outlet_id,
            Assignment.kind == payload.get("kind", "reporter"),
            Assignment.role_slot.is_(None) if slot is None else Assignment.role_slot == slot,
            Assignment.dept.is_(None) if dept is None else Assignment.dept == dept,
            Assignment.valid_to.is_(None),
        )
    )
    if existing:
        existing.valid_to = as_of

    session.add(
        Assignment(
            person_id=person.id,
            outlet_id=outlet_id,
            kind=payload.get("kind", "reporter"),
            role_slot=payload.get("role_slot"),
            role_label=payload.get("role_label"),
            dept=payload.get("dept"),
            rank=payload.get("rank", "기자"),
            rank_order=payload.get("rank_order", 50),
            note=payload.get("note"),
            concurrent=bool(payload.get("concurrent")),
            valid_from=as_of,
            source_file_id=source_file_id,
        )
    )


def _apply_removal(session: Session, change: Change, as_of: date) -> None:
    assignment_id = (change.payload or {}).get("assignment_id")
    assignment = session.get(Assignment, assignment_id) if assignment_id else None
    if assignment and assignment.valid_to is None:
        assignment.valid_to = as_of


def reject_all_pending(change_set: ChangeSet) -> None:
    for change in change_set.changes:
        if change.decision == PENDING:
            change.decision = REJECTED

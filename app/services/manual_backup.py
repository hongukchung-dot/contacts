"""재구축(rebuild) 때 손으로 고친 내용을 지켰다가 되살린다.

웹에서 손으로 고친 자리에는 `locked` 표시와 수정 시각이 남는다.
재구축은 인물·자리를 전부 지우고 원본 파일로 다시 만들기 때문에,
그 전에 손으로 고친 것만 추려 JSON 으로 백업해 두고(`collect` → `save`),
재적재가 끝난 뒤 다시 얹는다(`reapply`).

되살리는 것:
- 손으로 만들거나 고친 **활성 자리** (직접 입력·수정 — 인물 이름·번호 교정 포함)
- 손으로 **마감한 자리** (인사이동 처리 — 파일이 다시 만들어 놓은 자리를 도로 마감)
- 인물에 붙인 **이메일·메모** (파일에는 없는 정보라 재구축하면 사라진다)

되살릴 수 없는 것: 흔적 없이 삭제(purge)한 자리 — 기록 자체가 없다.
이런 자리는 재구축 후 다시 나타날 수 있으니 결과 화면에서 확인해야 한다.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ingest.normalize import normalize_key
from ..models import Assignment, Outlet, Person

BACKUP_VERSION = 1


def _seat_dict(assignment: Assignment) -> dict:
    person = assignment.person
    return {
        "outlet": assignment.outlet.name,
        "name": person.name,
        "phone": person.phone,
        "email": person.email,
        "memo": person.memo,
        "kind": assignment.kind,
        "role_slot": assignment.role_slot,
        "role_label": assignment.role_label,
        "dept": assignment.dept,
        "note": assignment.note,
        "concurrent": assignment.concurrent,
        "valid_from": assignment.valid_from.isoformat() if assignment.valid_from else None,
        "valid_to": assignment.valid_to.isoformat() if assignment.valid_to else None,
    }


def collect_manual_state(session: Session) -> dict:
    """손으로 고친 자리와 인물 추가 정보(이메일·메모)를 모은다."""
    locked = session.scalars(
        select(Assignment)
        .where(Assignment.locked.is_(True))
        .join(Person, Assignment.person_id == Person.id)
    ).unique().all()

    active = [_seat_dict(a) for a in locked if a.valid_to is None]
    closed = [_seat_dict(a) for a in locked if a.valid_to is not None]

    extras = [
        {"name": p.name, "phone": p.phone, "email": p.email, "memo": p.memo}
        for p in session.scalars(
            select(Person).where((Person.email.is_not(None)) | (Person.memo.is_not(None)))
        ).all()
    ]

    return {
        "version": BACKUP_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "active_seats": active,
        "closed_seats": closed,
        "person_extras": extras,
    }


def save_backup(state: dict, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = directory / f"manual-backup-{stamp}.json"
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_backup(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_active_seat(session: Session, item: dict, outlet: Outlet) -> Assignment | None:
    """백업 항목과 같은 자리를 찾는다: 같은 매체·이름·구분 (데스크는 직책 열까지)."""
    stmt = (
        select(Assignment)
        .join(Person, Assignment.person_id == Person.id)
        .where(
            Assignment.outlet_id == outlet.id,
            Assignment.valid_to.is_(None),
            Assignment.kind == item["kind"],
            Person.name_key == normalize_key(item["name"]),
        )
    )
    if item["kind"] == "desk" and item.get("role_slot"):
        stmt = stmt.where(Assignment.role_slot == item["role_slot"])
    return session.scalars(stmt.limit(1)).first()


def reapply_manual_state(session: Session, state: dict) -> dict:
    """재적재가 끝난 DB 위에 손으로 고친 내용을 다시 얹는다."""
    from .edit import AssignmentForm, _set_phone, _stamp, create_assignment

    report: dict[str, list[str]] = {
        "restored": [],       # 다시 만든/잠근 활성 자리
        "reclosed": [],       # 도로 마감한 자리
        "extras": [],         # 이메일·메모 복원
        "missing_outlet": [], # 매체를 찾지 못해 못 되살린 항목
        "check": [],          # 사람이 확인해야 할 항목 (같은 칸의 다른 사람 등)
    }

    # 1) 손으로 마감했던 자리 — 파일이 도로 만들어 놨으면 다시 마감한다
    for item in state.get("closed_seats", []):
        outlet = session.scalar(select(Outlet).where(Outlet.name == item["outlet"]))
        if outlet is None:
            report["missing_outlet"].append(f"{item['outlet']} {item['name']} (마감)")
            continue
        seat = _find_active_seat(session, item, outlet)
        if seat is not None and not seat.locked:
            seat.valid_to = (
                date.fromisoformat(item["valid_to"]) if item.get("valid_to") else date.today()
            )
            _stamp(seat, None)
            report["reclosed"].append(f"{item['outlet']} {item['name']}")

    # 2) 손으로 만들거나 고친 활성 자리
    for item in state.get("active_seats", []):
        outlet = session.scalar(select(Outlet).where(Outlet.name == item["outlet"]))
        if outlet is None:
            report["missing_outlet"].append(f"{item['outlet']} {item['name']}")
            continue

        seat = _find_active_seat(session, item, outlet)
        if seat is None:
            form = AssignmentForm(
                name=item["name"],
                phone=item.get("phone"),
                email=item.get("email"),
                memo=item.get("memo"),
                kind=item["kind"],
                role_slot=item.get("role_slot"),
                role_label=item.get("role_label"),
                dept=item.get("dept"),
                note=item.get("note"),
                concurrent=bool(item.get("concurrent")),
            )
            seat = create_assignment(session, outlet, form, user=None)
            if item.get("valid_from"):
                seat.valid_from = date.fromisoformat(item["valid_from"])
        else:
            person = seat.person
            if item.get("phone") and person.phone != item["phone"]:
                _set_phone(session, person, item["phone"])
            for field in ("role_slot", "role_label", "dept", "note"):
                setattr(seat, field, item.get(field))
            seat.concurrent = bool(item.get("concurrent"))
            _stamp(seat, None)
        report["restored"].append(f"{item['outlet']} {item['name']}")

        # 같은 칸(매체·직책 열·부서)에 파일로 들어온 다른 사람이 있으면 알려 준다.
        # (원래 손으로 교체했던 잘못된 인물이 되돌아온 경우일 수 있다)
        if item["kind"] == "desk" and item.get("role_slot"):
            others = session.scalars(
                select(Assignment)
                .join(Person, Assignment.person_id == Person.id)
                .where(
                    Assignment.outlet_id == outlet.id,
                    Assignment.valid_to.is_(None),
                    Assignment.kind == "desk",
                    Assignment.role_slot == item["role_slot"],
                    (Assignment.dept.is_(None) if item.get("dept") is None
                     else Assignment.dept == item.get("dept")),
                    Assignment.locked.is_(False),
                    Person.name_key != normalize_key(item["name"]),
                )
            ).unique().all()
            for other in others:
                report["check"].append(
                    f"{item['outlet']} {item.get('role_slot')}: "
                    f"{item['name']} 외에 {other.person.name} 도 같은 칸에 있습니다"
                )

    # 3) 이메일·메모
    for item in state.get("person_extras", []):
        stmt = select(Person).where(Person.name_key == normalize_key(item["name"]))
        if item.get("phone"):
            stmt = stmt.where(Person.phone == item["phone"])
        person = session.scalars(stmt.limit(1)).first()
        if person is None:
            continue
        changed = False
        if item.get("email") and person.email != item["email"]:
            person.email = item["email"]
            changed = True
        if item.get("memo") and person.memo != item["memo"]:
            person.memo = item["memo"]
            changed = True
        if changed:
            report["extras"].append(person.name)

    session.flush()
    return report

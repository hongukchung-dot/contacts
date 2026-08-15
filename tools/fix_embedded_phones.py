"""이름·직책 칸에 전화번호가 붙어 들어간 데이터를 찾아 번호 칸으로 옮긴다.

`김혜연 / 편집국장 02-393-0188` 처럼 파싱 과정에서 번호가 직책·이름에
붙어버린 행을 정리한다. 규칙:

- 그 사람의 번호 칸이 비어 있으면 → 번호 칸으로 옮긴다 (번호 이력에도 남김)
- 이미 다른 번호가 있으면      → 덮어쓰지 않고 담당 메모에 `유선 02-…` 로 보존
- 같은 번호가 이미 있으면      → 문자열에서 번호만 걷어낸다

    # 무엇이 바뀔지 먼저 확인 (DB 변경 없음)
    docker compose exec app python tools/fix_embedded_phones.py

    # 실제로 정리
    docker compose exec app python tools/fix_embedded_phones.py --yes

파서도 같은 규칙으로 고쳐졌으므로, 앞으로 올리는 파일에서는 이런 데이터가
생기지 않는다. 이 도구는 이미 들어가 있는 데이터를 제자리에서 고치는 용도다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.ingest.normalize import (  # noqa: E402
    extract_embedded_phone,
    format_phone,
    mask_phone_display,
)
from app.models import Assignment, Person  # noqa: E402


def find_dirty_rows(session) -> list[dict]:
    """번호가 붙어 있는 (인물, 자리, 칸) 목록을 만든다."""
    rows: list[dict] = []

    assignments = session.scalars(
        select(Assignment).where(Assignment.valid_to.is_(None))
    ).unique().all()

    for assignment in assignments:
        for field in ("role_label", "dept", "note"):
            value = getattr(assignment, field)
            cleaned, digits = extract_embedded_phone(value)
            if cleaned and cleaned.strip() in {"유선", "직통", "전화"}:
                cleaned = None  # 번호를 떼고 나면 빈 껍데기만 남는 표기
            if digits:
                rows.append({
                    "kind": "assignment", "field": field,
                    "assignment": assignment, "person": assignment.person,
                    "outlet": assignment.outlet.name,
                    "before": value, "after": cleaned, "digits": digits,
                })

    for person in session.scalars(select(Person)).all():
        cleaned, digits = extract_embedded_phone(person.name)
        if digits:
            rows.append({
                "kind": "person_name", "field": "name",
                "assignment": None, "person": person,
                "outlet": "-",
                "before": person.name, "after": cleaned, "digits": digits,
            })
    return rows


def apply_fixes(session, rows: list[dict]) -> dict:
    from app.ingest.normalize import normalize_key
    from app.services.edit import _set_phone

    counts = {"moved": 0, "noted": 0, "stripped": 0}
    for row in rows:
        person = row["person"]
        digits = row["digits"]

        # 1) 문자열에서 번호를 걷어낸다
        if row["kind"] == "person_name":
            if not row["after"]:
                continue  # 이름이 통째로 번호였던 경우 — 건드리지 않고 목록에만 남긴다
            person.name = row["after"]
            person.name_key = normalize_key(row["after"])
        else:
            setattr(row["assignment"], row["field"], row["after"])

        # 2) 번호를 옮긴다
        if person.phone is None:
            _set_phone(session, person, digits)
            counts["moved"] += 1
        elif person.phone == digits:
            counts["stripped"] += 1
        else:
            memo = f"유선 {format_phone(digits)}"
            if memo not in (person.memo or ""):
                person.memo = f"{person.memo} · {memo}" if person.memo else memo
            counts["noted"] += 1
    session.flush()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="이름·직책에 붙은 전화번호 분리")
    parser.add_argument("--yes", action="store_true", help="실제로 고친다 (없으면 목록만 출력)")
    parser.add_argument("--reveal", action="store_true", help="번호를 가리지 않고 출력")
    args = parser.parse_args()

    with session_scope() as session:
        rows = find_dirty_rows(session)
        if not rows:
            print("이름·직책 칸에 번호가 붙어 있는 데이터가 없습니다.")
            return 0

        print(f"번호가 붙어 있는 칸 {len(rows)}개")
        for row in rows:
            shown = format_phone(row["digits"]) if args.reveal else mask_phone_display(row["digits"])
            dest = (
                "→ 번호 칸으로" if row["person"].phone is None
                else ("(이미 같은 번호 있음 — 걷어내기만)" if row["person"].phone == row["digits"]
                      else "→ 메모에 보존 (다른 번호가 이미 있음)")
            )
            print(f"  · {row['outlet']:12s} {row['person'].name:8s} "
                  f"[{row['field']}] {row['before']!r} → {row['after']!r} · {shown} {dest}")

        if not args.yes:
            print("\n목록만 출력했습니다. 실제로 고치려면 --yes 를 붙여 주세요.")
            return 0

        counts = apply_fixes(session, rows)
        print(f"\n완료: 번호 칸으로 이동 {counts['moved']}건, "
              f"메모 보존 {counts['noted']}건, 중복 제거 {counts['stripped']}건")
        print("웹 화면에서 결과를 확인하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""명령줄에서 파일을 적재한다. 웹 업로드 화면과 같은 절차를 거친다.

초기 4개 파일을 한 번에 밀어 넣을 때 쓰면 편하다.

    # 변경 내역만 보고 반영하지 않음 (기본)
    python tools/load_file.py "(26-0609) 출입기자 현황.xlsx"

    # 자동 승인 대상(신규·단순변경)만 반영. 오타 의심·삭제는 웹에서 검토
    python tools/load_file.py *.xlsx *.docx --apply

    # 모든 항목을 반영 (권장하지 않음 — 오타까지 그대로 들어간다)
    python tools/load_file.py 파일.xlsx --apply --approve-all
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import session_scope  # noqa: E402
from app.models import APPROVED, CONFLICT, NEW, REMOVE, UPDATE  # noqa: E402
from app.services.ingest import DuplicateFileError, apply_change_set, stage_file  # noqa: E402

LABEL = {NEW: "신규", UPDATE: "변경", CONFLICT: "확인필요", REMOVE: "삭제"}


def load(path: Path, *, apply: bool, approve_all: bool, as_of: date | None) -> int:
    print("=" * 78)
    print(f"파일: {path.name}")
    try:
        with session_scope() as session:
            change_set = stage_file(
                session, path, filename=path.name, as_of_override=as_of
            )
            session.flush()

            source = change_set.source_file
            print(f"  유형 {source.file_kind} · 기준일 {source.as_of} · 읽은 인원 {source.record_count}명")
            if source.unparsed:
                print(f"  ⚠ 해석 못 한 칸 {len(source.unparsed)}개")
                for item in source.unparsed[:5]:
                    print(f"      {item}")

            counts = Counter(change.change_type for change in change_set.changes)
            if not change_set.changes:
                print("  변경 없음 — 이미 최신 상태입니다.")
                return 0
            print("  변경: " + ", ".join(f"{LABEL.get(k, k)} {v}건" for k, v in counts.items()))

            needs_review = [c for c in change_set.changes if not c.auto_apply]
            for change in needs_review[:30]:
                print(
                    f"    [{LABEL.get(change.change_type, change.change_type)}] "
                    f"{change.outlet_name} {change.person_name} "
                    f"{change.old_value or '—'} → {change.new_value or '—'}  ({change.reason})"
                )
            if len(needs_review) > 30:
                print(f"    … 외 {len(needs_review) - 30}건")

            if not apply:
                print(f"  → 반영하지 않았습니다. 웹의 검토 화면(/review/{change_set.id})에서 확인하세요.")
                return 0

            if approve_all:
                for change in change_set.changes:
                    change.decision = APPROVED
            applied = apply_change_set(session, change_set, applied_by_id=None)
            print(
                "  → 반영 완료: "
                + ", ".join(f"{LABEL.get(k, k)} {v}" for k, v in applied.items() if v)
            )
            if applied.get("skipped"):
                print(f"     (검토 필요 {applied['skipped']}건은 웹 화면에서 확인하세요)")
    except DuplicateFileError as exc:
        print(f"  건너뜀: {exc}")
    except ValueError as exc:
        print(f"  ✗ {exc}")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="주소록 파일 적재")
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--apply", action="store_true", help="자동 승인 대상을 실제로 반영")
    parser.add_argument(
        "--approve-all", action="store_true", help="오타 의심·삭제까지 전부 반영 (권장하지 않음)"
    )
    parser.add_argument("--as-of", type=date.fromisoformat, default=None, help="기준일 강제 지정 (YYYY-MM-DD)")
    args = parser.parse_args()

    # 기준일이 오래된 파일부터 처리해야 최신 파일이 옛 값을 덮어쓴다.
    status = 0
    for path in args.paths:
        if not path.exists():
            print(f"✗ 파일이 없습니다: {path}")
            status = 1
            continue
        status |= load(path, apply=args.apply, approve_all=args.approve_all, as_of=args.as_of)
    return status


if __name__ == "__main__":
    raise SystemExit(main())

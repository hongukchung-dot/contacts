"""같은 매체에 데스크와 출입기자로 겹쳐 등록된 사람을 정리한다.

이름·전화번호·매체가 같은데 두 자리를 쥔 경우, **데스크만 남기고**
출입기자 자리를 지운다. 손으로 고친(잠긴) 자리는 건드리지 않는다.

    # 무엇이 지워질지 먼저 확인 (DB 변경 없음)
    docker compose exec app python tools/dedupe_roles.py

    # 실제로 지우기
    docker compose exec app python tools/dedupe_roles.py --yes

파일을 다시 적재하거나 rebuild 를 돌리면 중복이 다시 생길 수 있으니,
그런 뒤에는 이 명령을 한 번 더 돌리면 된다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import session_scope  # noqa: E402
from app.services.edit import (  # noqa: E402
    find_desk_reporter_overlaps,
    remove_desk_reporter_overlaps,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="데스크·출입기자 중복 정리")
    parser.add_argument("--yes", action="store_true", help="실제로 지운다 (없으면 목록만 출력)")
    args = parser.parse_args()

    with session_scope() as session:
        overlaps = find_desk_reporter_overlaps(session)
        if not overlaps:
            print("데스크와 출입기자로 겹쳐 등록된 사람이 없습니다.")
            return 0

        print(f"겹쳐 등록된 출입기자 자리 {len(overlaps)}건 — 데스크는 남고 아래가 지워집니다")
        for item in sorted(overlaps, key=lambda o: (o.outlet_name, o.person_name)):
            mark = "  (잠김 — 건너뜀)" if item.locked else ""
            role = f" · {item.role_label}" if item.role_label else ""
            print(f"  · {item.outlet_name:12s} {item.person_name}{role}{mark}")

        if not args.yes:
            print("\n목록만 출력했습니다. 실제로 지우려면 --yes 를 붙여 주세요.")
            return 0

        removed, skipped = remove_desk_reporter_overlaps(session)
        print(f"\n완료: {removed}건 삭제" + (f", 잠긴 {skipped}건은 유지" if skipped else ""))
        print("웹 화면에서 결과를 확인하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

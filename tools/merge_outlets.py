"""같은 매체가 두 표기로 갈라져 들어온 것을 합친다.

`헤럴드` / `헤럴드경제` 처럼 한 매체가 파일마다 다르게 적혀 두 개로 등록된 경우에 쓴다.
사전(`data/reference/outlets.yml`)에 별칭을 넣은 뒤 `--auto` 로 돌리면
사전 기준으로 합쳐야 할 것을 알아서 찾는다.

    # 사전에 별칭을 넣은 뒤 — 무엇을 합칠지 먼저 확인
    docker compose exec app python tools/merge_outlets.py --auto

    # 실제로 합치기
    docker compose exec app python tools/merge_outlets.py --auto --yes

    # 사전과 무관하게 직접 지정
    docker compose exec app python tools/merge_outlets.py --from 헤럴드 --into 헤럴드경제 --yes

자리와 번호 이력은 남는 쪽으로 모이고, 같은 매체에 같은 이름이 둘이 되면 인물도 합쳐진다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import func, select  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.ingest.reference import get_reference  # noqa: E402
from app.models import Assignment, Outlet  # noqa: E402
from app.services.edit import EditError, merge_outlets  # noqa: E402


def outlet_counts(session) -> dict[str, int]:
    rows = session.execute(
        select(Outlet.name, func.count(Assignment.id))
        .join(Assignment, Assignment.outlet_id == Outlet.id, isouter=True)
        .group_by(Outlet.id)
    ).all()
    return {name: count for name, count in rows}


def find_auto_pairs() -> list[tuple[str, str]]:
    """사전 기준으로 합쳐야 할 (없앨 매체, 남길 매체) 쌍을 찾는다.

    DB 에 있는 매체 이름이 사전에서 다른 매체의 별칭으로 등록돼 있으면 합칠 대상이다.
    """
    reference = get_reference()
    pairs: list[tuple[str, str]] = []
    with session_scope() as session:
        names = [row[0] for row in session.execute(select(Outlet.name)).all()]

    for name in names:
        match = reference.match_outlet(name)
        if match and match.name != name and match.name in names:
            pairs.append((name, match.name))
    return pairs


def show(pairs: list[tuple[str, str]]) -> None:
    with session_scope() as session:
        counts = outlet_counts(session)
    for drop_name, keep_name in pairs:
        print(f"  · {drop_name} ({counts.get(drop_name, 0)}건)  →  "
              f"{keep_name} ({counts.get(keep_name, 0)}건)")


def run(pairs: list[tuple[str, str]]) -> int:
    failures = 0
    for drop_name, keep_name in pairs:
        with session_scope() as session:
            drop = session.scalar(select(Outlet).where(Outlet.name == drop_name))
            keep = session.scalar(select(Outlet).where(Outlet.name == keep_name))
            if drop is None or keep is None:
                print(f"  ✗ 찾을 수 없습니다: {drop_name} 또는 {keep_name}")
                failures += 1
                continue
            try:
                result = merge_outlets(session, keep, drop)
            except EditError as exc:
                print(f"  ✗ {drop_name} → {keep_name}: {exc}")
                failures += 1
                continue
            print(f"  ✓ {drop_name} → {keep_name}: 자리 {result['moved']}건 이동"
                  + (f", 인물 {result['merged_people']}명 합침" if result["merged_people"] else ""))
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="같은 매체가 갈라진 것을 합친다")
    parser.add_argument("--auto", action="store_true", help="사전(outlets.yml) 기준으로 찾아 합친다")
    parser.add_argument("--from", dest="source", default=None, help="없앨 매체명")
    parser.add_argument("--into", dest="target", default=None, help="남길 매체명")
    parser.add_argument("--yes", action="store_true", help="실제로 실행 (없으면 계획만 출력)")
    args = parser.parse_args()

    if args.auto:
        pairs = find_auto_pairs()
        if not pairs:
            print("사전 기준으로 합칠 매체가 없습니다.")
            return 0
        print(f"사전 기준으로 합칠 매체 {len(pairs)}쌍")
    elif args.source and args.target:
        pairs = [(args.source, args.target)]
        print("합칠 매체")
    else:
        parser.error("--auto 를 쓰거나 --from 과 --into 를 함께 지정해 주세요.")
        return 2

    show(pairs)

    if not args.yes:
        print("\n계획만 출력했습니다. 실제로 합치려면 --yes 를 붙여 주세요.")
        return 0

    print()
    failures = run(pairs)
    print("\n완료. 웹 화면에서 결과를 확인하세요.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

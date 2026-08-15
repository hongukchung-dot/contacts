"""보관된 원본 파일로 주소록 데이터를 처음부터 다시 만든다.

파싱·매칭 규칙을 고친 뒤, 잘못 쌓인 데이터를 버리고 새 규칙으로 다시 세울 때 쓴다.
계정·매체 사전·접속 기록은 그대로 두고 **인물/자리/업로드 이력만** 새로 만든다.
원본 파일은 업로드할 때 보관해 두었으므로 파일을 다시 올릴 필요가 없다.

    docker compose exec app python tools/rebuild.py            # 무엇을 할지 보여만 준다
    docker compose exec app python tools/rebuild.py --yes      # 실제로 다시 만든다
    docker compose exec app python tools/rebuild.py --yes --from-dir /tmp/원본

`--from-dir` 은 보관본이 없을 때(볼륨을 지운 경우) 파일이 있는 디렉터리를 직접 지정한다.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, func, select  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.models import (  # noqa: E402
    APPROVED,
    Assignment,
    Change,
    ChangeSet,
    Person,
    PersonPhone,
    SourceFile,
    SourceRecord,
)
from app.services.ingest import (  # noqa: E402
    DuplicateFileError,
    apply_change_set,
    stage_file,
)
from app.services.manual_backup import (  # noqa: E402
    collect_manual_state,
    reapply_manual_state,
    save_backup,
)

LABEL = {"new": "신규", "update": "변경", "conflict": "확인필요", "remove": "삭제"}


def collect_sources(from_dir: Path | None) -> list[tuple[Path, str, object]]:
    """다시 넣을 파일 목록을 (경로, 원래 파일명, 기준일) 로 모은다. 기준일 오름차순."""
    if from_dir is not None:
        files = sorted(
            path
            for path in from_dir.iterdir()
            if path.suffix.lower() in {".xlsx", ".xlsm", ".docx"} and not path.name.startswith("~$")
        )
        return [(path, path.name, None) for path in files]

    with session_scope() as session:
        rows = session.execute(
            select(SourceFile.stored_path, SourceFile.filename, SourceFile.as_of)
            .order_by(SourceFile.as_of, SourceFile.id)
        ).all()

    sources: list[tuple[Path, str, object]] = []
    for stored_path, filename, as_of in rows:
        if not stored_path:
            print(f"  ! 보관본이 없어 건너뜁니다: {filename}")
            continue
        path = Path(stored_path)
        if not path.exists():
            print(f"  ! 보관 파일을 찾을 수 없습니다: {path} ({filename})")
            continue
        sources.append((path, filename, as_of))
    return sources


def summarise() -> dict:
    with session_scope() as session:
        return {
            "people": session.scalar(select(func.count()).select_from(Person)) or 0,
            "assignments": session.scalar(select(func.count()).select_from(Assignment)) or 0,
            "locked": session.scalar(
                select(func.count()).select_from(Assignment).where(Assignment.locked.is_(True))
            )
            or 0,
            "files": session.scalar(select(func.count()).select_from(SourceFile)) or 0,
        }


def wipe() -> None:
    """인물·자리·업로드 이력을 지운다. 계정·매체 사전·접속 기록은 남긴다."""
    with session_scope() as session:
        session.execute(delete(Change))
        session.execute(delete(ChangeSet))
        session.execute(delete(SourceRecord))
        session.execute(delete(Assignment))
        session.execute(delete(PersonPhone))
        session.execute(delete(Person))
        session.execute(delete(SourceFile))


def reload_sources(sources, *, approve_all: bool) -> None:
    for path, filename, as_of in sources:
        print("=" * 74)
        print(f"· {filename}")
        try:
            with session_scope() as session:
                change_set = stage_file(
                    session, path, filename=filename, as_of_override=as_of
                )
                session.flush()

                source = change_set.source_file
                sheets = ", ".join(
                    f"{item['sheet']} {item['count']}명" for item in (source.sheet_stats or [])
                )
                print(f"    기준일 {source.as_of} · 읽은 인원 {source.record_count}명"
                      + (f" ({sheets})" if sheets else ""))
                if source.unparsed:
                    print(f"    ! 해석 못 한 칸 {len(source.unparsed)}개")
                for warning in (source.parse_warnings or [])[:5]:
                    print(f"    ! {warning}")

                if approve_all:
                    for change in change_set.changes:
                        change.decision = APPROVED
                counts = apply_change_set(session, change_set, applied_by_id=None)
                summary = ", ".join(
                    f"{LABEL.get(key, key)} {value}" for key, value in counts.items() if value
                )
                print(f"    → {summary or '변경 없음'}")
                if counts.get("skipped"):
                    print(f"      (확인 필요 {counts['skipped']}건은 웹 검토 화면에 남아 있습니다)")
        except DuplicateFileError as exc:
            print(f"    건너뜀: {exc}")
        except Exception as exc:  # noqa: BLE001 - 한 파일이 실패해도 나머지는 계속한다
            print(f"    ✗ 실패: {type(exc).__name__}: {exc}")


def report_lookalike_outlets() -> None:
    """이름이 서로 겹치는 매체를 짚어 준다.

    `조선일보`/`조선비즈` 처럼 앞부분이 같은 매체가 실수로 합쳐지지 않았는지,
    혹은 반대로 같은 매체가 두 이름으로 갈라지지 않았는지 눈으로 확인하기 위한 것.
    """
    from app.models import Outlet

    with session_scope() as session:
        rows = session.execute(
            select(Outlet.name, Outlet.category, func.count(Assignment.id))
            .join(Assignment, Assignment.outlet_id == Outlet.id, isouter=True)
            .group_by(Outlet.id)
            .order_by(Outlet.sort_order)
        ).all()

    active = [(name, category, count) for name, category, count in rows if count]
    pairs = [
        (a, b)
        for a in active
        for b in active
        if a[0] != b[0] and b[0].startswith(a[0])
    ]
    if not pairs:
        return
    print("\n" + "=" * 74)
    print("이름이 비슷한 매체 — 서로 다른 매체가 맞는지 확인하세요")
    for (short_name, short_cat, short_count), (long_name, long_cat, long_count) in pairs:
        print(f"  · {short_name} ({short_cat}, {short_count}건)  ↔  "
              f"{long_name} ({long_cat}, {long_count}건)")
    print("  같은 매체라면 data/reference/outlets.yml 에서 한쪽을 별칭으로 옮기고,")
    print("  웹의 인물 페이지에서 중복 인물을 합치면 됩니다.")


def main() -> int:
    parser = argparse.ArgumentParser(description="보관된 원본 파일로 데이터를 다시 만든다")
    parser.add_argument("--yes", action="store_true", help="실제로 실행 (없으면 계획만 출력)")
    parser.add_argument(
        "--from-dir", type=Path, default=None, help="보관본 대신 이 디렉터리의 파일을 쓴다"
    )
    parser.add_argument(
        "--approve-all",
        action="store_true",
        help="확인 필요 항목까지 전부 반영 (권장하지 않음)",
    )
    args = parser.parse_args()

    before = summarise()
    print("현재 상태")
    print(f"  인물 {before['people']}명 · 자리 {before['assignments']}건 "
          f"(손으로 고친 것 {before['locked']}건) · 업로드 파일 {before['files']}개")

    sources = collect_sources(args.from_dir)
    if not sources:
        print("\n다시 넣을 파일이 없습니다. --from-dir 로 원본이 있는 디렉터리를 지정해 주세요.")
        return 1

    print(f"\n다시 넣을 파일 {len(sources)}개 (기준일 오름차순)")
    for _, filename, as_of in sources:
        print(f"  · {as_of or '(파일에서 판별)'}  {filename}")

    if not args.yes:
        print("\n계획만 출력했습니다. 실제로 다시 만들려면 --yes 를 붙여 주세요.")
        if before["locked"]:
            print(f"  · 손으로 고친 {before['locked']}건은 자동으로 백업했다가 재적재 후 다시 얹습니다.")
            print("    (단, 흔적 없이 삭제했던 자리는 되살릴 기록이 없어 다시 나타날 수 있습니다)")
        return 0

    # 손으로 고친 내용을 지웠다가 재적재 후 다시 얹는다.
    with session_scope() as session:
        manual_state = collect_manual_state(session)
    backup_path = save_backup(manual_state, get_settings().upload_dir / "manual-backups")
    manual_total = len(manual_state["active_seats"]) + len(manual_state["closed_seats"])
    print(f"\n· 손으로 고친 {manual_total}건 + 이메일·메모 {len(manual_state['person_extras'])}건 백업")
    print(f"  → {backup_path}")

    print("\n· 기존 인물·자리·업로드 이력을 지웁니다 (계정·매체 사전·접속 기록은 유지) …")
    wipe()

    print("· 원본 파일을 기준일 순서로 다시 넣습니다 …\n")
    reload_sources(sources, approve_all=args.approve_all)

    if manual_total or manual_state["person_extras"]:
        print("\n· 손으로 고친 내용을 다시 얹습니다 …")
        with session_scope() as session:
            manual_report = reapply_manual_state(session, manual_state)
        if manual_report["restored"]:
            print(f"  복원한 자리 {len(manual_report['restored'])}건: "
                  + ", ".join(manual_report["restored"][:10])
                  + (" …" if len(manual_report["restored"]) > 10 else ""))
        if manual_report["reclosed"]:
            print(f"  도로 마감한 자리 {len(manual_report['reclosed'])}건: "
                  + ", ".join(manual_report["reclosed"][:10]))
        if manual_report["extras"]:
            print(f"  이메일·메모 복원 {len(manual_report['extras'])}명")
        for line in manual_report["missing_outlet"]:
            print(f"  ! 매체를 찾지 못해 못 되살림: {line}")
        for line in manual_report["check"]:
            print(f"  ⚠ 확인 필요: {line}")

    report_lookalike_outlets()

    after = summarise()
    print("\n" + "=" * 74)
    print("완료")
    print(f"  인물 {before['people']} → {after['people']}명")
    print(f"  자리 {before['assignments']} → {after['assignments']}건")
    print("\n웹 화면에서 결과를 확인하세요. '확인 필요'로 남은 항목은 검토 화면에 있습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

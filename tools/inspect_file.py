"""원본 주소록 파일을 DB 없이 파싱해 보고 결과를 눈으로 확인하는 도구.

실제 파일을 처음 넣기 전에 이 명령으로 파싱이 제대로 되는지 먼저 확인한다.
DB에 아무것도 쓰지 않는다.

    python tools/inspect_file.py "(26-0803) 주요 데스크 출입기자 현황.docx"
    python tools/inspect_file.py *.xlsx --full
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ingest.normalize import format_phone  # noqa: E402
from app.ingest.parsers import detect_and_parse  # noqa: E402


def mask(phone: str | None, reveal: bool) -> str:
    if not phone:
        return "(번호없음)"
    formatted = format_phone(phone)
    if reveal:
        return formatted
    head, mid, tail = formatted.split("-")
    return f"{head}-{'*' * len(mid)}-{tail}"


def report(path: Path, *, full: bool, reveal: bool) -> int:
    print("=" * 78)
    print(f"파일: {path.name}")
    try:
        result = detect_and_parse(path)
    except Exception as exc:  # noqa: BLE001 - 사용자에게 원인을 그대로 보여 준다
        print(f"  ✗ 파싱 실패: {exc}")
        return 1

    print(f"  유형   : {result.file_kind}")
    print(f"  기준일 : {result.as_of or '(찾지 못함)'}")
    print(f"  인원   : {len(result.records)}명")
    if result.sheet_kinds:
        print("  탭별   :")
        for title, kind in result.sheet_kinds.items():
            label = {"desk_matrix": "데스크 표", "reporter_list": "출입기자 목록"}.get(kind, kind)
            count = result.sheet_counts.get(title, 0)
            mark = "  ← 확인 필요" if count == 0 else ""
            print(f"           {title:20s} {label:12s} {count:4d}명{mark}")
    else:
        print(f"  시트/표: {', '.join(result.sheets)}")

    by_kind = Counter(r.kind for r in result.records)
    print(f"  구분   : " + ", ".join(f"{k} {v}명" for k, v in by_kind.items()))

    by_category: dict[str, set[str]] = defaultdict(set)
    for record in result.records:
        by_category[record.category].add(record.outlet)
    for category, outlets in sorted(by_category.items()):
        print(f"    · {category} {len(outlets)}개: {', '.join(sorted(outlets))}")

    unknown = sorted({r.outlet_raw for r in result.records if not r.outlet_known})
    if unknown:
        print(f"\n  ⚠ 사전에 없는 매체 {len(unknown)}개 → data/reference/outlets.yml 에 추가하세요")
        for name in unknown:
            print(f"      - {name}")

    no_phone = [r for r in result.records if not r.phone]
    if no_phone:
        print(f"\n  ⚠ 전화번호 없는 인원 {len(no_phone)}명")
        for record in no_phone[:20]:
            print(f"      - {record.outlet} {record.name} ({record.role_label or '-'})")
        if len(no_phone) > 20:
            print(f"      … 외 {len(no_phone) - 20}명")

    suspicious = [r for r in result.records if any("확인 필요" in w for w in r.warnings)]
    if suspicious:
        print(f"\n  ⚠ 이름이 이상한 레코드 {len(suspicious)}건")
        for record in suspicious[:20]:
            print(f"      - {record.outlet} {record.name!r} ← {record.source_text!r}")

    if result.unparsed:
        print(f"\n  ✗ 해석하지 못한 칸 {len(result.unparsed)}개")
        for item in result.unparsed[:30]:
            print(f"      - {item}")
        if len(result.unparsed) > 30:
            print(f"      … 외 {len(result.unparsed) - 30}개")

    if result.warnings:
        print(f"\n  경고 {len(result.warnings)}건")
        for item in result.warnings[:20]:
            print(f"      - {item}")

    if full:
        print("\n  ── 전체 목록 ──")
        current = None
        for record in sorted(
            result.records, key=lambda r: (r.category, r.outlet, r.rank_order, r.name)
        ):
            if record.outlet != current:
                current = record.outlet
                print(f"\n  [{record.category}] {record.outlet}")
            role = record.role_label or record.role_slot or "-"
            dept = f" {record.dept}" if record.dept else ""
            print(f"    {record.kind:8s}{dept:>8s} {record.name:6s} {role:24s} {mask(record.phone, reveal)}")

    ok = not result.unparsed
    print(f"\n  결과: {'✓ 정상' if ok else '✗ 확인 필요'}")
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="주소록 원본 파일 파싱 점검 (DB 변경 없음)")
    parser.add_argument("paths", nargs="+", type=Path, help="검사할 xlsx/docx 파일")
    parser.add_argument("--full", action="store_true", help="파싱된 전체 명단 출력")
    parser.add_argument("--reveal", action="store_true", help="전화번호를 가리지 않고 출력")
    args = parser.parse_args()

    status = 0
    for path in args.paths:
        if not path.exists():
            print(f"✗ 파일이 없습니다: {path}")
            status = 1
            continue
        status |= report(path, full=args.full, reveal=args.reveal)
    return status


if __name__ == "__main__":
    raise SystemExit(main())

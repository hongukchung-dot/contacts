"""원본 파일 4종을 읽는 파서.

세 가지 레이아웃을 지원한다.

1. `desk_matrix`  — 데스크 현황 xlsx. 행=매체, 열=직책. 한 칸에 여러 명, 한 매체에 여러 행.
2. `reporter_list`— 출입기자 현황 xlsx. 매체·이름·직급·전화 4열. 매체명은 그룹 첫 행에만.
3. `combined_docx`— 데스크+출입기자 docx. 매체 행(부서별 부장) 다음 행에 팀원이 뭉쳐서 들어감.

파일 유형은 확장자와 내용으로 자동 판별한다(`detect_and_parse`).
어떤 경우에도 예외로 죽지 않고, 해석하지 못한 칸은 `ParseResult.unparsed`에 남긴다.
"""

from __future__ import annotations

import re
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path
from xml.etree import ElementTree

from .normalize import clean_text, is_empty, normalize_key, parse_people
from .records import (
    DESK,
    REPORTER,
    ContactRecord,
    ParseResult,
    as_of_from_filename,
    as_of_from_text,
)
from .reference import get_reference

_HEADER_HINTS = {"매체", "매체명", "언론사"}

_CUSTOM_PROPS_PART = "docProps/custom.xml"


@contextmanager
def open_workbook(path: Path):
    """xlsx 를 연다. 열고 나면 반드시 닫는다.

    보안문서·DRM 도구를 거친 파일은 `docProps/custom.xml` 의 사용자 지정 속성에
    이름이 비어 있는 경우가 있는데, openpyxl 이 이를 만나면 표를 읽기도 전에
    TypeError 로 죽는다(`StringProperty.name should be str but value is NoneType`).
    그런 파일은 해당 부분만 들어낸 사본을 만들어 다시 연다. 표 데이터에는 영향이 없다.
    """
    from openpyxl import load_workbook

    try:
        workbook = load_workbook(path, data_only=True, read_only=True)
    except TypeError as exc:
        if "Property" not in str(exc):
            raise
        cleaned = _strip_custom_doc_props(path)
        try:
            workbook = load_workbook(cleaned, data_only=True, read_only=True)
        except Exception:
            cleaned.unlink(missing_ok=True)
            raise
        try:
            yield workbook
        finally:
            workbook.close()
            cleaned.unlink(missing_ok=True)
        return

    try:
        yield workbook
    finally:
        workbook.close()


def _strip_custom_doc_props(path: Path) -> Path:
    """`docProps/custom.xml` 과 그 참조를 제거한 사본을 만들어 경로를 돌려준다."""
    handle = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
    handle.close()
    target = Path(handle.name)

    with zipfile.ZipFile(path) as source, zipfile.ZipFile(
        target, "w", zipfile.ZIP_DEFLATED
    ) as out:
        for item in source.infolist():
            if item.filename == _CUSTOM_PROPS_PART:
                continue
            data = source.read(item.filename)
            if item.filename == "[Content_Types].xml":
                data = _drop_xml_nodes(data, "Override", "PartName", "/" + _CUSTOM_PROPS_PART)
            elif item.filename == "_rels/.rels":
                data = _drop_xml_nodes(data, "Relationship", "Target", _CUSTOM_PROPS_PART)
            out.writestr(item, data)
    return target


def _drop_xml_nodes(data: bytes, tag: str, attribute: str, value: str) -> bytes:
    """지정한 속성값을 가진 노드를 XML 에서 제거한다. 실패하면 원본을 그대로 둔다."""
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        return data

    namespace = root.tag.split("}")[0].strip("{") if root.tag.startswith("{") else ""
    qualified = f"{{{namespace}}}{tag}" if namespace else tag
    removed = [
        child
        for child in list(root)
        if child.tag == qualified and (child.get(attribute) or "").lstrip("/") == value.lstrip("/")
    ]
    for child in removed:
        root.remove(child)
    if not removed:
        return data
    if namespace:
        ElementTree.register_namespace("", namespace)
    return ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)


_CATEGORY_ROW_RE = re.compile(r"^[\[<\(【]\s*(.+?)\s*[\]>\)】]$")
_DEPT_SPLIT_RE = re.compile(r"\s*[:：]\s*")


# ── 공통 도우미 ─────────────────────────────────────────────────────────────

def _make_record(
    person,
    outlet_raw: str,
    kind: str,
    *,
    slot_hint: str | None = None,
    dept_hint: str | None = None,
    sheet: str = "",
    ref: str = "",
) -> ContactRecord:
    reference = get_reference()
    outlet, category, _order, known = reference.outlet_or_placeholder(outlet_raw)

    rank, rank_order = reference.rank_of(person.role_label)

    # 간부 현황 표의 칸(role_slot)은 데스크만 차지한다.
    # 출입기자에게 칸을 주면 `산업부 차장`이 산업부장 칸에 들어가 버린다.
    role_slot: str | None = None
    if kind == DESK:
        role_slot = reference.slot_from_label(person.role_label)
        if person.concurrent and slot_hint:
            # `편집국장` 열에 `(兼 경제부장)` 이라 적힌 경우 본직은 열 쪽이다.
            role_slot = slot_hint
        if role_slot is None and dept_hint:
            # 직책이 `부장`뿐이고 부서가 따로 적힌 경우: `테크부` + `장` → 테크부장 → 산업부장 슬롯
            role_slot = reference.slot_from_label(f"{dept_hint}장")
        if role_slot is None:
            role_slot = slot_hint

    dept = reference.dept_of(person.role_label) or dept_hint
    if dept is None and role_slot:
        dept = reference.dept_of(role_slot)

    warnings = list(person.warnings)
    if not known:
        near = reference.near_miss(outlet_raw)
        if near:
            warnings.append(
                f"사전에 없는 매체: {outlet_raw!r} — {near.name} 와(과) 이름이 비슷하지만 "
                "다른 매체로 등록합니다. 같은 매체라면 outlets.yml 에 별칭을 넣어 주세요"
            )
        else:
            warnings.append(f"사전에 없는 매체: {outlet_raw!r} — 분류 지정 필요")
    if person.phone is None:
        warnings.append("전화번호 없음")

    return ContactRecord(
        outlet_raw=clean_text(outlet_raw),
        outlet=outlet,
        category=category,
        outlet_known=known,
        name=person.name,
        raw_name=person.raw_name,
        phone=person.phone,
        raw_phone=person.raw_phone,
        kind=kind,
        role_label=person.role_label,
        role_slot=role_slot,
        rank=rank,
        rank_order=rank_order,
        dept=dept,
        note=person.note,
        concurrent=person.concurrent,
        source_sheet=sheet,
        source_ref=ref,
        source_text=person.source_text,
        warnings=warnings,
    )


# 데스크로 볼 직급. 이보다 아래(차장·팀장·기자)는 출입기자로 본다.
_DESK_RANKS = {"경영", "국장", "부장"}


def classify_kind(role_label: str | None, *, fallback: str) -> str:
    """직급으로 데스크/출입기자를 가른다.

    통합 docx 는 매체마다 맨 위에 부장급 데스크가 오고 그 아래에 출입기자가 붙는다.
    그런데 행 위치만 보고 나누면 배치가 조금만 달라져도 출입기자가 데스크로 잡힌다.
    실제로 그런 오류가 대량으로 났다. 직급이라는 값 자체를 기준으로 삼는 편이 안전하다.

    직급이 안 적힌 사람은 부장일 가능성이 낮으므로 fallback(대개 출입기자)을 따른다.
    """
    if not role_label:
        return fallback
    rank, _ = get_reference().rank_of(role_label)
    if rank in _DESK_RANKS:
        return DESK
    if rank in {"차장", "기자"}:
        return REPORTER
    return fallback


def _row_values(row) -> list[str]:
    return [clean_text(cell.value) for cell in row]


def _is_header_row(values: list[str]) -> bool:
    return any(normalize_key(v) in {normalize_key(h) for h in _HEADER_HINTS} for v in values)


# ── xlsx: 시트마다 구조가 다를 수 있다 ─────────────────────────────────────

def detect_sheet_kind(sheet) -> str | None:
    """시트 하나의 구조를 판별한다.

    한 파일 안에서도 시트마다 양식이 다르다(어떤 탭은 매트릭스, 어떤 탭은 4열 목록).
    파일 전체를 한 유형으로 단정하면 나머지 탭이 통째로 누락되므로 시트별로 본다.
    """
    for row in sheet.iter_rows(min_row=1, max_row=40):
        values = _row_values(row)
        if not any(values):
            continue
        if _build_reporter_map(values):
            return "reporter_list"
        if _is_header_row(values):
            columns, _ = _build_column_map(values, get_reference())
            if columns:
                return "desk_matrix"
    return None


def parse_xlsx(path: Path) -> ParseResult:
    """xlsx 를 시트별로 알맞은 파서에 태워 읽는다. 숨김 시트도 읽는다."""
    result = ParseResult(file_kind="xlsx")
    result.as_of = as_of_from_filename(path.name)

    with open_workbook(path) as workbook:
        # read_only 모드에서도 시트 목록은 숨김 여부와 무관하게 나온다.
        for sheet in workbook.worksheets:
            before = len(result.records)
            kind = detect_sheet_kind(sheet)
            if kind == "reporter_list":
                _read_reporter_sheet(sheet, result)
            elif kind == "desk_matrix":
                _read_desk_sheet(sheet, result)
            else:
                result.sheets.append(sheet.title)
                result.add_warning(
                    f"[{sheet.title}] 표 구조를 찾지 못해 건너뜀 — "
                    "머리글에 '매체명'과 직책 열, 또는 '매체/이름/직급/전화번호'가 있어야 합니다"
                )
                result.sheet_kinds[sheet.title] = "미인식"
                result.sheet_counts[sheet.title] = 0
                continue
            result.sheet_kinds[sheet.title] = kind
            result.sheet_counts[sheet.title] = len(result.records) - before

    # 파일 전체의 대표 유형은 가장 많이 읽은 시트 유형으로 정한다.
    if result.sheet_kinds:
        tally: dict[str, int] = {}
        for title, kind in result.sheet_kinds.items():
            if kind in {"reporter_list", "desk_matrix"}:
                tally[kind] = tally.get(kind, 0) + result.sheet_counts.get(title, 0)
        if tally:
            result.file_kind = max(tally, key=lambda k: tally[k])
    return result


def parse_desk_matrix(path: Path) -> ParseResult:
    """호환용 진입점. 실제로는 시트별 판별을 거친다."""
    return parse_xlsx(path)


def _read_desk_sheet(sheet, result: ParseResult) -> None:
    reference = get_reference()
    result.sheets.append(sheet.title)
    columns: dict[int, str] = {}   # 열 인덱스 → 슬롯 코드
    outlet_col: int | None = None
    current_outlet: str | None = None

    for row_idx, row in enumerate(sheet.iter_rows(), start=1):
        values = _row_values(row)
        if not any(values):
            continue

        joined = " ".join(v for v in values if v)
        if result.as_of is None:
            result.as_of = as_of_from_text(joined)

        if _is_header_row(values):
            columns, outlet_col = _build_column_map(values, reference)
            current_outlet = None
            if not columns:
                result.add_warning(f"[{sheet.title}] {row_idx}행 머리글에서 직책 열을 찾지 못함")
            continue

        if outlet_col is None:
            continue  # 머리글 전의 제목/기준일 행

        candidate = values[outlet_col] if outlet_col < len(values) else ""
        if candidate and not is_empty(candidate):
            if _CATEGORY_ROW_RE.match(candidate):
                continue  # `<종합지 데스크> 현황` 같은 구획 행
            current_outlet = candidate
        if not current_outlet:
            continue

        for col_idx, slot in columns.items():
            if col_idx >= len(values):
                continue
            cell_text = values[col_idx]
            if is_empty(cell_text):
                continue
            people = parse_people(cell_text)
            if not people:
                result.unparsed.append(
                    f"[{sheet.title}] {row_idx}행 {current_outlet}/{slot}: {cell_text!r}"
                )
                continue
            for person in people:
                result.records.append(
                    _make_record(
                        person,
                        current_outlet,
                        DESK,
                        slot_hint=slot,
                        sheet=sheet.title,
                        ref=f"{sheet.title}!R{row_idx}C{col_idx + 1}",
                    )
                )


def _build_column_map(values: list[str], reference) -> tuple[dict[int, str], int | None]:
    columns: dict[int, str] = {}
    outlet_col: int | None = None
    for idx, header in enumerate(values):
        if not header:
            continue
        if normalize_key(header) in {normalize_key(h) for h in _HEADER_HINTS}:
            if outlet_col is None:
                outlet_col = idx
            continue
        slot = reference.slot_from_header(header) or reference.slot_from_label(header)
        if slot:
            columns[idx] = slot
    return columns, outlet_col


# ── 2. 출입기자 현황 xlsx (4열 목록) ────────────────────────────────────────

_REPORTER_HEADERS = {
    "매체": "outlet",
    "매체명": "outlet",
    "언론사": "outlet",
    "이름": "name",
    "성명": "name",
    "직급": "role",
    "직책": "role",
    "전화번호": "phone",
    "연락처": "phone",
    "휴대폰": "phone",
}


def parse_reporter_list(path: Path) -> ParseResult:
    """호환용 진입점. 실제로는 시트별 판별을 거친다."""
    return parse_xlsx(path)


def _read_reporter_sheet(sheet, result: ParseResult) -> None:
    result.sheets.append(sheet.title)
    mapping: dict[str, int] = {}
    current_outlet: str | None = None

    for row_idx, row in enumerate(sheet.iter_rows(), start=1):
        values = _row_values(row)
        if not any(values):
            continue

        joined = " ".join(v for v in values if v)
        if result.as_of is None:
            result.as_of = as_of_from_text(joined)

        new_mapping = _build_reporter_map(values)
        if new_mapping:
            mapping = new_mapping
            current_outlet = None
            continue
        if not mapping:
            continue

        outlet_cell = values[mapping["outlet"]] if mapping.get("outlet", -1) < len(values) else ""
        if outlet_cell and not is_empty(outlet_cell):
            if _CATEGORY_ROW_RE.match(outlet_cell):
                continue
            current_outlet = outlet_cell
        if not current_outlet:
            continue

        name_cell = values[mapping["name"]] if mapping.get("name", 99) < len(values) else ""
        role_cell = values[mapping["role"]] if mapping.get("role", 99) < len(values) else ""
        phone_cell = values[mapping["phone"]] if mapping.get("phone", 99) < len(values) else ""
        if is_empty(name_cell) and is_empty(phone_cell):
            continue

        blob = " ".join(part for part in [name_cell, role_cell, phone_cell] if part)
        people = parse_people(blob)
        if not people:
            result.unparsed.append(f"[{sheet.title}] {row_idx}행: {blob!r}")
            continue
        for person in people:
            result.records.append(
                _make_record(
                    person,
                    current_outlet,
                    REPORTER,
                    sheet=sheet.title,
                    ref=f"{sheet.title}!R{row_idx}",
                )
            )


def _build_reporter_map(values: list[str]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for idx, header in enumerate(values):
        key = normalize_key(header)
        for candidate, field_name in _REPORTER_HEADERS.items():
            if key == normalize_key(candidate) and field_name not in mapping:
                mapping[field_name] = idx
    # 4개 항목이 모두 잡혀야 머리글 행으로 인정한다.
    return mapping if {"outlet", "name", "phone"} <= set(mapping) else {}


# ── 3. 데스크+출입기자 docx ─────────────────────────────────────────────────

def parse_combined_docx(path: Path) -> ParseResult:
    import docx

    result = ParseResult(file_kind="combined_docx")
    result.as_of = as_of_from_filename(path.name)
    document = docx.Document(str(path))
    reference = get_reference()

    if result.as_of is None:
        head = " ".join(p.text for p in document.paragraphs[:20])
        result.as_of = as_of_from_text(head)

    for table_idx, table in enumerate(document.tables, start=1):
        sheet = f"표{table_idx}"
        result.sheets.append(sheet)
        current_outlet: str | None = None
        # 직전 매체 행에서 읽어 둔 (부서, 열 인덱스) 목록
        dept_slots: list[str | None] = []

        for row_idx, row in enumerate(table.rows, start=1):
            cells = [clean_text(cell.text) for cell in row.cells]
            # docx는 병합 셀을 중복해서 돌려준다. 인접 중복을 접는다.
            cells = _dedupe_adjacent(cells)
            if not any(cells):
                continue

            first = cells[0]
            if _CATEGORY_ROW_RE.match(first) and not any(cells[1:]):
                continue  # `[종합지]` 구획 행

            outlet_match = reference.match_outlet(first) if first else None
            if outlet_match and len(first) <= 12:
                current_outlet = first
                dept_slots = []
                for col_idx, cell_text in enumerate(cells[1:], start=1):
                    if is_empty(cell_text):
                        continue
                    dept, body = _split_dept(cell_text)
                    dept_slots.append(dept)
                    for person in parse_people(body):
                        # 맨 윗줄이라도 차장·기자면 출입기자다.
                        kind = classify_kind(person.role_label, fallback=DESK)
                        result.records.append(
                            _make_record(
                                person,
                                current_outlet,
                                kind,
                                # 이 문서는 산업 출입처 데스크 명단이므로,
                                # 부서 표기가 없는 칸(`이관범 부장 …`)은 산업부장으로 본다.
                                slot_hint="산업부장",
                                dept_hint=reference.dept_of(dept) or dept,
                                sheet=sheet,
                                ref=f"{sheet}!R{row_idx}C{col_idx + 1}",
                            )
                        )
                continue

            if not current_outlet:
                continue

            # 팀원 행. 비어 있지 않은 칸을 순서대로 직전 매체 행의 부서에 대응시킨다.
            filled = [(idx, text) for idx, text in enumerate(cells) if not is_empty(text)]
            if not filled:
                continue
            if len(filled) > len(dept_slots) and dept_slots:
                result.add_warning(
                    f"[{sheet}] {row_idx}행 {current_outlet}: 팀원 칸 수({len(filled)})가 "
                    f"부서 수({len(dept_slots)})보다 많음 — 첫 부서로 배정"
                )
            for order, (col_idx, cell_text) in enumerate(filled):
                dept = dept_slots[order] if order < len(dept_slots) else (dept_slots[0] if dept_slots else None)
                people = parse_people(cell_text)
                if not people:
                    result.unparsed.append(f"[{sheet}] {row_idx}행 {current_outlet}: {cell_text!r}")
                    continue
                for person in people:
                    # 아랫줄이라도 부장급이면 데스크다.
                    kind = classify_kind(person.role_label, fallback=REPORTER)
                    result.records.append(
                        _make_record(
                            person,
                            current_outlet,
                            kind,
                            slot_hint="산업부장" if kind == DESK else None,
                            dept_hint=reference.dept_of(dept) or dept,
                            sheet=sheet,
                            ref=f"{sheet}!R{row_idx}C{col_idx + 1}",
                        )
                    )

    return result


def _dedupe_adjacent(cells: list[str]) -> list[str]:
    out: list[str] = []
    for cell in cells:
        if out and cell and cell == out[-1]:
            continue
        out.append(cell)
    return out


def _split_dept(text: str) -> tuple[str | None, str]:
    """`테크부 : 전수용 부장 010-…` → ('테크부', '전수용 부장 010-…')."""
    parts = _DEPT_SPLIT_RE.split(text, maxsplit=1)
    if len(parts) == 2 and parts[0] and len(parts[0]) <= 12:
        return parts[0], parts[1]
    return None, text


# ── 유형 자동 판별 ──────────────────────────────────────────────────────────

def detect_kind(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return "combined_docx"
    if suffix not in {".xlsx", ".xlsm"}:
        raise ValueError(f"지원하지 않는 파일 형식입니다: {suffix} (xlsx/docx만 가능)")

    with open_workbook(path) as workbook:
        for sheet in workbook.worksheets:
            kind = detect_sheet_kind(sheet)
            if kind:
                return kind
    raise ValueError(
        "파일 구조를 인식하지 못했습니다. "
        "'매체명'과 직책 열이 있는 데스크 표, 또는 '매체/이름/직급/전화번호' 열이 있는 "
        "출입기자 표여야 합니다."
    )


PARSERS = {
    "desk_matrix": parse_xlsx,
    "reporter_list": parse_xlsx,
    "combined_docx": parse_combined_docx,
}


def detect_and_parse(path: str | Path) -> ParseResult:
    path = Path(path)
    kind = detect_kind(path)   # 지원하지 않는 파일이면 여기서 걸러진다
    result = PARSERS[kind](path)
    if kind == "combined_docx":
        result.file_kind = kind
    if result.as_of is None:
        result.add_warning("기준일을 찾지 못했습니다. 업로드 화면에서 직접 지정해 주세요.")
    return result

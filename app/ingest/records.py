"""파서가 공통으로 내놓는 중간 레코드."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

DESK = "desk"          # 데스크(간부) 정보
REPORTER = "reporter"  # 출입기자 정보


@dataclass
class ContactRecord:
    """정규화된 '한 사람의 한 자리' 레코드."""

    outlet_raw: str
    outlet: str
    category: str
    outlet_known: bool

    name: str
    raw_name: str
    phone: str | None
    raw_phone: str | None

    kind: str                      # DESK | REPORTER
    role_label: str | None         # 원문 직책 ("산업1부장", "차장")
    role_slot: str | None          # 표준 슬롯 ("산업부장") — 매트릭스 열 배치용
    rank: str                      # 경영/국장/부장/차장/기자
    rank_order: int
    dept: str | None               # 산업부/테크부/IT과학부 …
    note: str | None
    concurrent: bool

    source_sheet: str
    source_ref: str                # 시트!셀 또는 표/행 좌표
    source_text: str
    warnings: list[str] = field(default_factory=list)

    def identity(self) -> tuple:
        """같은 파일 안의 중복 제거 키."""
        return (self.outlet, self.name, self.phone, self.kind, self.role_slot, self.dept)


@dataclass
class ParseResult:
    records: list[ContactRecord] = field(default_factory=list)
    as_of: date | None = None
    file_kind: str = ""
    sheets: list[str] = field(default_factory=list)
    # 시트별로 어떤 구조로 읽었고 몇 명을 얻었는지. 누락된 탭을 눈으로 확인하기 위한 것.
    sheet_kinds: dict[str, str] = field(default_factory=dict)
    sheet_counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)

    def add_warning(self, message: str) -> None:
        if message not in self.warnings:
            self.warnings.append(message)


_FILENAME_DATE_RE = re.compile(r"\(?\s*(\d{2})\s*[-.]\s*(\d{2})(\d{2})\s*\)?")
_CONTENT_DATE_RE = re.compile(r"'?(\d{2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})")


def as_of_from_filename(filename: str) -> date | None:
    """`(26-0803) …` → 2026-08-03."""
    match = _FILENAME_DATE_RE.search(filename)
    if not match:
        return None
    year, month, day = (int(g) for g in match.groups())
    try:
        return date(2000 + year, month, day)
    except ValueError:
        return None


def as_of_from_text(text: str) -> date | None:
    """`'26.6.9일 기준`, `주요 데스크 현황(26.7.31)` → date."""
    for match in _CONTENT_DATE_RE.finditer(text or ""):
        year, month, day = (int(g) for g in match.groups())
        try:
            return date(2000 + year, month, day)
        except ValueError:
            continue
    return None

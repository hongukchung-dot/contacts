"""원본 셀 텍스트를 사람 단위 레코드로 쪼개고 정규화한다.

원본 주소록은 한 칸에 여러 명이 들어가고, 이름은 자간 공백이 있으며,
직책은 괄호 안팎에 자유 서술로 붙는다. 예)

    "정 욱(산업부) 010-4379-2814 고 재 만(테크부) 010-9018-9028"
    "이성규 부국장 경제산업담당 010-4661-7640"
    "김성민 차장 010-9960-2381최인준 차장 010-4750-2723"   (docx, 구분자 없음)

전화번호는 이 데이터에서 유일하게 형태가 일정한 토큰이므로,
**전화번호를 기준으로 자르는 것**을 파싱의 축으로 삼는다.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# ── 전화번호 ────────────────────────────────────────────────────────────────
# 앞자리 0이 누락된 표기(`10-4633-3750`)가 원본에 실제로 존재하므로 0을 선택적으로 둔다.
PHONE_RE = re.compile(r"0?1[016-9][-.\s]?\d{3,4}[-.\s]?\d{4}")

# 값이 비어 있음을 뜻하는 표기들
EMPTY_MARKS = {"", "-", "--", "―", "–", "없음", "n/a", "na", "공석", "미정"}

# 이름 뒤에 붙어 직책으로 읽어야 하는 토큰들 (괄호가 없는 경우 분리 기준)
ROLE_TOKENS = (
    "회장", "부회장", "사장", "대표이사", "대표", "발행인", "편집인",
    "주필", "논설주간", "논설실장", "수석논설위원", "논설위원",
    "편집국장", "국장", "부국장", "본부장", "부문장", "실장",
    "에디터", "디렉터", "매니징에디터",
    "부장", "차장", "팀장", "기자", "위원", "특파원",
)
ROLE_TOKEN_RE = re.compile("(" + "|".join(sorted(ROLE_TOKENS, key=len, reverse=True)) + ")")

# 직책 앞에 붙는 겸직 표기
CONCURRENT_RE = re.compile(r"^(겸|兼)\s*")

_PAREN_RE = re.compile(r"[（(]\s*([^)）]*?)\s*[)）]")
_WS_RE = re.compile(r"\s+")


@dataclass
class ParsedPerson:
    """셀 하나에서 뽑아낸 인물 1명."""

    name: str                      # 정규화된 성명 (공백 제거)
    raw_name: str                  # 원본 표기 (자간 공백 포함)
    phone: str | None              # 정규화된 번호 (숫자 11자리) 또는 None
    raw_phone: str | None          # 원본 번호 표기
    role_label: str | None = None  # 원문 직책 문자열 ("산업1부장", "겸 경제부장" …)
    note: str | None = None        # 담당 분야 등 부가 표기 ("(재계)", "(전자)")
    concurrent: bool = False       # 겸직 여부
    source_text: str = ""          # 이 인물이 뽑혀 나온 원본 조각 (추적용)
    warnings: list[str] = field(default_factory=list)


# ── 기본 정규화 ─────────────────────────────────────────────────────────────

def clean_text(value: object) -> str:
    """셀 값을 문자열로 만들고 유니코드/공백을 정리한다."""
    if value is None:
        return ""
    text = str(value)
    text = unicodedata.normalize("NFKC", text)
    text = text.replace(" ", " ").replace("﻿", "")
    # 줄바꿈은 사람 사이의 구분자 역할을 하므로 공백 하나로 바꿔 둔다.
    text = text.replace("\r", "\n")
    text = re.sub(r"\n+", " ", text)
    return _WS_RE.sub(" ", text).strip()


def is_empty(value: object) -> bool:
    return clean_text(value).lower() in EMPTY_MARKS


def normalize_phone(raw: str) -> tuple[str | None, list[str]]:
    """전화번호를 숫자 11자리로 정규화한다.

    Returns: (정규화된 번호 또는 None, 경고 목록)
    """
    warnings: list[str] = []
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None, warnings

    if len(digits) == 10 and digits.startswith("1"):
        # `10-4633-3750` 처럼 맨 앞 0이 빠진 표기. 원본에 실제로 존재한다.
        digits = "0" + digits
        warnings.append("앞자리 0 누락 추정 → 0을 보정함")

    if len(digits) == 10 and digits.startswith("01"):
        # 011/016/017/018/019 구형 번호
        return digits, warnings

    if len(digits) != 11 or not digits.startswith("01"):
        warnings.append(f"휴대폰 번호 형식이 아님: {raw!r}")
        return None, warnings

    return digits, warnings


def format_phone(digits: str | None) -> str:
    """저장된 숫자열을 화면 표기(010-1234-5678)로 되돌린다."""
    if not digits:
        return ""
    if len(digits) == 11:
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return digits


def normalize_name(raw: str) -> str:
    """`이 길 성` → `이길성`. 이름 안의 공백만 제거한다."""
    return re.sub(r"\s+", "", clean_text(raw))


def normalize_key(text: str) -> str:
    """매체명·검색어 대조용 키. 공백/기호 제거 + 소문자."""
    text = unicodedata.normalize("NFKC", str(text or ""))
    text = re.sub(r"[\s\-_·・.,'\"()（）\[\]]+", "", text)
    return text.lower()


# ── 셀 → 인물 목록 ──────────────────────────────────────────────────────────

def _split_name_and_role(blob: str) -> tuple[str, str | None, bool]:
    """이름+직책이 섞인 조각에서 (이름, 직책, 겸직여부)를 분리한다."""
    blob = clean_text(blob)
    if not blob:
        return "", None, False

    concurrent = bool(CONCURRENT_RE.match(blob))
    blob = CONCURRENT_RE.sub("", blob)

    tokens = blob.split(" ")

    # 1) 앞쪽에 연속으로 나오는 1글자 토큰은 자간 띄어쓴 성명이다. (`이 길 성`)
    lead: list[str] = []
    idx = 0
    while idx < len(tokens) and len(tokens[idx]) == 1:
        lead.append(tokens[idx])
        idx += 1

    if len(lead) >= 2:
        name = "".join(lead)
        rest = tokens[idx:]
    elif tokens:
        # 2) 붙여 쓴 성명. 단, `김현수산업1부장` 처럼 직책이 붙어버린 경우를 잘라낸다.
        head = tokens[0]
        match = ROLE_TOKEN_RE.search(head)
        if match and match.start() >= 2:
            name = head[: match.start()]
            rest = [head[match.start():]] + tokens[1:]
        else:
            name = head
            rest = tokens[1:]
    else:
        return "", None, concurrent

    role = " ".join(t for t in rest if t).strip() or None
    if role:
        concurrent = concurrent or bool(CONCURRENT_RE.match(role))
        role = CONCURRENT_RE.sub("", role).strip() or None
    return name, role, concurrent


def parse_people(cell: object) -> list[ParsedPerson]:
    """셀 텍스트 하나에서 인물 목록을 뽑아낸다.

    전화번호 위치를 기준으로 조각을 나눈다. 번호가 하나도 없으면
    셀 전체를 이름/직책으로 보고 번호 없는 인물 1명으로 처리한다.
    """
    text = clean_text(cell)
    if not text or text.lower() in EMPTY_MARKS:
        return []

    matches = list(PHONE_RE.finditer(text))
    people: list[ParsedPerson] = []

    if not matches:
        person = _build_person(text, None, text)
        return [person] if person else []

    cursor = 0
    for match in matches:
        blob = text[cursor : match.start()]
        raw_phone = match.group(0)
        segment = text[cursor : match.end()].strip()
        person = _build_person(blob, raw_phone, segment)
        if person:
            people.append(person)
        elif people:
            # 이름 없이 번호만 이어지는 경우 → 직전 인물의 추가 번호로 보고 경고만 남긴다.
            people[-1].warnings.append(f"이름 없는 추가 번호 발견: {raw_phone}")
        cursor = match.end()

    # 마지막 번호 뒤에 이름이 더 남아 있으면(번호 미기재 인물) 함께 살린다.
    tail = text[cursor:].strip(" ,/·")
    if tail and re.search(r"[가-힣A-Za-z]", tail):
        person = _build_person(tail, None, tail)
        if person:
            people.append(person)

    return people


def _build_person(blob: str, raw_phone: str | None, source_text: str) -> ParsedPerson | None:
    blob = clean_text(blob).strip(" ,/·")
    if not blob and not raw_phone:
        return None

    warnings: list[str] = []

    # 괄호 안 내용은 직책 또는 담당 분야다.
    parens = _PAREN_RE.findall(blob)
    stripped = _PAREN_RE.sub(" ", blob)

    name, trailing_role, concurrent = _split_name_and_role(stripped)
    if not name:
        return None
    if not re.search(r"[가-힣A-Za-z]", name):
        return None

    role_parts: list[str] = []
    notes: list[str] = []
    for item in parens:
        item = clean_text(item)
        if not item:
            continue
        if CONCURRENT_RE.match(item):
            concurrent = True
            item = CONCURRENT_RE.sub("", item).strip()
        if ROLE_TOKEN_RE.search(item) or "담당" in item or "부" in item:
            role_parts.append(item)
        else:
            notes.append(item)
    if trailing_role:
        role_parts.append(trailing_role)

    phone, phone_warnings = normalize_phone(raw_phone) if raw_phone else (None, [])
    warnings.extend(phone_warnings)
    if raw_phone and not phone:
        warnings.append("번호를 해석하지 못해 비워 둠")

    normalized_name = normalize_name(name)
    if len(normalized_name) > 20:
        # 이 정도면 성명이 아니라 파싱이 어긋난 것이다. 억지로 넣지 않고 미해석으로 넘긴다.
        return None
    if len(normalized_name) > 5:
        warnings.append(f"성명이 비정상적으로 김: {normalized_name!r} — 확인 필요")

    return ParsedPerson(
        name=normalized_name,
        raw_name=name,
        phone=phone,
        raw_phone=raw_phone,
        role_label=" ".join(role_parts).strip() or None,
        note=" ".join(notes).strip() or None,
        concurrent=concurrent,
        source_text=source_text,
        warnings=warnings,
    )


# 화면 표기용 ─────────────────────────────────────────────────────────────────
GENERIC_ROLES = {"부장", "차장", "팀장", "기자", "국장", "부국장", "에디터", "위원", "특파원"}


def role_display(role_label: str | None, role_slot: str | None, dept: str | None) -> str:
    """`부장` + 부서 `산업부` → `산업부장` 처럼 읽기 좋은 직책 문자열을 만든다."""
    if role_label and role_label not in GENERIC_ROLES:
        return role_label
    if role_label == "부장" and dept and dept.endswith("부"):
        return dept[:-1] + role_label
    return role_label or role_slot or ""

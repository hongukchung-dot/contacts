"""매체·직책 참조 사전 로딩 및 매칭.

`data/reference/*.yml` 을 읽어 원본 표기(별칭, 자유 서술 직책)를
표준 코드로 바꿔 준다. 사전에 없는 값이 나와도 파싱을 실패시키지 않고
`기타`로 통과시킨 뒤 리포트에 남긴다 — 원본 파일이 계속 바뀌기 때문이다.
"""

from __future__ import annotations

import functools
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .normalize import normalize_key

# 매체명 뒤에 붙는 부서 꼬리표. `조선일보(산업부)` → `조선일보`
_DEPT_SUFFIX_RE = re.compile(
    r"(산업\d*부|테크부|it과학부|ict부|경제부|사회부|정치부|편집국|뉴스룸|본사|서울본사)$"
)

REFERENCE_DIR = Path(__file__).resolve().parents[2] / "data" / "reference"

UNCLASSIFIED = "기타"


@dataclass(frozen=True)
class OutletRef:
    name: str
    category: str
    order: int


@dataclass(frozen=True)
class SlotRef:
    code: str
    label: str
    order: int


class Reference:
    """참조 사전 전체. 프로세스당 한 번만 로드한다."""

    def __init__(self, directory: Path | None = None) -> None:
        directory = directory or REFERENCE_DIR
        outlets_raw = yaml.safe_load((directory / "outlets.yml").read_text("utf-8"))
        roles_raw = yaml.safe_load((directory / "roles.yml").read_text("utf-8"))

        self.outlets: list[OutletRef] = []
        self._outlet_by_key: dict[str, OutletRef] = {}
        for item in outlets_raw["outlets"]:
            outlet = OutletRef(item["name"], item["category"], int(item["order"]))
            self.outlets.append(outlet)
            for alias in [item["name"], *(item.get("aliases") or [])]:
                self._outlet_by_key[normalize_key(alias)] = outlet

        self.slots: list[SlotRef] = []
        self._slot_by_header: dict[str, str] = {}
        self._slot_patterns: list[tuple[str, str]] = []  # (정규화 패턴, slot code)
        for item in roles_raw["slots"]:
            slot = SlotRef(item["code"], item["label"], int(item["order"]))
            self.slots.append(slot)
            for header in item.get("headers") or []:
                self._slot_by_header[normalize_key(header)] = slot.code
            for pattern in [item["code"], *(item.get("patterns") or [])]:
                self._slot_patterns.append((normalize_key(pattern), slot.code))
        # 긴 패턴을 먼저 대조해야 `산업1부장`이 `부장`보다 우선한다.
        self._slot_patterns.sort(key=lambda pair: len(pair[0]), reverse=True)

        self._rank_patterns: list[tuple[str, str, int]] = []
        for item in roles_raw["ranks"]:
            for pattern in item.get("patterns") or []:
                self._rank_patterns.append((normalize_key(pattern), item["code"], int(item["order"])))
        self._rank_patterns.sort(key=lambda triple: len(triple[0]), reverse=True)

        self._dept_by_key: dict[str, str] = {}
        for canonical, variants in (roles_raw.get("depts") or {}).items():
            for variant in [canonical, *variants]:
                self._dept_by_key[normalize_key(variant)] = canonical

    # ── 매체 ────────────────────────────────────────────────────────────
    def match_outlet(self, raw: str) -> OutletRef | None:
        """정확 일치 → 접두/포함 일치 순으로 매체를 찾는다."""
        key = normalize_key(raw)
        if not key:
            return None
        if key in self._outlet_by_key:
            return self._outlet_by_key[key]

        # `조선일보(산업부)` 처럼 부서 꼬리표가 붙은 경우만 떼어 내고 다시 본다.
        stripped = _DEPT_SUFFIX_RE.sub("", key)
        if stripped != key and stripped in self._outlet_by_key:
            return self._outlet_by_key[stripped]

        # 여기서 앞부분만 같다고 이어 붙이면 안 된다.
        # `조선비즈`가 `조선일보`로, `연합뉴스TV`가 `연합뉴스`로 합쳐지는 사고가 실제로 났다.
        # 모르는 매체는 새 매체로 등록하고 리포트에 남긴다. 잘못 합치는 것보다 훨씬 낫다.
        return None

    def near_miss(self, raw: str) -> "OutletRef | None":
        """사전에 없지만 등록된 매체와 앞부분이 겹치는 경우를 알려 준다.

        `조선비즈` ↔ `조선일보` 처럼 헷갈리기 쉬운 매체를 사람이 눈으로 확인하도록
        경고를 붙이기 위한 것이다. 매칭에는 쓰지 않는다.
        """
        key = normalize_key(raw)
        if not key or key in self._outlet_by_key:
            return None
        best: OutletRef | None = None
        best_len = 0
        for alias_key, outlet in self._outlet_by_key.items():
            if len(alias_key) < 2:
                continue
            if key.startswith(alias_key) and len(alias_key) > best_len:
                best, best_len = outlet, len(alias_key)
        return best

    def outlet_or_placeholder(self, raw: str) -> tuple[str, str, int, bool]:
        """(정식명, 분류, 정렬순서, 사전등록여부)."""
        outlet = self.match_outlet(raw)
        if outlet:
            return outlet.name, outlet.category, outlet.order, True
        from .normalize import clean_text

        return clean_text(raw), UNCLASSIFIED, 9000, False

    # ── 직책 ────────────────────────────────────────────────────────────
    def slot_from_header(self, header: str) -> str | None:
        """엑셀 열 머리글 → 표준 슬롯 코드."""
        key = normalize_key(header)
        if not key:
            return None
        if key in self._slot_by_header:
            return self._slot_by_header[key]
        # `사회부장 ①` 처럼 번호가 붙은 머리글
        stripped = key.rstrip("①②③④⑤123456789")
        return self._slot_by_header.get(stripped)

    def slot_from_label(self, label: str | None) -> str | None:
        """자유 서술 직책 → 표준 슬롯 코드. (`산업1부장` → `산업부장`)"""
        key = normalize_key(label or "")
        if not key:
            return None
        for pattern, code in self._slot_patterns:
            if pattern and pattern in key:
                return code
        return None

    def rank_of(self, label: str | None) -> tuple[str, int]:
        """직책 문자열에서 직급 계층을 판정한다. 기본값은 기자."""
        key = normalize_key(label or "")
        for pattern, code, order in self._rank_patterns:
            if pattern and pattern in key:
                return code, order
        return "기자", 50

    def dept_of(self, label: str | None) -> str | None:
        key = normalize_key(label or "")
        if not key:
            return None
        if key in self._dept_by_key:
            return self._dept_by_key[key]
        for variant_key, canonical in sorted(
            self._dept_by_key.items(), key=lambda pair: len(pair[0]), reverse=True
        ):
            if len(variant_key) >= 2 and variant_key in key:
                return canonical
        return None

    @property
    def slot_codes(self) -> list[str]:
        return [slot.code for slot in sorted(self.slots, key=lambda s: s.order)]


@functools.lru_cache(maxsize=1)
def get_reference() -> Reference:
    return Reference()

"""매체·직책 참조 사전 로딩 및 매칭.

`data/reference/*.yml` 을 읽어 원본 표기(별칭, 자유 서술 직책)를
표준 코드로 바꿔 준다. 사전에 없는 값이 나와도 파싱을 실패시키지 않고
`미분류`로 통과시킨 뒤 리포트에 남긴다 — 원본 파일이 계속 바뀌기 때문이다.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path

import yaml

from .normalize import normalize_key

REFERENCE_DIR = Path(__file__).resolve().parents[2] / "data" / "reference"

UNCLASSIFIED = "미분류"


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
        # `조선일보(테크부)` 같은 꼬리표가 붙은 경우
        candidates = [
            outlet
            for alias_key, outlet in self._outlet_by_key.items()
            if len(alias_key) >= 2 and (key.startswith(alias_key) or alias_key.startswith(key))
        ]
        if candidates:
            # 가장 긴 이름과 맞은 것을 고른다 (`한국` vs `한국경제`)
            return max(candidates, key=lambda o: len(normalize_key(o.name)))
        return None

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

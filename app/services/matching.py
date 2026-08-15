"""인물 동일성 판정과 오타 의심 탐지.

전화번호가 가장 안정적인 식별자이지만 원본에 번호 재사용/복붙 오류가 실제로 있으므로
(같은 번호에 다른 사람이 적힌 사례가 확인됨) 단독 키로 쓰지 않고 우선순위 매칭을 한다.
"""

from __future__ import annotations


def digit_diff_positions(left: str | None, right: str | None) -> int | None:
    """길이가 같은 두 번호에서 다른 자리 수를 센다. 길이가 다르면 None."""
    if not left or not right or len(left) != len(right):
        return None
    return sum(1 for a, b in zip(left, right) if a != b)


def is_typo_suspect(old: str | None, new: str | None) -> tuple[bool, str | None]:
    """번호 변경이 실제 변경이 아니라 오타로 의심되는지 판정한다.

    실제 원본에서 확인된 사례:
      · 서울경제 손철  …8199 ↔ …8119 (두 자리 자리바꿈)
      · 이데일리 김정남 …9879 ↔ …9876 (한 자리)
    번호를 정말 바꾸면 보통 여러 자리가 한꺼번에 달라지므로,
    1~2자리만 다르면 오타로 보고 승인 대상으로 돌린다.
    """
    if not old or not new or old == new:
        return False, None

    diff = digit_diff_positions(old, new)
    if diff is None:
        return False, None
    if diff == 1:
        return True, "번호가 한 자리만 다릅니다 — 오타 가능성"
    if diff == 2:
        # 인접 두 자리가 뒤바뀐 형태인지 확인한다.
        positions = [i for i, (a, b) in enumerate(zip(old, new)) if a != b]
        i, j = positions
        if j == i + 1 and old[i] == new[j] and old[j] == new[i]:
            return True, "인접한 두 자리가 뒤바뀌었습니다 — 오타 가능성"
        return True, "번호가 두 자리만 다릅니다 — 오타 가능성"
    return False, None


def match_person(
    *,
    name_key: str,
    phone: str | None,
    outlet_id: int,
    by_phone: dict[str, int],
    by_outlet_name: dict[tuple[int, str], int],
    by_name: dict[str, list[int]],
) -> tuple[int | None, str]:
    """(person_id, 매칭근거). 찾지 못하면 (None, 'new').

    우선순위
      1) 번호 일치            — 가장 강한 근거
      2) 같은 매체 + 이름 일치 — 번호가 바뀐 경우
      3) 전체에서 이름이 유일  — 매체를 옮긴 경우
    """
    if phone and phone in by_phone:
        return by_phone[phone], "phone"

    key = (outlet_id, name_key)
    if key in by_outlet_name:
        return by_outlet_name[key], "outlet+name"

    candidates = by_name.get(name_key) or []
    if len(candidates) == 1:
        return candidates[0], "name"

    return None, "new"

"""인물 동일성 판정과 오타 의심 탐지.

**매체를 옮겼다고 보려면 이름과 번호가 모두 같아야 한다.**
이름만 같다고 같은 사람으로 묶으면 동명이인이 통째로 다른 회사로 옮겨간 것처럼
기록된다. 출입기자 명단처럼 사람이 많은 파일에서는 이런 오판이 대량으로 생긴다.

정리하면 이렇다.

| 상황 | 판정 | 이유 |
|------|------|------|
| 같은 매체 + 이름 일치 | 같은 사람 | 한 매체 안에 동명이인은 드물고, 번호는 바뀔 수 있다 |
| 이름 일치 + 번호 일치 | 같은 사람 (매체 이동) | 두 값이 모두 맞으면 이동으로 볼 만하다 |
| 번호만 일치, 이름 다름 | **다른 사람** | 원본에 번호 재사용·복붙 오류가 실제로 있다 |
| 이름만 일치, 번호 다름 | **다른 사람** | 동명이인 |
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
    by_outlet_name: dict[tuple[int, str], int],
    by_phone_name: dict[tuple[str, str], int],
    by_phone: dict[str, int] | None = None,
) -> tuple[int | None, str]:
    """(person_id, 매칭 근거). 찾지 못하면 (None, 'new').

    `by_phone` 은 판정에 쓰지 않고, "번호는 같은데 이름이 다른" 경우를
    호출부에 알려 주기 위해서만 본다(`phone-name-mismatch`).
    """
    key = (outlet_id, name_key)
    if key in by_outlet_name:
        return by_outlet_name[key], "outlet+name"

    if phone:
        moved = by_phone_name.get((phone, name_key))
        if moved is not None:
            return moved, "phone+name"
        if by_phone and phone in by_phone:
            # 번호는 이미 쓰이고 있는데 이름이 다르다.
            # 담당 교체나 오기일 수 있으므로 같은 사람으로 묶지 않는다.
            return None, "phone-name-mismatch"

    return None, "new"

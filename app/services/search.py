"""검색.

두 단계로 답한다.

1. **규칙 파서** — "매경 산업부장이 누구지?" 처럼 매체 + 직책이 들어 있으면
   문장에서 둘을 뽑아 바로 답을 만든다. 외부 API 없이 즉시 응답한다.
2. **유사도 검색** — 규칙에 걸리지 않으면 pg_trgm 으로 이름·매체·직책을 통합 검색한다.
   `매일경졔`, `이길정` 같은 오타도 후보로 잡힌다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select, text
from sqlalchemy.orm import Session, joinedload

from ..ingest.normalize import normalize_key, normalize_phone, role_display
from ..ingest.reference import get_reference
from ..models import Assignment, Outlet, OutletAlias, Person

# 질의에서 걷어낼 조사·군말
_NOISE = [
    "이누구지", "가누구지", "이누구야", "가누구야", "은누구", "는누구", "이누구", "가누구",
    "누구지", "누구야", "누구인가", "누구", "알려줘", "알려주세요", "찾아줘", "검색",
    "연락처", "전화번호", "번호", "좀", "요", "?", "!",
]


@dataclass
class Hit:
    person_id: int
    person_name: str
    outlet_id: int
    outlet_name: str
    role_label: str | None
    role_slot: str | None
    dept: str | None
    kind: str
    phone: str | None
    email: str | None = None
    score: float = 0.0

    @property
    def role_text(self) -> str:
        return role_display(self.role_label, self.role_slot, self.dept)


@dataclass
class SearchResult:
    query: str
    answer: Hit | None = None          # 확정적으로 하나로 좁혀진 경우
    hits: list[Hit] = field(default_factory=list)
    interpreted: str | None = None     # "매일경제 · 산업부장" 처럼 해석 결과를 보여 준다
    outlet_id: int | None = None
    note: str | None = None


def search(session: Session, query: str, *, limit: int = 40) -> SearchResult:
    raw = (query or "").strip()
    if not raw:
        return SearchResult(query=raw)

    result = _by_phone(session, raw)
    if result:
        return result

    outlet, remainder = _extract_outlet(session, raw)
    slot = _extract_slot(remainder)

    if outlet and slot:
        hits = _query(session, outlet_id=outlet.id, role_slot=slot, limit=limit)
        interpreted = f"{outlet.name} · {slot}"
        if hits:
            return SearchResult(
                query=raw,
                answer=hits[0] if len(hits) == 1 else None,
                hits=hits,
                interpreted=interpreted,
                outlet_id=outlet.id,
            )
        return SearchResult(
            query=raw,
            interpreted=interpreted,
            outlet_id=outlet.id,
            note=f"{outlet.name}의 {slot} 정보가 아직 등록돼 있지 않습니다.",
        )

    if outlet and not remainder.strip():
        hits = _query(session, outlet_id=outlet.id, limit=limit)
        return SearchResult(
            query=raw, hits=hits, interpreted=outlet.name, outlet_id=outlet.id
        )

    if slot and not outlet:
        hits = _query(session, role_slot=slot, limit=limit)
        return SearchResult(query=raw, hits=hits, interpreted=f"전체 매체 · {slot}")

    return _fuzzy(session, raw, outlet_id=outlet.id if outlet else None, limit=limit)


# ── 1) 번호로 찾기 ──────────────────────────────────────────────────────────

def _by_phone(session: Session, raw: str) -> SearchResult | None:
    digits = re.sub(r"\D", "", raw)
    if len(digits) < 8:
        return None
    phone, _ = normalize_phone(digits)
    target = phone or digits
    people = session.scalars(
        select(Person).where(Person.phone.like(f"%{target[-8:]}%"))
    ).all()
    if not people:
        return None
    hits: list[Hit] = []
    for person in people:
        hits.extend(_query(session, person_id=person.id, limit=10))
    return SearchResult(
        query=raw,
        answer=hits[0] if len(hits) == 1 else None,
        hits=hits,
        interpreted=f"번호 {target[-8:]} 로 조회",
    )


# ── 2) 문장에서 매체 / 직책 뽑기 ────────────────────────────────────────────

def _extract_outlet(session: Session, raw: str) -> tuple[Outlet | None, str]:
    """질의 안에 들어 있는 매체 별칭 중 가장 긴 것을 찾는다."""
    key = normalize_key(_strip_noise(raw))
    if not key:
        return None, raw

    rows = session.execute(
        select(OutletAlias.alias_key, OutletAlias.outlet_id).order_by(
            OutletAlias.alias_key
        )
    ).all()
    rows += session.execute(select(Outlet.name_key, Outlet.id)).all()

    best_key = ""
    best_id: int | None = None
    for alias_key, outlet_id in rows:
        if not alias_key or len(alias_key) < 2:
            continue
        if alias_key in key and len(alias_key) > len(best_key):
            best_key, best_id = alias_key, outlet_id

    if best_id is None:
        return None, raw

    outlet = session.get(Outlet, best_id)
    remainder = key.replace(best_key, " ", 1)
    return outlet, remainder


def _extract_slot(remainder: str) -> str | None:
    reference = get_reference()
    cleaned = normalize_key(_strip_noise(remainder))
    if not cleaned:
        return None
    return reference.slot_from_label(cleaned)


def _strip_noise(text_value: str) -> str:
    out = text_value
    for token in _NOISE:
        out = out.replace(token, " ")
    return out


# ── 3) 구조화 조회 ──────────────────────────────────────────────────────────

def _query(
    session: Session,
    *,
    outlet_id: int | None = None,
    role_slot: str | None = None,
    person_id: int | None = None,
    limit: int = 40,
) -> list[Hit]:
    stmt = (
        select(Assignment)
        .options(joinedload(Assignment.person), joinedload(Assignment.outlet))
        .join(Outlet)
        .where(Assignment.valid_to.is_(None))
        .order_by(Assignment.rank_order, Outlet.sort_order, Assignment.id)
        .limit(limit)
    )
    if outlet_id:
        stmt = stmt.where(Assignment.outlet_id == outlet_id)
    if role_slot:
        stmt = stmt.where(Assignment.role_slot == role_slot, Assignment.kind == "desk")
    if person_id:
        stmt = stmt.where(Assignment.person_id == person_id)

    return [_hit(a) for a in session.scalars(stmt).unique().all()]


def _hit(assignment: Assignment, score: float = 1.0) -> Hit:
    return Hit(
        person_id=assignment.person_id,
        person_name=assignment.person.name,
        outlet_id=assignment.outlet_id,
        outlet_name=assignment.outlet.name,
        role_label=assignment.role_label,
        role_slot=assignment.role_slot,
        dept=assignment.dept,
        kind=assignment.kind,
        phone=assignment.person.phone,
        email=assignment.person.email,
        score=score,
    )


# ── 4) 유사도 폴백 ──────────────────────────────────────────────────────────

_FUZZY_SQL = text(
    """
    SELECT a.id,
           GREATEST(
               similarity(p.name, :q),
               similarity(o.name, :q),
               similarity(COALESCE(a.role_label, ''), :q),
               similarity(COALESCE(a.dept, ''), :q)
           ) AS score
      FROM assignment a
      JOIN person p ON p.id = a.person_id
      JOIN outlet o ON o.id = a.outlet_id
     WHERE a.valid_to IS NULL
       AND (CAST(:outlet_id AS integer) IS NULL OR a.outlet_id = CAST(:outlet_id AS integer))
       AND (
             p.name ILIKE :like
          OR COALESCE(p.email, '') ILIKE :like
          OR COALESCE(p.memo, '') ILIKE :like
          OR o.name ILIKE :like
          OR COALESCE(a.role_label, '') ILIKE :like
          OR COALESCE(a.dept, '') ILIKE :like
          OR p.name % :q
          OR o.name % :q
          OR COALESCE(a.role_label, '') % :q
       )
     ORDER BY score DESC, a.rank_order, a.id
     LIMIT :limit
    """
)


def _fuzzy(session: Session, raw: str, *, outlet_id: int | None, limit: int) -> SearchResult:
    term = _strip_noise(raw).strip()
    if not term:
        return SearchResult(query=raw)

    rows = session.execute(
        _FUZZY_SQL,
        {"q": term, "like": f"%{term}%", "outlet_id": outlet_id, "limit": limit},
    ).all()
    if not rows:
        return SearchResult(
            query=raw,
            note="일치하는 결과가 없습니다. 매체명이나 이름 일부로 다시 찾아보세요.",
        )

    ids = [row[0] for row in rows]
    scores = {row[0]: float(row[1] or 0) for row in rows}
    assignments = session.scalars(
        select(Assignment)
        .options(joinedload(Assignment.person), joinedload(Assignment.outlet))
        .where(Assignment.id.in_(ids))
    ).unique().all()
    hits = sorted(
        (_hit(a, scores.get(a.id, 0)) for a in assignments),
        key=lambda h: -h.score,
    )
    return SearchResult(
        query=raw,
        answer=hits[0] if len(hits) == 1 else None,
        hits=hits,
        outlet_id=outlet_id,
    )

"""검색 테스트 — '매경 산업부장이 누구지?' 류의 자연어 질의를 확인한다."""

from __future__ import annotations

import pytest

from app.services.ingest import apply_change_set, stage_file
from app.services.search import search
from tests.conftest import FIXTURES, requires_db

pytestmark = requires_db


@pytest.fixture()
def seeded(db_session):
    for name in ("sample_desk_matrix.xlsx", "sample_reporter_list.xlsx", "sample_combined.docx"):
        change_set = stage_file(db_session, FIXTURES / name, filename=name)
        db_session.flush()
        apply_change_set(db_session, change_set, applied_by_id=None)
    db_session.commit()
    return db_session


def names(result) -> set[str]:
    return {hit.person_name for hit in result.hits}


class TestNaturalLanguage:
    def test_outlet_alias_plus_role(self, seeded):
        """'매경' 별칭 + '산업부장' 을 알아들어야 한다."""
        result = search(seeded, "매경 산업부장이 누구지?")
        assert result.interpreted == "매일경제 · 산업부장"
        assert "정욱" in names(result)

    def test_question_particles_are_stripped(self, seeded):
        for query in ("매경 산업부장", "매경 산업부장은 누구야", "매경 산업부장 연락처 알려줘"):
            result = search(seeded, query)
            assert "정욱" in names(result), query

    def test_full_outlet_name(self, seeded):
        result = search(seeded, "매일경제 산업부장")
        assert "정욱" in names(result)

    def test_role_variant_maps_to_slot(self, seeded):
        """'산업1부장' 도 산업부장 슬롯으로 해석된다."""
        result = search(seeded, "동아일보 산업1부장")
        assert "김현수" in names(result)

    def test_editor_in_chief(self, seeded):
        result = search(seeded, "조선일보 편집국장")
        assert "강경희" in names(result)

    def test_outlet_only_lists_everyone(self, seeded):
        result = search(seeded, "조선일보")
        assert result.interpreted == "조선일보"
        assert {"이길성", "전수용", "정한국"} <= names(result)

    def test_role_only_spans_all_outlets(self, seeded):
        result = search(seeded, "산업부장")
        assert result.interpreted == "전체 매체 · 산업부장"
        assert len({hit.outlet_name for hit in result.hits}) > 1

    def test_unknown_role_for_known_outlet_explains(self, seeded):
        result = search(seeded, "문화일보 법조팀장")
        assert result.hits == []
        assert "등록돼 있지 않습니다" in (result.note or "")


class TestPersonAndPhone:
    def test_name_lookup(self, seeded):
        result = search(seeded, "이길성")
        assert "이길성" in names(result)

    def test_phone_lookup(self, seeded):
        person_hit = search(seeded, "이길성").hits[0]
        assert person_hit.phone
        result = search(seeded, person_hit.phone)
        assert "이길성" in names(result)

    def test_partial_phone_lookup(self, seeded):
        result = search(seeded, "5271-0002")
        assert "이길성" in names(result)


class TestFuzzy:
    def test_typo_in_outlet_name_still_finds(self, seeded):
        """pg_trgm 폴백: '매일경졔' 처럼 오타가 나도 후보를 찾는다."""
        result = search(seeded, "매일경졔")
        assert result.hits
        assert any(hit.outlet_name == "매일경제" for hit in result.hits)

    def test_typo_in_person_name(self, seeded):
        result = search(seeded, "이길정")
        assert any(hit.person_name == "이길성" for hit in result.hits)

    def test_dept_search(self, seeded):
        result = search(seeded, "테크부")
        assert result.hits
        assert any(hit.dept == "테크부" for hit in result.hits)

    def test_no_match_gives_guidance(self, seeded):
        result = search(seeded, "존재하지않는이름xyz")
        assert result.hits == []
        assert result.note

    def test_empty_query(self, seeded):
        result = search(seeded, "   ")
        assert result.hits == [] and result.answer is None

"""파서 통합 테스트 — 실제 파일 레이아웃을 재현한 픽스처로 검증한다."""

from datetime import date
from pathlib import Path

import pytest

from app.ingest.parsers import detect_and_parse, detect_kind
from app.ingest.records import DESK, REPORTER

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture(scope="module", autouse=True)
def ensure_fixtures():
    from tests.make_fixtures import main

    if not (FIXTURES / "sample_desk_matrix.xlsx").exists():
        main()


def find(records, name, outlet=None):
    hits = [r for r in records if r.name == name and (outlet is None or r.outlet == outlet)]
    assert hits, f"{name} 레코드를 찾지 못함"
    return hits


# ── 데스크 매트릭스 xlsx ────────────────────────────────────────────────────

class TestDeskMatrix:
    @pytest.fixture(scope="class")
    def result(self):
        return detect_and_parse(FIXTURES / "sample_desk_matrix.xlsx")

    def test_detected_kind(self):
        assert detect_kind(FIXTURES / "sample_desk_matrix.xlsx") == "desk_matrix"

    def test_as_of_from_content(self, result):
        assert result.as_of == date(2026, 7, 31)

    def test_all_records_are_desk(self, result):
        assert result.records
        assert {r.kind for r in result.records} == {DESK}

    def test_outlet_alias_resolved(self, result):
        assert find(result.records, "이규성")[0].outlet == "아시아투데이"

    def test_leading_blank_column_ignored(self, result):
        """첫 열이 비어 있어도 매체명 열을 정확히 찾아야 한다."""
        assert find(result.records, "강경희")[0].outlet == "조선일보"

    def test_outlet_fill_down(self, result):
        """매체명이 없는 후속 행도 직전 매체에 귀속된다."""
        assert find(result.records, "전수용")[0].outlet == "조선일보"
        assert find(result.records, "신수정")[0].outlet == "동아일보"

    def test_column_maps_to_slot(self, result):
        assert find(result.records, "강경희")[0].role_slot == "편집국장"
        assert find(result.records, "방현철")[0].role_slot == "경제부장"

    def test_free_text_role_overrides_column_slot(self, result):
        """`전 수 용 (테크부장)` 은 산업부장 열에 있어도 테크부장으로 읽힌다."""
        record = find(result.records, "전수용")[0]
        assert record.role_label == "테크부장"
        assert record.role_slot == "산업부장"  # 매트릭스 배치는 산업부장 열
        assert record.dept == "테크부"

    def test_two_people_in_one_cell(self, result):
        maekyung = [r for r in result.records if r.outlet == "매일경제"]
        names = {r.name for r in maekyung}
        assert {"정욱", "고재만", "황인혁", "이호승"} <= names

    def test_concurrent_flag(self, result):
        assert find(result.records, "이규성")[0].concurrent is True

    def test_dash_marker_is_skipped(self, result):
        assert not [r for r in result.records if r.name == "-"]

    def test_role_without_parentheses(self, result):
        record = find(result.records, "이성규")[0]
        assert record.role_label == "부국장 경제산업담당"
        assert record.role_slot == "산업부장"
        assert record.rank == "국장"

    def test_no_unparsed_cells(self, result):
        assert result.unparsed == [], result.unparsed

    def test_every_record_has_phone(self, result):
        missing = [r.name for r in result.records if not r.phone]
        assert missing == [], missing


# ── 출입기자 목록 xlsx ──────────────────────────────────────────────────────

class TestReporterList:
    @pytest.fixture(scope="class")
    def result(self):
        return detect_and_parse(FIXTURES / "sample_reporter_list.xlsx")

    def test_detected_kind(self):
        assert detect_kind(FIXTURES / "sample_reporter_list.xlsx") == "reporter_list"

    def test_multiple_sheets(self, result):
        assert result.sheets == ["종합지", "경제지", "방송"]

    def test_as_of(self, result):
        assert result.as_of == date(2026, 6, 9)

    def test_rank_decides_kind_even_in_reporter_list(self, result):
        """출입기자 명단이라도 부장급 이상은 데스크다 (작은 매체는 이 파일에만 나온다)."""
        assert find(result.records, "이길성")[0].kind == DESK      # 산업부장
        assert find(result.records, "전수용")[0].kind == DESK      # 테크부장
        assert find(result.records, "정한국")[0].kind == REPORTER  # 차장
        assert find(result.records, "안별")[0].kind == REPORTER    # 직급 없음
        assert find(result.records, "권지혜")[0].kind == REPORTER  # 팀장(차장급)

    def test_outlet_fill_down_across_rows(self, result):
        assert find(result.records, "정한국")[0].outlet == "조선일보"
        assert find(result.records, "안별")[0].outlet == "조선일보"
        assert find(result.records, "이동훈")[0].outlet == "동아일보"

    def test_alias_expansion(self, result):
        """`조선` → `조선일보`, `동아` → `동아일보`, `국민` → `국민일보`."""
        assert {r.outlet for r in result.records if r.category == "종합지"} == {
            "조선일보",
            "동아일보",
            "국민일보",
        }

    def test_beat_note_extracted(self, result):
        record = find(result.records, "권지혜")[0]
        assert record.note == "재계"
        assert record.rank == "차장"

    def test_rank_classification(self, result):
        assert find(result.records, "이길성")[0].rank == "부장"
        assert find(result.records, "정한국")[0].rank == "차장"
        assert find(result.records, "안별")[0].rank == "기자"

    def test_sheet_recorded_for_traceability(self, result):
        assert find(result.records, "송욱")[0].source_sheet == "방송"

    def test_no_unparsed(self, result):
        assert result.unparsed == [], result.unparsed


# ── 통합 docx ───────────────────────────────────────────────────────────────

class TestCombinedDocx:
    @pytest.fixture(scope="class")
    def result(self):
        return detect_and_parse(FIXTURES / "sample_combined.docx")

    def test_detected_kind(self):
        assert detect_kind(FIXTURES / "sample_combined.docx") == "combined_docx"

    def test_as_of_from_filename_or_body(self, result):
        assert result.as_of == date(2026, 8, 3)

    def test_desk_rows_become_desk_records(self, result):
        record = find(result.records, "전수용")[0]
        assert record.kind == DESK
        assert record.dept == "테크부"

    def test_team_blob_split_without_separators(self, result):
        """구분자 없이 붙은 팀원 문자열이 사람 단위로 쪼개져야 한다."""
        chosun = {r.name for r in result.records if r.outlet == "조선일보"}
        assert {"김성민", "최인준", "안별", "정한국", "박순찬"} <= chosun

    def test_team_members_are_reporters(self, result):
        assert find(result.records, "김성민")[0].kind == REPORTER

    def test_team_column_maps_to_correct_dept(self, result):
        """팀원 행은 한 칸씩 밀려 있다 — 부서 대응이 어긋나면 안 된다."""
        assert find(result.records, "김성민")[0].dept == "테크부"      # 1번째 칸 → 테크부
        assert find(result.records, "정한국")[0].dept == "산업부"      # 2번째 칸 → 재계통신
        assert find(result.records, "이덕주")[0].dept == "산업부"
        assert find(result.records, "김대기")[0].dept == "테크부"

    def test_category_rows_ignored(self, result):
        assert not [r for r in result.records if "종합지" in r.outlet]

    def test_second_table_parsed(self, result):
        assert find(result.records, "박예원")[0].outlet == "KBS"

    def test_missing_leading_zero_is_repaired(self, result):
        record = find(result.records, "방준원")[0]
        assert record.phone == "01046330052"
        assert any("0 누락" in w for w in record.warnings)

    def test_outlet_without_dept_prefix(self, result):
        record = find(result.records, "이관범")[0]
        assert record.outlet == "문화일보"
        assert record.kind == DESK

    def test_no_unparsed(self, result):
        assert result.unparsed == [], result.unparsed


# ── 유형 판별 ───────────────────────────────────────────────────────────────

def test_unsupported_extension(tmp_path):
    bad = tmp_path / "주소록.txt"
    bad.write_text("매체,이름", encoding="utf-8")
    with pytest.raises(ValueError, match="지원하지 않는 파일 형식"):
        detect_kind(bad)


def test_docx_desk_maps_to_matrix_slot():
    """docx의 데스크는 매트릭스 '산업부장' 열에 배치될 수 있어야 한다."""
    result = detect_and_parse(FIXTURES / "sample_combined.docx")
    for name in ("전수용", "이길성", "김현수", "이관범", "정욱", "고재만"):
        assert find(result.records, name)[0].role_slot == "산업부장", name


def test_concurrent_role_keeps_column_slot():
    """`편집국장` 열의 `(兼 경제부장)` 은 편집국장 자리로 둔다 (겸직은 라벨로 남김)."""
    result = detect_and_parse(FIXTURES / "sample_desk_matrix.xlsx")
    record = find(result.records, "이규성")[0]
    assert record.role_slot == "편집국장"
    assert record.role_label == "경제부장"
    assert record.concurrent is True


class TestSecuredWorkbook:
    """보안문서·DRM 도구를 거친 파일 대응 (openpyxl 이 죽는 형태)."""

    @pytest.fixture()
    def secured(self, tmp_path):
        from tests.make_fixtures import inject_nameless_custom_property

        return inject_nameless_custom_property(
            FIXTURES / "sample_desk_matrix.xlsx", tmp_path / "secured.xlsx"
        )

    def test_openpyxl_alone_fails_on_this_file(self, secured):
        """전제 확인: 그냥 열면 실제로 죽는다."""
        from openpyxl import load_workbook

        with pytest.raises(TypeError, match="Property"):
            load_workbook(secured, data_only=True, read_only=True)

    def test_our_loader_opens_it(self, secured):
        from app.ingest.parsers import open_workbook

        with open_workbook(secured) as workbook:
            assert workbook.worksheets

    def test_parses_identically_to_the_clean_file(self, secured):
        clean = detect_and_parse(FIXTURES / "sample_desk_matrix.xlsx")
        secured_result = detect_and_parse(secured)

        assert secured_result.file_kind == "desk_matrix"
        assert [r.name for r in secured_result.records] == [r.name for r in clean.records]
        assert [r.phone for r in secured_result.records] == [r.phone for r in clean.records]
        assert secured_result.unparsed == []


class TestMixedSheets:
    """한 파일에 양식이 다른 탭이 섞여 있어도 모두 읽어야 한다."""

    @pytest.fixture(scope="class")
    def result(self):
        return detect_and_parse(FIXTURES / "sample_mixed_sheets.xlsx")

    def test_every_sheet_is_visited(self, result):
        assert set(result.sheet_kinds) == {"데스크", "출입기자", "메모"}

    def test_each_sheet_gets_its_own_parser(self, result):
        assert result.sheet_kinds["데스크"] == "desk_matrix"
        assert result.sheet_kinds["출입기자"] == "reporter_list"
        assert result.sheet_kinds["메모"] == "미인식"

    def test_desk_sheet_records(self, result):
        assert find(result.records, "이강은")[0].role_slot == "산업부장"
        assert find(result.records, "이천종")[0].kind == DESK

    def test_reporter_sheet_records(self, result):
        """예전 로직이라면 이 탭이 통째로 빠졌을 것이다."""
        assert find(result.records, "김건호")[0].kind == REPORTER
        assert find(result.records, "김건호")[0].outlet == "세계일보"
        assert find(result.records, "이재호")[0].outlet == "아주경제"

    def test_unrecognised_sheet_is_reported_not_silently_dropped(self, result):
        assert result.sheet_counts["메모"] == 0
        assert any("메모" in w and "건너뜀" in w for w in result.warnings)

    def test_sheet_counts_add_up(self, result):
        assert sum(result.sheet_counts.values()) == len(result.records)
        assert result.sheet_counts["데스크"] == 3
        assert result.sheet_counts["출입기자"] == 3


class TestDeskReporterByRank:
    """통합 docx 의 데스크/출입기자는 줄 위치가 아니라 직급으로 가른다."""

    @pytest.fixture(scope="class")
    def result(self):
        return detect_and_parse(FIXTURES / "sample_combined.docx")

    def test_bujang_in_the_top_row_is_a_desk(self, result):
        assert find(result.records, "이강은")[0].kind == DESK

    def test_chajang_in_the_top_row_is_a_reporter(self, result):
        """맨 윗줄에 함께 적혔어도 차장이면 출입기자다."""
        record = find(result.records, "김건호")[0]
        assert record.kind == REPORTER
        assert record.role_slot is None, "출입기자는 간부 표의 칸을 차지하지 않는다"

    def test_bujang_in_a_team_row_is_a_desk(self, result):
        """아랫줄에 있어도 부장이면 데스크다."""
        record = find(result.records, "모규엽")[0]
        assert record.kind == DESK
        assert record.role_slot == "산업부장"

    def test_chajang_in_a_team_row_stays_a_reporter(self, result):
        assert find(result.records, "권지혜")[0].kind == REPORTER

    def test_person_without_rank_follows_the_row(self, result):
        """직급이 안 적힌 팀원은 출입기자로 둔다."""
        record = find(result.records, "임성호")[0]
        assert record.kind == REPORTER
        assert record.role_slot is None

    def test_no_reporter_occupies_a_matrix_slot(self, result):
        offenders = [r.name for r in result.records if r.kind == REPORTER and r.role_slot]
        assert offenders == [], offenders


class TestMergedOutletColumn:
    """매체명 칸이 세로 병합돼 팀원 행에도 반복되는 실제 레이아웃.

    이 반복을 새 매체 행으로 오인하면 팀원 전원이 데스크(산업부장)로 등록된다.
    실제 파일에서 144명이 그렇게 잘못 들어갔던 사고의 회귀 테스트.
    """

    @pytest.fixture(scope="class")
    def result(self):
        return detect_and_parse(FIXTURES / "sample_combined.docx")

    def test_unlabeled_member_in_repeated_row_is_a_reporter(self, result):
        for name in ("안별", "박순찬", "이동훈", "박소라"):
            record = find(result.records, name)[0]
            assert record.kind == REPORTER, name
            assert record.role_slot is None, name

    def test_desks_are_only_the_top_row(self, result):
        chosun_desks = {r.name for r in result.records if r.outlet == "조선일보" and r.kind == DESK}
        assert chosun_desks == {"전수용", "이길성"}

    def test_repeated_row_members_keep_column_dept(self, result):
        assert find(result.records, "김성민")[0].dept == "테크부"
        assert find(result.records, "정한국")[0].dept == "산업부"

    def test_gukjang_does_not_take_the_sanupbujang_slot(self, result):
        """국장급은 산업부장 칸에 밀어 넣지 않는다. 보도국장은 편집국장 칸."""
        record = find(result.records, "이재준")[0]
        assert record.kind == DESK
        assert record.role_slot == "편집국장"
        assert find(result.records, "임진수")[0].role_slot == "산업부장"

    def test_unknown_outlet_is_not_absorbed_into_previous_one(self, result):
        """사전에 없는 매체(OBS·MTN 케이스)도 매체 행으로 인식해야 한다."""
        desk = find(result.records, "정철호")[0]
        assert desk.outlet == "가나방송"
        assert desk.kind == DESK
        assert desk.outlet_known is False
        member = find(result.records, "유성훈")[0]
        assert member.outlet == "가나방송"
        assert member.kind == REPORTER
        cbs = {r.name for r in result.records if r.outlet == "CBS"}
        assert "정철호" not in cbs and "유성훈" not in cbs


class TestSimilarOutletsStaySeparate:
    """이름이 비슷해도 다른 매체는 합치지 않는다."""

    @pytest.fixture(scope="class")
    def result(self):
        return detect_and_parse(FIXTURES / "sample_combined.docx")

    def test_chosun_biz_is_not_chosun_ilbo(self, result):
        assert find(result.records, "최우석")[0].outlet == "조선비즈"
        assert find(result.records, "전수용")[0].outlet == "조선일보"

    def test_yonhap_tv_is_not_yonhap(self, result):
        assert find(result.records, "박상돈")[0].outlet == "연합뉴스TV"
        assert find(result.records, "박용주")[0].outlet == "연합뉴스"

    def test_both_are_recognised_outlets(self, result):
        for name in ("최우석", "박상돈"):
            assert find(result.records, name)[0].outlet_known is True


class TestNearMissWarning:
    def test_unknown_lookalike_is_flagged(self):
        from app.ingest.reference import get_reference

        reference = get_reference()
        assert reference.match_outlet("조선경제") is None, "모르는 매체는 이어 붙이지 않는다"
        near = reference.near_miss("조선경제")
        assert near is not None and near.name == "조선일보"

    def test_department_suffix_still_resolves(self):
        from app.ingest.reference import get_reference

        outlet = get_reference().match_outlet("조선일보(산업부)")
        assert outlet is not None and outlet.name == "조선일보"

"""정규화·셀 파싱 테스트.

케이스는 전부 구글 드라이브 '주소록' 폴더의 실제 4개 파일에서 발췌한 표기다.
(번호는 실제 값이지만 테스트 목적상 마지막 4자리를 임의 값으로 바꿔 두었다)
"""

from app.ingest.normalize import (
    ParsedPerson,
    format_phone,
    normalize_key,
    normalize_name,
    normalize_phone,
    parse_people,
)


def names(people: list[ParsedPerson]) -> list[str]:
    return [p.name for p in people]


class TestPhone:
    def test_basic(self):
        assert normalize_phone("010-5385-0000")[0] == "01053850000"

    def test_leading_zero_missing(self):
        """docx 원본의 `방준원 10-4633-0000` 표기."""
        digits, warnings = normalize_phone("10-4633-0000")
        assert digits == "01046330000"
        assert warnings

    def test_no_separator(self):
        assert normalize_phone("01053850000")[0] == "01053850000"

    def test_garbage(self):
        digits, warnings = normalize_phone("내선 1234")
        assert digits is None and warnings

    def test_format_roundtrip(self):
        assert format_phone("01053850000") == "010-5385-0000"

    def test_seoul_landline(self):
        """02 유선번호도 오류 없이 받는다 (주간지 편집국 대표번호 등)."""
        digits, warnings = normalize_phone("02-393-0188")
        assert digits == "023930188" and not warnings
        assert format_phone(digits) == "02-393-0188"
        digits, _ = normalize_phone("02-1234-5678")
        assert format_phone(digits) == "02-1234-5678"

    def test_area_landline_and_voip(self):
        assert normalize_phone("031-123-4567")[0] == "0311234567"
        assert normalize_phone("070-8123-4567")[0] == "07081234567"
        assert format_phone("07081234567") == "070-8123-4567"

    def test_representative_number(self):
        digits, warnings = normalize_phone("1588-1234")
        assert digits == "15881234" and not warnings
        assert format_phone(digits) == "1588-1234"

    def test_mask_handles_all_shapes(self):
        from app.ingest.normalize import mask_phone_display

        assert mask_phone_display("01053850000") == "010-●●●●-0000"
        assert mask_phone_display("023930188") == "02-●●●-0188"
        assert mask_phone_display("15881234") == "●●●●-1234"
        assert mask_phone_display(None) == "—"


class TestName:
    def test_spaced_name(self):
        assert normalize_name("이 길 성") == "이길성"

    def test_four_syllable_name(self):
        assert normalize_name("신윤동욱") == "신윤동욱"

    def test_two_syllable_spaced(self):
        assert normalize_name("안 별") == "안별"


class TestKey:
    def test_outlet_alias_keys_collapse(self):
        assert normalize_key("파이낸셜 뉴스") == normalize_key("파이낸셜뉴스")
        assert normalize_key("헤럴드 경제") == normalize_key("헤럴드경제")
        assert normalize_key("TV 조선") == normalize_key("tv조선")


class TestParseCell:
    def test_single_person_with_role(self):
        people = parse_people("이 길 성 (산업부장) 010-5271-0000")
        assert len(people) == 1
        p = people[0]
        assert p.name == "이길성"
        assert p.role_label == "산업부장"
        assert p.phone == "01052710000"

    def test_single_person_no_role(self):
        people = parse_people("강 경 희 010-7344-0000")
        assert names(people) == ["강경희"]
        assert people[0].role_label is None

    def test_two_people_in_one_cell(self):
        """데스크 xlsx: 한 칸에 산업부장·테크부장이 함께 들어 있다."""
        people = parse_people("정 욱(산업부) 010-4379-0000 고 재 만(테크부) 010-9018-0000")
        assert names(people) == ["정욱", "고재만"]
        assert people[0].role_label == "산업부"
        assert people[1].phone == "01090180000"

    def test_second_person_without_role(self):
        people = parse_people("황 인 혁 (디지털뉴스) 010-5023-0000 이 호 승 010-6338-0000")
        assert names(people) == ["황인혁", "이호승"]
        assert people[1].role_label is None

    def test_role_without_parentheses(self):
        people = parse_people("이성규 부국장 경제산업담당 010-4661-0000")
        assert names(people) == ["이성규"]
        assert people[0].role_label == "부국장 경제산업담당"

    def test_concurrent_role(self):
        people = parse_people("이 규 성 (兼 경제부장) 010-3352-0000")
        assert people[0].name == "이규성"
        assert people[0].concurrent is True
        assert people[0].role_label == "경제부장"

    def test_docx_team_blob_without_separators(self):
        """docx 팀원 셀은 구분자 없이 붙어 있다."""
        blob = (
            "김성민 차장 010-9960-0000최인준 차장 010-4750-0000"
            "안 별 010-6291-0000박지민 010-2082-0000"
        )
        people = parse_people(blob)
        assert names(people) == ["김성민", "최인준", "안별", "박지민"]
        assert people[0].role_label == "차장"
        assert people[3].role_label is None

    def test_docx_leading_zero_missing_in_blob(self):
        people = parse_people("석민수 팀장 010-5738-0000 방준원 10-4633-0000")
        assert names(people) == ["석민수", "방준원"]
        assert people[1].phone == "01046330000"
        assert people[1].warnings

    def test_name_with_beat_note(self):
        """출입기자 xlsx: `권지혜(재계)` 처럼 담당 분야가 붙는다."""
        people = parse_people("권지혜(재계) 팀장 010-9092-0000")
        assert people[0].name == "권지혜"
        assert people[0].note == "재계"
        assert people[0].role_label == "팀장"

    def test_empty_markers(self):
        assert parse_people("-") == []
        assert parse_people("") == []
        assert parse_people(None) == []

    def test_person_without_phone_is_kept(self):
        people = parse_people("김은빈 기자")
        assert names(people) == ["김은빈"]
        assert people[0].phone is None

    def test_dept_prefixed_desk_cell(self):
        """docx 지면 표: `산업부 : 정욱 부장 010-...` 형태."""
        people = parse_people("정욱 부장 010-4379-0000")
        assert people[0].name == "정욱"
        assert people[0].role_label == "부장"

    def test_multiline_cell(self):
        people = parse_people("한 석 희 (산업부장/국장) 010-4320-0000\n박 영 훈 (IT과학/국장) 010-8735-0000")
        assert names(people) == ["한석희", "박영훈"]

    def test_source_text_is_preserved(self):
        people = parse_people("강 경 희 010-7344-0000")
        assert "강 경 희" in people[0].source_text


class TestRoleDisplay:
    def test_generic_role_is_expanded_with_dept(self):
        from app.ingest.normalize import role_display

        assert role_display("부장", "산업부장", "테크부") == "테크부장"
        assert role_display("부장", "산업부장", "산업부") == "산업부장"

    def test_specific_role_is_kept(self):
        from app.ingest.normalize import role_display

        assert role_display("산업1부장", "산업부장", "산업부") == "산업1부장"

    def test_falls_back_to_slot(self):
        from app.ingest.normalize import role_display

        assert role_display(None, "편집국장", None) == "편집국장"


class TestEmbeddedPhone:
    """직책·이름에 붙은 전화번호 분리."""

    def test_landline_in_role_moves_to_phone(self):
        from app.ingest.normalize import parse_people

        person = parse_people("김혜연 편집국장 02-393-0188")[0]
        assert person.name == "김혜연"
        assert person.role_label == "편집국장"
        assert person.phone == "023930188"

    def test_landline_kept_as_note_when_mobile_exists(self):
        from app.ingest.normalize import parse_people

        person = parse_people("박대표 발행인 02-777-8888 010-2222-3333")[0]
        assert person.phone == "01022223333"
        assert "유선 02-777-8888" in (person.note or "")
        assert "02" not in (person.role_label or "")

    def test_normal_role_with_digits_is_untouched(self):
        from app.ingest.normalize import extract_embedded_phone

        assert extract_embedded_phone("산업1부장") == ("산업1부장", None)
        assert extract_embedded_phone("사회2부장") == ("사회2부장", None)

    def test_extract_from_plain_role(self):
        from app.ingest.normalize import extract_embedded_phone

        cleaned, digits = extract_embedded_phone("편집국장 02-393-0188")
        assert cleaned == "편집국장"
        assert digits == "023930188"

"""테스트용 합성 원본 파일 생성기.

구글 드라이브 '주소록' 폴더의 실제 4개 파일 레이아웃(제목행·기준일행·빈 선행열·
한 칸 다중 인물·매체 병합행·부서 접두어·구분자 없는 팀원 블록)을 그대로 흉내 낸다.
**전화번호는 전부 가짜 값**이므로 이 파일들은 저장소에 커밋해도 안전하다.

    python tests/make_fixtures.py
"""

from __future__ import annotations

from pathlib import Path

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def build_desk_matrix(path: Path) -> None:
    """(26-0731) 주요 데스크 현황.xlsx 레이아웃."""
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "세로"

    rows = [
        ["", "□ 주요 데스크 현황(26.7.31)", "", "* 전월 변동있는 경우 표시"],
        [],
        ["", "매체명", "편집국장", "산업부장", "경제부장", "증권부장",
         "사회부장 ①", "사회부장 ②", "정치부장", "논설실장", "온라인부장"],
        ["", "조선일보", "강 경 희 010-7344-0001", "이 길 성 (산업부장) 010-5271-0002",
         "방 현 철 010-5385-0003", "", "최 경 운 010-9570-0004",
         "신 은 진 (사회정책) 010-9262-0005", "황 대 진 010-3677-0006",
         "김 창 균 (주필) 010-8891-0007", "이 택 진 (디지털뉴스에디터) 010-9176-0008"],
        ["", "", "", "전 수 용 (테크부장) 010-5260-0009", "", "", "", "", "",
         "박 정 훈 (논설실장) 010-5385-0010", ""],
        ["", "동아일보", "이 승 헌 010-8754-0011", "김 현 수 (산업1부장) 010-3121-0012",
         "이 상 훈 010-9750-0013", "", "한 상 준 010-7158-0014", "", "", "", ""],
        ["", "", "", "신 수 정 (산업2부장) 010-8856-0015", "", "", "", "", "", "", ""],
        ["", "매일경제",
         "김 대 영 010-2793-0016",
         "정 욱(산업부) 010-4379-0017 고 재 만(테크부) 010-9018-0018",
         "신 헌 철 010-4353-0019", "강 두 순 010-4209-0020",
         "김 동 은 010-3720-0021", "강 두 순 (증권/직무대행) 010-4209-0020",
         "강 계 만 010-5309-0022", "김 선 걸 010-2211-0023",
         "황 인 혁 (디지털뉴스) 010-5023-0024 이 호 승 010-6338-0025"],
        ["", "문화일보", "이 제 교 010-9967-0026", "이 관 범 010-4304-0027",
         "김 석 010-9405-0028", "-", "신 보 영 010-3294-0029", "", "", "", ""],
        ["", "국민일보", "태 원 준 010-5397-0030",
         "이성규 부국장 경제산업담당 010-4661-0031", "", "", "", "", "", "", ""],
        ["", "아시아 투데이", "이 규 성 (兼 경제부장) 010-3352-0032",
         "최 원 영 010-3075-0033", "", "", "", "", "", "", ""],
    ]
    for row in rows:
        sheet.append(row)

    workbook.save(path)


def build_reporter_list(path: Path) -> None:
    """(26-0609) 출입기자 현황.xlsx 레이아웃 (시트 여러 개, 매체명 fill-down)."""
    from openpyxl import Workbook

    workbook = Workbook()

    sheet = workbook.active
    sheet.title = "종합지"
    for row in [
        ["<종합지 출입기자> 현황"],
        ["", "", "'26.6.9일 기준"],
        ["매체", "이름", "직급", "전화번호"],
        ["조선", "이길성", "산업부장", "010-5271-0002"],
        ["", "정한국", "차장", "010-4013-0034"],
        ["", "전수용", "테크부장", "010-5260-0009"],
        ["", "안 별", "기자", "010-6291-0035"],
        ["동아", "김현수", "산업1부장", "010-3121-0012"],
        ["", "이동훈", "팀장", "010-3580-0036"],
        ["국민", "권지혜(재계)", "팀장(차장)", "010-9092-0037"],
        ["", "손재호(전자)", "팀장", "010-4677-0038"],
    ]:
        sheet.append(row)

    sheet = workbook.create_sheet("경제지")
    for row in [
        ["<경제지 출입기자> 현황"],
        ["", "", "'26.6.9일 기준"],
        ["매체", "이름", "직급", "전화번호"],
        ["매일경제", "정욱", "산업부장", "010-4379-0017"],
        ["", "이덕주", "차장", "010-2600-0039"],
        ["서울경제", "손철", "산업부장", "010-8601-0040"],
        ["", "구경우", "차장(전자팀장)", "010-6800-0041"],
    ]:
        sheet.append(row)

    sheet = workbook.create_sheet("방송")
    for row in [
        ["<방송 출입기자> 현황"],
        ["", "", "'26.6.9일 기준"],
        ["매체", "이름", "직급", "전화번호"],
        ["KBS", "박예원", "경제산업부장", "010-9580-0042"],
        ["", "석민수", "기자(산업노동팀장)", "010-5738-0043"],
        ["SBS", "송 욱", "경제부장", "010-8959-0044"],
    ]:
        sheet.append(row)

    workbook.save(path)


def build_combined_docx(path: Path) -> None:
    """(26-0803) 주요 데스크 출입기자 현황.docx 레이아웃."""
    import docx

    document = docx.Document()
    document.add_paragraph("주요 데스크/출입기자(지면/통신)")
    document.add_paragraph("*전월 변동 내역 음영표시 2026.8.3")

    table = document.add_table(rows=0, cols=3)
    data = [
        ["[종합지]", "", ""],
        ["조선일보", "테크부 : 전수용 부장 010-5260-0009", "재계통신 : 이길성 부장 010-5271-0002"],
        [
            "김성민 차장 010-9960-0045최인준 차장 010-4750-0046안 별 010-6291-0035",
            "정한국 차장 010-4013-0034박순찬 010-9757-0047",
            "",
        ],
        ["동아일보", "산업1부 : 김현수 부장 010-3121-0012", ""],
        ["장윤정 차장 010-4200-0048이동훈 010-3580-0036", "", ""],
        ["[경제지/전문지]", "", ""],
        ["매일경제", "산업부 : 정욱 부장 010-4379-0017", "테크부 : 고재만 부장 010-9018-0018"],
        [
            "이덕주 차장 010-2600-0039박소라 010-9394-0049",
            "김대기 차장 010-5478-0050",
            "",
        ],
        ["문화일보", "이관범 부장 010-4304-0027", ""],
        ["이용권 차장 010-9132-0051", "", ""],
    ]
    for row_values in data:
        cells = table.add_row().cells
        for idx, value in enumerate(row_values):
            cells[idx].text = value

    document.add_paragraph("주요 데스크/출입기자(방송)")
    table = document.add_table(rows=0, cols=2)
    for row_values in [
        ["[방송]", ""],
        ["KBS", "박예원 부장 010-9580-0042"],
        ["석민수 팀장 010-5738-0043 방준원 10-4633-0052", ""],
    ]:
        cells = table.add_row().cells
        for idx, value in enumerate(row_values):
            cells[idx].text = value

    document.save(str(path))


def main() -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    build_desk_matrix(FIXTURE_DIR / "sample_desk_matrix.xlsx")
    build_reporter_list(FIXTURE_DIR / "sample_reporter_list.xlsx")
    build_combined_docx(FIXTURE_DIR / "sample_combined.docx")
    print(f"픽스처 생성 완료 → {FIXTURE_DIR}")


if __name__ == "__main__":
    main()


def inject_nameless_custom_property(source: Path, target: Path) -> Path:
    """이름이 비어 있는 사용자 지정 문서 속성을 심은 사본을 만든다.

    보안문서·DRM 도구를 거친 실제 파일에서 나타나는 형태로, openpyxl 이
    `StringProperty.name should be str but value is NoneType` 로 죽는 원인이다.
    """
    import shutil
    import zipfile

    custom_xml = (
        b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        b'<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"'
        b' xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes">'
        b'<property fmtid="{D5CDD505-2E9C-101B-9397-08002B2CF9AE}" pid="2">'
        b"<vt:lpwstr>secure-doc</vt:lpwstr></property>"
        b"</Properties>"
    )
    override = (
        '<Override PartName="/docProps/custom.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.custom-properties+xml"/>'
    )
    relationship = (
        '<Relationship Id="rIdCustom" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/custom-properties" '
        'Target="docProps/custom.xml"/>'
    )

    shutil.copy(source, target)
    with zipfile.ZipFile(source) as src:
        names = src.namelist()
        payload = {name: src.read(name) for name in names}

    payload["[Content_Types].xml"] = payload["[Content_Types].xml"].replace(
        b"</Types>", override.encode() + b"</Types>"
    )
    payload["_rels/.rels"] = payload["_rels/.rels"].replace(
        b"</Relationships>", relationship.encode() + b"</Relationships>"
    )
    payload["docProps/custom.xml"] = custom_xml

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as out:
        for name, data in payload.items():
            out.writestr(name, data)
    return target

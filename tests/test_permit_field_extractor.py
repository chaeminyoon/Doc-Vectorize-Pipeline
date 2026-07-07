from datetime import date

from sqlalchemy import Text

from src.extractors.permit_field_extractor import PermitExtractedFields, PermitFieldExtractor
from src.parsers.odt_parser import ContentSection, ODTContent
from src.pipeline.processor import DocumentPipeline


def test_permit_extraction_table_declares_requested_fields():
    from src.vectordb.models import Base

    table = Base.metadata.tables["permit_extractions"]

    assert table.primary_key.columns.keys() == ["doc_id"]
    for column_name in (
        "org_nm",
        "ask_org_nm",
        "addr",
        "xtn",
        "purps_cn",
        "collection_volume",
        "permit_start_date",
        "permit_end_date",
        "amount",
    ):
        assert column_name in table.columns

    assert table.columns["org_nm"].comment == "기관명"
    assert table.columns["ask_org_nm"].comment == "피허가자명"
    assert table.columns["addr"].comment == "위치"
    assert table.columns["xtn"].comment == "면적"
    assert table.columns["purps_cn"].comment == "목적"
    assert isinstance(table.columns["ask_org_nm"].type, Text)
    assert isinstance(table.columns["xtn"].type, Text)
    assert isinstance(table.columns["addr"].type, Text)
    assert isinstance(table.columns["purps_cn"].type, Text)


def test_pipeline_creates_permit_extraction_with_requested_db_columns():
    content = ODTContent(
        doc_id="DOC001",
        file_path="sample.odt",
        title="sample",
        sections=[
            ContentSection(
                section_type="text",
                content="""
                부산지방해양수산청
                피허가자명 : 주식회사 테스트
                위치 : 부산광역시 중구 중앙동 공유수면
                면적 : 123㎡
                목적 : 부잔교 설치
                """,
                order=1,
            )
        ],
    )
    pipeline = DocumentPipeline.__new__(DocumentPipeline)
    pipeline.permit_extractor = PermitFieldExtractor()

    entity = pipeline._create_permit_extraction_entity(content)

    assert entity.doc_id == "DOC001"
    assert entity.org_nm == "부산지방해양수산청"
    assert entity.ask_org_nm == "주식회사 테스트"
    assert entity.addr == "부산광역시 중구 중앙동 공유수면"
    assert entity.xtn == "123㎡"
    assert entity.purps_cn == "부잔교 설치"


def test_pipeline_keeps_long_extracted_values_for_text_columns():
    long_permittee = "허가대상기관" * 40
    long_area = "직접 1,000㎡ 간접 2,000㎡ " * 20
    fields = PermitExtractedFields(
        permittee_name=long_permittee,
        location="인천광역시 중구 항동",
        area=long_area,
        purpose="부두시설 설치",
    )

    entity = DocumentPipeline._permit_fields_to_entity("DOC_LONG", fields)

    assert entity.ask_org_nm == long_permittee
    assert entity.xtn == long_area


def test_filters_separator_tokens_from_extracted_fields():
    text = """
    기관명: 부산지방해양수산청
    피허가자명: |
    위치: |
    면적: (㎡) |
    목적: | 허가
    허가기간: 2017.01.05. ~ 2020.01.04.
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.permittee_name is None
    assert fields.location is None
    assert fields.area is None
    assert fields.purpose is None
    assert fields.permit_start_date == date(2017, 1, 5)
    assert fields.permit_end_date == date(2020, 1, 4)


def test_filters_connector_token_from_location():
    text = """
    기관명: 인천지방해양수산청
    위치: 및
    면적: 1,000㎡
    목적: 천연가스 공급 가스배관 매설
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.location is None
    assert fields.area == "1,000㎡"
    assert fields.purpose == "천연가스 공급 가스배관 매설"


def test_extracts_inline_permit_fields_from_body_text():
    text = """
    희망의 새시대
    인천지방해양항만청
    수신 대한싸이로(주) 대표이사 김창용 귀하
    제목 공유수면 점용ㆍ사용 변경허가증 교부
    - 다 음 -
    가. 위치 : 인천광역시 중구 북성동 1가 3-12번지선 공유수면
    나. 면적 : 4,717.8㎡(직접 2,248.1㎡, 간접 2,469.7㎡)
    다. 목적 : 곡물하역용 컨베이어벨트 및 잔교시설 설치ㆍ운영
    라. 허가기간 : 2013.10.01. ~ 2014.09.30.(1년)
    마. 점용료ㆍ사용료 및 납부기한 : 일금23,822,320원정/ 2013.10.17.(목)
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.org_name == "인천지방해양항만청"
    assert fields.permittee_name == "대한싸이로(주) 대표이사 김창용"
    assert fields.location == "인천광역시 중구 북성동 1가 3-12번지선 공유수면"
    assert fields.area == "4,717.8㎡(직접 2,248.1㎡, 간접 2,469.7㎡)"
    assert fields.purpose == "곡물하역용 컨베이어벨트 및 잔교시설 설치ㆍ운영"
    assert fields.permit_start_date == date(2013, 10, 1)
    assert fields.permit_end_date == date(2014, 9, 30)
    assert fields.amount == "23,822,320원"


def test_extracts_explicit_dates_and_volume_fields():
    text = """
    기관명 : 목포지방해양항만청
    피허가자명 : 전라남도지사
    최초허가일자 : 2014. 9. 18.
    허가일자 : 2014. 9. 24.
    투기량 : 14,006㎥
    채취량 : 3,500㎥
    허가시작일 : 2014-10-01
    허가종료일 : 2014-12-31
    금액 : 1,005,680원
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.org_name == "목포지방해양항만청"
    assert fields.permittee_name == "전라남도지사"
    assert fields.collection_volume == "3,500㎥"
    assert fields.permit_start_date == date(2014, 10, 1)
    assert fields.permit_end_date == date(2014, 12, 31)
    assert fields.amount == "1,005,680원"


def test_extracts_combined_area_and_purpose_without_label_leakage():
    text = """
    가. 위치 : 인천광역시 중구 항동 7가 27-104번지선 공유수면
    나. 면적 및 목적 : 1.8㎡(해수인수관 75㎜ 1기/ 어시장 내 수산물상가 해수공급)
    다. 허가기간 : 2014.01.01. ~ 2014.12.31.
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.area == "1.8㎡"
    assert fields.purpose == "해수인수관 75㎜ 1기/ 어시장 내 수산물상가 해수공급"


def test_extracts_collapsed_permit_detail_table():
    text = """
    □ 공유수면 점용·사용 허가 사항
    허가번호신 청 인점용장소(지선)점용유형(목적)점용면적(㎡)점사용료(원)허가기간비고주 소성 명
    2014-14경남 창원시 마산합포구 수산1길181, 102동 2003호황인수 외 1명경남 창원시 마산합포구 수산1길 144(남성동 230-1번지)유람선 접안용 부잔교171.941,005,6802014.08.012014.10.31(3개월)
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.permittee_name == "황인수 외 1명"
    assert fields.location == "경남 창원시 마산합포구 수산1길 144(남성동 230-1번지)"
    assert fields.purpose == "유람선 접안용 부잔교"
    assert fields.area == "171.94㎡"
    assert fields.amount == "1,005,680원"
    assert fields.permit_start_date == date(2014, 8, 1)
    assert fields.permit_end_date == date(2014, 10, 31)


def test_extracts_collapsed_application_table_with_dredging_volume():
    text = """
    □ 공유수면 점·사용 허가 신청내역 □
    신 청 자 위 치사 용 목 적면적준설량기간전라남도지사신안군 압해면 송공항 일원 공유수면항로준설5,742㎡14,006m3허가일로부터 3개월협의서 접수2014. 9. 18.(목)
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.permittee_name == "전라남도지사"
    assert fields.location == "신안군 압해면 송공항 일원 공유수면"
    assert fields.purpose == "항로준설"
    assert fields.area == "5,742㎡"
    assert fields.collection_volume == "14,006m3"


def test_extracts_narrative_bullet_dumping_fields():
    text = """
    해군 진해기지사령부에서 준설토를 배타적 경제수역에 해양 투기(투기면적 : 195,250㎡, 투기량 : 90,000㎡)하고자 공유수면 점용․사용 승인 신청서를 제출하였습니다.
    o 위 치 : N 34° 48'00", E 129° 00'00"(진해항 동.남방 50㎞)
    o 목 적 : 준설토 해양 투기
    o 면 적(준설량) : 196,250㎡(90,000㎡)
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.location == "N 34° 48'00\", E 129° 00'00\"(진해항 동.남방 50㎞)"
    assert fields.purpose == "준설토 해양 투기"
    assert fields.area == "196,250㎡"


def test_cleans_glued_slogan_from_org_name():
    text = "희망의 새시대목포지방해양항만청수신수신자 참조제목공유수면 점사용 협의"

    fields = PermitFieldExtractor().extract(text)

    assert fields.org_name == "목포지방해양항만청"


def test_extracts_glued_receiver_permittee_name():
    text = "마산지방해양수산청수신에프더블유1412 유동화전문 유한회사 귀하(경유)제목고발 취하 알림"

    fields = PermitFieldExtractor().extract(text)

    assert fields.permittee_name == "에프더블유1412 유동화전문 유한회사"


def test_extracts_narrative_permittee_from_common_application_sentences():
    extractor = PermitFieldExtractor()

    cases = [
        (
            "인천항만공사의 공유수면 점ㆍ사용 변경허가 신청에 대해 허가합니다.",
            "인천항만공사",
        ),
        (
            "포항시장으로부터 제출된 공유수면 점용·사용 변경승인 신청에 대하여 검토한 바",
            "포항시장",
        ),
        (
            "한국남부발전(주)에서 제출한 공유수면 점용 사용 변경허가 신청에 대하여 변경 허가하고",
            "한국남부발전(주)",
        ),
        (
            "㈜돝섬해피랜드 대표이사 홍길동으로부터 공유수면 점용 사용 변경허가 신청이 있어 검토합니다.",
            "㈜돝섬해피랜드 대표이사 홍길동",
        ),
        (
            "수신녹산어촌계장 귀하 (경유)제목공유수면 점용 사용 변경 허가",
            "녹산어촌계장",
        ),
    ]

    for text, expected in cases:
        assert extractor.extract(text).permittee_name == expected


def test_cleans_narrative_permittee_artifacts():
    extractor = PermitFieldExtractor()

    cases = [
        (
            "현대중공업(주)로부터 다음과 같이 공유수면 점용 사용 변경허가 신청이 있어 의견을 조회합니다.",
            "현대중공업(주)",
        ),
        (
            "유진건설 주식회사(대표 홍길동)로부터 공유수면 점용 사용 허가 신청에 따른 협의를 요청합니다.",
            "유진건설 주식회사",
        ),
        (
            "공유수면 점용 사용 변경허가 고시 한국남부발전(주)에서 공유수면 점용 사용 변경허가 신청서를 제출하였습니다.",
            "한국남부발전(주)",
        ),
    ]

    for text, expected in cases:
        assert extractor.extract(text).permittee_name == expected


def test_does_not_extract_distribution_list_as_permittee():
    text = "부산지방해양항만청장수신자해양수산부장관(연안계획과장), 부산광역시장주무관 2013. 5. 27."

    fields = PermitFieldExtractor().extract(text)

    assert fields.permittee_name is None


def test_extracts_spaced_central_government_org_name():
    text = '"국민과 함께 가는 행복의 길"해  양  수  산  부수신한국해양수산개발원장제목공유수면매립기본계획 반영 신청안 알림'

    fields = PermitFieldExtractor().extract(text)

    assert fields.org_name == "해양수산부"


def test_extracts_org_name_from_footer_address_when_header_table_lost():
    text = """
    | 수신 |
    | 제목 | 공유수면 점용·사용 실시계획 준공검사 확인증 교부 및 고시
    시행해양수산환경과-5572접수우48755부산광역시 동구 충장대로 351, 부산지방해양수산청 (좌천동)
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.org_name == "부산지방해양수산청"


def test_extracts_collapsed_occupation_table_with_collection_volume():
    text = """
    신청인점용·사용 장소점용·사용 목적면적(㎡) /채취량(㎥)점용․사용 기간
    ㈜엔엘피창원시 마산합포구 오동동 315-1 인근해역미세조류 및 먹이생물 시험에 필요한 생물 채취1㎡ /회당 약1800㎥(월4∼5회)허가일로부터 1년
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.permittee_name == "㈜엔엘피"
    assert fields.location == "창원시 마산합포구 오동동 315-1 인근해역"
    assert fields.purpose == "미세조류 및 먹이생물 시험에 필요한 생물 채취"
    assert fields.area == "1㎡"
    assert fields.collection_volume == "1800㎥"


def test_extracts_collapsed_position_purpose_area_table():
    text = """
    신청인점용･사용위치목 적면 적(㎡)기  간직접간접계
    창원시장창원시 성산구 귀산동 535-11번지 지선삼귀해안 도로탐방로 데크설치1,194㎡-1,194㎡승인일로부터 1년
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.permittee_name == "창원시장"
    assert fields.location == "창원시 성산구 귀산동 535-11번지 지선"
    assert fields.purpose == "삼귀해안 도로탐방로 데크설치"
    assert fields.area == "1,194㎡"


def test_extracts_collapsed_permit_detail_table_with_short_dates():
    text = """
    허가번호신 청 인점용장소(지선)점용유형(목적)점용면적(㎡)점사용료(원)허가기간비고주 소성 명
    2015-10창원시 성산구삼귀로466번길12-10김민수창원시 성산구 귀산동 609 번지어구작업장 및 어선 접안용 부잔교131.1646,5002015.5.1.~2016.4.30.(1년간)
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.permittee_name == "김민수"
    assert fields.location == "창원시 성산구 귀산동 609 번지"
    assert fields.purpose == "어구작업장 및 어선 접안용 부잔교"
    assert fields.area == "131.16㎡"
    assert fields.amount == "46,500원"
    assert fields.permit_start_date == date(2015, 5, 1)
    assert fields.permit_end_date == date(2016, 4, 30)


def test_extracts_narrative_location_and_quoted_purpose():
    text = """
    귀하께서 포항시 북구 흥해읍 용한리 산5-1번지선의 공유수면에
    “포항영일만항 항만배후단지(1단계1공구) 상부기반시설공사”를 위한
    공사용 현장사무실 사용 목적으로 제출한 공유수면 점용‧사용허가 신청에 대하여 허가합니다.
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.location == "포항시 북구 흥해읍 용한리 산5-1번지선의 공유수면"
    assert fields.purpose == "포항영일만항 항만배후단지(1단계1공구) 상부기반시설공사"


def test_extracts_change_table_after_values():
    text = """
    구 분변경 전변경 후장 소인천광역시 서구 원창동 435번지선 공유수면면 적240㎡(길이 60m, 폭 4m)목 적청라투기장 진출입 가설교량 설치운영변경 없음기 간2012.4.1 ~ 2015.3.31(3년)2015.4.1 ~ 2018.3.31(3년)
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.location == "인천광역시 서구 원창동 435번지선 공유수면"
    assert fields.area == "240㎡(길이 60m, 폭 4m)"
    assert fields.purpose == "청라투기장 진출입 가설교량 설치운영"
    assert fields.permit_start_date == date(2015, 4, 1)
    assert fields.permit_end_date == date(2018, 3, 31)


def test_extracts_change_table_area_without_unchanged_noise():
    text = """
    구  분변경 전변경 후장  소인천광역시 동구 송현동 120번지선 공유수면면  적314㎡(직접 314)변경 없음목  적인천도시계획시설(수로암거 3련) 설치․운영변경 없음기  간‘12.12.1 ~‘16.12.31 (4년1개월)‘17. 1. 1 ~‘19.12.31 (3년)
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.location == "인천광역시 동구 송현동 120번지선 공유수면"
    assert fields.area == "314㎡(직접 314)"
    assert fields.purpose == "인천도시계획시설(수로암거 3련) 설치․운영"


def test_extracts_compact_applicant_location_purpose_area_period_table():
    text = """
    신청인위 치목 적면 적허가 기간(유)초원건설대표 박영수목포시 산정동 1110-2지선북항 씨푸드타운해수인수관 설치866m22015.6.19. ~2020.6.18.
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.permittee_name == "(유)초원건설대표 박영수"
    assert fields.location == "목포시 산정동 1110-2지선"
    assert fields.purpose == "북항 씨푸드타운해수인수관 설치"
    assert fields.area == "866m2"
    assert fields.permit_start_date == date(2015, 6, 19)
    assert fields.permit_end_date == date(2020, 6, 18)


def test_extracts_dredging_volume_from_area_parentheses():
    text = """
    o 면 적(준설량) : 3,675㎡(4,670㎥)
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.area == "3,675㎡"
    assert fields.collection_volume == "4,670㎥"


def test_extracts_dredging_volume_from_sentence():
    text = """
    나. 면적 : 7,805㎡(직접 7,008㎡ 간접 797㎡/ 준설면적 6,467㎡, 준설량 18,749㎥)
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.area == "7,805㎡(직접 7,008㎡ 간접 797㎡/ 준설면적 6,467㎡, 준설량 18,749㎥)"
    assert fields.collection_volume == "18,749㎥"


def test_normalizes_org_name_from_collapsed_table_header_noise():
    text = """
    기관명위 치목 적면 적허가기간한국해양소년단전남서부연맹목포시 상동1181번지 지선해양교육훈련장499.3㎡허가일로부터10년간
    붙임 1. 공유수면 점사용허가 신청서 1부. 끝.
    목포지방해양수산청장
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.org_name == "목포지방해양수산청"


def test_normalizes_org_name_with_ministry_prefix_to_specific_agency():
    text = """
    시행해양수산환경과-10923
    접수우30110 경상남도 창원시 마산합포구 제2부두로 10
    해양수산부 마산지방해양수산청 해양수산환경과
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.org_name == "마산지방해양수산청"


def test_infers_org_name_from_mof_url_when_body_has_no_agency_name():
    text = """
    [TABLE_START] | 수신 | | (경유) | | 제목 | [TABLE_END]
    셀파이엔씨(주)에서 제출한 공유수면 점용·사용 허가 신청의 검토 결과를 붙임과 같이 보고합니다.
    주무관주무관과장전결 2023. 5. 25.
    전화번호033-520-6232팩스번호033-520-6230/http://donghae.mof.go.kr/비공개(7)
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.org_name == "동해지방해양수산청"


def test_extracts_fields_from_markdown_key_value_table():
    text = """
    [TABLE_START] | 구분 | 내용 | | --- | --- |
    | 업체명 | 비금주민태양광발전(주) |
    | 위치 | 비금도 가산선착장 북측 ~ 안좌도 북지선착장 인근 해역 |
    | 면적 | (직접) 81,847m2 / (간접) 681,000m2 |
    | 목적 | 송전선로(해저케이블) 설치 |
    | 기간 | (직접) 허가일로부터 22년 / (간접) 허가일로부터 10개월 |
    [TABLE_END]
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.permittee_name == "비금주민태양광발전(주)"
    assert fields.location == "비금도 가산선착장 북측 ~ 안좌도 북지선착장 인근 해역"
    assert fields.area == "(직접) 81,847m2 / (간접) 681,000m2"
    assert fields.purpose == "송전선로(해저케이블) 설치"


def test_extracts_fields_from_markdown_applicant_table():
    text = """
    [TABLE_START] | 피허가자 | 위치 | 목적 | 면적(㎡) | 기간 |
    | --- | --- | --- | --- | --- |
    | 이상혁 | 창원시 성산구 귀곡동 754-3번지 지선 | 수상레저기구 및 어선 요트 계류용 부잔교 설치 | 295.35 | 2020. 2. 4. ~2021. 2. 3. |
    [TABLE_END]
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.permittee_name == "이상혁"
    assert fields.location == "창원시 성산구 귀곡동 754-3번지 지선"
    assert fields.purpose == "수상레저기구 및 어선 요트 계류용 부잔교 설치"
    assert fields.area == "295.35㎡"
    assert fields.permit_start_date == date(2020, 2, 4)
    assert fields.permit_end_date == date(2021, 2, 3)


def test_extracts_short_year_period_from_markdown_applicant_table():
    text = """
    [TABLE_START] | 피허가자 | 위치 | 목적 | 면적(㎡) | 기간 |
    | --- | --- | --- | --- | --- |
    | 포항시장 | 포항시 북구 환호동 478번지 지선 | 수목식재 | 314㎡ | ‘17. 1. 1 ~ ‘19.12.31 |
    [TABLE_END]
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.permit_start_date == date(2017, 1, 1)
    assert fields.permit_end_date == date(2019, 12, 31)


def test_extracts_inline_collapsed_table_when_purpose_precedes_area():
    text = """
    - 아 래 -
    피승인자위치면적점용 목적점․사용 기간목포수산업협동조합목포시 달동 754-2 지선유류저장부선 계류342m22017. 3. 1.~2017. 8. 31. 끝.
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.permittee_name == "목포수산업협동조합"
    assert fields.location == "목포시 달동 754-2 지선"
    assert fields.purpose == "유류저장부선 계류"
    assert fields.area == "342m2"
    assert fields.permit_start_date == date(2017, 3, 1)
    assert fields.permit_end_date == date(2017, 8, 31)


def test_extracts_inline_collapsed_table_when_area_precedes_purpose():
    text = """
    신청인위 치면 적공사 내용공사 기간(유)초원건설대표 박영수목포시 산정동1110-2지선866m2북항씨푸드타운해수인수관 설치착공일로부터10일간 끝.
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.permittee_name == "(유)초원건설대표 박영수"
    assert fields.location == "목포시 산정동1110-2지선"
    assert fields.area == "866m2"
    assert fields.purpose == "북항씨푸드타운해수인수관 설치"


def test_extracts_inline_application_table_with_unit_only_in_header():
    text = """
    □ 공유수면 점·사용 허가 신청내역 □ 신 청 자 위 치사 용 목 적면적(m2)기간이상섭안좌면 존포리 산68-2 지선청소년야영장 계단설치(목재 계단)96허가일로부터 3년협의서 접수2014. 9. 25.(목)
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.permittee_name == "이상섭"
    assert fields.location == "안좌면 존포리 산68-2 지선"
    assert fields.purpose == "청소년야영장 계단설치(목재 계단)"
    assert fields.area == "96m2"


def test_extracts_labeled_outline_application_fields():
    text = """
    가. 신청자 : (주)지오메카이엔지
    나. 신청목적 : 당진-평택지역 전력구 공사 설계용역을 위한 지반 조사
    다. 허가장소 : 당진시 송악읍 부곡리 564 ~ 평택시 포승읍 만호리 649 지선해면
    라. 허가규모 : 4,235(121m2x35개소)m2
    마. 허가기간 : 2014.6.1. ~ 2014.6.7.(7일간)
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.permittee_name == "(주)지오메카이엔지"
    assert fields.location == "당진시 송악읍 부곡리 564 ~ 평택시 포승읍 만호리 649 지선해면"
    assert fields.purpose == "당진-평택지역 전력구 공사 설계용역을 위한 지반 조사"
    assert fields.area == "4,235(121m2x35개소)m2"
    assert fields.permit_start_date == date(2014, 6, 1)
    assert fields.permit_end_date == date(2014, 6, 7)


def test_extracts_narrative_quoted_purpose_after_location_with_applicant():
    text = """
    창원시 마산합포구 합포로 183-1 방종호로부터 창원시 마산합포구 덕동동 59-1번지 지선 일대 공유수면에
    『선박(레저) 접안용 부잔교 설치』를 위한 공유수면 점용·사용 실시계획 승인 신청이 있어 검토합니다.
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.permittee_name == "방종호"
    assert fields.location == "창원시 마산합포구 덕동동 59-1번지 지선 일대 공유수면"
    assert fields.purpose == "선박(레저) 접안용 부잔교 설치"


def test_does_not_extract_table_header_and_body_as_permittee():
    text = """
    [TABLE_START]
    | 일자리가 성장이고 복지입니다. |
    | --- |
    | 수신 |
    | 제목 |
    [TABLE_END]
    1. 부산항만공사 스마트항만실-3453(2020.12.11.)호와 관련이 있습니다.
    2. 부산항만공사에서 경남 창원시 진해구 연도동 300번지 지선 공유수면에 "준설토 이송을 위한 바지선 및 언로딩펌프선 접안, 준설토 하역작업"을 위한 점용·사용 실시계획 변경승인 신청이 있어 검토합니다.
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.permittee_name == "부산항만공사"
    assert fields.location == "경남 창원시 진해구 연도동 300번지 지선 공유수면"
    assert fields.purpose == "준설토 이송을 위한 바지선 및 언로딩펌프선 접안, 준설토 하역작업"


def test_extracts_bulleted_application_overview_without_long_sentence_values():
    text = """
    □ 신청개요
    ○ 신청인 : 영광해운 대표자 김복순
    ○ 신청위치 : 창원시 진해구 안골동 839번지 지선
    ○ 면적 / 목적 : 165㎡ / 수리선박 계류
    ○ 허가기간 : 30 일
    □ 신청 배경
    가. 공유수면 점용·사용의 유형 : 선박 계류
    ○ 해정호 수리를 위해 예인선으로 부산 다대포 조선소로 이동 중 침몰 위험이 있어 회항하였습니다.
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.permittee_name == "영광해운 대표자 김복순"
    assert fields.location == "창원시 진해구 안골동 839번지 지선"
    assert fields.area == "165㎡"
    assert fields.purpose == "수리선박 계류"


def test_rejects_collapsed_multi_row_application_table_as_single_permittee():
    text = """
    □공유수면 점·사용 변경허가 신청 내역□
    피허가자신청내역비고주소성명장소(인근해역)목적면적(㎡)허가기간당초변경진도군 고군면금계리 14-1최정훈진도군 고군면 금계리 1212-1인배수관852002.06.18.2007.06.17.허가일로부터5년간진도군 고군면금계리 14-1최정훈진도군 고군면금계리 1212-14인배수관702004.02.10.2009.02.09.허가일로부터5년간
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.permittee_name is None


def test_rejects_permittee_contaminated_by_document_header():
    text = """
    "국민과 함께 가는 행복의 길, 바다로 세계로 미래로"마산지방해양수산청수신항만건설과장제목공유수면 점용·사용 실시계획 승인 신청에 따른 기술검토 의뢰 창원시장
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.permittee_name is None


def test_rejects_permittee_extracted_from_body_sentence():
    text = """
    신청인은 동 행위와 관련하여 관계기관의 별도 허가, 승인 등이 필요할 경우 이를 이행해야 하며,
    본 허가로 인한 피해 및 민원이 발생할 경우 전담하여 해결해야 합니다.
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.permittee_name is None


def test_rejects_permittee_contaminated_by_area_and_period():
    text = """
    신청인 : 신안군 압해읍 송공리 718-44지선송공선착장 설치공사572㎡허가일로부터 10년간
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.permittee_name is None


def test_keeps_multiple_numbered_permittees():
    text = """
    신청자 : 1) (유)신안해풍 2) (유)신안해풍2호 3) (유)신안나눔풍력 4) 코리아윈드파워
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.permittee_name == "1) (유)신안해풍 2) (유)신안해풍2호 3) (유)신안나눔풍력 4) 코리아윈드파워"


def test_rejects_location_contaminated_by_table_headers():
    text = """
    성 명위 치사용 목적면적(㎡)기간한국석유공사목 적면 적(㎡)기 간울산 남구 매암동 울산항 남동쪽 약 43km 지점동해 대륙봉 가스전 시추 후 잔존 구조물 보존직접 23.23㎡간접 5.07㎡2018.02.01.~2018.5.31
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.location is None


def test_rejects_location_contaminated_by_area_purpose_and_period():
    text = """
    위치 : 부산시 사하구 감천동 410-17부산시 사하구 감천동 441-9면적/목적3,305㎡/부유물 방지막신청기간2013.8.1-12.31
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.location is None


def test_rejects_location_contaminated_by_review_request_sentence():
    text = """
    위치 : 목적허가면적허가기간진해구 제덕동 892번지지선 공유수면부잔교 설치157㎡2025.12.2. ~2030.12.1. 창원시청에서 공유수면 점용·사용 실시계획 신고가 있어 공사계획과 설계도서 적정성 등을 검토 의뢰하오니 회신하여 주시기 바랍니다
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.location is None


def test_prefers_changed_location_after_slash_separator():
    text = """
    위치 : 인천광역시 옹진군 덕적면 굴업리 북서측 약 59km 인근 ①37-23-24N, 125-14-50E / 인천광역시 옹진군 덕적면 굴업리 북서측 약 59km 인근 ①37-23-54N, 125-20-56E
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.location == "인천광역시 옹진군 덕적면 굴업리 북서측 약 59km 인근 ①37-23-54N, 125-20-56E"


def test_trims_location_before_attached_submission_sentence():
    text = """
    위치 : 사하구 다대동 산113-56번지 지선 공유수면을 '준설토 적치 구조물 설치' 목적으로 사용하기 위하여 제출한 공유수면 점용·사용 허가 신청에 대하여 검토한 결과 아래와 같은 사유로 신청서를 반려하오니 양지하시기 바랍니다.
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.location == "사하구 다대동 산113-56번지 지선 공유수면"


def test_deduplicates_repeated_marked_location_items():
    text = """
    위치 : ⓐ 인천광역시 중구 북성동 1가 6-29번지선 공유수면 ⓑ 인천광역시 중구 북성동 1가 6-145번지선 공유수면 ⓒ 인천광역시 중구 북성동 1가 6-32번지선 공유수면 ⓐ 인천광역시 중구 북성동 1가 6-29번지선 공유수면 ⓑ 인천광역시 중구 북성동 1가 6-145번지선 공유수면
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.location == (
        "ⓐ 인천광역시 중구 북성동 1가 6-29번지선 공유수면 "
        "ⓑ 인천광역시 중구 북성동 1가 6-145번지선 공유수면 "
        "ⓒ 인천광역시 중구 북성동 1가 6-32번지선 공유수면"
    )


def test_rejects_area_and_purpose_contaminated_by_table_headers():
    text = """
    면적 : 부과(납부)기간부과액(원)대죽리 산28-1447.27연료 및 스팀장치2014. 06. 01~2015. 05. 31152,980
    목적 : 목적면적(㎡)기간울산 남구 매암동 잔존 구조물 보존직접 23.23㎡2018.02.01.~2018.5.31
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.area is None
    assert fields.purpose is None


def test_rejects_area_without_numeric_area_value():
    text = """
    면적 : 변경내역
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.area is None


def test_rejects_area_contaminated_by_purpose_sentence():
    text = """
    면적 : 목적 변경된 사항에 대한 사업계획서, 계획평면도 및 구적도 등 첨부서류 누락으로 검토불가
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.area is None


def test_rejects_area_contaminated_by_location_and_date():
    text = """
    면적 : 인천광역시 중구 중산동 1849번지선으로 연결되는 사이 공유수면 구간39,664.6㎡(직접 20,109.9㎡) 2019.1.1.부터
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.area is None


def test_prefers_changed_area_after_slash_separator():
    text = """
    면적 : 7,000㎡ (직접 7,000 / A구역 4,000, B구역 3,000) / 6,000㎡ (직접 6,000 / A구역 4,000, B구역 2,000)
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.area == "6,000㎡ (직접 6,000 / A구역 4,000, B구역 2,000)"


def test_prefers_changed_area_when_period_follows_after_slash():
    text = """
    면적 : 4,864㎡(직접3,824㎡, 간접1,040㎡) / 3,824㎡(직접3,824㎡) / 2021.10.1.부터 비관리청항만공사
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.area == "3,824㎡(직접3,824㎡)"


def test_rejects_purpose_when_approval_notice_sentence_is_attached():
    text = """
    목적 : 으로 하는 공유수면 점용·사용 실시계획 승인 신청에 대하여 법률에 따라 붙임과 같이 승인하였음을 알려드리오니 승인조건을 준수하시기 바랍니다
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.purpose is None


def test_rejects_purpose_contaminated_by_application_location_table():
    text = """
    목적 : 신청 위치/면적비 고감천파출소 순찰정 계류용 폰툰사하구 감천동 441-5 /460m²(감천항 행정선 부두 전면)기간연장(~'18.12.31)
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.purpose is None


def test_prefers_changed_purpose_after_slash_separator():
    text = """
    목적 : 선박 접안시설 설치·운영 및 선가대 제거공사 / 선박 접안시설 설치·운영 및 선가대 원상회복
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.purpose == "선박 접안시설 설치·운영 및 선가대 원상회복"


def test_prefers_repeated_changed_purpose_without_slash():
    text = """
    목적 : 선박수리조선소 선가대(6기) 보강 설치․운영(침목 : 나무 → 콘트리트, 1 ․ 2 ․ 3 ․ 4 ․ 5 ․ 6열 선대 교체공사) 선박수리조선소 선가대(4기) 보강 설치․운영(침목 : 나무 → 콘트리트, 3 ․ 4 ․ 5 ․ 6열 선대 교체공사)
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.purpose == "선박수리조선소 선가대(4기) 보강 설치․운영(침목 : 나무 → 콘트리트, 3 ․ 4 ․ 5 ․ 6열 선대 교체공사)"


def test_prefers_numbered_repeated_purpose_tail():
    text = """
    목적 : 공기부양정 슬립웨이ㆍ비상 경사로 및 계류장 설치ㆍ운영 1. 슬립웨이(314m×41m), 보호옹벽 및 방풍벽 2. 호안(40m)공기부양정 슬립웨이 및 비상경사로 등 설치ㆍ운영 1. 슬립웨이(314m×41m), 보호옹벽 및 방풍벽 2. 호안(40m)
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.purpose == "공기부양정 슬립웨이 및 비상경사로 등 설치ㆍ운영 1. 슬립웨이(314m×41m), 보호옹벽 및 방풍벽 2. 호안(40m)"


def test_extracts_equipment_purpose_from_location_contaminated_bullets():
    text = """
    목적 : ㅇ 다목적 인양기(JIB형 5톤급) 3기 - 명지동 616-95 지선 물양장 - 대항동 외항포내 물양장 - 녹산동 215-7 지선 물양장 / ㅇ 다목적 인양기(JIB형 5톤급) 2기 - 명지동 616-95 지선 물양장 - 대항동 외항포내 물양장 ㅇ 다목적 인양기(JIB형 7.5톤급) 1기 - 녹산동 215-7 지선 물양장
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.purpose == "다목적 인양기(JIB형 5톤급) 2기, 다목적 인양기(JIB형 7.5톤급) 1기"


def test_rejects_location_contaminated_by_compact_header_sequence():
    text = """
    위치 : 사용목적점용기간면적(㎡)비 고N 35-33-00, E 129-31-30신조선박 외항계류'13.05.12 ~ '13.05.20 19,872
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.location is None


def test_rejects_location_when_body_sentence_is_attached():
    text = """
    위치 : 부산광역시 영도구 봉래동 4가 7번지 지선 공유수면 점용.사용 허가를 받은 경남조선(주)에 대하여 현장 실태조사를 실시한 결과 목적 외 무단 시설이 확인되었습니다.
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.location is None


def test_extracts_narrative_quoted_purpose_parenthesized_location():
    text = """
    (주)강남에서 신청한 "선가대 및 부표 설치"(부산 사하구 구평동 402-10번지 및 402-12번지 지선)목적의
    공유수면 점용·사용 변경 허가에 대하여 변경 허가하고자 합니다.
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.permittee_name == "(주)강남"
    assert fields.location == "부산 사하구 구평동 402-10번지 및 402-12번지 지선"
    assert fields.purpose == "선가대 및 부표 설치"


def test_extracts_narrative_location_before_purpose_phrase():
    text = """
    코리아에너지터미널㈜(대표이사 서경식)로부터 울산광역시 남구 황성동 882-1번지선 공유수면 일원에
    해수식 기화기 운영 및 소방용수 공급을 위한 해수 취·배수 목적의 공유수면 점용·사용허가 신청이 있어 검토합니다.
    """

    fields = PermitFieldExtractor().extract(text)

    assert fields.permittee_name == "코리아에너지터미널㈜"
    assert fields.location == "울산광역시 남구 황성동 882-1번지선 공유수면 일원"
    assert fields.purpose == "해수식 기화기 운영 및 소방용수 공급을 위한 해수 취·배수"

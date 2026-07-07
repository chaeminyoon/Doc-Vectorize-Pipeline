from lxml import etree

from src.parsers.odt_parser import NAMESPACES, ODTParser


def _parse_root(xml: str):
    return etree.fromstring(xml.encode("utf-8"))


def test_extract_names_from_text_keeps_real_names():
    names = ODTParser._extract_names_from_text("김민준, 이영훈")

    assert names == ["김민준", "이영훈"]


def test_extract_names_from_text_excludes_department_like_terms():
    names = ODTParser._extract_names_from_text(
        "부산지방, 해양항만, 선원해사, 안전과장, 항만물류, 항만건설, 항로표지"
    )

    assert names == []


def test_extract_author_name_from_tables_returns_none_for_org_listing():
    xml = f"""
    <office:document-content
        xmlns:office="{NAMESPACES['office']}"
        xmlns:table="{NAMESPACES['table']}"
        xmlns:text="{NAMESPACES['text']}">
      <office:body>
        <office:text>
          <table:table>
            <table:table-row>
              <table:table-cell><text:p>주무관</text:p></table:table-cell>
            </table:table-row>
            <table:table-row>
              <table:table-cell><text:p>부산지방, 해양항만, 선원해사, 안전과장</text:p></table:table-cell>
            </table:table-row>
          </table:table>
        </office:text>
      </office:body>
    </office:document-content>
    """

    parser = ODTParser()
    root = _parse_root(xml)

    assert parser._extract_author_name_from_tables(root) is None


def test_extract_author_name_from_tables_returns_name_from_approval_block():
    xml = f"""
    <office:document-content
        xmlns:office="{NAMESPACES['office']}"
        xmlns:table="{NAMESPACES['table']}"
        xmlns:text="{NAMESPACES['text']}">
      <office:body>
        <office:text>
          <table:table>
            <table:table-row>
              <table:table-cell><text:p>주무관</text:p></table:table-cell>
              <table:table-cell><text:p>과장</text:p></table:table-cell>
            </table:table-row>
            <table:table-row>
              <table:table-cell><text:p>김민준</text:p></table:table-cell>
              <table:table-cell><text:p>이영훈</text:p></table:table-cell>
            </table:table-row>
          </table:table>
        </office:text>
      </office:body>
    </office:document-content>
    """

    parser = ODTParser()
    root = _parse_root(xml)

    assert parser._extract_author_name_from_tables(root) == "김민준, 이영훈"

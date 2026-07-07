from lxml import etree

from src.parsers.odt_parser import NAMESPACES, ODTParser


def _parse_root(xml: str):
    return etree.fromstring(xml.encode("utf-8"))


def test_extract_names_from_text_excludes_receiver_titles():
    names = ODTParser._extract_names_from_text(
        "\uCC3D\uC6D0\uC2DC\uC7A5, \uC601\uC554\uAD70\uC218, \uD3EC\uD56D\uC2DC\uC7A5, "
        "\uD68C\uC7A5, \uADC0\uD558, \uC6B4\uC601\uC9C0\uC6D0"
    )

    assert names == []


def test_extract_names_from_text_excludes_common_nouns():
    names = ODTParser._extract_names_from_text(
        "\uC758\uACAC, \uB0B4\uC6A9, \uC8FC\uC694, \uC9C1\uC778, \uC2B9\uC778\uC758, \uC810\uC6A9\uC0AC\uC6A9"
    )

    assert names == []


def test_extract_names_from_text_excludes_domain_nouns():
    names = ODTParser._extract_names_from_text(
        "\uBCC0\uACBD\uD5C8\uAC00, \uBC95\uB960, \uC124\uCE58, \uC5C6\uC74C, "
        "\uACC4\uB958\uC2DC\uC124, \uBC30\uACBD, \uBCC0\uACBD\uC2B9\uC778, "
        "\uBD80\uC2DD\uC2DC\uD5D8, \uC0AC\uD558\uAD6C\uCCAD, \uC6B4\uC601, "
        "\uC810\uC0AC\uC6A9, \uC900\uC124, \uD0C0\uB2F9\uC131, \uBCF4\uACE0, "
        "\uBD80\uC0B0\uC870\uC120, \uC131\uCC3D\uAE30\uC5C5, \uC2E4\uC2DC\uAC04, "
        "\uC5B8\uB85C\uB529\uC120, \uD5C8\uAC00\uCDE8\uC18C, \uB3D9\uD574\uC804\uB825, "
        "\uD1A0\uAC74, \uC5F0\uC7A5\uC5D0, \uB300\uD574, \uB0B4\uC6A9\uC744, "
        "\uD569\uB2C8\uB2E4, \uD3C9\uD0DD\uB300\uD45C, \uAE40\uCCAD\uC218\uC5D0, "
        "\uB300\uD558\uC5EC, \uB4DC\uB9BD\uB2C8\uB2E4"
    )

    assert names == []


def test_extract_author_name_from_tables_skips_receiver_row_and_reads_approval_names():
    xml = f"""
    <office:document-content
        xmlns:office="{NAMESPACES['office']}"
        xmlns:table="{NAMESPACES['table']}"
        xmlns:text="{NAMESPACES['text']}">
      <office:body>
        <office:text>
          <table:table>
            <table:table-row>
              <table:table-cell><text:p>\uC218\uC2E0\uC790</text:p></table:table-cell>
              <table:table-cell>
                <text:p>\uCC3D\uC6D0\uC2DC\uC7A5(\uD574\uC591\uD56D\uB9CC\uACFC\uC7A5), \uC120\uC6D0\uD574\uC0AC\uC548\uC804\uACFC\uC7A5</text:p>
              </table:table-cell>
            </table:table-row>
            <table:table-row>
              <table:table-cell><text:p></text:p></table:table-cell>
            </table:table-row>
            <table:table-row>
              <table:table-cell><text:p>\uC8FC\uBB34\uAD00</text:p></table:table-cell>
              <table:table-cell><text:p>\uACFC\uC7A5</text:p></table:table-cell>
              <table:table-cell><text:p>\uC804\uACB0 2016. 12. 23.</text:p></table:table-cell>
            </table:table-row>
            <table:table-row>
              <table:table-cell><text:p>\uD669\uD6C8</text:p></table:table-cell>
              <table:table-cell><text:p>\uC11C\uC815\uCCA0</text:p></table:table-cell>
            </table:table-row>
          </table:table>
        </office:text>
      </office:body>
    </office:document-content>
    """

    parser = ODTParser()
    root = _parse_root(xml)

    assert parser._extract_author_name_from_tables(root) == "\uD669\uD6C8, \uC11C\uC815\uCCA0"

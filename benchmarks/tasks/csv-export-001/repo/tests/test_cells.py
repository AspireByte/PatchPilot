"""文本单元格转义的正常用例。"""

from csv_export_demo.cells import escape_cell


def test_direct_formula_prefixes() -> None:
    for value in ["=1+2", "+123", "-42", "@name"]:
        assert escape_cell(value) == "'" + value


def test_plain_text_is_unchanged() -> None:
    assert escape_cell("报告,中文") == "报告,中文"
    assert escape_cell("value=1") == "value=1"


def test_empty_and_whitespace_are_unchanged() -> None:
    assert escape_cell("") == ""
    assert escape_cell(" \t\n") == " \t\n"


def test_existing_apostrophe_is_not_duplicated() -> None:
    assert escape_cell("'=1+2") == "'=1+2"

"""前导空白后的公式前缀未转义的断言失败复现。"""

from csv_export_demo.cells import escape_cell


def test_repro_spaces_before_formula() -> None:
    assert escape_cell("  =1+2") == "'  =1+2"


def test_repro_tab_before_formula() -> None:
    assert escape_cell("\t+123") == "'\t+123"

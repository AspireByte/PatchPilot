"""公式前缀文本转义任务的独立维护者验收。"""

import pytest
from csv_export_demo.cells import escape_cell


@pytest.mark.parametrize("value", [" \t=2+2", "\r\n+123", " -42", "\t @name"])
def test_whitespace_before_each_dangerous_prefix(value: str) -> None:
    assert escape_cell(value) == "'" + value


def test_safe_whitespace_and_noninitial_formula_character() -> None:
    for value in ["   普通文本", "\t", "\r\n", " ", "   x=1", "  '=1"]:
        assert escape_cell(value) == value


def test_original_content_and_idempotence() -> None:
    original = '\t =中文,"内容"\n末尾 '
    escaped = escape_cell(original)
    assert escaped == "'" + original
    assert escaped[1:] == original
    assert escape_cell(escaped) == escaped
    assert escape_cell("普通") == "普通"

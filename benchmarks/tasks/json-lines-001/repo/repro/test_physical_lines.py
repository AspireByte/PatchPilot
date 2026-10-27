"""错误必须对应原始物理行号的断言失败复现。"""

from json_lines_demo.parser import parse_json_lines


def test_repro_leading_blank_line() -> None:
    result = parse_json_lines('\n{"x":1}\n坏行')
    assert result == {"records": [{"x": 1}], "error": {"line": 3, "message": "无效 JSON"}}


def test_repro_blank_line_between_records() -> None:
    result = parse_json_lines('1\n \t\n2\n{')
    assert result == {"records": [1, 2], "error": {"line": 4, "message": "无效 JSON"}}

"""内存 JSONL 解析的正常用例。"""

from json_lines_demo.parser import parse_json_lines


def test_consecutive_objects() -> None:
    assert parse_json_lines('{"a":1}\n{"b":2}') == {
        "records": [{"a": 1}, {"b": 2}], "error": None
    }


def test_json_scalars_and_arrays() -> None:
    assert parse_json_lines('null\nfalse\n[1,2]\n"中文"') == {
        "records": [None, False, [1, 2], "中文"], "error": None
    }


def test_blank_text() -> None:
    assert parse_json_lines("\n \t\n") == {"records": [], "error": None}


def test_invalid_line_returns_error_not_exception() -> None:
    assert parse_json_lines('1\n无效\n2') == {
        "records": [1], "error": {"line": 2, "message": "无效 JSON"}
    }

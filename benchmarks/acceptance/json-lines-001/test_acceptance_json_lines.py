"""JSONL 任务的独立维护者验收。"""

import pytest
from json_lines_demo.parser import parse_json_lines


@pytest.mark.parametrize(
    ("text", "records", "error"),
    [
        ("\r\n \t\r\n{", [], {"line": 3, "message": "无效 JSON"}),
        ('null\n\nfalse\n \n?\n{"ignored":1}', [None, False], {"line": 5, "message": "无效 JSON"}),
        (' \n{"ok":1}\n\t\n[2]\n', [{"ok": 1}, [2]], None),
        ('\t\n"未闭合\n1', [], {"line": 2, "message": "无效 JSON"}),
        ("", [], None),
    ],
)
def test_original_lines_and_partial_results(text: str, records: list, error: dict | None) -> None:
    assert parse_json_lines(text) == {"records": records, "error": error}

"""请求头任务的独立维护者验收。"""

import pytest
from header_demo.headers import get_header


@pytest.mark.parametrize("name", ["x-aBc", "X-ABC", "x-abc"])
def test_ascii_case_variants(name: str) -> None:
    headers = {"Host": "example.invalid", "X-Abc": "\t原值  "}
    before = headers.copy()
    assert get_header(headers, name, "备用") == "\t原值  "
    assert headers == before


def test_first_matching_entry_wins() -> None:
    headers = {"X-Key": "首次", "x-key": "其次", "X-KEY": "末次"}
    assert get_header(headers, "x-key") == "首次"
    assert list(headers) == ["X-Key", "x-key", "X-KEY"]


def test_empty_default_and_empty_value_are_distinct_from_missing() -> None:
    assert get_header({}, "X-None", "") == ""
    assert get_header({"x-empty": ""}, "X-EMPTY", "备用") == ""
    assert get_header({"X-Other": "值"}, "x-missing") is None

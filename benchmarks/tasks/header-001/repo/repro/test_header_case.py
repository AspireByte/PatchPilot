"""用户可见的大小写匹配失败复现。"""

from header_demo.headers import get_header


def test_repro_uppercase_query() -> None:
    assert get_header({"Content-Type": "application/json"}, "CONTENT-TYPE") == "application/json"


def test_repro_lowercase_query() -> None:
    assert get_header({"X-Trace": "abc"}, "x-trace", "缺失") == "abc"

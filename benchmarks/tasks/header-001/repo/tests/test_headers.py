"""请求头查询的正常用例。"""

from header_demo.headers import get_header


def test_exact_name() -> None:
    assert get_header({"Content-Type": "text/plain"}, "Content-Type") == "text/plain"


def test_missing_uses_default() -> None:
    assert get_header({}, "Accept") is None
    assert get_header({"Host": "example.invalid"}, "Accept", "备用") == "备用"


def test_preserves_values_and_mapping() -> None:
    headers = {"X-Label": "  中文\t", "X-Empty": ""}
    before = headers.copy()
    assert get_header(headers, "X-Label") == "  中文\t"
    assert get_header(headers, "X-Empty", "备用") == ""
    assert headers == before

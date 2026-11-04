"""维护者验收用例，仅复制到公开快照之外的临时目录运行。"""

import pytest
from null_demo.labels import resolve_label


@pytest.mark.parametrize("fallback", ["默认", "", " ", "另一个"])
def test_preserves_empty_label_with_any_fallback(fallback: str) -> None:
    assert resolve_label("", fallback=fallback) == ""


@pytest.mark.parametrize("value", ["\t", "\n", "  标签  "])
def test_does_not_normalize_whitespace(value: str) -> None:
    assert resolve_label(value, fallback="") == value


def test_calls_do_not_share_fallback_state() -> None:
    assert resolve_label(None, fallback="甲") == "甲"
    assert resolve_label("", fallback="乙") == ""
    assert resolve_label(None, fallback="") == ""
    assert resolve_label(None) == "未命名"

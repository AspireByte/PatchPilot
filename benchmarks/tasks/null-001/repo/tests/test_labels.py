import pytest
from null_demo.labels import resolve_label


@pytest.mark.parametrize("fallback", ["默认", "", " "])
def test_none_uses_fallback(fallback: str) -> None:
    assert resolve_label(None, fallback=fallback) == fallback


@pytest.mark.parametrize("value", ["name", "中文", " ", "0"])
def test_preserves_nonempty_strings(value: str) -> None:
    assert resolve_label(value, fallback="备用") == value


def test_default_fallback() -> None:
    assert resolve_label(None) == "未命名"

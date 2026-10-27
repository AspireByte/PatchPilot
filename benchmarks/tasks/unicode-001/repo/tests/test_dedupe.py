"""字符串去重的正常用例。"""

from unicode_demo.dedupe import unique_strings


def test_casefold_keeps_first_original() -> None:
    assert unique_strings(["Straße", "STRASSE", "straße"]) == ["Straße"]


def test_distinct_values_preserve_order_and_input() -> None:
    values = ["中文", "Alpha", "BETA", "alpha"]
    before = values.copy()
    assert unique_strings(values) == ["中文", "Alpha", "BETA"]
    assert values == before


def test_empty_input_and_empty_string() -> None:
    assert unique_strings([]) == []
    assert unique_strings(["", ""]) == [""]

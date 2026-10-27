"""规范等价字符串重复出现的断言失败复现。"""

from unicode_demo.dedupe import unique_strings


def test_repro_decomposed_accent() -> None:
    assert unique_strings(["Café", "CAFÉ"]) == ["Café"]


def test_repro_hangul_composition() -> None:
    assert unique_strings(["가", "가"]) == ["가"]

"""Unicode 去重任务的独立维护者验收。"""

import pytest
from unicode_demo.dedupe import unique_strings


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (["Å", "Å", "å", "终"], ["Å", "终"]),
        (["ạ́", "ạ́"], ["ạ́"]),
        (["É", "É", "é"], ["É"]),
    ],
)
def test_canonical_equivalence_preserves_first(values: list[str], expected: list[str]) -> None:
    before = values.copy()
    assert unique_strings(values) == expected
    assert values == before


def test_no_compatibility_normalization_or_whitespace_trimming() -> None:
    assert unique_strings(["Ａ", "A", " A", "A ", "a"]) == ["Ａ", "A", " A", "A "]


def test_calls_have_no_shared_state_and_return_new_lists() -> None:
    values = ["é", "É"]
    first = unique_strings(values)
    assert first == ["é"]
    first.append("外加")
    assert unique_strings(values) == ["é"]
    assert unique_strings([]) == []

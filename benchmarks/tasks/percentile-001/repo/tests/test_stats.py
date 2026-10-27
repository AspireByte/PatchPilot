"""整数排名与非法参数的正常用例。"""

import pytest
from percentile_demo.stats import percentile


def test_exact_rank_and_unchanged_input() -> None:
    values = [40, 10, 30, 20]
    assert percentile(values, 50) == 20
    assert values == [40, 10, 30, 20]


def test_hundred_returns_maximum() -> None:
    assert percentile([3, 8, 2], 100) == 8


def test_single_value() -> None:
    assert percentile([7], 50) == 7


def test_empty_is_rejected() -> None:
    with pytest.raises(ValueError):
        percentile([], 50)


def test_zero_percent_is_rejected() -> None:
    with pytest.raises(ValueError):
        percentile([1, 2], 0)

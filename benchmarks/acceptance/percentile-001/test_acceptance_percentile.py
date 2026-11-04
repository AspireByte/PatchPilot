"""nearest-rank 任务的独立维护者验收。"""

import pytest
from percentile_demo.stats import percentile


@pytest.mark.parametrize(
    ("values", "p", "expected"),
    [
        ([9, 1, 5], 1, 1),
        ([9, 1, 5], 34, 5),
        ([9, 1, 5], 67, 9),
        ([-5, -2, -2, 8], 51, -2),
    ],
)
def test_nearest_rank_without_interpolation(values: list[float], p: float, expected: float) -> None:
    before = values.copy()
    assert percentile(values, p) == expected
    assert values == before


def test_invalid_percentages_raise_value_error() -> None:
    for p in [-1, 0, 100.01, float("nan"), float("inf"), True, "50", None]:
        with pytest.raises(ValueError):
            percentile([1, 2, 3], p)


def test_invalid_samples_raise_value_error() -> None:
    for values in [[], [True], [1, "2"], [None], [float("nan")], [float("-inf")]]:
        with pytest.raises(ValueError):
            percentile(values, 50)

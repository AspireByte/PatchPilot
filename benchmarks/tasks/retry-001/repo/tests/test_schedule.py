"""首两轮、上限及输入校验的正常行为。"""

import pytest

from retry_demo.schedule import backoff


@pytest.mark.parametrize(
    ("attempt", "base", "cap", "expected"),
    [(0, 3, 100, 3), (1, 3, 100, 6), (6, 2, 10, 10)],
)
def test_initial_and_capped_delay(attempt, base, cap, expected):
    assert backoff(attempt, base=base, cap=cap) == expected


def test_reject_negative_attempt():
    with pytest.raises(ValueError):
        backoff(-1, base=2, cap=10)

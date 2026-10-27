"""多次重试的可见等待时间复现。"""

import pytest

from retry_demo.schedule import backoff


@pytest.mark.parametrize(
    ("attempt", "base", "cap", "expected"), [(2, 3, 100, 12), (4, 2, 20, 20)]
)
def test_repro_exponential_delay(attempt, base, cap, expected):
    assert backoff(attempt, base=base, cap=cap) == expected

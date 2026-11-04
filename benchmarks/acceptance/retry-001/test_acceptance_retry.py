"""维护者指数序列及非法参数验收。"""

import pytest
from retry_demo.schedule import backoff


def test_exponential_sequence():
    assert [backoff(n, base=5, cap=1000) for n in range(6)] == [5, 10, 20, 40, 80, 160]


def test_cap_smaller_than_initial_delay():
    assert backoff(0, base=20, cap=7) == 7


def test_exact_and_between_powers_cap():
    assert backoff(3, base=2, cap=16) == 16
    assert backoff(3, base=2, cap=13) == 13


def test_large_attempt():
    assert backoff(10000, base=1, cap=99) == 99


def test_invalid_types_in_each_position():
    for attempt, base, cap in ((True, 2, 9), (1.5, 2, 9), (1, False, 9), (1, 2, "9")):
        with pytest.raises(ValueError):
            backoff(attempt, base=base, cap=cap)


def test_invalid_ranges_in_each_position():
    for attempt, base, cap in ((-2, 2, 9), (0, 0, 9), (0, -1, 9), (0, 2, 0), (0, 2, -9)):
        with pytest.raises(ValueError):
            backoff(attempt, base=base, cap=cap)

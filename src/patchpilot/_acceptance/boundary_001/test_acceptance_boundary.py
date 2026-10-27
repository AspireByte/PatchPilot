"""维护者验收用例，仅复制到公开快照之外的临时目录运行。"""

import pytest
from boundary_demo.window import select_window


@pytest.mark.parametrize(("start", "end"), [(0, 0), (4, 4), (-3, -3), (0, 1), (-5, 0)])
def test_half_open_windows(start: int, end: int) -> None:
    timestamps = [end, start, start - 1, end + 1, start, end - 1]
    original = timestamps.copy()
    expected = [timestamp for timestamp in timestamps if start <= timestamp < end]
    assert select_window(timestamps, start=start, end=end) == expected
    assert timestamps == original


def test_adjacent_windows_do_not_overlap() -> None:
    timestamps = [2, 0, 1, 2, 3, 4]
    left = select_window(timestamps, start=0, end=2)
    right = select_window(timestamps, start=2, end=4)
    assert left == [0, 1]
    assert right == [2, 2, 3]
    assert len(left) + len(right) == len(select_window(timestamps, start=0, end=4))


def test_arbitrary_precision_integer_boundaries() -> None:
    start = 10**30
    assert select_window([start + 2, start, start + 1], start=start, end=start + 2) == [
        start,
        start + 1,
    ]

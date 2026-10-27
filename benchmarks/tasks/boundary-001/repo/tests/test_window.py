import pytest
from boundary_demo.window import select_window


def test_selects_interior_values() -> None:
    assert select_window([-2, 1, 3, 9], start=0, end=5) == [1, 3]


def test_preserves_order_duplicates_and_input() -> None:
    timestamps = [3, -1, 1, 3, 8]
    assert select_window(timestamps, start=0, end=5) == [3, 1, 3]
    assert timestamps == [3, -1, 1, 3, 8]


def test_empty_input() -> None:
    assert select_window([], start=0, end=5) == []


@pytest.mark.parametrize(("start", "end"), [(2, 1), (0, -1)])
def test_rejects_reversed_window(start: int, end: int) -> None:
    with pytest.raises(ValueError):
        select_window([0], start=start, end=end)


def test_returns_a_new_list() -> None:
    timestamps = [1, 2]
    result = select_window(timestamps, start=0, end=3)
    assert result == timestamps
    assert result is not timestamps

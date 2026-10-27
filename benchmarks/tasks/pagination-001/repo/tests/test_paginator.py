import pytest
from pagination_demo.paginator import Row, paginate


def test_pages_with_distinct_timestamps() -> None:
    rows = [Row(3, 102), Row(1, 100), Row(2, 101)]
    assert [row.id for row in paginate(rows, limit=2)] == [1, 2]
    assert [row.id for row in paginate(rows, cursor=(101, 2), limit=2)] == [3]


def test_empty_rows() -> None:
    assert paginate([]) == []


def test_cursor_after_last_row() -> None:
    assert paginate([Row(1, 100)], cursor=(200, 9)) == []


def test_input_order_is_not_changed() -> None:
    rows = [Row(2, 101), Row(1, 100)]
    paginate(rows)
    assert [row.id for row in rows] == [2, 1]


@pytest.mark.parametrize("limit", [0, -1])
def test_nonpositive_limit_is_rejected(limit: int) -> None:
    with pytest.raises(ValueError):
        paginate([Row(1, 100)], limit=limit)

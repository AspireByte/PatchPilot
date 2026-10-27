"""维护者验收用例；不复制到公开任务 repo 或候选工作区。"""

import pytest
from pagination_demo.paginator import Row, paginate


@pytest.mark.parametrize("limit", [1, 2, 3, 4])
def test_full_traversal_with_ties(limit: int) -> None:
    rows = [Row(5, 102), Row(3, 100), Row(1, 100), Row(4, 101), Row(2, 100)]
    observed = []
    cursor = None
    for _ in range(7):
        page = paginate(rows, cursor=cursor, limit=limit)
        if not page:
            break
        observed.extend(row.id for row in page)
        cursor = (page[-1].created_at, page[-1].id)
    assert observed == [1, 2, 3, 4, 5]
    assert [row.id for row in rows] == [5, 3, 1, 4, 2]


def test_cursor_between_equal_timestamp_records() -> None:
    rows = [Row(1, 100), Row(5, 100), Row(8, 100), Row(9, 101)]
    assert [row.id for row in paginate(rows, cursor=(100, 4), limit=10)] == [5, 8, 9]


def test_all_records_have_the_same_timestamp() -> None:
    rows = [Row(8, 100), Row(1, 100), Row(4, 100)]
    assert [row.id for row in paginate(rows, cursor=(100, 1), limit=10)] == [4, 8]


def test_later_timestamps_do_not_require_larger_ids() -> None:
    rows = [Row(2, 102), Row(9, 100), Row(5, 101), Row(1, 101)]
    assert [row.id for row in paginate(rows, cursor=(100, 9), limit=10)] == [1, 5, 2]
    observed = []
    cursor = None
    for _ in range(6):
        page = paginate(rows, cursor=cursor, limit=1)
        if not page:
            break
        observed.extend(row.id for row in page)
        cursor = (page[-1].created_at, page[-1].id)
    assert observed == [9, 1, 5, 2]

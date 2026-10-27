from pagination_demo.paginator import Row, paginate


def test_pages_keep_records_with_equal_timestamps() -> None:
    rows = [Row(3, 101), Row(2, 100), Row(1, 100)]
    first = paginate(rows, limit=1)
    assert [row.id for row in first] == [1]

    cursor = (first[-1].created_at, first[-1].id)
    second = paginate(rows, cursor=cursor, limit=1)
    assert [row.id for row in second] == [2]

    cursor = (second[-1].created_at, second[-1].id)
    third = paginate(rows, cursor=cursor, limit=1)
    assert [row.id for row in third] == [3]
    assert paginate(rows, cursor=(101, 3), limit=1) == []

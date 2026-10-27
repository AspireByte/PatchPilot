"""平分条目的公开顺序复现。"""

import pytest

from ranking_demo.leaderboard import rank_records


@pytest.mark.parametrize(
    ("records", "expected"),
    [
        ([{"id": "b", "score": 5}, {"id": "a", "score": 5}], ["a", "b"]),
        (
            [{"id": "z", "score": 9}, {"id": "c", "score": 2}, {"id": "b", "score": 2}],
            ["z", "b", "c"],
        ),
    ],
)
def test_repro_tie_order(records, expected):
    assert [item["id"] for item in rank_records(records)] == expected

"""无平分的正常榜单及输入保护。"""

from ranking_demo.leaderboard import rank_records


def test_empty_ranking():
    assert rank_records([]) == []


def test_score_descending():
    records = [{"id": "z", "score": 1}, {"id": "a", "score": 3}]
    assert [item["score"] for item in rank_records(records)] == [3, 1]


def test_negative_scores():
    records = [{"id": "a", "score": -8}, {"id": "b", "score": -2}]
    assert [item["score"] for item in rank_records(records)] == [-2, -8]


def test_input_order_unchanged():
    records = [{"id": "a", "score": 1}, {"id": "b", "score": 5}]
    result = rank_records(records)
    assert records == [{"id": "a", "score": 1}, {"id": "b", "score": 5}]
    assert result is not records

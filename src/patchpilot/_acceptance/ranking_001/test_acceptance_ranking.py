"""维护者混合方向与稳定排序验收。"""

from ranking_demo.leaderboard import rank_records


def test_negative_score_ties():
    records = [{"id": "b", "score": -1}, {"id": "a", "score": -1}, {"id": "z", "score": 0}]
    assert [item["id"] for item in rank_records(records)] == ["z", "a", "b"]


def test_ids_use_lexical_order():
    records = [{"id": value, "score": 7} for value in ("2", "10", "1")]
    assert [item["id"] for item in rank_records(records)] == ["1", "10", "2"]


def test_identical_keys_are_stable():
    records = [{"id": "a", "score": 5, "tag": n} for n in (3, 1, 2)]
    assert [item["tag"] for item in rank_records(records)] == [3, 1, 2]


def test_empty_output_is_new_list():
    records = []
    assert rank_records(records) is not records


def test_retains_record_identity_and_input_order():
    first = {"id": "b", "score": 2, "metadata": [1]}
    second = {"id": "a", "score": 2, "metadata": [2]}
    records = [first, second]
    result = rank_records(records)
    assert records[0] is first and records[1] is second
    assert result[0] is second and result[1] is first
    assert first["metadata"] == [1] and second["metadata"] == [2]


def test_large_integer_scores():
    records = [{"id": "z", "score": 10**80}, {"id": "a", "score": 10**80 + 1}]
    assert [item["id"] for item in rank_records(records)] == ["a", "z"]

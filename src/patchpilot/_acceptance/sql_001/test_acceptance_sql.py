"""维护者内存筛选真值表和参数绑定验收。"""

from itertools import product

from sql_demo.visibility import visible_ids


def test_all_boolean_combinations():
    records = [
        (index, owner, active, public)
        for index, (active, owner, public) in enumerate(
            product((0, 1), ("alice", "bob"), (0, 1)), start=1
        )
    ]
    assert visible_ids(records, "alice") == [5, 6, 8]


def test_owner_is_bound_literal():
    owner = "' OR 1=1 --"
    records = [(1, owner, 1, 0), (2, "other", 1, 0), (3, "other", 1, 1)]
    assert visible_ids(records, owner) == [1, 3]


def test_numeric_id_order():
    records = [(10, "alice", 1, 0), (2, "alice", 1, 1), (-1, "bob", 1, 1)]
    assert visible_ids(records, "alice") == [-1, 2, 10]


def test_unicode_owner_and_exact_match():
    records = [(1, "甲", 1, 0), (2, "甲乙", 1, 0), (3, "乙", 0, 1)]
    assert visible_ids(records, "甲") == [1]


def test_input_not_modified():
    records = [(3, "bob", 0, 1), (2, "alice", 1, 0)]
    before = list(records)
    assert visible_ids(records, "alice") == [2]
    assert records == before


def test_calls_use_separate_memory_databases():
    assert visible_ids([(1, "alice", 1, 0)], "alice") == [1]
    assert visible_ids([], "alice") == []
    assert visible_ids([(1, "bob", 1, 1)], "alice") == [1]

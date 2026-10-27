"""启用记录及非公开停用记录的正常筛选。"""

from sql_demo.visibility import visible_ids


def test_empty_records():
    assert visible_ids([], "alice") == []


def test_active_owned_record():
    assert visible_ids([(1, "alice", 1, 0), (2, "bob", 1, 0)], "alice") == [1]


def test_active_public_record():
    assert visible_ids([(3, "bob", 1, 1)], "alice") == [3]


def test_inactive_private_records():
    assert visible_ids([(1, "alice", 0, 0), (2, "bob", 0, 0)], "alice") == []

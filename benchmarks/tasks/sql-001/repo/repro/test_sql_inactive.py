"""停用的公开记录不应返回。"""

import pytest

from sql_demo.visibility import visible_ids


@pytest.mark.parametrize("record_owner", ["alice", "bob"])
def test_repro_inactive_public_record(record_owner):
    assert visible_ids([(7, record_owner, 0, 1)], "alice") == []

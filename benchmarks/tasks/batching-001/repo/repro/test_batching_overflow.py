"""加入下一条目将超过容量时的用户复现。"""

import pytest

from batching_demo.packer import batch_sizes


@pytest.mark.parametrize(
    ("sizes", "capacity", "expected"),
    [([4, 3], 5, [[4], [3]]), ([2, 3, 4], 6, [[2, 3], [4]])],
)
def test_repro_batch_capacity(sizes, capacity, expected):
    assert batch_sizes(sizes, capacity) == expected

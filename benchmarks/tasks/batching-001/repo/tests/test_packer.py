"""不跨越容量及非法超大条目的正常场景。"""

import pytest

from batching_demo.packer import batch_sizes


def test_empty_input():
    assert batch_sizes([], 5) == []


def test_single_under_capacity_batch():
    assert batch_sizes([1, 2], 5) == [[1, 2]]


def test_exact_capacity_batches():
    assert batch_sizes([2, 3, 5], 5) == [[2, 3], [5]]


def test_reject_oversized_item():
    with pytest.raises(ValueError):
        batch_sizes([6], 5)

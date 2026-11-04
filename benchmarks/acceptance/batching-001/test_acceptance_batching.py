"""维护者贪心分批、不变性及非法输入验收。"""

import pytest
from batching_demo.packer import batch_sizes


def test_multiple_overflow_transitions():
    assert batch_sizes([4, 4, 2, 5, 1], 6) == [[4], [4, 2], [5, 1]]


def test_no_empty_batches_at_capacity():
    assert batch_sizes([3, 3, 3], 3) == [[3], [3], [3]]


def test_greedy_order_not_bin_packing():
    assert batch_sizes([3, 1, 2, 2], 4) == [[3, 1], [2, 2]]
    assert batch_sizes([3, 2, 1], 4) == [[3], [2, 1]]


def test_input_and_batches_are_independent():
    sizes = [2, 2, 2]
    result = batch_sizes(sizes, 3)
    assert sizes == [2, 2, 2]
    result[0].append(1)
    assert sizes == [2, 2, 2]
    assert result[1:] == [[2], [2]]


def test_invalid_capacity_even_with_empty_input():
    for capacity in (0, -1, True, 1.5, "5"):
        with pytest.raises(ValueError):
            batch_sizes([], capacity)


def test_invalid_items_and_oversized_middle_item():
    for sizes in ([0], [-1], [True], [1.5], ["2"], [1, 6, 1]):
        with pytest.raises(ValueError):
            batch_sizes(sizes, 5)

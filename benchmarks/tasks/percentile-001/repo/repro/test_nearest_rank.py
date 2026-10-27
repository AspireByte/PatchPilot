"""非整数排名时选错样本的断言失败复现。"""

from percentile_demo.stats import percentile


def test_repro_fractional_rank() -> None:
    assert percentile([10, 20, 30, 40], 60) == 30


def test_repro_high_percentile_selects_maximum() -> None:
    assert percentile([5, 1, 4, 2, 3], 99) == 5

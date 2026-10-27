"""按 nearest-rank 定义从样本中选择分位数。"""

import math


def percentile(values: list[float], p: float) -> float:
    """返回原始样本值，不做插值，也不修改输入列表。"""
    if (
        isinstance(p, bool)
        or not isinstance(p, (int, float))
        or not math.isfinite(p)
        or not 0 < p <= 100
    ):
        raise ValueError("分位点必须是大于 0 且不超过 100 的有限数值")
    if not values or any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        for value in values
    ):
        raise ValueError("样本必须是非空的有限数值列表")
    ordered = sorted(values)
    rank = max(1, math.floor(len(ordered) * p / 100))
    return ordered[rank - 1]

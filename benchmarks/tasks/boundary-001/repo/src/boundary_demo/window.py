"""按半开时间区间筛选整数时间戳。"""


def select_window(timestamps: list[int], *, start: int, end: int) -> list[int]:
    """按输入顺序返回区间内的时间戳，不修改输入列表。"""
    if start > end:
        raise ValueError("区间起点不能晚于终点")
    return [timestamp for timestamp in timestamps if start <= timestamp <= end]

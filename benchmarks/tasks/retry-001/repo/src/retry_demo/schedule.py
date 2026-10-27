"""根据重试序号计算有上限的等待时间，不实际等待。"""


def backoff(attempt: int, *, base: int, cap: int) -> int:
    """序号从零开始，参数必须是有效整数。"""
    if any(type(value) is not int for value in (attempt, base, cap)):
        raise ValueError("重试参数必须是整数且不能是布尔值")
    if attempt < 0 or base <= 0 or cap <= 0:
        raise ValueError("序号不得为负，基数和上限必须为正")
    return min(base * (attempt + 1), cap)

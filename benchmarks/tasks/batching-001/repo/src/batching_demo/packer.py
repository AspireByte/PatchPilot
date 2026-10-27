"""将正整数大小序列按容量连续分批。"""


def batch_sizes(sizes: list[int], capacity: int) -> list[list[int]]:
    """保持输入顺序，返回新的非空批次列表。"""
    if type(capacity) is not int or capacity <= 0:
        raise ValueError("容量必须是正整数")
    if any(type(size) is not int or size <= 0 or size > capacity for size in sizes):
        raise ValueError("条目必须是正整数且不能超过容量")
    batches = []
    current = []
    total = 0
    for size in sizes:
        current.append(size)
        total += size
        if total >= capacity:
            batches.append(current)
            current = []
            total = 0
    if current:
        batches.append(current)
    return batches

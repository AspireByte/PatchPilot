"""校验闭区间内的整数数量。"""


def validate_quantity(value: object, *, minimum: int = 0, maximum: int = 100) -> int:
    """返回合法数量，非法类型、越界值和倒置范围抛出值错误。"""
    if minimum > maximum:
        raise ValueError("数量下限不能大于上限")
    if not isinstance(value, int):
        raise ValueError("数量必须是非布尔整数")
    if not minimum <= value <= maximum:
        raise ValueError("数量超出允许范围")
    return value

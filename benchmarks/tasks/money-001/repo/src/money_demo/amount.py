"""将十进制金额格式化为两位小数。"""

from decimal import Decimal, InvalidOperation, localcontext


def format_money(value: str) -> str:
    """接受有限十进制字符串，返回固定两位金额。"""
    try:
        amount = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("金额必须是有限十进制字符串") from exc
    if not amount.is_finite():
        raise ValueError("金额必须是有限十进制字符串")
    with localcontext() as context:
        context.prec = max(28, len(amount.as_tuple().digits) + abs(amount.as_tuple().exponent) + 2)
        rounded = amount.quantize(Decimal("0.01"))
    return format(rounded, ".2f")

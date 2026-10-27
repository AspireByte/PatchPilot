"""为可选字符串提供显示标签。"""


def resolve_label(value: str | None, *, fallback: str = "未命名") -> str:
    """缺失标签使用备用值，其余字符串保持原样。"""
    return value or fallback

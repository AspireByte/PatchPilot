"""在文本导出之前为可能被解释为公式的单元格添加标记。"""


def escape_cell(value: str) -> str:
    """保留完整原值，仅在危险文本前添加一个单引号。"""
    dangerous_prefixes = ("=", "+", "-", "@")
    if value.startswith(dangerous_prefixes):
        return "'" + value
    return value

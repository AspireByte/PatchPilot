"""查询 HTTP 请求头，保留调用者提供的原始内容。"""

from collections.abc import Mapping


def get_header(
    headers: Mapping[str, str], name: str, default: str | None = None
) -> str | None:
    """返回首个匹配头的值，缺失时返回默认值。"""
    for key, value in headers.items():
        if key == name:
            return value
    return default

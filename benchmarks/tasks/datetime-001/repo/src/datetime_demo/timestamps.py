"""将带时区的 ISO 8601 时间统一表示为 UTC。"""

from datetime import datetime, timezone


def to_utc(value: str) -> datetime:
    """返回同一瞬间的 UTC 时间，拒绝无时区输入。"""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("时间必须包含时区")
    return parsed.replace(tzinfo=timezone.utc)

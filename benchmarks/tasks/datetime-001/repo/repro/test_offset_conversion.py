"""跨日期时区换算的断言失败复现。"""

from datetime_demo.timestamps import to_utc


def test_repro_positive_offset_crosses_previous_day() -> None:
    assert to_utc("2025-01-01T01:02:03.123456+08:00").isoformat() == "2024-12-31T17:02:03.123456+00:00"


def test_repro_negative_offset_crosses_next_day() -> None:
    assert to_utc("2025-01-01T23:45:00-03:30").isoformat() == "2025-01-02T03:15:00+00:00"

"""UTC 输入与无效时间的正常用例。"""

from datetime import datetime, timezone

import pytest
from datetime_demo.timestamps import to_utc


def test_z_suffix() -> None:
    assert to_utc("2025-03-04T12:13:14Z") == datetime(2025, 3, 4, 12, 13, 14, tzinfo=timezone.utc)


def test_zero_offset_preserves_microseconds() -> None:
    result = to_utc("2025-03-04T12:13:14.123456+00:00")
    assert result.microsecond == 123456
    assert result.tzinfo is timezone.utc


def test_naive_is_rejected() -> None:
    with pytest.raises(ValueError):
        to_utc("2025-03-04T12:13:14")


def test_invalid_is_rejected() -> None:
    with pytest.raises(ValueError):
        to_utc("不是时间")

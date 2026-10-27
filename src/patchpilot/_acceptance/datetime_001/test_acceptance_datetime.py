"""时间转换任务的独立维护者验收。"""

from datetime import UTC, datetime

import pytest
from datetime_demo.timestamps import to_utc


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2024-03-01T00:15:00.000007+05:45", "2024-02-29T18:30:00.000007+00:00"),
        ("2025-12-31T23:59:59.654321-09:30", "2026-01-01T09:29:59.654321+00:00"),
        ("2025-06-10T02:03:04.001002+00:00", "2025-06-10T02:03:04.001002+00:00"),
        ("2025-06-10T02:03:04.000001Z", "2025-06-10T02:03:04.000001+00:00"),
    ],
)
def test_same_instant_and_microseconds(value: str, expected: str) -> None:
    result = to_utc(value)
    assert result.isoformat() == expected
    assert result.tzinfo is UTC
    assert result.timestamp() == datetime.fromisoformat(value).timestamp()


@pytest.mark.parametrize("value", ["2025-01-01", "2025-01-01T00:00:00.000001"])
def test_no_local_timezone_fallback(value: str) -> None:
    with pytest.raises(ValueError):
        to_utc(value)

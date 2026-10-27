"""不涉及中点舍入的正常金额。"""

import pytest

from money_demo.amount import format_money


@pytest.mark.parametrize(
    ("value", "expected"),
    [("12", "12.00"), ("1.234", "1.23"), ("-2.346", "-2.35"), ("0.00", "0.00")],
)
def test_format_money(value, expected):
    assert format_money(value) == expected

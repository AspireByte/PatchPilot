"""用户可见的正负中点复现。"""

import pytest

from money_demo.amount import format_money


@pytest.mark.parametrize(("value", "expected"), [("1.005", "1.01"), ("-1.005", "-1.01")])
def test_repro_midpoint(value, expected):
    assert format_money(value) == expected

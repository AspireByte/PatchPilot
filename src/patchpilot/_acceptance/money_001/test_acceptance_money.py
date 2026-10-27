"""维护者金额契约验收，不随任务快照公开。"""

from decimal import ROUND_DOWN, localcontext

import pytest
from money_demo.amount import format_money


def test_half_up_carry():
    assert format_money("9.995") == "10.00"
    assert format_money("-9.995") == "-10.00"


def test_fixed_trailing_zeroes():
    assert format_money("12.50000") == "12.50"
    assert format_money("-4") == "-4.00"


def test_signed_zero():
    assert format_money("-0.004") == "-0.00"
    assert format_money("0.005") == "0.01"


def test_large_exact_decimal():
    assert format_money("123456789012345678901234567890.125") == (
        "123456789012345678901234567890.13"
    )


def test_rounding_is_independent_of_callers_context():
    with localcontext() as context:
        context.rounding = ROUND_DOWN
        context.prec = 3
        assert format_money("15.255") == "15.26"


def test_invalid_and_nonfinite_values():
    for value in ("not-money", "", "NaN", "Infinity", "-Infinity"):
        with pytest.raises(ValueError):
            format_money(value)

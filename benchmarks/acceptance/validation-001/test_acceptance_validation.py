"""维护者验收用例，仅复制到公开快照之外的临时目录运行。"""

import pytest
from validation_demo.quantity import validate_quantity


class Quantity(int):
    """用于确认合法整数子类仍被接受。"""


def test_accepts_integer_subclass_without_conversion() -> None:
    value = Quantity(3)
    assert validate_quantity(value) is value


@pytest.mark.parametrize(
    ("value", "minimum", "maximum"),
    [(0, 0, 0), (100, 100, 100), (-3, -3, -1), (10**30, 0, 10**30)],
)
def test_preserves_valid_closed_range(value: int, minimum: int, maximum: int) -> None:
    assert validate_quantity(value, minimum=minimum, maximum=maximum) == value


@pytest.mark.parametrize(("value", "minimum", "maximum"), [(True, 0, 2), (False, -1, 0)])
def test_rejects_booleans_inside_custom_ranges(value: bool, minimum: int, maximum: int) -> None:
    with pytest.raises(ValueError):
        validate_quantity(value, minimum=minimum, maximum=maximum)


@pytest.mark.parametrize("value", [-4, 1])
def test_rejects_values_outside_custom_range(value: int) -> None:
    with pytest.raises(ValueError):
        validate_quantity(value, minimum=-3, maximum=0)


@pytest.mark.parametrize("value", [b"1", 1j, object()])
def test_rejects_other_types(value: object) -> None:
    with pytest.raises(ValueError):
        validate_quantity(value)

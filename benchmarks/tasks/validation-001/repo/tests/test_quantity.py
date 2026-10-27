import pytest
from validation_demo.quantity import validate_quantity


@pytest.mark.parametrize("value", [0, 1, 50, 100])
def test_accepts_default_integer_range(value: int) -> None:
    assert validate_quantity(value) == value


@pytest.mark.parametrize("value", [-1, 101, 1.0, "1", None, [], {}])
def test_rejects_invalid_quantities(value: object) -> None:
    with pytest.raises(ValueError):
        validate_quantity(value)


def test_custom_range() -> None:
    assert validate_quantity(-2, minimum=-5, maximum=-1) == -2


def test_rejects_reversed_range() -> None:
    with pytest.raises(ValueError):
        validate_quantity(2, minimum=3, maximum=1)

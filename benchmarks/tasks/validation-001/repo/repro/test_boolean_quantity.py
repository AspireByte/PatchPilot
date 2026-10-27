import pytest
from validation_demo.quantity import validate_quantity


@pytest.mark.parametrize("value", [False, True])
def test_repro_boolean_is_not_a_quantity(value: bool) -> None:
    rejected = False
    try:
        validate_quantity(value)
    except ValueError:
        rejected = True
    assert rejected, "布尔值必须被作为非法数量拒绝"

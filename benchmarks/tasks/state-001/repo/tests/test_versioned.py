import pytest
from state_demo.versioned import VersionedValue


def test_default_initial_state() -> None:
    item = VersionedValue("初始")
    assert (item.value, item.version) == ("初始", 0)


@pytest.mark.parametrize("version", [1, 7, 100])
def test_explicit_initial_version(version: int) -> None:
    item = VersionedValue("初始", version=version)
    assert (item.value, item.version) == ("初始", version)


def test_matching_update_writes_value() -> None:
    item = VersionedValue("旧值", version=3)
    assert item.update("新值", expected_version=3) is True
    assert item.value == "新值"


@pytest.mark.parametrize("expected_version", [-1, 1, 8])
def test_mismatched_update_keeps_state(expected_version: int) -> None:
    item = VersionedValue("初始")
    assert item.update("不应写入", expected_version=expected_version) is False
    assert (item.value, item.version) == ("初始", 0)


def test_matching_update_accepts_same_value() -> None:
    item = VersionedValue("")
    assert item.update("", expected_version=0) is True
    assert item.value == ""

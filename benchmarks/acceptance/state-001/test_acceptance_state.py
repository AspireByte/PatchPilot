"""维护者验收用例，仅复制到公开快照之外的临时目录运行。"""

import pytest
from state_demo.versioned import VersionedValue


@pytest.mark.parametrize("version", [0, 1, 11])
def test_success_increments_exactly_once(version: int) -> None:
    item = VersionedValue("旧值", version=version)
    assert item.update("新值", expected_version=version) is True
    assert (item.value, item.version) == ("新值", version + 1)


@pytest.mark.parametrize("expected_version", [0, 1, 8])
def test_stale_or_future_update_never_changes_state(expected_version: int) -> None:
    item = VersionedValue("保留", version=4)
    assert item.update("拒绝", expected_version=expected_version) is False
    assert (item.value, item.version) == ("保留", 4)


def test_sequence_of_successful_and_stale_updates() -> None:
    item = VersionedValue("起点", version=5)
    for version in range(5, 9):
        assert item.update(str(version), expected_version=version) is True
        assert item.update("陈旧", expected_version=version) is False
        assert (item.value, item.version) == (str(version), version + 1)


def test_instances_are_independent() -> None:
    first = VersionedValue("甲")
    second = VersionedValue("乙")
    assert first.update("丙", expected_version=0) is True
    assert (first.value, first.version) == ("丙", 1)
    assert (second.value, second.version) == ("乙", 0)
    assert second.update("丁", expected_version=0) is True
    assert (second.value, second.version) == ("丁", 1)


def test_same_value_update_still_advances_version() -> None:
    item = VersionedValue("", version=2)
    assert item.update("", expected_version=2) is True
    assert (item.value, item.version) == ("", 3)
    assert item.update("不应写入", expected_version=2) is False
    assert (item.value, item.version) == ("", 3)

from state_demo.versioned import VersionedValue


def test_repro_success_advances_version_and_rejects_stale_update() -> None:
    item = VersionedValue("初始")
    assert item.update("第一次", expected_version=0) is True
    assert (item.value, item.version) == ("第一次", 1)
    assert item.update("陈旧写入", expected_version=0) is False
    assert (item.value, item.version) == ("第一次", 1)

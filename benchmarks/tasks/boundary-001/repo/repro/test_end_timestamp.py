from boundary_demo.window import select_window


def test_repro_excludes_end_timestamp() -> None:
    assert select_window([9, 10, 19, 20, 21], start=10, end=20) == [10, 19]

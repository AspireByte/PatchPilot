from null_demo.labels import resolve_label


def test_repro_empty_string_is_a_valid_label() -> None:
    assert resolve_label("", fallback="备用") == ""

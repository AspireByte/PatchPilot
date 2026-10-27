"""URL 路径构造的正常用例。"""

from url_path_demo.paths import build_path


def test_simple_segments() -> None:
    assert build_path(["api", "users", "42"]) == "/api/users/42"


def test_space_is_encoded() -> None:
    assert build_path(["two words"]) == "/two%20words"


def test_empty_segments_are_preserved() -> None:
    assert build_path([]) == "/"
    assert build_path(["", "x", ""]) == "//x/"


def test_reserved_characters_and_input_are_preserved() -> None:
    segments = ["a?b", "100%"]
    assert build_path(segments) == "/a%3Fb/100%25"
    assert segments == ["a?b", "100%"]

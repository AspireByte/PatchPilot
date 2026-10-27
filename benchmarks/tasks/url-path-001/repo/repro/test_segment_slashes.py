"""单段内部斜杠改变路径结构的断言失败复现。"""

from url_path_demo.paths import build_path


def test_repro_embedded_slash() -> None:
    assert build_path(["projects", "a/b"]) == "/projects/a%2Fb"


def test_repro_slashes_at_segment_edges() -> None:
    assert build_path(["/left/", "right"]) == "/%2Fleft%2F/right"

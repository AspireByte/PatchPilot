"""URL 逐段编码任务的独立维护者验收。"""

import pytest
from url_path_demo.paths import build_path


@pytest.mark.parametrize(
    ("segments", "expected"),
    [
        (["目录/文件 名?"], "/%E7%9B%AE%E5%BD%95%2F%E6%96%87%E4%BB%B6%20%E5%90%8D%3F"),
        (["a?b#c%"], "/a%3Fb%23c%25"),
        (["", "/", ""], "//%2F/"),
        (["._-~", ".."], "/._-~/.."),
        (["%2F", "x/y"], "/%252F/x%2Fy"),
    ],
)
def test_segments_are_independently_encoded(segments: list[str], expected: str) -> None:
    before = segments.copy()
    assert build_path(segments) == expected
    assert segments == before

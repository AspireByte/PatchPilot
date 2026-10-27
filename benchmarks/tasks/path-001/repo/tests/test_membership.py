"""路径归属的正常场景。"""

import pytest

from path_demo.membership import relative_member


@pytest.mark.parametrize(
    ("root", "candidate", "expected"),
    [
        ("/docs", "/docs", ""),
        ("/docs", "/docs/guides/start", "guides/start"),
        ("/docs", "/images/logo", None),
        ("/docs/", "/docs//guides/./start/", "guides/start"),
    ],
)
def test_membership(root, candidate, expected):
    assert relative_member(root, candidate) == expected

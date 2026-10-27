"""名称前缀相似的兄弟路径不属于目录。"""

import pytest

from path_demo.membership import relative_member


@pytest.mark.parametrize("candidate", ["/root-other/item", "/rooted"])
def test_repro_sibling_prefix(candidate):
    assert relative_member("/root", candidate) is None

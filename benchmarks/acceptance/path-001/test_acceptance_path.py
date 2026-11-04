"""维护者纯路径段验收。"""

import pytest
from path_demo.membership import relative_member


def test_root_directory():
    assert relative_member("/", "/a/b") == "a/b"
    assert relative_member("/", "/") == ""


def test_segment_not_character_prefix():
    assert relative_member("/team/config", "/team/config-backup/file") is None
    assert relative_member("/team/config", "/team/configuration") is None


def test_candidate_is_parent():
    assert relative_member("/team/config", "/team") is None


def test_redundant_separators_and_dots():
    assert relative_member("/team//./config/", "/team/config//a/./b/") == "a/b"


def test_case_sensitive_unicode_segments():
    assert relative_member("/项目", "/项目/数据") == "数据"
    assert relative_member("/Root", "/root/a") is None


def test_reject_relative_parent_and_double_root():
    for root, candidate in (
        ("root", "/root/a"),
        ("/root", "root/a"),
        ("/root/..", "/root/a"),
        ("/root", "/root/../a"),
        ("//root", "/root/a"),
        ("/root", "//root/a"),
    ):
        with pytest.raises(ValueError):
            relative_member(root, candidate)

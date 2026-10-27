"""合并结果取值与调用时不修改输入。"""

from copy import deepcopy

from merge_demo.config import merge_config


def test_empty_configuration():
    assert merge_config({}, {}) == {}


def test_nested_dictionary_merge():
    assert merge_config({"db": {"host": "local", "port": 1}}, {"db": {"port": 2}}) == {
        "db": {"host": "local", "port": 2}
    }


def test_list_replaces_without_changing_inputs():
    base = {"items": [1, 2]}
    overrides = {"items": [3]}
    before = deepcopy((base, overrides))
    assert merge_config(base, overrides) == {"items": [3]}
    assert (base, overrides) == before


def test_dictionary_can_replace_scalar():
    assert merge_config({"feature": False}, {"feature": {"enabled": True}}) == {
        "feature": {"enabled": True}
    }

"""维护者递归合并及可变结构隔离验收。"""

from copy import deepcopy

from merge_demo.config import merge_config


def test_deep_base_branch_isolated():
    base = {"tree": [{"leaves": [1]}]}
    result = merge_config(base, {"extra": True})
    result["tree"][0]["leaves"].append(2)
    assert base == {"tree": [{"leaves": [1]}]}


def test_new_override_dictionary_isolated():
    overrides = {"new": {"children": [{"name": "first"}]}}
    result = merge_config({}, overrides)
    result["new"]["children"][0]["name"] = "changed"
    assert overrides["new"]["children"][0]["name"] == "first"


def test_input_mutations_do_not_change_output():
    base = {"left": [1]}
    overrides = {"right": [2]}
    result = merge_config(base, overrides)
    base["left"].append(3)
    overrides["right"].append(4)
    assert result == {"left": [1], "right": [2]}


def test_type_conflicts_replace_whole_value():
    assert merge_config({"a": {"x": 1}, "b": [1]}, {"a": None, "b": {"x": 2}}) == {
        "a": None,
        "b": {"x": 2},
    }


def test_recursive_inputs_remain_unchanged():
    base = {"a": {"b": {"keep": [1], "replace": 0}}}
    overrides = {"a": {"b": {"replace": [2]}}}
    before = deepcopy((base, overrides))
    result = merge_config(base, overrides)
    assert result == {"a": {"b": {"keep": [1], "replace": [2]}}}
    assert (base, overrides) == before


def test_independent_results():
    base = {"a": [1]}
    first = merge_config(base, {})
    second = merge_config(base, {})
    first["a"].append(2)
    assert second == {"a": [1]}
    assert first is not base and second is not base

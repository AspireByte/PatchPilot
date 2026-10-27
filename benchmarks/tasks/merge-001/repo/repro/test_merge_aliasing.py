"""修改合并结果不能影响两个输入。"""

from merge_demo.config import merge_config


def test_repro_base_list_shared():
    base = {"service": {"labels": ["base"]}}
    merged = merge_config(base, {})
    merged["service"]["labels"].append("result")
    assert base == {"service": {"labels": ["base"]}}


def test_repro_override_list_shared():
    overrides = {"service": {"labels": ["override"]}}
    merged = merge_config({"service": {"port": 8}}, overrides)
    merged["service"]["labels"].append("result")
    assert overrides == {"service": {"labels": ["override"]}}

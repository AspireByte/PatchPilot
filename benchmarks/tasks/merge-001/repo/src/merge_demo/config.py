"""递归合并自建配置字典。"""


def merge_config(base: dict, overrides: dict) -> dict:
    """仅当两边同为字典时递归，其他值由覆盖配置替换。"""
    merged = dict(base)
    for key, value in overrides.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = merge_config(merged[key], value)
        else:
            merged[key] = value
    return merged

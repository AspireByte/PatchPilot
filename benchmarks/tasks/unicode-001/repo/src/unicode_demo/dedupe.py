"""按 Unicode 规范化后的身份保留首次出现的原始字符串。"""


def unique_strings(values: list[str]) -> list[str]:
    """去重但不修改原始字符串及其出现顺序。"""
    seen = set()
    result = []
    for value in values:
        key = value.casefold()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result

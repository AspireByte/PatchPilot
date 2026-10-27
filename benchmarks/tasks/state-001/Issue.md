# 成功更新后无法按新版本继续更新

## 公共行为约定

- 公共 API 为 `state_demo.versioned.VersionedValue(value: str, version: int = 0)`，公开读取 `value` 和 `version` 字段。
- 初始版本由调用者提供，为非负整数；默认是 `0`。调用者不直接修改字段。
- `update(new_value: str, *, expected_version: int) -> bool` 使用整数期望版本进行匹配。
- 期望版本等于当前版本时，保存新字符串，当前版本递增 `1`，返回 `True`。
- 即使新旧字符串相同，匹配成功也属于一次更新，版本仍递增。
- 期望版本不匹配时返回 `False`，值和版本都保持原样；陈旧版本不能覆盖已成功的更新。
- 各实例互不影响；只要求单线程、顺序调用，不涉及持久化或网络。

## 复现

创建 `VersionedValue("初始")` 后，调用 `update("第一次", expected_version=0)` 返回 `True`。

实际状态为 `("第一次", 0)`，预期状态为 `("第一次", 1)`；再次使用期望版本 `0` 应返回 `False` 并保持状态。

可见复现用例位于 `repro/test_version_progress.py`；请保持原有测试和配置不变。

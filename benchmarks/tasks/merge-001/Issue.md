# 合并后的配置变动影响了原配置

## 公共行为约定

- API：`merge_demo.config.merge_config(base: dict, overrides: dict) -> dict`。
- 输入为无环的、字符串键配置字典；值限字典、列表及不可变标量（字符串、数字、布尔值、`None`）。
- 同一个键两边都是字典时递归合并；其他冲突由 `overrides` 的整个值替换，列表不拼接。保留没有被覆盖的键。
- 返回新字典，调用本身不修改任何输入；返回结果的所有可变后代均不得与任一输入共享。之后修改任一输入也不得影响已返回结果。不同调用的结果相互独立。

## 用户复现

`base = {"service": {"labels": ["base"]}}`，调用 `merged = merge_config(base, {})` 后执行 `merged["service"]["labels"].append("result")`，原 `base` 的列表实际也多出 `"result"`，预期保持 `["base"]`。覆盖配置中的列表也出现相同行为。

复现见 `repro/test_merge_aliasing.py`。请保持测试及配置不变。

本样例为自建场景，无外部 Issue 来源；基准资料采用根目录 MIT 许可证。

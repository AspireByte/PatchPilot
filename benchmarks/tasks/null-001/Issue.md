# 显式空标签未被保留

## 公共行为约定

- 公共 API 为 `null_demo.labels.resolve_label(value, *, fallback="未命名") -> str`。
- `value` 只接受字符串或 `None`；`fallback` 为字符串。
- `None` 表示缺失，返回 `fallback`；未提供备用值时返回 `"未命名"`。
- 任何字符串都是合法标签，包括空字符串、纯空白和中文；应原样返回，不裁剪、不替换。
- 备用值也可以是空字符串。调用之间互不影响。

## 复现

调用 `resolve_label("", fallback="备用")`，实际返回 `"备用"`，预期返回 `""`。

可见复现用例位于 `repro/test_empty_label.py`；请保持原有测试和配置不变。

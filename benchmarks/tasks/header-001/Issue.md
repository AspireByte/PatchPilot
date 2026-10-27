# 同一个 HTTP 头使用不同大小写时查不到值

## 公共行为约定

- API：`header_demo.headers.get_header(headers, name, default=None) -> str | None`。
- `headers` 是保持迭代顺序的字符串映射，键和查询名称均为非空 ASCII HTTP 头名称，值为任意字符串；默认值为字符串或 `None`。
- 名称按 ASCII 不区分大小写匹配；如多个键仅大小写不同，返回映射迭代顺序中的第一个匹配值。
- 未匹配时原样返回默认值；空值字符串是有效结果，不应使用默认值替换。
- 不修改映射、键或值，不裁剪或规范化返回值。

## 用户复现

`get_header({"Content-Type": "application/json"}, "CONTENT-TYPE")` 返回 `None`，预期为 `"application/json"`。

运行 `repro/test_header_case.py` 可复现。请保持测试与配置不变。本任务为自建合成场景，无外部 Issue 来源。

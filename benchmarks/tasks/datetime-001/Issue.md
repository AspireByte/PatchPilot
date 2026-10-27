# 带偏移量的时间转 UTC 后瞬间改变

## 公共行为约定

- API：`datetime_demo.timestamps.to_utc(value: str) -> datetime`。
- 接受 Python `datetime.fromisoformat` 支持的 ISO 8601 字符串，包括末尾大写 `Z` 和显式时区偏移量。
- 返回 `tzinfo is datetime.timezone.utc` 的时间，必须与输入表示同一瞬间，正确处理跨日、跨年与非整小时时区，并保留微秒。
- 缺少时区的字符串和无法解析的字符串均抛出 `ValueError`；不依赖运行机器的本地时区。

## 用户复现

`to_utc("2025-01-01T01:02:03.123456+08:00").isoformat()` 返回 `"2025-01-01T01:02:03.123456+00:00"`，预期为 `"2024-12-31T17:02:03.123456+00:00"`。

运行 `repro/test_offset_conversion.py` 可复现。请保持测试与配置不变。本任务为自建合成场景，无外部 Issue 来源。

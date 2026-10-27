# 规范等价的 Unicode 字符串仍被保留为两项

## 公共行为约定

- API：`unicode_demo.dedupe.unique_strings(values: list[str]) -> list[str]`。
- 去重身份严格定义为 `unicodedata.normalize("NFC", value).casefold()`；两项身份相同即视为重复，不使用兼容规范化 NFKC，不裁剪空白。
- 按输入顺序返回每个身份首次出现的原始字符串，不能用规范化结果替换输出。
- 输入列表保持不变；空列表返回新空列表，调用之间不共享去重状态。

## 用户复现

`unique_strings(["Café", "CAFÉ"])` 返回两项，预期只返回 `["Café"]`。

运行 `repro/test_canonical_duplicates.py` 可复现。请保持测试与配置不变。本任务为自建合成场景，无外部 Issue 来源。

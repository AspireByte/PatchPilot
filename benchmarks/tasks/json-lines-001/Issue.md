# JSONL 错误位置与输入文本行号不一致

## 公共行为约定

- API：`json_lines_demo.parser.parse_json_lines(text: str) -> dict`，仅处理内存字符串，不访问文件。
- 使用 `str.splitlines()` 分行，原始行号从 1 开始；`line.strip()` 为空的行被跳过，但仍占物理行号。
- 每个非空行按 `json.loads` 的语义解析，允许对象、数组与标量，成功记录按输入顺序保留。
- 全部成功返回 `{"records": [...], "error": None}`。
- 首个无效 JSON 行出现时立即停止，返回此前的成功记录，以及 `{"line": 原始物理行号, "message": "无效 JSON"}` 形式的 `error`。不向调用者抛出 JSON 解析异常，不继续处理后续行。

## 用户复现

输入 `"\n{\"x\":1}\n坏行"`，结果中的错误行号为 2；预期 `records` 为 `[{"x": 1}]`，`error` 为 `{"line": 3, "message": "无效 JSON"}`。

运行 `repro/test_physical_lines.py` 可复现。请保持测试与配置不变。本任务为自建合成场景，无外部 Issue 来源。

# 路径段内的斜杠被误当成段分隔符

## 公共行为约定

- API：`url_path_demo.paths.build_path(segments: list[str]) -> str`，仅构造字符串，不联网。
- 每个输入字符串视为不可分割的一段，编码结果严格等同于对该段调用 `urllib.parse.quote(segment, safe="")`，使用默认 UTF-8 编码。
- 编码后以 `/` 连接，再添加一个开头的 `/`；段内的斜杠、空格、问号、百分号、Unicode 等需要百分号编码，标准未保留字符保持原样。
- 空列表返回 `/`；空段仍保留其位置，不裁剪、不解析已有百分号编码，不合并或规范化路径段。
- 不修改输入列表。

## 用户复现

`build_path(["projects", "a/b"])` 实际返回 `"/projects/a/b"`，预期返回 `"/projects/a%2Fb"`，因为 `"a/b"` 是一个完整段。

运行 `repro/test_segment_slashes.py` 可复现。请保持测试与配置不变。本任务为自建合成场景，无外部 Issue 来源。

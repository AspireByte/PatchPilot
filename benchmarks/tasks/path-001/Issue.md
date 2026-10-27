# 相似名称的目录被判定为子路径

## 公共行为约定

- API：`path_demo.membership.relative_member(root: str, candidate: str) -> str | None`。
- 这是区分大小写的纯 POSIX 路径计算，不读取、解析或写入真实文件系统，也不处理符号链接。
- 两个输入必须为单根绝对路径；相对路径、双斜杠根及任何 `..` 路径段抛出 `ValueError`。重复分隔符、`.` 和尾部斜杠按路径段规范化。
- 只有完整路径段相同才构成归属。目录本身返回 `""`，严格子路径返回无前导或尾部斜杠的相对路径，其余返回 `None`。根目录 `/` 包含所有合法输入。

## 用户复现

`relative_member("/root", "/root-other/item")` 实际返回 `"-other/item"`；`relative_member("/root", "/rooted")` 实际返回 `"ed"`。两者预期均为 `None`。

复现见 `repro/test_path_prefix.py`。请保持测试及配置不变。

本样例为自建场景，无外部 Issue 来源；基准资料采用根目录 MIT 许可证。

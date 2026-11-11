# 内部模块边界

当前产品只围绕聊天和通用 Harness 组织。仓库中仍有几组较早实现，它们可以被测试和兼容层使用，但不应被当成新的用户入口。

## 固定任务模块（legacy）

| 模块 | 仍然承担的职责 | 当前边界 |
| --- | --- | --- |
| `legacy/benchmark/task.py` | 解析固定任务、构造公开任务输入 | 不参与普通聊天仓库识别 |
| `legacy/benchmark/snapshot.py` / `workspace.py` | 固定任务快照和工作区 | 聊天使用自己的仓库与 Harness 快照 |
| `legacy/benchmark/agent.py` | 固定任务 Agent 循环 | 不是聊天 Agent 的主实现 |
| `legacy/benchmark/tools.py` / `patching.py` | 固定任务工具和补丁边界 | 不向 `chat` 直接暴露 |
| `legacy/benchmark/verification.py` | `verify_patch` 适配器 | 通过固定任务适配器执行，不进入聊天 |
| `legacy/benchmark/sandbox.py` | 固定任务 Docker/JUnit 后端 | 保留 acceptance 和测试分类语义 |

## 服务与评测模块

`service/` 仍包含 FastAPI、PostgreSQL、Worker、租约、事件和产物模型；`evaluation/` 仍包含任务目录、baseline、批量记录和指标。它们服务于历史固定任务的内部回归和研究，不是聊天启动所需依赖，也不代表当前产品提供公网任务平台。

旧模块被保留的原因是减少一次性删除和迁移风险，并让历史验证仍可复现。它们的存在不意味着旧 `agent-run`、服务或评测命令仍是产品文档推荐路径。

## 未来扩展的接入点

- 上下文压缩通过 `core.compression.HistoryCompactor` 接入 `ContextBuilder`；会话保存完整原文及可选压缩状态，兼容读取旧 version 1。参见 [上下文压缩](context-compression.md)。
- 长期记忆通过 `core.memory.MemoryBackend` 和只读 `ContextSource` 快照接入；会话层协调提取与工具，独立 SQLite 保存版本和处理水位。参见 [原生长期记忆](memory.md)。
- 外部客户端协议（例如 MCP）应作为 `VerificationRunner` 或工具层的薄适配器，不把执行策略复制到协议层。
- 多 Agent 和后台任务尚未实现，不能从现有服务代码推断聊天已经具备这些能力。

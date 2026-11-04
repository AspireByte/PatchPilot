# PatchPilot 快速学习指南

这份文档帮助你在较短时间内理解 PatchPilot 当前的产品边界、调用链和源码组织。
内容以当前代码为准；固定任务、服务和评测属于内部回归链路，不是聊天产品的主入口。

## 1. 项目是什么

PatchPilot 是一个面向本地代码仓库的持续对话 Agent：

- 模型负责理解用户目标、决定下一步并提出工具调用；
- Runtime 负责预算、取消、协议检查、权限和工具结果配对；
- 工具运行时负责读取和修改文件、执行授权命令、运行 Harness；
- Session 负责多轮历史、Goal、恢复和持久化；
- Harness 在临时副本中执行预先登记的检查并生成结构化证据。

它当前不是公网多租户平台，也没有自动安装依赖、长期 Memory、MCP、多 Agent 调度或自动上下文压缩。

## 2. 先运行起来

安装依赖：

```powershell
uv sync --locked
```

查看公开命令：

```powershell
uv run --locked patchpilot --help
```

启动聊天：

```powershell
uv run --locked patchpilot chat `
  --repo D:\code\My-agent `
  --allow-api
```

权限开关是独立的：

- `--allow-api`：允许请求模型服务；
- `--allow-commands`：向模型开放宿主命令工具；
- `--allow-harness`：向模型开放已登记的 Harness 检查；
- `--resume <id>`：恢复已有会话，但权限需要本次启动重新提供；
- `--sessions-dir <path>`：指定会话目录。

只读了解项目时可以先不开放命令和 Harness。公开入口只有 `patchpilot chat`；服务和评测仍可通过以下模块启动：

```powershell
python -m patchpilot.service.cli --help
python -m patchpilot.evaluation.cli --help
```

## 3. 一次请求的主链路

```text
patchpilot chat
  -> interfaces.cli.main.main
  -> interfaces.cli.chat.run_chat
  -> session_runtime.session.AgentSession.submit
  -> core.loop.AgentLoop
  -> model.protocol.ModelClient
  -> model.providers.anthropic.AnthropicModel
  -> tools_runtime.executor.ToolExecutor
  -> tools_runtime.builtin.LocalTools
  -> 文件 / 宿主进程 / infrastructure.verification
```

当模型返回工具调用时，`AgentLoop` 会按顺序执行工具，把每个结果用原来的
`tool_use_id` 写回消息，再继续请求模型。模型没有直接读写宿主环境的能力；它只能请求当前授权范围内的工具。

普通输入完成后，Session 返回回答并等待下一次输入。只有用户输入 `/goal <完成条件>`，才会启动无工具的评估调用，并根据结果决定继续、暂停或达成。

## 4. 源码目录怎么分

### `src/patchpilot/interfaces`

CLI 和交互层。

- `cli/main.py`：解析顶层命令；
- `cli/chat.py`：解析聊天参数、创建模型和 `AgentSession`、处理终端输入输出。

这一层可以依赖 Session 和基础设施，但不应把 CLI 逻辑塞进 Core。

### `src/patchpilot/core`

与具体 Provider、数据库和 Web 框架无关的 Runtime 内核。

- `loop.py`：模型请求、工具调用、预算、协议错误、取消和生命周期事件；
- `agent.py`：Agent 生命周期、订阅和中止；
- `types.py`：`ModelRequest`、`ModelResponse`、`ToolCall`、用量等内部类型；
- `budget.py`：模型、工具、token、上下文和时间预算；
- `context.py`：完整历史构建和容量检查，未来压缩从这里接入；
- `events.py`：`RuntimeEvent` 及事件接收器；
- `control.py`：取消和执行停止；
- `ports.py`：模型、工具和事件的最小协议。

Core 可以注入 Fake Model 和 Fake Tool，因此不需要 Anthropic、Docker、PostgreSQL 或真实仓库也能测试。

### `src/patchpilot/model`

模型端口和 Provider 适配器。

- `protocol.py`：`ModelClient`、`ModelFailure` 和旧关键字模型到内部协议的适配器；
- `providers/anthropic.py`：Anthropic SDK、流式响应收集、工具 JSON 校验、超时和错误分类。

Core 只接触 `ModelRequest` / `ModelResponse`，不接触 Anthropic SDK 类型。未来增加其他 Provider 时，新增适配器即可。

### `src/patchpilot/tools_runtime`

工具的注册、校验、授权和执行边界。

- `registry.py`：工具名称、说明、schema 和当前授权列表；
- `executor.py`：参数检查、取消检查、异常转换和结果规范化；
- `protocol.py`：`ToolContext` 和工具 Provider 协议；
- `builtin/filesystem.py`：文件参数模型、摘要和公开路径规则；
- `builtin/process.py`：宿主命令参数；
- `builtin/harness.py`：Harness 参数；
- `builtin/local.py`：聊天内置工具集合的组合实现；
- `builtin/factory.py`：创建内置工具集合的入口。

主要工具：

```text
list_files          列出公开文件
read_file           读取 UTF-8 文件并返回 sha256
search_text         搜索文本
write_file          新建或替换文件
edit_file           唯一文本匹配替换
get_diff            查看本次文件工具修改
run_command         执行授权的宿主命令
run_harness_check   选择已登记的 Harness profile
```

文件修改采用乐观并发保护：先读取文件得到摘要，修改时必须携带最近摘要；文件被外部程序改变后，运行时拒绝覆盖。

### `src/patchpilot/session_runtime`

持续对话和恢复边界。

- `session.py`：`AgentSession`，负责用户消息、斜杠命令、Goal、上下文、事件和保存；
- `goal.py`：目标状态和严格 JSON 评估结果解析；
- `state.py`：version 1 状态编码和未来迁移入口；
- `prompts.py`：通用 Agent 提示词、聊天策略；
- `api.py`：Session 对外协议；
- `manager.py`：创建和恢复的轻量工厂边界。

Session 不负责实现 Provider，也不把 Goal 评估塞进通用 `AgentLoop`。普通对话和显式目标循环保持分离。

### `src/patchpilot/infrastructure`

可替换的宿主设施。

- `persistence/session_store.py`：JSON 状态、`events.jsonl`、锁和原子替换；
- `process/runner.py`、`job.py`：进程启动、超时、输出上限、取消和进程树回收；
- `verification/config.py`：Harness TOML 和 profile 校验；
- `verification/runner.py`：快照、宿主/Docker 执行和证据产物；
- `verification/service.py`：Harness 服务门面；
- `verification/port.py`：Session 和工具依赖的验证协议。

Harness 配置在会话启动时读取并冻结。模型只能选择 profile 名称，不能临时拼接任意命令。

### `src/patchpilot/legacy/benchmark`

固定任务和基准链路的隔离区：任务解析、旧 Agent、固定任务工具、补丁、工作区、快照、沙箱和独立验证。

这部分保留是为了评测、服务 Worker 和历史回归，不应被 Core Runtime 反向导入。

### `src/patchpilot/evaluation` 与 `src/patchpilot/service`

分别承载批量评测和 FastAPI/PostgreSQL/Worker 服务。它们继续保留公开模块命令，但不属于聊天链路的必需依赖。

## 5. 会话和恢复

默认会话目录为 `.patchpilot-sessions/<session_id>/`，主要文件包括：

- `state.json`：恢复依据，继续使用 version 1 格式；
- `events.jsonl`：执行轨迹；
- 修改基线和 Harness 产物：用于 diff、证据和恢复。

恢复时会：

1. 校验仓库路径；
2. 恢复完整消息和目标状态；
3. 暂停未完成目标，等待显式 `/goal resume`；
4. 清空旧的文件读取授权，修改前必须重新读取；
5. 补齐未完成工具调用的中断结果；
6. 不自动重放命令或 Harness；
7. 要求重新提供命令和 Harness 权限。

状态文件用于恢复，事件日志用于追踪，两者职责不同。

## 6. Goal Loop

```text
/goal <条件>
  -> 工作模型使用文件/命令/Harness 工具
  -> 工作轮结束
  -> 无工具评估模型检查消息和证据
  -> achieved：结束
  -> 未完成：追加反馈并继续
  -> impossible / 中断 / 预算耗尽：paused
```

评估器只判断现有证据，不执行工具。`run_harness_check` 返回 `ok=true` 只代表工具调用完成，仍要检查嵌套结果的状态、退出码、快照摘要和隔离级别。

## 7. 建议阅读顺序

如果要快速理解项目，按下面顺序阅读：

1. [README.md](README.md)：产品边界和启动方式；
2. [interfaces/cli/chat.py](src/patchpilot/interfaces/cli/chat.py)：聊天入口；
3. [session_runtime/session.py](src/patchpilot/session_runtime/session.py)：Session 如何组织一轮提交；
4. [core/loop.py](src/patchpilot/core/loop.py)：模型—工具循环；
5. [core/types.py](src/patchpilot/core/types.py)：内部消息和响应格式；
6. [model/providers/anthropic.py](src/patchpilot/model/providers/anthropic.py)：Provider 适配；
7. [tools_runtime/builtin/local.py](src/patchpilot/tools_runtime/builtin/local.py)：聊天工具行为和文件保护；
8. [infrastructure/persistence/session_store.py](src/patchpilot/infrastructure/persistence/session_store.py)：恢复和持久化；
9. [infrastructure/verification/runner.py](src/patchpilot/infrastructure/verification/runner.py)：Harness 证据链；
10. [legacy/benchmark/agent.py](src/patchpilot/legacy/benchmark/agent.py)：最后再看固定任务旧链路。

阅读每一层时，始终问三个问题：

- 谁决定下一步：模型、Runtime、工具、Harness 还是用户？
- 哪个边界检查让动作真正生效：权限、schema、路径、摘要、预算还是 profile？
- 状态保存在哪里：消息、事件、文件基线、快照 manifest 还是 result？

## 8. 如何验证

不依赖真实模型、Docker 或 PostgreSQL 的本地回归：

```powershell
uv run --locked pytest -q
uv run --locked ruff check src tests
uv run --locked ruff format --check src tests
```

架构相关测试集中在 `tests/test_runtime_core.py`，覆盖：

- 多轮工具调用和消息配对；
- 重复工具 ID、未知工具和协议错误；
- 模型、工具、上下文和取消边界；
- Tool Registry 和 schema 校验；
- version 1 状态编解码；
- Core 不导入 Provider、Web、数据库、Service、Evaluation 或 Legacy。

Docker、PostgreSQL 和真实 Anthropic 测试需要单独的环境和权限，未运行时不能把本地测试通过描述成真实模型或隔离验收通过。

## 9. 面试式总结

> PatchPilot 是一个面向本地仓库的持续对话 Agent。模型通过 Provider 无关的 Runtime 请求工具，工具运行时负责权限、schema、路径和文件摘要保护；Session 保存完整消息、目标和恢复状态；Harness 在临时副本中执行冻结 profile，并返回带快照摘要、退出码和运行时信息的结构化证据。固定任务和服务代码被隔离在 legacy 与独立基础设施边界中，避免污染通用 Agent Runtime。

回答实现细节时，优先说明：

1. 模型只提出动作，Runtime 决定是否执行；
2. 工具结果按调用 ID 配对，异常和中断不会伪装成成功；
3. 状态文件用于恢复，事件日志用于追踪；
4. 宿主检查不是安全沙箱，`isolated=false` 必须如实保留；
5. 新 Provider、Memory、Compactor、RunStore 和 Evaluation 应通过现有端口接入，而不是重新复制一套模型循环。

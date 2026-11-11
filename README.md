# myagent

面向本地代码仓库的持续对话 Agent：理解代码、修改文件、运行检查，并在后续对话中保留上下文。公开入口为 `myagent chat`，日常聊天无需启动 Docker 或 PostgreSQL。

## 功能概览

| 能力 | 使用方式 |
| --- | --- |
| 多轮代码协作 | 连续提问、阅读与搜索代码、创建或精确修改文件 |
| 项目检查 | 授权后按需调用 Harness，在临时仓库副本中运行已登记的检查 |
| 目标续跑 | 用 `/goal <完成条件>` 设置目标，由工作模型执行、独立评估调用判断进展 |
| 上下文压缩 | 自动或手动压缩旧历史，保留原始消息 |
| 长期记忆 | 显式开启后，跨会话保存偏好、项目事实、决策和经验 |
| 保存与恢复 | 持久化消息、事件、用量和目标状态，支持按会话编号恢复 |

## 快速开始

### 1. 安装

需要 Python 3.12+ 和 [uv](https://docs.astral.sh/uv/)。首次获取项目并安装锁定依赖：

```powershell
git clone https://github.com/AspireByte/PatchPilot.git
cd PatchPilot
uv sync --locked
```

已有源码时，在项目根目录执行 `uv sync --locked` 即可。仓库名称为 PatchPilot，发行包和主命令为 `myagent`；旧命令 `patchpilot` 继续兼容。

### 2. 配置模型并启动

以下示例使用 PowerShell，在项目根目录启动常驻交互终端：

```powershell
$env:ANTHROPIC_API_KEY = "你的 API 密钥"
# 使用兼容服务时设置；官方服务可省略。
# $env:ANTHROPIC_BASE_URL = "https://你的服务地址"
uv run --locked myagent chat --repo . --allow-api
```

`--repo` 指向要操作的仓库，可以是本项目或其他本地目录。默认可以对话和使用文件工具；执行宿主命令、运行 Harness、启用长期记忆需要分别添加参数。

常用的开发配置：

```powershell
uv run --locked myagent chat --repo . --allow-api --allow-commands --allow-harness --memory
```

也可参考 [.env.example](.env.example) 创建自己的 `.env`，填写后添加 `--env-file .env`。只加载显式指定的环境文件，已有环境变量优先；不自动读取目标仓库的 `.env`，不执行脚本或展开变量。

模型服务需支持流式 Anthropic Messages API、工具调用，以及工作模型使用的 adaptive thinking。用 `--model <模型名>` 指定服务支持的工作模型；`--evaluator-model <模型名>` 指定目标评估模型，默认与工作模型相同。

### 3. 从其他目录启动

`--project` 用于定位本程序，`--repo` 用于选择操作对象。下面的两个路径请替换为你的实际目录：

```powershell
uv run --project D:\code\My-agent --locked myagent chat --repo D:\code\目标仓库 --allow-api --allow-harness --memory --env-file D:\code\My-agent\.env
```

没有使用 `.env` 时省略 `--env-file`。仅传 `--repo` 不会帮助 uv 找到本程序。

### 启动权限

| 参数 | 含义 |
| --- | --- |
| `--allow-api` | 允许向模型发送对话、代码和工具结果，可能产生费用；启动聊天必需 |
| `--allow-commands` | 允许模型自动执行宿主命令，命令拥有当前用户的环境访问能力 |
| `--allow-harness` | 允许模型按需运行已登记的项目检查，与宿主命令分别授权 |
| `--memory` | 启用长期记忆及自动提取，提取共享本轮模型预算 |

未授权的命令工具和 Harness 工具不会暴露给模型。文件工具默认可修改目标仓库；需要先分析时，可在对话中明确要求暂不改动文件。

## 日常对话

启动后可以连续输入：

```text
你 > 解释项目的启动流程，先不要修改代码
你 > 刚才提到的函数在哪里？
你 > 修复这个函数，保留我已有的其他改动，并运行项目检查
你 > /goal 修复失败的测试，并取得测试通过的执行证据
```

普通输入完成回答后回到提示符；显式设置目标后才启用评估和自动续跑。工作模型负责执行，独立的无工具评估调用根据已有证据判断是否完成。遇到需要澄清、无法验证、预算耗尽、评估异常或取消时暂停。

| 命令 | 作用 |
| --- | --- |
| `/help` | 查看交互命令帮助 |
| `/status` | 查看会话状态与用量 |
| `/diff` | 查看本会话文件工具产生的差异 |
| `/goal <条件>` | 设置并执行目标 |
| `/goal`、`/goal pause`、`/goal resume`、`/goal clear` | 查看、暂停、继续或清除目标 |
| `/compact` | 手动压缩旧历史 |
| `/memory` | 查看长期记忆状态与管理入口，需启用 `--memory` |
| `/new` | 开启新会话，保留磁盘上的文件改动 |
| `/exit` | 保存并退出 |

执行中按 `Ctrl+C` 会取消本轮请求或命令进程树，保留已有文件改动并返回提示符；等待输入时按 `Ctrl+C` 会保存退出。`/diff` 只覆盖本会话文件工具的修改，宿主命令或外部编辑器的修改需另行检查。

### 上下文与长期记忆

**上下文压缩**处理当前会话的旧历史：达到可用字符预算的 80% 时自动尝试压缩，目标降至 60%。至少保留最近 6 条原始消息及当前用户轮次，工具调用和结果成组处理。摘要由工作模型生成并计入预算，原始消息仍保存在会话中。最近轮次本身过大时，需要调整预算或使用 `/new`。详见 [上下文压缩](docs/context-compression.md)。

**长期记忆**用于跨会话复用信息，通过 `--memory` 开启。全局偏好跨项目共享，项目事实、决策和经验按仓库路径隔离；默认使用 `~/.patchpilot/memory.sqlite3`，可用 `--memory-db <路径>` 自定义。恢复会话时也需再次开启。

```text
你 > /memory remember --global 始终使用中文解释
你 > /memory remember --kind decision 项目检查优先使用 pytest
你 > /memory search 项目检查
```

自动提取使用工作模型并共享本轮预算；`/memory extract` 可重试待处理内容。详见 [原生长期记忆](docs/memory.md)。

## Harness 项目检查

授权 `--allow-harness` 后，模型可按需调用 `run_harness_check`，运行启动时登记的 profile。检查使用当前仓库的临时副本，包括已有的用户修改和未提交文件；普通相对文件的修改只作用于副本。

默认配置为 `<目标仓库>/.patchpilot/harness.toml`，也可用 `--harness-config <路径>` 指定。以下是 Python 项目的最小配置：

```toml
version = 1
runtime = "host"
default_profile = "test"
timeout_seconds = 120
max_output_bytes = 16000

[profiles.test]
argv = ["python", "-m", "pytest", "-q"]
cwd = "."

[profiles.lint]
argv = ["python", "-m", "ruff", "check", "."]
cwd = "."
```

本仓库已提供 [项目配置](.patchpilot/harness.toml)，`test` 运行聊天、上下文压缩、工具、Harness 和模型协议等相关测试，`lint` 运行 Ruff。完整历史回归在开发终端单独运行。更多配置见 [配置示例](.patchpilot/harness.toml.example) 和 [Harness 说明](docs/harness.md)。

| 运行环境 | 行为 |
| --- | --- |
| `host`（默认） | 在宿主上的临时副本执行，使用当前 Python 环境，无需 Docker |
| `docker` | 必须使用本地 Docker Linux 引擎及预先准备的镜像，缺失时返回 `infra_error` |
| `auto` | 优先探测 Docker 和本地镜像；预检不可用时，按 `allow_host_fallback` 决定是否使用宿主 |

profile 的 `runtime` 可覆盖全局配置，`--harness-runtime host|docker|auto` 可覆盖运行环境。Docker 镜像需包含相应检查依赖，Harness 不下载镜像或安装依赖；执行时使用不可变镜像 ID、非 root 用户、无网络和资源上限。检查执行失败后不会自动切换环境重跑。

配置在启动时加载并冻结，修改后需重新启动聊天。`argv` 使用参数数组和 `shell=False`，拒绝 shell、批处理入口和内联 Python；项目脚本仍需由使用者审核。未配置时，仅为检测到 Python 项目标记或根目录 Python 文件的仓库提供默认 pytest 检查。

快照默认排除凭据、链接、依赖、缓存、会话产物和旧基准维护者材料，文件数上限为 20000、体积上限为 100 MiB。宿主执行拥有当前用户权限，不提供安全沙箱。结果记录实际运行环境、退出码、耗时、快照摘要及产物路径；`passed` 表示登记命令以零退出码结束。

## 会话保存与恢复

会话默认保存在启动目录下的 `.patchpilot-sessions/<会话编号>/`，可通过 `--sessions-dir` 修改位置：

```text
state.json                    原始消息、目标、用量、压缩状态和文件修改记录
events.jsonl                  模型、工具、目标与 Harness 事件
harness/harness-<编号>/        检查配置、快照清单、输出和结果报告
```

在相同启动目录恢复：

```powershell
uv run --locked myagent chat --resume <会话编号> --allow-api --allow-harness --memory
```

从其他目录恢复时，显式指定原记录目录：

```powershell
uv run --project D:\code\My-agent --locked myagent chat --resume <会话编号> --sessions-dir D:\code\My-agent\.patchpilot-sessions --allow-api --allow-harness --memory
```

`--repo` 可用于核对原仓库路径。同一会话禁止并发打开。恢复后目标暂停，读取授权清空，未完成工具补记中断结果；旧命令和检查不会重放。需要继续目标时输入 `/goal resume`，本次所需的命令、Harness 和记忆选项需在启动时重新提供。

### 每轮预算

| 参数 | 默认值 |
| --- | --- |
| `--max-model-calls` | 24 次模型请求 |
| `--max-tool-calls` | 64 次工具调用 |
| `--timeout` | 600 秒 |
| `--max-tokens` | 100000 个已知 token |
| `--max-output-tokens` | 每次请求最多输出 4096 个 token |
| `--max-context-chars` | 120000 字符上下文 |
| `--max-continuations` | 8 次目标续跑 |

工作、摘要、记忆提取和评估调用共享本轮预算，并分别统计用量；Harness 受本轮剩余时限约束。已知 token 阈值并非费用硬上限，失败请求也可能产生费用。

## 开发与验证

### 本地检查

以下命令不需要真实模型密钥，默认跳过需要显式开启的 Docker 和 PostgreSQL 集成测试：

```powershell
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked pytest -q
uv build --wheel
```

### Docker、PostgreSQL 与参考补丁验证

需要 Docker Linux 引擎。以下命令与 CI 的集成验证对应，在项目根目录执行；PostgreSQL 使用 [compose.yaml](compose.yaml) 中的本地测试配置：

```powershell
docker compose up -d --wait postgres
docker build --tag patchpilot-tests:py312-v1 sandbox
$env:PATCHPILOT_TEST_DATABASE_URL = "postgresql+psycopg://patchpilot:patchpilot-local-only@127.0.0.1:55432/patchpilot"
uv run --locked pytest --run-postgres --run-docker -q
uv run --locked python -m patchpilot.evaluation.cli validate --output .patchpilot-evals
```

参考补丁校验要求正常测试通过、原缺陷可复现、参考修复通过独立验收。输出中的 `result_kind=dataset-validation-not-model-performance` 表示数据集校验，不代表模型修复成绩。CI 配置见 [ci.yml](.github/workflows/ci.yml)，失败时也会上传 `.patchpilot-evals` 证据。

默认测试中的模拟模型用于验证程序行为；真实 API 验收可另行执行“读代码 → 修改 → Harness 检查 → 追问 → 恢复”。Windows 与 Linux 各有平台专用测试，未支持的平台用例会跳过。

## 架构与文档

一次聊天请求的主要调用链：

```text
CLI → AgentSession → AgentLoop → 模型适配器
                         ↕
                 工具注册与执行 → 文件 / 宿主命令 / Harness
```

| 目录 | 职责 |
| --- | --- |
| `src/patchpilot/interfaces` | CLI 参数和交互输入输出 |
| `src/patchpilot/session_runtime` | 多轮会话、目标、压缩、记忆和恢复协调 |
| `src/patchpilot/core` | 模型与工具循环、预算、上下文、事件和取消协议 |
| `src/patchpilot/model` | 模型协议与 Anthropic 适配器 |
| `src/patchpilot/tools_runtime` | 工具注册、权限、执行和内置工具 |
| `src/patchpilot/infrastructure` | 会话与记忆存储、进程执行和验证设施 |
| `src/patchpilot/legacy/benchmark` | 固定任务、补丁和旧基准兼容链路 |
| `src/patchpilot/evaluation`、`src/patchpilot/service` | 内部评测及服务端回归支持 |

推荐从 [CLI](src/patchpilot/interfaces/cli/chat.py) 开始，再读 [Session](src/patchpilot/session_runtime/session.py)、[AgentLoop](src/patchpilot/core/loop.py)、[模型适配器](src/patchpilot/model/providers/anthropic.py) 和 [本地工具](src/patchpilot/tools_runtime/builtin/local.py)。

| 文档 | 内容 |
| --- | --- |
| [项目导读](learn.md) | 源码阅读顺序和核心调用链 |
| [文档导航](docs/README.md) | 完整设计文档入口 |
| [整体架构](docs/architecture.md) | 系统边界、职责与数据流 |
| [聊天执行链路](docs/chat-flow.md) | 一次输入如何经过模型和工具 |
| [会话与恢复](docs/session-and-recovery.md) | 目标、持久化、取消与恢复 |
| [内部模块边界](docs/modules.md) | 固定任务、服务和评测的定位 |
| [开发指南](docs/development.md) | 本地检查与源码阅读建议 |

内部 Python 包名 `patchpilot`、`.patchpilot-sessions`、`.patchpilot/harness.toml` 和默认记忆路径保留，已有会话与记忆无需因命令更名迁移。旧固定任务子命令及服务、评测 console script 已下线；内部评测仍通过 Python 模块调用。当前未提供 MCP、多 Agent 或后台任务。

项目使用 [MIT 许可证](LICENSE)。

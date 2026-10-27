# PatchPilot

面向本地仓库的持续对话 Agent。你可以连续提问、阅读代码、修改文件、运行宿主命令，并按需调用 Harness 检查当前代码。`patchpilot chat` 是唯一公开用户入口，无需先启动数据库或 Docker。

## 安装与启动

需要 Python 3.12+ 和 uv。在项目根目录安装锁定依赖：

```powershell
uv sync --locked
```

下面启动的是**常驻交互终端**：

```powershell
$env:ANTHROPIC_API_KEY = "你的API密钥"
# 使用 Anthropic 兼容服务时设置；默认使用官方地址。
# $env:ANTHROPIC_BASE_URL = "https://你的服务地址"
uv run --locked patchpilot chat --repo D:\code\My-agent --allow-api --allow-commands --allow-harness
```

也可复制 [.env.example](.env.example) 为 `.env`，再显式添加 `--env-file .env`。已有环境变量优先，不自动加载目标仓库的 `.env`。支持单行 `KEY=VALUE`、单双引号和 `export` 前缀，不执行脚本或展开变量。

`--model` 选择工作模型，`--evaluator-model` 选择目标评估模型，默认与工作模型相同。接口需要支持流式 Messages API、工具调用及工作模型的 adaptive thinking。

权限在每次启动时授予：

| 参数 | 开放能力 |
| --- | --- |
| `--allow-api` | 向模型发送对话、代码和工具结果，可能产生费用 |
| `--allow-commands` | 模型可自动运行宿主命令，拥有当前用户的环境访问能力 |
| `--allow-harness` | 模型可按需运行已登记的项目检查，与命令工具分别授权 |

不提供后两项时仍可对话和使用文件工具。未授权的执行工具不会暴露给模型。

## 连续对话与目标

```text
你 > 解释项目启动流程，先不要修改代码
你 > 刚才提到的函数在哪里？
你 > 修改这个函数，保留我已有的其他改动，完成后运行项目检查
你 > /goal 修复失败，并取得测试通过的执行证据
你 > /goal
你 > /goal pause
你 > /goal resume
你 > /goal clear
你 > /diff
你 > /status
你 > /new
你 > /exit
```

普通回答后回到输入提示；只有显式设置 `/goal <条件>` 才启用目标评估和自动续跑。工作模型负责执行，独立无工具评估调用根据现有证据判断是否完成。遇到无法验证、需要澄清、预算耗尽、评估异常或取消时暂停；使用 `/goal resume` 显式继续。

执行中 `Ctrl+C` 取消请求或命令进程树，保留文件修改后回到提示；等待输入时 `Ctrl+C` 保存退出。`/new` 开启新会话并保留磁盘修改。上下文达到上限时提示开启新会话，当前没有自动上下文压缩。

文件工具支持浏览、搜索、分页读取、新建、精确替换和差异。修改前必须先读取并携带摘要，外部变化会导致拒绝覆盖。默认跳过凭据、链接、依赖、缓存和会话产物。`/diff` 只跟踪当前会话文件工具的修改；命令或外部编辑器产生的修改需要额外检查。

## Harness 项目检查

模型在用户要求验证修改、检查测试或目标缺少执行证据时，按需调用 `run_harness_check`。不会每次写文件后自动运行，也不会再启动另一个模型 Agent。

默认配置位置为 `<repo>/.patchpilot/harness.toml`，可参考 [配置示例](.patchpilot/harness.toml.example)，或使用 `--harness-config <路径>` 指定：

本仓库已提供 [项目配置](.patchpilot/harness.toml)：`test` 检查聊天、工具、Harness 和模型协议，`lint` 执行静态检查。旧基准回归依赖的维护者材料会被通用快照排除，因此完整历史回归在开发终端单独运行。下面是其他 Python 仓库的通用配置示例：

```toml
version = 1
runtime = "auto"
image = "patchpilot-project:py312"
default_profile = "test"
timeout_seconds = 120
max_output_bytes = 16000
allow_host_fallback = true

[profiles.test]
argv = ["python", "-m", "pytest", "-q"]
cwd = "."

[profiles.lint]
argv = ["python", "-m", "ruff", "check", "."]
cwd = "."
```

配置在启动时读取并冻结；修改配置后重新启动聊天才会生效。模型只能选择已存在的 profile。命令以参数数组、`shell=False` 执行，拒绝 shell、批处理入口、内联 Python 和拼接字符；可以登记仓库内的检查脚本。`cwd` 必须位于仓库内。配置本身及项目脚本应由你审核。

没有配置时，只为检测到 Python 项目标记或根目录 Python 文件的仓库提供默认 `python -m pytest -q`。依赖使用当前 Python 环境；Harness 不自动安装依赖。其他项目需要显式登记 profile。

| runtime | 行为 |
| --- | --- |
| `auto` | 配置的本地 Docker Linux 镜像可用时使用 Docker；否则按 `allow_host_fallback` 决定是否转到宿主 |
| `docker` | 必须使用 Docker；缺少引擎或镜像时返回 `infra_error` |
| `host` | 在宿主上的临时仓库副本执行，不需要 Docker |

`--harness-runtime auto|docker|host` 可覆盖配置；省略则采用配置。镜像需要预先准备、包含 profile 所需依赖，Harness 不下载镜像或联网安装。Docker 使用不可变镜像 ID、无网络、非 root、资源上限和仅挂载临时副本；超时和取消时清理本次容器。

运行前复制当前仓库，包括已有用户修改和未提交文件。默认跳过隐藏目录（`.github` 除外）、凭据、依赖、缓存、旧维护者验收及参考答案；快照上限 20000 个文件、100 MiB。检查修改普通相对文件只会作用于副本。**宿主执行不提供安全沙箱，项目脚本仍拥有当前用户权限**；不会向检查环境直接传入模型密钥。

结果包含 profile、实际运行环境、退出码、耗时、快照摘要、截断标记和产物路径。宿主结果标记 `isolated=false / evidence_level=local`；Docker 为 `isolated=true / evidence_level=sandbox`。`passed` 表示登记检查返回零退出码，不代表测试覆盖充分、代码绝对正确或通过了隐藏验收。不会在检查执行失败后静默切换运行环境再跑一次。

## 保存与恢复

会话默认保存在**启动目录**下 `.patchpilot-sessions/<会话编号>/`：

```text
state.json                    完整消息、目标、用量、文件修改前内容、Harness 元数据
events.jsonl                  模型、工具、目标和 Harness 事件
harness/harness-<编号>/
  manifest.json               本次实际复制文件的摘要和模式
  profile.json                本次冻结的检查配置
  output.log                  有界输出尾部
  result.json                 检查报告
```

```powershell
uv run --locked patchpilot chat --resume <会话编号> --allow-api --allow-harness
# 从其他启动目录恢复时指定记录目录：
uv run --locked patchpilot chat --resume <会话编号> --sessions-dir D:\code\My-agent\.patchpilot-sessions --allow-api
```

可提供 `--repo` 核对原仓库路径。同一会话禁止并发打开，状态采用临时文件替换。恢复后目标暂停、文件读取授权清空、未完成工具补记中断结果，旧命令和 Harness 检查都不会重放。命令和 Harness 权限需要本次启动重新授予。

## 预算与验证

每次用户提交默认最多 24 次模型请求、64 次工具调用、600 秒、100000 个已知 token、120000 字符上下文、8 次目标续跑。工作模型和评估模型共用预算、分别统计；Harness 受本轮剩余时限约束。token 阈值不是费用硬上限，失败请求也可能已计费。

可使用 `--max-model-calls`、`--max-tool-calls`、`--timeout`、`--max-tokens`、`--max-output-tokens`、`--max-context-chars`、`--max-continuations` 调整。

下面是**一次性开发检查**，不需要真实模型密钥：

```powershell
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
```

Docker/PostgreSQL 内部回归需要显式传入测试开关；模拟模型测试只能证明运行链路，真实 API 验收应单独跑“读代码 → 修改 → Harness 检查 → 追问 → 恢复”。

## 实现与历史代码

阅读路线见 [架构说明](docs/architecture.md)。聊天循环、通用模型协议、上下文边界、文件工具和 Harness 已分开，便于后续添加上下文压缩等功能。当前没有 MCP、多 Agent、长期记忆或后台任务。

旧固定任务、服务、评测代码和历史文档暂留作内部迁移和回归参考。原来的固定任务子命令及服务/评测 console script 已下线，不属于产品用户入口。历史报告不能视为当前能力或真实模型成绩。

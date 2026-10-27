# PatchPilot 项目学习与面试讲解路线

这份文档按当前代码整理。PatchPilot 现在的产品入口只有 `patchpilot chat`：用户围绕一个本地仓库连续提问、阅读代码、修改文件、运行已授权命令，并按需调用受控 Harness 检查。

旧的固定任务、服务和评测代码仍保留在仓库中，用于内部迁移和回归参考；它们不再是普通用户的启动方式，也不应和聊天 Agent 混为一条调用链。

## 一、先记住项目定位

PatchPilot 是一个强调权限、证据和可恢复性的本地代码 Agent 原型。模型负责理解目标、决定下一步和组织工具调用；运行时负责检查参数、权限、路径和预算；Harness 负责在临时副本中执行预先登记的检查；会话层负责保存历史、工具结果、目标状态和恢复信息。

它不是只会修改代码的补丁生成器，也不是已经具备公网多租户安全能力的平台。当前综合 Agent 能够解释项目、阅读配置和测试、修改普通文本文件、执行用户授权的宿主命令，并在需要时请求 Harness 产生检查证据。

## 二、当前唯一产品调用链

```text
patchpilot chat
  -> cli.main()
  -> chat_cli.run_chat()
  -> AgentSession.submit()
  -> ContextManager 检查完整上下文容量
  -> AnthropicModel.complete()
  -> LocalTools.dispatch()
       -> list_files / read_file / search_text
       -> write_file / edit_file / get_diff
       -> run_command（需要 --allow-commands）
       -> run_harness_check（需要 --allow-harness）
  -> 工具结果按 tool_use_id 回到下一轮消息
  -> SessionStore 保存消息、事件、用量和修改记录
  -> 普通回答，或显式 /goal 后评估并继续/暂停
```

用户输入一次后，模型可以连续进行多次“模型响应—工具调用—工具结果”循环；普通回答结束后回到输入提示，不会自动进入目标评估。只有输入 `/goal <完成条件>`，才会开启独立、无工具的评估调用和自动续跑。

## 三、推荐的源码阅读顺序

### 1. 先看入口和产品边界

阅读：

1. `README.md`
2. `pyproject.toml`
3. `src/patchpilot/cli.py`
4. `src/patchpilot/chat_cli.py`
5. `docs/architecture.md`

需要确认：

- 安装后只公开 `patchpilot`，且只保留 `chat` 子命令。
- `--allow-api` 控制是否向模型服务发送请求。
- `--allow-commands` 单独开放宿主命令。
- `--allow-harness` 单独开放已配置的 Harness 检查。
- `--repo` 指定被操作的普通目录；`--resume` 恢复已有会话。
- 聊天不要求启动 PostgreSQL 或 Docker。

最常用的启动方式是：

```powershell
uv run --locked patchpilot chat `
  --repo D:\code\My-agent `
  --allow-api `
  --allow-commands `
  --allow-harness
```

### 2. 读模型适配和综合 Agent 提示词

阅读：

1. `src/patchpilot/prompts.py`
2. `src/patchpilot/runtime.py`
3. `src/patchpilot/anthropic_model.py`
4. `src/patchpilot/session.py`

`prompts.py` 将职责拆成三层：

- `AGENT_CORE_PROMPT`：理解目标、阅读证据、谨慎修改、解释风险、不能伪造结果。
- `CHAT_POLICY_PROMPT`：说明本地文件工具、宿主命令、Harness、用户改动保护和权限边界。
- `BENCHMARK_POLICY_PROMPT`：只给内部固定任务模式使用，限制公开工作区和独立验收材料。

这样聊天 Agent 是综合开发 Agent，旧固定任务模式仍可以复用相同的核心能力，但不会把固定任务限制错误地套到普通仓库。

`runtime.py` 提供模型调用所需的预算、适配器协议和统一失败类型。`AnthropicModel` 负责 Anthropic 兼容 Messages API、流式响应收集、工具调用协议、超时和错误归类；`AgentSession` 不直接依赖 SDK 细节。

### 3. 读 `AgentSession`：持续对话的核心

重点看 `src/patchpilot/session.py` 的 `submit()`、工具循环、`_call()` 和恢复处理。

一轮提交的大致步骤是：

1. 检查取消信号、时间、模型调用、工具调用和 token 预算。
2. 用完整历史构造请求，并检查上下文字符上限。
3. 保存 assistant 响应；如果包含工具调用，逐个分发。
4. 为每个 `tool_use` 生成同一 ID 的 `tool_result`，再继续请求模型。
5. 保存文本、工具结果、用量和事件。
6. 普通回答结束后等待下一个输入；目标模式才交给 `GoalController` 评估。

这里的关键不是一个无限 `while`，而是一个可恢复的会话状态机：消息、工具调用、目标、文件修改基线和用量都能落盘；取消时保留已经发生的修改，并补齐工具结果。

### 4. 读本地工具：模型能做什么由运行时决定

阅读 `src/patchpilot/local_tools.py`。

聊天工具包括：

```text
list_files          浏览仓库内公开文件
read_file           分页读取 UTF-8 文本并返回 sha256
search_text         搜索文本并返回路径、行号和有界结果
write_file          新建或替换文件
edit_file           对唯一匹配文本做精确替换
get_diff            查看本会话文件工具产生的差异
run_command         在宿主执行用户授权的命令
run_harness_check   运行已登记的 Harness profile
```

文件工具的边界：

- 路径必须是仓库内的普通相对路径。
- 拒绝越界、绝对路径、路径链接和目录联接。
- 默认跳过凭据、`.env`、依赖目录、缓存、隐藏目录和会话产物。
- 修改必须携带最近读取的文件摘要；文件被编辑器或其他进程改动后拒绝覆盖。
- 首次修改会保存原内容，因而非 Git 目录也能查看本会话差异。
- 不自动提交、重置或清理用户已有改动。

`run_command` 只有在启动参数包含 `--allow-commands` 时才会暴露给模型。Windows 使用 `PowerShell -NoProfile`；底层进程执行器统一限制时长、输出大小，并在取消或超时后回收进程树。

### 5. 读 Harness：把“运行检查”从聊天循环中抽出来

阅读 `src/patchpilot/harness.py`，然后看 `src/patchpilot/process_runner.py`、`src/patchpilot/sandbox.py` 和 `src/patchpilot/verification.py`。

核心对象是：

```text
HarnessProfile  一个允许执行的检查配置
HarnessConfig   启动时解析并冻结的配置
HarnessResult   带证据和状态的结构化结果
HarnessService  建立快照、选择运行时、执行检查和写报告
```

优先读取仓库中的 `.patchpilot/harness.toml`，也可以用 `--harness-config` 指定；没有配置时仅对可识别的 Python 项目提供默认 pytest profile。模型只能传入已存在的 profile 名称，不能传任意命令字符串。profile 使用参数数组，拒绝 shell 拼接、管道、重定向和隐式脚本执行；`cwd` 必须位于仓库内。

一次 Harness 检查的流程是：

```text
当前仓库
  -> 复制临时快照（包含用户当前改动）
  -> 记录实际复制内容的 snapshot hash
  -> 选择 docker / host 运行时
  -> 执行冻结 profile
  -> 限制时长和输出，必要时取消进程树
  -> 写 manifest、profile、日志和 result
  -> 将结构化结果加入聊天历史
```

运行时含义：

| runtime | 行为 |
| --- | --- |
| `auto` | Docker 镜像可用时优先 Docker；不可用且允许降级时使用宿主 |
| `docker` | Docker 或镜像不可用直接返回 `infra_error` |
| `host` | 在宿主的临时副本运行，不需要 Docker |

Docker 结果标记 `isolated=true / evidence_level=sandbox`；宿主结果标记 `isolated=false / evidence_level=local`。宿主检查是本地回归证据，不能描述成独立隔离验收。

`passed` 只表示登记命令返回零退出码，不代表代码绝对正确，也不等于固定任务的维护者隐藏验收结论。检查失败、超时、取消、基础设施错误和清理异常都保留在结果中，不会失败后静默换环境重跑。

### 6. 读 Goal Loop：显式目标才自动推进

阅读 `src/patchpilot/goal.py` 和 `src/patchpilot/session.py` 中的目标调用部分。

交互命令：

```text
/goal <完成条件>   设置目标并开始执行
/goal              查看条件、状态、评估次数和用量
/goal pause        暂停
/goal resume       显式继续未完成目标
/goal clear        清除目标但保留聊天历史和文件修改
```

工作模型可以使用本地工具；评估模型没有工具权限，只检查聊天消息和工具证据，并返回严格的 `ok / reason / impossible` JSON。评估时必须区分：

- Harness 工具调用成功，还是检查本身 `status=passed`；
- 命令退出码是否符合目标；
- 证据是否对应当前快照；
- 结果是宿主本地证据还是 Docker 隔离证据。

目标和评估共用模型预算，评估异常、无法客观验证、取消或预算耗尽时保留目标并暂停；只有 `/goal resume` 才会再次运行。

### 7. 读会话保存与上下文边界

阅读 `src/patchpilot/session_store.py` 和 `src/patchpilot/context.py`。

会话目录默认是启动目录下的 `.patchpilot-sessions/<会话编号>/`，保存：

- 完整消息和工具调用配对结果；
- 目标状态、评估次数和模型用量；
- 文件修改前内容及 Harness 元数据；
- `events.jsonl` 中的模型、工具、目标和检查事件；
- Harness 的 manifest、冻结 profile、输出尾部和 result。

状态文件采用临时文件替换，同一会话禁止并发打开。恢复时：

- 核对仓库路径；
- 目标默认暂停；
- 清空旧的文件读取授权，修改前必须重新读取；
- 未完成工具调用补记中断结果；
- 不自动重放旧命令或旧 Harness 检查；
- `--allow-commands`、`--allow-harness` 等权限需要本次启动重新授予。

`ContextManager` 目前只负责消息追加和容量判断，不会静默删除历史；达到上限时提示使用 `/new`。未来可以在这里增加 `compact()`，而不改变工具协议和会话格式。

## 四、旧固定任务链应该如何理解

旧链路仍可作为内部实现和回归材料阅读，但已经不是公开产品入口：

```text
内部任务输入
  -> task.py / snapshot.py / workspace.py
  -> agent.py 的固定任务 Agent 循环
  -> tools.py / patching.py
  -> sandbox.py 与 HarnessService 的基准适配
  -> verification.py 的 verify_patch 兼容封装
```

它解决的是固定快照、公开任务工具和独立基准验收；聊天 Agent 不会再嵌套调用这套完整模型循环。`verify_patch()` 只作为内部兼容适配器复用通用 Harness 的执行能力。

旧的 `task.py`、`tools.py`、`evaluation/`、`service/` 和历史测试暂时保留，原因是方便迁移和回归，而不是建议用户启动它们。服务代码仍包含 FastAPI、PostgreSQL、Worker、租约和事件模型；评测代码仍包含数据集、baseline 和指标，但它们不属于聊天的正常产品路径。

## 五、最值得掌握的设计亮点

### 1. 权限由代码控制，不靠提示词自觉

模型只能看到已经开放的工具。是否可以执行宿主命令、是否可以运行 Harness，由启动参数决定；工具分发器还会再次检查参数、路径和状态。

### 2. 文件修改有乐观并发保护

模型先读文件得到 `sha256`，再携带摘要修改。外部编辑导致摘要变化时，运行时拒绝覆盖并要求重新读取。这解决的是“模型基于旧内容写回”的问题。

### 3. Harness 产生的是可追踪证据

结果绑定 profile、实际运行时、退出码、耗时、快照摘要和产物路径。检查在副本中进行，不改变用户当前工作区；报告明确区分宿主证据与 Docker 隔离证据。

### 4. 普通对话和目标循环边界清楚

普通回答不会偷偷触发评估或自动续跑。只有用户显式设置目标，评估器才会检查完成条件；目标暂停和恢复也是显式操作。

### 5. 恢复策略避免重复副作用

恢复会话不会自动重放历史命令或 Harness。旧工具调用会补记为中断，权限和文件读取授权重新建立，避免把未知状态当成成功。

### 6. 失败状态比一个布尔值更重要

模型失败、工具拒绝、文件冲突、命令失败、Harness 超时、基础设施不可用和评估异常分别记录。这样用户能知道下一步是重试、重新读取、修改配置，还是需要人工判断。

## 六、面试时可以这样介绍

> PatchPilot 是一个面向本地仓库的持续对话 Agent。模型通过严格的文件工具读取和修改代码，修改必须携带读取时的文件摘要；命令和 Harness 分别由用户显式授权。Harness 不接受模型临时拼接的命令，而是从冻结 profile 中选择检查，在当前仓库的临时副本执行，并返回带快照摘要、退出码、运行时和产物路径的结构化证据。会话保存完整消息、工具结果、目标状态和修改记录，恢复时不会自动重放旧副作用。旧固定任务和服务代码仍作为内部验证材料保留，但不再是聊天入口。

回答技术追问时始终说明三件事：

1. **谁决定下一步？** 模型提出工具请求，运行时决定是否允许，Harness 产生检查证据。
2. **什么时候生效？** 权限在启动时生效，文件摘要在修改时校验，检查在模型按需调用时执行，目标评估只在显式 `/goal` 后执行。
3. **状态怎么流动？** 消息进入上下文，工具结果按 ID 配对，文件修改记录基线，Harness 记录快照和报告，会话存储负责恢复。

## 七、实操验证顺序

```powershell
# 安装并查看唯一入口
uv sync --locked
uv run --locked patchpilot --help

# 不调用模型运行本地回归
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .

# 启动只读代码对话
uv run --locked patchpilot chat --repo D:\code\My-agent --allow-api

# 允许宿主命令，但不允许 Harness
uv run --locked patchpilot chat `
  --repo D:\code\My-agent --allow-api --allow-commands

# 允许按需 Harness 检查
uv run --locked patchpilot chat `
  --repo D:\code\My-agent --allow-api --allow-commands --allow-harness

# 恢复会话；权限需要重新提供
uv run --locked patchpilot chat `
  --resume <会话编号> `
  --sessions-dir D:\code\My-agent\.patchpilot-sessions `
  --allow-api --allow-harness
```

建议用“读取代码 → 修改文件 → 按需运行 Harness → 追问 → 退出 → 恢复”的顺序验证。模拟模型测试只能证明协议和状态流转；真实 API 运行和真实 Docker 检查应单独记录，不能把单元测试通过写成模型已经自主完成修复。

## 八、当前明确限制

- 没有 MCP、多 Agent、后台任务、长期记忆和自动上下文压缩。
- 宿主命令和宿主 Harness 运行在当前用户权限下；`isolated=false` 的结果不是安全沙箱证明。
- Harness 不自动安装依赖，也不会在检查失败后换环境重跑。
- `passed` 只代表已登记检查返回零退出码，不等于完整正确性或隐藏验收通过。
- 旧服务和评测代码仍在仓库中，但不属于当前用户入口；不要把历史基准报告当成聊天 Agent 的真实成功率。

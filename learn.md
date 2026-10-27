# PatchPilot 项目学习与面试讲解路线

这份文档服务两个目标：

1. 用最短路径读懂项目的主流程，而不是从测试或所有细节开始。
2. 把源码中的设计取舍转换成简历和面试中可以核对的项目亮点。

项目名是 **PatchPilot**。它是一个面向受支持 Python 仓库的受控代码修复 Agent：模型读取 Issue 和公开代码，通过白名单工具生成候选补丁；系统再从固定基线重建工作区，在 Docker 中执行测试，并保存补丁、日志、会话和验证报告。

项目有两条需要分开理解的主线：

- `patchpilot chat`：面向本地仓库的持续开发对话，支持文件工具、可选宿主命令、会话恢复和显式 `/goal` 目标循环。
- `agent-run` / `patchpilot-service`：面向固定任务快照的受控修复、独立验收和 PostgreSQL 任务服务。

面试时不要把它描述成已经具备任意仓库、任意命令、多人公网运行能力的通用 Coding Agent。当前最有说服力的定位是：**一个强调边界、证据和可恢复性的代码修复 Agent 原型**。

---

## 一、先记住这条总调用链

### 1. 固定任务修复链

```text
命令行
  -> patchpilot.cli.main()
  -> load_task()
  -> compute_snapshot_hash()
  -> create_workspace()
  -> run_agent()
  -> AnthropicModel.complete()
  -> ToolDispatcher.dispatch()
  -> list/read/search/apply_patch/run_tests 等工具
  -> export_diff()
  -> verify_patch()
  -> DockerRunner.run()
  -> agent-result.json / verification.json / candidate.patch
```

对应的核心问题是：模型提出了什么动作，运行时如何校验，动作改了哪一份工作区，测试证据如何产生，最后结论如何绑定到这次补丁。

### 2. 本地持续对话链

```text
patchpilot chat
  -> chat_cli.run_chat()
  -> AgentSession.submit()
  -> AnthropicModel.complete()
  -> LocalTools.dispatch()
  -> SessionStore.save()/event()
  -> 普通回答，或 GoalController 评估后继续/暂停
```

这条链直接操作用户指定的本地仓库。它和固定任务模式使用不同的工具、权限和验证路径；`chat` 中的宿主命令不是 Docker 沙箱命令。

### 3. 服务化链

```text
HTTP API
  -> service.api.create_app()
  -> Repository.create_run()
  -> PostgreSQL queued 状态
  -> Worker.claim()（行锁 + SKIP LOCKED）
  -> AgentExecutor.execute()
  -> run_agent()
  -> heartbeat / checkpoint / artifact / event
  -> Repository.finalize()
```

API 负责排队和查询，Worker 负责真正执行。两者通过 PostgreSQL 协作，不要把它讲成已经拆成多个微服务的平台。

---

## 二、最快的代码阅读路线

### 第 0 步：先看项目边界

先读：

1. `README.md` 开头和“当前边界”部分
2. `pyproject.toml`
3. `docs/architecture.md`

需要回答：

- 项目解决什么问题？为什么限定为固定 Python 任务快照？
- 有哪些入口命令？
- 模型、工具运行时、Docker 执行器、独立验证器分别负责什么？
- 哪些是当前能力，哪些只是后续计划？

`pyproject.toml` 可以确认 Python 版本、依赖、入口脚本：

- `patchpilot` -> `patchpilot.cli:main`
- `patchpilot-service` -> `patchpilot.service.cli:main`
- `patchpilot-eval` -> `patchpilot.evaluation.cli:main`

### 第 1 步：从 CLI 入口建立索引

读 `src/patchpilot/cli.py` 的 `main()`，只关注子命令分支：

- `chat`：交给 `chat_cli.run_chat`
- `validate-task`：调用 `load_task`，可输出 `to_agent_input`
- `sandbox-check`：检查 Docker 和测试镜像
- `agent-run`：创建模型、预算和 DockerRunner，调用 `run_agent`
- `verify-patch`：读取补丁字节，调用 `verify_patch`

这一步的目的不是读完参数，而是确定“入口如何把控制权交给核心模块”。

### 第 2 步：先读任务和快照，不要先读模型

按顺序读：

1. `src/patchpilot/task.py`
2. `src/patchpilot/snapshot.py`
3. `src/patchpilot/workspace.py`

重点看：

- `TaskSpec` 用 Pydantic 严格限制任务格式、环境标识、可写目录和测试配置。
- `load_task()` 检查 `task.json`、`repo/`、`Issue.md`、`src/`、`tests/`、`repro/`、`pytest.ini`，并核对快照 SHA-256。
- `to_agent_input()` 显式构造给模型看的公开视图，避免把 `LoadedTask` 内部路径、参考答案或维护者资料直接序列化给模型。
- `compute_snapshot_hash()` 对“相对 POSIX 路径 + 文件内容摘要”排序后再做总 SHA-256，不依赖修改时间和遍历顺序。
- `create_workspace()` 只复制任务的 `repo/`，不复制任务包、答案或宿主目录，并再次校验复制结果摘要。

这里形成一个重要面试概念：**任务元数据校验、快照身份校验和实际执行隔离是三件事**。`environment_id` 是登记信息，不等于自动获得沙箱权限；真正的测试隔离由 `DockerRunner` 完成。

### 第 3 步：读单 Agent 循环

核心文件：`src/patchpilot/agent.py`。

重点函数和类：

- `AgentBudget`：限制模型调用、工具调用、总时间、总 token、上下文和重复动作。
- `AgentModel`：模型适配器协议，核心循环不依赖具体 SDK。
- `ModelFailure`：把 SDK/网络失败归一成安全错误分类。
- `run_agent()`：单决策者循环和候选补丁出口。

建议按下面顺序读 `run_agent()`：

1. 创建或恢复 `Workspace`。
2. 用 `to_agent_input()` 构造公开任务输入。
3. 先调用一次 `list_files` 做初始目录观察。
4. 检查时间、调用次数、token、上下文和取消状态。
5. 调用模型并保存完整 assistant 响应。
6. 先做响应级协议检查，再逐个处理 `tool_use`。
7. `ToolDispatcher.dispatch()` 返回结果后，构造带同一 `tool_use_id` 的 `tool_result`，放回下一轮消息。
8. 记录 `model_started`、`model_finished`、`tool_started`、`tool_finished` 等事件。
9. 模型合法 `end_turn` 后导出候选补丁并进入独立验证。
10. 生成 `candidate.patch`、`transcript.json`、`events.jsonl`、`agent-result.json`。

要特别理解三个顺序：

- 响应级协议检查发生在工具执行前；不能收到半截补丁就开始执行。
- 同一轮工具是逐个执行的，不是整批工具事务；前一个工具可能已经修改文件，后一个工具才发现参数错误。
- `end_turn` 只表示模型结束候选生成，不表示补丁已经通过测试。

### 第 4 步：读工具权限和补丁写入

按顺序读：

1. `src/patchpilot/tools.py`
2. `src/patchpilot/patching.py`

固定任务模式开放七个工具：

```text
list_files
read_file
search_text
apply_patch
add_regression_test
get_diff
run_tests
```

`ToolDispatcher` 的职责：

- 用严格 Pydantic schema 校验参数，并拒绝多余字段。
- 做路径白名单检查，拒绝越界、链接、隐藏路径和不允许修改的目录。
- 限制文件大小、结果长度和测试目标。
- 在 `artifacts/tools/` 保存每次工具调用的有界证据。

`patching.py` 的亮点：

- `apply_patch()` 要求模型传入最近工作区的 `expected_hash`，防止基于旧内容覆盖新内容。
- 只允许受支持的 unified diff 和指定源码路径；原测试、配置、依赖不能被模型改写。
- 补丁先在 staging 目录中执行 `git apply --check` 和实际应用，再核对文件清单和工作区摘要，最后发布到工作区。
- `export_diff()` 从原始快照和当前工作区导出候选补丁，而不是相信模型自己声称改了什么。

面试时可以把它概括成：**模型只提出意图，运行时决定动作是否有权限，文件系统变化再反过来生成证据**。

### 第 5 步：读 Docker 执行和独立验收

按顺序读：

1. `src/patchpilot/sandbox.py`
2. `src/patchpilot/verification.py`

`DockerRunner` 负责：

- 使用预构建、可识别的本地镜像，不在测试时动态安装依赖。
- 将工作区复制进容器，而不是把宿主目录直接挂载给测试代码。
- 用固定命令运行公开测试，并保存日志和有界 JUnit 报告。
- 结合进程退出码和测试报告分类，区分通过、断言失败、收集错误、无测试、全部跳过、超时和基础设施错误。
- 施加无网络、非 root、CPU/内存/进程数/临时目录等资源限制。

`verify_patch()` 的核心保证：

1. 从原始快照重新创建验证工作区。
2. 只接收封存的补丁字节，不接收 Agent 当前工作区。
3. 应用补丁后运行原测试、公开复现测试和维护者验收目录。
4. 报告绑定 `patch_sha256`、`base_sha256`、`candidate_sha256`、验收材料摘要和实际镜像信息。
5. 验收期间再次检查候选工作区和验收目录摘要是否发生变化。

可信边界要讲准确：这套设计适合自行编写、维护者审核的可信任务；它提高了可追溯性和误判防护，不等于对恶意 Python 代码提供虚拟机级安全保证。

### 第 6 步：再读本地聊天模式

按顺序读：

1. `src/patchpilot/chat_cli.py`
2. `src/patchpilot/session.py`
3. `src/patchpilot/local_tools.py`
4. `src/patchpilot/session_store.py`
5. `src/patchpilot/goal.py`
6. `src/patchpilot/anthropic_model.py`

关键职责：

- `chat_cli.py` 解析 `--repo`、`--resume`、`--allow-api`、`--allow-commands` 和预算参数。
- `AgentSession.submit()` 实现一轮或多轮模型—工具交互，普通输入回答后等待下一次输入。
- `LocalTools` 提供 `list_files`、`read_file`、`search_text`、`write_file`、`edit_file`、`get_diff`，以及显式开启时的 `run_command`。
- 本地文件修改必须先读取，并携带 `sha256`；文件版本变化时拒绝覆盖。
- `SessionStore` 保存状态和事件；恢复会话时不会自动重放上一次未完成的命令，而是补充“结果未知”的工具结果。
- `GoalController` 只有在用户显式输入 `/goal` 后才启用自动续跑；评估模型无工具权限，只能给完成/暂停/继续判断。
- `AnthropicModel` 负责 SDK 适配、流式响应收集、完整 JSON 校验、超时和错误分类。

这一条线的面试亮点是：**“持续对话”是会话状态机和恢复策略，不只是把模型调用放进 `while True`**。

### 第 7 步：最后读服务和评测

服务端按顺序读：

1. `src/patchpilot/service/cli.py`
2. `src/patchpilot/service/api.py`
3. `src/patchpilot/service/repository.py`
4. `src/patchpilot/service/worker.py`
5. `src/patchpilot/service/executors.py`
6. `src/patchpilot/service/models.py`
7. `src/patchpilot/service/artifacts.py`

评测按顺序读：

1. `src/patchpilot/evaluation/catalog.py`
2. `src/patchpilot/evaluation/runner.py`
3. `src/patchpilot/evaluation/baseline.py`
4. `src/patchpilot/evaluation/validation.py`
5. `src/patchpilot/evaluation/metrics.py`

---

## 三、核心代码文件清单

### 入口和 Agent 编排

| 文件 | 作用 | 面试关键词 |
| --- | --- | --- |
| `src/patchpilot/cli.py` | 统一 CLI 入口，分发聊天、任务校验、沙箱检查、Agent 执行和补丁验收 | 命令分层、显式授权 |
| `src/patchpilot/chat_cli.py` | 本地聊天命令和交互终端 | 常驻交互、恢复会话 |
| `src/patchpilot/agent.py` | 固定任务的单 Agent 循环、预算、工具协议、事件和候选导出 | 状态机、预算、可审计 |
| `src/patchpilot/session.py` | 聊天模式的多轮会话、工具调用、目标续跑和暂停 | 会话状态、恢复、取消 |
| `src/patchpilot/anthropic_model.py` | Anthropic SDK 适配层，不把 SDK 细节扩散到 Agent 循环 | 适配器、超时、错误分类 |
| `src/patchpilot/goal.py` | `/goal` 目标的设置、暂停、恢复和评估结果解析 | 显式目标、评估器隔离 |
| `src/patchpilot/control.py` | 取消和停止信号，在模型、工具、测试之间传递 | 协作式取消 |

### 任务身份、工作区和补丁

| 文件 | 作用 | 面试关键词 |
| --- | --- | --- |
| `src/patchpilot/task.py` | 严格校验 `task.json`、布局、Issue 和快照摘要；构造公开 Agent 输入 | Pydantic、输入边界 |
| `src/patchpilot/snapshot.py` | 计算确定性的内容快照 SHA-256 | 可复现、内容寻址 |
| `src/patchpilot/workspace.py` | 从固定快照复制独立运行目录 | 工作区隔离、基线 |
| `src/patchpilot/patching.py` | 校验、staging、应用和导出 unified diff | 乐观并发、补丁边界 |
| `src/patchpilot/verification.py` | 从基线重建工作区并执行独立验收 | 独立验证、证据绑定 |

### 工具和执行环境

| 文件 | 作用 | 面试关键词 |
| --- | --- | --- |
| `src/patchpilot/tools.py` | 固定任务工具 schema、路径策略、分发和工具证据 | 白名单、纵深防御 |
| `src/patchpilot/sandbox.py` | Docker 容器创建、资源限制、日志和测试结果分类 | 受控执行、失败分类 |
| `src/patchpilot/local_tools.py` | 聊天模式的本地文件工具和可选宿主命令 | 文件版本冲突、权限开关 |
| `src/patchpilot/process_job.py` | 服务或测试中对外部进程的有限生命周期管理 | 进程树、超时清理 |

### 会话和服务持久化

| 文件 | 作用 | 面试关键词 |
| --- | --- | --- |
| `src/patchpilot/session_store.py` | 保存聊天会话状态、事件和恢复记录 | 原子保存、历史恢复 |
| `src/patchpilot/service/api.py` | FastAPI 任务、运行记录、产物、取消、重启和 SSE 接口 | API 边界、事件续读 |
| `src/patchpilot/service/repository.py` | PostgreSQL 状态读写、领取、租约、心跳、检查点和终态提交 | 行锁、fencing、事务 |
| `src/patchpilot/service/worker.py` | 独立 Worker 领取任务、心跳、执行、发布结果 | 队列消费、租约 |
| `src/patchpilot/service/executors.py` | `AgentExecutor` 和 `ReferenceDiagnosticExecutor` 两种执行器 | 真实模型与诊断路径分离 |
| `src/patchpilot/service/artifacts.py` | 补丁、报告、日志和 checkpoint 产物存储与恢复 | 大文件外置、摘要登记 |
| `src/patchpilot/service/models.py` | Run、Attempt、Step、Event、Artifact、Checkpoint 等数据库表结构 | 状态建模 |

### 评测和基线

| 文件 | 作用 | 面试关键词 |
| --- | --- | --- |
| `src/patchpilot/evaluation/catalog.py` | 加载 20 项任务、开发/保留拆分、任务指纹 | 数据集治理 |
| `src/patchpilot/evaluation/runner.py` | 生成批次计划、冻结环境信息、运行 Agent 或 baseline 并保存记录 | 可复现实验 |
| `src/patchpilot/evaluation/baseline.py` | 固定检索 + 单次生成的对照策略，不提供测试反馈循环 | 对照实验 |
| `src/patchpilot/evaluation/validation.py` | 检查任务、参考补丁和验收材料 | 评测数据质量 |
| `src/patchpilot/evaluation/metrics.py` | 成功率、失败分类、配对比较、耗时和成本估算 | 指标完整性、未知成本 |

### 测试应该怎么读

测试用于验证设计，不是第一阅读入口。建议按主题挑选：

- `tests/test_task.py`、`tests/test_snapshot.py`：任务和摘要边界。
- `tests/test_tools.py`、`tests/test_local_tools.py`：工具参数、路径和文件冲突。
- `tests/test_patching.py`、`tests/test_verification.py`：补丁边界和独立验收。
- `tests/test_agent.py`、`tests/test_anthropic_model.py`：循环协议、预算和模型适配。
- `tests/test_chat_session.py`：会话恢复、文件修改基线和目标续跑。
- `tests/test_service_api.py`、`tests/test_repository.py`、`tests/test_worker.py`：服务状态、租约和 Worker。
- `tests/test_eval_runner.py`、`tests/test_eval_metrics.py`：评测计划、记录和统计。
- `tests/test_reference.py`：参考补丁能否通过独立验证；它证明的是验证流水线，不是模型自主修复能力。

---

## 四、面试时最值得讲的亮点

### 亮点 1：把 Agent 从“能调用模型”变成“受控的工具协议”

可以这样讲：

> 我没有让模型直接执行任意 shell，而是给它固定的工具 schema。模型只能请求列目录、读文件、搜索、应用补丁、添加回归测试、查看 diff 和运行公开测试。每次调用都要经过严格参数校验、路径白名单、文件大小和结果长度限制，工具结果再以同一个 `tool_use_id` 回到下一轮上下文。

继续追问时讲清：模型决定“想做什么”，运行时决定“能不能做”，不是靠系统提示词保证安全。

### 亮点 2：用快照、摘要和独立工作区保证可复现

可以这样讲：

> 任务先固定源码快照和 SHA-256。Agent 在复制出来的工作区中运行，原始快照不直接被修改；修改前的 `expected_hash` 可以防止模型基于旧版本覆盖文件。验收时不信任 Agent 当前工作区，而是从原始基线重新创建目录、重放封存补丁，再运行同一套测试。

这里的重点不是“用了哈希”这个名词，而是哈希在三个位置形成闭环：

```text
任务快照身份 -> 工作区基线 -> apply_patch 版本检查 -> 验收报告绑定补丁/基线/候选摘要
```

### 亮点 3：独立验收避免把模型的完成宣言当成通过

可以这样讲：

> Agent 的 `end_turn` 只代表它结束了候选生成。最终状态由 `verify_patch()` 决定。验证器从基线重建工作区，应用补丁，在 Docker 中运行原测试、公开复现测试和维护者验收，并联合退出码和 JUnit 报告分类；全跳过、零测试、报告缺失和退出码矛盾都不能算通过。

这比“模型说修好了，所以返回成功”更容易让面试官看到工程思维。

### 亮点 4：把失败当作一等状态，并且能解释失败原因

项目没有只返回 `True/False`，而是区分：

- 模型认证、限流、连接和超时错误
- 工具参数错误、路径拒绝、版本冲突
- 测试断言失败、收集错误、无测试、全部跳过、超时
- 工作区或验收材料改变等基础设施错误
- `unverified`、`rejected`、`failed`、`passed` 等最终状态

面试表达：

> 我优先保证失败可解释和证据可追踪，而不是把所有异常吞掉后返回一个模糊的失败。

### 亮点 5：服务模式处理“旧 Worker 迟到写回”的并发问题

可以这样讲：

> API 只负责创建 queued Run，Worker 用 PostgreSQL 的 `FOR UPDATE SKIP LOCKED` 领取任务。每个 Attempt 有 worker、随机 token 和 lease；心跳续租，所有写事件和终态转换前都重新做 fencing 校验，确认当前 Attempt 仍然拥有租约。这样旧 Worker 即使在网络延迟后恢复，也不能把迟到结果写成当前任务的最终结果。

要补充真实边界：fencing 不能撤销已经发出的模型请求，也不能把文件系统和数据库变成一个分布式事务；它解决的是状态发布的所有权问题。

### 亮点 6：聊天模式把“持续对话”做成可恢复会话

可以这样讲：

> `AgentSession` 持久化消息、目标、用量和文件修改基线。恢复时会补齐未完成的工具结果并暂停目标，不会自动重放旧命令；用户显式 `/goal resume` 后才继续。普通回答不会自动进入目标循环，只有 `/goal` 才会调用无工具评估器判断是否继续。

这能说明你理解会话恢复、幂等和用户授权，而不是只实现了一个无限循环。

### 亮点 7：评测有 baseline，但不夸大实验结论

项目提供：

- 20 项自建合成任务，开发/保留拆分。
- 固定检索、单次生成、无测试反馈的 baseline。
- Agent 与 baseline 的配对比较、失败分类、耗时和成本估算。
- 数据集、代码、工具 schema、镜像和提示词的指纹记录。

正确的面试说法是：

> 我搭建了可复现的评测框架和基线对照，但在没有真实批量 A/B 结果前，不声称模型一定优于 baseline。参考补丁测试通过只证明验证流水线可用，不能当作模型成功率。

---

## 五、可以直接用于简历的项目描述

下面这版只使用当前代码已经体现的能力，后续可以根据你真正跑出的数据补充数字：

**PatchPilot｜受控 Python 代码修复 Agent**

- 设计并实现面向固定 Python 任务快照的代码修复 Agent，采用“模型决策—白名单工具—独立验收”链路，支持 Issue 解析、源码检索、候选补丁生成和测试证据记录。
- 基于 Pydantic 严格工具契约、路径/文件类型白名单、`expected_hash` 版本校验和独立工作区，限制模型只能修改允许的源码范围，并避免基于旧内容覆盖文件。
- 使用固定 Docker 测试环境执行公开测试、复现测试和维护者验收；从原始快照重建候选工作区，生成绑定 `base/candidate/patch SHA-256` 的可审计报告。
- 实现本地持续对话模式，支持会话持久化、恢复、目标评估、取消和有限自动续跑；通过 PostgreSQL + Worker 实现任务排队、租约、心跳、fencing、checkpoint 和事件查询。
- 构建 20 项任务的可复现评测与 baseline 对照框架，记录数据集、代码、工具 schema、镜像和模型用量指纹，区分模型失败、测试失败和基础设施失败。

如果简历只允许两行，可以压缩为：

> 实现受控 Python 修复 Agent：模型仅通过白名单工具操作独立工作区，补丁经 SHA-256 基线校验后在固定 Docker 中独立验收，并产出可追溯的 patch、日志和测试报告。扩展 PostgreSQL + Worker 任务服务，支持租约 fencing、checkpoint 恢复和事件查询。

---

## 六、60 秒项目介绍模板

> 我做的是 PatchPilot，一个面向受支持 Python 仓库的代码修复 Agent。它和普通聊天机器人最大的区别是把“模型输出”当作待验证输入：模型只能通过白名单工具读取代码和提交 unified diff，工具层会做参数、路径和文件版本校验，Agent 在独立工作区里运行。候选补丁生成后，系统从固定基线重新创建工作区，在 Docker 中运行公开测试、复现测试和维护者验收，并把补丁摘要、工作区摘要、日志、transcript 和测试报告绑定起来。除此之外，我实现了一个本地持续对话模式，支持会话恢复和显式目标续跑；服务模式使用 PostgreSQL 和独立 Worker 处理任务队列、租约和 checkpoint。这个项目的重点不是宣称模型已经解决所有代码问题，而是让每次动作、失败和最终结论都可控、可解释、可复现。

---

## 七、常见追问与回答方向

### 为什么不用多 Agent？

当前问题首先是单个修复循环能否在有限预算下得到可信补丁。多 Agent 会增加消息协议、共享工作区冲突、成本、归因和恢复复杂度；独立验收也不能被多一个模型替代。只有真实任务显示出可分解子问题和可量化收益时，才值得引入规划者或审查者。

### 为什么不用向量数据库？

固定任务快照规模较小，目录枚举、按行读取和精确文本搜索已经足够。向量库会引入切块、索引版本、召回评测和运维成本，而且相似度检索不能替代精确符号和错误文本搜索。

### `environment_id` 能保证安全执行吗？

不能。它是任务配置中的登记字段。真正的执行策略在 `DockerRunner`，包括镜像、网络、资源和命令限制；任务格式校验和沙箱运行是两层不同的责任。

### 为什么既要文件摘要，又要 workspace hash？

聊天模式的 `LocalTools` 在 `write_file` / `edit_file` 中使用文件 `sha256`，防止本地文件被外部修改后覆盖；固定任务模式的 `ToolDispatcher` 在 `apply_patch` 中使用整份工作区的 `expected_hash`，描述补丁所依据的工作区版本。两者分别解决文件级和工作区级的版本一致性。

### 为什么不能直接复用 Agent 当前工作区验收？

因为模型或工具可能改变了不应进入评分的文件，也可能留下未记录的状态。验证器只接收封存补丁，从原始快照重建环境，才能把“候选内容”和“验收环境”分开。

### 服务能保证 exactly-once 吗？

不能这样承诺。租约和 fencing 可以阻止失去所有权的 Worker 发布迟到状态，但不能撤销已发出的模型调用，也不能让文件系统和数据库成为一个分布式事务。项目目标是防止错误状态写回，并让 checkpoint 后的恢复可控。

### 当前项目的限制是什么？

- `agent-run` 是一次有预算的修复循环，不等于无限持续的通用编程助手；持续对话由独立的 `chat` 模式提供。
- 没有真实批量 A/B 结果时，不能声称 Agent 胜过 baseline。
- 沙箱面向可信任务，不是对恶意 Python 或公网多租户的形式化安全保证。
- 本地服务是 PostgreSQL-backed 的任务 API 和 Worker，不是已经具备公网部署、多人隔离和自动运维的平台。

主动讲出这些限制，通常比把原型包装成“全自动软件工程师”更可信。

---

## 八、建议的实操学习顺序

在本地可以按这个顺序验证自己的理解：

```powershell
uv sync --locked --link-mode=copy
uv run --locked patchpilot --help

# 只校验一个固定任务，不调用模型
uv run --locked patchpilot validate-task benchmarks/tasks/pagination-001/task.json

# 查看面向模型的公开输入
uv run --locked patchpilot validate-task benchmarks/tasks/pagination-001/task.json --agent-input

# 先读核心单元测试
uv run --locked pytest tests/test_task.py tests/test_snapshot.py tests/test_tools.py
uv run --locked pytest tests/test_agent.py tests/test_verification.py

# 需要真实 Docker 时再运行集成测试
uv run --locked pytest --run-docker

# 本地持续对话（需要显式配置模型并授权）
uv run --locked patchpilot chat --repo D:\code\My-agent --allow-api
```

每读完一层，都尝试用一句话回答：

1. **谁决定下一步？** 模型、工具运行时、Docker 执行器还是独立验证器？
2. **什么时候生效？** 请求前、工具分发时、补丁发布时还是最终验收时？
3. **状态怎么流动？** 消息、工具结果、工作区摘要、事件、checkpoint 和最终报告分别保存在哪里？

如果这三个问题能沿着 `cli -> task -> workspace -> agent -> tools -> patching -> sandbox -> verification` 讲通，项目主体就已经掌握了；服务和评测再作为可靠性与实验设计的加分项展开。


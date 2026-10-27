# PatchPilot

一个包含本地仓库交互开发与受控 Python 修复评测的 Coding Agent 项目。`patchpilot chat` 提供持续对话、本地文件工具、可选宿主命令、会话恢复和 `/goal` 目标循环；原有任务模式提供受控修复与独立验收、PostgreSQL任务服务，以及20项自建任务、单次生成基线、批量评测和无模型密钥CI配置。**本地检查与真实模型成绩需要区分；没有真实批量A/B成绩，不能声称Agent优于基线。** 没有复杂前端、多Agent、Redis或向量库。

## 本地仓库持续对话

下面启动的是**常驻交互终端**，无需 PostgreSQL、Docker 或 `task.json`，直接读写指定目录。先完成下面的安装步骤，再配置模型：

```powershell
$env:ANTHROPIC_API_KEY = "你的API密钥"
# 使用兼容服务时设置；不设置则使用官方地址。
# $env:ANTHROPIC_BASE_URL = "https://你的服务地址"
uv run --locked patchpilot chat --repo D:\code\My-agent --allow-api --allow-commands
```

模型名通过 `--model` 指定；`--evaluator-model` 默认与工作模型相同。沿用项目 Anthropic SDK 适配器，服务需要支持流式 Messages API、工具调用及工作模型的 adaptive thinking。启动时会显示会话编号和记录目录。

也可复制 [.env.example](.env.example) 为 `.env` 并填写，通过 `--env-file .env` **显式加载**；已有进程环境变量优先。支持单行 `KEY=VALUE`、单/双引号和 `export` 前缀，不执行脚本、不展开变量、不支持多行值。不会自动读取目标仓库的 `.env`。

交互示例：

```text
你 > 先解释这个项目的启动流程，不要改代码
你 > 刚才提到的入口函数在哪里？
你 > 修改这个函数，保留已有其他改动
你 > /goal 运行指定测试并修复失败，直到测试成功
你 > /goal
你 > /goal pause
你 > /goal resume
你 > /goal clear
你 > /diff
你 > /status
你 > /new
你 > /exit
```

普通回答后等待下一条输入；只有 `/goal <条件>` 启用自动续跑。评估器使用独立、无工具的调用检查对话证据，未完成时反馈缺失项；无法验证、需要澄清、评估错误、预算耗尽或中断时暂停，保留目标，由 `/goal resume` 显式继续。目标完成不是原预置任务的独立验收结论。

默认每次用户提交最多 24 次模型调用、64 次工具调用、600 秒、100000 个已知 token、120000 字符上下文，自动续跑最多 8 次；工作模型与评估器共用预算、分别记录用量。可以用 `--max-model-calls`、`--max-tool-calls`、`--timeout`、`--max-tokens`、`--max-output-tokens`、`--max-context-chars`、`--max-continuations` 调整。已知用量阈值不是费用硬上限，请求失败也可能计费。上下文满后使用 `/new` 开新会话，文件改动保留。

恢复会话（默认记录目录相对**启动命令时的目录**）：

```powershell
uv run --locked patchpilot chat --resume <会话编号> --allow-api --allow-commands
# 从其他目录启动时，显式提供原记录目录：
uv run --locked patchpilot chat --resume <会话编号> --sessions-dir D:\code\My-agent\.patchpilot-sessions --allow-api
```

恢复历史、目标、累计用量和文件修改前内容；目标默认暂停，旧命令不会重放，文件修改前必须重新读取。可同时传 `--repo` 核对记录路径，路径不同会拒绝恢复。同一会话不能由两个进程同时打开；进程退出后系统释放锁。状态采用临时文件替换保存，事件在同目录的 `events.jsonl`。会话中可能包含源码、命令与输出，请按本地开发记录保存。

`Ctrl+C` 在执行中取消当前请求或命令进程树，然后回到提示；等待输入时保存退出。中断不会回滚已发生的文件修改，未取得结果的工具会被标记为结果未知，恢复后不会自动重试。

**本地模式的操作范围：** 文件工具支持仓库内普通 UTF-8 文本，单文件最多 256 KiB；跳过凭据、隐藏目录（`.github` 除外）、依赖、缓存和会话产物，拒绝越界和链接，按读取摘要防止覆盖外部修改。`/diff` 仅记录本会话文件工具修改前后的差异；命令或编辑器产生的变化需要自行检查，例如在 Git 仓库运行 `git diff`。现有未提交修改不会被自动重置或提交。

`--allow-commands` 授权本次启动自动执行宿主命令，权限与当前用户相同，**不受文件工具目录边界隔离**。Windows 优先使用可启动的 PowerShell 7，否则使用 Windows PowerShell，均带 `-NoProfile`；Unix 使用 `sh`。命令默认最多 120 秒，受剩余轮次时限约束，输出仅保留末尾 16000 字节；超时没有成功退出码。省略该标志即可只用文件工具。原 `agent-run` 和基准验收仍使用固定任务与 Docker，规则不会被聊天模式扩大。

## 1. 安装与启动

需要 uv 和 Git。项目使用 Python 3.12；uv 会管理项目解释器，无需替换已有 Python。下面的命令在项目根目录执行，适用于 PowerShell。

```powershell
uv python install 3.12
uv sync --locked --link-mode=copy
uv run --locked patchpilot --help
```

`--link-mode=copy` 适合 Windows 中 uv 缓存与项目位于不同磁盘的情况。命令统一使用 `uv run`，不用依赖可能指向 Windows 商店别名的裸 `python`。

校验内置任务：

```powershell
uv run --locked patchpilot validate-task benchmarks/tasks/pagination-001/task.json
```

只查看未来可以交给 Agent 的公开输入：

```powershell
uv run --locked patchpilot validate-task benchmarks/tasks/pagination-001/task.json --agent-input
```

校验成功退出码为 `0`；任务无效时退出码为 `2`，错误写入 stderr。`--agent-input` 成功时 stdout 只有 JSON，不混入状态提示。

## 2. 运行检查

```powershell
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
```

根 pytest 只收集 `tests/`。不要把 `pytest` 的收集范围改成整个仓库：内置缺陷样例中有一个**应该失败**的复现测试。

单独验证缺陷与参考补丁：

```powershell
uv run --locked pytest tests/test_reference.py -v
```

这两项项目测试会自动完成以下检查：

1. 把缺陷快照复制到临时目录；所有样例测试只在副本中执行。
2. 缺陷版应为 **6 项通过、1 项失败**，失败必须是指定的复现断言，而不能是导入、收集或环境错误。
3. 使用 Git 在另一份副本应用参考补丁；不需要初始化 Git 仓库，也不提交代码。
4. 相同的 7 项样例测试全部通过，并确认只改变了目标源码。
5. 确认原始快照内容没有改变。

因此，**项目自身全绿与样例故意失败可以同时成立**。没有符号链接创建权限的系统会跳过对应测试并给出原因；Windows 目录联接有独立测试，不会被当成普通目录接受。

## 3. 文件布局

```text
pyproject.toml                         项目元数据、依赖、CLI 入口与检查配置
uv.lock                                uv 生成的依赖锁文件
.python-version                        项目解释器版本选择
src/patchpilot/
  __init__.py                          Python 包
  __main__.py                          python -m patchpilot 入口
  cli.py                               命令行参数、输出和退出码
  chat_cli.py                          本地交互入口、显式环境文件加载
  session.py                           多轮对话、共享预算与中断处理
  goal.py                              可选目标状态和独立证据评估
  local_tools.py                       本地仓库读写、搜索、差异和宿主命令
  session_store.py                     会话独占锁、保存与恢复
  process_job.py                       Windows 命令子进程回收
  snapshot.py                          固定快照的内容摘要
  task.py                              任务模型、加载与公开输入构造
  workspace.py                         独立工作区创建
  sandbox.py                           Docker 生命周期、资源限制与结果分类
  patching.py                          受控补丁应用与差异导出
  verification.py                      从基线重建的独立验收
  _acceptance/pagination_001/           维护者验收，不属于公开快照
sandbox/                               固定镜像 Dockerfile 与依赖锁
tests/                                 项目自身的自动测试
benchmarks/
  tasks/pagination-001/
    task.json                          任务描述，不包含参考答案
    Issue.md                           公开问题与行为约定
    repo/                              唯一允许作为任务源码快照的目录
      pytest.ini                       样例测试配置
      src/pagination_demo/paginator.py 分页组件缺陷版本
      tests/                           原有正常测试
      repro/                           公开复现测试
  solutions/pagination-001/
    reference.patch                    维护者参考补丁，不属于源码快照
docs/phase1-progress.md                 实施与验证记录
```

参考补丁在 `repo/` 之外。未来建立 Agent 工作区时，只能复制任务的 `repo/`，并额外传入公开任务视图；不能挂载整个项目或 `benchmarks/` 父目录。

`solutions` 只是职责分区，不是文件系统权限隔离；阶段二通过独立工作区复制边界与无宿主挂载的容器限制访问，仍不等于对敌对代码的强隔离。

## 4. 第一项缺陷：相同时间戳跨页漏项

样例中的记录按 `(created_at, id)` 升序排序，游标保存上一页最后一条记录的位置。

记录 `(id=1, created_at=100)` 与 `(id=2, created_at=100)` 在排序中是不同位置。缺陷代码在翻页时却只比较时间戳，导致返回编号 1 后，编号 2 被跳过。

参考修复使用完整的二元组位置比较。Python 元组采用字典序比较：先比较时间戳，相同时继续比较编号。因此同一时间戳下的剩余记录仍能返回。

这是维护者解释；公开 Issue 只给出 API 行为、症状和复现，不包含参考补丁。

**不要直接在原始 `repo/` 中运行测试或导入样例模块**：测试缓存、字节码和其他新文件也会改变快照摘要。使用上面的参考补丁验证测试，它会在临时副本操作。

## 5. 任务描述与校验

首版是固定格式的预置任务，不是任意命令执行配置。

| 字段 | 首版约定 |
| --- | --- |
| `task_id` | 小写字母、数字和连字符组成的任务编号 |
| `snapshot_path` | 固定为任务包中的 `repo` |
| `snapshot_sha256` | 64 位小写十六进制 SHA-256，必须匹配真实内容 |
| `issue_path` | 固定为任务包中的 `Issue.md` |
| `environment_id` | 仅登记 `python-pytest-v1` |
| `writable_dirs` | 固定为 `["src"]`，不能扩展到测试或配置 |
| `test_profile` | 仅登记 `pytest-visible-v1` |

可见测试配置约定为在快照副本中使用 `pytest.ini` 执行 `tests` 和 `repro`。阶段二将 `python-pytest-v1` 对应到预构建镜像，执行器记录实际镜像 ID；标识本身不是权限授权。

加载器会拒绝未知字段、缺失字段、错误类型、不支持的配置、绝对路径、父目录跳转、反斜杠路径、链接、缺失布局、空 Issue 和摘要不匹配。首版使用固定路径值来缩小输入面，后续有真实需要再扩展。

配置校验不等于权限授予。未来真正允许什么操作，仍由运行时与工具策略决定，不能由任务文件自行声明。

### 快照摘要如何计算

1. 遍历 `repo/` 下所有普通文件，包括测试、配置和隐藏文件；拒绝符号链接与 Windows 目录联接。
2. 每个文件记录为 `[POSIX 相对路径, 原始字节的 SHA-256]`。
3. 按相对路径排序，将记录序列化为紧凑 UTF-8 JSON，保留非 ASCII 字符、不加尾换行。
4. 对这段 JSON 再计算 SHA-256。

修改时间和创建顺序不影响摘要；文件改名、内容变化、增加文件都会影响。空目录不计入摘要。`task.json` 位于快照之外，避免“摘要包含自身”造成循环。

`.gitattributes` 约定文本使用 LF，避免日后 Git 检出时因换行转换改变字节。当前不自动初始化 Git。

如果维护者有意修改了快照，必须重新验证缺陷与参考补丁，再显式更新任务中的摘要；**加载器不会自动接受变化或重写摘要**。

### 内部状态与 Agent 输入为什么分开

- `TaskSpec`：严格校验任务元数据。
- `LoadedTask`：包含已核验的描述、宿主快照路径和 Issue 内容，供内部程序使用。
- `AgentTaskInput`：显式白名单构造的公开数据，只含任务号、相对快照标识、摘要、Issue、环境标识、可写范围和测试配置标识。

不要直接序列化 `LoadedTask`。未知的 `reference_patch`、`hidden_tests`、`command` 字段会被拒绝，公开输入不携带内部绝对路径或维护者文件。

这不能自动判断 Issue 正文是否人为写入了答案或敏感信息；任务作者仍需审核正文。快照 hash 也不是数字签名：它能发现与登记值不一致，不能证明任务来源可信。

## 6. 验证干净环境安装

下面创建另一份虚拟环境，以非 editable 方式安装，而不是复用当前 `.venv`。原环境不删除、不覆盖。

```powershell
$previous = $env:UV_PROJECT_ENVIRONMENT
$env:UV_PROJECT_ENVIRONMENT = Join-Path $env:TEMP ("patchpilot-check-" + [guid]::NewGuid())
try {
    uv sync --locked --no-editable --link-mode=copy
    if ($LASTEXITCODE -ne 0) { throw "安装失败" }
    uv run --locked --no-editable patchpilot --help
    if ($LASTEXITCODE -ne 0) { throw "入口验证失败" }
    uv run --locked --no-editable pytest
    if ($LASTEXITCODE -ne 0) { throw "测试失败" }
} finally {
    $env:UV_PROJECT_ENVIRONMENT = $previous
}
```

依赖版本由 `uv.lock` 锁定；构建后端在 `pyproject.toml` 中固定。这里验证的是可安装和可复现运行，不承诺构建产物逐字节完全相同。

## 7. 建议阅读顺序

1. `pyproject.toml` → `__main__.py` → `cli.py`：理解目录名、发行包名、导入包和命令入口的区别。
2. `Issue.md` → 复现测试 → 缺陷代码 → 参考补丁：自己解释漏项原因。
3. `snapshot.py` → 对应测试：理解“固定版本”不只是给目录起名字。
4. `task.py` → `test_task.py`：理解输入校验、文件事实核验与公开视图。
5. `test_reference.py`：理解为什么“程序成功运行”“测试确实执行”“缺陷真正修复”是不同判断。

## 8. 阶段二：固定镜像、工作区与独立验收

### 8.1 预先构建测试镜像

Windows 需要 Docker Desktop 的 WSL2 Linux 容器引擎。先启动 Docker Desktop，再在项目根目录执行：

```powershell
docker info --format '{{.OSType}}'
docker build --tag patchpilot-tests:py312-v1 sandbox
uv run --locked patchpilot sandbox-check
```

第一个命令应输出 `linux`；`sandbox-check` 确认引擎与本地镜像，输出不可变的镜像 ID。它只是前置检查，实际容器执行由后续验收完成。

镜像使用 digest 固定的 Python 3.12.8 基础镜像；`sandbox/requirements.lock` 用精确版本与文件哈希固定 pytest 8.3.5 和传递依赖。这个镜像与宿主 uv 开发环境互相独立。升级镜像应显式更新 digest、依赖锁、策略测试和环境标识，而不是偷偷使用浮动 latest。固定版本不代表永远安全，后续维护仍需安全更新。

网络仅在预构建镜像时需要。执行器运行时使用本地镜像 ID 和 `--pull never`，不会临时下载依赖，也不会在 Docker 不可用时回退到宿主执行。

### 8.2 验收参考补丁

```powershell
uv run --locked patchpilot verify-patch benchmarks/tasks/pagination-001/task.json benchmarks/solutions/pagination-001/reference.patch
```

成功时输出 JSON，`status` 为 `passed`，原有正常测试、公开复现与维护者验收用例共 **14 项通过**。

输出目录默认为 `.patchpilot-runs/`，可以指定：

```powershell
uv run --locked patchpilot verify-patch benchmarks/tasks/pagination-001/task.json benchmarks/solutions/pagination-001/reference.patch --runs-dir .patchpilot-runs --timeout 10
```

验证空补丁不能冒充修复：

```powershell
$empty = New-TemporaryFile
uv run --locked patchpilot verify-patch benchmarks/tasks/pagination-001/task.json $empty.FullName
```

这条命令预期退出码为 **1**，不是环境出错，也不是修复通过。

退出码约定：

- `0`：补丁通过独立验收。
- `1`：补丁被拒绝、断言失败、测试收集异常、没有实际执行测试或超时。
- `2`：配置、文件读取、Docker 或其他基础设施异常。

### 8.3 独立工作区与执行产物

一次验收的主要文件：

```text
.patchpilot-runs/run-<唯一编号>/
  repo/                         只从核验过的原始 repo 复制
  artifacts/
    candidate.patch             本次接收的原始补丁字节
    diff.patch                  相对原始快照导出的可重放差异
    agent-input.json            面向未来 Agent 的公开任务视图
    verification.json           绑定版本的独立验收报告
    test-<唯一编号>/
      container.json            实际容器配置
      container.log             有界日志，保留 stdout 和 stderr
      report.xml                成功取得的 JUnit 报告；超时等情况下可能不存在
      result.json               结构化测试结果
```

非法补丁会在执行前拒绝，因此其产物中可能没有 diff 或测试目录。

工作区每次重新建立，只复制 `repo/`，不会复制参考补丁、父目录、其他任务或维护者验收文件。容器又通过 `docker cp` 接收一份文件副本，**不挂载宿主工作区**；测试修改容器中的文件不会写回宿主快照。

维护者验收位于安装包的 `src/patchpilot/_acceptance/pagination_001/`，只在独立验收容器中作为 `/acceptance` 额外传入，不属于普通工作区 `repo/`。公开任务 JSON 不能指定或替换验收配置。它们属于开源维护者材料，不是对能阅读整个 GitHub 仓库的人保密；未来 Agent 的工具仍必须限定在公开工作区。

报告记录原补丁 SHA-256、原始快照 SHA-256、候选代码 SHA-256、验收用例 SHA-256 和实际镜像 ID。结论针对这一次输入，不会因为模型或用户说“已修好”就自动通过。

### 8.4 测试分类不是只看退出码

`src/patchpilot/sandbox.py` 联合 pytest 退出码与有界 JUnit 报告进行分类：

| 状态 | 含义 |
| --- | --- |
| `passed` | 退出码成功，至少一项实际通过，且没有失败或错误 |
| `failed` | 测试实际执行，存在断言失败 |
| `test_error` | 收集、用例执行异常或 pytest 使用错误 |
| `no_tests` | 未收集到测试，或所有测试都被跳过 |
| `timeout` | 超过测试执行时限，已终止容器 |
| `infra_error` | Docker、报告读取／解析、异常退出或清理失败等执行异常 |

没有报告时计数为 `null`，不是编造的零；退出码和报告相互矛盾时不声明成功。运行结果包含耗时、日志路径、容器 ID 和 `cleanup_ok`。

### 8.5 资源与权限限制

默认策略：非 root 用户 `10001:10001`、无网络、1 CPU、256 MiB 内存、64 个进程、64 MiB `/tmp`、单文件 16 MiB、轮转日志上限约 1 MiB、丢弃全部 capabilities、禁止提权。不传入宿主环境变量、不挂载 Docker socket 或宿主目录。默认测试时限为 30 秒，可由维护者通过 CLI 调整为有限正数。

容器仍有可写的自身文件层，尚未设置容器总磁盘配额。测试超时会 kill 整个容器，再在 finally 中强制删除它和匿名卷；若清理失败，报告会明确给出异常和容器名称，而不是声明已清理。

超时参数针对测试执行阶段；前置 Docker 检查、复制、日志收集与清理各自还有有界命令超时，因此总耗时可以长于测试时限。若宿主程序被强制结束或 Docker daemon 自身退出，finally 无法提供绝对保证，可检查本项目标记的容器：

```powershell
docker ps -a --filter label=patchpilot.managed=true
```

不要为了清理本项目去执行全局容器 prune，以免影响其他项目。

### 8.6 补丁支持范围

阶段二基线只接受普通 UTF-8 Git unified diff，修改 `src/` 内**已存在**的 `.py` 文件，路径限不含空格的常规 ASCII 名称。阶段三新增的唯一例外为 `regression/test_*.py`，详见第9节；其余新建、删除、重命名、文件模式变化、二进制补丁及测试／配置修改仍拒绝。

应用前校验工作区摘要，防止基于陈旧内容覆盖；然后复制到临时 staging，调用 Git 校验与应用完整补丁，并核对实际文件变化。格式或 Git 校验阶段失败不会修改原工作树，全部检查通过后才发布；发布使用两次目录 rename，异常时尝试回滚，不承诺强杀、磁盘故障下的崩溃原子性。补丁上下文或位置不符时拒绝，不接受偏移应用。当前只支持单用户串行操作，没有跨进程并发事务。

`export_diff` 会相对核验过的原始快照导出补丁。`verify_patch` 不接收候选工作区，只从原始快照创建新工作区、应用封存补丁并独立测试。

### 8.7 运行真正的 Docker 集成测试

```powershell
uv run --locked pytest --run-docker
uv run --locked ruff check .
uv run --locked ruff format --check .
```

默认 `pytest` 跳过 Docker 集成用例；显式传入 `--run-docker` 后，Docker 或镜像缺失会导致真实用例失败，不能用跳过冒充验收。Windows 无符号链接创建权限时，原有 3 项符号链接测试仍会注明原因跳过；Windows 目录联接另有真实测试。

集成用例实际覆盖：容器内 Python/pytest 版本与资源策略、宿主秘密不传入、正常通过、断言失败、收集错误、无测试、全部跳过、stderr 保留、无限循环携带子进程、超时清理、正确补丁与空补丁、CLI 输出。

学习顺序建议：`workspace.py` → `sandbox.py` 的结果分类 → 容器生命周期 → `patching.py` → `verification.py` → CLI。先运行对应测试，再解释每项失败应该属于哪个层次。

## 9. 阶段三：工具、循环与模型接入

### 9.1 权限边界与工具契约

`tools.py` 提供七个工具：

| 工具 | 能力与限制 |
| --- | --- |
| `list_files` | 列出公开工作区相对路径，可以限制目录和数量 |
| `read_file` | 按1起始闭区间行号读取UTF-8文本，最多100行，返回工作区摘要 |
| `search_text` | 精确、区分大小写的文本搜索，返回路径、行号、内容；不提供正则或向量检索 |
| `apply_patch` | 传入普通Git diff及最近观察的工作区摘要，冲突拒绝，不静默覆盖 |
| `add_regression_test` | 只新增 `regression/test_*.py`，不能覆盖已有文件 |
| `get_diff` | 查看相对于原始快照的累积补丁与当前摘要 |
| `run_tests` | 运行公开目标，不接受任意命令或pytest参数 |

原始 `tests`、`repro`、测试配置和依赖声明都不可修改。阶段三对阶段二的唯一新建文件例外是 `regression/test_*.py`；源码仍只能修改已有 `src/**/*.py`。

测试目标为 `visible`（原tests＋repro＋已新增regression）、`original`、`repro`、`regression`，或公开测试文件相对路径。不接受 shell、绝对路径、`/acceptance` 或 `::` 形式的任意选择表达式。

所有工具参数由严格Pydantic模型校验，未知字段拒绝。结果统一包含 `call_id`、`name`、`ok`、`data`、`error`、`truncated`。文件不存在是结构化 `not_found`，越界与链接是 `path_denied`，而不是整个任务崩溃。工具输出仅作为数据返回，不能修改权限策略。

单文件读取上限256KiB、每行摘要最多1000字符、结果约16000字符上限；超限会明确标记。`run_tests` 只给模型结构化计数与必要日志摘要，完整的**有界**容器日志和报告保存在宿主artifacts；Docker日志本身有轮转上限，并非无限保存全部输出。模型文件工具无法读取artifacts。

### 9.2 Agent 循环与停止

`agent.py` 是一个显式单决策者循环：构造公开任务 → 模型完整响应 → 检查停止标记与工具参数 → 调用工具 → 把观察放入下一轮。没有预设某个任务的修复逻辑。

- 完整保留模型content块，包含SDK要求回传的签名等字段，不只保存text。
- 同一轮所有工具结果合为一个user消息，使用对应的tool_use_id。
- `max_tokens`、拒绝、协议矛盾等状态下不执行本轮工具。
- 模型 `end_turn` 仅表示候选结束：运行时另建工作区调用独立验证器。
- 独立验收结果不回流给模型反复试答案；模型新增回归测试也不作为可信验收的替代。

默认预算：8次模型调用、32次工具调用、180秒、每次最多4096输出token、累计已知100000token停止阈值、120000字符上下文上限、同一版本相同行动重复3次停止。每次动作启动前检查预算；未执行的同轮工具仍留下匹配编号的停止结果。

历史采用append-only，不静默剪掉工具配对；上下文超过上限就停止并封存补丁，而不是假装仍记得完整历史。进入独立验收前预算耗尽时状态为 `unverified`，保存当前产物，不自动启动验收；已进入独立验收后耗尽剩余测试时间则可能得到 `timeout`，不是所有时限路径都返回同一状态。

**注意：token阈值不是账单硬上限。** 本轮实际输入和输出用量只能在响应后知道，进行中的请求可能超过阈值；请求失败时也可能已计费。`calls_without_usage` 明确记录无法取得用量的请求，usage字段为0不能被解释为没有费用。总时限按动作检查并传给网络与容器；清理有独立的有界宽限，磁盘操作和宿主被强杀不属于硬实时保证。

### 9.3 官方 SDK 与授权门

只接入 Anthropic 官方Python SDK，默认模型 `claude-opus-5-5`，不做多模型路由。适配器内部使用异步SDK与 `asyncio.timeout` 约束完整流的绝对时限，对同步CLI提供同步方法；在同一事件循环内复用客户端并显式关闭。使用流式响应、完整读取后才处理工具；严格检查原始工具JSON片段，防止容忍式解析误接受坏JSON。自定义代理不发送可能不兼容的eager流字段。SDK隐式重试为0；超时、限流、鉴权、拒绝、截断及流内API错误都会安全停止并留证，不偷偷换模型。

查看命令不会发起模型请求：

```powershell
uv run --locked patchpilot agent-run --help
```

下面这条**会发送公开任务内容并产生费用**，必须在你确认认证、目标服务和预算后主动执行：

```powershell
uv run --locked patchpilot agent-run benchmarks/tasks/pagination-001/task.json --allow-api --max-model-calls 6 --max-output-tokens 4096 --timeout 180
```

不加 `--allow-api` 会拒绝运行。SDK从本机已配置的环境认证／官方认证配置中解析凭据；不要把密钥写入Issue、task.json、代码或命令行参数。容器不接收宿主模型凭据。API地址由本机配置决定，不应将“使用官方SDK”误解为已验证实际服务后端来源。

Agent退出码：独立验收通过为0，未验证、预算停止或验收未通过为1，CLI配置/前置环境错误为2。执行时进度事件写入stderr并实时刷新，stdout仅输出最终JSON，方便重定向保存报告。

### 9.4 执行记录

Agent工作区的 `artifacts/` 包含：

- `agent-result.json`：执行状态、停止原因、模型/工具次数、已知用量与独立验收引用。
- `candidate.patch`：相对于固定基线的候选差异。
- `transcript.json`：公开任务、完整响应content及配对的工具观察。
- `events.jsonl`：按序记录模型请求、工具动作、验收和停止事件。
- `tools/`：工具结果证据；公开工具本身无权读取这个目录。

独立验证会另建一个工作区，沿用阶段二的补丁、源码和镜像绑定报告。直接使用 `agent-run` 时仍是本地文件记录；阶段四任务服务另外提供租约和已确认代码状态的检查点恢复，但不承诺任意时刻恢复原模型会话或exactly-once。

### 9.5 五个可信任务与真实结果的区别

| 任务 | 缺陷 | 正常／公开复现／维护者验收用例数 |
| --- | --- | --- |
| `pagination-001` | 相同时间戳跨页漏项 | 6／1／7 |
| `boundary-001` | 半开区间错误包含终点 | 6／1／7 |
| `null-001` | 空字符串被错误当作None回退 | 8／1／8 |
| `validation-001` | bool被误认为合法整数数量 | 13／2／12 |
| `state-001` | 成功更新后未递增版本 | 9／1／9 |

每项都先在临时副本证明缺陷版断言失败，再证明参考补丁通过正常、复现和额外验收，且原快照不变。`tests/test_five_task_agent.py` 用同一执行循环运行5项，但测试替身**显式提供参考补丁**，因此只能证明链路与隔离，不是模型修复率。

一次经过用户授权的真实分页尝试：第1次请求遇到 `provider_error`，0次工具执行、1次无usage请求，结论为 `unverified`，未启动独立验收。没有自动重试或切模型，也没有将失败改写成成功。报告在 `.patchpilot-runs/run-wdr2p9fm/artifacts/agent-result.json`（本地运行产物，不随包分发）。目前不能宣称真实模型已修复任何任务；具体服务状态需要另行授权诊断。

### 9.6 分层学习与验证

```powershell
uv run --locked pytest tests/test_tools.py
uv run --locked pytest tests/test_agent.py
uv run --locked pytest tests/test_anthropic_model.py
uv run --locked pytest tests/test_five_task_agent.py --run-docker
uv run --locked pytest --run-docker
```

测试不会自动联网调用模型。`FakeModel`只存在于tests；SDK替身测试用真实SDK数据类型验证协议和错误分类，也不产生模型费用。

推荐先阅读 `tools.py` 的参数契约与调度 → `agent.py` 的一次响应/工具结果配对 → 预算停止测试 → `anthropic_model.py` → 五任务测试。对照解释：为什么测试工具返回的失败不一定是工具执行异常，以及为什么模型说完成不等于修复通过。

## 10. 阶段四：可靠任务后端

完整启动、API、SSE、取消与恢复说明见 [阶段四服务指南](docs/stage4-service.md)。

- PostgreSQL保存执行、Attempt、步骤、事件、产物引用和检查点；大日志仍放文件。
- API只排队和查询；Worker作为独立进程执行，不在HTTP后台任务中假装可靠队列。
- 原子领取、DB时钟租约和fencing阻止旧Worker覆盖新结果；取消与最终报告原子提交。
- SSE从持久化序号续读，断开连接不取消任务。
- 安全重启只恢复已确认代码状态，重建全新workspace，不盲目重放旧工具。
- 诊断模式不调用模型，不能当作AI修复成绩。真实Agent Worker须显式`--allow-api`。

```powershell
docker compose up -d --wait postgres
# 先按服务指南设置本机API令牌和数据库环境变量。
uv run --locked patchpilot-service migrate
uv run --locked patchpilot-service serve
# 另一个配置好相同环境的终端：
uv run --locked patchpilot-service worker --diagnostic
```

完整验证：`uv run --locked pytest --run-postgres --run-docker`。测试不自动发起付费模型请求。Postgres测试schema按权限要求保留，不自动删除；租约回收器默认也不批量删除容器，强杀后的孤立容器需核对具体ID再授权处理。

## 11. 阶段五：可信评测与自动检查

详细方法、成本口径、失败分类与付费门见 [评测指南](docs/evaluation.md)；完整候选Issue与评分规则见 [输入审阅材料](benchmarks/REVIEW.md)。

- 20项AI协助自建合成任务，12开发/8保留，18个关联group不跨拆分；基准资料单独MIT许可，不冒充真实生产Issue。
- baseline固定公开检索、一次生成；Agent用现有反馈循环。两者共享模型、冻结镜像/任务/验收和预算上限，实际调用与成本分别记录。
- 主分母包括环境、服务失败与未完成尝试；重复运行和配对结果均保留，不挑最好一次。
- 无显式定价或缺usage时成本未知；失败花费计入成功摊销成本。
- 普通CI无需模型密钥，真实评测必须`--allow-api`＋`--approve-dataset HASH`，保留集另需确认。

```powershell
uv run --locked patchpilot-eval list
uv run --locked patchpilot-eval validate
uv run --locked patchpilot-eval report <实际批次目录>
```

本阶段固定容器验证了20/20参考任务材料，不是模型20/20修复率。GitHub Actions配置已提供但尚未在远程GitHub实跑；当前数据集仍待人工审阅，没有真实付费对照结论。

## 12. 当前边界

本项目仅面向自行编写、维护者审核的可信任务。测试本身仍能执行任意 Python；当前没有防止恶意源码篡改 pytest、伪造 JUnit 或读取同一验收进程内材料的强隔离。独立验收能提高可信任务的质量，不等于对敌对代码的形式化正确性证明。不要开放公网任意仓库上传或将其用于恶意多租户执行服务。

没有初始化或提交 Git；公开发布前还需明确许可证和发布范围。

## 13. 架构理解与独立复现

按目的阅读，而不是一次读完所有历史阶段文档：

- **理解设计：** [架构、取舍与边界](docs/architecture.md)，回答为什么选这个范围、模型与运行时如何分工、为什么不先堆框架，以及验收到底能证明什么。
- **自己跑通：** [干净环境复现](docs/reproduction.md)，独立源码副本＋全新非editable环境，不需要模型密钥；脚本替身完整链路和参考补丁验收明确不是模型成绩。
- **准备讲解：** [一次工具请求的追踪与自测](docs/execution-walkthrough.md)，用证据填写执行卡，再关掉文档复述；不是可直接背诵的生成答案。
- **检查发布范围：** [材料检查与待办](docs/release-readiness.md)，区分扫描结果、人工复核和全局许可证缺口，不直接上传本机运行目录。
- **查本次证据：** [阶段六进度](docs/phase6-progress.md)。第35步演示由用户自行完成，真实模型调用不在本轮授权范围内。

全局应用许可证尚未选择；基准资料的MIT不自动覆盖整个项目。文档和自动测试不能代替用户本人完成不看答案的讲解验收。

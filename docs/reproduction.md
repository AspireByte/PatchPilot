# 从干净环境复现

## 1. 这里的“干净”具体指什么

本轮复现使用独立源码副本与全新虚拟环境，非 editable 安装后确认从该环境的 `site-packages` 加载；不复制当前 `.venv`、运行产物、凭据或缓存目录。

复用本机 uv 下载缓存、Git 和已构建的固定 Docker 镜像。**这不是全新操作系统、空 Docker 缓存或从零网络下载的验证，也不是逐字节可重现构建承诺。** 普通检查不调用真实模型，下面的完整 Agent 测试用明确的脚本替身。

准备条件：

- Windows PowerShell、uv、Git；宿主 Python 由 uv 管理为 3.12。
- Docker Desktop 的 Linux 容器引擎，仅在容器验证时需要。
- 首次安装依赖和构建镜像需要网络；工具运行时测试容器不联网。
- 本文不要求 PostgreSQL，不会因复现额外创建数据库测试 schema。

本轮具体命令与结果见[实施证据](phase6-progress.md)。若条件不满足，应保留失败记录，不跳过真实容器检查却声称全链路通过。

## 2. 取得独立源码副本

当前尚无远程仓库，不提供虚构的 `git clone` 地址。可从实际取得的源码归档解压到新空目录，或按[候选发布白名单](release-readiness.md)复制现有项目。

副本应含 `src/`、`tests/`、`sandbox/`、`benchmarks/`、`docs/`、`.github/` 以及根配置和锁文件；不能携带 `.venv/`、`.patchpilot-*/`、`.env`、`dist/`、`__pycache__/` 或本机认证文件。不要复制整个用户目录或把所有未跟踪文件打包。

从副本根目录在**新终端**执行后续命令。路径应短一些，例如临时目录下的 `pp6-随机编号/repo`，避免 Windows 嵌套产物路径过长。

## 3. 非 editable 安装并核对加载位置

下面仅修改当前新终端的环境，不改系统或用户持久环境变量。保留该终端直到复现结束。

```powershell
uv --version
git --version
uv python install 3.12

$env:UV_PROJECT_ENVIRONMENT = Join-Path $env:TEMP ("pp-env-" + [guid]::NewGuid().ToString('N'))
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONDONTWRITEBYTECODE = '1'

uv sync --locked --no-editable --link-mode=copy
if ($LASTEXITCODE -ne 0) { throw '锁定安装失败，停止复现' }

uv run --locked --no-editable python -I -c "import patchpilot,sys; print(sys.version); print(sys.prefix); print(patchpilot.__file__); assert 'site-packages' in patchpilot.__file__"
if ($LASTEXITCODE -ne 0) { throw '未确认从新安装包加载' }

uv run --locked --no-editable patchpilot --help
if ($LASTEXITCODE -ne 0) { throw 'CLI入口失败' }
```

安装完成后不要漏掉 `--no-editable`，否则后续 `uv run` 可能把项目重新同步成 editable，失去这次验证的含义。`--link-mode=copy` 避免 Windows 跨磁盘硬链接问题，不改变版本锁定。

`uv.lock` 和构建后端固定版本；没有使用本机私有 Python 索引的必要。本次复现保留安装日志，但不打印环境变量全集或模型凭据。

## 4. 运行不需要真实模型的自动测试

这些测试本来就不需要配置模型认证。若新终端继承了模型或项目服务环境，可仅在此终端移除这些变量，避免误读本机配置；这不删除 SDK 认证文件，也不是本机凭据清理操作。

```powershell
Get-ChildItem Env: |
    Where-Object { $_.Name -match '^(ANTHROPIC_|CLAUDE_CODE_|PATCHPILOT_)' } |
    ForEach-Object { Remove-Item -LiteralPath ("Env:" + $_.Name) }

uv run --locked --no-editable pytest -q --tb=short
if ($LASTEXITCODE -ne 0) { throw '普通测试失败' }
```

普通测试显式跳过需要 `--run-docker` / `--run-postgres` 的集成项；Windows 没有符号链接创建权限也会说明原因后跳过。**跳过不是集成测试已通过。** 根配置只收集 `tests/`，不要直接在原始 benchmark repo 中运行 pytest 或导入缺陷模块，以免缓存改变快照摘要。

## 5. 固定测试镜像

首次准备镜像时：

```powershell
docker info --format '{{.OSType}}'
# 必须为 linux；若不是，先修复 Docker Desktop 的引擎配置。
docker build --tag patchpilot-tests:py312-v1 sandbox
if ($LASTEXITCODE -ne 0) { throw '固定测试镜像构建失败' }
uv run --locked --no-editable patchpilot sandbox-check
if ($LASTEXITCODE -ne 0) { throw '沙箱前置检查失败' }
```

Dockerfile 以 digest 固定 Python 3.12.8 基础镜像，测试依赖有版本与哈希锁。相同构建输入不代表不同机器的 image ID 必然一样；每次证据记录实际不可变 ID。已有镜像可以复用，但要明确写明缓存条件，不能声称本次重新构建过。

没有 Docker 时不要改成宿主执行样例，应该报告容器部分未验证。

## 6. 跑通一个完整 Agent 运行时任务

```powershell
uv run --locked --no-editable pytest --run-docker -q --tb=short "tests/test_five_task_agent.py::test_same_agent_loop_works_with_all_five_scripted_tasks[pagination-001]"
if ($LASTEXITCODE -ne 0) { throw '完整Agent运行时链路失败' }
```

这不是另写一套 Agent：测试直接调用现有 `run_agent`。脚本替身安排搜索、读取、提交参考补丁、公开测试、结束候选，共 5 次替身模型响应；实际工具、补丁应用、Docker 和独立验收都走现有实现。

**参考补丁显式交给了测试替身。** 成功只能证明协议、工具和验收链路工作，不能说模型自行发现并修好了问题。这也不代替由用户负责的第35步演示。

pytest 临时目录中的执行产物可用于练习追踪；`run_agent` 的 `workspace_path` / `result_path` 和产物布局见 [agent.py](../src/patchpilot/agent.py)。如需稳定保留一条独立验收报告，继续下一节。

## 7. CLI 独立验收并检查证据

```powershell
$json = uv run --locked --no-editable patchpilot verify-patch benchmarks/tasks/pagination-001/task.json benchmarks/solutions/pagination-001/reference.patch
if ($LASTEXITCODE -ne 0) { throw '参考补丁独立验收失败' }
$verification = $json | ConvertFrom-Json
$verification | ConvertTo-Json -Depth 10
if ($verification.status -ne 'passed' -or $verification.tests.passed -ne 14) {
    throw '验收结果不符合分页任务约定'
}
```

应看到 `status=passed`、14 项测试通过及 `patch_sha256`、`base_sha256`、`candidate_sha256`、`acceptance_sha256`、镜像 ID、日志与报告引用。验证从基线新建工作区，不复用前一条 Agent 候选工作树。

这是**维护者参考补丁验证**，不产生模型费用，也不是 AI 成功案例。产物在当前副本 `.patchpilot-runs/`；不要把整份目录当发布材料。

## 8. 格式、打包和全部任务摘要

```powershell
uv run --locked --no-editable ruff check .
if ($LASTEXITCODE -ne 0) { throw '静态检查失败' }
uv run --locked --no-editable ruff format --check .
if ($LASTEXITCODE -ne 0) { throw '格式检查失败' }
uv build --wheel
if ($LASTEXITCODE -ne 0) { throw 'wheel构建失败' }
uv run --locked --no-editable patchpilot-eval list
if ($LASTEXITCODE -ne 0) { throw '任务材料体检失败' }
```

`list` 要能读取全部20项任务，材料错误应为空；它不运行完整容器评分，也不是模型评测。不需要为本轮文档复现启动付费 `agent-run` 或 `patchpilot-eval run`。

完整 PostgreSQL／Docker 集成命令仍见[服务指南](stage4-service.md)，但不是上述最小复现的默认步骤；其独立 schema 会按现有权限策略保留。

## 9. 结束与失败排查

- 关闭本次新终端即可放弃这些临时环境变量；新虚拟环境、源码副本和报告暂时保留便于核对，不自动删除。
- `No module named patchpilot`：先确认在完整副本根目录执行过锁定安装，而非直接调用 Windows 商店别名 `python`。
- 从 `src/` 而非新 `site-packages` 加载：检查是否漏了 `--no-editable` 或误用了旧终端环境。
- 镜像/引擎错误：停止容器验收，查看 `sandbox-check`；不回退宿主执行、不把环境错误算模型修复失败。
- 快照摘要不匹配：检查换行转换、缓存或人工编辑，不自动重写摘要“修复”问题。
- Windows 路径过长：使用更短的新副本根目录；不要为复现随意更改系统注册表。
- 发现本项目遗留容器：先只读 `docker ps -a --filter label=patchpilot.managed=true`，核对具体 ID 后另行授权清理，不执行全局 prune。

最后完成[个人讲解自测](execution-walkthrough.md)。命令通过只能证明系统行为，不能代替你自己不看答案的讲解。

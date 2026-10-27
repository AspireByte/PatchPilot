# 阶段六进度与证据

## 1. 范围与结论

用户自行负责第35步。本轮处理第34步及第36步的文档、可执行复现与候选材料检查；没有真实模型请求、演示制作、Git初始化/提交/发布、数据库清理或批量容器清理。

**第34步文档与第36步的可代办复现工作已有本轮证据。第36步整体不代签完成：用户个人讲解、最终私有材料/权属确认和应用全局许可证选择仍待人工处理。**

实施范围见[phase6-plan](phase6-plan.md)。没有把阶段五的386项完整集成结果拿来冒充本轮结果；本轮没有重跑PostgreSQL全套，也没有重新构建Docker镜像。

## 2. 文档与事实核对

- `docs/architecture.md`：范围、模型/运行时分工、为何暂不多Agent/向量库/复杂编排、沙箱、验收含义与已知限制。
- `docs/reproduction.md`：独立源码/新非editable环境的PowerShell步骤、无模型自动测试、完整替身链路、参考补丁CLI验收及排错。
- `docs/execution-walkthrough.md`：请求→校验→执行→记录→观察→下一轮→验收的源码路径、空白执行卡与不看答案自测。
- `docs/release-readiness.md`：候选白名单、有限秘密扫描、来源与许可检查，以及尚未解除的发布门槛。
- README新增目的导向入口，保留真实API失败记录；修正两处过强表述：staging校验不等于崩溃原子发布，验收前预算停止不等于验收中timeout。

两次只读核对分别用于查清实现事实和独立审阅新文档。最终静态审阅未报告需要修改的Important/Critical；该结论不是测试通过或法律审计证明。

## 3. 独立复现环境

- 以显式白名单复制267个候选文件，排除原虚拟环境、运行产物、环境凭据和缓存，复制前后逐文件摘要一致。
- 新源码副本位于系统临时目录 `pp6-_d57cw8b/repo`，新虚拟环境为同级 `env`；均保留，不自动删除。
- `uv sync --locked --no-editable --link-mode=copy`成功，安装约9秒。
- 宿主Python为3.12.14；实际导入路径是新环境 `env/Lib/site-packages/patchpilot/__init__.py`，不是原项目src或原.venv。
- 本次子进程移除Anthropic、Claude Code及PatchPilot配置变量，不打印凭据；没有删除本机认证文件。
- 使用uv缓存、已有Git和本地固定测试镜像，不是冷缓存或全新机器实验。安装后主要运行命令另带uv的`--offline`；这只限制uv解析/下载，不是模型网络沙箱证明。
- 实际镜像ID：`sha256:b2ef6e4be50710c2aef540c7c4be6eb3283fda9bb18b342b2997adb29221df69`。本轮复用镜像，没有声称重建。

## 4. 本轮实际命令与结果

以下均在独立源码副本中执行；运行时带`--no-editable`，不是只在原开发环境冒烟。

| 检查 | 本轮结果 |
| --- | --- |
| `patchpilot --help` | 退出0，CLI可用 |
| `pytest -q --tb=short` | **249 passed / 141 skipped**，pytest报告98.94秒 |
| `pytest --run-docker ...tests/test_five_task_agent.py::test_same_agent_loop_works_with_all_five_scripted_tasks[pagination-001]` | **1 passed**，7.60秒 |
| `patchpilot sandbox-check` | Linux引擎、本地不可变镜像可用 |
| `patchpilot verify-patch benchmarks/tasks/pagination-001/task.json benchmarks/solutions/pagination-001/reference.patch` | **passed，14项通过、0失败/错误/跳过，cleanup_ok=true** |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 133 files already formatted |
| `uv build --wheel` | 成功生成 `dist/patchpilot-0.1.0-py3-none-any.whl`，位于副本内 |
| `patchpilot-eval list` | 20任务、12开发/8保留、18group；资料体检无错误 |

普通测试的141项跳过包含未开启的Docker/PostgreSQL集成及4项Windows符号链接创建权限；这些未执行项没有被算作通过。本轮仅额外显式执行一项完整Docker Agent链路，不宣称重跑了全部集成。

完整链路测试直接使用现有`run_agent`和真实工具/Docker/独立验收，但5次模型响应来自脚本替身，参考patch显式给替身；CLI验收也直接输入维护者参考补丁。**两种成功都不是模型自主修复成功，不是第35步演示或真实A/B成绩。**

任务集指纹仍为`0f4112ae3e08c845a48a62e454f3c635f5bbc7697bed9b6fbc141f3717f79365`。这与前阶段材料版本一致；本轮未修改任务快照、参考补丁、维护者验收或生产代码。

## 5. 候选材料检查

- 267个候选文件均可按UTF-8读取；白名单目录未发现链接，未打包本机运行产物或虚拟环境。
- 有限规则扫描的两个待核查命中均已确认是公开测试夹具的相同固定本机API令牌；4处公开开发数据库URL单独记录。
- 未检出其他规则命中的真实密钥；这不证明未知格式秘密、隐私或私有来源绝对不存在，仍需用户人工确认。
- 基准资料有范围明确的MIT；根应用LICENSE缺失，未自动选择或伪造全局授权，不能称“所有材料开源许可已齐全”。
- 新环境生成40个已安装发行包的许可元数据/许可证文件清单，只作库存，不声称完成许可证兼容性法律审查。
- `uv.lock`登记的registry仅`https://pypi.org/simple`。
- README及本轮6份文档的仓库内相对链接已实际检查，无失效目标。
- 复现结束后只读查询本项目容器标签，未见遗留测试容器；没有运行批量删除命令。本轮未启动PostgreSQL集成，不新增测试schema。

## 6. 证据文件

本地证据目录：`.patchpilot-runs/phase6-20261024/`，已忽略，不是发布材料。

- `context.json`、`source-manifest.json`：副本位置、最初候选材料摘要。
- `checks.json`与每条命令的`*.stdout.log` / `*.stderr.log`：完整命令、退出码、耗时和输出。
- `material-scan.json`、`material-review.json`：脱敏模式命中与人工核对依据。
- `dependency-licenses.json`、`document-check.json`：新环境依赖许可元数据与链接检查。
- 副本中的`.patchpilot-runs/run-cvpnypdf/artifacts/verification.json`：参考补丁独立验收，补丁/base/candidate/acceptance摘要及实际镜像绑定。

后续只更新进度文档/计划等说明时，初始manifest不被改写成“从未变化”；最终文档检查与材料扫描另行执行，生产代码与基准摘要保持不变。

## 7. 明确保留给用户的事项

- 第35步：用户制作并验证成功、失败、被停止三类演示。
- 按[执行卡与自测](execution-walkthrough.md)完成不看生成答案的个人讲解，助手不代为勾选。
- 按[发布检查](release-readiness.md)确认私有材料、权属与全局许可证。自动扫描不能替代授权。
- 真实模型成功、付费A/B、账单核对、远程GitHub CI与实际公开发布均未在本轮执行。

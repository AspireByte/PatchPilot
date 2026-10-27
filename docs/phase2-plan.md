> 历史资料：本文记录旧固定任务、服务或评测实现，仅供内部迁移与回归参考。旧命令已下线；当前使用方式见 [README](../README.md) 和 [架构说明](architecture.md)。

# 阶段二：执行、补丁与独立验证实施计划

**目标：** 落地用户指定第 4～9 步，不接模型，交付 `verify-patch` 命令和可复现容器测试证据。
**依据：** 本次对话中已确定的阶段二六步与验收条件。前置工程见 README 和 docs/phase1-progress.md。
**架构：** 复用 load_task 与 compute_snapshot_hash；workspace 仅复制 repo；patching 在临时副本应用普通源码修改；sandbox 使用 Docker CLI 的参数列表；verification 重建快照并加入维护者测试，结果绑定补丁摘要与镜像 ID。

## 约束与默认决定
- 仍使用 argparse、标准库、Pydantic；不引 Docker SDK、任务队列或 Web。
- 当前没有 Git 仓库，原地实施，不初始化/提交/推送。
- 只支持本地自建可信任务；Docker 降低执行风险，不承诺抵御恶意多租户或恶意源码伪造测试结果。
- 基础镜像固定 digest，pytest 及传递依赖以版本和哈希锁定；运行器仅使用已存在的本地镜像 ID，不自动安装依赖或拉镜像。
- 容器非 root、无网络、无 host bind mount/socket、CPU 1、内存 256MiB、pids 64、tmpfs 64MiB、单文件 16MiB、日志轮转；超时后 kill 并强制清理本次容器。无 Docker 时不回退宿主执行。
- 首版补丁仅允许修改 src 中已存在的普通 .py 文件。不支持新增/删除/重命名/二进制/权限修改；非法路径、软链接、fuzzy offset/context mismatch 拒绝。空补丁可验收但不能算修复。
- 单用户串行操作；暂不承诺跨进程并发补丁事务。hash 是内容标识，不是来源签名。

## 任务与验收
1. 固定镜像：sandbox/Dockerfile、requirements.in/lock；记录基础 digest 与本地 image ID。先测试镜像中 Python/pytest 和断网运行。
2. 工作区：src/patchpilot/workspace.py，create_workspace(task, runs_root)->Workspace；root/repo/artifacts 与基线摘要分离。测试不同工作区、源快照不变、无答案复制、拒绝改变后的源快照、输出路径不能放入源快照。
3. 测试执行：src/patchpilot/sandbox.py，DockerRunner.run(workspace, acceptance=None)->TestResult；pytest JUnit 分类 PASS/FAILED/TEST_ERROR/NO_TESTS/TIMEOUT/INFRA_ERROR；记录退出码、计数、日志、耗时、镜像/容器 ID、清理结果。解析器覆盖缺失/坏XML/收集错误/无测试/全跳过；Docker显式集成测试覆盖正常、断言失败、收集错误、无测试、无限循环与资源策略。
4. 补丁：src/patchpilot/patching.py，apply_patch(workspace, patch_bytes, expected_hash)->AppliedPatch；先验证头部和内容，git apply --check 在 staging 执行，修改前后inventory校验；export_diff(task, workspace)->bytes。测试越界、测试修改、删除、软链接、陈旧基线、失败不留下部分修改、diff可在新工作区应用。
5. 验证：src/patchpilot/verification.py，verify_patch(task, patch, runs_root, runner)->VerificationResult；独立工作区从原始快照创建，额外测试仅由宿主登记表选择。benchmarks/acceptance/pagination-001 维护者测试不复制进普通工作区；封存 patch 并记录摘要、基线和镜像 ID。正确 patch通过，空patch失败，测试篡改拒绝，验收hash可追溯。
6. CLI/文档：sandbox-check、verify-patch；成功0，验收失败1，配置/运行环境错误2。默认项目测试不依赖 Docker；显式 --run-docker 时不可因缺镜像/daemon而跳过。更新 README、进度，最终完整检查与独立审查。

## 每项执行顺序
- [x] 先写测试并确认因缺少功能失败。
- [x] 最小实现后运行当前测试与完整项目测试。
- [x] Ruff 语义/格式检查；记录实际输出和必要取舍。
- [x] 最后显式执行全部 Docker 集成用例、实际 CLI baseline/patch 验收、容器清理检查。

## 重点风险
链接/Windows junction 路径（workspace/patching）；测试未运行但误判成功（sandbox）；超时清理失败（sandbox）；补丁越权与部分应用（patching）；验收答案混入公开工作区或版本绑定错误（verification）。各项以对应测试覆盖。

# 阶段二实施与验证记录

## 范围与决定

依据 docs/phase2-plan.md 与本次对话第 4～9 步。当前无 Git 仓库，原地执行，不初始化/提交/推送。主进程逐项实现，最终独立审查一次；阶段一上次审查未完成，不能称其已通过审查。

- 决定：使用 Docker CLI 参数数组，不新增 Docker SDK。运行阶段不拉镜像、不装依赖，预构建镜像由 digest 固定基础镜像，pip require-hashes 锁定 pytest 及传递依赖。
- 决定：容器文件通过 docker cp 传入，零宿主 bind mount；非 root、无网络、固定CPU/内存/PID/单文件/tmpfs/日志限制。容器写层仍可写，不宣称已做总磁盘配额或公网恶意多租户隔离。
- 决定：补丁首版只修改已有 src/**/*.py 文本文件，拒绝新建/删除/重命名/模式修改和偏移应用；后续需要新增回归测试时另行扩展策略与测试。
- 决定：独立验收用例作为维护者 package data 存于 `_acceptance`，不复制到公开工作区，保证非 editable 安装后也可加载。它们不是开源意义上的秘密，隔离指相对于未来 Agent 的文件/工具访问边界。
- 决定：只针对可信自建任务，测试本身运行任意 Python，当前不防恶意代码篡改 pytest/JUnit，也不自动检测 Issue 文本中的秘密。hash不是签名。

## 已取得证据

1. 基线：阶段一 60 passed / 3 skipped，Ruff检查/格式通过。Docker初始daemon未启动；启动已有 Docker Desktop后确认Linux引擎。
2. 固定镜像：基础镜像 python:3.12.8-slim-bookworm@sha256:2199a62885a12290dc9c5be3ca0681d367576ab7bf037da120e564723292a2f0；pytest8.3.5；镜像构建成功，运行时使用本地image ID。
3. 工作区：测试先3failed，最小实现后全量63passed/3skipped，覆盖唯一目录、只复制repo、源摘要改变拒绝、输出嵌入源快照拒绝。
4. 执行器：单元测试先19failed；真实Docker测试先8failed。实现后发现local日志驱动压缩与max-file=1不兼容，明确compress=false后27项相关测试通过。
5. 补丁：测试先21failed，最小实现后21passed，全量103passed/11skipped（8个Docker默认不运行＋3个symlink权限）。覆盖陈旧hash、越界、原测试修改、无部分应用、可重放diff。
6. 独立验收：测试先4failed，最小实现后4passed；含Docker全量115passed/3skipped。参考补丁13项通过，空补丁失败。
7. CLI：新增命令测试先9failed，最小实现后9passed。支持sandbox-check、verify-patch、超时校验、可读错误和结构化JSON产物。
8. 日志与策略补充：实际验证镜像内Python/pytest版本、uid、断网、cgroup与hostsecret不传入。stderr回归最初被pytest捕获机制干扰，改为capfd.disabled后确认旧实现确实丢失stderr（RED）；合并stdout/stderr后通过（GREEN）。
9. 当前含Docker完整测试：126 passed / 3 skipped；Ruff语义与格式检查通过。跳过仅因为Windows无创建symlink权限；Windows目录联接测试实测通过。

## 独立审查与修正

- 独立审查发现 1 项 Important：验收数据的时间戳和ID同向增长，错误的 `created_at >= cursor[0] and id > cursor[1]` 补丁会误过全部13测试。
- 已实际复现：新增反例测试要求失败，原验证器返回passed（RED）。加入时间增大、ID减小的验收后，错误补丁被判failed（GREEN）。正确参考补丁的验收数量更新为14。
- 修正后全量：127 passed / 3 skipped，Ruff语义/格式检查通过；未报告其他Important或Minor。审查按要求未实测daemon宕机和人工强制中断；这些限制在README明确，不承诺无法执行finally时的绝对清理。
- 审查代理报告其技能工具意外启动了一个额外代理并已要求停止；该额外代理未参与审查结论。

## 安装与产物验证

- 首轮全新非editable环境从site-packages加载，内含维护者验收资源，126passed/3skipped；更新验收用例后还需重跑最终安装验收。
- 已实测真实CLI：参考补丁passed、版本摘要与镜像ID齐全，cleanup_ok=true；当时13项用例。新增反例后最终为14项，待最终重跑。
- `uv build --wheel`成功；最终代码更新后将再构建。
- README已补全部阶段二命令、资源限制、补丁支持范围、状态分类和已知边界；工作区产物被Git/Ruff排除。

## 最终验收

- 主环境：`uv run --locked pytest --run-docker -q --tb=short` → **127 passed / 3 skipped**，约34秒。
- 新建非editable环境：确认从site-packages加载且包含验收资源；同一全量命令 → **127 passed / 3 skipped**，约34秒。
- 默认命令（不运行Docker）：111 passed / 19 skipped，其中16项为显式Docker集成开关、3项为Windows symlink权限。
- `ruff check .` → All checks passed；`ruff format --check .` → 30 files already formatted。
- 最新真实CLI：参考补丁status=passed，测试14passed/0failed，cleanup_ok=true；报告位于 `.patchpilot-runs/run-nnwu3ods/artifacts/verification.json`。
- `uv build --wheel` → 成功生成最终 `dist/patchpilot-0.1.0-py3-none-any.whl`。
- 所有验证结束后 `docker ps -a --filter label=patchpilot.managed=true` 无输出：没有遗留本项目测试容器；未清理或变更其他项目容器。
- 原任务重新validate成功，原始快照摘要仍与阶段一一致。
- 独立审查发现已按RED→GREEN修正，无延后Minor。未接模型、未初始化/提交/推送Git。

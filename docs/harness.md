# 受控代码验证执行

## 1. Harness 做什么

Harness 是聊天 Agent 可选调用的受控检查工具。它不理解用户目标，也不负责第二次模型推理；它只从已登记的 profile 中选择一个检查，在当前仓库的临时副本执行，并返回可追踪的结构化证据。执行核心是进程内的 `VerificationRunner`，没有独立守护进程。

聊天工具只接受：

```json
{"profile": "test"}
```

模型不能通过参数传入任意命令。只有启动时添加 `--allow-harness`，`run_harness_check` 才会出现在工具列表中。

## 2. 配置模型

默认读取 `<repo>/.patchpilot/harness.toml`，也可以用 `--harness-config` 指定。配置在会话启动时读取并冻结；`--harness-runtime` 只覆盖运行环境，不改变 profile 的命令。

```toml
version = 1
runtime = "host"
default_profile = "test"
timeout_seconds = 120
max_output_bytes = 16000
allow_host_fallback = true

[profiles.test]
argv = ["python", "-m", "pytest", "-q"]
cwd = "."
runtime = "host"

[profiles.integration]
argv = ["python", "-m", "pytest", "-q", "tests/integration"]
cwd = "."
runtime = "docker"
```

profile 的 `argv` 是参数数组，禁止 shell 拼接、管道、重定向、批处理入口和内联脚本；`cwd` 必须位于仓库内。模型只能选择配置中已经存在的 profile。profile 的 `runtime` 可覆盖全局设置；推荐把普通单元检查设为 `host`，把需要系统隔离的集成检查设为 `docker`。

## 3. 执行流程

```text
VerificationRunner.run(profile)
  -> 校验 profile 和剩余预算
  -> 复制当前仓库到临时快照
  -> 排除凭据、依赖、缓存、会话产物和隐藏内部材料
  -> 根据实际复制内容计算 snapshot_sha256
  -> 选择 Docker 或宿主运行时
  -> 执行 argv，限制输出和时长
  -> 保存 manifest/profile/output/result
  -> 返回 HarnessResult 并写入聊天历史
```

快照包含用户已有的未提交修改和本次会话修改，但不会改变用户原目录。复制过程拒绝链接跳转，并限制文件数量和总大小。

## 4. 运行时与证据等级

| 配置 | 行为 | 结果含义 |
| --- | --- | --- |
| `auto` | Docker 镜像可用时使用 Docker；预检不可用且允许降级时使用宿主 | 以结果中的实际 runtime 为准 |
| `docker` | Docker 或镜像不可用直接 `infra_error` | `isolated=true`、`evidence_level=sandbox` |
| `host` | 在宿主临时副本执行 | `isolated=false`、`evidence_level=local` |

Docker 使用不可变镜像、无网络、受限资源和临时副本；宿主使用当前 Python 环境和进程控制器，但项目代码仍拥有当前用户权限。检查启动后失败不会静默换环境重跑。

默认全局运行时为 `host`。只有 profile 或启动参数明确要求 `docker`（或 `auto` 检测）时才探测 Docker；这样普通 Agent 工作流不会因为一次检查自动启动容器。

## 5. 结果和产物

`HarnessResult` 至少记录：

- `status`、实际 `runtime`、`isolated` 和 `evidence_level`；
- profile、argv、cwd、退出码、超时/取消状态和耗时；
- 当前快照摘要、镜像 ID（如有）、清理状态；
- 有界输出、截断标记、错误详情和产物路径。

每次检查的产物目录包含 `manifest.json`、`profile.json`、`output.log` 和 `result.json`。宿主结果可以作为本地回归证据，但不能描述成维护者独立验收；`passed` 只表示登记命令返回零退出码。

## 6. 与旧验证链的关系

旧 `verify_patch()` 通过 `HarnessService.verify_benchmark()` 兼容分支服务固定任务回归。该分支接收固定任务快照和封存补丁，仍可使用 `sandbox.DockerRunner` 的 JUnit/acceptance 语义；聊天工具不会调用旧的完整 Agent 循环，也不会把隐藏验收材料提供给模型。未来协议适配只需要调用 `VerificationRunner`，不会复制执行策略。

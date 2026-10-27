# 阶段四：本机可靠任务服务

## 1. 现在新增的是什么

API只排队、查询和输出持久化事件；独立Worker负责执行已有Agent/Docker/验收链路。它们在同一代码库中，通过PostgreSQL协作，**不是微服务，也没有进程内假队列**。

没有在本阶段追加任何付费模型请求。诊断模式明确应用维护者参考补丁并运行真实Docker，只验证后端生命周期，不能算AI修复成绩。阶段三真实请求的provider_error记录仍然保留。

## 2. 安装与启动

在项目根目录执行，需要uv、Docker Desktop的Linux引擎、Git。

```powershell
uv sync --locked --link-mode=copy
docker compose up -d --wait postgres
```

`compose.yaml`只启动本项目的PostgreSQL，固定镜像digest，监听 `127.0.0.1:55432`，数据在 `patchpilot_postgres_data` 卷中。示例数据库密码是公开的本地开发值，不能用于生产或公网部署；不要为了重置本项目而清理其他项目的数据库/卷。

设置本终端配置。不要打印、提交或把真实令牌发到聊天里：

```powershell
$env:PATCHPILOT_DATABASE_URL = 'postgresql+psycopg://patchpilot:patchpilot-local-only@127.0.0.1:55432/patchpilot'
$env:PATCHPILOT_API_TOKEN = uv run --locked python -c "import secrets; print(secrets.token_urlsafe(32))"
uv run --locked patchpilot-service migrate
```

令牌至少16字符。`migrate`使用包内Alembic迁移，不自动删除数据，不依赖仓库外alembic.ini；重复升级可安全执行。

前台启动（Ctrl+C停止）：

```powershell
uv run --locked patchpilot-service serve --port 8000
```

服务只绑定127.0.0.1，不提供公网host开关。另一个客户端终端需安全传入相同API令牌。若希望在当前终端继续演示，可以**改用**以下后台启动方式，不要同时占用同一个端口：

```powershell
$apiProcess = Start-Process -FilePath (Get-Command uv).Source -ArgumentList @('run', '--locked', 'patchpilot-service', 'serve', '--port', '8000') -PassThru
$headers = @{ Authorization = "Bearer $env:PATCHPILOT_API_TOKEN" }
Invoke-RestMethod http://127.0.0.1:8000/v1/tasks -Headers $headers
```

API启动需要片刻；若尚未连接成功，先查看API进程输出，不要盲目重复启动多个服务。后台进程继承启动终端的环境配置。

## 3. 完整、无模型费用的诊断示例

以下命令假设API已启动、当前终端持有上面的环境变量。

```powershell
$run = Invoke-RestMethod http://127.0.0.1:8000/v1/runs -Method Post -Headers $headers -ContentType 'application/json' -Body '{"task_id":"pagination-001","mode":"diagnostic"}'
$run.id
uv run --locked patchpilot-service worker --once --diagnostic
Invoke-RestMethod "http://127.0.0.1:8000/v1/runs/$($run.id)" -Headers $headers
Invoke-RestMethod "http://127.0.0.1:8000/v1/runs/$($run.id)/report" -Headers $headers
```

创建接口仅返回queued，不在HTTP请求里跑修复。Worker是独立进程，诊断任务的报告带 `executor_kind=reference-diagnostic`、`model_calls=0`；正确参考补丁应通过14项分页测试。

连续消费诊断队列：

```powershell
uv run --locked patchpilot-service worker --diagnostic
```

`--once`的退出成功表示本轮领取/处理流程结束，任务是否通过请查询Run的 `execution_status` 和 `verdict`，不要只看Worker进程退出码。

## 4. 真实Agent Worker必须明确授权

API任务默认mode为agent。诊断Worker不会领取agent队列；真实Worker不会偷偷用参考答案替代模型。

```powershell
# 该命令允许Worker持续处理agent队列，发送公开任务代码并产生费用。
uv run --locked patchpilot-service worker --allow-api
```

只有准备好本机模型认证、API服务和费用预算后才执行。当前上一阶段真实API修复尚未成功联调，不要把诊断模式成功当成真实模型成功。

认证仅由宿主官方SDK读取，不进数据库、任务请求或测试容器。API不接受API Key、模型地址、仓库链接、宿主文件路径、shell命令或任意模型标识。

## 5. 最小API

所有业务接口均需 `Authorization: Bearer <本机令牌>`。

| 接口 | 用途 |
| --- | --- |
| `GET /v1/tasks` | 已登记任务及固定源码摘要 |
| `POST /v1/runs` | 创建排队执行：task_id、mode、受限budget |
| `GET /v1/runs` | 最近的历史执行 |
| `GET /v1/runs/{id}` | 状态、判定、用量和当前Attempt |
| `GET /v1/runs/{id}/attempts` | 历次尝试，不返回fence token |
| `GET /v1/runs/{id}/steps` | 模型/工具步骤及完成/未知状态 |
| `GET /v1/runs/{id}/events` | 持久化事件SSE |
| `GET /v1/runs/{id}/artifacts` | 不包含宿主绝对路径的产物引用 |
| `GET /v1/runs/{id}/patch`、`/report` | 当前Attempt补丁和报告 |
| `GET /v1/runs/{id}/artifacts/{artifact_id}` | 按Run绑定及摘要校验下载允许的产物 |
| `POST /v1/runs/{id}/cancel` | 请求取消；终态调用幂等 |
| `POST /v1/runs/{id}/restart` | 失败或中断后的安全重启，返回queued |

budget最多8次模型请求、32次工具动作、300秒、100000已知token阈值、每次4096输出token。它不是金额硬上限；正在执行且未返回usage的请求可能已经计费。

未知任务、未知字段和任意执行输入返回422；未知Run/产物404；状态冲突或内容完整性错误409；认证失败401。仅用于本机受控环境，未实现多人权限体系、API限流或公网运营所需安全设施。

## 6. 两种状态必须分开看

`execution_status`描述任务生命周期：queued / running / completed / failed / cancelled / interrupted。

`verdict`描述补丁验收：not_run / passed / failed / error。

例如：
- Agent正常结束但补丁没通过：completed + failed。
- 预算耗尽、尚未验收：completed + not_run，reason说明停止原因。
- API服务错误导致执行失败：failed + not_run。
- 用户取消：cancelled + not_run，即使某个迟到测试恰好完成，也不发布“任务成功”。

最终报告的文件先封存、再在同一个fence事务中登记并提交终态；取消标志在持锁时决定，避免“数据库取消但报告通过”的竞态。报告还绑定最终采用的patch_artifact_id，补丁接口按这个明确引用读取，而不是选择第一份同类候选；报告尚未提交时该接口返回404。磁盘写成功而事务失败时可能留下孤儿文件或被放弃的候选，但它不会被自动当作最终结果。

## 7. 为什么领取与心跳这样设计

- claim使用PostgreSQL `FOR UPDATE SKIP LOCKED`；多个Worker并发时，一个排队Run只能被一个正常领取。
- 每次领取生成新Attempt和随机token，不把worker_id当成所有权凭据。
- Worker写事件、步骤、产物、检查点和终态，都必须匹配当前Attempt/token、running状态及未过期lease。
- 时间以数据库 `clock_timestamp()` 为准；已过期heartbeat不能复活旧租约。
- 心跳线程独立于模型和测试，不会因为长网络请求就停止续租。
- 数据库不可达或失去所有权时保守停止后续动作；不能凭本地旧状态继续发布结果。

本机API维护任务和Worker循环都能回收过期租约。如果所有维护进程都停了，DB不会自己主动改状态；下次服务启动后继续回收。

## 8. SSE：进度记录，不是模型思维

每Run的事件seq在数据库行锁内递增，在其事务内提交；Run终态与对应终止事件同事务提交。模型/工具进度与步骤upsert可能分事务，崩溃窗口内未完整确认的步骤保守标unknown，恢复只认已提交checkpoint。SSE从这些记录读取，不依赖内存缓冲。只输出有界进度元数据，不公开模型thinking或完整消息内容。读到终态后会再次排空事件，避免查询交错漏掉最后事件。

```powershell
curl.exe -N -H "Authorization: Bearer $env:PATCHPILOT_API_TOKEN" "http://127.0.0.1:8000/v1/runs/$($run.id)/events"
```

重连时带 `Last-Event-ID: <上次完整收到的seq>`；也可用 `?after=<seq>`。序号属于Run而非Attempt，安全重启后仍连续增长。

页面断开只结束这条读取连接，不修改cancel_requested。需要取消必须明确调用取消接口。

## 9. 取消与Worker中断

队列取消立即终结，不会被Worker领取。运行中取消先持久化flag，由心跳传播控制信号：
- 模型异步流可取消并关闭；
- docker wait可被打断，原执行器强制清理自己创建的容器与测试进程；
- Agent在后续模型/工具动作前检查停止信号；
- 保留最后已确认的部分补丁，判定仍是not_run，不把部分产物当作修复成功。

Worker被强杀或租约过期时，API独立回收为interrupted（已请求取消则cancelled），started但未确认的步骤标unknown。旧Worker的迟到写入被fence拒绝。

**权限限制：** 本轮自动按查询结果批量删容器的测试路径被权限机制拦截，因此API/Worker回收器默认只回收DB状态，不自动批量删除容器。合作式取消仍清理本执行器自身的已知容器；SIGKILL或daemon崩溃可能留下孤立容器。先检查：

```powershell
docker ps -a --filter label=patchpilot.managed=true
```

只有核对具体ID及其Attempt标签后，再另行授权删除明确目标；不要运行全局prune，也不要把禁用清理描述为已完成自动回收。后台 `reap` 命令同样默认只标记状态。

## 10. 安全重启与步骤边界恢复

只有failed/interrupted允许restart；取消不是自动重试授权，若需要重做取消任务请创建新Run。

重启后产生新Attempt、新工作区、新模型会话：
1. 核验Run登记的固定base摘要与当前任务快照相同。
2. 读取最后已提交checkpoint的封存补丁，校验文件大小与SHA-256。
3. 从原始base建立全新workspace，只应用这份已确认补丁一次。
4. 重新计算整个候选快照摘要，必须等于checkpoint登记值。
5. 不一致则拒绝继续，不让模型或测试执行器接手。

不会信任旧工作区中的未提交变化，也不会把上一次test日志当作本次结果。三种策略都有测试：
- 读取后中断：读取可重做，代码状态保持已确认版本。
- 修改已发生但结果/边界未提交：丢弃该未确认修改，从上一个checkpoint重建。
- 测试已完成但结果未记账：步骤unknown，保留已确认源码，重新跑测试。

这是**代码状态的步骤边界恢复**，不是任意时刻恢复原模型内部状态；不重放旧工具请求，也不保证模型/测试只执行一次。新的Attempt重新计算自己的预算和费用，历史用量仍可从旧Attempt查询，模型请求可能重复计费。

## 11. 数据与文件布局

数据库六表：run、attempt、step、event、artifact、checkpoint。数据库保存小型状态与引用，不存大日志、完整模型消息或思维内容。

```text
.patchpilot-service/
  workspaces/<attempt-id>/run-<uuid>/   每次尝试独立工作区
  artifacts/<attempt-id>/<uuid>-*.bin  已封存文件，数据库保存相对引用/大小/摘要
```

文件名唯一，不覆盖旧Attempt文件。API按Run绑定检查产物ID，拒绝路径越界/链接，下载前验证摘要。旧Attempt历史可以查询，但不能替换当前Attempt的结果。

## 12. 测试与已知代价

```powershell
uv run --locked pytest --run-postgres --run-docker
uv run --locked ruff check .
uv run --locked ruff format --check .
```

默认pytest不访问Postgres，不启动Docker，不调用付费模型。数据库测试使用真实PostgreSQL，每测试独立随机schema，不用SQLite冒充行锁行为。

**测试清理限制：** 自动删除随机schema也曾被权限机制拦截；当前夹具已经完全去掉自动DROP，只释放连接并保留 `pp_test_<随机编号>`。精确schema名记录在pytest的 `postgres_schema` 属性中。重复测试会积累本项目测试数据；清理前需核对具体schema并另行授权，不提供未核实目标的批量删除命令。

`.patchpilot-service/` 和 `.env` 已忽略，但程序不自动加载.env：环境变量需由终端或部署配置显式提供。数据库卷和产物都需要备份；当前不提供跨机器共享存储、自动GC或数据库故障下的exactly-once保证。

建议阅读：repository的claim/fence/finalize → worker的heartbeat与boundary → api的SSE → artifacts的恢复 → test_live_service的真实双进程验收。

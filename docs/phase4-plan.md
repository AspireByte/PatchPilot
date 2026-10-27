# 阶段四：可靠任务服务实施计划

**依据：** 用户指定第21～28步。沿用现有CLI/Agent/官方SDK/Docker/独立验收，不调用额外付费模型，不引队列/Redis/微服务。

## 架构与约束

- PostgreSQL + SQLAlchemy2 + Alembic保存Run、Attempt、Step、Event、Artifact、Checkpoint；大文件只保存受控相对引用、摘要和大小。API与Worker是同一代码库的不同进程。
- API默认仅127.0.0.1，Bearer令牌必需；创建接口仅接受登记task_id、受限预算与agent/diagnostic模式，不接受路径、仓库链接、命令、模型凭据或任意模型地址。
- 独立项目Postgres，固定已有postgres:16摘要，仅监听127.0.0.1:55432，不改其他项目服务/数据。测试必须显式`--run-postgres`并使用临时隔离schema，不用SQLite冒充锁/租约验收。
- 原子领取：SELECT FOR UPDATE SKIP LOCKED，创建Attempt和随机fence token；所有Worker写入都校验当前Attempt、token和未过期租约，过期不可复活。DB时钟为准。
- 每Run递增事件seq在行锁内分配；SSE只读取持久化事件，Last-Event-ID续读；不输出内部thinking或原始SDK内容，断开不取消。
- 取消：队列立即取消，运行中持久化cancel_requested；心跳线程传播ExecutionControl，SDK异步流和docker wait可取消；取消优先于迟到成功结果。
- 失效：API独立维护任务定期reap，Worker循环也reap；租约过期标interrupted（已请求取消则cancelled），未知步骤标unknown。旧Worker不能发布产物/结果到当前Attempt。
- 重启：只从固定base+已提交checkpoint补丁重建新workspace；校验base/patch/candidate摘要，拒绝不一致。原workspace不复用；新Attempt与新模型会话，旧结果不覆盖新结果。
- 步骤边界恢复限定为已提交的代码状态：读取可重做；未提交修改丢弃；测试已执行但未记录则重跑。不会重放未确认工具，不声称exactly-once，模型请求可能重复计费。
- 诊断模式显式应用维护者参考补丁并运行真实Docker验收，只验证服务生命周期，不伪装AI修复。生产Agent Worker需`--allow-api`；本轮仅测试/诊断不付费。

## 数据仓储接口（供API/Worker共用）

模块`patchpilot.service.repository`：
- `Repository(database_url: str)`，属性`engine`；`close()`释放。
- `migrate(database_url: str)`升级Alembic（单独`database.py`导出）。
- `create_run(task_id: str, mode: str, budget: dict, base_sha256: str) -> dict`：queued并持久化初始事件；返回get_run形状。
- `get_run(run_id: str) -> dict | None`，`list_attempts(run_id)->list[dict]`，`list_steps(run_id)->list[dict]`，`list_artifacts(run_id)->list[dict]`，`list_events(run_id, after: int=0, limit: int=100)->list[dict]`。
- `claim(worker_id: str, *, mode: str='agent', lease_seconds: float=15) -> Claim | None`。
- Claim冻结dataclass字段：`run_id,attempt_id,token,worker_id,task_id,mode,budget,base_sha256,checkpoint_id`（最后可None）；所有ID用str。
- `heartbeat(claim, lease_seconds=15)->bool`：校验fence/租约，刷新，返回cancel_requested；失败抛`LeaseLost`。
- `append_event(claim, kind: str, payload: dict)->dict`：有界metadata事件；`upsert_step(claim, key: str, kind: str, name: str, status: str, metadata: dict)->None`，key在Attempt内唯一。
- `register_artifact(claim, kind: str, relative_path: str, sha256: str, size: int)->dict`；字段`id,attempt_id,kind,relative_path,sha256,size`。
- `save_checkpoint(claim, artifact_id: str, base_sha256: str, candidate_sha256: str, metadata: dict)->dict`；字段`id,artifact_id,base_sha256,candidate_sha256,metadata,attempt_id`；原子更新Run.latest_checkpoint_id。
- `get_checkpoint(checkpoint_id)->dict|None`；`get_artifact(run_id, artifact_id)->dict|None`。
- `finish(claim, execution_status: str, verdict: str, reason: str, usage: dict)->None`：cancel_requested时覆盖为cancelled/not_run；允许completed/failed；释放租约并插入终止事件，fence失效拒绝。
- `cancel(run_id)->dict|None`：queued即cancelled；running仅置flag；终止态幂等。
- `reap_expired()->list[dict]`：返回被回收的`run_id,attempt_id`，状态interrupted或cancelled，步骤started->unknown。
- `restart(run_id)->dict`：仅interrupted/failed允许，引用latest_checkpoint，设queued且清除cancel/current_attempt/判定；旧Attempt不动。其他状态抛`StateConflict`。
- `LeaseLost`、`StateConflict`异常。

Run公共字典字段至少：`id,task_id,mode,execution_status,verdict,reason,budget,base_sha256,current_attempt_id,latest_checkpoint_id,cancel_requested,created_at,updated_at,usage`；时间用ISO字符串，避免返回ORM对象。执行状态与verdict独立。事件字段`seq,kind,payload,attempt_id`。只存有界metadata；不把完整日志/思维/消息写进数据库。

## 主进程负责

`service/config.py,catalog.py,artifacts.py,api.py,worker.py,executors.py,cli.py`；Agent新增control/on_boundary/prebuilt workspace可选钩子，独立执行器不受影响；SDK与Docker接入可取消Control。增加Postgres/Docker/进程集成测试、文档、真实无付费诊断演示。

## 实施门与验收

- [x] 仓储/迁移：先RED后GREEN，重连持久、并发只一领取、事件序号、租约fence、取消/reap/restart、checkpoint引用。
- [x] 文件产物：受控根、摘要、链接与越界拒绝；恢复新workspace与hash核对。
- [x] 控制传播：取消不再新动作，SDK/Docker活动可终止，finally清理。
- [x] API：创建只排队、未知任务/字段拒绝、查询步骤/patch/report、认证、SSE恢复与断开不取消。
- [x] Worker：独立进程、心跳与所有权、取消和中断识别、安全重启和三种边界恢复策略。
- [x] 全量现有测试、真实Postgres/容器测试、构建/非editable、独立审查与必要修正；不宣称exactly-once。

## 实施后确认的权限边界

随机schema自动删除与回收器按查询结果批量删容器被拦截，未绕过；fixture完全移除自动DROP并保留测试schema，回收器cleanup默认False且CLI无启用开关。合作式取消原执行器仍清理自身已创建的容器；强杀孤立容器需核对具体ID后另行授权。详见phase4-progress.md和stage4-service.md。

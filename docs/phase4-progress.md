> 历史资料：本文记录旧固定任务、服务或评测实现，仅供内部迁移与回归参考。旧命令已下线；当前使用方式见 [README](../README.md) 和 [架构说明](architecture.md)。

# 阶段四实施与验收记录

## 范围与实际决定

按用户第21～28步及docs/phase4-plan.md实现本地任务服务。未初始化/提交/推送Git，未新增任何付费模型请求。

- API与Worker为同一代码库的独立进程；PostgreSQL作为持久化任务队列与状态存储，不引Redis/Celery/Kafka。
- 独立compose项目：postgres:16固定digest，仅127.0.0.1:55432，数据卷patchpilot_postgres_data，不改其他项目服务。
- 六表run/attempt/step/event/artifact/checkpoint；模型消息与大日志仍在文件。API仅登记任务、受限budget和agent/diagnostic模式；本机Bearer认证。
- 原子claim、Run行锁seq、DB clock租约、随机fence token。所有Worker发布检查当前Attempt/token/lease；过期不可复活。
- 取消通过独立heartbeat传播，Agent/异步SDK流/Docker wait均可停止。原执行器清理自己创建的已知测试容器。
- 恢复只恢复确认过的代码状态：新Attempt、新workspace、新模型会话；没有重放未确认工具，没有exactly-once承诺。
- 显式reference-diagnostic执行器只验证服务生命周期，不伪装AI修复。真实Agent Worker仍要求--allow-api，本轮未启动。

## 两项权限限制与安全替代

1. 自动删除随机测试schema的路径被权限机制拒绝。未换工具重试删除；已从fixture彻底移除自动DROP，仅释放连接并保留随机pp_test_<uuid>。新fixture经过读取核对后，才运行后续仅创建/查询的隔离测试。精确schema名记录在pytest user_properties的postgres_schema字段。
2. 租约回收器按运行时查询结果批量强制删容器的测试路径被拒绝。未执行该路径，ServiceSettings与回收器cleanup默认False，CLI没有启用开关。回收只改DB状态；SIGKILL遗留容器需先核对具体ID再另行授权清理。合作式取消仍由原执行器清理它自身创建的已知容器。

最终只读统计：本项目数据库保留510个测试schema。未擅自删除，也没有清空卷或全局prune。

## 分层证据

- 仓储：契约先RED；真实Postgres初64passed、增强79passed。增加list_runs一度受旧fixture删除策略限制，移除删除后已纳入最终全量验收。finalize先8项RED，原子实现后15项针对性GREEN。最终仓储90项纳入全量。
- 控制：3项先RED；Agent初始取消不请求模型，活动容器取消起初接口缺失RED，接入后GREEN；SDK在流中被取消并关闭。
- 产物：7项先RED，封存/校验/路径拒绝及base+patch重建后GREEN。
- API：实现前1failed/8errors；接入后通过。初次一条测试将预算键max_tokens误当秘密token，修正为检验真实私密字段与值，不修改生产逻辑。
- Worker：7项先RED，后通过；并发领取、heartbeat取消、失效fence、新workspace恢复与损坏checkpoint拒绝。
- CLI：独立服务入口先RED，实现后真实子进程诊断通过；HTTP API与Worker两个真实进程实测，SSE断开不取消、重连续读；强杀测试Worker后由独立API回收，pending步骤unknown。
- 三种恢复边界：读取后中断、补丁未确认、真实Docker测试完成但tool_finished/边界尚未保存，均有测试。最后一项不是伪造测试日志。
- API取消→Worker heartbeat→活动Docker终止实测通过，保留确认过的部分补丁，报告和DB为cancelled/not_run。

## 独立审查及修正

发现3项Important，未报告Critical或风格Minor；均实际故障注入RED→GREEN：

1. SSE查询空事件后终态恰好提交，会漏发最后事件。新增交错测试RED；读终态后再排空持久事件，GREEN。
2. permission_denied/invalid_json/model_timeout被误标completed，不能restart。新增9类模型故障用例，3项RED；共享ModelFailure.REASONS分类后全部failed/not_run，GREEN。
3. 报告封存一次临时失败后重发confirmed patch，API却选最早被放弃候选。注入OSError，RED；最终报告绑定patch_artifact_id，补丁API按该明确引用读，GREEN。报告与终态仍通过同一fence事务提交。

## 最终验证

- 主环境：`uv run --locked pytest --run-postgres --run-docker -q --tb=short` → **334 passed / 4 skipped**，约176秒。
- 全新非editable环境：从site-packages加载，迁移版本脚本存在；相同全量命令 → **334 passed / 4 skipped**，约184秒。
- 4项跳过均为Windows无创建symlink权限，junction测试实际执行。
- Ruff语义与格式检查通过：91 files already formatted。
- `uv build --wheel`成功，`patchpilot-service --help`真实执行成功，安装包包含迁移资源。
- 最后检查无遗留本项目测试容器；5项原始快照重新load_task均通过。仅本项目Postgres服务按需保留运行。

## 可查询诊断记录

本项目数据库public schema已执行migrate，并由新的独立Worker进程持久化如下记录，重连另一进程查询通过：

- Run ID：`abcef5b9-09aa-4e84-8a64-722bfb5e5d69`
- task_id：pagination-001
- mode：diagnostic（reference-diagnostic，不是AI修复）
- execution_status：completed
- verdict：passed
- model_calls：0，tool_calls：2；分页验收14项通过。

本次没有留下常驻API/Worker进程；用户可按docs/stage4-service.md启动并查询此记录。随机API演示令牌只在执行命令的临时环境中生成，没有打印或写入项目。

## 未承诺的能力

没有恢复原模型会话或去重模型计费；没有跨机器共享产物/自动GC；没有公网多用户权限体系；没有在Worker被SIGKILL后无条件自动清理孤立容器。所有限制在服务指南中明确。

> 历史资料：本文记录旧固定任务、服务或评测实现，仅供内部迁移与回归参考。旧命令已下线；当前使用方式见 [README](../README.md) 和 [架构说明](architecture.md)。

# 阶段三进度与证据

## 决定与边界

- 依据 docs/phase3-plan.md 与用户第10～20步要求；不增加数据库、Web、多Agent或向量库。
- 继续在无Git的原目录实施，不初始化/提交/推送。新增四个样例在独立专属文件范围实现；主运行时仍为单Agent。
- 工具仅访问公开workspace.repo；精确搜索而不提供正则（避免额外复杂度与ReDoS）。新增测试限regression/test_*.py，不能覆盖原tests/repro/配置/依赖。独立验收仍只运行可信原测试和维护者用例，不把模型新增测试当作正确性证明。
- 单提供方官方anthropic SDK 1.17.0，默认claude-opus-5-5。用户明确不做多模型路由，因此不启用默认跨模型fallback；拒绝直接停止。SDK隐式重试为0。
- 检测到已配置认证和API地址，仅检查是否存在，不显示内容。自定义网关不发送可能不支持的eager_input_streaming字段；仍严格等待流完成并校验原始JSON片段和工具schema。
- 预算是操作启动门与已知用量停止阈值，不是API金额硬上限；进行中的请求可能计费但未返回usage，记录calls_without_usage。清理有独立有界宽限，不能对宿主强杀/daemon故障承诺绝对清理。

## TDD与回归

- 工具模块：20项先因未实现失败；最小实现后暴露容器targets接口与Git新增文件头差异；检查真实git diff输出后规范化regression新增文件头，补齐受控targets。
- 新增回归补丁：旧策略明确拒绝（RED），扩展后可导出和重放（GREEN）；相关工具/补丁/Docker测试58passed/1权限skip。
- Agent循环：12项测试先失败，实现后全部通过；模型3次上限、工具配对、失败反馈、截断/拒绝不执行、重复动作/token/context/time预算、真实Docker独立验收。
- SDK适配：13项测试先失败，最小实现后全部通过；保留content/signature/usage，错误安全分类、代理字段策略与流JSON校验。
- 五任务通用循环与CLI：9failed/1passed的RED，登记维护者验收与CLI后10passed。五任务均用显式test-double携带参考补丁，只证明流程，不代表模型修复成绩。
- 协议异常补充：end_turn与tool_use同时出现时旧实现会先执行工具，新增测试RED后修正为先拒绝，GREEN。
- 默认回归169passed/28skipped（当时版本）；最后含真实Docker全量194passed/4skipped。4项仅Windows无symlink创建权限，junction实测通过。
- Ruff导入修正与格式化后检查通过。

## 新增任务

boundary-001：半开区间终点（6正常+1复现+7验收）；null-001：仅None回退、保留空串（8+1+8）；validation-001：拒绝bool数量（13+2+12）；state-001：乐观版本更新递增（9+1+9）。均在临时副本验证缺陷断言失败、参考补丁全绿、源摘要不变；没有运行期按任务id选择修复逻辑。

## 真实调用授权

用户通过交互明确选择“允许一次有界实测”。范围仅pagination-001，claude-opus-5-5，最多6次请求，每次4096输出token，180秒总预算，不自动重试或切模型。实际费用由当前配置的服务计费。

真实结果：首个请求返回SDK API状态错误，stop_reason=provider_error，status=unverified，model_calls=1、tool_calls=0、calls_without_usage=1，verification=null。没有重试、切模型或补做请求。累计usage字段为0仅表示未取得usage，不代表零计费。未保存该错误的HTTP状态码，不能推测具体服务故障原因。

报告：`.patchpilot-runs/run-wdr2p9fm/artifacts/agent-result.json`；事件和消息也在同一artifacts目录。候选补丁为空，源快照未改。步骤19的“真实尝试并留证”已执行，但真实修复成功尚未验证，不包装为成功案例。

## 独立审查与修正

独立审查仅静态读代码，发现2项Important，无Critical或Minor：
1. 同步SDK的read timeout不等于整个流的绝对deadline。新增停滞流测试，旧实现0.25秒后才退出，超过0.05秒预算（RED）；改用AsyncAnthropic＋asyncio.timeout及稳定asyncio.Runner后按deadline取消并关闭流（GREEN）。
2. 流内基础anthropic.APIError未被安全兜底。新增流内含敏感哨兵的异常及最终报告测试，旧实现泄出原始异常且没有最终报告（RED）；在具体异常分支后补APIError安全转换（GREEN），最终报告保留、无哨兵泄露。

另补真实官方SDK＋httpx2.MockTransport的离线SSE解析与连续两次客户端复用测试，不联网、不用真实凭据。CLI进度回调先RED后GREEN，stderr实时事件与stdout最终JSON分离。此次修正没有再次调用真实模型。

## 最终验证

- 主环境：`uv run --locked pytest --run-docker -q --tb=short` → **199 passed / 4 skipped**，约111秒。
- 新建非editable虚拟环境：从site-packages加载、内含5项验收资源；相同全量命令 → **199 passed / 4 skipped**，约127秒。
- 4项跳过均为当前Windows无创建symlink权限，Windows目录联接检查实际执行。
- Ruff：All checks passed；格式检查61 files already formatted。
- `uv build --wheel`成功生成 `dist/patchpilot-0.1.0-py3-none-any.whl`；`agent-run --help`实际执行成功。
- README已覆盖工具契约、权限例外、消息配对、预算边界、SDK授权与真实失败结果，测试替身与真实修复成绩明确区分。
- 真实模型尝试只执行已授权的一次，首请求provider_error，最终结果unverified；没有可宣称的真实修复成功案例。

最终核对已完成：5项任务均重新load_task通过，原始快照摘要未变；`docker ps -a --filter label=patchpilot.managed=true`无输出，没有本项目遗留测试容器。未删除或修改其他项目容器，未初始化/提交/推送Git。

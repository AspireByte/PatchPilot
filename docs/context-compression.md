# 上下文压缩

本模块借鉴 billion-context 的核心分工：模型撰写摘要，内核管理消息引用、压缩范围、摘要块及发送视图。实现为 Python 原生模块，不依赖其 TypeScript 代理，也没有复制其源码。这里的层级表示摘要合并深度，并非完整复刻其 LSM 调度算法。

## 调用与职责

`AgentSession → AgentLoop → ContextBuilder.build() → HistoryCompactor → Session 摘要适配 → 当前工作模型`。

- `core/compression.py`：分组完整轮次、摘要块、引用校验、视图构建和多层合并。不直接访问 Provider 或磁盘。
- `session_runtime/compression.py`：固定摘要提示与 JSON 协议；超大原文分片摘要，每个请求都检查实际序列化大小。
- `session_runtime/session.py`：注入主模型，协调预算、持久化、worker/evaluator 和手动入口。

`ContextBuilder.fits()` 与 `view()` 只读，不触发模型调用；`build()` 先扣除 system、工具定义和外部来源的开销，再检查自动触发阈值。目标评估额外检查 conversation 包装成 JSON 字符串后的转义开销。当前预算单位依然是字符，不是精确的模型 token 窗口。

## 默认行为

历史视图达到可用字符预算的 80% 时尝试压缩，目标为 60%；保护窗口导致无法达到目标时，只要低于硬上限仍可继续。手动 `/compact` 跳过触发阈值，保护规则不变。没有可压缩旧历史时返回零节省，不调用模型。

至少保留最近 6 条原始消息及当前用户发起的完整轮次。只有完整轮次边界可以折叠，工具调用及所有结果整体进入摘要或整体保留。保留的 thinking/redacted_thinking 等块不会被改写。单个当前轮次或工具输出过大时会明确停止，不截断原文。

短轮次按体积分组，摘要正文最多 6000 字符且不超过可用预算的 1/8。4 个活动摘要块合并为一个父块，层级最多标记为 3；更深合并仍记录子块关系。宿主决定范围，模型仅返回 `{"summary": "..."}`。摘要需非空、未超长、正常结束且确实缩小视图。

摘要与工作请求共享模型调用次数、已知 token、超时和取消信号。`stats.compactor` 单独记录摘要用量，未知用量保留为未知。一次手动操作使用新的本轮预算。摘要仍可能丢失信息，保留原文不等于摘要完全无损。

## 状态与失败

`state.json` 保留 `version: 1` 和原始 `messages`，新增可选 `compression.blocks` 及 `stats.compactor`；新运行时可读旧会话，旧运行时不保证能读取新增字段。引用 `m0`、`m1` 按原始消息位置稳定编号；块记录半开范围、SHA-256、摘要、层级和子块。恢复时校验原文与范围，损坏时拒绝恢复。

所有候选摘要先暂存，成功且满足硬预算后整体提交。模型调用失败、非法 JSON、摘要未缩小、预算耗尽或压缩后仍超限时保留之前已提交的块和全部原文。模型调用费用统计不会回滚。`context_compacted`、`compression_failed` 和带 `kind=compactor` 的模型事件用于追踪。

## 程序化入口

```python
result = session.compact()  # before_chars / after_chars / saved_chars / blocks
originals = session.compactor.decompress("b1", session.messages)
matches = session.compactor.search("main.py", session.messages, limit=10)
```

恢复接口返回原文副本，不会自动将全部原文重新放回模型请求。检索为本地关键词检索，返回 `mN` 引用和片段；没有新增模型检索工具或 CCR 外置存储。

内部服务的 `create_app(settings, repository=..., session=session)` 可显式绑定一个已授权的活动会话，随后使用 Bearer 鉴权调用 `POST /v1/sessions/{session_id}/compact`。未绑定或 ID 不匹配返回 404，会话忙碌或压缩失败返回 409；普通服务启动不自动打开磁盘会话或创建付费模型。会话所有权、关闭和模型配置仍由宿主负责。手动压缩与 `submit()` 互斥。

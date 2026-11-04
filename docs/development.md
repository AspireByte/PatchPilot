# 开发与阅读指南

## 1. 本地检查

```powershell
uv sync --locked
uv run --locked pytest -q
uv run --locked ruff check .
uv run --locked ruff format --check .
```

默认测试不调用真实模型，也不要求启动 Docker 或 PostgreSQL。真实 API、Docker 和数据库集成要单独确认环境与费用，测试替身通过只能说明协议和状态流转正确。

## 2. 启动聊天

```powershell
uv run --locked patchpilot chat `
  --repo D:\code\My-agent `
  --allow-api `
  --allow-commands `
  --allow-harness
```

只读分析可以省略 `--allow-commands` 和 `--allow-harness`。恢复时使用 `--resume <会话编号>`，并重新提供本次需要的权限。

## 3. 推荐阅读顺序

```text
interfaces/cli/main.py / chat.py
  -> session_runtime/session.py
  -> core/loop.py / model/protocol.py / model/providers/anthropic.py
  -> tools_runtime/builtin / tools_runtime/executor.py
  -> core/context.py / infrastructure/persistence/session_store.py
  -> session_runtime/goal.py
  -> infrastructure/verification / infrastructure/process
  -> legacy/benchmark（固定任务兼容边界）
```

阅读每一层时回答三个问题：

1. 谁决定下一步：模型、工具运行时、Harness，还是用户？
2. 决策什么时候生效：请求前、工具分发时、文件写入时，还是检查结束时？
3. 状态写在哪里：消息、事件、文件基线、快照 manifest，还是 result 报告？

## 4. 事实边界

- 单元测试通过不等于真实模型已经完成代码修复。
- 宿主 Harness 的 `isolated=false` 结果不是安全沙箱证明。
- Harness 的 `passed` 只表示登记命令退出码为零。
- 文档中的未来扩展不等于当前已实现功能。

# 聊天 Agent 执行链路

## 1. 启动阶段

`myagent chat` 首先确定仓库路径、会话目录、模型和权限。`--allow-api`、`--allow-commands`、`--allow-harness` 分别控制三类能力；未授权的工具不会出现在发送给模型的工具列表中。

新会话会创建 `session_runtime.AgentSession`、`tools_runtime.builtin.LocalTools`、`core.context.ContextBuilder` 和 `infrastructure.persistence.SessionStore`。恢复会话时核对原仓库路径，并重新读取启动时的 Harness 配置；命令和 Harness 权限必须由本次启动再次提供。

## 2. 一轮 submit

```text
用户文本
  -> 追加 user message
  -> 检查取消、时间、调用次数、token 和上下文容量
  -> 请求工作模型
  -> 保存完整 assistant message
  -> 若有 tool_use：逐个校验并执行
  -> 写入配对的 tool_result
  -> 继续请求模型，直到没有工具调用
  -> 输出文本，等待下一次提交
```

工具是逐个执行的，不是整批事务。因此同一轮前一个工具已经成功修改文件时，后一个工具失败不会自动回滚前一个修改。模型看到的是有界工具结果；完整消息和事件仍保存到会话产物中。

## 3. 文件修改的保护链

```text
read_file(path)
  -> 返回 UTF-8 内容和 sha256
write_file/edit_file(path, expected_hash)
  -> 重新读取实际文件并比对摘要
  -> 发现外部变化或没有读取授权则拒绝
  -> 原子替换文件并记录首次修改前内容
```

工具还会拒绝越界路径、链接跳转、凭据文件、依赖目录和缓存。`get_diff` 基于会话保存的原始内容生成差异，因此不要求仓库是 Git 仓库。

## 4. 宿主命令与取消

`run_command` 需要 `--allow-commands`。Windows 使用 `PowerShell -NoProfile`，命令在固定仓库目录启动，由共用进程执行器限制时长和输出，并尝试回收进程树。取消请求会通过 `ExecutionControl` 从会话传到模型、工具和外部进程；已经发生的文件修改会保留。

宿主命令拥有当前用户的系统权限，因此它不是安全沙箱。模型只有在用户显式打开该能力时才能看到这个工具。

## 5. Goal Loop

普通输入不会自动触发评估。用户输入 `/goal <完成条件>` 后，工作模型继续使用本地工具；每轮工作结束，再由独立且无工具的评估调用检查聊天和工具证据。

评估器必须返回严格的 `ok / reason / impossible` JSON，并区分工具调用成功和实际检查成功。例如 Harness 工具返回 `ok=true` 只代表工具调用完成，仍需检查嵌套结果的 `status`、退出码、快照摘要和隔离标记。

目标状态只有在显式目标下变化：`none`、`active`、`paused`、`achieved`。评估异常、无法客观验证、预算耗尽或用户中断都会保留目标并暂停；只有 `/goal resume` 才会继续。

## 6. 关键边界

- `end_turn` 表示模型结束当前回答，不表示代码已通过测试。
- 普通回答结束后不会启动 Docker、PostgreSQL 或隐藏验收。
- 模型的文字判断不是测试证据；证据来自工具、命令和 Harness 结果。
- 上下文达到限制时提示 `/new`，当前不会静默删除旧消息或自动压缩。

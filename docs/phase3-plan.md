> 历史资料：本文记录旧固定任务、服务或评测实现，仅供内部迁移与回归参考。旧命令已下线；当前使用方式见 [README](../README.md) 和 [架构说明](architecture.md)。

# 阶段三：最小受控 Coding Agent 实施计划

依据：用户指定第10～20步。已有阶段二固定镜像、独立workspace、补丁staging和独立验收；本阶段复用，不加数据库/Web/多Agent/向量检索。

## 实现与边界
1. `tools/`：路径、列表/读取/精确搜索、源码补丁、get_diff、新增regression tests、固定测试选择器，Pydantic严格参数与统一call id/result。所有文件能力只接触workspace.repo，不接受host路径。输出有上限，明确truncated；原始工具证据保存在artifacts，不公开host绝对路径。
2. 扩展patching：仅新增`regression/test_*.py`，不允许改原tests/repro/config/dependencies。独立验收只能使用源码修复，候选新增tests不影响可信验收。导出补丁保留新增回归测试。普通容器可运行full/原tests/repro/regression或已登记文件，不接受命令/pytest参数。
3. `agent.py`：单模型协议+显式循环，append-only消息，先完整获取模型响应、检查stop_reason再执行工具。所有同轮tool_result合为一个user消息，保留调用编号。模型end_turn只触发独立验证，不等于passed。
4. `budget/context`：模型次数、工具次数、总时长、token用量、重复无效动作、请求/回复大小限制。开始每个动作前检查deadline，并把剩余时间传给SDK/容器；deadline耗尽不启动验收。保留完整调用/结果配对，不静默裁剪历史；上下文超限时停止并封存patch。工具结果仅数据，不能更改权限。
5. `providers/anthropic_model.py`：单提供方官方SDK、流式收集、使用get_final_message和原始响应content回传。默认claude-opus-5-5、自适应thinking；不启用跨模型fallback（用户明确不做多模型路由）。无工具输入在流完成/校验前执行。超时/限流/鉴权/截断/拒绝有明确停止原因，关闭SDK隐式重试保证预算透明。SDK替身测试不联网。
6. CLI `agent-run`：真实模型需要显式`--allow-api`确认发送公开任务内容及付费。默认不调用API，不读/打印密钥；示例/测试替身不进入真实修复成绩。凭据仅由官方客户端解析，不传给容器。未知/未配置认证只报告错误，不索要明文密钥。
7. 5个可信任务：现有pagination加boundary/null/validation/state，自建样例各自先验证缺陷红、参考补丁绿、独立用例绿。运行时不能按task_id选择修复逻辑；只有维护者验收登记按id选测试。

## 逐项测试与验收
- [x] 工具路径、链接、行范围、缺文件、搜索空结果与输出截断（TDD）。
- [x] 新增回归测试、原测试不可修改、补丁冲突不覆盖、相对差异重放（TDD）。
- [x] 固定目标选择器与真实Docker可见测试，不携带独立验收。
- [x] 替身完整搜索→读取→修改→测试→结束循环；失败反馈和call id保留。
- [x] 3次模型上限不发第4次；工具/时间/token/重复错误限制；截断/拒绝不执行工具。
- [x] 官方SDK替身保留content/usage，超时/限流等不会泄露凭据。
- [x] 5任务红绿+同一通用执行链；一次真实尝试已获授权并执行，首请求服务状态错误，未声称修复成功。
- [x] 真实Docker全量回归、Ruff、wheel、非editable安装、独立审查与必要修正。

## 授权与风险
用户授权实现；真实API发送源码/费用另行确认。当前环境只知道认证变量是否存在，不读取任何明文秘密。仍是本地可信代码范畴，不宣称可防敌对Python伪造pytest结果。当前无Git仓库，不初始化/提交/推送。

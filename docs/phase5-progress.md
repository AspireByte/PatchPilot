# 阶段五进度与证据

## 设计与来源

按用户第29～33步及phase5-plan实现。没有新增付费模型请求、没有初始化/提交/推送Git，没有远程GitHub CI执行。

- 原5任务保留，新增15项自建场景，20类别、18group，development12/holdout8；关联group不跨拆分。
- 原创资料为AI协助编写、可执行行为测试核验，没有外部真实Issue来源；MIT范围在benchmarks/LICENSE明确，仅基准资料，不自动给整个项目选许可。
- dataset.json与benchmarks/REVIEW.md明确draft_requires_human_review，不假定已经人工逐项批准。
- 真实评测有--allow-api与--approve-dataset指纹双门，保留集另需--confirm-holdout。本轮仅资料验证与脚本/SDK替身测试。

## 实现证据

1. 15新任务分两组独立专属文件实现：8项/7项测试先因任务缺失RED，后8passed/7passed；每项正常绿、复现断言红、参考patch+维护者验收绿，源hash不变。
2. metrics先9failed，后9passed；部分usage遗漏与错模型误套价格新增2项RED，修正后11passed。完整分母、全部失败成本、未知费用、重复配对均有测试。
3. baseline先4failed，后4passed；单次complete、无反馈重试、公开片段隔离、相同Docker独立验收。
4. runner先5failed，随后4通过/1失败；隔离诊断确认Windows路径过长（WinError206）导致容器日志目录创建失败。短物理目录＋完整UUID计划映射修正后5passed；未改变系统注册表。
5. 结果损坏校验先RED后GREEN；缺失/损坏/中断记录保留分母。审批后Issue变化先RED，版本指纹前后检查后GREEN，避免继续请求模型。
6. CLI与validation辅助工具先RED后GREEN。CI配置先RED，配置与结构校验后GREEN；无模型秘密/--allow-api，不使用pull_request_target，仅contents:read。

## 当前实际检查

- 新评测核心测试（metrics/catalog/baseline/runner/validation/CLI/CI）31passed。
- `patchpilot-eval list`：20任务、12/8拆分、18group，material_errors为空，归一token>=0.9近似候选为空（不等于语义独立证明）。
- `patchpilot-eval validate --output .patchpilot-evals`：20/20 valid。每项固定镜像内正常测试通过、复现失败、参考补丁独立验收通过。
- 验证报告：`.patchpilot-evals/v-aca964e62b00/validation.json`；数据指纹`0f4112ae3e08c845a48a62e454f3c635f5bbc7697bed9b6fbc141f3717f79365`。
- 完整本地回归：`pytest --run-postgres --run-docker` → **382passed/4skipped**，约242秒；跳过仅Windows symlink权限，junction真实执行。
- 所有fixture批次明确标offline-fixture，不算模型成绩；本轮没有真实baseline/Agent A/B分数。
- 沿用权限限制：不自动DROP测试schema，不按查询结果批量删容器；测试数据按既定策略保留。

## 独立审查与修正

独立只读审查发现2项Important，无Critical；4个新增离线用例先RED后GREEN：
1. SDK成功返回响应但核心input/output缺失或null，旧审计把它变0且标完整，可能按免费估计。现分别跟踪responses_received、核心usage完整性、usage_missing_fields；已知部分保留，calls_without_usage包含不完整核心usage，总成本null。
2. served_model_mismatch/version_mismatch仍被算进方向性模型胜负。现end_to_end_paired保留全部端到端配对，paired另列comparable/incomparable及原因，仅同模型/版本且可判分的配对计算方向性结果；主分母不删除任何失败。

审查抽看money/sql/json-lines/unicode契约与验收未见明显矛盾；没有逐项人工审定剩余任务，也未验证真实服务账单或GitHub远程CI，因此不能声称模型效能优势。

## 最终验证

- 主环境：`pytest --run-postgres --run-docker` → **386passed/4skipped**，约260秒。
- 全新非editable环境：确认从site-packages加载，含20项dataset与20项验收资源；同一全量命令 → **386passed/4skipped**，约279秒。
- 移除本次进程所有Anthropic认证/地址环境变量后的普通测试 → **249passed/141skipped**，约93秒；未调用真实模型，数据库/容器集成按显式开关跳过。
- 4项全量跳过仅Windows symlink创建权限，junction测试实际执行。
- Ruff语义与格式通过：127 files already formatted；wheel构建成功，uv.lock注册表主机为pypi.org，不依赖本机私有索引。
- 固定容器完整材料验证20/20有效，报告仍是dataset-validation-not-model-performance，不是模型成功率。
- 本地离线配对格式烟测：`.patchpilot-evals/b-9a8c62c57f7b/summary.md`。baseline替身直接给参考patch、Agent替身故意无修改，结果2/2对0/2只验证格式/空补丁拒绝，报告明确标offline-fixture，绝不是效能对照。
- 最后检查20项原始快照全部load_task通过，无遗留本项目测试容器。
- 按现有权限策略，本项目数据库累计保留834个隔离测试schema（未DROP、未清空卷）。没有新增付费模型请求、Git提交/推送或远程GitHub执行。

所有实现和本地验收已完成；输入仍需用户人工审阅后按指纹批准，真实A/B运行与账单核对尚未发生。

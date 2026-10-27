# 阶段五：可审计评测、单次基线与CI

## 1. 目前能得出什么结论

已经建立20项自建合成任务和可运行的对照设施，固定容器中逐项验证了“正常测试通过、复现失败、参考补丁及独立验收通过”。这说明任务材料与执行链路可用，**不说明模型成功率是20/20，也不说明Agent优于单次生成**。

本阶段没有发起真实批量模型请求。此前唯一真实修复尝试在首个请求遇到provider_error，不能作为成功案例。下面的运行命令需要操作者额外确认发送内容和费用。

## 2. 任务来源、许可与拆分

清单：`src/patchpilot/evaluation/dataset.json`。

- 共20任务，新增15项，覆盖20个类别、18个group；不是把同一个边界缺陷改名20次。
- 开发集12，保留集8。分页/排序、版本更新/幂等等关联group没有跨拆分。
- 全部为AI协助自建场景，没有编造外部Issue链接或生产用户来源。
- 预期行为来自公开API约定，评分检查行为，不要求候选补丁文本模仿参考答案。
- 基准资料采用 `benchmarks/LICENSE` 的MIT许可，范围含列出的任务快照、Issue、参考补丁和对应维护者验收；不等于整个应用已选好开源许可证。
- 当前状态是 `draft_requires_human_review`：可执行测试通过不等于已经独立人工审定。

完整候选Issue与评分规则在 **`benchmarks/REVIEW.md`**。真实评测前请实际阅读，尤其确认公共行为是否合理、是否偏简单、验收有没有遗漏。

保留集不是盲测：构建者已知其内容，开源后别人也能读到参考材料。它只用于防止日常调参直接追分，不保证模型训练无污染。小型数行修复可能让强模型很快饱和，不宜包装成行业权威评测或SWE-bench成绩。

### 资料体检与无模型费用验证

```powershell
uv run --locked patchpilot-eval list --review-path benchmarks/REVIEW.md
uv run --locked patchpilot-eval validate
```

`list`报告拆分、group、类别、材料错误与归一化token近似重复候选；该启发式不是语义重复率证明。当前阈值0.9下未检出候选，不代表任务完全独立。

`validate`需要预构建测试镜像，不需要模型密钥，逐项执行：

1. 从固定快照建立正常测试工作区，要求实际通过；
2. 在另一工作区运行公开复现，要求实际断言失败而非环境/收集错误；
3. 参考补丁在新的验证工作区通过原测试与维护者验收；
4. 检查原始快照摘要未变。

最新完整材料验证记录：`.patchpilot-evals/v-aca964e62b00/validation.json`，20/20 valid，类型明确为dataset-validation-not-model-performance。这个路径是本地运行产物，不随包发布。

## 3. 两种策略比较的是什么

### baseline：固定检索＋一次生成

确定性读取公开目录：最多4份源码、2份复现、2份原测试，每份最多100行，结果截断会标明。将公开Issue和这些片段一次发给模型，要求Git unified diff；可确定性解析一整个diff代码围栏，但不再问第二次。

- 只调用一次模型；没有工具驱动检索，没有测试反馈后的再修改。
- 拒绝、输出截断、协议错误都不会执行本轮工具或自动重试。
- 候选补丁封存后调用与Agent相同的 `verify_patch`。

这些小任务的核心源码通常能全部放进固定检索。基线不是故意拿不到关键文件的弱对手；实际结果可能显示一次生成已经足够，这也是有效结论。

### agent：现有生产入口

直接调用现有 `run_agent`，不重写另一套更强或更弱的评测Agent。模型可以搜索、读取、修改和运行公开测试，并受已有预算限制；最终仍由独立验证器判定，不把隐藏验收反馈给模型刷分。

### 公平性约束

- 相同requested模型、提供方SDK、任务快照、公开Issue、独立验收版本和不可变镜像ID。
- 相同预算上限与每轮输出上限；baseline按定义只用一次请求，不声称两策略实际成本相同。
- 同一任务的策略顺序按任务/重复编号交替，减少固定先后顺序的干扰；不伪造模型随机种子或完全确定性。
- 每次尝试独立工作区，不复用上一策略的文件、补丁或工具观察。
- 对SDK返回的model字段逐次审计，不匹配时在执行工具前停止，标served_model_mismatch。该字段仍是服务报告，不是物理后端权重来源的证明。
- 输入/评分材料在批次前冻结指纹，运行中变化则标version_mismatch，不能悄悄用新Issue或新验收。

## 4. 真实评测必须双重批准

先读取候选材料与评分规则：

```powershell
$inventory = uv run --locked patchpilot-eval list | ConvertFrom-Json
$inventory.dataset_sha256
```

数据指纹涵盖清单、许可、Issue、任务描述、快照、参考补丁和维护者验收。材料变化后需重新审阅并重新批准。

只有确认当前API服务、认证、数据与费用后，才主动执行下列命令；**它会发送公开任务内容并产生费用**：

```powershell
uv run --locked patchpilot-eval run --tasks pagination-001 --strategy both --repeat 1 --allow-api --approve-dataset $inventory.dataset_sha256
```

默认选择development，而不是holdout或all。默认最多40个计划尝试，每任务/策略/重复都算一个；baseline最多1次请求，Agent默认每尝试最多8次。先从1～2项小批次确认服务可用，不要直接对未成功联调的API跑完整集合。

保留集需要额外确认：

```powershell
# 这是额外费用示例，不是普通测试命令。
uv run --locked patchpilot-eval run --split holdout --strategy both --repeat 1 --allow-api --approve-dataset $inventory.dataset_sha256 --confirm-holdout
```

保留集结果一旦被拿来改提示或工具，就不能继续将它描述为未用于调参的保留证据；应建立新的版本和保留集。

没有自动重试、自动切模型或失败后覆盖成功记录。增加repeat会创建全新尝试；失败不能靠只保留最好的一次消失。相同模型仍有随机波动，重复结果相关，当前只做描述统计，不声称统计显著性。

## 5. 批次记录与故障隔离

```text
.patchpilot-evals/b-<短目录编号>/
  plan.json                 完整批次ID、配置、全部trial计划与完整UUID
  summary.json              机器可读统计
  summary.md                普通Markdown表格
  t/0000/result.json        一次尝试的状态、失败原因、版本与用量
  t/0000/model-responses/   SDK完整响应证据，不包含客户端凭据
  t/0000/p/                独立复现预检
  t/0000/w/                baseline或Agent工作区及独立验收产物
```

物理目录缩短是为兼容Windows路径限制，完整独立UUID仍在计划与记录中，不通过目录名猜身份。

计划先写，随后每次结果原子替换落盘。单个任务失败不终止其他计划项，记录包括requested/served模型、Python和核心依赖版本、代码/提示/工具schema/配置/数据集hash、任务/验收hash、实际镜像ID、停止原因与产物相对引用。

重新汇总不调用模型：

本地报告格式样例：`.patchpilot-evals/b-9a8c62c57f7b/summary.md`。它是明确标记的offline-fixture烟测：基线替身被直接提供参考补丁，Agent替身故意不修改代码，以验证空补丁失败和表格生成。因此其中2/2与0/2绝不能被当作模型胜负。

```powershell
uv run --locked patchpilot-eval report <实际批次目录>
```

缺失结果、损坏记录、进行中断开的尝试都留在计划分母，分别标incomplete或record_invalid。report不会偷偷重新执行模型，也不把失败记录覆盖成新成功。

## 6. 成功率与失败分类

主指标是端到端的 `passed_trials / total_trials`，分母为全部计划尝试，包括环境错误、服务错误、超时与未完成项。这衡量系统能否交付，不是纯模型能力分数。

同时另列：
- `scorable_trials`与条件成功率：只用于定位有可判分输出时的表现，不替代主分母；
- 每个重复轮次的成功/总数；
- 任务在全部重复中均成功的数量；
- 至少成功一次的数量（明确不能拿它冒充稳定成功率）；
- `end_to_end_paired`保留全部端到端配对结果，包含基础设施失败；
- `paired`另列有效对照与不可比较数量/原因，只有同模型、同版本且两侧可判分的配对才计算方向性结果。模型或版本不匹配、服务失败等不能被包装成某个模型获胜，主分母仍不删除。

失败类别包含not_reproduced、task_invalid、environment_failed、provider_error、served_model_mismatch、timeout、truncated、refusal、budget_exhausted、invalid_patch、repair_failed、verification_error、version_mismatch、internal_error与incomplete。

例如“API不可用”不能被解读为“模型不会修Bug”；它仍是端到端交付失败，但有独立分类。不能只展示成功案例或删除这些尝试的成本。

## 7. 用量与成本：未知不是免费

未提供价格时，实际模型调用成本为null。请求没有返回usage，或已返回响应但input/output核心计数缺失、为null或非法时，calls_without_usage都会计数；总成本保持未知，不能把0字段解释成零计费。已知部分照常保留，usage_missing_fields与responses_received帮助区分“收到响应”和“用量完整”。可选缓存字段缺失按服务契约视为未报告缓存用量；仍需对照真实账单，不能据此证明平台没有额外费用。

可用 `--pricing <文件>` 显式提供你配置服务的费率，不能默认套用模型原厂价格。格式为：

```json
{
  "model": "与你评测选择一致的模型ID",
  "currency": "USD",
  "version": "你核对的服务报价版本",
  "input_per_million": "填入真实报价",
  "output_per_million": "填入真实报价",
  "cache_read_per_million": "填入真实报价",
  "cache_creation_per_million": "填入真实报价"
}
```

以上是待填写模板，不是可直接使用的报价。费率必须有限、非负；模型不匹配或非零缓存用量没有对应价格时不捏造成本。计划保存定价快照，report默认复用；显式重新定价会在摘要标出新版本。

指标分别是：
- `known_usage_estimated_cost`：已知用量部分的估算；不是完整账单；
- `estimated_total_cost`：只有全部尝试费用可估时才有值；
- `mean_success_attempt_cost`：仅成功尝试自身的平均成本；
- `cost_per_success`：**包含失败尝试成本**的总估计成本除以成功次数。

零次模型调用的已结束环境失败可确认为没有模型用量，但未完成/无用量的请求不能据此当作免费。估算不包含平台额外收费，也不代替服务账单核对。

## 8. CI与本地自动检查

```powershell
uv run --locked pytest
uv run --locked pytest --run-docker --run-postgres
uv run --locked ruff check .
uv run --locked ruff format --check .
uv build --wheel
```

`.github/workflows/ci.yml`提供两层：
- checks：锁定安装、Ruff、无密钥普通测试、wheel；
- integration：临时PostgreSQL、固定测试镜像、真实数据库/容器/故障回归、20项参考补丁验证。

两层都不读取模型秘密、不带--allow-api。workflow权限contents:read，不发布、不推送，也不使用pull_request_target。当前尚未创建远程仓库，**这里只完成配置与本地对应检查，不能称GitHub CI已经实跑通过**。

故意含缺陷的 `benchmarks/tasks/*/repo` 是冻结数据，不由Ruff自动格式化；项目代码、测试和维护者验收仍检查。改动快照必须重新计算任务hash、重验参考补丁并更新审批证据，不能让格式工具悄悄改变原始数据。

继续遵守阶段四权限限制：本地Postgres测试schema保留、不自动DROP；回收器默认不按查询结果批量删容器。GitHub临时服务的生命周期由其隔离runner管理，不以此绕过本地删除授权。

## 9. 当前结论与下一步

当前有材料红绿证据、脚本替身的批次/指标证据和自动检查，但**没有真实baseline对Agent的成绩表**，不能回答增加反馈循环是否值得。

下一步应先修复/确认API服务，人工审阅候选输入与评分，再做小规模真实配对试跑并核对实际账单。若一次生成已足够、Agent多付成本却没收益，应保留这个结论并调整项目方向，而不是挑案例证明Agent更先进。

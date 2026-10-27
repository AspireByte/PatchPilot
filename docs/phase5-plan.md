> 历史资料：本文记录旧固定任务、服务或评测实现，仅供内部迁移与回归参考。旧命令已下线；当前使用方式见 [README](../README.md) 和 [架构说明](architecture.md)。

# 阶段五：可信任务集、对照评测与CI计划

依据用户第29～33步。继续使用现有Agent与独立验收器，不自造LLM裁判、不增加图表/前端，不在本轮默认启动付费评测。

## 设计决定

- 20项自建合成任务：保留原5项，新增15项不同功能缺陷；资料来源标明AI协助新创作、可执行测试验证，未宣称生产真实Issue或独立人工审定。基准资料单独MIT许可，不推定整个项目已选许可证。
- 开发集12、保留集8；关联group不跨split。保留集仅表示不用于调提示，不意味着构建者未知、训练无污染或严格盲测。产出完整Issue审阅文档，真实付费运行前需用户按数据fingerprint明确批准。
- 基线：确定性读取公开源码和复现片段，一次模型生成补丁、一次独立验收；不把测试反馈再送回模型。与Agent同一模型、环境image ID、任务快照、验收版本和预算上限；策略不同、实际调用次数单独报告。
- 批次先写完整计划和独立trial_id，再逐条写结果；失败不终止其他任务，重复独立workspace；不自动重试、不覆盖失败记录。进程中断后的缺失记录仍留在计划分母，摘要标pending/incomplete而不是删除。
- 主指标是包含基础设施失败的端到端成功次数/全部计划次数；另列可判分结果的条件指标，不能把服务错误解释为模型能力失败或暗中删除失败分母。
- 成本只按用户显式定价版本与实际usage估算；无定价/无usage为null，非零假装为0。报告已知用量估计、未知成本条数、成功尝试均值及全成本摊销到成功的成本。
- 记录requested/served模型；返回模型不匹配时在执行工具前阻止本次尝试，标不可比较。记录代码、配置、数据集、task、验收hash及不可变镜像ID。
- 真实CLI需要--allow-api与--approve-dataset HASH，保留集还需要显式确认；普通pytest/CI从不请求模型。当前API服务错误未解决，不声称Agent胜过基线。
- 沿用权限限制：不自动DROP测试schema、不自动批量删查询容器；原执行器清理自身已知容器。

## 文件与顺序

1. 新任务专属目录与tests/test_corpus_a.py、test_corpus_b.py；共享tests/corpus_helpers.py只在临时副本红/绿检查。
2. evaluation/catalog.py＋dataset.json：来源/许可/类别/group/split/预期行为，TaskCatalog与verifier共享登记。benchmarks/LICENSE与REVIEW.md。
3. evaluation/metrics.py：显式定价、全部分母、失败分类、重复差异和配对比较，先单元RED再实现。
4. evaluation/baseline.py：固定检索＋一次complete，保留响应/用量/候选/同一验证器，先SDK替身与Docker验证。
5. evaluation/runner.py：计划、冻结配置、preflight、审计模型、批次/重复/失败隔离、逐项落盘、恢复汇总。
6. evaluation/cli.py及patchpilot-eval入口：validate/list/run/report；无密钥可验证数据，付费入口显式授权。
7. .github/workflows/ci.yml：无密钥普通检查、真实Linux Docker＋PostgreSQL集成；权限contents:read、不发布、不提交、不运行真实模型。
8. 中文评测指南、失败/局限说明、全量测试、非editable/wheel与独立审查修正。

## 验收门

- [x] 20项来源/许可/字段齐全，group拆分不泄漏；全部缺陷红/参考补丁与验收绿。
- [x] 基线只调用一次，失败不重问，隐藏资料不入prompt，与Agent复用验收器。
- [x] 两任务/多重复批次失败不中止，ID与版本绑定，缺失记录不掉分母，served mismatch无工具执行。
- [x] 计费未知不记零，失败成本计入总成本，重复/配对统计可追溯。
- [x] CLI默认无API调用，真实运行授权与数据批准门有效；CI不依赖模型密钥。
- [x] 本地完整测试、真实容器/PG、打包与独立审查；未运行的GitHub CI/付费对照明确标注。

输入集仍标待人工审阅，真实付费A/B结果和GitHub远程CI不在本轮已验证范围。最终证据见phase5-progress.md。

# nearest-rank 分位数选中了更低的样本

## 公共行为约定

- API：`percentile_demo.stats.percentile(values: list[float], p: float) -> float`。
- 样本为非空有限数值列表，元素接受 `int`、`float`，不接受布尔值；`p` 同样只接受非布尔有限数值，范围为 `0 < p <= 100`。
- 使用 nearest-rank 定义：先升序排列，取索引 `ceil(n * p / 100) - 1` 对应的原始样本值。不是线性插值，不对结果求平均。
- 不修改输入列表；重复值、负值与单元素列表均适用。
- 空样本、非数值或非有限样本元素，以及非法 `p` 均抛出 `ValueError`。

## 用户复现

`percentile([10, 20, 30, 40], 60)` 实际返回 20，预期返回 30：4 个样本的 60 分位数应选择第 3 个样本。

运行 `repro/test_nearest_rank.py` 可复现。请保持测试与配置不变。本任务为自建合成场景，无外部 Issue 来源。

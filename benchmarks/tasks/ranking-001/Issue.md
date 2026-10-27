# 榜单平分时标识顺序与约定不符

## 公共行为约定

- API：`ranking_demo.leaderboard.rank_records(records: list[dict]) -> list[dict]`。
- 每条记录至少有整数 `score` 和字符串 `id`；额外字段保留且不参与排序。
- 首先按 `score` 降序，再按 `id` 的 Python 字符串字典序升序；两个排序键完全相同时保持输入顺序。
- 返回新列表，不修改输入列表或记录内容；返回列表中的元素仍是原记录对象，不要求深拷贝。
- 支持负分及任意精度整数；空输入返回新的空列表。

## 用户复现

`rank_records([{"id": "b", "score": 5}, {"id": "a", "score": 5}])` 实际标识顺序是 `["b", "a"]`，预期 `["a", "b"]`。不同分数仍应分高者在前。

复现见 `repro/test_ranking_ties.py`。请保持测试及配置不变。

本样例为自建场景，无外部 Issue 来源；基准资料采用根目录 MIT 许可证。

# 多次重试的等待时间不符合公开序列

## 公共行为约定

- API：`retry_demo.schedule.backoff(attempt: int, *, base: int, cap: int) -> int`。
- `attempt` 从零开始且不得为负；`base`、`cap` 为正整数。任何参数类型不为整数（包括布尔值），或数值范围不符，均抛出 `ValueError`。
- 返回 `min(base * 2**attempt, cap)`；即使 `base > cap`，第一轮也不能超过上限。支持任意精度整数。
- 仅计算整数延迟，不休眠、不读时钟、不执行网络请求。

## 用户复现

`backoff(2, base=3, cap=100)` 实际返回 `9`，预期 `12`；`backoff(4, base=2, cap=20)` 实际返回 `10`，预期 `20`。

复现见 `repro/test_retry_growth.py`。请保持测试及配置不变。

本样例为自建场景，无外部 Issue 来源；基准资料采用根目录 MIT 许可证。

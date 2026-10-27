# 金额在半分位置显示不符合约定

## 公共行为约定

- API：`money_demo.amount.format_money(value: str) -> str`。
- 输入为有限十进制字符串；计算不得先转成浮点数。无效字符串和非有限值抛出 `ValueError`。
- 按十进制 `ROUND_HALF_UP` 舍入到分：恰好半分时远离零，包括负数。返回固定两位小数字符串，保留尾零及十进制负零符号。
- 支持超过默认 Decimal 精度的金额；结果不受调用者 Decimal 精度或舍入模式影响。

## 用户复现

`format_money("1.005")` 实际返回 `"1.00"`，预期 `"1.01"`；`format_money("-1.005")` 实际返回 `"-1.00"`，预期 `"-1.01"`。

复现见 `repro/test_money_midpoint.py`。请保持测试及配置不变。

本样例为自建场景，无外部 Issue 来源；基准资料采用根目录 MIT 许可证。

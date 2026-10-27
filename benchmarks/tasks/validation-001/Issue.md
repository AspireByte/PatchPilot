# 布尔值被当作合法数量接受

## 公共行为约定

- 公共 API 为 `validation_demo.quantity.validate_quantity(value, *, minimum=0, maximum=100) -> int`。
- `value` 必须是整数，可接受非布尔的 `int` 子类；不转换字符串、浮点数或其他类型。
- `True` 和 `False` 均不是合法数量，必须抛出 `ValueError`。
- 上下限为调用者提供的非布尔整数，合法数量范围是闭区间 `[minimum, maximum]`，合法输入原样返回。
- 默认范围为 `[0, 100]`；自定义范围支持负数及任意精度整数。
- 非法类型、越界数量或 `minimum > maximum` 均抛出 `ValueError`。

## 复现

调用 `validate_quantity(True)` 或 `validate_quantity(False)`，实际直接返回对应布尔值；预期抛出 `ValueError`。

可见复现用例位于 `repro/test_boolean_quantity.py`；请保持原有测试和配置不变。

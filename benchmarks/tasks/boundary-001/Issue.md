# 时间窗口返回了终点位置的记录

## 公共行为约定

- 公共 API 为 `boundary_demo.window.select_window(timestamps, *, start, end) -> list[int]`。
- 输入为整数时间戳列表及整数边界；窗口采用半开区间 `[start, end)`，包含起点，不包含终点。
- 返回新列表，保持原输入顺序和重复项，不修改输入列表。
- `start == end` 表示空窗口，应返回空列表；`start > end` 时抛出 `ValueError`。
- 支持负整数和任意精度整数，不读取当前时间。

## 复现

调用 `select_window([9, 10, 19, 20, 21], start=10, end=20)`，实际返回 `[10, 19, 20]`，预期返回 `[10, 19]`。

可见复现用例位于 `repro/test_end_timestamp.py`；请保持原有测试和配置不变。

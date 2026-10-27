# 候选基准输入与评分规则审阅

PatchPilot contributors 自建，AI协助编写；以可执行行为测试核验，尚未独立人工逐项审定；无外部真实Issue来源。

资料许可：benchmarks/LICENSE（MIT，仅基准资料）。当前是待人工审阅候选，不假定已获用户批准。

开发12项、保留8项，关联group不跨拆分；保留不等于盲测。

## 评分规则

- 检查候选最终行为，不要求补丁文字等同参考答案。
- 固定base的新工作区应用封存补丁，在新容器运行原测试和维护者验收。
- 原测试/配置/依赖不可改；无实际测试、空补丁和异常不能冒充通过。
- 环境/服务失败保留在主分母，条件能力指标另列。
- 测试不是正确性证明，仍需人工审查特判与设计质量。

| ID | 拆分 | group | 预期行为 |
| --- | --- | --- | --- |
| pagination-001 | development | ordering | 按时间与编号的字典序分页，不重复或遗漏同时间戳记录。 |
| boundary-001 | development | range-selection | 半开区间[start,end)，保持顺序和重复，不改输入。 |
| null-001 | development | optional-values | 仅None使用默认标签，保留合法空字符串和空白。 |
| validation-001 | development | type-validation | 数量接受合法整数与子类，拒绝bool、非整数及闭区间外值。 |
| state-001 | development | state-transition | 版本匹配时更新并递增，陈旧更新不改变状态。 |
| money-001 | development | decimal-rounding | 有限十进制金额ROUND_HALF_UP到两位，不受调用者Decimal上下文影响。 |
| path-001 | holdout | path-containment | 按纯POSIX路径段判定归属，相似前缀兄弟目录不属于根目录。 |
| retry-001 | development | retry-policy | 指数退避min(base*2**attempt,cap)，非法参数拒绝。 |
| merge-001 | development | data-ownership | 递归合并配置，不修改输入且不共享输入可变后代。 |
| ranking-001 | development | ordering | 按score降序和id升序稳定排序，不修改输入。 |
| batching-001 | holdout | batch-packing | 按容量顺序贪心分批，不产生空批或溢出，非法条目拒绝。 |
| sql-001 | holdout | sql-predicate | 只返回active且owner匹配或公开的记录，使用参数绑定。 |
| idempotency-001 | development | state-transition | 回调成功后登记请求ID，失败可重试，成功重复不再执行。 |
| header-001 | development | header-lookup | ASCII头名不区分大小写查询，首匹配优先且保留原值。 |
| datetime-001 | holdout | timezone | 有时区ISO时间转换UTC并保留瞬间与微秒，无时区拒绝。 |
| json-lines-001 | development | line-provenance | 跳过空白JSONL行，首错报告原始物理行号及此前记录。 |
| unicode-001 | holdout | unicode-normalization | NFC与casefold去重，保留首次原文和顺序。 |
| percentile-001 | holdout | quantile | nearest-rank上取整分位数，不插值、不改输入，非法请求拒绝。 |
| url-path-001 | holdout | url-encoding | 逐段URL路径转义，段内斜杠、空格、问号和Unicode正确编码。 |
| csv-export-001 | holdout | csv-safety | 忽略前导空白检测公式前缀，在完整原值前加引号，普通值保留。 |

## pagination-001

来源：synthetic；许可：MIT；拆分：development

````text
# 分页在相同时间戳的记录之间漏项

## 公共行为约定

- 记录编号唯一，`created_at` 使用整数表示时间。
- 分页按 `(created_at, id)` 升序返回；输入列表可以无序，且不得被修改。
- 游标为上一页最后一条记录的 `(created_at, id)`。
- 游标表示严格的后续位置，遍历所有页面不应重复或遗漏记录。
- `limit` 必须为正整数；没有后续记录时返回空列表。

## 复现

记录为 `(id=3, created_at=101)`、`(id=2, created_at=100)`、`(id=1, created_at=100)`，每页数量为 1。

第一页得到编号 1，将它的位置作为游标请求第二页，实际得到编号 3，编号 2 没有返回。

预期连续翻页得到编号 1、2、3，之后返回空列表。

可见复现用例位于 `repro/test_same_timestamp.py`；请保持原有测试和配置不变。
````

## boundary-001

来源：synthetic；许可：MIT；拆分：development

````text
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
````

## null-001

来源：synthetic；许可：MIT；拆分：development

````text
# 显式空标签未被保留

## 公共行为约定

- 公共 API 为 `null_demo.labels.resolve_label(value, *, fallback="未命名") -> str`。
- `value` 只接受字符串或 `None`；`fallback` 为字符串。
- `None` 表示缺失，返回 `fallback`；未提供备用值时返回 `"未命名"`。
- 任何字符串都是合法标签，包括空字符串、纯空白和中文；应原样返回，不裁剪、不替换。
- 备用值也可以是空字符串。调用之间互不影响。

## 复现

调用 `resolve_label("", fallback="备用")`，实际返回 `"备用"`，预期返回 `""`。

可见复现用例位于 `repro/test_empty_label.py`；请保持原有测试和配置不变。
````

## validation-001

来源：synthetic；许可：MIT；拆分：development

````text
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
````

## state-001

来源：synthetic；许可：MIT；拆分：development

````text
# 成功更新后无法按新版本继续更新

## 公共行为约定

- 公共 API 为 `state_demo.versioned.VersionedValue(value: str, version: int = 0)`，公开读取 `value` 和 `version` 字段。
- 初始版本由调用者提供，为非负整数；默认是 `0`。调用者不直接修改字段。
- `update(new_value: str, *, expected_version: int) -> bool` 使用整数期望版本进行匹配。
- 期望版本等于当前版本时，保存新字符串，当前版本递增 `1`，返回 `True`。
- 即使新旧字符串相同，匹配成功也属于一次更新，版本仍递增。
- 期望版本不匹配时返回 `False`，值和版本都保持原样；陈旧版本不能覆盖已成功的更新。
- 各实例互不影响；只要求单线程、顺序调用，不涉及持久化或网络。

## 复现

创建 `VersionedValue("初始")` 后，调用 `update("第一次", expected_version=0)` 返回 `True`。

实际状态为 `("第一次", 0)`，预期状态为 `("第一次", 1)`；再次使用期望版本 `0` 应返回 `False` 并保持状态。

可见复现用例位于 `repro/test_version_progress.py`；请保持原有测试和配置不变。
````

## money-001

来源：synthetic；许可：MIT；拆分：development

````text
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
````

## path-001

来源：synthetic；许可：MIT；拆分：holdout

````text
# 相似名称的目录被判定为子路径

## 公共行为约定

- API：`path_demo.membership.relative_member(root: str, candidate: str) -> str | None`。
- 这是区分大小写的纯 POSIX 路径计算，不读取、解析或写入真实文件系统，也不处理符号链接。
- 两个输入必须为单根绝对路径；相对路径、双斜杠根及任何 `..` 路径段抛出 `ValueError`。重复分隔符、`.` 和尾部斜杠按路径段规范化。
- 只有完整路径段相同才构成归属。目录本身返回 `""`，严格子路径返回无前导或尾部斜杠的相对路径，其余返回 `None`。根目录 `/` 包含所有合法输入。

## 用户复现

`relative_member("/root", "/root-other/item")` 实际返回 `"-other/item"`；`relative_member("/root", "/rooted")` 实际返回 `"ed"`。两者预期均为 `None`。

复现见 `repro/test_path_prefix.py`。请保持测试及配置不变。

本样例为自建场景，无外部 Issue 来源；基准资料采用根目录 MIT 许可证。
````

## retry-001

来源：synthetic；许可：MIT；拆分：development

````text
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
````

## merge-001

来源：synthetic；许可：MIT；拆分：development

````text
# 合并后的配置变动影响了原配置

## 公共行为约定

- API：`merge_demo.config.merge_config(base: dict, overrides: dict) -> dict`。
- 输入为无环的、字符串键配置字典；值限字典、列表及不可变标量（字符串、数字、布尔值、`None`）。
- 同一个键两边都是字典时递归合并；其他冲突由 `overrides` 的整个值替换，列表不拼接。保留没有被覆盖的键。
- 返回新字典，调用本身不修改任何输入；返回结果的所有可变后代均不得与任一输入共享。之后修改任一输入也不得影响已返回结果。不同调用的结果相互独立。

## 用户复现

`base = {"service": {"labels": ["base"]}}`，调用 `merged = merge_config(base, {})` 后执行 `merged["service"]["labels"].append("result")`，原 `base` 的列表实际也多出 `"result"`，预期保持 `["base"]`。覆盖配置中的列表也出现相同行为。

复现见 `repro/test_merge_aliasing.py`。请保持测试及配置不变。

本样例为自建场景，无外部 Issue 来源；基准资料采用根目录 MIT 许可证。
````

## ranking-001

来源：synthetic；许可：MIT；拆分：development

````text
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
````

## batching-001

来源：synthetic；许可：MIT；拆分：holdout

````text
# 连续分批结果超过容量

## 公共行为约定

- API：`batching_demo.packer.batch_sizes(sizes: list[int], capacity: int) -> list[list[int]]`。
- `capacity` 和每个条目均为正整数，布尔值不算整数。非法类型、零、负数或任一条目大于容量都抛出 `ValueError`；空输入也要校验容量。
- 按原顺序连续贪心分批：如果加入下一条会使当前批总和超过容量，应先结束当前批，再将条目放入下一批。恰好等于容量可以放入。
- 不重排、不拆条目、不回填先前批次。每批非空且总和不超过容量；空输入返回 `[]`。
- 返回新列表及新批次列表，不修改输入。

## 用户复现

`batch_sizes([4, 3], 5)` 实际返回 `[[4, 3]]`，预期 `[[4], [3]]`；`batch_sizes([2, 3, 4], 6)` 实际返回 `[[2, 3, 4]]`，预期 `[[2, 3], [4]]`。

复现见 `repro/test_batching_overflow.py`。请保持测试及配置不变。

本样例为自建场景，无外部 Issue 来源；基准资料采用根目录 MIT 许可证。
````

## sql-001

来源：synthetic；许可：MIT；拆分：holdout

````text
# 停用的公开记录出现在可见列表中

## 公共行为约定

- API：`sql_demo.visibility.visible_ids(records: list[tuple[int, str, int, int]], owner: str) -> list[int]`。
- 每条自建记录依次为唯一整数 `id`、字符串 `owner`、取值为 `0` 或 `1` 的 `active`、`public`。输入符合此结构。
- 仅返回满足“已启用，并且（所属者精确匹配查询用户，或者记录公开）”的记录；停用记录始终不可见。字符串精确区分大小写。
- 返回整数标识升序列表，不修改输入；空输入返回空列表。
- 每次调用使用独立 `sqlite3` 内存数据库并释放连接，插入和所属者查询使用参数绑定。不得连接外部数据库或写入文件。

## 用户复现

`visible_ids([(7, "bob", 0, 1)], "alice")` 实际返回 `[7]`，预期 `[]`。记录所属者与查询用户相同时，停用的公开记录同样不应返回。

复现见 `repro/test_sql_inactive.py`。请保持测试及配置不变。

本样例为自建场景，无外部 Issue 来源；基准资料采用根目录 MIT 许可证。
````

## idempotency-001

来源：synthetic；许可：MIT；拆分：development

````text
# 回调失败后同一请求无法重试

## 公共行为约定

- API：`idempotency_demo.gate.RequestGate()`，以及 `gate.run(request_id: str, callback: Callable[[], object]) -> bool`。
- 适用于单线程、非重入的同步调用。标识为字符串（允许空字符串），精确区分不同内容；各实例状态独立且仅保存在内存。
- 尚未成功完成的标识应执行无参数回调；回调正常返回后登记成功并返回 `True`，不使用回调返回值判断成功。
- 回调抛出的异常原样传播，失败不消耗标识，之后可使用同一标识再次尝试；不回滚回调自身已经发生的副作用。
- 已经成功的标识直接返回 `False`，不得再次执行回调。不涉及持久化或并发保证。

## 用户复现

令同一个回调第一次调用抛出 `RuntimeError`、第二次正常返回。首次 `gate.run("request-1", callback)` 的异常被调用者捕获后，再次调用相同标识实际直接返回 `False`，回调总共只执行一次。预期第二次执行回调并返回 `True`，总共执行两次。

复现见 `repro/test_idempotency_failure.py`，覆盖两种异常。请保持测试及配置不变。

本样例为自建场景，无外部 Issue 来源；基准资料采用根目录 MIT 许可证。
````

## header-001

来源：synthetic；许可：MIT；拆分：development

````text
# 同一个 HTTP 头使用不同大小写时查不到值

## 公共行为约定

- API：`header_demo.headers.get_header(headers, name, default=None) -> str | None`。
- `headers` 是保持迭代顺序的字符串映射，键和查询名称均为非空 ASCII HTTP 头名称，值为任意字符串；默认值为字符串或 `None`。
- 名称按 ASCII 不区分大小写匹配；如多个键仅大小写不同，返回映射迭代顺序中的第一个匹配值。
- 未匹配时原样返回默认值；空值字符串是有效结果，不应使用默认值替换。
- 不修改映射、键或值，不裁剪或规范化返回值。

## 用户复现

`get_header({"Content-Type": "application/json"}, "CONTENT-TYPE")` 返回 `None`，预期为 `"application/json"`。

运行 `repro/test_header_case.py` 可复现。请保持测试与配置不变。本任务为自建合成场景，无外部 Issue 来源。
````

## datetime-001

来源：synthetic；许可：MIT；拆分：holdout

````text
# 带偏移量的时间转 UTC 后瞬间改变

## 公共行为约定

- API：`datetime_demo.timestamps.to_utc(value: str) -> datetime`。
- 接受 Python `datetime.fromisoformat` 支持的 ISO 8601 字符串，包括末尾大写 `Z` 和显式时区偏移量。
- 返回 `tzinfo is datetime.timezone.utc` 的时间，必须与输入表示同一瞬间，正确处理跨日、跨年与非整小时时区，并保留微秒。
- 缺少时区的字符串和无法解析的字符串均抛出 `ValueError`；不依赖运行机器的本地时区。

## 用户复现

`to_utc("2025-01-01T01:02:03.123456+08:00").isoformat()` 返回 `"2025-01-01T01:02:03.123456+00:00"`，预期为 `"2024-12-31T17:02:03.123456+00:00"`。

运行 `repro/test_offset_conversion.py` 可复现。请保持测试与配置不变。本任务为自建合成场景，无外部 Issue 来源。
````

## json-lines-001

来源：synthetic；许可：MIT；拆分：development

````text
# JSONL 错误位置与输入文本行号不一致

## 公共行为约定

- API：`json_lines_demo.parser.parse_json_lines(text: str) -> dict`，仅处理内存字符串，不访问文件。
- 使用 `str.splitlines()` 分行，原始行号从 1 开始；`line.strip()` 为空的行被跳过，但仍占物理行号。
- 每个非空行按 `json.loads` 的语义解析，允许对象、数组与标量，成功记录按输入顺序保留。
- 全部成功返回 `{"records": [...], "error": None}`。
- 首个无效 JSON 行出现时立即停止，返回此前的成功记录，以及 `{"line": 原始物理行号, "message": "无效 JSON"}` 形式的 `error`。不向调用者抛出 JSON 解析异常，不继续处理后续行。

## 用户复现

输入 `"\n{\"x\":1}\n坏行"`，结果中的错误行号为 2；预期 `records` 为 `[{"x": 1}]`，`error` 为 `{"line": 3, "message": "无效 JSON"}`。

运行 `repro/test_physical_lines.py` 可复现。请保持测试与配置不变。本任务为自建合成场景，无外部 Issue 来源。
````

## unicode-001

来源：synthetic；许可：MIT；拆分：holdout

````text
# 规范等价的 Unicode 字符串仍被保留为两项

## 公共行为约定

- API：`unicode_demo.dedupe.unique_strings(values: list[str]) -> list[str]`。
- 去重身份严格定义为 `unicodedata.normalize("NFC", value).casefold()`；两项身份相同即视为重复，不使用兼容规范化 NFKC，不裁剪空白。
- 按输入顺序返回每个身份首次出现的原始字符串，不能用规范化结果替换输出。
- 输入列表保持不变；空列表返回新空列表，调用之间不共享去重状态。

## 用户复现

`unique_strings(["Café", "CAFÉ"])` 返回两项，预期只返回 `["Café"]`。

运行 `repro/test_canonical_duplicates.py` 可复现。请保持测试与配置不变。本任务为自建合成场景，无外部 Issue 来源。
````

## percentile-001

来源：synthetic；许可：MIT；拆分：holdout

````text
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
````

## url-path-001

来源：synthetic；许可：MIT；拆分：holdout

````text
# 路径段内的斜杠被误当成段分隔符

## 公共行为约定

- API：`url_path_demo.paths.build_path(segments: list[str]) -> str`，仅构造字符串，不联网。
- 每个输入字符串视为不可分割的一段，编码结果严格等同于对该段调用 `urllib.parse.quote(segment, safe="")`，使用默认 UTF-8 编码。
- 编码后以 `/` 连接，再添加一个开头的 `/`；段内的斜杠、空格、问号、百分号、Unicode 等需要百分号编码，标准未保留字符保持原样。
- 空列表返回 `/`；空段仍保留其位置，不裁剪、不解析已有百分号编码，不合并或规范化路径段。
- 不修改输入列表。

## 用户复现

`build_path(["projects", "a/b"])` 实际返回 `"/projects/a/b"`，预期返回 `"/projects/a%2Fb"`，因为 `"a/b"` 是一个完整段。

运行 `repro/test_segment_slashes.py` 可复现。请保持测试与配置不变。本任务为自建合成场景，无外部 Issue 来源。
````

## csv-export-001

来源：synthetic；许可：MIT；拆分：holdout

````text
# 前导空白使导出文本绕过公式标记

## 公共行为约定

- API：`csv_export_demo.cells.escape_cell(value: str) -> str`，输入与输出均为文本单元格，不负责 CSV 引号或分隔符编码。
- 按 Python `str.lstrip()` 的空白定义，忽略前导空白后，如首字符是 `=`、`+`、`-`、`@` 中任意一个，在完整原值之前添加一个 ASCII 单引号 `'`。
- 原值必须逐字符保留，包括前导空白、换行、Unicode 与末尾内容；只用忽略空白后的文本进行危险前缀判定，不裁剪输出。
- 其余字符串原样返回，包括空字符串、纯空白、普通文本与已由单引号开头的文本。重复调用不额外叠加单引号。
- 函数只处理字符串，不打开电子表格、不执行公式、不访问文件。

## 用户复现

`escape_cell("  =1+2")` 实际返回 `"  =1+2"`，预期返回 `"'  =1+2"`，保留两个前导空格但在整个原值前添加单引号。

运行 `repro/test_leading_whitespace.py` 可复现。请保持测试与配置不变。本任务为自建合成场景，无外部 Issue 来源。
````

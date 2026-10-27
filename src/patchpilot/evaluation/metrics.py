"""完整分母、失败分层与显式定价；未知费用不能当作零。"""

import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
)
SCORABLE_FAILURES = {"repair_failed", "invalid_patch", "refusal"}


@dataclass(frozen=True)
class Pricing:
    model: str
    currency: str
    version: str
    input_per_million: Decimal | str
    output_per_million: Decimal | str
    cache_read_per_million: Decimal | str | None = None
    cache_creation_per_million: Decimal | str | None = None

    def __post_init__(self):
        if not self.model or not self.version or re.fullmatch(r"[A-Z]{3}", self.currency) is None:
            raise ValueError("定价需要模型、币种与版本")
        for field in (
            "input_per_million",
            "output_per_million",
            "cache_read_per_million",
            "cache_creation_per_million",
        ):
            value = getattr(self, field)
            if value is not None:
                try:
                    parsed = Decimal(str(value))
                except InvalidOperation as error:
                    raise ValueError("定价必须是有限非负数") from error
                if not parsed.is_finite() or parsed < 0:
                    raise ValueError("定价必须是有限非负数")
                object.__setattr__(self, field, parsed)


def _decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.normalize(), "f")


def estimate_cost(record: dict, pricing: Pricing | None) -> Decimal | None:
    if (
        record.get("status") not in ("passed", "failed")
        or not record.get("usage_complete", False)
        or record.get("calls_without_usage", 0)
    ):
        return None
    return known_usage_cost(record, pricing)


def known_usage_cost(record: dict, pricing: Pricing | None) -> Decimal | None:
    if record.get("model_calls") == 0:
        return Decimal(0)
    if (
        pricing is None
        or record.get("model_requested") != pricing.model
        or any(model != pricing.model for model in record.get("model_served", []))
    ):
        return None
    usage = record.get("usage", {})
    total = Decimal(0)
    rates = (
        pricing.input_per_million,
        pricing.output_per_million,
        pricing.cache_read_per_million,
        pricing.cache_creation_per_million,
    )
    for field, rate in zip(TOKEN_FIELDS, rates, strict=True):
        count = usage.get(field, 0)
        if type(count) is not int or count < 0:
            raise ValueError("用量必须为非负整数")
        if count and rate is None:
            return None
        if rate is not None:
            total += Decimal(count) * rate / Decimal(1000000)
    return total


def _group(rows: list[dict], pricing: Pricing | None) -> dict:
    total = len(rows)
    passed = sum(row["status"] == "passed" for row in rows)
    failures = Counter(
        row.get("failure_category") or "incomplete" for row in rows if row["status"] != "passed"
    )
    scorable = [
        row
        for row in rows
        if row["status"] == "passed" or row.get("failure_category") in SCORABLE_FAILURES
    ]
    costs = [estimate_cost(row, pricing) for row in rows]
    known = [cost for cost in costs if cost is not None]
    total_cost = sum(known, Decimal(0)) if len(known) == total else None
    successful_costs = [
        cost for row, cost in zip(rows, costs, strict=True) if row["status"] == "passed"
    ]
    mean_success = (
        sum(successful_costs, Decimal(0)) / len(successful_costs)
        if successful_costs and all(cost is not None for cost in successful_costs)
        else None
    )
    times = [row["elapsed_seconds"] for row in rows if row.get("elapsed_seconds") is not None]
    if any(
        not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
        for value in times
    ):
        raise ValueError("耗时必须是有限非负数")
    by_task = defaultdict(list)
    by_repeat = defaultdict(list)
    for row in rows:
        by_task[row["task_id"]].append(row)
        by_repeat[row.get("repeat", 0)].append(row)
    return {
        "total_trials": total,
        "finished_trials": sum(row["status"] in ("passed", "failed") for row in rows),
        "passed_trials": passed,
        "success_rate": passed / total if total else None,
        "failure_counts": dict(sorted(failures.items())),
        "scorable_trials": len(scorable),
        "conditional_scorable_success_rate": passed / len(scorable) if scorable else None,
        "unique_tasks": len(by_task),
        "tasks_all_repeats_passed": sum(
            all(row["status"] == "passed" for row in group) for group in by_task.values()
        ),
        "tasks_at_least_once_passed": sum(
            any(row["status"] == "passed" for row in group) for group in by_task.values()
        ),
        "unknown_cost_trials": sum(cost is None for cost in costs),
        "known_usage_estimated_cost": _decimal(
            sum(
                (cost for row in rows if (cost := known_usage_cost(row, pricing)) is not None),
                Decimal(0),
            )
        ),
        "estimated_total_cost": _decimal(total_cost),
        "mean_success_attempt_cost": _decimal(mean_success),
        "cost_per_success": _decimal(
            total_cost / passed if total_cost is not None and passed else None
        ),
        "latency_seconds": {
            "count": len(times),
            "mean": statistics.mean(times) if times else None,
            "median": statistics.median(times) if times else None,
            "min": min(times) if times else None,
            "max": max(times) if times else None,
            "stddev": statistics.pstdev(times) if times else None,
        },
        "repeats": [
            {
                "repeat": index,
                "total_trials": len(group),
                "passed_trials": sum(row["status"] == "passed" for row in group),
            }
            for index, group in sorted(by_repeat.items())
        ],
        "tasks": [
            {
                "task_id": name,
                "trials": len(group),
                "passed": sum(row["status"] == "passed" for row in group),
            }
            for name, group in sorted(by_task.items())
        ],
    }


def summarize(records: list[dict], pricing: Pricing | None = None) -> dict:
    slots = set()
    groups = defaultdict(list)
    paired = defaultdict(dict)
    for row in records:
        key = (row["strategy"], row["task_id"], row.get("repeat", 0))
        if key in slots:
            raise ValueError("同一任务/策略/重复位置出现重复记录")
        if row["status"] not in ("pending", "running", "passed", "failed"):
            raise ValueError("未知评测状态")
        slots.add(key)
        groups[row["strategy"]].append(row)
        paired[(row["task_id"], row.get("repeat", 0))][row["strategy"]] = row
    comparisons = Counter()
    compatible = Counter()
    incomparable_reasons = Counter()
    for pair in paired.values():
        if "baseline" not in pair or "agent" not in pair:
            comparisons["unpaired_slots"] += 1
            continue
        comparisons["total_pairs"] += 1
        left, right = pair["baseline"], pair["agent"]
        a, b = left["status"] == "passed", right["status"] == "passed"
        outcome = (
            "both_passed"
            if a and b
            else "baseline_only_passed"
            if a
            else "agent_only_passed"
            if b
            else "neither_passed"
        )
        comparisons[outcome] += 1
        if any(row["status"] not in ("passed", "failed") for row in pair.values()):
            comparisons["incomplete_pairs"] += 1
        reasons = set()
        for row in (left, right):
            if row["status"] != "passed" and row.get("failure_category") not in SCORABLE_FAILURES:
                reasons.add(row.get("failure_category") or "incomplete")
            if any(model != row.get("model_requested") for model in row.get("model_served", [])):
                reasons.add("served_model_mismatch")
        for field in (
            "model_requested",
            "task_snapshot_sha256",
            "acceptance_sha256",
            "image_id",
            "config_sha256",
            "code_sha256",
            "dataset_sha256",
        ):
            if left.get(field) != right.get(field):
                reasons.add("pair_metadata_mismatch")
        if reasons:
            compatible["incomparable_pairs"] += 1
            incomparable_reasons.update(reasons)
        else:
            compatible["comparable_pairs"] += 1
            compatible[outcome] += 1
    return {
        "denominator_policy": "all_planned_trials_including_errors_and_incomplete",
        "cost_policy": "explicit_rates_times_known_usage_not_invoice",
        "currency": pricing.currency if pricing else None,
        "pricing_version": pricing.version if pricing else None,
        "strategies": {name: _group(rows, pricing) for name, rows in sorted(groups.items())},
        "end_to_end_paired": {
            "scope": "all_planned_pairs_including_errors",
            **{
                name: comparisons[name]
                for name in (
                    "total_pairs",
                    "both_passed",
                    "baseline_only_passed",
                    "agent_only_passed",
                    "neither_passed",
                    "incomplete_pairs",
                    "unpaired_slots",
                )
            },
        },
        "paired": {
            "scope": "same_model_versions_and_scorable_outcomes",
            "total_pairs": comparisons["total_pairs"],
            "unpaired_slots": comparisons["unpaired_slots"],
            "comparable_pairs": compatible["comparable_pairs"],
            "incomparable_pairs": compatible["incomparable_pairs"],
            "incomparable_reasons": dict(sorted(incomparable_reasons.items())),
            **{
                name: compatible[name]
                for name in (
                    "both_passed",
                    "baseline_only_passed",
                    "agent_only_passed",
                    "neither_passed",
                )
            },
        },
        "limitations": [
            "小样本合成任务不等同真实生产分布",
            "重复运行相关，仅报告描述统计，不声称显著性",
            "服务/环境失败计入端到端分母，条件能力指标另列",
            "费用为显式定价估计，不是服务账单",
        ],
    }


def markdown_summary(summary: dict) -> str:
    lines = [
        "# 评测摘要",
        "",
        "主分母包含全部计划尝试，环境/服务失败和未完成记录不删除。",
        "",
        "| 策略 | 成功/总尝试 | 全重复成功任务/任务数 | 未知成本尝试 | 估计总成本 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for name, group in summary["strategies"].items():
        cost = (
            group["estimated_total_cost"] if group["estimated_total_cost"] is not None else "未知"
        )
        lines.append(
            f"| {name} | {group['passed_trials']}/{group['total_trials']} | {group['tasks_all_repeats_passed']}/{group['unique_tasks']} | {group['unknown_cost_trials']} | {cost} |"
        )
    lines += [
        "",
        "费用单位：" + (summary["currency"] or "未指定") + "；这是用量估算，不是账单。",
        f"有效对照配对：{summary['paired']['comparable_pairs']}/{summary['paired']['total_pairs']}；不可比较：{summary['paired']['incomparable_pairs']}。不可比较尝试仍保留在端到端主分母，但不计作模型胜负。",
        "",
        *[f"- {item}" for item in summary["limitations"]],
    ]
    return "\n".join(lines) + "\n"

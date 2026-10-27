import importlib
from decimal import Decimal

import pytest


def api():
    assert importlib.util.find_spec("patchpilot.evaluation"), "尚未实现评测模块"
    return importlib.import_module("patchpilot.evaluation.metrics")


def record(
    task="a", strategy="agent", repeat=0, status="passed", category=None, usage=None, **kwargs
):
    return {
        "trial_id": f"{strategy}-{task}-{repeat}",
        "task_id": task,
        "strategy": strategy,
        "repeat": repeat,
        "status": status,
        "failure_category": category,
        "model_requested": "model-test",
        "model_calls": 1,
        "calls_without_usage": 0,
        "usage_complete": True,
        "usage": usage or {"input_tokens": 100, "output_tokens": 20},
        "elapsed_seconds": 2.0,
        **kwargs,
    }


def pricing():
    return api().Pricing("model-test", "USD", "example-v1", "1", "2", "0.1", "1.25")


def test_all_failures_and_incomplete_trials_remain_in_the_denominator():
    rows = [
        record("a"),
        record("b", status="failed", category="environment_failed"),
        record("c", status="pending", usage_complete=False),
    ]
    summary = api().summarize(rows)
    group = summary["strategies"]["agent"]
    assert group["total_trials"] == 3 and group["passed_trials"] == 1
    assert group["failure_counts"] == {"environment_failed": 1, "incomplete": 1}
    assert group["success_rate"] == pytest.approx(1 / 3)
    assert group["scorable_trials"] == 1


def test_known_usage_cost_includes_cache_and_is_decimal():
    row = record(
        usage={
            "input_tokens": 1000000,
            "output_tokens": 2000000,
            "cache_read_input_tokens": 1000000,
            "cache_creation_input_tokens": 1000000,
        }
    )
    assert api().estimate_cost(row, pricing()) == Decimal("6.35")


def test_missing_pricing_or_usage_is_unknown_not_free():
    row = record(calls_without_usage=1, usage_complete=False)
    assert api().estimate_cost(row, pricing()) is None
    assert api().estimate_cost(record(), None) is None
    summary = api().summarize([row], pricing())
    assert summary["strategies"]["agent"]["estimated_total_cost"] is None
    assert summary["strategies"]["agent"]["unknown_cost_trials"] == 1


def test_partial_known_usage_is_reported_without_fabricating_total_cost():
    row = record(
        status="failed",
        category="provider_error",
        calls_without_usage=1,
        usage_complete=False,
        usage={"input_tokens": 1000000, "output_tokens": 0},
    )
    group = api().summarize([row], pricing())["strategies"]["agent"]
    assert group["estimated_total_cost"] is None
    assert group["known_usage_estimated_cost"] == "1"


def test_wrong_served_model_cannot_use_the_requested_model_price():
    row = record(model_served=["different-model"])
    assert api().estimate_cost(row, pricing()) is None


def test_failed_attempt_cost_is_included_in_cost_per_success():
    rows = [
        record("a", usage={"input_tokens": 1000000, "output_tokens": 0}),
        record(
            "b",
            status="failed",
            category="repair_failed",
            usage={"input_tokens": 2000000, "output_tokens": 0},
        ),
    ]
    group = api().summarize(rows, pricing())["strategies"]["agent"]
    assert group["estimated_total_cost"] == "3"
    assert group["cost_per_success"] == "3"
    assert group["mean_success_attempt_cost"] == "1"


def test_repetitions_and_paired_outcomes_are_not_cherry_picked():
    rows = [
        record("a", "baseline", 0),
        record("a", "agent", 0),
        record("a", "baseline", 1, status="failed", category="repair_failed"),
        record("a", "agent", 1, status="failed", category="provider_error"),
    ]
    summary = api().summarize(rows)
    assert summary["strategies"]["agent"]["tasks_all_repeats_passed"] == 0
    assert summary["strategies"]["agent"]["tasks_at_least_once_passed"] == 1
    assert len(summary["strategies"]["agent"]["repeats"]) == 2
    assert summary["paired"]["both_passed"] == 1
    assert summary["end_to_end_paired"]["neither_passed"] == 1
    assert summary["paired"]["neither_passed"] == 0
    assert summary["paired"]["incomparable_pairs"] == 1
    assert summary["paired"]["total_pairs"] == 2


@pytest.mark.parametrize("category", ["served_model_mismatch", "version_mismatch"])
def test_incomparable_pairs_are_not_counted_as_directional_model_wins(category):
    rows = [record("a", "baseline", status="failed", category=category), record("a", "agent")]
    report = api().summarize(rows)
    assert report["strategies"]["baseline"]["total_trials"] == 1
    assert report["paired"]["incomparable_pairs"] == 1
    assert report["paired"]["agent_only_passed"] == 0
    assert report["end_to_end_paired"]["agent_only_passed"] == 1


def test_duplicate_trial_slots_are_rejected():
    with pytest.raises(ValueError):
        api().summarize([record(), record()])


@pytest.mark.parametrize("price", ["-1", "NaN", "Infinity"])
def test_invalid_prices_are_rejected(price):
    with pytest.raises(ValueError):
        api().Pricing("model-test", "USD", "example-v1", price, "2", "0.1", "1.25")

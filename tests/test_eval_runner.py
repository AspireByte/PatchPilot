import importlib
import json
from pathlib import Path

import pytest
from test_agent import FakeModel, call, reply

from patchpilot.agent import AgentBudget, ModelFailure
from patchpilot.anthropic_model import DEFAULT_MODEL
from patchpilot.sandbox import TestResult as Result

ROOT = Path(__file__).resolve().parents[1]


def api():
    assert importlib.util.find_spec("patchpilot.evaluation.runner"), "尚未实现批量评测入口"
    return importlib.import_module("patchpilot.evaluation.runner")


class FixtureModel(FakeModel):
    def complete(self, **kwargs):
        result = super().complete(**kwargs)
        return result.model_copy(update={"model": DEFAULT_MODEL})


class ReproducingRunner:
    def __init__(self, image):
        self.image = image

    def check(self):
        return {"image_id": "sha256:" + "a" * 64, "engine": "linux"}

    def run(self, workspace, **kwargs):
        log = workspace.artifacts / "fake.log"
        log.write_text("fixture", encoding="utf-8")
        return Result(
            "failed",
            1,
            1,
            1,
            0,
            0,
            0.01,
            "sha256:" + "a" * 64,
            "fixture",
            str(log),
            None,
            True,
            "fixture",
        )


def test_real_eval_requires_explicit_api_and_dataset_approval(tmp_path):
    module = api()
    with pytest.raises(ValueError):
        module.run_batch(
            ROOT / "benchmarks/tasks",
            ROOT / "benchmarks/solutions",
            tmp_path,
            config=module.BatchConfig(task_ids=("pagination-001",), strategies=("baseline",)),
        )
    assert not list(tmp_path.iterdir())


def test_served_model_mismatch_stops_before_tool_execution(tmp_path):
    module = api()
    wrong = FakeModel(
        [
            reply(
                [
                    call(
                        "add_regression_test",
                        {"path": "regression/test_bad.py", "content": "assert True"},
                    )
                ]
            )
        ]
    )
    result = module.run_batch(
        ROOT / "benchmarks/tasks",
        ROOT / "benchmarks/solutions",
        tmp_path,
        config=module.BatchConfig(
            task_ids=("pagination-001",), strategies=("agent",), fixture=True
        ),
        model_factory=lambda *args: wrong,
        runner_factory=ReproducingRunner,
    )
    rows = module.load_records(Path(result.batch_path))
    assert rows[0]["failure_category"] == "served_model_mismatch"
    assert rows[0]["calls_without_usage"] == 0
    assert rows[0]["usage"]["input_tokens"] == 10
    assert not list(Path(result.batch_path).rglob("test_bad.py"))


@pytest.mark.docker
def test_failure_does_not_stop_batch_and_repetitions_have_unique_ids(tmp_path):
    module = api()

    def factory(task_id, strategy, repeat):
        if task_id == "boundary-001":

            class Broken:
                kind = "test-double"

                def complete(self, **kwargs):
                    raise ModelFailure("provider_error")

            return Broken()
        patch = (ROOT / "benchmarks/solutions" / task_id / "reference.patch").read_text(
            encoding="utf-8"
        )
        return FixtureModel([reply([{"type": "text", "text": patch}], stop="end_turn")])

    config = module.BatchConfig(
        task_ids=("boundary-001", "pagination-001"),
        strategies=("baseline",),
        repeats=2,
        fixture=True,
        budget=AgentBudget(),
    )
    result = module.run_batch(
        ROOT / "benchmarks/tasks",
        ROOT / "benchmarks/solutions",
        tmp_path,
        config=config,
        model_factory=factory,
    )
    rows = module.load_records(Path(result.batch_path))
    assert len(rows) == len({row["trial_id"] for row in rows}) == 4
    assert [row["status"] for row in rows].count("passed") == 2
    assert all(row["config_sha256"] == rows[0]["config_sha256"] for row in rows)
    group = result.summary["strategies"]["baseline"]
    assert (group["passed_trials"], group["total_trials"]) == (2, 4)
    assert group["failure_counts"]["provider_error"] == 2
    assert result.summary["result_kind"] == "offline-fixture"
    assert group["estimated_total_cost"] is None


@pytest.mark.parametrize("missing", ["input_tokens", "output_tokens"])
def test_successful_response_missing_core_usage_is_not_treated_as_free(tmp_path, missing):
    from patchpilot.evaluation.metrics import Pricing

    module = api()

    class PartialUsage(FixtureModel):
        def complete(self, **kwargs):
            result = super().complete(**kwargs)
            return result.model_copy(
                update={"usage": result.usage.model_copy(update={missing: None})}
            )

    model = PartialUsage([reply([{"type": "text", "text": "invalid patch"}], stop="end_turn")])
    result = module.run_batch(
        ROOT / "benchmarks/tasks",
        ROOT / "benchmarks/solutions",
        tmp_path,
        config=module.BatchConfig(
            task_ids=("pagination-001",), strategies=("baseline",), fixture=True
        ),
        model_factory=lambda *args: model,
        runner_factory=ReproducingRunner,
        pricing=Pricing(DEFAULT_MODEL, "USD", "test-rates", "1", "2", "0.1", "1.25"),
    )
    row = module.load_records(Path(result.batch_path))[0]
    assert not row["usage_complete"]
    assert row["calls_without_usage"] == 1
    assert row["model_served"] == [DEFAULT_MODEL]
    assert result.summary["strategies"]["baseline"]["estimated_total_cost"] is None


def test_environment_failure_is_recorded_for_every_planned_trial(tmp_path):
    module = api()
    from patchpilot.sandbox import SandboxError

    class BrokenRunner:
        def __init__(self, *args):
            pass

        def check(self):
            raise SandboxError("fixture unavailable")

    def no_model(*args):
        raise AssertionError("环境失败不能发模型请求")

    result = module.run_batch(
        ROOT / "benchmarks/tasks",
        ROOT / "benchmarks/solutions",
        tmp_path,
        config=module.BatchConfig(task_ids=("pagination-001", "boundary-001"), fixture=True),
        model_factory=no_model,
        runner_factory=BrokenRunner,
    )
    rows = module.load_records(Path(result.batch_path))
    assert len(rows) == 4
    assert all(row["failure_category"] == "environment_failed" for row in rows)
    assert sum(group["total_trials"] for group in result.summary["strategies"].values()) == 4


def test_changed_issue_invalidates_frozen_data_before_any_model_call(tmp_path):
    import shutil

    module = api()
    task_root = tmp_path / "data/tasks"
    case = Path(
        shutil.copytree(ROOT / "benchmarks/tasks/pagination-001", task_root / "pagination-001")
    )

    class Changed(ReproducingRunner):
        def check(self):
            (case / "Issue.md").write_text("changed after approval", encoding="utf-8")
            return super().check()

    def forbidden(*args):
        raise AssertionError("资料已变更，不可调用模型")

    result = module.run_batch(
        task_root,
        ROOT / "benchmarks/solutions",
        tmp_path / "out",
        config=module.BatchConfig(
            task_ids=("pagination-001",), strategies=("baseline",), fixture=True
        ),
        model_factory=forbidden,
        runner_factory=Changed,
    )
    row = module.load_records(Path(result.batch_path))[0]
    assert row["failure_category"] == "version_mismatch"
    assert row["model_calls"] == 0


def test_corrupt_result_is_counted_as_failure_not_a_broken_report(tmp_path):
    import uuid

    module = api()
    identifier = str(uuid.uuid4())
    batch_id = str(uuid.uuid4())
    spec = {
        "trial_id": identifier,
        "task_id": "pagination-001",
        "strategy": "baseline",
        "repeat": 0,
    }
    plan = {
        "batch_id": batch_id,
        "config_sha256": "0" * 64,
        "result_kind": "offline-fixture",
        "trials": [spec],
    }
    (tmp_path / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    path = tmp_path / "trials" / identifier / "result.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                **spec,
                "batch_id": batch_id,
                "config_sha256": "0" * 64,
                "status": "passed",
                "usage": "invalid",
                "model_calls": 1,
                "calls_without_usage": 0,
                "usage_complete": True,
            }
        ),
        encoding="utf-8",
    )
    rows = module.load_records(tmp_path)
    assert rows[0]["failure_category"] == "record_invalid"
    assert module.save_summary(tmp_path)["strategies"]["baseline"]["total_trials"] == 1


def test_missing_result_is_still_in_the_planned_denominator(tmp_path):
    module = api()
    import uuid

    trial_id = str(uuid.uuid4())
    plan = {
        "batch_id": str(uuid.uuid4()),
        "config_sha256": "0" * 64,
        "result_kind": "offline-fixture",
        "trials": [
            {
                "trial_id": trial_id,
                "task_id": "pagination-001",
                "strategy": "baseline",
                "repeat": 0,
                "split": "development",
            }
        ],
    }
    (tmp_path / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    rows = module.load_records(tmp_path)
    assert len(rows) == 1 and rows[0]["status"] == "pending"
    assert rows[0]["failure_category"] == "incomplete"

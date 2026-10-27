import copy
import importlib
import json
from pathlib import Path

import pytest
from anthropic.types import Message

from patchpilot.sandbox import DockerRunner
from patchpilot.task import load_task

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "benchmarks/tasks/pagination-001/task.json"
PATCH = ROOT / "benchmarks/solutions/pagination-001/reference.patch"


def api():
    assert importlib.util.find_spec("patchpilot.agent"), "尚未实现Agent循环"
    return importlib.import_module("patchpilot.agent")


def reply(content=None, stop="tool_use", input_tokens=10, output_tokens=10):
    return Message.model_validate(
        {
            "id": "msg-test",
            "type": "message",
            "role": "assistant",
            "model": "test-double",
            "content": content or [],
            "stop_reason": stop,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        }
    )


def call(name, arguments, id="call-1"):
    return {"type": "tool_use", "id": id, "name": name, "input": arguments}


class FakeModel:
    kind = "test-double"

    def __init__(self, turns):
        self.turns = iter(turns)
        self.requests = []

    def complete(self, *, system, messages, tools, timeout_seconds, max_output_tokens):
        self.requests.append(copy.deepcopy(messages))
        turn = next(self.turns)
        return turn(messages) if callable(turn) else turn


class MustNotVerify:
    def run(self, *args, **kwargs):
        raise AssertionError("未结束的Agent不能启动独立验收")


def run(tmp_path, model, budget=None, runner=None):
    module = api()
    return module.run_agent(
        load_task(CASE), tmp_path, model, runner or MustNotVerify(), budget or module.AgentBudget()
    )


def test_progress_callback_reports_steps_without_host_content(tmp_path: Path) -> None:
    events = []
    module = api()
    model = FakeModel([reply([call("list_files", {})])])
    module.run_agent(
        load_task(CASE),
        tmp_path,
        model,
        MustNotVerify(),
        module.AgentBudget(max_model_calls=1),
        on_event=events.append,
    )
    assert [item["event"] for item in events] == [
        "run_started",
        "model_started",
        "model_finished",
        "tool_started",
        "tool_finished",
        "run_finished",
    ]
    assert str(tmp_path) not in json.dumps(events)


def test_three_model_calls_never_emit_a_fourth_request(tmp_path: Path) -> None:
    model = FakeModel([reply([call("list_files", {}, f"call-{i}")]) for i in range(4)])
    result = run(tmp_path, model, api().AgentBudget(max_model_calls=3, max_repeated_actions=10))
    assert len(model.requests) == 3
    assert result.stop_reason == "model_budget"
    assert result.verification is None
    assert result.status == "unverified"
    assert result.model_calls == 3
    assert result.usage["output_tokens"] == 30
    assert Path(result.patch_path).is_file()
    assert Path(result.report_path).is_file()


def test_results_are_paired_and_errors_observed_in_next_turn(tmp_path: Path) -> None:
    model = FakeModel(
        [
            reply(
                [
                    call("missing_tool", {}, "unknown"),
                    call("read_file", {"path": "src/missing.py"}, "missing"),
                ]
            ),
            reply([call("list_files", {}, "later")]),
        ]
    )
    result = run(tmp_path, model, api().AgentBudget(max_model_calls=2))
    observations = model.requests[1][-1]
    assert observations["role"] == "user"
    assert [block["tool_use_id"] for block in observations["content"]] == ["unknown", "missing"]
    assert all(block["is_error"] for block in observations["content"])
    assert "not_found" in observations["content"][1]["content"]
    assert result.tool_calls == 3


@pytest.mark.parametrize("reason", ["max_tokens", "refusal"])
def test_incomplete_or_refused_turn_never_executes_tools(tmp_path: Path, reason: str) -> None:
    model = FakeModel(
        [
            reply(
                [
                    call(
                        "add_regression_test",
                        {"path": "regression/test_bad.py", "content": "assert True"},
                    )
                ],
                stop=reason,
            )
        ]
    )
    result = run(tmp_path, model)
    assert result.stop_reason == reason
    assert result.tool_calls == 0
    assert not (Path(result.workspace_path) / "repo/regression").exists()
    assert result.verification is None


def test_tool_blocks_with_end_turn_are_not_executed(tmp_path: Path) -> None:
    model = FakeModel(
        [
            reply(
                [
                    call(
                        "add_regression_test",
                        {"path": "regression/test_bad.py", "content": "assert True"},
                    )
                ],
                stop="end_turn",
            )
        ]
    )
    result = run(tmp_path, model)
    assert result.stop_reason == "protocol_error"
    assert result.tool_calls == 0
    assert not (Path(result.workspace_path) / "repo/regression").exists()


def test_tool_budget_stops_mid_batch_without_losing_call_ids(tmp_path: Path) -> None:
    model = FakeModel([reply([call("list_files", {}, "one"), call("list_files", {}, "two")])])
    result = run(tmp_path, model, api().AgentBudget(max_tool_calls=1))
    assert result.tool_calls == 1
    assert result.stop_reason == "tool_budget"
    transcript = json.loads(Path(result.transcript_path).read_text(encoding="utf-8"))
    assert [item["tool_use_id"] for item in transcript[-1]["content"]] == ["one", "two"]
    assert (
        json.loads(transcript[-1]["content"][1]["content"])["error"]["code"] == "budget_exhausted"
    )


def test_repeated_ineffective_actions_stop(tmp_path: Path) -> None:
    model = FakeModel(
        [reply([call("read_file", {"path": "src/missing.py"}, f"bad-{i}")]) for i in range(5)]
    )
    result = run(tmp_path, model, api().AgentBudget(max_repeated_actions=2))
    assert result.stop_reason == "repeated_action"
    assert result.model_calls == 2


def test_token_budget_does_not_send_another_request(tmp_path: Path) -> None:
    model = FakeModel([reply([call("list_files", {})], output_tokens=100000)])
    result = run(tmp_path, model, api().AgentBudget(max_total_tokens=50000))
    assert result.stop_reason == "token_budget"
    assert result.model_calls == 1
    assert result.tool_calls == 0


def test_context_limit_stops_instead_of_silently_dropping_history(tmp_path: Path) -> None:
    model = FakeModel([])
    result = run(tmp_path, model, api().AgentBudget(max_context_chars=100))
    assert result.stop_reason == "context_budget"
    assert result.model_calls == 0


def test_expired_total_budget_does_not_start_a_model_request(tmp_path: Path, monkeypatch) -> None:
    module = api()
    ticks = iter([0, 2, 2, 2, 2, 2, 2, 2])
    monkeypatch.setattr(module, "monotonic", lambda: next(ticks, 2))
    model = FakeModel([])
    result = run(tmp_path, model, module.AgentBudget(timeout_seconds=1))
    assert result.stop_reason == "time_budget"
    assert not model.requests


def test_hidden_answers_and_host_paths_are_not_in_model_context(tmp_path: Path) -> None:
    model = FakeModel([reply([call("list_files", {})])])
    run(tmp_path, model, api().AgentBudget(max_model_calls=1))
    text = json.dumps(model.requests, ensure_ascii=False)
    for forbidden in (
        str(ROOT),
        "reference.patch",
        "/acceptance",
        "_acceptance",
        "ANTHROPIC_AUTH_TOKEN",
    ):
        assert forbidden not in text


@pytest.mark.docker
def test_scripted_search_read_patch_test_finish_uses_independent_verification(
    tmp_path: Path,
) -> None:
    def apply_from_observation(messages):
        observed = json.loads(messages[-1]["content"][0]["content"])
        return reply(
            [
                call(
                    "apply_patch",
                    {
                        "patch": PATCH.read_text(encoding="utf-8"),
                        "expected_hash": observed["data"]["workspace_hash"],
                    },
                    "patch",
                )
            ]
        )

    model = FakeModel(
        [
            reply([call("search_text", {"query": "cursor", "directory": "src"}, "search")]),
            reply([call("read_file", {"path": "src/pagination_demo/paginator.py"}, "read")]),
            apply_from_observation,
            reply([call("run_tests", {}, "test")]),
            reply([{"type": "text", "text": "修复候选已就绪"}], stop="end_turn"),
        ]
    )
    result = run(tmp_path, model, runner=DockerRunner())
    assert result.status == "passed", result.stop_reason
    assert result.verification.tests.passed == 14
    assert result.model_kind == "test-double"
    assert len(model.requests) == 5
    assert "passed" in model.requests[-1][-1]["content"][0]["content"]
    assert Path(result.patch_path).read_bytes()
    assert Path(result.events_path).is_file()


@pytest.mark.docker
def test_model_claiming_success_without_a_fix_is_not_success(tmp_path: Path) -> None:
    model = FakeModel([reply([{"type": "text", "text": "全部修好了"}], stop="end_turn")])
    result = run(tmp_path, model, runner=DockerRunner())
    assert result.status == "failed"
    assert result.verification.tests.failed > 0

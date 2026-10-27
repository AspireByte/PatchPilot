from pathlib import Path

import pytest

from patchpilot.agent import ModelFailure
from patchpilot.control import ExecutionControl
from patchpilot.service import executors
from patchpilot.service.execution import ExecutionHooks
from patchpilot.task import load_task
from patchpilot.workspace import create_workspace

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "reason",
    [
        "permission_denied",
        "invalid_json",
        "model_timeout",
        "authentication_error",
        "model_not_found",
        "provider_error",
        "bad_request",
        "rate_limited",
        "model_connection_error",
    ],
)
def test_model_faults_are_failed_and_can_be_restarted(tmp_path, monkeypatch, reason):
    class FaultModel:
        kind = "test-double"

        def complete(self, **kwargs):
            raise ModelFailure(reason)

        def close(self):
            pass

    class NoNetworkRunner:
        def check(self):
            return {}

    monkeypatch.setattr(executors, "AnthropicModel", lambda **kwargs: FaultModel())
    task = load_task(ROOT / "benchmarks/tasks/pagination-001/task.json")
    workspace = create_workspace(task, tmp_path)
    hooks = ExecutionHooks(ExecutionControl(), lambda event: None, lambda workspace, kind: None)
    result = executors.AgentExecutor({}, allow_api=True).execute(
        task, workspace, hooks, NoNetworkRunner()
    )
    assert result.execution_status == "failed"
    assert result.verdict == "not_run"
    assert result.reason == reason

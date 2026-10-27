import json
from pathlib import Path

import pytest
from test_agent import FakeModel, call, reply

from patchpilot.agent import run_agent
from patchpilot.sandbox import DockerRunner
from patchpilot.task import load_task

ROOT = Path(__file__).resolve().parents[1]
TASKS = ["pagination-001", "boundary-001", "null-001", "validation-001", "state-001"]


@pytest.mark.docker
@pytest.mark.parametrize("task_id", TASKS)
def test_same_agent_loop_works_with_all_five_scripted_tasks(tmp_path: Path, task_id: str) -> None:
    task = load_task(ROOT / "benchmarks/tasks" / task_id / "task.json")
    patch = (ROOT / "benchmarks/solutions" / task_id / "reference.patch").read_text(
        encoding="utf-8"
    )

    def read_discovered_file(messages):
        observations = json.loads(messages[-1]["content"][0]["content"])
        path = observations["data"]["matches"][0]["path"]
        return reply([call("read_file", {"path": path}, "read")])

    def patch_observed_revision(messages):
        observations = json.loads(messages[-1]["content"][0]["content"])
        return reply(
            [
                call(
                    "apply_patch",
                    {"patch": patch, "expected_hash": observations["data"]["workspace_hash"]},
                    "patch",
                )
            ]
        )

    model = FakeModel(
        [
            reply([call("search_text", {"query": "def ", "directory": "src"}, "search")]),
            read_discovered_file,
            patch_observed_revision,
            reply([call("run_tests", {}, "tests")]),
            reply([{"type": "text", "text": "候选完成，等待独立验证"}], stop="end_turn"),
        ]
    )
    result = run_agent(task, tmp_path, model, DockerRunner())
    assert result.status == "passed", result.stop_reason
    assert result.model_kind == "test-double"
    assert result.verification.tests.failed == 0
    assert result.verification.tests.passed > 0
    assert result.model_calls == 5
    assert "passed" in model.requests[-1][-1]["content"][0]["content"]

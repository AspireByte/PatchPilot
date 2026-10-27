import importlib
import json
from pathlib import Path

import pytest
from test_agent import FakeModel, reply

from patchpilot.agent import AgentBudget
from patchpilot.task import load_task

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "benchmarks/tasks/pagination-001/task.json"
PATCH = ROOT / "benchmarks/solutions/pagination-001/reference.patch"


def api():
    assert importlib.util.find_spec("patchpilot.evaluation.baseline"), "尚未实现单次生成基线"
    return importlib.import_module("patchpilot.evaluation.baseline")


class MustNotRun:
    def run(self, *args, **kwargs):
        raise AssertionError("不完整/非法补丁不得执行测试")


def test_baseline_retrieval_is_public_and_invalid_patch_is_not_retried(tmp_path):
    model = FakeModel([reply([{"type": "text", "text": "not a patch"}], stop="end_turn")])
    result = api().run_baseline(load_task(CASE), tmp_path, model, MustNotRun())
    assert result.model_calls == 1 and result.tool_calls == 0
    assert result.status == "rejected"
    assert len(model.requests) == 1
    text = json.dumps(model.requests, ensure_ascii=False)
    assert "paginator.py" in text and "test_same_timestamp.py" in text
    assert "reference.patch" not in text and "_acceptance" not in text
    assert str(ROOT) not in text
    assert Path(result.transcript_path).is_file()


@pytest.mark.parametrize("stop", ["max_tokens", "refusal"])
def test_baseline_stopped_output_is_never_executed(tmp_path, stop):
    model = FakeModel(
        [reply([{"type": "text", "text": PATCH.read_text(encoding="utf-8")}], stop=stop)]
    )
    result = api().run_baseline(load_task(CASE), tmp_path, model, MustNotRun(), AgentBudget())
    assert result.stop_reason == stop
    assert result.verification is None
    assert result.model_calls == 1


@pytest.mark.docker
def test_one_generation_uses_the_same_independent_verifier(tmp_path):
    from patchpilot.sandbox import DockerRunner

    model = FakeModel(
        [reply([{"type": "text", "text": PATCH.read_text(encoding="utf-8")}], stop="end_turn")]
    )
    result = api().run_baseline(load_task(CASE), tmp_path, model, DockerRunner())
    assert result.status == "passed"
    assert result.verification.tests.passed == 14
    assert result.verification.base_sha256 == load_task(CASE).spec.snapshot_sha256
    assert len(model.requests) == 1

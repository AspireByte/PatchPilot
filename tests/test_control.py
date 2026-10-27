import importlib
from pathlib import Path

import pytest


def api():
    assert importlib.util.find_spec("patchpilot.control"), "尚未实现执行取消控制"
    return importlib.import_module("patchpilot.control")


@pytest.mark.docker
def test_cancel_terminates_an_active_test_container(tmp_path: Path, monkeypatch) -> None:
    import subprocess
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from patchpilot import sandbox
    from patchpilot.task import load_task
    from patchpilot.workspace import create_workspace

    control = api().ExecutionControl()
    task = load_task(
        Path(__file__).resolve().parents[1] / "benchmarks/tasks/pagination-001/task.json"
    )
    workspace = create_workspace(task, tmp_path)
    (workspace.repo / "repro/test_loop.py").write_text(
        "def test_loop():\n    while True:\n        pass\n", encoding="utf-8"
    )
    started = Event()
    checked = sandbox._checked

    def observed(arguments, **kwargs):
        result = checked(arguments, **kwargs)
        if arguments[0] == "start":
            started.set()
        return result

    monkeypatch.setattr(sandbox, "_checked", observed)
    runner = sandbox.DockerRunner(control=control)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(runner.run, workspace)
        assert started.wait(15)
        control.stop("cancelled")
        result = future.result(timeout=10)
    assert result.status == "cancelled"
    assert result.cleanup_ok
    assert (
        subprocess.run(["docker", "inspect", result.container_id], capture_output=True).returncode
        != 0
    )


def test_cancel_interrupts_an_inflight_model_stream() -> None:
    import time
    from threading import Timer

    from test_anthropic_model import FakeClient, SlowStream, message

    from patchpilot.anthropic_model import AnthropicModel

    control = api().ExecutionControl()
    stream = SlowStream(message())
    client = FakeClient()
    client.stream = lambda **kwargs: stream
    model = AnthropicModel(client=client, control=control)
    timer = Timer(0.03, lambda: control.stop("cancelled"))
    started = time.monotonic()
    timer.start()
    try:
        with pytest.raises(api().ExecutionStopped):
            model.complete(
                system="公开规则",
                messages=[{"role": "user", "content": "公开任务"}],
                tools=[],
                timeout_seconds=2,
                max_output_tokens=100,
            )
    finally:
        timer.cancel()
        model.close()
    assert time.monotonic() - started < 0.22
    assert stream.closed


def test_cancelled_control_interrupts_future_actions() -> None:
    module = api()
    control = module.ExecutionControl()
    control.check()
    control.stop("cancelled")
    with pytest.raises(module.ExecutionStopped) as raised:
        control.check()
    assert raised.value.reason == "cancelled"


def test_lease_loss_takes_priority_over_cancel() -> None:
    module = api()
    control = module.ExecutionControl()
    control.stop("cancelled")
    control.stop("lease_lost")
    with pytest.raises(module.ExecutionStopped) as raised:
        control.check()
    assert raised.value.reason == "lease_lost"


def test_cancel_before_run_makes_no_model_request(tmp_path: Path) -> None:
    module = api()
    from test_agent import FakeModel

    from patchpilot.agent import run_agent
    from patchpilot.task import load_task

    control = module.ExecutionControl()
    control.stop("cancelled")
    task = load_task(
        Path(__file__).resolve().parents[1] / "benchmarks/tasks/pagination-001/task.json"
    )
    model = FakeModel([])
    result = run_agent(task, tmp_path, model, None, control=control)
    assert result.stop_reason == "cancelled"
    assert result.model_calls == result.tool_calls == 0
    assert Path(result.report_path).is_file()

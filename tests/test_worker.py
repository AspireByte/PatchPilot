import importlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest
from postgres_helpers import pg_repository as pg_repository
from sqlalchemy import text

from patchpilot.service.config import ServiceSettings
from patchpilot.task import load_task

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.postgres


def api():
    assert importlib.util.find_spec("patchpilot.service.worker"), "尚未实现独立Worker"
    return importlib.import_module("patchpilot.service.worker")


@pytest.fixture
def settings(pg_repository, tmp_path):
    return ServiceSettings(
        pg_repository.engine.url.render_as_string(hide_password=False),
        "test-local-api-token",
        data_root=tmp_path,
        tasks_root=ROOT / "benchmarks/tasks",
        solutions_root=ROOT / "benchmarks/solutions",
        lease_seconds=3,
        heartbeat_seconds=0.2,
        cleanup_expired=False,
    )


def enqueue(repository):
    task = load_task(ROOT / "benchmarks/tasks/pagination-001/task.json")
    return repository.create_run(task.spec.task_id, "diagnostic", {}, task.spec.snapshot_sha256)


class ImmediateExecutor:
    kind = "test-double"

    def execute(self, task, workspace, hooks, runner):
        hooks.event({"event": "model_started", "number": 1})
        hooks.event(
            {
                "event": "model_finished",
                "number": 1,
                "reason": "end_turn",
                "usage": {"input_tokens": 5, "output_tokens": 7},
            }
        )
        return api().ExecutionOutput(
            "completed",
            "not_run",
            "test_completed",
            {"input_tokens": 5, "output_tokens": 7},
            b"",
            {"executor_kind": self.kind},
        )


def test_worker_claims_executes_and_persists_separate_status_and_verdict(
    pg_repository, settings
) -> None:
    run = enqueue(pg_repository)
    assert (
        api()
        .Worker(pg_repository, settings, mode="diagnostic", executor=ImmediateExecutor())
        .run_once()
    )
    stored = pg_repository.get_run(run["id"])
    assert stored["execution_status"] == "completed"
    assert stored["verdict"] == "not_run"
    assert stored["usage"]["output_tokens"] == 7
    assert pg_repository.list_steps(run["id"])[0]["status"] == "completed"
    assert {item["kind"] for item in pg_repository.list_artifacts(run["id"])} >= {
        "report",
        "candidate_patch",
        "checkpoint_patch",
    }
    events = pg_repository.list_events(run["id"])
    assert [item["seq"] for item in events] == list(range(1, len(events) + 1))


def test_concurrent_workers_only_execute_once(pg_repository, settings) -> None:
    run = enqueue(pg_repository)
    with ThreadPoolExecutor(2) as pool:
        futures = [
            pool.submit(
                api()
                .Worker(pg_repository, settings, mode="diagnostic", executor=ImmediateExecutor())
                .run_once
            )
            for _ in range(2)
        ]
    assert sorted(f.result() for f in futures) == [False, True]
    assert len(pg_repository.list_attempts(run["id"])) == 1


class BlockingExecutor:
    kind = "test-double"

    def __init__(self):
        self.started = Event()
        self.after_cancel = False

    def execute(self, task, workspace, hooks, runner):
        self.started.set()
        while True:
            hooks.control.check()
            Event().wait(0.02)


def test_running_cancel_propagates_and_does_not_publish_success(pg_repository, settings) -> None:
    run = enqueue(pg_repository)
    executor = BlockingExecutor()
    worker = api().Worker(pg_repository, settings, mode="diagnostic", executor=executor)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(worker.run_once)
        assert executor.started.wait(10)
        pg_repository.cancel(run["id"])
        assert future.result(timeout=10)
    stored = pg_repository.get_run(run["id"])
    assert stored["execution_status"] == "cancelled"
    assert stored["verdict"] == "not_run"


def expire(repository):
    with repository.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE attempt SET lease_until=clock_timestamp()-interval '1 second' WHERE execution_status='running'"
            )
        )


def test_expired_worker_stops_and_cannot_overwrite_interrupted_run(pg_repository, settings) -> None:
    run = enqueue(pg_repository)
    executor = BlockingExecutor()
    worker = api().Worker(pg_repository, settings, mode="diagnostic", executor=executor)
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(worker.run_once)
        assert executor.started.wait(10)
        expire(pg_repository)
        pg_repository.reap_expired()
        assert future.result(timeout=10)
    assert pg_repository.get_run(run["id"])["execution_status"] == "interrupted"


def test_report_publication_failure_does_not_serve_abandoned_patch(
    pg_repository, settings, monkeypatch
) -> None:
    from fastapi.testclient import TestClient

    from patchpilot.patching import apply_patch, export_diff
    from patchpilot.service.api import create_app

    run = enqueue(pg_repository)

    class UncommittedExecutor:
        kind = "test-double"

        def execute(self, task, workspace, hooks, runner):
            apply_patch(
                workspace,
                (ROOT / "benchmarks/solutions/pagination-001/reference.patch").read_bytes(),
                task.spec.snapshot_sha256,
            )
            return api().ExecutionOutput(
                "completed",
                "not_run",
                "model_budget",
                {},
                export_diff(task, workspace),
                {"executor_kind": self.kind},
            )

    worker = api().Worker(
        pg_repository, settings, mode="diagnostic", executor=UncommittedExecutor()
    )
    original = worker.storage.seal
    failed_once = False

    def fail_report_once(attempt_id, kind, content):
        nonlocal failed_once
        if kind == "report" and not failed_once:
            failed_once = True
            raise OSError("temporary report failure")
        return original(attempt_id, kind, content)

    monkeypatch.setattr(worker.storage, "seal", fail_report_once)
    assert worker.run_once()
    assert pg_repository.get_run(run["id"])["execution_status"] == "failed"
    assert (
        len(
            [
                item
                for item in pg_repository.list_artifacts(run["id"])
                if item["kind"] == "candidate_patch"
            ]
        )
        == 2
    )
    with TestClient(create_app(settings, repository=pg_repository)) as client:
        headers = {"Authorization": "Bearer test-local-api-token"}
        assert client.get(f"/v1/runs/{run['id']}/patch", headers=headers).content == b""


def test_corrupted_checkpoint_is_rejected_before_executor_runs(pg_repository, settings) -> None:
    run = enqueue(pg_repository)

    class CrashExecutor:
        kind = "test-double"

        def execute(self, task, workspace, hooks, runner):
            raise SimulatedCrash()

    with pytest.raises(SimulatedCrash):
        api().Worker(
            pg_repository, settings, mode="diagnostic", executor=CrashExecutor()
        ).run_once()
    expire(pg_repository)
    pg_repository.reap_expired()
    old = pg_repository.get_run(run["id"])
    checkpoint = pg_repository.get_checkpoint(old["latest_checkpoint_id"])
    artifact = pg_repository.get_artifact(run["id"], checkpoint["artifact_id"])
    (settings.data_root / "artifacts" / artifact["relative_path"]).write_bytes(b"corrupted")
    pg_repository.restart(run["id"])

    class MustNotRun:
        kind = "test-double"

        def execute(self, *args):
            raise AssertionError("损坏检查点不能继续执行")

    assert (
        api().Worker(pg_repository, settings, mode="diagnostic", executor=MustNotRun()).run_once()
    )
    final = pg_repository.get_run(run["id"])
    assert final["execution_status"] == "failed"
    assert final["reason"] == "checkpoint_invalid"


@pytest.mark.docker
def test_api_cancel_interrupts_worker_container_and_keeps_partial_patch(
    pg_repository, settings, monkeypatch
) -> None:
    from fastapi.testclient import TestClient

    from patchpilot import sandbox
    from patchpilot.patching import apply_patch
    from patchpilot.service.api import create_app

    started = Event()
    checked = sandbox._checked
    results = []

    def observed(arguments, **kwargs):
        result = checked(arguments, **kwargs)
        if arguments[0] == "start":
            started.set()
        return result

    monkeypatch.setattr(sandbox, "_checked", observed)

    class ContainerExecutor:
        kind = "test-double"

        def execute(self, task, workspace, hooks, runner):
            apply_patch(
                workspace,
                (ROOT / "benchmarks/solutions/pagination-001/reference.patch").read_bytes(),
                task.spec.snapshot_sha256,
            )
            (workspace.repo / "regression").mkdir()
            (workspace.repo / "regression/test_loop.py").write_text(
                "def test_loop():\n    while True:\n        pass\n"
            )
            hooks.boundary(workspace, "tool_batch")
            hooks.event({"event": "tool_started", "call_id": "active-tests", "tool": "run_tests"})
            results.append(runner.run(workspace, targets=("regression",)))
            hooks.control.check()
            raise AssertionError("取消后不应继续动作")

    with TestClient(create_app(settings, repository=pg_repository)) as client:
        headers = {"Authorization": "Bearer test-local-api-token"}
        run = client.post(
            "/v1/runs", headers=headers, json={"task_id": "pagination-001", "mode": "diagnostic"}
        ).json()
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(
                api()
                .Worker(pg_repository, settings, mode="diagnostic", executor=ContainerExecutor())
                .run_once
            )
            assert started.wait(15)
            assert client.post(f"/v1/runs/{run['id']}/cancel", headers=headers).status_code == 200
            assert future.result(timeout=15)
        stored = pg_repository.get_run(run["id"])
        assert stored["execution_status"] == "cancelled" and stored["verdict"] == "not_run"
        report = client.get(f"/v1/runs/{run['id']}/report", headers=headers).json()
        assert report["execution_status"] == "cancelled" and report["verdict"] == "not_run"
        assert "diff --git" in client.get(f"/v1/runs/{run['id']}/patch", headers=headers).text
        assert results[0].status == "cancelled" and results[0].cleanup_ok


class SimulatedCrash(BaseException):
    pass


@pytest.mark.docker
@pytest.mark.parametrize("point", ["after_read", "patch_not_recorded", "test_not_recorded"])
def test_restart_rebuilds_only_confirmed_boundary(pg_repository, settings, point) -> None:
    from patchpilot.patching import apply_patch
    from patchpilot.snapshot import compute_snapshot_hash

    run = enqueue(pg_repository)
    old_workspace = []

    class CrashExecutor:
        kind = "test-double"

        def execute(self, task, workspace, hooks, runner):
            old_workspace.append(workspace.root)
            hooks.event({"event": "tool_started", "call_id": "read", "tool": "read_file"})
            hooks.event(
                {"event": "tool_finished", "call_id": "read", "tool": "read_file", "ok": True}
            )
            hooks.boundary(workspace, "tool_batch")
            if point != "after_read":
                apply_patch(
                    workspace,
                    (ROOT / "benchmarks/solutions/pagination-001/reference.patch").read_bytes(),
                    task.spec.snapshot_sha256,
                )
                if point == "test_not_recorded":
                    hooks.boundary(workspace, "tool_batch")
                    hooks.event({"event": "tool_started", "call_id": "test", "tool": "run_tests"})
                    completed_test = runner.run(workspace)
                    assert completed_test.status == "passed"
                    # 真正测试完成后模拟进程中断，故意不提交tool_finished和边界。
            raise SimulatedCrash()

    with pytest.raises(SimulatedCrash):
        api().Worker(
            pg_repository, settings, mode="diagnostic", executor=CrashExecutor()
        ).run_once()
    expire(pg_repository)
    pg_repository.reap_expired()
    old = pg_repository.get_run(run["id"])
    checkpoint = pg_repository.get_checkpoint(old["latest_checkpoint_id"])
    assert (checkpoint["candidate_sha256"] == old["base_sha256"]) is (point != "test_not_recorded")
    pg_repository.restart(run["id"])

    class RestoredExecutor(ImmediateExecutor):
        def execute(self, task, workspace, hooks, runner):
            assert workspace.root not in old_workspace
            assert compute_snapshot_hash(workspace.repo) == checkpoint["candidate_sha256"]
            return super().execute(task, workspace, hooks, runner)

    assert (
        api()
        .Worker(pg_repository, settings, mode="diagnostic", executor=RestoredExecutor())
        .run_once()
    )
    assert len(pg_repository.list_attempts(run["id"])) == 2
    if point == "test_not_recorded":
        assert any(
            step["name"] == "run_tests" and step["status"] == "unknown"
            for step in pg_repository.list_steps(run["id"])
        )

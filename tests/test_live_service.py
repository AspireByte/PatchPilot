import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from threading import Event

import httpx
import pytest
from postgres_helpers import pg_repository as pg_repository
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
pytestmark = [pytest.mark.postgres, pytest.mark.docker]


def wait_for(condition, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        Event().wait(0.05)
    raise AssertionError("条件未在时限内满足")


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)


@pytest.fixture
def live_service(pg_repository, tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    environment = {
        **os.environ,
        "PATCHPILOT_DATABASE_URL": pg_repository.engine.url.render_as_string(hide_password=False),
        "PATCHPILOT_API_TOKEN": "test-local-api-token",
        "PATCHPILOT_DATA_ROOT": str(tmp_path),
        "PATCHPILOT_TASKS_ROOT": str(ROOT / "benchmarks/tasks"),
        "PATCHPILOT_SOLUTIONS_ROOT": str(ROOT / "benchmarks/solutions"),
        "PYTHONIOENCODING": "utf-8",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "patchpilot.service.cli", "serve", "--port", str(port)],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    client = httpx.Client(
        base_url=f"http://127.0.0.1:{port}",
        headers={"Authorization": "Bearer test-local-api-token"},
        timeout=5,
        trust_env=False,
    )

    def ready():
        try:
            return client.get("/v1/tasks").status_code == 200
        except httpx.HTTPError:
            return False

    try:
        wait_for(ready)
        yield client, environment
    finally:
        client.close()
        stop(process)


def test_http_api_and_worker_are_separate_real_processes(live_service, pg_repository) -> None:
    client, environment = live_service
    run = client.post("/v1/runs", json={"task_id": "pagination-001", "mode": "diagnostic"}).json()
    assert run["execution_status"] == "queued"
    with client.stream("GET", f"/v1/runs/{run['id']}/events") as response:
        first = next(line for line in response.iter_lines() if line.startswith("id:"))
    assert pg_repository.get_run(run["id"])["execution_status"] == "queued"
    assert not pg_repository.get_run(run["id"])["cancel_requested"]
    worker = subprocess.run(
        [sys.executable, "-m", "patchpilot.service.cli", "worker", "--once", "--diagnostic"],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert worker.returncode == 0, worker.stdout + worker.stderr
    stored = client.get(f"/v1/runs/{run['id']}").json()
    assert stored["execution_status"] == "completed" and stored["verdict"] == "passed"
    report = client.get(f"/v1/runs/{run['id']}/report").json()
    assert report["executor_kind"] == "reference-diagnostic"
    assert report["tests"]["passed"] == 14
    assert "diff --git" in client.get(f"/v1/runs/{run['id']}/patch").text
    events = client.get(
        f"/v1/runs/{run['id']}/events", headers={"Last-Event-ID": first.split(":", 1)[1].strip()}
    )
    assert events.status_code == 200 and "run_finished" in events.text
    assert first + "\n" not in events.text
    assert client.get("/v1/runs").json()[0]["id"] == run["id"]


def test_killed_worker_is_reaped_by_independent_api_process(
    live_service, pg_repository, tmp_path
) -> None:
    client, environment = live_service
    run = client.post("/v1/runs", json={"task_id": "pagination-001", "mode": "diagnostic"}).json()
    marker = tmp_path / "worker-ready"
    worker = subprocess.Popen(
        [sys.executable, "-I", str(ROOT / "tests/fixtures/worker_probe.py"), str(marker)],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        wait_for(marker.exists)
        worker.kill()
        worker.communicate(timeout=10)
        with pg_repository.engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE attempt SET lease_until=clock_timestamp()-interval '1 second' WHERE execution_status='running'"
                )
            )

        def interrupted():
            result = client.get(f"/v1/runs/{run['id']}").json()
            return result if result["execution_status"] == "interrupted" else None

        result = wait_for(interrupted, seconds=10)
        assert result["verdict"] == "not_run"
        assert any(
            step["status"] == "unknown" for step in client.get(f"/v1/runs/{run['id']}/steps").json()
        )
    finally:
        stop(worker)

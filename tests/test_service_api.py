import asyncio
import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from postgres_helpers import pg_repository as pg_repository

from patchpilot.service.config import ServiceSettings

ROOT = Path(__file__).resolve().parents[1]
AUTH = {"Authorization": "Bearer test-local-api-token"}
pytestmark = pytest.mark.postgres


def api():
    assert importlib.util.find_spec("patchpilot.service.api"), "尚未实现任务API"
    return importlib.import_module("patchpilot.service.api")


@pytest.fixture
def settings(pg_repository, tmp_path):
    return ServiceSettings(
        pg_repository.engine.url.render_as_string(hide_password=False),
        "test-local-api-token",
        data_root=tmp_path,
        tasks_root=ROOT / "benchmarks/tasks",
        solutions_root=ROOT / "benchmarks/solutions",
        cleanup_expired=False,
    )


@pytest.fixture
def client(pg_repository, settings):
    with TestClient(api().create_app(settings, repository=pg_repository)) as client:
        yield client


def test_api_auth_and_creation_only_enqueue(client, pg_repository, tmp_path) -> None:
    assert client.get("/v1/tasks").status_code == 401
    assert len(client.get("/v1/tasks", headers=AUTH).json()) == 20
    response = client.post("/v1/runs", headers=AUTH, json={"task_id": "pagination-001"})
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["execution_status"] == "queued"
    assert run["verdict"] == "not_run"
    assert client.get(f"/v1/runs/{run['id']}/steps", headers=AUTH).json() == []
    assert not (tmp_path / "workspaces").exists()
    assert pg_repository.get_run(run["id"])["execution_status"] == "queued"
    assert "token" not in run and "test-local-api-token" not in response.text


@pytest.mark.parametrize(
    "payload",
    [
        {"task_id": "../outside"},
        {"task_id": "pagination-001", "path": "/etc"},
        {"task_id": "pagination-001", "command": "anything"},
        {"task_id": "pagination-001", "repository_url": "https://invalid"},
        {"task_id": "pagination-001", "budget": {"max_model_calls": 1000}},
    ],
)
def test_api_does_not_accept_arbitrary_execution_inputs(client, payload) -> None:
    assert client.post("/v1/runs", headers=AUTH, json=payload).status_code == 422


def test_sse_replays_persisted_events_after_last_id(client, pg_repository) -> None:
    run = client.post("/v1/runs", headers=AUTH, json={"task_id": "pagination-001"}).json()
    client.post(f"/v1/runs/{run['id']}/cancel", headers=AUTH)
    events = pg_repository.list_events(run["id"])
    first = events[0]["seq"]
    response = client.get(
        f"/v1/runs/{run['id']}/events", headers={**AUTH, "Last-Event-ID": str(first)}
    )
    assert response.status_code == 200
    assert f"id: {first}\n" not in response.text
    assert all(f"id: {item['seq']}\n" in response.text for item in events[1:])
    assert pg_repository.get_run(run["id"])["execution_status"] == "cancelled"


def test_disconnecting_event_stream_does_not_cancel(pg_repository) -> None:
    run = pg_repository.create_run("pagination-001", "agent", {}, "0" * 64)

    async def disconnected():
        return True

    async def collect():
        return [
            item async for item in api().event_stream(pg_repository, run["id"], 0, disconnected)
        ]

    assert asyncio.run(collect()) == []
    assert pg_repository.get_run(run["id"])["execution_status"] == "queued"


def test_sse_drains_terminal_event_committed_between_its_reads(pg_repository, monkeypatch) -> None:
    run = pg_repository.create_run("pagination-001", "agent", {}, "0" * 64)
    first = pg_repository.list_events(run["id"])[0]["seq"]
    original = pg_repository.get_run
    injected = False

    def race(run_id):
        nonlocal injected
        if not injected:
            injected = True
            pg_repository.cancel(run_id)
        return original(run_id)

    monkeypatch.setattr(pg_repository, "get_run", race)

    async def disconnected():
        return False

    async def collect():
        return [
            item async for item in api().event_stream(pg_repository, run["id"], first, disconnected)
        ]

    emitted = "".join(asyncio.run(collect()))
    final_events = pg_repository.list_events(run["id"], first)
    assert final_events
    assert all(f"id: {item['seq']}\n" in emitted for item in final_events)


def test_artifact_download_is_bound_to_run_and_hash(client, pg_repository, settings) -> None:
    from patchpilot.service.artifacts import ArtifactStorage

    run = client.post("/v1/runs", headers=AUTH, json={"task_id": "pagination-001"}).json()
    claim = pg_repository.claim("worker-test")
    storage = ArtifactStorage(settings.data_root / "artifacts")
    sealed = storage.seal(claim.attempt_id, "candidate_patch", b"example patch")
    reference = pg_repository.register_artifact(claim, "candidate_patch", **sealed)
    url = f"/v1/runs/{run['id']}/artifacts/{reference['id']}"
    assert client.get(url, headers=AUTH).content == b"example patch"
    other = client.post("/v1/runs", headers=AUTH, json={"task_id": "null-001"}).json()
    assert (
        client.get(f"/v1/runs/{other['id']}/artifacts/{reference['id']}", headers=AUTH).status_code
        == 404
    )
    (storage.root / sealed["relative_path"]).write_bytes(b"changed")
    assert client.get(url, headers=AUTH).status_code == 409

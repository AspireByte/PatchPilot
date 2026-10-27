"""仓储契约及真实 PostgreSQL 锁、租约、迁移与恢复回归测试。"""

import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from datetime import datetime
from threading import Barrier

import pytest
from postgres_helpers import pg_repository as pg_repository
from sqlalchemy import inspect, text

BASE = "a" * 64
CANDIDATE = "b" * 64
DIGEST = "c" * 64
BUDGET = {"max_model_calls": 2, "timeout_seconds": 30}


def test_repository_contract_is_available():
    try:
        spec = importlib.util.find_spec("patchpilot.service.repository")
    except ModuleNotFoundError:
        spec = None
    assert spec is not None, "阶段四 PostgreSQL 仓储尚未实现"


def test_history_query_contract_is_available():
    from patchpilot.service.repository import Repository

    assert callable(getattr(Repository, "list_runs", None)), "历史 Run 查询尚未实现"


def test_history_query_builds_bounded_reverse_order_without_database():
    from unittest.mock import MagicMock

    from sqlalchemy.dialects.postgresql import dialect

    from patchpilot.service.repository import Repository

    repository = Repository.__new__(Repository)
    repository.engine = MagicMock()
    connection = repository.engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value = []
    assert repository.list_runs(limit=2) == []
    compiled = connection.execute.call_args.args[0].compile(dialect=dialect())
    assert "created_at DESC" in str(compiled) and "id DESC" in str(compiled)
    assert 2 in compiled.params.values()
    for limit in (0, -1, True, 1001):
        with pytest.raises(ValueError):
            repository.list_runs(limit)
    assert connection.execute.call_count == 1


def new_run(repository, *, mode="agent"):
    return repository.create_run("pagination-001", mode, BUDGET, BASE)


def expire(repository, claim):
    with repository.engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE attempt SET lease_until=clock_timestamp()-interval '1 second' WHERE id=:id"
            ),
            {"id": claim.attempt_id},
        )


def checkpoint(repository, claim):
    artifact = repository.register_artifact(claim, "patch", "attempt/candidate.patch", DIGEST, 5)
    saved = repository.save_checkpoint(claim, artifact["id"], BASE, CANDIDATE, {"step": "patch"})
    return artifact, saved


@pytest.mark.postgres
def test_migrations_are_explicit_idempotent_and_reconnection_persists(pg_repository):
    from patchpilot.service.database import migrate
    from patchpilot.service.repository import Repository

    url = pg_repository.engine.url.render_as_string(hide_password=False)
    schema = pg_repository.engine.url.query["options"].removeprefix("-csearch_path=")
    assert schema.startswith("pp_test_") and len(schema) == len("pp_test_") + 32
    migrate(url)
    migrate(url)
    assert set(inspect(pg_repository.engine).get_table_names()) == {
        "alembic_version",
        "run",
        "attempt",
        "step",
        "event",
        "artifact",
        "checkpoint",
    }
    run = new_run(pg_repository)
    pg_repository.close()
    other = Repository(url)
    try:
        assert other.get_run(run["id"]) == run
        assert other.list_events(run["id"])[0]["kind"] == "run_created"
    finally:
        other.close()


@pytest.mark.postgres
def test_public_run_shape_and_frozen_claim(pg_repository):
    run = new_run(pg_repository)
    expected = {
        "id",
        "task_id",
        "mode",
        "execution_status",
        "verdict",
        "reason",
        "budget",
        "base_sha256",
        "current_attempt_id",
        "latest_checkpoint_id",
        "cancel_requested",
        "created_at",
        "updated_at",
        "usage",
    }
    assert set(run) == expected
    assert run["execution_status"] == "queued"
    assert run["verdict"] == "not_run"
    assert run["usage"] == {}
    for key in ("created_at", "updated_at"):
        assert datetime.fromisoformat(run[key]).tzinfo is not None
    claim = pg_repository.claim("worker-1")
    assert claim.run_id == run["id"] and claim.budget == BUDGET
    assert claim.base_sha256 == BASE and claim.checkpoint_id is None
    for key in ("run_id", "attempt_id", "token", "worker_id", "task_id", "mode"):
        assert isinstance(getattr(claim, key), str)
    with pytest.raises(FrozenInstanceError):
        claim.token = "other"
    assert "token" not in json.dumps(pg_repository.get_run(run["id"]))
    assert "token" not in json.dumps(pg_repository.list_attempts(run["id"]))
    assert pg_repository.get_run("missing") is None
    assert pg_repository.get_checkpoint("missing") is None
    assert pg_repository.get_artifact(run["id"], "missing") is None


@pytest.mark.postgres
def test_concurrent_claim_skips_locked_and_mode_isolated(pg_repository):
    run = new_run(pg_repository)
    diagnostic = new_run(pg_repository, mode="diagnostic")
    barrier = Barrier(4)

    def take(number):
        barrier.wait()
        return pg_repository.claim(f"worker-{number}")

    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(take, range(4)))
    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1 and winners[0].run_id == run["id"]
    assert len(pg_repository.list_attempts(run["id"])) == 1
    assert pg_repository.claim("worker-d", mode="diagnostic").run_id == diagnostic["id"]
    assert pg_repository.claim("empty") is None


@pytest.mark.postgres
def test_claim_does_not_wait_for_locked_queued_run(pg_repository):
    first = new_run(pg_repository)
    second = new_run(pg_repository)
    with pg_repository.engine.begin() as connection:
        connection.execute(
            text('SELECT id FROM "run" WHERE id=:id FOR UPDATE'), {"id": first["id"]}
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            claim = pool.submit(pg_repository.claim, "worker-2").result(timeout=5)
    assert claim.run_id == second["id"]


@pytest.mark.postgres
def test_events_are_sequential_under_concurrency_and_resume(pg_repository):
    run = new_run(pg_repository)
    claim = pg_repository.claim("worker-1", lease_seconds=60)
    with ThreadPoolExecutor(max_workers=5) as pool:
        list(
            pool.map(
                lambda n: pg_repository.append_event(claim, "progress", {"number": n}), range(20)
            )
        )
    events = pg_repository.list_events(run["id"])
    assert [event["seq"] for event in events] == list(range(1, 23))
    assert set(events[-1]) == {"seq", "kind", "payload", "attempt_id"}
    assert pg_repository.list_events(run["id"], after=20, limit=1) == events[20:21]
    assert pg_repository.list_events("missing") == []


@pytest.mark.postgres
def test_step_upsert_artifact_checkpoint_and_independent_usage_verdict(pg_repository):
    run = new_run(pg_repository)
    claim = pg_repository.claim("worker-1")
    pg_repository.upsert_step(claim, "verify", "test", "公开测试", "started", {"number": 1})
    pg_repository.upsert_step(
        claim, "verify", "test", "公开测试", "completed", {"status": "passed"}
    )
    steps = pg_repository.list_steps(run["id"])
    assert len(steps) == 1
    assert set(steps[0]) == {"id", "key", "kind", "name", "status", "metadata", "attempt_id"}
    assert steps[0]["metadata"] == {"status": "passed"}
    artifact, saved = checkpoint(pg_repository, claim)
    assert set(artifact) == {"id", "attempt_id", "kind", "relative_path", "sha256", "size"}
    assert set(saved) == {
        "id",
        "artifact_id",
        "base_sha256",
        "candidate_sha256",
        "metadata",
        "attempt_id",
    }
    assert pg_repository.get_checkpoint(saved["id"]) == saved
    assert pg_repository.get_run(run["id"])["latest_checkpoint_id"] == saved["id"]
    assert pg_repository.get_artifact(run["id"], artifact["id"]) == artifact
    other = new_run(pg_repository)
    assert pg_repository.get_artifact(other["id"], artifact["id"]) is None
    assert pg_repository.list_artifacts(run["id"]) == [artifact]
    usage = {"input_tokens": 8, "output_tokens": 2, "model_calls": 1}
    pg_repository.finish(claim, "completed", "failed", "验收未通过", usage)
    finished = pg_repository.get_run(run["id"])
    assert finished["execution_status"] == "completed"
    assert finished["verdict"] == "failed"
    assert finished["usage"] == usage
    assert pg_repository.list_attempts(run["id"])[0]["usage"] == usage


WRITES = [
    "heartbeat",
    "append_event",
    "upsert_step",
    "register_artifact",
    "save_checkpoint",
    "finish",
]


def perform_write(repository, claim, method, artifact_id):
    if method == "heartbeat":
        return repository.heartbeat(claim)
    if method == "append_event":
        return repository.append_event(claim, "progress", {"number": 2})
    if method == "upsert_step":
        return repository.upsert_step(claim, "late", "test", "迟到步骤", "started", {})
    if method == "register_artifact":
        return repository.register_artifact(claim, "patch", "late.patch", DIGEST, 0)
    if method == "save_checkpoint":
        return repository.save_checkpoint(claim, artifact_id, BASE, CANDIDATE, {})
    return repository.finish(claim, "completed", "passed", "", {})


@pytest.mark.postgres
@pytest.mark.parametrize("method", WRITES)
@pytest.mark.parametrize(
    "invalid", ["expired", "token", "unicode_token", "worker", "attempt", "run"]
)
def test_every_worker_write_is_fenced(pg_repository, method, invalid):
    from patchpilot.service.repository import LeaseLost

    run = new_run(pg_repository)
    claim = pg_repository.claim("worker-1")
    artifact, _ = checkpoint(pg_repository, claim)
    if invalid == "expired":
        expire(pg_repository, claim)
    elif invalid == "token":
        claim = replace(claim, token="bad-token")
    elif invalid == "unicode_token":
        claim = replace(claim, token="伪造令牌")
    elif invalid == "worker":
        claim = replace(claim, worker_id="other-worker")
    elif invalid == "attempt":
        claim = replace(claim, attempt_id="missing")
    else:
        claim = replace(claim, run_id="missing")
    before = pg_repository.list_events(run["id"])
    with pytest.raises(LeaseLost):
        perform_write(pg_repository, claim, method, artifact["id"])
    assert pg_repository.list_events(run["id"]) == before
    assert pg_repository.get_run(run["id"])["execution_status"] == "running"


@pytest.mark.postgres
def test_expired_heartbeat_cannot_revive_and_reap_restart_reuses_checkpoint(pg_repository):
    from patchpilot.service.repository import LeaseLost

    run = new_run(pg_repository)
    first = pg_repository.claim("worker-1")
    pg_repository.upsert_step(first, "patch", "tool", "修改", "started", {})
    artifact, saved = checkpoint(pg_repository, first)
    expire(pg_repository, first)
    with pytest.raises(LeaseLost):
        pg_repository.heartbeat(first, lease_seconds=100)
    assert pg_repository.reap_expired() == [{"run_id": run["id"], "attempt_id": first.attempt_id}]
    assert pg_repository.reap_expired() == []
    assert pg_repository.get_run(run["id"])["execution_status"] == "interrupted"
    assert pg_repository.list_steps(run["id"])[0]["status"] == "unknown"
    restarted = pg_repository.restart(run["id"])
    assert restarted["execution_status"] == "queued"
    assert restarted["current_attempt_id"] is None
    assert restarted["latest_checkpoint_id"] == saved["id"]
    second = pg_repository.claim("worker-2")
    assert second.checkpoint_id == saved["id"]
    assert second.attempt_id != first.attempt_id
    pg_repository.upsert_step(second, "patch", "tool", "重新修改", "started", {})
    assert len(pg_repository.list_steps(run["id"])) == 2
    for method in WRITES:
        with pytest.raises(LeaseLost):
            perform_write(pg_repository, first, method, artifact["id"])
    assert pg_repository.get_checkpoint(saved["id"])["artifact_id"] == artifact["id"]
    assert len(pg_repository.list_attempts(run["id"])) == 2
    assert pg_repository.get_run(run["id"])["current_attempt_id"] == second.attempt_id


@pytest.mark.postgres
@pytest.mark.parametrize("action", ["finish", "reap"])
def test_cancel_running_wins_late_success_or_reap(pg_repository, action):
    from patchpilot.service.repository import LeaseLost, StateConflict

    run = new_run(pg_repository)
    claim = pg_repository.claim("worker-1")
    pg_repository.upsert_step(claim, "test", "test", "测试", "started", {})
    result = pg_repository.cancel(run["id"])
    assert result["execution_status"] == "running" and result["cancel_requested"]
    assert pg_repository.heartbeat(claim) is True
    if action == "finish":
        pg_repository.finish(claim, "completed", "passed", "迟到成功", {"input_tokens": 3})
    else:
        expire(pg_repository, claim)
        pg_repository.reap_expired()
        assert pg_repository.list_steps(run["id"])[0]["status"] == "unknown"
    final = pg_repository.get_run(run["id"])
    assert final["execution_status"] == "cancelled" and final["verdict"] == "not_run"
    count = len(pg_repository.list_events(run["id"]))
    assert pg_repository.cancel(run["id"]) == final
    assert len(pg_repository.list_events(run["id"])) == count
    with pytest.raises(StateConflict):
        pg_repository.restart(run["id"])
    with pytest.raises(LeaseLost):
        pg_repository.finish(claim, "completed", "passed", "", {})


@pytest.mark.postgres
def test_queue_cancel_and_restart_state_machine(pg_repository):
    from patchpilot.service.repository import StateConflict

    run = new_run(pg_repository)
    with pytest.raises(StateConflict):
        pg_repository.restart(run["id"])
    assert pg_repository.cancel("missing") is None
    with pytest.raises(StateConflict):
        pg_repository.restart("missing")
    assert pg_repository.cancel(run["id"])["execution_status"] == "cancelled"
    assert pg_repository.claim("worker-1") is None
    failed = new_run(pg_repository)
    claim = pg_repository.claim("worker-1")
    pg_repository.finish(claim, "failed", "infra_error", "执行失败", {"input_tokens": 2})
    before = pg_repository.list_attempts(failed["id"])
    restarted = pg_repository.restart(failed["id"])
    assert restarted["verdict"] == "not_run" and restarted["reason"] == ""
    assert restarted["usage"] == {} and restarted["cancel_requested"] is False
    assert pg_repository.list_attempts(failed["id"]) == before


@pytest.mark.postgres
def test_heartbeat_uses_database_clock_and_returns_cancellation(pg_repository):
    new_run(pg_repository)
    claim = pg_repository.claim("worker-1", lease_seconds=1)
    assert pg_repository.heartbeat(claim, lease_seconds=30) is False
    with pg_repository.engine.connect() as connection:
        remaining = connection.execute(
            text(
                "SELECT extract(epoch FROM lease_until-clock_timestamp()) FROM attempt WHERE id=:id"
            ),
            {"id": claim.attempt_id},
        ).scalar_one()
    assert 20 < remaining <= 30


@pytest.mark.postgres
@pytest.mark.parametrize(
    "path",
    ["../x", "a/../b", "/tmp/x", "C:/secret", "a\\b", "a//b", "./x", "a/", "", "https://x/y"],
)
def test_artifact_path_is_canonical_relative(pg_repository, path):
    new_run(pg_repository)
    claim = pg_repository.claim("worker-1")
    with pytest.raises(ValueError):
        pg_repository.register_artifact(claim, "patch", path, DIGEST, 0)


@pytest.mark.postgres
def test_invalid_artifact_and_cross_attempt_checkpoint_rejected(pg_repository):
    run = new_run(pg_repository)
    claim = pg_repository.claim("worker-1")
    for digest, size in [("bad", 0), (DIGEST, -1), (DIGEST, True)]:
        with pytest.raises(ValueError):
            pg_repository.register_artifact(claim, "patch", "candidate.patch", digest, size)
    artifact, _ = checkpoint(pg_repository, claim)
    with pytest.raises(ValueError):
        pg_repository.save_checkpoint(claim, artifact["id"], "d" * 64, CANDIDATE, {})
    another = new_run(pg_repository)
    other_claim = pg_repository.claim("worker-2")
    assert other_claim.run_id == another["id"]
    with pytest.raises(ValueError):
        pg_repository.save_checkpoint(other_claim, artifact["id"], BASE, CANDIDATE, {})
    assert pg_repository.get_run(run["id"])["latest_checkpoint_id"] is not None


@pytest.mark.postgres
@pytest.mark.parametrize(
    "metadata",
    [
        {"messages": [{"role": "user", "content": "完整消息"}]},
        {"thinking": "内部推理"},
        {"log": "完整日志"},
        {"workspace_path": "C:/private"},
        {"database_url": "postgresql://private"},
        {"arbitrary": 1},
        {"reason": "x" * 5000},
        {"number": float("nan")},
        {"usage": {"token": "secret"}},
        {"metadata": {"messages": "secret"}},
    ],
)
def test_json_metadata_is_bounded_and_allowlisted(pg_repository, metadata):
    new_run(pg_repository)
    claim = pg_repository.claim("worker-1")
    with pytest.raises(ValueError):
        pg_repository.append_event(claim, "progress", metadata)
    with pytest.raises(ValueError):
        pg_repository.upsert_step(claim, "key", "test", "测试", "started", metadata)
    artifact = pg_repository.register_artifact(claim, "patch", "safe.patch", DIGEST, 0)
    with pytest.raises(ValueError):
        pg_repository.save_checkpoint(claim, artifact["id"], BASE, CANDIDATE, metadata)


@pytest.mark.postgres
def test_budget_usage_and_completion_values_are_validated(pg_repository):
    for budget in (
        {"database_url": "private"},
        {"max_model_calls": -1},
        {"timeout_seconds": float("inf")},
    ):
        with pytest.raises(ValueError):
            pg_repository.create_run("pagination-001", "agent", budget, BASE)
    new_run(pg_repository)
    claim = pg_repository.claim("worker-1")
    for usage in ({"messages": []}, {"input_tokens": -1}, {"input_tokens": True}):
        with pytest.raises(ValueError):
            pg_repository.finish(claim, "completed", "passed", "", usage)
    with pytest.raises(ValueError):
        pg_repository.finish(claim, "queued", "passed", "", {})
    for seconds in (0, -1, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            pg_repository.heartbeat(claim, seconds)


@pytest.mark.postgres
def test_concurrent_migration_reuses_connection_and_model_matches_schema(pg_repository):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from patchpilot.service.database import migrate
    from patchpilot.service.models import metadata

    url = pg_repository.engine.url.render_as_string(hide_password=False)
    barrier = Barrier(3)

    def upgrade(_):
        barrier.wait()
        migrate(url)

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(upgrade, range(3)))
    with pg_repository.engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), metadata) == []


@pytest.mark.postgres
def test_finished_attempt_rejects_all_later_writes(pg_repository):
    from patchpilot.service.repository import LeaseLost

    run = new_run(pg_repository)
    claim = pg_repository.claim("worker-1")
    artifact, _ = checkpoint(pg_repository, claim)
    pg_repository.finish(claim, "completed", "passed", "完成", {})
    events = pg_repository.list_events(run["id"])
    for method in WRITES:
        with pytest.raises(LeaseLost):
            perform_write(pg_repository, claim, method, artifact["id"])
    assert pg_repository.list_events(run["id"])[-1]["kind"] == "run_finished"
    assert pg_repository.list_events(run["id"]) == events


@pytest.mark.postgres
def test_event_failure_rolls_back_sequence_and_terminal_state(pg_repository, monkeypatch):
    run = new_run(pg_repository)
    claim = pg_repository.claim("worker-1")
    real_event = pg_repository._event

    def failing_event(*args, **kwargs):
        real_event(*args, **kwargs)
        raise RuntimeError("模拟事件写入后事务异常")

    with monkeypatch.context() as scoped:
        scoped.setattr(pg_repository, "_event", failing_event)
        with pytest.raises(RuntimeError, match="模拟"):
            pg_repository.finish(claim, "completed", "passed", "", {"input_tokens": 4})
    assert pg_repository.get_run(run["id"])["execution_status"] == "running"
    assert pg_repository.list_attempts(run["id"])[0]["execution_status"] == "running"
    appended = pg_repository.append_event(claim, "progress", {"number": 1})
    assert appended["seq"] == 3
    assert [event["seq"] for event in pg_repository.list_events(run["id"])] == [1, 2, 3]


@pytest.mark.postgres
def test_old_checkpoint_is_readable_but_new_checkpoint_requires_current_attempt_artifact(
    pg_repository,
):
    run = new_run(pg_repository)
    first = pg_repository.claim("worker-1")
    artifact, saved = checkpoint(pg_repository, first)
    pg_repository.finish(first, "failed", "infra_error", "测试故障", {})
    pg_repository.restart(run["id"])
    second = pg_repository.claim("worker-2")
    assert second.checkpoint_id == saved["id"]
    assert pg_repository.get_artifact(run["id"], artifact["id"]) == artifact
    with pytest.raises(ValueError):
        pg_repository.save_checkpoint(second, artifact["id"], BASE, CANDIDATE, {})
    assert pg_repository.get_run(run["id"])["latest_checkpoint_id"] == saved["id"]
    assert pg_repository.get_checkpoint(saved["id"]) == saved


@pytest.mark.postgres
def test_concurrent_reapers_do_not_duplicate_terminal_events(pg_repository):
    run = new_run(pg_repository)
    claim = pg_repository.claim("worker-1")
    expire(pg_repository, claim)
    barrier = Barrier(4)

    def reap(_):
        barrier.wait()
        return pg_repository.reap_expired()

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(reap, range(4)))
    assert sum(len(result) for result in results) == 1
    events = pg_repository.list_events(run["id"])
    assert [event["kind"] for event in events].count("run_interrupted") == 1
    assert [event["seq"] for event in events] == [1, 2, 3]


@pytest.mark.postgres
def test_metadata_total_bytes_and_public_reason_reject_private_content(pg_repository):
    from patchpilot.service.repository import METADATA_KEYS

    run = new_run(pg_repository)
    claim = pg_repository.claim("worker-1")
    metadata = {key: "摘要" * 300 for key in METADATA_KEYS if key != "usage"}
    with pytest.raises(ValueError, match="大小上限"):
        pg_repository.append_event(claim, "progress", metadata)
    for reason in (
        "postgresql+psycopg://private/db",
        "发生错误 C:/private/workspace/file.py",
        "发生错误 /private/workspace/file.py",
        "含有\x00控制字符",
    ):
        with pytest.raises(ValueError):
            pg_repository.finish(claim, "failed", "infra_error", reason, {})
        with pytest.raises(ValueError):
            pg_repository.append_event(claim, "progress", {"reason": reason})
    assert pg_repository.get_run(run["id"])["reason"] == ""


@pytest.mark.postgres
def test_all_worker_metadata_keys_are_supported(pg_repository):
    run = new_run(pg_repository)
    claim = pg_repository.claim("worker-1")
    payload = {
        "event": "model_finished",
        "number": 1,
        "reason": "摘要",
        "status": "completed",
        "call_id": "toolu_123",
        "tool": "read_file",
        "ok": True,
        "truncated": False,
        "model_kind": "fake",
        "executor_kind": "diagnostic",
        "model_calls": 1,
        "tool_calls": 2,
        "calls_without_usage": 0,
        "usage": {
            "input_tokens": 4,
            "output_tokens": 2,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
        "boundary": "tool_batch",
        "resumed": True,
        "checkpoint_id": "id",
        "base_sha256": BASE,
        "candidate_sha256": CANDIDATE,
        "patch_sha256": DIGEST,
        "artifact_id": "id",
        "attempt_id": claim.attempt_id,
    }
    event = pg_repository.append_event(claim, "progress", payload)
    assert pg_repository.list_events(run["id"])[-1] == event
    assert event["payload"] == payload


@pytest.mark.postgres
def test_list_runs_returns_public_shapes_in_reverse_creation_order(pg_repository):
    runs = [new_run(pg_repository) for _ in range(3)]
    pg_repository.cancel(runs[0]["id"])
    history = pg_repository.list_runs()
    assert [run["id"] for run in history] == [run["id"] for run in reversed(runs)]
    assert history == [pg_repository.get_run(run["id"]) for run in history]
    assert pg_repository.list_runs(limit=2) == history[:2]
    for limit in (0, -1, True, 1001):
        with pytest.raises(ValueError):
            pg_repository.list_runs(limit=limit)


@pytest.mark.postgres
@pytest.mark.parametrize("cancelled", [False, True])
def test_finalize_report_matches_effective_terminal_state(pg_repository, tmp_path, cancelled):
    import hashlib

    run = new_run(pg_repository)
    claim = pg_repository.claim("worker-1")
    if cancelled:
        pg_repository.cancel(run["id"])
    calls = []
    report_path = tmp_path / "report.json"

    def report_factory(status, verdict, reason):
        calls.append((status, verdict, reason))
        content = json.dumps(
            {"execution_status": status, "verdict": verdict, "reason": reason}
        ).encode()
        report_path.write_bytes(content)
        return {
            "relative_path": "attempt/report.json",
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
        }

    artifact = pg_repository.finalize(
        claim, "completed", "passed", "执行完成", {"input_tokens": 3}, report_factory
    )
    final = pg_repository.get_run(run["id"])
    report = json.loads(report_path.read_bytes())
    effective = (
        ("cancelled", "not_run", "cancel_requested")
        if cancelled
        else ("completed", "passed", "执行完成")
    )
    assert calls == [effective]
    assert (final["execution_status"], final["verdict"], final["reason"]) == effective
    assert report == {key: final[key] for key in ("execution_status", "verdict", "reason")}
    assert artifact["kind"] == "report" and artifact["attempt_id"] == claim.attempt_id
    assert pg_repository.get_artifact(run["id"], artifact["id"]) == artifact
    assert pg_repository.list_artifacts(run["id"]) == [artifact]
    assert final["usage"] == {"input_tokens": 3}
    assert pg_repository.list_attempts(run["id"])[0]["execution_status"] == effective[0]
    assert pg_repository.list_events(run["id"])[-1]["payload"] == report


@pytest.mark.postgres
@pytest.mark.parametrize("failure", ["factory", "event", "invalid_ref"])
def test_finalize_failure_rolls_back_registration_and_terminal_state(
    pg_repository, tmp_path, monkeypatch, failure
):
    run = new_run(pg_repository)
    claim = pg_repository.claim("worker-1")
    before = pg_repository.list_events(run["id"])
    orphan = tmp_path / "orphan.json"

    def report_factory(*_):
        orphan.write_text("{}", encoding="utf-8")
        if failure == "factory":
            raise RuntimeError("报告封存失败")
        return {
            "relative_path": "../report.json"
            if failure == "invalid_ref"
            else "attempt/report.json",
            "sha256": DIGEST,
            "size": 2,
        }

    real_event = pg_repository._event

    def failing_event(*args, **kwargs):
        real_event(*args, **kwargs)
        raise RuntimeError("终止事件失败")

    if failure == "event":
        monkeypatch.setattr(pg_repository, "_event", failing_event)
    with pytest.raises(ValueError if failure == "invalid_ref" else RuntimeError):
        pg_repository.finalize(claim, "completed", "passed", "", {}, report_factory)
    assert orphan.exists()
    assert pg_repository.get_run(run["id"])["execution_status"] == "running"
    attempt = pg_repository.list_attempts(run["id"])[0]
    assert attempt["execution_status"] == "running" and attempt["lease_until"] is not None
    assert pg_repository.list_artifacts(run["id"]) == []
    assert pg_repository.list_events(run["id"]) == before


@pytest.mark.postgres
@pytest.mark.parametrize("stale", ["expired", "token", "old_attempt"])
def test_finalize_rejects_stale_claim_before_calling_report_factory(pg_repository, stale):
    from unittest.mock import Mock

    from patchpilot.service.repository import LeaseLost

    run = new_run(pg_repository)
    claim = pg_repository.claim("worker-1")
    if stale == "expired":
        expire(pg_repository, claim)
    elif stale == "token":
        claim = replace(claim, token="wrong")
    else:
        pg_repository.finish(claim, "failed", "infra_error", "执行失败", {})
        pg_repository.restart(run["id"])
        pg_repository.claim("worker-2")
    before = pg_repository.list_events(run["id"])
    factory = Mock(
        return_value={"relative_path": "attempt/report.json", "sha256": DIGEST, "size": 2}
    )
    with pytest.raises(LeaseLost):
        pg_repository.finalize(claim, "completed", "passed", "", {}, factory)
    factory.assert_not_called()
    assert pg_repository.list_events(run["id"]) == before
    assert pg_repository.list_artifacts(run["id"]) == []
    assert pg_repository.get_run(run["id"])["execution_status"] == "running"


@pytest.mark.parametrize("method", ["repository", "migrate"])
def test_sqlite_cannot_substitute_for_postgresql(method):
    from patchpilot.service.database import migrate
    from patchpilot.service.repository import Repository

    with pytest.raises(ValueError, match="PostgreSQL"):
        (Repository if method == "repository" else migrate)("sqlite:///:memory:")

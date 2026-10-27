"""同步 PostgreSQL 仓储：Run 行锁、数据库时钟租约及所有 Worker 写入 fencing。"""

import json
import math
import re
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from patchpilot.service import models as m
from patchpilot.service.database import make_engine

MAX_METADATA_BYTES = 16 * 1024
MAX_TEXT_LENGTH = 1024
BUDGET_KEYS = frozenset(
    {
        "max_model_calls",
        "max_tool_calls",
        "timeout_seconds",
        "max_total_tokens",
        "max_context_chars",
        "max_repeated_actions",
        "max_output_tokens",
    }
)
USAGE_KEYS = frozenset(
    {
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "model_calls",
        "tool_calls",
        "calls_without_usage",
    }
)
METADATA_KEYS = frozenset(
    {
        "event",
        "number",
        "reason",
        "status",
        "call_id",
        "tool",
        "ok",
        "truncated",
        "model_kind",
        "executor_kind",
        "model_calls",
        "tool_calls",
        "calls_without_usage",
        "usage",
        "boundary",
        "resumed",
        "checkpoint_id",
        "base_sha256",
        "candidate_sha256",
        "patch_sha256",
        "artifact_id",
        "attempt_id",
        "step",
        "key",
        "kind",
        "name",
        "execution_status",
        "verdict",
        "cancel_requested",
        "input_tokens",
        "output_tokens",
        "cache_read_input_tokens",
        "cache_creation_input_tokens",
        "count",
        "exit_code",
        "duration_seconds",
        "size",
        "sha256",
    }
)
_LABEL = re.compile(r"[\w][\w.:-]*", re.UNICODE)
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
_PRIVATE_TEXT = re.compile(
    r"(?:[a-z][a-z0-9+.-]*://|[a-z]:[\\/]|\\\\|(?:^|\s)/(?:[^\s/]+/))",
    re.IGNORECASE,
)
RUN_FIELDS = (
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
)
ATTEMPT_FIELDS = (
    "id",
    "run_id",
    "worker_id",
    "execution_status",
    "verdict",
    "reason",
    "usage",
    "checkpoint_id",
    "lease_until",
    "started_at",
    "finished_at",
)
STEP_FIELDS = ("id", "key", "kind", "name", "status", "metadata", "attempt_id")
ARTIFACT_FIELDS = ("id", "attempt_id", "kind", "relative_path", "sha256", "size")
CHECKPOINT_FIELDS = (
    "id",
    "artifact_id",
    "base_sha256",
    "candidate_sha256",
    "metadata",
    "attempt_id",
)
EVENT_FIELDS = ("seq", "kind", "payload", "attempt_id")


class LeaseLost(RuntimeError):
    """当前 Worker 已无权写入；不携带 token 或底层连接信息。"""


class StateConflict(RuntimeError):
    """Run 当前状态不允许所请求的转换。"""


@dataclass(frozen=True)
class Claim:
    run_id: str
    attempt_id: str
    token: str = field(repr=False)
    worker_id: str
    task_id: str
    mode: str
    budget: dict
    base_sha256: str
    checkpoint_id: str | None


def _id() -> str:
    return str(uuid.uuid4())


def _label(value: str, name: str, maximum: int = 64) -> str:
    if not isinstance(value, str) or len(value) > maximum or not _LABEL.fullmatch(value):
        raise ValueError(f"{name} 必须为有界安全标识")
    return value


def _safe_text(value: str, name: str, maximum: int = MAX_TEXT_LENGTH) -> str:
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or _PRIVATE_TEXT.search(value)
    ):
        raise ValueError(f"{name} 必须为有界摘要，不能包含地址、私密路径或控制字符")
    return value


def _digest(value: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ValueError("sha256 必须为 64 位十六进制字符串")
    return value.lower()


def _json_copy(value: dict) -> dict:
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > MAX_METADATA_BYTES:
            raise ValueError("JSON 元数据超过大小上限")
        return json.loads(encoded)
    except (TypeError, OverflowError, UnicodeError) as error:
        raise ValueError("元数据必须为有界 JSON") from error


def _counts(value: dict, keys: frozenset[str], *, positive: bool = False) -> dict:
    if not isinstance(value, dict) or not value.keys() <= keys:
        raise ValueError("预算或用量包含非白名单字段")
    for key, number in value.items():
        if key == "timeout_seconds" and positive:
            valid = type(number) in (int, float) and math.isfinite(number) and number > 0
        else:
            valid = type(number) is int and (number > 0 if positive else number >= 0)
        if not valid or number > 2**63 - 1:
            raise ValueError("预算必须为正数，用量必须为非负整数")
    return _json_copy(value)


def _metadata(value: dict) -> dict:
    if not isinstance(value, dict) or not value.keys() <= METADATA_KEYS:
        raise ValueError("元数据包含非白名单字段")
    result = {}
    for key, item in value.items():
        if key == "usage":
            result[key] = _counts(item, USAGE_KEYS)
        elif isinstance(item, str):
            result[key] = _safe_text(item, "元数据字符串")
        elif item is None or type(item) in (bool, int):
            result[key] = item
        elif type(item) is float and math.isfinite(item):
            result[key] = item
        else:
            raise ValueError("元数据仅允许摘要标量及白名单 usage")
    return _json_copy(result)


def _lease(seconds: float) -> timedelta:
    if type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("租约时长必须为有限正数")
    try:
        delta = timedelta(seconds=seconds)
    except OverflowError as error:
        raise ValueError("租约时长过大") from error
    if delta <= timedelta(0):
        raise ValueError("租约时长必须至少为一微秒")
    return delta


def _path(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1024
        or value.startswith("/")
        or "\\" in value
        or ":" in value
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or any(part in ("", ".", "..") for part in value.split("/"))
    ):
        raise ValueError("产物路径必须为规范 POSIX 相对路径，不允许点段、反斜线或地址")
    return value


def _public(row: Any, fields: tuple[str, ...]) -> dict | None:
    if row is None:
        return None
    return {
        key: row[key].isoformat() if isinstance(row[key], datetime) else row[key] for key in fields
    }


class Repository:
    def __init__(self, database_url: str):
        self.engine = make_engine(database_url)

    def close(self) -> None:
        self.engine.dispose()

    @staticmethod
    def _now(connection: Connection) -> datetime:
        return connection.scalar(select(func.clock_timestamp()))

    @staticmethod
    def _run(connection: Connection, run_id: str, *, lock: bool = False):
        statement = select(m.run).where(m.run.c.id == run_id)
        if lock:
            statement = statement.with_for_update()
        return connection.execute(statement).mappings().first()

    def _fence(self, connection: Connection, claim: Claim):
        # 所有转换统一按 Run→Attempt 顺序加锁，等待锁后再读取数据库时钟。
        current = self._run(connection, claim.run_id, lock=True)
        if (
            current is None
            or current["execution_status"] != "running"
            or current["current_attempt_id"] != claim.attempt_id
        ):
            raise LeaseLost("当前 Attempt 已失去租约")
        attempt = (
            connection.execute(
                select(m.attempt).where(m.attempt.c.id == claim.attempt_id).with_for_update()
            )
            .mappings()
            .first()
        )
        now = self._now(connection)
        if (
            attempt is None
            or attempt["run_id"] != claim.run_id
            or attempt["execution_status"] != "running"
            or attempt["worker_id"] != claim.worker_id
            or not isinstance(claim.token, str)
            or not claim.token.isascii()
            or not secrets.compare_digest(attempt["token"], claim.token)
            or attempt["lease_until"] is None
            or attempt["lease_until"] <= now
        ):
            raise LeaseLost("当前 Attempt 已失去租约")
        return current, attempt, now

    @staticmethod
    def _event(
        connection: Connection, run_id: str, attempt_id: str | None, kind: str, payload: dict
    ) -> dict:
        # 调用方已持有 Run 行锁；递增与插入在同一个事务，回滚不会留下序号缺口。
        seq = connection.execute(
            update(m.run)
            .where(m.run.c.id == run_id)
            .values(event_seq=m.run.c.event_seq + 1, updated_at=func.clock_timestamp())
            .returning(m.run.c.event_seq)
        ).scalar_one()
        connection.execute(
            insert(m.event).values(
                run_id=run_id,
                seq=seq,
                attempt_id=attempt_id,
                kind=kind,
                payload=payload,
            )
        )
        return {"seq": seq, "kind": kind, "payload": payload, "attempt_id": attempt_id}

    def create_run(self, task_id: str, mode: str, budget: dict, base_sha256: str) -> dict:
        task_id = _label(task_id, "task_id", 128)
        if mode not in ("agent", "diagnostic"):
            raise ValueError("mode 必须为 agent 或 diagnostic")
        budget = _counts(budget, BUDGET_KEYS, positive=True)
        base_sha256 = _digest(base_sha256)
        run_id = _id()
        with self.engine.begin() as connection:
            connection.execute(
                insert(m.run).values(
                    id=run_id,
                    task_id=task_id,
                    mode=mode,
                    budget=budget,
                    base_sha256=base_sha256,
                    execution_status="queued",
                )
            )
            self._event(connection, run_id, None, "run_created", {"status": "queued"})
            return _public(self._run(connection, run_id), RUN_FIELDS)

    def get_run(self, run_id: str) -> dict | None:
        with self.engine.connect() as connection:
            return _public(self._run(connection, run_id), RUN_FIELDS)

    def list_runs(self, limit: int = 50) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit 必须在 1 到 1000 之间")
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(m.run).order_by(m.run.c.created_at.desc(), m.run.c.id.desc()).limit(limit)
            ).mappings()
            return [_public(row, RUN_FIELDS) for row in rows]

    def list_attempts(self, run_id: str) -> list[dict]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(m.attempt)
                .where(m.attempt.c.run_id == run_id)
                .order_by(m.attempt.c.started_at, m.attempt.c.id)
            ).mappings()
            return [_public(row, ATTEMPT_FIELDS) for row in rows]

    def list_steps(self, run_id: str) -> list[dict]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(m.step)
                .where(m.step.c.run_id == run_id)
                .order_by(m.step.c.created_at, m.step.c.id)
            ).mappings()
            return [_public(row, STEP_FIELDS) for row in rows]

    def list_artifacts(self, run_id: str) -> list[dict]:
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(m.artifact)
                .where(m.artifact.c.run_id == run_id)
                .order_by(m.artifact.c.created_at, m.artifact.c.id)
            ).mappings()
            return [_public(row, ARTIFACT_FIELDS) for row in rows]

    def list_events(self, run_id: str, after: int = 0, limit: int = 100) -> list[dict]:
        if type(after) is not int or after < 0 or type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("after 必须非负，limit 必须在 1 到 1000 之间")
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(m.event)
                .where(m.event.c.run_id == run_id, m.event.c.seq > after)
                .order_by(m.event.c.seq)
                .limit(limit)
            ).mappings()
            return [_public(row, EVENT_FIELDS) for row in rows]

    def claim(
        self, worker_id: str, *, mode: str = "agent", lease_seconds: float = 15
    ) -> Claim | None:
        worker_id = _label(worker_id, "worker_id", 128)
        if mode not in ("agent", "diagnostic"):
            raise ValueError("mode 必须为 agent 或 diagnostic")
        lease = _lease(lease_seconds)
        with self.engine.begin() as connection:
            current = (
                connection.execute(
                    select(m.run)
                    .where(
                        m.run.c.execution_status == "queued",
                        m.run.c.mode == mode,
                        m.run.c.cancel_requested.is_(False),
                    )
                    .order_by(m.run.c.created_at, m.run.c.id)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if current is None:
                return None
            now = self._now(connection)
            attempt_id, token = _id(), secrets.token_urlsafe(32)
            connection.execute(
                insert(m.attempt).values(
                    id=attempt_id,
                    run_id=current["id"],
                    worker_id=worker_id,
                    token=token,
                    execution_status="running",
                    checkpoint_id=current["latest_checkpoint_id"],
                    lease_until=now + lease,
                    started_at=now,
                )
            )
            connection.execute(
                update(m.run)
                .where(m.run.c.id == current["id"])
                .values(
                    current_attempt_id=attempt_id,
                    execution_status="running",
                    updated_at=now,
                )
            )
            self._event(connection, current["id"], attempt_id, "run_claimed", {"status": "running"})
            return Claim(
                run_id=current["id"],
                attempt_id=attempt_id,
                token=token,
                worker_id=worker_id,
                task_id=current["task_id"],
                mode=current["mode"],
                budget=current["budget"],
                base_sha256=current["base_sha256"],
                checkpoint_id=current["latest_checkpoint_id"],
            )

    def heartbeat(self, claim: Claim, lease_seconds: float = 15) -> bool:
        lease = _lease(lease_seconds)
        with self.engine.begin() as connection:
            current, _, now = self._fence(connection, claim)
            connection.execute(
                update(m.attempt)
                .where(m.attempt.c.id == claim.attempt_id)
                .values(
                    lease_until=now + lease,
                )
            )
            return current["cancel_requested"]

    def append_event(self, claim: Claim, kind: str, payload: dict) -> dict:
        kind, payload = _label(kind, "事件类型"), _metadata(payload)
        with self.engine.begin() as connection:
            self._fence(connection, claim)
            return self._event(connection, claim.run_id, claim.attempt_id, kind, payload)

    def upsert_step(
        self, claim: Claim, key: str, kind: str, name: str, status: str, metadata: dict
    ) -> None:
        key, kind = _label(key, "步骤 key", 256), _label(kind, "步骤类型")
        name, metadata = _safe_text(name, "步骤名称", 256), _metadata(metadata)
        if status not in ("started", "completed", "failed", "cancelled", "unknown", "skipped"):
            raise ValueError("不支持的步骤状态")
        with self.engine.begin() as connection:
            self._fence(connection, claim)
            statement = pg_insert(m.step).values(
                id=_id(),
                run_id=claim.run_id,
                attempt_id=claim.attempt_id,
                key=key,
                kind=kind,
                name=name,
                status=status,
                metadata=metadata,
            )
            connection.execute(
                statement.on_conflict_do_update(
                    constraint="uq_step_attempt_key",
                    set_={
                        "kind": kind,
                        "name": name,
                        "status": status,
                        "metadata": metadata,
                        "updated_at": func.clock_timestamp(),
                    },
                )
            )

    def register_artifact(
        self, claim: Claim, kind: str, relative_path: str, sha256: str, size: int
    ) -> dict:
        kind = _label(kind, "产物类型")
        relative_path, sha256 = _path(relative_path), _digest(sha256)
        if type(size) is not int or not 0 <= size <= 2**63 - 1:
            raise ValueError("产物大小必须为非负整数")
        values = {
            "id": _id(),
            "attempt_id": claim.attempt_id,
            "kind": kind,
            "relative_path": relative_path,
            "sha256": sha256,
            "size": size,
        }
        with self.engine.begin() as connection:
            self._fence(connection, claim)
            connection.execute(insert(m.artifact).values(run_id=claim.run_id, **values))
            return values

    def save_checkpoint(
        self,
        claim: Claim,
        artifact_id: str,
        base_sha256: str,
        candidate_sha256: str,
        metadata: dict,
    ) -> dict:
        base_sha256, candidate_sha256 = _digest(base_sha256), _digest(candidate_sha256)
        metadata = _metadata(metadata)
        with self.engine.begin() as connection:
            current, _, _ = self._fence(connection, claim)
            artifact = (
                connection.execute(
                    select(m.artifact).where(
                        m.artifact.c.id == artifact_id,
                        m.artifact.c.run_id == claim.run_id,
                        m.artifact.c.attempt_id == claim.attempt_id,
                    )
                )
                .mappings()
                .first()
            )
            if artifact is None or base_sha256 != current["base_sha256"]:
                raise ValueError("checkpoint 必须引用当前 Attempt 产物及 Run 固定基线")
            values = {
                "id": _id(),
                "artifact_id": artifact_id,
                "base_sha256": base_sha256,
                "candidate_sha256": candidate_sha256,
                "metadata": metadata,
                "attempt_id": claim.attempt_id,
            }
            connection.execute(insert(m.checkpoint).values(run_id=claim.run_id, **values))
            connection.execute(
                update(m.run)
                .where(m.run.c.id == claim.run_id)
                .values(
                    latest_checkpoint_id=values["id"],
                    updated_at=func.clock_timestamp(),
                )
            )
            return values

    def get_checkpoint(self, checkpoint_id: str) -> dict | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(select(m.checkpoint).where(m.checkpoint.c.id == checkpoint_id))
                .mappings()
                .first()
            )
            return _public(row, CHECKPOINT_FIELDS)

    def get_artifact(self, run_id: str, artifact_id: str) -> dict | None:
        with self.engine.connect() as connection:
            row = (
                connection.execute(
                    select(m.artifact).where(
                        m.artifact.c.id == artifact_id,
                        m.artifact.c.run_id == run_id,
                    )
                )
                .mappings()
                .first()
            )
            return _public(row, ARTIFACT_FIELDS)

    @staticmethod
    def _unknown_steps(connection: Connection, attempt_id: str) -> None:
        connection.execute(
            update(m.step)
            .where(
                m.step.c.attempt_id == attempt_id,
                m.step.c.status == "started",
            )
            .values(status="unknown", updated_at=func.clock_timestamp())
        )

    def _finish_locked(
        self,
        connection: Connection,
        claim: Claim,
        now: datetime,
        execution_status: str,
        verdict: str,
        reason: str,
        usage: dict,
    ) -> None:
        connection.execute(
            update(m.attempt)
            .where(m.attempt.c.id == claim.attempt_id)
            .values(
                execution_status=execution_status,
                verdict=verdict,
                reason=reason,
                usage=usage,
                lease_until=None,
                finished_at=now,
            )
        )
        connection.execute(
            update(m.run)
            .where(m.run.c.id == claim.run_id)
            .values(
                execution_status=execution_status,
                verdict=verdict,
                reason=reason,
                usage=usage,
                updated_at=now,
            )
        )
        self._unknown_steps(connection, claim.attempt_id)
        self._event(
            connection,
            claim.run_id,
            claim.attempt_id,
            "run_finished",
            {
                "execution_status": execution_status,
                "verdict": verdict,
                "reason": reason,
            },
        )

    @staticmethod
    def _completion_values(
        execution_status: str, verdict: str, reason: str, usage: dict
    ) -> tuple[str, str, str, dict]:
        if execution_status not in ("completed", "failed"):
            raise ValueError("finish 仅接受 completed 或 failed")
        return (
            execution_status,
            _label(verdict, "判定", 32),
            _safe_text(reason, "结束原因"),
            _counts(usage, USAGE_KEYS),
        )

    def finish(
        self, claim: Claim, execution_status: str, verdict: str, reason: str, usage: dict
    ) -> None:
        execution_status, verdict, reason, usage = self._completion_values(
            execution_status, verdict, reason, usage
        )
        with self.engine.begin() as connection:
            current, _, now = self._fence(connection, claim)
            if current["cancel_requested"]:
                execution_status, verdict, reason = "cancelled", "not_run", "cancel_requested"
            self._finish_locked(connection, claim, now, execution_status, verdict, reason, usage)

    def finalize(
        self,
        claim: Claim,
        execution_status: str,
        verdict: str,
        reason: str,
        usage: dict,
        report_factory: Callable[[str, str, str], dict],
    ) -> dict:
        """锁内封存最终报告并提交终态；回调仅写小文件，不得重入仓储。"""
        execution_status, verdict, reason, usage = self._completion_values(
            execution_status, verdict, reason, usage
        )
        with self.engine.begin() as connection:
            current, _, _ = self._fence(connection, claim)
            if current["cancel_requested"]:
                execution_status, verdict, reason = "cancelled", "not_run", "cancel_requested"
            reference = report_factory(execution_status, verdict, reason)
            if (
                not isinstance(reference, dict)
                or not {"relative_path", "sha256", "size"} <= reference.keys()
            ):
                raise ValueError("报告回调必须返回 relative_path、sha256 和 size 引用")
            size = reference["size"]
            if type(size) is not int or not 0 <= size <= 2**63 - 1:
                raise ValueError("产物大小必须为非负整数")
            artifact = {
                "id": _id(),
                "attempt_id": claim.attempt_id,
                "kind": "report",
                "relative_path": _path(reference["relative_path"]),
                "sha256": _digest(reference["sha256"]),
                "size": size,
            }
            # 文件写入后再次校验 DB 时钟，避免回调耗尽租约后公开登记。
            _, _, now = self._fence(connection, claim)
            connection.execute(insert(m.artifact).values(run_id=claim.run_id, **artifact))
            self._finish_locked(connection, claim, now, execution_status, verdict, reason, usage)
            return artifact

    def cancel(self, run_id: str) -> dict | None:
        with self.engine.begin() as connection:
            current = self._run(connection, run_id, lock=True)
            if current is None:
                return None
            if current["execution_status"] == "queued":
                connection.execute(
                    update(m.run)
                    .where(m.run.c.id == run_id)
                    .values(
                        execution_status="cancelled",
                        verdict="not_run",
                        reason="cancel_requested",
                        cancel_requested=True,
                        updated_at=func.clock_timestamp(),
                    )
                )
                self._event(connection, run_id, None, "run_cancelled", {"status": "cancelled"})
            elif current["execution_status"] == "running" and not current["cancel_requested"]:
                connection.execute(
                    update(m.run)
                    .where(m.run.c.id == run_id)
                    .values(
                        cancel_requested=True,
                        updated_at=func.clock_timestamp(),
                    )
                )
                self._event(
                    connection, run_id, current["current_attempt_id"], "cancel_requested", {}
                )
            return _public(self._run(connection, run_id), RUN_FIELDS)

    def reap_expired(self) -> list[dict]:
        reaped = []
        with self.engine.begin() as connection:
            # 只锁 Run，避免与 Worker 的 Run→Attempt 顺序形成反向死锁。
            rows = (
                connection.execute(
                    select(m.run)
                    .join(m.attempt, m.run.c.current_attempt_id == m.attempt.c.id)
                    .where(
                        m.run.c.execution_status == "running",
                        m.attempt.c.execution_status == "running",
                        m.attempt.c.lease_until <= func.clock_timestamp(),
                    )
                    .order_by(m.run.c.id)
                    .with_for_update(of=m.run, skip_locked=True)
                )
                .mappings()
                .all()
            )
            for current in rows:
                attempt_id = current["current_attempt_id"]
                attempt = (
                    connection.execute(
                        select(m.attempt).where(m.attempt.c.id == attempt_id).with_for_update()
                    )
                    .mappings()
                    .one()
                )
                now = self._now(connection)
                if attempt["lease_until"] is None or attempt["lease_until"] > now:
                    continue
                status = "cancelled" if current["cancel_requested"] else "interrupted"
                reason = "cancel_requested" if current["cancel_requested"] else "lease_expired"
                connection.execute(
                    update(m.attempt)
                    .where(m.attempt.c.id == attempt_id)
                    .values(
                        execution_status=status,
                        verdict="not_run",
                        reason=reason,
                        lease_until=None,
                        finished_at=now,
                    )
                )
                connection.execute(
                    update(m.run)
                    .where(m.run.c.id == current["id"])
                    .values(
                        execution_status=status,
                        verdict="not_run",
                        reason=reason,
                        updated_at=now,
                    )
                )
                self._unknown_steps(connection, attempt_id)
                self._event(
                    connection,
                    current["id"],
                    attempt_id,
                    f"run_{status}",
                    {
                        "execution_status": status,
                        "verdict": "not_run",
                        "reason": reason,
                    },
                )
                reaped.append({"run_id": current["id"], "attempt_id": attempt_id})
        return reaped

    def restart(self, run_id: str) -> dict:
        with self.engine.begin() as connection:
            current = self._run(connection, run_id, lock=True)
            if current is None or current["execution_status"] not in ("interrupted", "failed"):
                raise StateConflict("仅 interrupted 或 failed Run 可以重启")
            connection.execute(
                update(m.run)
                .where(m.run.c.id == run_id)
                .values(
                    execution_status="queued",
                    cancel_requested=False,
                    current_attempt_id=None,
                    verdict="not_run",
                    reason="",
                    usage={},
                    updated_at=func.clock_timestamp(),
                )
            )
            self._event(
                connection,
                run_id,
                None,
                "run_restarted",
                {
                    "status": "queued",
                    "checkpoint_id": current["latest_checkpoint_id"],
                },
            )
            return _public(self._run(connection, run_id), RUN_FIELDS)

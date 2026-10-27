"""独立Worker：领取、心跳、合作式取消、检查点及fenced结果提交。"""

import json
import re
import uuid
from threading import Event, Thread

from sqlalchemy.exc import SQLAlchemyError

from patchpilot.control import ExecutionControl, ExecutionStopped
from patchpilot.patching import export_diff
from patchpilot.sandbox import DockerRunner
from patchpilot.service.artifacts import ArtifactStorage, restore_workspace
from patchpilot.service.catalog import TaskCatalog
from patchpilot.service.execution import ExecutionHooks
from patchpilot.service.execution import ExecutionOutput as ExecutionOutput
from patchpilot.service.executors import AgentExecutor, ReferenceDiagnosticExecutor
from patchpilot.service.maintenance import reap_and_cleanup
from patchpilot.service.repository import METADATA_KEYS, USAGE_KEYS, LeaseLost
from patchpilot.snapshot import compute_snapshot_hash
from patchpilot.workspace import create_workspace


def _label(value):
    return re.sub(r"[^\w.:-]", "_", str(value))[:120]


class Worker:
    def __init__(
        self, repository, settings, *, mode="agent", executor=None, allow_api=False, worker_id=None
    ):
        if mode not in ("agent", "diagnostic"):
            raise ValueError("不支持的Worker模式")
        if mode == "agent" and executor is None and not allow_api:
            raise ValueError("Agent Worker需要显式--allow-api")
        self.repository, self.settings, self.mode = repository, settings, mode
        self.executor = executor
        self.allow_api = allow_api
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex}"
        self.storage = ArtifactStorage(settings.data_root / "artifacts")
        self.catalog = TaskCatalog(settings.tasks_root)
        self.stopping = Event()

    def _artifact(self, claim, kind, content):
        reference = self.storage.seal(claim.attempt_id, kind, content)
        return self.repository.register_artifact(claim, kind, **reference)

    def run_once(self) -> bool:
        reap_and_cleanup(self.repository, cleanup=self.settings.cleanup_expired)
        claim = self.repository.claim(
            self.worker_id, mode=self.mode, lease_seconds=self.settings.lease_seconds
        )
        if claim is None:
            return False
        control = ExecutionControl()
        finished = Event()
        state = {"model_calls": 0, "tool_calls": 0, "usage": {}}
        confirmed_patch = b""
        executor = self.executor or (
            ReferenceDiagnosticExecutor(self.settings.solutions_root)
            if self.mode == "diagnostic"
            else AgentExecutor(claim.budget, allow_api=self.allow_api)
        )

        def heartbeat():
            while not finished.is_set():
                try:
                    if self.repository.heartbeat(claim, self.settings.lease_seconds):
                        control.stop("cancelled")
                except (LeaseLost, SQLAlchemyError):
                    control.stop("lease_lost")
                    return
                if finished.wait(self.settings.heartbeat_seconds):
                    return

        def event(record):
            kind = _label(record["event"])
            payload = {key: value for key, value in record.items() if key in METADATA_KEYS}
            if "usage" in payload:
                payload["usage"] = {
                    key: value
                    for key, value in payload["usage"].items()
                    if key in USAGE_KEYS and isinstance(value, int)
                }
            try:
                self.repository.append_event(claim, kind, payload)
                if kind == "model_started":
                    state["model_calls"] = record["number"]
                    self.repository.upsert_step(
                        claim, f"model-{record['number']}", "model", executor.kind, "started", {}
                    )
                elif kind in ("model_finished", "model_failed"):
                    for key, value in payload.get("usage", {}).items():
                        state["usage"][key] = state["usage"].get(key, 0) + value
                    self.repository.upsert_step(
                        claim,
                        f"model-{state['model_calls']}",
                        "model",
                        executor.kind,
                        "completed" if kind == "model_finished" else "failed",
                        {"reason": record.get("reason", "")},
                    )
                elif kind in ("tool_started", "tool_finished"):
                    if kind == "tool_started":
                        state["tool_calls"] += 1
                    self.repository.upsert_step(
                        claim,
                        _label(f"tool-{record['call_id']}"),
                        "tool",
                        _label(record["tool"]),
                        "started"
                        if kind == "tool_started"
                        else "completed"
                        if record.get("ok")
                        else "failed",
                        {key: record[key] for key in ("ok", "truncated") if key in record},
                    )
            except LeaseLost:
                control.stop("lease_lost")
                raise ExecutionStopped("lease_lost") from None

        def boundary(workspace, kind):
            nonlocal confirmed_patch
            control.check()
            patch = export_diff(task, workspace)
            reference = self._artifact(claim, "checkpoint_patch", patch)
            self.repository.save_checkpoint(
                claim,
                reference["id"],
                claim.base_sha256,
                compute_snapshot_hash(workspace.repo),
                {
                    "boundary": kind,
                    "resumed": claim.checkpoint_id is not None,
                    "model_calls": state["model_calls"],
                    "tool_calls": state["tool_calls"],
                    "usage": state["usage"],
                },
            )
            confirmed_patch = patch

        def publish(output):
            patch_reference = self._artifact(claim, "candidate_patch", output.patch)

            def report_factory(execution_status, verdict, reason):
                report = {
                    **output.report,
                    "run_id": claim.run_id,
                    "attempt_id": claim.attempt_id,
                    "patch_artifact_id": patch_reference["id"],
                    "execution_status": execution_status,
                    "verdict": verdict,
                    "reason": reason,
                    "usage": output.usage,
                }
                return self.storage.seal(
                    claim.attempt_id,
                    "report",
                    json.dumps(report, ensure_ascii=False, indent=2).encode(),
                )

            for log in output.logs:
                self._artifact(claim, "log", log)
            self.repository.finalize(
                claim,
                output.execution_status,
                output.verdict,
                output.reason,
                output.usage,
                report_factory,
            )

        thread = Thread(target=heartbeat, daemon=True, name=f"heartbeat-{claim.attempt_id}")
        thread.start()
        try:
            control.check()
            task = self.catalog.load(claim.task_id)
            if task.spec.snapshot_sha256 != claim.base_sha256:
                raise ValueError("task_version_changed")
            run_root = self.settings.data_root / "workspaces" / claim.attempt_id
            if claim.checkpoint_id:
                checkpoint = self.repository.get_checkpoint(claim.checkpoint_id)
                if not checkpoint or checkpoint["base_sha256"] != claim.base_sha256:
                    raise ValueError("checkpoint_invalid")
                reference = self.repository.get_artifact(claim.run_id, checkpoint["artifact_id"])
                workspace = restore_workspace(
                    task, run_root, self.storage.read(reference), checkpoint["candidate_sha256"]
                )
            else:
                workspace = create_workspace(task, run_root)
            event(
                {
                    "event": "executor_started",
                    "executor_kind": executor.kind,
                    "resumed": claim.checkpoint_id is not None,
                }
            )
            boundary(workspace, "initial")
            runner = DockerRunner(control=control, attempt_id=claim.attempt_id)
            output = executor.execute(
                task, workspace, ExecutionHooks(control, event, boundary), runner
            )
            control.check()
            publish(output)
        except ExecutionStopped as error:
            if error.reason == "cancelled":
                try:
                    publish(
                        ExecutionOutput(
                            "completed",
                            "not_run",
                            "cancel_requested",
                            state["usage"],
                            confirmed_patch,
                            {"executor_kind": executor.kind},
                        )
                    )
                except (LeaseLost, SQLAlchemyError):
                    pass
        except LeaseLost:
            control.stop("lease_lost")
        except Exception as error:
            reason = (
                "checkpoint_invalid"
                if isinstance(error, ValueError) and claim.checkpoint_id
                else "worker_error"
            )
            try:
                publish(
                    ExecutionOutput(
                        "failed",
                        "not_run",
                        reason,
                        state["usage"],
                        confirmed_patch,
                        {"executor_kind": executor.kind},
                    )
                )
            except (LeaseLost, SQLAlchemyError):
                pass
        finally:
            finished.set()
            thread.join(timeout=10)
        return True

    def run_forever(self):
        while not self.stopping.is_set():
            if not self.run_once():
                self.stopping.wait(0.5)

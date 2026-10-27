"""真实Agent与显式参考补丁诊断执行器，不能混淆修复成绩。"""

from pathlib import Path

from patchpilot.agent import AgentBudget, ModelFailure, run_agent
from patchpilot.anthropic_model import AnthropicModel
from patchpilot.patching import apply_patch, export_diff
from patchpilot.service.execution import ExecutionOutput
from patchpilot.snapshot import compute_snapshot_hash
from patchpilot.verification import verify_patch


def _log(result) -> tuple[bytes, ...]:
    return (
        (Path(result.log_path).read_bytes(),) if result and Path(result.log_path).is_file() else ()
    )


class AgentExecutor:
    kind = "anthropic-agent"

    def __init__(self, budget: dict, *, allow_api: bool):
        if not allow_api:
            raise ValueError("Agent Worker需要显式--allow-api，可能发送代码并产生费用")
        self.budget = AgentBudget(**budget)

    def execute(self, task, workspace, hooks, runner):
        runner.check()
        hooks.control.check()
        model = AnthropicModel(control=hooks.control)
        try:
            result = run_agent(
                task,
                workspace.root.parent,
                model,
                runner,
                self.budget,
                existing_workspace=workspace,
                control=hooks.control,
                on_event=hooks.event,
                on_boundary=hooks.boundary,
            )
        finally:
            model.close()
        verdict = "not_run"
        execution_status = "completed"
        tests = None
        if result.verification:
            tests = result.verification.tests
            verdict = (
                "passed"
                if result.verification.status == "passed"
                else "failed"
                if result.verification.status in ("failed", "rejected")
                else "error"
            )
        elif result.stop_reason in ModelFailure.REASONS | {
            "runtime_error",
            "invalid_workspace",
            "protocol_error",
        }:
            execution_status = "failed"
        usage = {
            **result.usage,
            "model_calls": result.model_calls,
            "tool_calls": result.tool_calls,
            "calls_without_usage": result.calls_without_usage,
        }
        report = {
            "executor_kind": self.kind,
            "model_kind": result.model_kind,
            "stop_reason": result.stop_reason,
            "verification_status": result.verification.status if result.verification else "not_run",
            "usage": usage,
        }
        if tests:
            report["tests"] = {
                name: getattr(tests, name)
                for name in (
                    "status",
                    "passed",
                    "failed",
                    "errors",
                    "skipped",
                    "image_id",
                    "cleanup_ok",
                )
            }
        return ExecutionOutput(
            execution_status,
            verdict,
            result.stop_reason,
            usage,
            Path(result.patch_path).read_bytes(),
            report,
            _log(tests),
        )


class ReferenceDiagnosticExecutor:
    kind = "reference-diagnostic"

    def __init__(self, solutions_root: Path):
        self.solutions_root = solutions_root

    def execute(self, task, workspace, hooks, runner):
        hooks.control.check()
        hooks.event({"event": "tool_started", "call_id": "diagnostic-patch", "tool": "apply_patch"})
        if compute_snapshot_hash(workspace.repo) == task.spec.snapshot_sha256:
            patch = (self.solutions_root / task.spec.task_id / "reference.patch").read_bytes()
            apply_patch(workspace, patch, task.spec.snapshot_sha256)
        hooks.event(
            {
                "event": "tool_finished",
                "call_id": "diagnostic-patch",
                "tool": "apply_patch",
                "ok": True,
            }
        )
        hooks.boundary(workspace, "tool_batch")
        hooks.control.check()
        hooks.event(
            {
                "event": "tool_started",
                "call_id": "diagnostic-tests",
                "tool": "independent_verification",
            }
        )
        patch = export_diff(task, workspace)
        verified = verify_patch(task, patch, workspace.root.parent, runner)
        hooks.control.check()
        hooks.event(
            {
                "event": "tool_finished",
                "call_id": "diagnostic-tests",
                "tool": "independent_verification",
                "ok": verified.status == "passed",
            }
        )
        hooks.boundary(workspace, "tool_batch")
        verdict = (
            "passed"
            if verified.status == "passed"
            else "failed"
            if verified.status in ("failed", "rejected")
            else "error"
        )
        report = {
            "executor_kind": self.kind,
            "diagnostic": True,
            "model_calls": 0,
            "verification_status": verified.status,
            "base_sha256": verified.base_sha256,
            "candidate_sha256": verified.candidate_sha256,
            "patch_sha256": verified.patch_sha256,
        }
        if verified.tests:
            report["tests"] = {
                name: getattr(verified.tests, name)
                for name in (
                    "status",
                    "passed",
                    "failed",
                    "errors",
                    "skipped",
                    "image_id",
                    "cleanup_ok",
                )
            }
        return ExecutionOutput(
            "completed" if verdict != "error" else "failed",
            verdict,
            "diagnostic_completed",
            {"model_calls": 0, "tool_calls": 2},
            patch,
            report,
            _log(verified.tests),
        )

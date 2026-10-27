"""从固定基线重建验证环境，并将结论绑定到补丁和执行证据。"""

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic

from patchpilot.evaluation.catalog import acceptance_directory
from patchpilot.patching import apply_patch, export_diff
from patchpilot.sandbox import DockerRunner, TestResult
from patchpilot.snapshot import compute_snapshot_hash
from patchpilot.task import LoadedTask, to_agent_input
from patchpilot.workspace import create_workspace


@dataclass(frozen=True)
class VerificationResult:
    status: str
    task_id: str
    patch_sha256: str
    base_sha256: str
    candidate_sha256: str | None
    acceptance_sha256: str | None
    workspace_path: str
    report_path: str
    tests: TestResult | None
    detail: str


def verify_patch(
    task: LoadedTask,
    patch: bytes,
    runs_root: Path,
    runner: DockerRunner,
    *,
    time_budget: float | None = None,
) -> VerificationResult:
    """不接收候选工作区或任务自带的验收命令，只验证封存的补丁字节。"""
    started = monotonic()
    workspace = create_workspace(task, runs_root)
    report_path = workspace.artifacts / "verification.json"
    (workspace.artifacts / "candidate.patch").write_bytes(patch)
    (workspace.artifacts / "agent-input.json").write_text(
        to_agent_input(task).model_dump_json(indent=2), encoding="utf-8"
    )
    candidate_hash = acceptance_hash = None
    tests = None
    try:
        acceptance = acceptance_directory(task.spec.task_id)
        acceptance_hash = compute_snapshot_hash(acceptance)
        applied = apply_patch(workspace, patch, task.spec.snapshot_sha256)
        candidate_hash = applied.after_sha256
        (workspace.artifacts / "diff.patch").write_bytes(export_diff(task, workspace))
        remaining = (
            max(0, time_budget - (monotonic() - started)) if time_budget is not None else None
        )
        tests = runner.run(workspace, acceptance=acceptance, time_budget=remaining)
        status = tests.status
        detail = tests.detail
        if compute_snapshot_hash(workspace.repo) != candidate_hash:
            status, detail = "infra_error", "验收期间候选工作区发生了改变"
        if compute_snapshot_hash(acceptance) != acceptance_hash:
            status, detail = "infra_error", "验收用例在执行期间发生了改变"
    except ValueError as error:
        status, detail = "rejected", str(error)
    result = VerificationResult(
        status=status,
        task_id=task.spec.task_id,
        patch_sha256=hashlib.sha256(patch).hexdigest(),
        base_sha256=task.spec.snapshot_sha256,
        candidate_sha256=candidate_hash,
        acceptance_sha256=acceptance_hash,
        workspace_path=str(workspace.root),
        report_path=str(report_path),
        tests=tests,
        detail=detail,
    )
    report_path.write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result

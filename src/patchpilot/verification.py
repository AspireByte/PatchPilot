"""从固定基线重建验证环境，并将结论绑定到补丁和执行证据。"""

from dataclasses import dataclass
from pathlib import Path

from patchpilot.sandbox import DockerRunner, TestResult
from patchpilot.task import LoadedTask


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
    """内部兼容 API：把固定任务验收交给统一 Harness 服务。"""
    from patchpilot.harness import HarnessService

    return HarnessService.verify_benchmark(task, patch, runs_root, runner, time_budget=time_budget)

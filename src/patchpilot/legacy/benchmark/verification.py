"""从固定基线重建验证环境，并将结论绑定到补丁和执行证据。"""

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from patchpilot.legacy.benchmark.sandbox import DockerRunner, TestResult
from patchpilot.legacy.benchmark.task import LoadedTask


@dataclass(frozen=True)
class VerificationResult:
    """固定任务补丁验收报告，将结论与所有关键输入的版本联系起来。

    patch_sha256 标识补丁，base/candidate/acceptance 摘要分别标识基线、应用后的代码和验收用例。
    tests 保存 Docker 的实际执行结果；候选或验收摘要为空时，说明流程未取得相应阶段的证据。
    rejected 表示补丁等输入不符合约束，测试失败和基础设施失败则由执行结果分别报告。
    """

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
    """内部兼容 API：把固定任务验收交给独立的基准适配分支。"""
    # task 与 patch 分别指定基线和待验收差异，runs_root 保存新的验收工作区，runner 提供容器后端。
    # time_budget 传递剩余秒数；报告记录被拒绝或实际执行结果，调用方不能把返回对象的存在当成通过。
    # 保留旧调用接口，实际委托 HarnessService 的基准兼容分支重建基线、应用补丁并调用 Docker。
    # 返回包含基线/补丁/候选/验收摘要的证据对象；通用聊天 Harness 不走这条固定任务验收链路。
    return verify_benchmark(task, patch, runs_root, runner, time_budget=time_budget)


def verify_benchmark(task, patch, runs_root, runner, *, time_budget=None):
    """内部基准适配入口；不在聊天工具中暴露维护者材料。"""
    # 参数 task 提供固定基线，patch 是交付补丁字节，runner 负责容器 pytest，返回 VerificationResult。
    # 此流程额外引入维护者 acceptance，不能把聊天 run 的普通 profile 结果当作同等验收结论。
    # 局部导入让兼容依赖在调用时才加载，也避开 verification 与 Harness 之间的循环导入。
    from patchpilot.evaluation.catalog import acceptance_directory
    from patchpilot.legacy.benchmark.patching import apply_patch, export_diff
    from patchpilot.legacy.benchmark.snapshot import compute_snapshot_hash
    from patchpilot.legacy.benchmark.task import to_agent_input
    from patchpilot.legacy.benchmark.verification import VerificationResult
    from patchpilot.legacy.benchmark.workspace import create_workspace

    started = time.monotonic()
    # 验收重新从固定基线建工作区，再应用导出的补丁，检验交付补丁本身是否可复现。
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
            max(0, time_budget - (time.monotonic() - started)) if time_budget is not None else None
        )
        tests = runner.run(workspace, acceptance=acceptance, time_budget=remaining)
        status = tests.status
        detail = tests.detail
        # 候选代码和维护者验收各有摘要，执行前后都核对，避免把变化中的输入当作同一次证据。
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

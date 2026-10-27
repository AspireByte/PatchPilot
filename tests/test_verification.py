import hashlib
import importlib
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from patchpilot.sandbox import DockerRunner
from patchpilot.snapshot import compute_snapshot_hash
from patchpilot.task import load_task

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "benchmarks/tasks/pagination-001/task.json"
PATCH = ROOT / "benchmarks/solutions/pagination-001/reference.patch"


def api():
    assert importlib.util.find_spec("patchpilot.verification"), "尚未实现独立补丁验证器"
    return importlib.import_module("patchpilot.verification")


class MustNotRun:
    def run(self, *args, **kwargs):
        raise AssertionError("非法补丁不能进入测试执行器")


@pytest.mark.parametrize("target", ["tests/test_paginator.py", "repro/test_same_timestamp.py"])
def test_test_tampering_is_rejected_before_execution(tmp_path: Path, target: str) -> None:
    module = api()
    task = load_task(CASE)
    patch = PATCH.read_bytes().replace(b"src/pagination_demo/paginator.py", target.encode())
    result = module.verify_patch(task, patch, tmp_path, MustNotRun())
    assert result.status == "rejected"
    assert result.tests is None
    assert result.patch_sha256 == hashlib.sha256(patch).hexdigest()
    assert Path(result.report_path).is_file()
    assert compute_snapshot_hash(task.snapshot_root) == task.spec.snapshot_sha256


@pytest.mark.docker
def test_reference_patch_passes_independent_acceptance(tmp_path: Path) -> None:
    module = api()
    task = load_task(CASE)
    patch = PATCH.read_bytes()
    result = module.verify_patch(task, patch, tmp_path, DockerRunner())
    assert result.status == "passed", result.detail
    assert result.tests.status == "passed"
    assert result.tests.passed == 14
    assert result.patch_sha256 == hashlib.sha256(patch).hexdigest()
    assert result.base_sha256 == task.spec.snapshot_sha256
    assert result.candidate_sha256 != result.base_sha256
    assert result.acceptance_sha256
    assert result.tests.image_id.startswith("sha256:")
    report = json.loads(Path(result.report_path).read_text(encoding="utf-8"))
    assert report["patch_sha256"] == result.patch_sha256
    assert report["tests"] == asdict(result.tests)
    workspace = Path(result.workspace_path)
    assert (workspace / "artifacts/candidate.patch").read_bytes() == patch
    assert (workspace / "artifacts/diff.patch").is_file()
    assert not list((workspace / "repo").rglob("*acceptance*"))
    assert not list((workspace / "repo").rglob("reference.patch"))
    assert compute_snapshot_hash(task.snapshot_root) == task.spec.snapshot_sha256


@pytest.mark.docker
def test_componentwise_cursor_bug_is_rejected_by_independent_acceptance(tmp_path: Path) -> None:
    patch = PATCH.read_bytes().replace(
        b"+        ordered = [row for row in ordered if (row.created_at, row.id) > cursor]",
        b"+        ordered = [row for row in ordered if row.created_at >= cursor[0] and row.id > cursor[1]]",
    )
    assert patch != PATCH.read_bytes()
    result = api().verify_patch(load_task(CASE), patch, tmp_path, DockerRunner())
    assert result.status == "failed", "验收必须发现时间增大但 ID 减小时的漏项"
    assert result.tests.failed > 0


@pytest.mark.docker
def test_empty_patch_cannot_claim_a_fix(tmp_path: Path) -> None:
    result = api().verify_patch(load_task(CASE), b"", tmp_path, DockerRunner())
    assert result.status == "failed", result.detail
    assert result.tests.failed > 0
    assert result.candidate_sha256 == result.base_sha256
    assert result.patch_sha256 == hashlib.sha256(b"").hexdigest()

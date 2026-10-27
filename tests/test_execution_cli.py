import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "benchmarks/tasks/pagination-001/task.json"
PATCH = ROOT / "benchmarks/solutions/pagination-001/reference.patch"


def run_cli(arguments: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "patchpilot", *arguments],
        cwd=cwd,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=90,
    )


@pytest.mark.docker
def test_sandbox_check_reports_the_local_linux_image(tmp_path: Path) -> None:
    result = run_cli(["sandbox-check"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["image_id"].startswith("sha256:")


@pytest.mark.docker
def test_verify_patch_cli_exports_a_bound_report(tmp_path: Path) -> None:
    result = run_cli(
        ["verify-patch", str(CASE), str(PATCH), "--runs-dir", str(tmp_path / "runs")], tmp_path
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "passed"
    assert report["patch_sha256"] == hashlib.sha256(PATCH.read_bytes()).hexdigest()
    assert report["tests"]["passed"] == 14
    assert Path(report["report_path"]).is_file()


@pytest.mark.docker
def test_empty_patch_cli_returns_failure_instead_of_success(tmp_path: Path) -> None:
    empty = tmp_path / "empty.patch"
    empty.write_bytes(b"")
    result = run_cli(
        ["verify-patch", str(CASE), str(empty), "--runs-dir", str(tmp_path / "runs")], tmp_path
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert json.loads(result.stdout)["status"] == "failed"


def test_missing_patch_is_a_readable_cli_error(tmp_path: Path) -> None:
    result = run_cli(["verify-patch", str(CASE), str(tmp_path / "missing.patch")], tmp_path)
    assert result.returncode == 2
    assert "无法读取补丁" in result.stderr
    assert "Traceback" not in result.stderr


def test_missing_image_is_reported_without_installing_anything(tmp_path: Path) -> None:
    result = run_cli(["sandbox-check", "--image", "patchpilot-missing-image:never"], tmp_path)
    assert result.returncode == 2
    assert "沙箱检查失败" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("timeout", ["0", "-1", "nan", "inf"])
def test_invalid_timeout_is_a_usage_error(tmp_path: Path, timeout: str) -> None:
    result = run_cli(["verify-patch", str(CASE), str(PATCH), "--timeout", timeout], tmp_path)
    assert result.returncode == 2
    assert "超时" in result.stderr
    assert "Traceback" not in result.stderr

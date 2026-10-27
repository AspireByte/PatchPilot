import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "benchmarks/tasks/pagination-001"


def run_cli(arguments: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "patchpilot", *arguments],
        cwd=cwd,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
    )


def test_help_works_outside_project_directory(tmp_path: Path) -> None:
    result = run_cli(["--help"], tmp_path)
    assert result.returncode == 0, result.stderr
    assert "patchpilot" in result.stdout
    assert "--help" in result.stdout
    assert result.stderr == ""


def test_validate_task_accepts_the_supplied_case(tmp_path: Path) -> None:
    result = run_cli(["validate-task", str(CASE / "task.json")], tmp_path)
    assert result.returncode == 0, result.stderr
    assert "pagination-001" in result.stdout
    assert result.stderr == ""


def test_agent_input_mode_emits_only_public_json(tmp_path: Path) -> None:
    result = run_cli(["validate-task", str(CASE / "task.json"), "--agent-input"], tmp_path)
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["task_id"] == "pagination-001"
    assert data["snapshot_path"] == "repo"
    assert "公共行为约定" in data["issue"]
    assert "issue_path" not in data
    for forbidden in (str(ROOT), "solutions", "reference.patch"):
        assert forbidden not in result.stdout
    assert result.stderr == ""


@pytest.mark.parametrize("kind", ["missing", "invalid-json", "tampered", "extra-field"])
def test_invalid_task_is_a_readable_cli_error(tmp_path: Path, kind: str) -> None:
    copied = Path(shutil.copytree(CASE, tmp_path / "case"))
    manifest = copied / "task.json"
    match kind:
        case "missing":
            manifest.unlink()
        case "invalid-json":
            manifest.write_text("not json", encoding="utf-8")
        case "tampered":
            (copied / "repo/src/pagination_demo/paginator.py").write_bytes(b"changed\n")
        case "extra-field":
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["reference_patch"] = "MAINTAINER_ONLY"
            manifest.write_text(json.dumps(data), encoding="utf-8")
    result = run_cli(["validate-task", str(manifest)], tmp_path)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "任务校验失败" in result.stderr
    assert "Traceback" not in result.stderr
    assert "MAINTAINER_ONLY" not in result.stderr

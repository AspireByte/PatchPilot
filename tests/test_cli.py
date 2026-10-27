import os
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


@pytest.mark.parametrize("command", ["agent-run", "verify-patch", "validate-task", "sandbox-check"])
def test_legacy_commands_are_no_longer_public(tmp_path: Path, command: str) -> None:
    result = run_cli([command], tmp_path)
    assert result.returncode == 2
    assert command in result.stderr
    assert "choose from chat" in result.stderr
    assert "Traceback" not in result.stderr


def test_only_chat_is_in_public_help(tmp_path: Path) -> None:
    result = run_cli(["--help"], tmp_path)
    assert "chat" in result.stdout
    for name in ("agent-run", "verify-patch", "sandbox-check", "validate-task"):
        assert name not in result.stdout

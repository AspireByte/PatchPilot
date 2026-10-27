import os
import subprocess
import sys
from pathlib import Path

import pytest
from postgres_helpers import pg_repository as pg_repository

from patchpilot.task import load_task

ROOT = Path(__file__).resolve().parents[1]


def test_service_cli_help_is_available() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "patchpilot.service.cli", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "worker" in result.stdout and "migrate" in result.stdout


@pytest.mark.postgres
@pytest.mark.docker
def test_separate_worker_process_executes_only_explicit_diagnostic_mode(
    pg_repository, tmp_path: Path
) -> None:
    task = load_task(ROOT / "benchmarks/tasks/pagination-001/task.json")
    run = pg_repository.create_run(task.spec.task_id, "diagnostic", {}, task.spec.snapshot_sha256)
    environment = {
        **os.environ,
        "PATCHPILOT_DATABASE_URL": pg_repository.engine.url.render_as_string(hide_password=False),
        "PATCHPILOT_API_TOKEN": "test-local-api-token",
        "PATCHPILOT_DATA_ROOT": str(tmp_path),
        "PATCHPILOT_TASKS_ROOT": str(ROOT / "benchmarks/tasks"),
        "PATCHPILOT_SOLUTIONS_ROOT": str(ROOT / "benchmarks/solutions"),
        "PYTHONIOENCODING": "utf-8",
    }
    result = subprocess.run(
        [sys.executable, "-m", "patchpilot.service.cli", "worker", "--once", "--diagnostic"],
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    stored = pg_repository.get_run(run["id"])
    assert stored["execution_status"] == "completed", result.stdout + result.stderr
    assert stored["verdict"] == "passed"
    assert stored["usage"]["model_calls"] == 0
    from patchpilot.service.artifacts import ArtifactStorage

    report = next(
        item for item in pg_repository.list_artifacts(run["id"]) if item["kind"] == "report"
    )
    assert b"reference-diagnostic" in ArtifactStorage(tmp_path / "artifacts").read(report)

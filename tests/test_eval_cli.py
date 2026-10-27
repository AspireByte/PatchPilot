import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def api():
    assert importlib.util.find_spec("patchpilot.evaluation.cli"), "尚未实现评测CLI"
    return importlib.import_module("patchpilot.evaluation.cli")


def test_eval_help_is_available_without_model_credentials():
    result = subprocess.run(
        [sys.executable, "-m", "patchpilot.evaluation.cli", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "validate" in result.stdout and "report" in result.stdout


def test_paid_eval_is_refused_without_explicit_permission(tmp_path, capsys):
    code = api().main(["run", "--tasks", "pagination-001", "--output", str(tmp_path)])
    assert code == 2
    assert "allow-api" in capsys.readouterr().err
    assert not list(tmp_path.iterdir())


def test_cli_lists_reviewable_tasks_and_fingerprint(capsys):
    assert api().main(["list"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["task_count"] == 20
    assert len(report["dataset_sha256"]) == 64
    assert report["review_status"] == "draft_requires_human_review"


@pytest.mark.docker
def test_cli_validates_one_task_without_model_access(tmp_path, capsys):
    code = api().main(["validate", "--tasks", "pagination-001", "--output", str(tmp_path)])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["total"] == report["valid"] == 1
    assert report["result_kind"] == "dataset-validation-not-model-performance"

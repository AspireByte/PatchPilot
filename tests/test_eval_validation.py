import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def api():
    module = importlib.import_module("patchpilot.evaluation.validation")
    assert hasattr(module, "audit_dataset"), "尚未实现资料审阅与参考验证"
    return module


def test_corpus_audit_and_review_cover_all_inputs(tmp_path):
    report = api().audit_dataset(ROOT / "benchmarks/tasks", ROOT / "benchmarks/solutions")
    assert report["task_count"] == 20
    assert report["splits"] == {"development": 12, "holdout": 8}
    assert report["material_errors"] == []
    review = tmp_path / "REVIEW.md"
    api().write_review(review, ROOT / "benchmarks/tasks")
    text = review.read_text(encoding="utf-8")
    assert "待人工审阅" in text
    assert all(f"## {name}" in text for name in ("pagination-001", "path-001", "csv-export-001"))


def test_validation_records_environment_failure_without_dropping_tasks(tmp_path):
    class NoImage:
        def __init__(self, *args):
            pass

        def check(self):
            raise ValueError("missing")

    result = api().validate_tasks(
        ROOT / "benchmarks/tasks",
        ROOT / "benchmarks/solutions",
        tmp_path,
        task_ids=["pagination-001", "path-001"],
        runner_factory=NoImage,
    )
    assert result["total"] == 2 and result["valid"] == 0
    assert len(result["records"]) == 2
    assert result["result_kind"] == "dataset-validation-not-model-performance"


@pytest.mark.docker
def test_validator_uses_normal_reproduction_and_independent_reference_tests(tmp_path):
    result = api().validate_tasks(
        ROOT / "benchmarks/tasks",
        ROOT / "benchmarks/solutions",
        tmp_path,
        task_ids=["pagination-001", "path-001"],
    )
    assert result["total"] == result["valid"] == 2, result
    assert all(
        row["normal_status"] == "passed"
        and row["repro_status"] == "failed"
        and row["reference_status"] == "passed"
        for row in result["records"]
    )

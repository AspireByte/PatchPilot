import importlib
import shutil
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def api():
    assert importlib.util.find_spec("patchpilot.evaluation.catalog"), "尚未实现规范任务集清单"
    return importlib.import_module("patchpilot.evaluation.catalog")


def test_dataset_has_twenty_documented_tasks_and_disjoint_groups():
    dataset = api().load_dataset()
    assert len(dataset.tasks) == 20
    assert len({item.task_id for item in dataset.tasks}) == 20
    dev = [item for item in dataset.tasks if item.split == "development"]
    holdout = [item for item in dataset.tasks if item.split == "holdout"]
    assert (len(dev), len(holdout)) == (12, 8)
    assert not ({item.group for item in dev} & {item.group for item in holdout})
    assert all(
        item.source_kind == "synthetic" and item.license == "MIT" and item.expected_behavior
        for item in dataset.tasks
    )
    assert "AI" in dataset.provenance
    assert dataset.review_status == "draft_requires_human_review"


def test_dataset_approval_changes_when_issue_or_acceptance_changes(tmp_path):
    dataset = api().load_dataset()
    copied = Path(
        shutil.copytree(ROOT / "benchmarks/tasks/pagination-001", tmp_path / "tasks/pagination-001")
    )
    before = dataset.fingerprint(tmp_path / "tasks", ROOT / "benchmarks/solutions")
    (copied / "Issue.md").write_text("changed public issue", encoding="utf-8")
    assert dataset.fingerprint(tmp_path / "tasks", ROOT / "benchmarks/solutions") != before


def test_development_is_default_and_unknown_selection_is_rejected():
    dataset = api().load_dataset()
    assert len(dataset.select()) == 12
    with pytest.raises(ValueError):
        dataset.select(task_ids=["../escape"])
    with pytest.raises(ValueError):
        dataset.select(task_ids=["pagination-001", "pagination-001"])

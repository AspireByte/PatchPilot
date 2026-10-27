import importlib
from pathlib import Path

import pytest

from patchpilot.snapshot import compute_snapshot_hash
from patchpilot.task import load_task

CASE = Path(__file__).resolve().parents[1] / "benchmarks/tasks/pagination-001/task.json"


def api():
    assert importlib.util.find_spec("patchpilot.workspace"), "尚未实现独立工作区"
    return importlib.import_module("patchpilot.workspace")


def test_workspaces_are_unique_and_copy_only_the_verified_repo(tmp_path: Path) -> None:
    task = load_task(CASE)
    module = api()
    first = module.create_workspace(task, tmp_path)
    second = module.create_workspace(task, tmp_path)
    assert first.root != second.root
    assert first.repo == first.root / "repo"
    assert first.artifacts == first.root / "artifacts"
    assert compute_snapshot_hash(first.repo) == task.spec.snapshot_sha256
    assert sorted(path.name for path in first.root.iterdir()) == ["artifacts", "repo"]
    assert not list(first.repo.rglob("reference.patch"))
    (first.repo / "src/pagination_demo/paginator.py").write_bytes(b"changed")
    assert compute_snapshot_hash(second.repo) == task.spec.snapshot_sha256
    assert compute_snapshot_hash(task.snapshot_root) == task.spec.snapshot_sha256


def test_output_inside_the_source_is_rejected() -> None:
    task = load_task(CASE)
    module = api()
    with pytest.raises(ValueError):
        module.create_workspace(task, task.snapshot_root / "runs")
    assert compute_snapshot_hash(task.snapshot_root) == task.spec.snapshot_sha256


def test_changed_source_is_rejected_before_copying(tmp_path: Path) -> None:
    import shutil

    from patchpilot.task import LoadedTask

    task = load_task(CASE)
    source = Path(shutil.copytree(task.snapshot_root, tmp_path / "source"))
    changed = LoadedTask(spec=task.spec, snapshot_root=source, issue=task.issue)
    (source / "src/pagination_demo/paginator.py").write_bytes(b"tampered")
    with pytest.raises(ValueError):
        api().create_workspace(changed, tmp_path / "runs")

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from patchpilot import task
from patchpilot.snapshot import compute_snapshot_hash


@pytest.fixture
def task_package(tmp_path: Path) -> Path:
    root = tmp_path / "任务包"
    repo = root / "repo"
    for name in ("src", "tests", "repro"):
        (repo / name).mkdir(parents=True)
    (repo / "src/example.py").write_bytes(b"value = 1\n")
    (repo / "tests/test_example.py").write_bytes(b"def test_example():\n    assert True\n")
    (repo / "repro/test_issue.py").write_bytes(b"def test_issue():\n    assert False\n")
    (repo / "pytest.ini").write_bytes(b"[pytest]\npythonpath = src\n")
    (root / "Issue.md").write_text("分页会遗漏相同时间戳的记录。", encoding="utf-8")
    data = {
        "task_id": "pagination-001",
        "snapshot_path": "repo",
        "snapshot_sha256": compute_snapshot_hash(repo),
        "issue_path": "Issue.md",
        "environment_id": "python-pytest-v1",
        "writable_dirs": ["src"],
        "test_profile": "pytest-visible-v1",
    }
    manifest = root / "task.json"
    manifest.write_text(json.dumps(data), encoding="utf-8")
    return manifest


def update_manifest(path: Path, **changes: object) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    data.update(changes)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_valid_task_loads_verified_snapshot_and_issue(task_package: Path) -> None:
    loaded = task.load_task(task_package)
    assert loaded.spec.task_id == "pagination-001"
    assert loaded.snapshot_root == (task_package.parent / "repo").resolve()
    assert loaded.issue == "分页会遗漏相同时间戳的记录。"


@pytest.mark.parametrize(
    "field",
    [
        "task_id",
        "snapshot_path",
        "snapshot_sha256",
        "issue_path",
        "environment_id",
        "writable_dirs",
        "test_profile",
    ],
)
def test_required_fields_are_not_silently_defaulted(task_package: Path, field: str) -> None:
    api = task
    data = json.loads(task_package.read_text(encoding="utf-8"))
    del data[field]
    task_package.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(api.TaskValidationError):
        api.load_task(task_package)


@pytest.mark.parametrize(
    "changes",
    [
        {"environment_id": "arbitrary-python"},
        {"test_profile": "arbitrary-command"},
        {"task_id": 123},
        {"task_id": "../escape"},
        {"snapshot_sha256": "invalid"},
        {"snapshot_path": "../outside"},
        {"snapshot_path": "/etc"},
        {"snapshot_path": "C:/outside"},
        {"snapshot_path": "repo\\src"},
        {"snapshot_path": "repo/../outside"},
        {"issue_path": "../solutions/reference.patch"},
        {"issue_path": "Issue.md:alternate"},
        {"writable_dirs": []},
        {"writable_dirs": ["."]},
        {"writable_dirs": ["tests"]},
        {"writable_dirs": ["repro"]},
        {"writable_dirs": ["src", "tests"]},
        {"writable_dirs": ["src/../tests"]},
        {"writable_dirs": "src"},
        {"reference_patch": "solutions/reference.patch"},
        {"hidden_tests": "maintainer-only"},
        {"command": "arbitrary command"},
    ],
)
def test_unsupported_or_unsafe_fields_are_rejected(task_package: Path, changes: dict) -> None:
    api = task
    update_manifest(task_package, **changes)
    with pytest.raises(api.TaskValidationError):
        api.load_task(task_package)


@pytest.mark.parametrize("name", ["src/example.py", "tests/test_example.py", "pytest.ini"])
def test_snapshot_tampering_is_rejected(task_package: Path, name: str) -> None:
    api = task
    (task_package.parent / "repo" / name).write_bytes(b"changed\n")
    with pytest.raises(api.TaskValidationError):
        api.load_task(task_package)


def test_agent_input_is_an_explicit_public_view(task_package: Path) -> None:
    api = task
    solutions = task_package.parent / "solutions"
    solutions.mkdir()
    (solutions / "reference.patch").write_text("MAINTAINER_ONLY", encoding="utf-8")
    loaded = api.load_task(task_package)
    view = api.to_agent_input(loaded).model_dump(mode="json")
    assert set(view) == {
        "task_id",
        "snapshot_path",
        "snapshot_sha256",
        "issue",
        "environment_id",
        "writable_dirs",
        "test_profile",
    }
    assert view["snapshot_path"] == "repo"
    assert view["issue"] == "分页会遗漏相同时间戳的记录。"
    assert view["writable_dirs"] == ["src"]
    serialized = json.dumps(view, ensure_ascii=False)
    for forbidden in (str(task_package.parent), "solutions", "reference.patch", "MAINTAINER_ONLY"):
        assert forbidden not in serialized


@pytest.mark.parametrize("content", [b"not json", b"\xff\xfe"])
def test_invalid_task_encoding_or_json_is_rejected(task_package: Path, content: bytes) -> None:
    api = task
    task_package.write_bytes(content)
    with pytest.raises(api.TaskValidationError):
        api.load_task(task_package)


def test_missing_manifest_is_reported_as_validation_error(tmp_path: Path) -> None:
    api = task
    with pytest.raises(api.TaskValidationError):
        api.load_task(tmp_path / "missing.json")


def test_empty_issue_is_rejected(task_package: Path) -> None:
    api = task
    (task_package.parent / "Issue.md").write_text("  \n", encoding="utf-8")
    with pytest.raises(api.TaskValidationError):
        api.load_task(task_package)


def test_missing_test_profile_files_are_rejected(task_package: Path) -> None:
    api = task
    (task_package.parent / "repo/pytest.ini").unlink()
    update_manifest(
        task_package, snapshot_sha256=compute_snapshot_hash(task_package.parent / "repo")
    )
    with pytest.raises(api.TaskValidationError):
        api.load_task(task_package)


def test_issue_symlink_is_rejected(task_package: Path, tmp_path: Path) -> None:
    api = task
    outside = tmp_path / "outside.md"
    outside.write_text("MAINTAINER_ONLY", encoding="utf-8")
    issue = task_package.parent / "Issue.md"
    issue.unlink()
    try:
        issue.symlink_to(outside)
    except OSError:
        pytest.skip("当前系统没有创建符号链接的权限")
    with pytest.raises(api.TaskValidationError):
        api.load_task(task_package)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 目录联接专用测试")
def test_snapshot_directory_junction_is_rejected(task_package: Path, tmp_path: Path) -> None:
    api = task
    repo = task_package.parent / "repo"
    outside = tmp_path / "outside"
    shutil.move(str(repo), str(outside))
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(repo), str(outside)],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, "无法创建用于校验的目录联接"
    with pytest.raises(api.TaskValidationError):
        api.load_task(task_package)

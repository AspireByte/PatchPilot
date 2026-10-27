import importlib
from pathlib import Path

import pytest

from patchpilot.snapshot import compute_snapshot_hash
from patchpilot.task import load_task
from patchpilot.workspace import create_workspace

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "benchmarks/tasks/pagination-001/task.json"
REFERENCE = ROOT / "benchmarks/solutions/pagination-001/reference.patch"


def api():
    assert importlib.util.find_spec("patchpilot.patching"), "尚未实现受控补丁应用"
    return importlib.import_module("patchpilot.patching")


def test_reference_patch_can_be_exported_and_reapplied(tmp_path: Path) -> None:
    module = api()
    task = load_task(CASE)
    workspace = create_workspace(task, tmp_path)
    applied = module.apply_patch(workspace, REFERENCE.read_bytes(), task.spec.snapshot_sha256)
    assert applied.before_sha256 == task.spec.snapshot_sha256
    assert applied.after_sha256 == compute_snapshot_hash(workspace.repo)
    assert applied.changed_files == ("src/pagination_demo/paginator.py",)
    assert len(applied.patch_sha256) == 64
    exported = module.export_diff(task, workspace)
    assert b"diff --git a/src/" in exported
    other = create_workspace(task, tmp_path)
    module.apply_patch(other, exported, task.spec.snapshot_sha256)
    assert compute_snapshot_hash(other.repo) == applied.after_sha256
    assert compute_snapshot_hash(task.snapshot_root) == task.spec.snapshot_sha256


def test_stale_workspace_hash_is_rejected_without_changes(tmp_path: Path) -> None:
    module = api()
    task = load_task(CASE)
    workspace = create_workspace(task, tmp_path)
    (workspace.repo / "src/pagination_demo/paginator.py").write_bytes(b"changed\n")
    before = compute_snapshot_hash(workspace.repo)
    with pytest.raises(ValueError):
        module.apply_patch(workspace, REFERENCE.read_bytes(), task.spec.snapshot_sha256)
    assert compute_snapshot_hash(workspace.repo) == before


@pytest.mark.parametrize(
    "target",
    [
        "../escape.py",
        "/tmp/escape.py",
        "C:/escape.py",
        "src/../../escape.py",
        "src\\escape.py",
        "src/file.py:stream",
        "tests/test_paginator.py",
        "pytest.ini",
        "repro/test_same_timestamp.py",
        "src/.git/config.py",
        "src/missing.py",
    ],
)
def test_disallowed_patch_paths_never_change_the_workspace(tmp_path: Path, target: str) -> None:
    module = api()
    task = load_task(CASE)
    workspace = create_workspace(task, tmp_path)
    patch = REFERENCE.read_bytes().replace(b"src/pagination_demo/paginator.py", target.encode())
    with pytest.raises(ValueError):
        module.apply_patch(workspace, patch, task.spec.snapshot_sha256)
    assert compute_snapshot_hash(workspace.repo) == task.spec.snapshot_sha256


@pytest.mark.parametrize("kind", ["delete", "mode", "rename", "binary", "context", "partial"])
def test_unsupported_or_conflicting_patches_are_atomic(tmp_path: Path, kind: str) -> None:
    module = api()
    task = load_task(CASE)
    workspace = create_workspace(task, tmp_path)
    patch = REFERENCE.read_bytes()
    match kind:
        case "delete":
            patch = patch.replace(b"+++ b/src/pagination_demo/paginator.py", b"+++ /dev/null")
        case "mode":
            patch = patch.replace(b"--- a/", b"old mode 100644\nnew mode 100755\n--- a/")
        case "rename":
            patch = patch.replace(b"--- a/", b"rename from x\nrename to y\n--- a/")
        case "binary":
            patch = b"GIT binary patch\n" + patch
        case "context":
            patch = patch.replace(b"row.created_at > cursor[0]", b"row.created_at == 999999")
        case "partial":
            patch += patch.replace(b"src/pagination_demo/paginator.py", b"src/missing.py")
    with pytest.raises(ValueError):
        module.apply_patch(workspace, patch, task.spec.snapshot_sha256)
    assert compute_snapshot_hash(workspace.repo) == task.spec.snapshot_sha256


def test_export_refuses_modifications_to_original_tests(tmp_path: Path) -> None:
    module = api()
    task = load_task(CASE)
    workspace = create_workspace(task, tmp_path)
    (workspace.repo / "tests/test_paginator.py").write_bytes(b"# removed tests\n")
    with pytest.raises(ValueError):
        module.export_diff(task, workspace)


def test_new_regression_test_can_be_exported_and_replayed(tmp_path: Path) -> None:
    module = api()
    task = load_task(CASE)
    workspace = create_workspace(task, tmp_path)
    patch = (
        b"diff --git a/regression/test_new.py b/regression/test_new.py\n"
        b"new file mode 100644\n--- /dev/null\n+++ b/regression/test_new.py\n"
        b"@@ -0,0 +1,2 @@\n+def test_new():\n+    assert True\n"
    )
    applied = module.apply_patch(workspace, patch, task.spec.snapshot_sha256)
    assert applied.changed_files == ("regression/test_new.py",)
    exported = module.export_diff(task, workspace)
    other = create_workspace(task, tmp_path)
    module.apply_patch(other, exported, task.spec.snapshot_sha256)
    assert compute_snapshot_hash(other.repo) == compute_snapshot_hash(workspace.repo)


def test_empty_patch_is_a_noop_not_a_fabricated_change(tmp_path: Path) -> None:
    module = api()
    task = load_task(CASE)
    workspace = create_workspace(task, tmp_path)
    applied = module.apply_patch(workspace, b"", task.spec.snapshot_sha256)
    assert applied.changed_files == ()
    assert applied.after_sha256 == applied.before_sha256
    assert module.export_diff(task, workspace) == b""

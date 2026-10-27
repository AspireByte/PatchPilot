import importlib
from pathlib import Path

import pytest

from patchpilot.task import load_task
from patchpilot.workspace import create_workspace

ROOT = Path(__file__).resolve().parents[1]


def api():
    assert importlib.util.find_spec("patchpilot.service.artifacts"), "尚未实现受控产物存储"
    return importlib.import_module("patchpilot.service.artifacts")


def test_sealed_artifact_is_immutable_and_hash_checked(tmp_path: Path) -> None:
    module = api()
    storage = module.ArtifactStorage(tmp_path)
    first = storage.seal("00000000-0000-0000-0000-000000000001", "candidate_patch", b"hello")
    second = storage.seal("00000000-0000-0000-0000-000000000001", "candidate_patch", b"world")
    assert first["relative_path"] != second["relative_path"]
    assert storage.read(first) == b"hello"
    (tmp_path / first["relative_path"]).write_bytes(b"tampered")
    with pytest.raises(ValueError):
        storage.read(first)


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "C:/secret", "a\\b", "a/../b"])
def test_artifact_paths_cannot_escape(tmp_path: Path, path: str) -> None:
    with pytest.raises(ValueError):
        api().ArtifactStorage(tmp_path).read({"relative_path": path, "sha256": "0" * 64, "size": 1})


def test_restore_uses_base_and_confirmed_patch_not_old_workspace(tmp_path: Path) -> None:
    from patchpilot.patching import apply_patch
    from patchpilot.snapshot import compute_snapshot_hash

    task = load_task(ROOT / "benchmarks/tasks/pagination-001/task.json")
    original = create_workspace(task, tmp_path / "old")
    patch = (ROOT / "benchmarks/solutions/pagination-001/reference.patch").read_bytes()
    expected = apply_patch(original, patch, task.spec.snapshot_sha256).after_sha256
    (original.repo / "src/pagination_demo/paginator.py").write_bytes(b"uncertain after crash")
    restored = api().restore_workspace(task, tmp_path / "new", patch, expected)
    assert restored.root != original.root
    assert compute_snapshot_hash(restored.repo) == expected
    with pytest.raises(ValueError):
        api().restore_workspace(task, tmp_path / "bad", patch, "0" * 64)
    assert compute_snapshot_hash(task.snapshot_root) == task.spec.snapshot_sha256

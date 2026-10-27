import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from patchpilot.snapshot import compute_snapshot_hash


def test_hash_matches_the_documented_canonical_representation(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_bytes(b"hello")
    canonical = b'[["a.txt","2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"]]'
    assert compute_snapshot_hash(tmp_path) == hashlib.sha256(canonical).hexdigest()


def test_empty_directory_has_a_deterministic_hash(tmp_path: Path) -> None:
    assert compute_snapshot_hash(tmp_path) == hashlib.sha256(b"[]").hexdigest()


def test_creation_order_and_mtime_do_not_change_the_hash(tmp_path: Path) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    for root, names in ((first, ["b.txt", "a.txt"]), (second, ["a.txt", "b.txt"])):
        for name in names:
            (root / name).write_bytes(name.encode())
    os.utime(first / "a.txt", (1_000_000, 1_000_000))
    assert compute_snapshot_hash(first) == compute_snapshot_hash(second)


@pytest.mark.parametrize("change", ["content", "name", "test", "config", "hidden"])
def test_file_changes_invalidate_the_snapshot(tmp_path: Path, change: str) -> None:
    (tmp_path / "module.py").write_bytes(b"answer = 1\n")
    before = compute_snapshot_hash(tmp_path)
    match change:
        case "content":
            (tmp_path / "module.py").write_bytes(b"answer = 2\n")
        case "name":
            (tmp_path / "module.py").rename(tmp_path / "renamed.py")
        case "test":
            (tmp_path / "test_module.py").write_bytes(b"assert True\n")
        case "config":
            (tmp_path / "pytest.ini").write_bytes(b"[pytest]\n")
        case "hidden":
            (tmp_path / ".hidden").write_bytes(b"extra")
    assert compute_snapshot_hash(tmp_path) != before


def test_nested_unicode_paths_are_portable(tmp_path: Path) -> None:
    first, second = tmp_path / "中文目录", tmp_path / "other"
    for root in (first, second):
        (root / "子目录").mkdir(parents=True)
        (root / "子目录/文件.txt").write_bytes(b"same bytes")
    assert compute_snapshot_hash(first) == compute_snapshot_hash(second)


def test_non_directory_is_rejected(tmp_path: Path) -> None:
    file = tmp_path / "file"
    file.write_bytes(b"data")
    with pytest.raises(ValueError):
        compute_snapshot_hash(file)


@pytest.mark.parametrize("kind", ["file", "directory"])
def test_symbolic_links_are_rejected(tmp_path: Path, kind: str) -> None:
    outside, repo = tmp_path / "outside", tmp_path / "repo"
    outside.mkdir()
    repo.mkdir()
    (outside / "secret.txt").write_bytes(b"not part of the snapshot")
    target = outside if kind == "directory" else outside / "secret.txt"
    try:
        (repo / "link").symlink_to(target, target_is_directory=kind == "directory")
    except OSError:
        pytest.skip("当前系统没有创建符号链接的权限")
    with pytest.raises(ValueError):
        compute_snapshot_hash(repo)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 目录联接专用测试")
def test_windows_junctions_are_rejected(tmp_path: Path) -> None:
    outside, repo = tmp_path / "outside", tmp_path / "repo"
    outside.mkdir()
    repo.mkdir()
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(repo / "junction"), str(outside)],
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, "无法创建用于校验的目录联接"
    with pytest.raises(ValueError):
        compute_snapshot_hash(repo)

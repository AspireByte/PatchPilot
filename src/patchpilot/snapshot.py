"""为只读源码快照计算确定性的内容摘要。"""

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path


def _iter_files(directory: Path) -> Iterator[Path]:
    for path in directory.iterdir():
        if path.is_symlink() or path.is_junction():
            raise ValueError("快照中不允许符号链接或目录联接")
        if path.is_dir():
            yield from _iter_files(path)
        elif path.is_file():
            yield path
        else:
            raise ValueError("快照中只能包含普通文件和目录")


def compute_snapshot_hash(repo: Path) -> str:
    """包含全部文件的路径和原始字节，不依赖修改时间和遍历顺序。"""
    if repo.is_symlink() or repo.is_junction() or not repo.is_dir():
        raise ValueError("快照必须是非链接的普通目录")

    records: list[list[str]] = []
    for path in _iter_files(repo):
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        records.append([path.relative_to(repo).as_posix(), digest])
    records.sort(key=lambda record: record[0])
    canonical = json.dumps(records, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()

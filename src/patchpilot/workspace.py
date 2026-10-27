"""从核验过的源码快照创建独立工作区。"""

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from patchpilot.snapshot import compute_snapshot_hash
from patchpilot.task import LoadedTask


@dataclass(frozen=True)
class Workspace:
    root: Path
    repo: Path
    artifacts: Path
    base_sha256: str


def create_workspace(task: LoadedTask, runs_root: Path) -> Workspace:
    """只复制 repo，不复制任务包、答案或宿主环境；失败时清理未完成副本。"""
    destination = runs_root.resolve()
    source = task.snapshot_root.resolve()
    if destination == source or destination.is_relative_to(source):
        raise ValueError("工作区不能创建在原始快照内部")
    if compute_snapshot_hash(task.snapshot_root) != task.spec.snapshot_sha256:
        raise ValueError("原始快照已经改变，拒绝创建工作区")
    destination.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="run-", dir=destination))
    try:
        repo = Path(shutil.copytree(task.snapshot_root, root / "repo"))
        if compute_snapshot_hash(repo) != task.spec.snapshot_sha256:
            raise ValueError("复制后的快照摘要不一致")
        artifacts = root / "artifacts"
        artifacts.mkdir()
        return Workspace(root, repo, artifacts, task.spec.snapshot_sha256)
    except BaseException:
        shutil.rmtree(root)
        raise

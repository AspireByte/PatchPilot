"""从核验过的源码快照创建独立工作区。"""

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from patchpilot.legacy.benchmark.snapshot import compute_snapshot_hash
from patchpilot.legacy.benchmark.task import LoadedTask


@dataclass(frozen=True)
class Workspace:
    """独立固定任务工作区的路径集合，不负责调度模型或运行测试。

    root 是本次运行目录，repo 是可修改副本，artifacts 保存补丁、日志和报告。
    base_sha256 记录创建副本时的基线，用于恢复或应用补丁时核对版本。
    """

    root: Path
    repo: Path
    artifacts: Path
    base_sha256: str


def create_workspace(task: LoadedTask, runs_root: Path) -> Workspace:
    """只复制 repo，不复制任务包、答案或宿主环境；失败时清理未完成副本。"""
    # 复制前后都核对固定摘要；返回 Workspace 路径集合，供工具写代码、执行器存放产物。
    # 本地副本避免直接改基线，但并不隔离执行权限，运行仓库测试仍需 DockerRunner。
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
        # 这里捕获范围包含 Ctrl+C：只做资源回收后立即重新抛出，不吞掉取消或程序退出信号。
        shutil.rmtree(root)
        raise

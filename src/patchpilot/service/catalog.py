"""预置任务目录，不允许API提交任意路径、仓库链接或命令。"""

from pathlib import Path

from patchpilot.evaluation.catalog import SUPPORTED_TASKS
from patchpilot.task import LoadedTask, load_task


class TaskCatalog:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def load(self, task_id: str) -> LoadedTask:
        if task_id not in SUPPORTED_TASKS:
            raise ValueError("不支持的预置任务")
        task = load_task(self.root / task_id / "task.json")
        if task.spec.task_id != task_id:
            raise ValueError("任务登记与内容不一致")
        return task

    def public_tasks(self) -> list[dict]:
        return [
            {"task_id": name, "base_sha256": self.load(name).spec.snapshot_sha256}
            for name in SUPPORTED_TASKS
        ]

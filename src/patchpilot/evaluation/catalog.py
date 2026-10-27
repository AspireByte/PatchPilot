"""合成基准清单、拆分与显式数据批准指纹，不向模型提供参考答案。"""

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from patchpilot.snapshot import compute_snapshot_hash

MANIFEST_PATH = Path(__file__).with_name("dataset.json")


class TaskMetadata(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    task_id: str = Field(pattern=r"^[a-z]+(?:-[a-z]+)*-\d{3}$")
    split: Literal["development", "holdout"]
    group: str = Field(min_length=1, max_length=80)
    category: str = Field(min_length=1, max_length=80)
    source_kind: Literal["synthetic"]
    license: Literal["MIT"]
    expected_behavior: str = Field(min_length=1, max_length=600)


class Dataset(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)
    version: str
    provenance: str
    review_status: Literal["draft_requires_human_review"]
    tasks: list[TaskMetadata]

    @model_validator(mode="after")
    def validate_split(self):
        ids = [item.task_id for item in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("任务编号重复")
        groups = {
            name: {item.group for item in self.tasks if item.split == name}
            for name in ("development", "holdout")
        }
        if groups["development"] & groups["holdout"]:
            raise ValueError("关联任务group不能跨开发/保留集")
        return self

    def select(
        self, *, split: str = "development", task_ids: list[str] | None = None
    ) -> list[TaskMetadata]:
        if task_ids is not None:
            if (
                not task_ids
                or len(task_ids) != len(set(task_ids))
                or set(task_ids) - {item.task_id for item in self.tasks}
            ):
                raise ValueError("任务选择为空、重复或包含未知编号")
            return [self.by_id(name) for name in task_ids]
        if split not in ("development", "holdout", "all"):
            raise ValueError("不支持的数据拆分")
        return [item for item in self.tasks if split == "all" or item.split == split]

    def by_id(self, task_id: str) -> TaskMetadata:
        for item in self.tasks:
            if item.task_id == task_id:
                return item
        raise ValueError("未知任务编号")

    def fingerprint(self, tasks_root: Path, solutions_root: Path) -> str:
        records = [["metadata", hashlib.sha256(self.model_dump_json().encode()).hexdigest()]]
        license_file = tasks_root.parent / "LICENSE"
        records.append(
            [
                "license",
                hashlib.sha256(license_file.read_bytes()).hexdigest()
                if license_file.is_file()
                else "unavailable",
            ]
        )
        for item in sorted(self.tasks, key=lambda entry: entry.task_id):
            root = tasks_root / item.task_id
            for name, path in (
                ("manifest", root / "task.json"),
                ("issue", root / "Issue.md"),
                ("reference", solutions_root / item.task_id / "reference.patch"),
            ):
                try:
                    if (
                        path.is_symlink()
                        or path.parent.is_symlink()
                        or path.is_junction()
                        or path.parent.is_junction()
                    ):
                        raise ValueError("链接不支持")
                    digest = hashlib.sha256(path.read_bytes()).hexdigest()
                except (OSError, ValueError):
                    digest = "unavailable"
                records.append([f"{item.task_id}/{name}", digest])
            for name, path in (
                ("snapshot", root / "repo"),
                ("acceptance", acceptance_directory(item.task_id)),
            ):
                try:
                    digest = compute_snapshot_hash(path)
                except (OSError, ValueError):
                    digest = "unavailable"
                records.append([f"{item.task_id}/{name}", digest])
        return hashlib.sha256(
            json.dumps(records, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()


def load_dataset(path: Path = MANIFEST_PATH) -> Dataset:
    return Dataset.model_validate_json(path.read_text(encoding="utf-8"))


SUPPORTED_TASKS = tuple(item.task_id for item in load_dataset().tasks)


def acceptance_directory(task_id: str) -> Path:
    if task_id not in SUPPORTED_TASKS:
        raise ValueError("未知任务验收配置")
    return Path(__file__).resolve().parents[1] / "_acceptance" / task_id.replace("-", "_")

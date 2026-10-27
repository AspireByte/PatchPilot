"""宿主侧封存产物与从可信检查点重建；不复用崩溃工作树。"""

import hashlib
import os
import re
import uuid
from pathlib import Path

from patchpilot.patching import apply_patch
from patchpilot.snapshot import compute_snapshot_hash
from patchpilot.task import LoadedTask
from patchpilot.workspace import Workspace, create_workspace

MAX_ARTIFACT_BYTES = 8 * 1024 * 1024


class ArtifactStorage:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, relative: str) -> Path:
        parts = relative.split("/")
        if (
            not relative
            or ":" in relative
            or "\\" in relative
            or any(part in ("", ".", "..") for part in parts)
        ):
            raise ValueError("产物引用不是合法相对路径")
        path = self.root
        for part in parts:
            path /= part
            if path.is_symlink() or path.is_junction():
                raise ValueError("产物不能使用链接")
        if not path.resolve().is_relative_to(self.root):
            raise ValueError("产物引用超出存储根")
        return path

    def seal(self, attempt_id: str, kind: str, content: bytes) -> dict:
        identifier = str(uuid.UUID(attempt_id))
        if not re.fullmatch(r"[a-z_]{1,40}", kind) or len(content) > MAX_ARTIFACT_BYTES:
            raise ValueError("产物类型或大小不合法")
        relative = f"{identifier}/{uuid.uuid4().hex}-{kind}.bin"
        path = self._path(relative)
        path.parent.mkdir(exist_ok=True)
        with path.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return {
            "relative_path": relative,
            "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content),
        }

    def read(self, reference: dict) -> bytes:
        path = self._path(reference["relative_path"])
        with path.open("rb") as stream:
            content = stream.read(MAX_ARTIFACT_BYTES + 1)
        if (
            len(content) > MAX_ARTIFACT_BYTES
            or len(content) != reference["size"]
            or hashlib.sha256(content).hexdigest() != reference["sha256"]
        ):
            raise ValueError("产物内容与已登记版本不一致")
        return content


def restore_workspace(
    task: LoadedTask, runs_root: Path, patch: bytes, candidate_sha256: str
) -> Workspace:
    workspace = create_workspace(task, runs_root)
    apply_patch(workspace, patch, task.spec.snapshot_sha256)
    if compute_snapshot_hash(workspace.repo) != candidate_sha256:
        raise ValueError("检查点无法一致重建，拒绝继续")
    return workspace

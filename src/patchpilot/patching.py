"""在临时副本检查文本补丁，仅允许修改已有 Python 源码。"""

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from patchpilot.snapshot import compute_snapshot_hash
from patchpilot.task import LoadedTask
from patchpilot.workspace import Workspace

MAX_PATCH_BYTES = 1024 * 1024
_SOURCE = r"src/[A-Za-z0-9_./-]+\.py"
_REGRESSION = r"regression/test_[A-Za-z0-9_]+\.py"
_HEADER = re.compile(rf"diff --git a/({_SOURCE}|{_REGRESSION}) b/\1")


def _inventory(repo: Path) -> dict[str, str]:
    compute_snapshot_hash(repo)
    result = {}
    for path in repo.rglob("*"):
        if path.is_file():
            relative = path.relative_to(repo).as_posix()
            if ".git" in PurePosixPath(relative).parts:
                raise ValueError("工作区不能包含 Git 元数据")
            result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _allowed_path(name: str) -> bool:
    parts = name.split("/")
    return _HEADER.fullmatch(f"diff --git a/{name} b/{name}") is not None and all(
        part not in ("", ".", "..") and not part.startswith(".") for part in parts
    )


def _validate_patch(patch: bytes, repo: Path) -> set[str]:
    if len(patch) > MAX_PATCH_BYTES or b"\x00" in patch:
        raise ValueError("补丁过大或包含二进制内容")
    if not patch:
        return set()
    try:
        lines = patch.decode("utf-8").splitlines()
    except UnicodeDecodeError as error:
        raise ValueError("补丁必须使用 UTF-8 文本格式") from error
    headers = [index for index, line in enumerate(lines) if line.startswith("diff --git ")]
    if not headers or headers[0] != 0:
        raise ValueError("补丁必须是普通 Git unified diff")
    paths: set[str] = set()
    for start, end in zip(headers, [*headers[1:], len(lines)], strict=True):
        match = _HEADER.fullmatch(lines[start])
        if match is None or not _allowed_path(match[1]):
            raise ValueError("补丁只能修改 src 内已有的普通 .py 文件")
        name = match[1]
        new_test = re.fullmatch(_REGRESSION, name) is not None
        if name in paths or ((repo / name).exists() if new_test else not (repo / name).is_file()):
            raise ValueError("补丁路径重复、目标缺失或回归测试已经存在")
        paths.add(name)
        block = lines[start + 1 : end]
        if new_test and block and block[0] == "new file mode 100644":
            block = block[1:]
        if block and block[0].startswith("index "):
            if re.fullmatch(r"index [0-9a-f]+\.\.[0-9a-f]+(?: 100644)?", block[0]) is None:
                raise ValueError("不支持补丁中的文件模式")
            block = block[1:]
        old_header = "--- /dev/null" if new_test else f"--- a/{name}"
        if len(block) < 3 or block[:2] != [old_header, f"+++ b/{name}"]:
            raise ValueError("拒绝删除、重命名、模式修改或不一致的路径")
        if not block[2].startswith("@@ "):
            raise ValueError("缺少文本修改块")
    return paths


def _git(arguments: list[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CEILING_DIRECTORIES": str(cwd.resolve()),
        }
    )
    try:
        return subprocess.run(
            ["git", "-c", "core.autocrlf=false", *arguments],
            cwd=cwd,
            env=environment,
            capture_output=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("无法执行 Git 补丁操作，请检查 Git 安装与运行状态") from error


def _changed(before: dict[str, str], after: dict[str, str]) -> tuple[str, ...]:
    deleted = before.keys() - after.keys()
    added = after.keys() - before.keys()
    if deleted or any(re.fullmatch(_REGRESSION, name) is None for name in added):
        raise ValueError("不允许删除文件；只能在 regression 中新增回归测试")
    modified = [name for name in before if name in after and before[name] != after[name]]
    if any(not _allowed_path(name) or not name.startswith("src/") for name in modified):
        raise ValueError("发现原测试、配置或源码范围之外的修改")
    return tuple(sorted([*modified, *added]))


@dataclass(frozen=True)
class AppliedPatch:
    patch_sha256: str
    before_sha256: str
    after_sha256: str
    changed_files: tuple[str, ...]


def apply_patch(workspace: Workspace, patch: bytes, expected_hash: str) -> AppliedPatch:
    """先在 staging 检查完整补丁，再替换工作树；仅支持单用户串行操作。"""
    before_hash = compute_snapshot_hash(workspace.repo)
    if before_hash != expected_hash:
        raise ValueError("工作区版本已改变，拒绝基于陈旧内容应用补丁")
    before = _inventory(workspace.repo)
    paths = _validate_patch(patch, workspace.repo)
    patch_hash = hashlib.sha256(patch).hexdigest()
    if not paths:
        return AppliedPatch(patch_hash, before_hash, before_hash, ())
    with tempfile.TemporaryDirectory(prefix="patch-", dir=workspace.root) as temporary:
        root = Path(temporary)
        stage = Path(shutil.copytree(workspace.repo, root / "staging"))
        patch_file = root / "candidate.patch"
        patch_file.write_bytes(patch)
        for arguments in (
            ["apply", "--check", "--verbose", str(patch_file)],
            ["apply", "--verbose", str(patch_file)],
        ):
            result = _git(arguments, stage)
            if result.returncode != 0 or b"offset" in result.stderr.lower():
                raise ValueError("补丁上下文或位置不匹配，原工作区保持不变")
        changed = _changed(before, _inventory(stage))
        if set(changed) != paths:
            raise ValueError("补丁声明路径与实际修改不一致")
        after_hash = compute_snapshot_hash(stage)
        if compute_snapshot_hash(workspace.repo) != before_hash:
            raise ValueError("应用期间工作区发生变化，拒绝覆盖")
        backup = root / "backup"
        workspace.repo.rename(backup)
        try:
            stage.rename(workspace.repo)
        except BaseException:
            backup.rename(workspace.repo)
            raise
    return AppliedPatch(patch_hash, before_hash, after_hash, changed)


def export_diff(task: LoadedTask, workspace: Workspace) -> bytes:
    """相对原始快照导出可重新应用的补丁，不包含宿主绝对路径。"""
    if compute_snapshot_hash(task.snapshot_root) != task.spec.snapshot_sha256:
        raise ValueError("原始快照已经改变")
    _changed(_inventory(task.snapshot_root), _inventory(workspace.repo))
    with tempfile.TemporaryDirectory(prefix="diff-", dir=workspace.root) as temporary:
        root = Path(temporary)
        shutil.copytree(task.snapshot_root, root / "a")
        shutil.copytree(workspace.repo, root / "b")
        result = _git(
            [
                "diff",
                "--no-index",
                "--no-renames",
                "--no-ext-diff",
                "--no-textconv",
                "--src-prefix=",
                "--dst-prefix=",
                "--",
                "a",
                "b",
            ],
            root,
        )
        if result.returncode not in (0, 1):
            raise ValueError("无法导出补丁差异")
        diff = re.sub(
            rb"(?m)^diff --git b/(regression/test_[A-Za-z0-9_]+\.py) b/\1$",
            rb"diff --git a/\1 b/\1",
            result.stdout,
        )
        _validate_patch(diff, task.snapshot_root)
        return diff

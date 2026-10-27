"""加载阶段一的预置任务；这里不执行仓库代码，也不授予运行权限。"""

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from patchpilot.snapshot import compute_snapshot_hash


class TaskValidationError(ValueError):
    """任务描述、文件布局或快照内容不符合约定。"""


class TaskSpec(BaseModel):
    """首版只接受固定布局和程序已登记的配置。"""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    task_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    snapshot_path: Literal["repo"]
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    issue_path: Literal["Issue.md"]
    environment_id: Literal["python-pytest-v1"]
    writable_dirs: list[Literal["src"]] = Field(min_length=1, max_length=1)
    test_profile: Literal["pytest-visible-v1"]


@dataclass(frozen=True)
class LoadedTask:
    spec: TaskSpec
    snapshot_root: Path
    issue: str


class AgentTaskInput(BaseModel):
    """显式公开视图，不包含内部文件路径或维护者资料。"""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    task_id: str
    snapshot_path: Literal["repo"]
    snapshot_sha256: str
    issue: str
    environment_id: Literal["python-pytest-v1"]
    writable_dirs: list[Literal["src"]] = Field(min_length=1, max_length=1)
    test_profile: Literal["pytest-visible-v1"]


def _resolve_within(root: Path, relative: str) -> Path:
    candidate = root
    for part in PurePosixPath(relative).parts:
        candidate /= part
        if candidate.is_symlink() or candidate.is_junction():
            raise TaskValidationError("任务文件不允许使用符号链接或目录联接")
    resolved = candidate.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise TaskValidationError("任务路径不能超出所属目录")
    return resolved


def _describe_errors(error: ValidationError) -> str:
    messages = {
        "missing": "缺少必要字段",
        "extra_forbidden": "包含不支持的字段",
        "literal_error": "不支持的取值",
        "string_type": "必须是字符串",
        "list_type": "必须是列表",
        "string_pattern_mismatch": "格式不正确",
        "string_too_short": "内容过短",
        "string_too_long": "内容过长",
        "too_short": "项目数量不足",
        "too_long": "项目数量过多",
        "json_invalid": "不是有效的 JSON",
    }
    details = []
    for item in error.errors(include_url=False, include_input=False, include_context=False):
        field = ".".join(str(part) for part in item["loc"]) or "任务"
        details.append(f"{field}：{messages.get(item['type'], '字段类型或取值不合法')}")
    return "；".join(details)


def load_task(path: Path) -> LoadedTask:
    """校验元数据、文件布局和实际内容摘要，但不运行其中的代码。"""
    if path.is_symlink() or path.is_junction():
        raise TaskValidationError("任务描述文件不能是链接")
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise TaskValidationError("无法读取任务描述，请检查文件路径、UTF-8 编码和权限") from error
    try:
        spec = TaskSpec.model_validate_json(content)
    except ValidationError as error:
        raise TaskValidationError(_describe_errors(error)) from error

    try:
        root = path.parent.resolve(strict=True)
        snapshot_root = _resolve_within(root, spec.snapshot_path)
        issue_file = _resolve_within(root, spec.issue_path)
        if not snapshot_root.is_dir() or not issue_file.is_file():
            raise TaskValidationError("任务必须包含 repo 目录和 Issue.md 文件")
        for directory in ("src", "tests", "repro"):
            if not _resolve_within(snapshot_root, directory).is_dir():
                raise TaskValidationError("快照缺少源码或可见测试目录")
        if not _resolve_within(snapshot_root, "pytest.ini").is_file():
            raise TaskValidationError("快照缺少测试配置 pytest.ini")
        if compute_snapshot_hash(snapshot_root) != spec.snapshot_sha256:
            raise TaskValidationError("快照 SHA-256 不匹配，源码、测试或配置可能已经改变")
        issue = issue_file.read_text(encoding="utf-8")
        if not issue.strip():
            raise TaskValidationError("问题描述不能为空")
    except TaskValidationError:
        raise
    except (OSError, UnicodeError) as error:
        raise TaskValidationError("无法读取任务相关文件，请检查布局、UTF-8 编码和权限") from error
    except ValueError as error:
        raise TaskValidationError(str(error)) from error
    return LoadedTask(spec=spec, snapshot_root=snapshot_root, issue=issue)


def to_agent_input(task: LoadedTask) -> AgentTaskInput:
    """逐项构造公开字段，不能直接序列化内部 LoadedTask。"""
    return AgentTaskInput(
        task_id=task.spec.task_id,
        snapshot_path=task.spec.snapshot_path,
        snapshot_sha256=task.spec.snapshot_sha256,
        issue=task.issue,
        environment_id=task.spec.environment_id,
        writable_dirs=task.spec.writable_dirs.copy(),
        test_profile=task.spec.test_profile,
    )

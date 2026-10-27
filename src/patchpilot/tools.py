"""工作区内的受控工具与统一调度，不接受宿主路径或命令字符串。"""

import json
import re
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from patchpilot.patching import apply_patch, export_diff
from patchpilot.sandbox import DockerRunner
from patchpilot.snapshot import compute_snapshot_hash
from patchpilot.task import LoadedTask
from patchpilot.workspace import Workspace

MAX_FILE_BYTES = 256 * 1024
MAX_RESULT_CHARS = 16000


class Arguments(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class ListArgs(Arguments):
    directory: str = "."
    limit: int = Field(default=100, ge=1, le=200)


class ReadArgs(Arguments):
    path: str
    start_line: int = Field(default=1, ge=1)
    end_line: int | None = Field(default=None, ge=1)


class SearchArgs(ListArgs):
    query: str = Field(min_length=1, max_length=200)


class PatchArgs(Arguments):
    patch: str = Field(max_length=1024 * 1024)
    expected_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class RegressionArgs(Arguments):
    path: str
    content: str = Field(min_length=1, max_length=16000)


class TestArgs(Arguments):
    target: str = "visible"


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    name: str
    ok: bool
    data: dict[str, Any]
    error: dict[str, str] | None = None
    truncated: bool = False


class ToolError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


_SCHEMAS: dict[str, tuple[type[Arguments], str]] = {
    "list_files": (ListArgs, "列出工作区相对路径。先用目录概览定位文件，结果可能被截断。"),
    "read_file": (ReadArgs, "按1起始的闭区间行号读取UTF-8文件，同时返回当前工作区摘要。"),
    "search_text": (SearchArgs, "在指定目录进行区分大小写的精确文本搜索，返回路径与行号。"),
    "apply_patch": (
        PatchArgs,
        "应用普通Git unified diff。必须传入最近观察的expected_hash，禁止修改原测试和配置。",
    ),
    "add_regression_test": (
        RegressionArgs,
        "只能在regression/test_*.py新增一个回归测试，不能覆盖已有文件。",
    ),
    "get_diff": (Arguments, "查看相对原始快照的累积差异与当前工作区摘要。"),
    "run_tests": (
        TestArgs,
        "在容器运行公开测试。target为visible/original/repro/regression或公开测试文件路径，不能传命令或参数。",
    ),
}


def tool_definitions() -> list[dict[str, Any]]:
    return [
        {"name": name, "description": description, "input_schema": schema.model_json_schema()}
        for name, (schema, description) in _SCHEMAS.items()
    ]


class ToolDispatcher:
    def __init__(self, task: LoadedTask, workspace: Workspace, runner: DockerRunner | None = None):
        self.task = task
        self.workspace = workspace
        self.runner = runner or DockerRunner()

    def _path(self, name: str, *, directory: bool = False, create: bool = False) -> Path:
        if name == "." and directory:
            return self.workspace.repo
        parts = name.split("/")
        if (
            not name
            or "\\" in name
            or ":" in name
            or "\x00" in name
            or any(part in ("", ".", "..") or part.startswith(".") for part in parts)
            or parts[0] not in ("src", "tests", "repro", "regression", "pytest.ini", "README.md")
        ):
            raise ToolError("path_denied", "只能访问工作区内公开的普通相对路径")
        candidate = self.workspace.repo
        for part in parts:
            candidate /= part
            if candidate.is_symlink() or candidate.is_junction():
                raise ToolError("path_denied", "禁止访问符号链接或目录联接")
        if not candidate.resolve().is_relative_to(self.workspace.repo.resolve()):
            raise ToolError("path_denied", "路径超出工作区")
        if not candidate.exists() and not create:
            raise ToolError("not_found", "文件或目录不存在")
        if candidate.exists() and (candidate.is_dir() != directory):
            raise ToolError("invalid_arguments", "文件与目录类型不匹配")
        return candidate

    def _files(self, directory: str) -> list[Path]:
        root = self._path(directory, directory=True)
        found = []
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(self.workspace.repo).as_posix()
            if any(
                part.startswith(".") or part == "__pycache__"
                for part in path.relative_to(root).parts
            ):
                continue
            if path.is_symlink() or path.is_junction():
                raise ToolError("path_denied", "目录包含不允许的链接")
            if path.is_file():
                try:
                    self._path(relative)
                except ToolError as error:
                    if error.code == "path_denied":
                        continue
                    raise
                found.append(path)
            if len(found) > 2000:
                raise ToolError("too_large", "文件数量超限，请缩小目录范围")
        return found

    def _text(self, path: Path) -> str:
        with path.open("rb") as stream:
            content = stream.read(MAX_FILE_BYTES + 1)
        if len(content) > MAX_FILE_BYTES:
            raise ToolError("too_large", "文件超过读取上限，请使用更小文件")
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ToolError("invalid_encoding", "只能读取UTF-8文本文件") from error

    def dispatch(
        self, call_id: str, name: str, arguments: object, *, timeout_seconds: float | None = None
    ) -> ToolResult:
        truncated = False
        try:
            if name not in _SCHEMAS:
                raise ToolError("unknown_tool", "不支持的工具名称")
            schema, _ = _SCHEMAS[name]
            args = schema.model_validate(arguments)
            data, truncated = self._execute(name, args, timeout_seconds)
            result = ToolResult(call_id, name, True, data, truncated=truncated)
        except ValidationError:
            result = ToolResult(
                call_id,
                name,
                False,
                {},
                {"code": "invalid_arguments", "message": "参数字段、类型或范围不符合工具约定"},
            )
        except ToolError as error:
            result = ToolResult(
                call_id, name, False, {}, {"code": error.code, "message": str(error)}
            )
        except (ValueError, OSError):
            result = ToolResult(
                call_id,
                name,
                False,
                {},
                {
                    "code": "operation_failed",
                    "message": "文件操作或补丁检查失败，内容可能已变化；请重新读取后再尝试",
                },
            )
        evidence = self.workspace.artifacts / "tools"
        evidence.mkdir(exist_ok=True)
        raw = json.dumps(asdict(result), ensure_ascii=False)
        (evidence / f"{uuid.uuid4().hex}.json").write_text(raw, encoding="utf-8")
        if len(raw) > MAX_RESULT_CHARS:
            return ToolResult(
                call_id,
                name,
                result.ok,
                {
                    "preview": json.dumps(result.data, ensure_ascii=False)[:12000],
                    "original_chars": len(raw),
                },
                result.error,
                True,
            )
        return result

    def _execute(self, name: str, args: Arguments, timeout: float | None) -> tuple[dict, bool]:
        if name == "list_files":
            paths = self._files(args.directory)
            return {
                "files": [
                    p.relative_to(self.workspace.repo).as_posix() for p in paths[: args.limit]
                ]
            }, len(paths) > args.limit
        if name == "read_file":
            text = self._text(self._path(args.path)).splitlines()
            end = args.end_line if args.end_line is not None else args.start_line + 99
            if end < args.start_line:
                raise ToolError("invalid_arguments", "结束行不能早于起始行")
            stop = min(end, args.start_line + 99, len(text))
            lines = [
                {"line": index + 1, "text": text[index][:1000]}
                for index in range(args.start_line - 1, stop)
            ]
            cut = end > stop and stop < len(text)
            cut = cut or any(len(text[index]) > 1000 for index in range(args.start_line - 1, stop))
            return {
                "path": args.path,
                "lines": lines,
                "total_lines": len(text),
                "workspace_hash": compute_snapshot_hash(self.workspace.repo),
            }, cut
        if name == "search_text":
            matches = []
            for path in self._files(args.directory):
                for number, line in enumerate(self._text(path).splitlines(), 1):
                    if args.query in line:
                        if len(matches) == args.limit:
                            return {"matches": matches}, True
                        matches.append(
                            {
                                "path": path.relative_to(self.workspace.repo).as_posix(),
                                "line": number,
                                "text": line[:1000],
                            }
                        )
            return {"matches": matches}, any(len(item["text"]) == 1000 for item in matches)
        if name == "apply_patch":
            return asdict(
                apply_patch(self.workspace, args.patch.encode("utf-8"), args.expected_hash)
            ), False
        if name == "add_regression_test":
            if re.fullmatch(r"regression/test_[A-Za-z0-9_]+\.py", args.path) is None:
                raise ToolError("path_denied", "只能新增regression/test_*.py")
            path = self._path(args.path, create=True)
            path.parent.mkdir(exist_ok=True)
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(args.content)
            return {
                "path": args.path,
                "workspace_hash": compute_snapshot_hash(self.workspace.repo),
            }, False
        if name == "get_diff":
            diff = export_diff(self.task, self.workspace).decode("utf-8")
            return {
                "diff": diff[:12000],
                "workspace_hash": compute_snapshot_hash(self.workspace.repo),
            }, len(diff) > 12000
        target = args.target
        profiles = {
            "visible": ("tests", "repro"),
            "original": ("tests",),
            "repro": ("repro",),
            "regression": ("regression",),
        }
        if target in profiles:
            targets = profiles[target]
            if target == "visible" and (self.workspace.repo / "regression").is_dir():
                targets = (*targets, "regression")
        else:
            if not re.fullmatch(r"(?:tests|repro|regression)/[A-Za-z0-9_/]+\.py", target):
                raise ToolError("invalid_arguments", "不支持的测试目标，不能传命令或参数")
            self._path(target)
            targets = (target,)
        for item in targets:
            self._path(item, directory=not item.endswith(".py"))
        result = self.runner.run(self.workspace, targets=targets, time_budget=timeout)
        summary = {
            key: getattr(result, key)
            for key in (
                "status",
                "exit_code",
                "passed",
                "failed",
                "errors",
                "skipped",
                "duration_seconds",
                "cleanup_ok",
            )
        }
        log = (
            Path(result.log_path).read_text(encoding="utf-8")
            if Path(result.log_path).is_file()
            else ""
        )
        summary["failure_summary"] = log[-6000:].replace(str(self.workspace.root), "<workspace>")
        return summary, len(log) > 6000

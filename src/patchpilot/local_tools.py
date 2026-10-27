"""本地开发工具；文件访问受仓库边界约束，授权命令在宿主环境执行。"""

import difflib
import hashlib
import math
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from patchpilot.control import ExecutionControl

MAX_FILE_BYTES = 256 * 1024
MAX_OUTPUT = 16000
SKIP_DIRS = {
    "node_modules",
    "__pycache__",
    "venv",
    "env",
    "dist",
    "build",
    "target",
    "_acceptance",
}
PRIVATE_NAMES = {"credentials", "credentials.json", "id_rsa", "id_ed25519"}


class Arguments(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class ListArgs(Arguments):
    directory: str = "."
    limit: int = Field(default=100, ge=1, le=200)


class SearchArgs(ListArgs):
    query: str = Field(min_length=1, max_length=200)


class ReadArgs(Arguments):
    path: str
    start_line: int = Field(default=1, ge=1)
    end_line: int | None = Field(default=None, ge=1)


class WriteArgs(Arguments):
    path: str
    content: str = Field(max_length=MAX_FILE_BYTES)
    expected_hash: str | None


class EditArgs(Arguments):
    path: str
    old_text: str = Field(min_length=1, max_length=MAX_FILE_BYTES)
    new_text: str = Field(max_length=MAX_FILE_BYTES)
    expected_hash: str


class CommandArgs(Arguments):
    command: str = Field(min_length=1, max_length=16000)
    timeout_seconds: float = Field(default=120.0, gt=0, le=600)


SCHEMAS = {
    "list_files": (ListArgs, "列出仓库相对文件路径，默认排除凭据、依赖、缓存与会话产物。"),
    "search_text": (SearchArgs, "在目录内搜索精确文本，返回路径与行号，结果可能截断。"),
    "read_file": (ReadArgs, "按1起始行号读取UTF-8文本，返回文件摘要供修改前校验。"),
    "write_file": (
        WriteArgs,
        "新建或替换UTF-8文件；替换必须先读取并传expected_hash，新建传null。",
    ),
    "edit_file": (EditArgs, "将唯一匹配的old_text替换为new_text，必须传最近读取的摘要。"),
    "get_diff": (Arguments, "查看本会话文件工具首次修改前与当前内容的差异，不要求Git仓库。"),
    "run_command": (
        CommandArgs,
        "在仓库目录运行宿主命令。Windows使用PowerShell -NoProfile；记录退出码和有界输出。",
    ),
}


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def excluded(part: str) -> bool:
    lower = part.lower()
    return (
        (lower.startswith(".") and lower != ".github")
        or lower in SKIP_DIRS
        or lower in PRIVATE_NAMES
        or lower.endswith((".pem", ".key", ".p12", ".pfx"))
        or lower.endswith(".egg-info")
    )


class LocalTools:
    def __init__(
        self,
        repo: Path,
        *,
        allow_commands: bool = False,
        baselines: dict[str, str | None] | None = None,
        on_change: Callable[[], None] | None = None,
        control: ExecutionControl | None = None,
    ):
        if repo.is_symlink() or repo.is_junction() or not repo.is_dir():
            raise ValueError("仓库必须是存在的普通目录")
        self.repo = repo.resolve()
        self.allow_commands = allow_commands
        self.baselines = baselines if baselines is not None else {}
        self.observed: dict[str, str] = {}
        self.on_change = on_change or (lambda: None)
        self.control = control

    def definitions(self) -> list[dict]:
        return [
            {"name": name, "description": description, "input_schema": schema.model_json_schema()}
            for name, (schema, description) in SCHEMAS.items()
            if name != "run_command" or self.allow_commands
        ]

    def path(self, name: str, *, directory: bool = False) -> Path:
        if name == "." and directory:
            return self.repo
        parts = name.split("/")
        if (
            not name
            or "\\" in name
            or ":" in name
            or "\x00" in name
            or any(part in ("", ".", "..") or excluded(part) for part in parts)
            or PurePosixPath(name).is_absolute()
            or name.startswith("benchmarks/solutions/")
        ):
            raise ValueError("路径被拒绝：只允许仓库内公开的普通相对路径")
        current = self.repo
        for part in parts:
            current /= part
            if current.is_symlink() or current.is_junction():
                raise ValueError("路径被拒绝：不允许链接或目录联接")
        if not current.resolve().is_relative_to(self.repo):
            raise ValueError("路径超出仓库")
        return current

    def _files(self, directory: str):
        root = self.path(directory, directory=True)
        if not root.is_dir():
            raise ValueError("目录不存在")
        count = 0
        for parent, directories, files in os.walk(root, followlinks=False):
            directories[:] = sorted(
                name
                for name in directories
                if not excluded(name)
                and not (Path(parent) / name).is_symlink()
                and not (Path(parent) / name).is_junction()
                and (Path(parent) / name).relative_to(self.repo).as_posix()
                != "benchmarks/solutions"
            )
            for name in sorted(files):
                if excluded(name):
                    continue
                relative = (Path(parent) / name).relative_to(self.repo).as_posix()
                try:
                    path = self.path(relative)
                except ValueError:
                    continue
                if path.is_file():
                    count += 1
                    if count > 20000:
                        raise ValueError("文件过多，请缩小目录范围")
                    yield relative, path

    def _read(self, path: Path) -> bytes:
        with path.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("文件超过256 KiB限制")
        data.decode("utf-8")
        if b"\x00" in data:
            raise ValueError("不支持二进制文件")
        return data

    def dispatch(self, name: str, arguments: dict, *, remaining: float = 600) -> dict:
        try:
            if name not in SCHEMAS or (name == "run_command" and not self.allow_commands):
                raise ValueError("未开放该工具；运行命令需要 --allow-commands")
            args = SCHEMAS[name][0].model_validate(arguments)
            if self.control:
                self.control.check()
            result = self._execute(name, args, remaining)
            return {"ok": True, **result}
        except (ValueError, OSError, ValidationError) as error:
            # 不回显参数或SDK异常，避免将凭据写入错误提示。
            message = "工具参数不符合约定" if isinstance(error, ValidationError) else str(error)
            return {"ok": False, "error": message[:1000]}

    def _execute(self, name: str, args: Arguments, remaining: float) -> dict:
        if name == "list_files":
            files = []
            for relative, _ in self._files(args.directory):
                files.append(relative)
                if len(files) > args.limit:
                    break
            return {"files": files[: args.limit], "truncated": len(files) > args.limit}
        if name == "search_text":
            matches = []
            size = 0
            for relative, path in self._files(args.directory):
                try:
                    lines = self._read(path).decode("utf-8").splitlines()
                except (ValueError, OSError):
                    continue
                for number, line in enumerate(lines, 1):
                    if args.query in line:
                        item = {"path": relative, "line": number, "text": line[:1000]}
                        size += len(item["text"])
                        if len(matches) >= args.limit or size > MAX_OUTPUT:
                            return {"matches": matches, "truncated": True}
                        matches.append(item)
            return {"matches": matches, "truncated": False}
        if name == "read_file":
            path = self.path(args.path)
            data = self._read(path)
            lines = data.decode("utf-8").splitlines(keepends=True)
            end = args.end_line if args.end_line is not None else args.start_line + 199
            if end < args.start_line:
                raise ValueError("结束行不能小于起始行")
            text = "".join(lines[args.start_line - 1 : end])
            self.observed[args.path] = digest(data)
            return {
                "path": args.path,
                "sha256": digest(data),
                "text": text[:MAX_OUTPUT],
                "total_lines": len(lines),
                "truncated": len(text) > MAX_OUTPUT or end < len(lines),
            }
        if name in ("write_file", "edit_file"):
            path = self.path(args.path)
            old = self._read(path) if path.exists() else None
            actual = digest(old) if old is not None else None
            if actual != args.expected_hash or (
                old is not None and self.observed.get(args.path) != actual
            ):
                raise ValueError("文件版本冲突或未读取，请重新读取文件后修改")
            if name == "edit_file":
                if old is None:
                    raise ValueError("文件不存在")
                text = old.decode("utf-8")
                if text.count(args.old_text) != 1:
                    raise ValueError("待替换文本必须唯一匹配")
                new = text.replace(args.old_text, args.new_text, 1).encode("utf-8")
            else:
                new = args.content.encode("utf-8")
            if len(new) > MAX_FILE_BYTES or b"\x00" in new:
                raise ValueError("内容必须是256 KiB以内的普通文本")
            if args.path not in self.baselines:
                self.baselines[args.path] = old.decode("utf-8") if old is not None else None
                self.on_change()
            path.parent.mkdir(parents=True, exist_ok=True)
            # 修改前再检查一次；本地模式不提供跨编辑器事务锁。
            self.path(args.path)
            if (path.read_bytes() if path.exists() else None) != old:
                raise ValueError("写入前文件发生变化，请重新读取")
            if old is None:
                with path.open("xb") as stream:
                    stream.write(new)
            else:
                descriptor, temporary = tempfile.mkstemp(prefix=".patchpilot-", dir=path.parent)
                try:
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(new)
                    shutil.copymode(path, temporary)
                    os.replace(temporary, path)
                finally:
                    Path(temporary).unlink(missing_ok=True)
            self.observed.pop(args.path, None)
            self.on_change()
            return {"path": args.path, "sha256": digest(new), "bytes": len(new)}
        if name == "get_diff":
            return self.get_diff()
        return self._command(args, remaining)

    def get_diff(self) -> dict:
        parts = []
        for name, before in self.baselines.items():
            path = self.path(name)
            after = self._read(path).decode("utf-8") if path.exists() else ""
            for line in difflib.unified_diff(
                (before or "").splitlines(keepends=True),
                after.splitlines(keepends=True),
                fromfile=f"a/{name}" if before is not None else "/dev/null",
                tofile=f"b/{name}" if path.exists() else "/dev/null",
            ):
                parts.append(
                    line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                )
        text = "".join(parts)
        return {"diff": text[:MAX_OUTPUT], "truncated": len(text) > MAX_OUTPUT}

    def _command(self, args: CommandArgs, remaining: float) -> dict:
        if not math.isfinite(args.timeout_seconds) or remaining <= 0:
            raise ValueError("命令时限无效或预算已耗尽")
        if os.name == "nt":
            shell = shutil.which("pwsh.exe") or shutil.which("powershell.exe")
            if not shell:
                raise ValueError("找不到PowerShell")
            script = (
                "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n"
                "$OutputEncoding = [Console]::OutputEncoding\n$global:LASTEXITCODE = 0\n"
                + args.command
                + "\n$patchpilotSucceeded = $?\n"
                "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n"
                "if (-not $patchpilotSucceeded) { exit 1 }\n"
            )
            command = [shell, "-NoProfile", "-NonInteractive", "-Command", script]
        else:
            command = ["/bin/sh", "-c", args.command]
        deadline = time.monotonic() + min(args.timeout_seconds, remaining)
        output = bytearray()
        output_size = 0
        output_lock = threading.Lock()
        job = None

        def drain(stream) -> None:
            nonlocal output_size
            try:
                while data := stream.read(4096):
                    with output_lock:
                        output_size += len(data)
                        output.extend(data)
                        del output[:-MAX_OUTPUT]
            finally:
                stream.close()

        try:

            def spawn():
                return subprocess.Popen(
                    command,
                    cwd=self.repo,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    start_new_session=os.name != "nt",
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                )

            try:
                process = spawn()
            except OSError:
                fallback = shutil.which("powershell.exe") if os.name == "nt" else None
                if not fallback or command[0] == fallback:
                    raise
                # WindowsApps中存在别名并不代表pwsh能够启动。只在启动失败时回退。
                command[0] = fallback
                process = spawn()
            if os.name == "nt":
                from patchpilot.process_job import WindowsJob

                try:
                    job = WindowsJob()
                    job.assign(process)
                except OSError:
                    if job:
                        job.close()
                    process.kill()
                    process.wait(timeout=10)
                    process.stdout.close()
                    raise
            reader = threading.Thread(target=drain, args=(process.stdout,), daemon=True)
            reader.start()
            timed_out = False
            try:
                while process.poll() is None:
                    if self.control:
                        self.control.check()
                    if time.monotonic() >= deadline:
                        timed_out = True
                        break
                    time.sleep(0.05)
            finally:
                # 中断、超时和正常退出均清理同一进程组中的遗留子进程。
                if job:
                    job.close()
                else:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=10)
                reader.join(timeout=2)
            with output_lock:
                text = bytes(output).decode("utf-8", errors="replace")
                size = output_size
        finally:
            if job:
                job.close()
            self.observed.clear()
        return {
            "command": args.command,
            "exit_code": None if timed_out else process.returncode,
            "output": text,
            "timed_out": timed_out,
            "truncated": size > MAX_OUTPUT,
            "shell": "PowerShell" if os.name == "nt" else "sh",
        }

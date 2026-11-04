"""本地开发工具；文件访问受仓库边界约束，授权命令在宿主环境执行。"""

import difflib
import math
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from patchpilot.core.control import ExecutionControl
from patchpilot.infrastructure.process.runner import ProcessStartError, run_process
from patchpilot.infrastructure.verification.runner import VerificationRunner

from .filesystem import (
    MAX_FILE_BYTES,
    MAX_OUTPUT,
    Arguments,
    EditArgs,
    ListArgs,
    ReadArgs,
    SearchArgs,
    WriteArgs,
    digest,
    excluded,
)
from .harness import HarnessArgs
from .process import CommandArgs

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
    "run_harness_check": (
        HarnessArgs,
        "运行预先配置的受控项目检查；只能选择已有profile，不修改当前仓库。",
    ),
}


class LocalTools:
    """聊天模型可使用的本地工具集合，也是文件访问和执行权限的校验边界。

    definitions 向模型描述已开放的工具，dispatch 将工具名与参数分派到具体操作。
    文件工具只处理仓库内公开的普通文本；observed 记录读取版本，baselines 保留修改前内容。
    命令工具直接操作宿主仓库，Harness 则检查临时副本，两者需要分别授权。
    路径限制只约束这些文件工具，不能限制已授权宿主命令的系统访问能力。
    """

    def __init__(
        self,
        repo: Path,
        *,
        allow_commands: bool = False,
        baselines: dict[str, str | None] | None = None,
        on_change: Callable[[], None] | None = None,
        control: ExecutionControl | None = None,
        harness: VerificationRunner | None = None,
        on_harness_event: Callable[[str, dict], None] | None = None,
    ):
        """绑定仓库、授权选项、修改基线和可选 Harness，不在构造时执行命令。

        baselines 若由会话传入则共享同一字典，on_change 用于在修改前后通知会话保存。
        control 供长操作检查停止信号；harness 为空时不会开放项目检查工具。
        仓库必须是存在的普通目录，根目录为符号链接或 Windows 目录联接时拒绝初始化。
        """
        if repo.is_symlink() or repo.is_junction() or not repo.is_dir():
            raise ValueError("仓库必须是存在的普通目录")
        self.repo = repo.resolve()
        self.allow_commands = allow_commands
        self.baselines = baselines if baselines is not None else {}
        # observed 是本次运行中读到的文件摘要，baselines 是首次修改前的内容，用于展示 diff。
        # 两者用途不同：恢复能保留 diff 基线，但必须重新读取文件才能再次授权覆盖。
        self.observed: dict[str, str] = {}
        self.on_change = on_change or (lambda: None)
        self.control = control
        self.harness = harness
        self.on_harness_event = on_harness_event or (lambda _kind, _values: None)
        if self.harness is not None:
            self.harness.control = control
            self.harness.on_event = self.on_harness_event

    def definitions(self) -> list[dict]:
        """返回本次可用工具的名称、说明和 JSON 输入 schema。

        参数类同时用于生成 schema 与执行前校验，减少模型说明和执行约定不一致的问题。
        Harness profile 来自已加载配置，模型可以选择名称，但不能在调用参数里新增检查命令。
        """
        # 未授权工具从发给模型的定义中移除；dispatch 仍再次检查，不能只靠模型遵守提示。
        definitions = [
            {"name": name, "description": description, "input_schema": schema.model_json_schema()}
            for name, (schema, description) in SCHEMAS.items()
            if (name != "run_command" or self.allow_commands)
            and (name != "run_harness_check" or self.harness is not None)
        ]
        if self.harness is not None:
            for definition in definitions:
                if definition["name"] == "run_harness_check":
                    profiles = list(self.harness.config.profiles)
                    if profiles:
                        definition["input_schema"]["properties"]["profile"].update(
                            enum=profiles, default=self.harness.config.default_profile
                        )
                    definition["description"] += (
                        " 可用profile："
                        + ", ".join(profiles)
                        + "；默认："
                        + self.harness.config.default_profile
                    )
        return definitions

    def path(self, name: str, *, directory: bool = False) -> Path:
        """把以 / 分隔的仓库相对路径转换为校验后的本机 Path。

        directory=True 时允许用 . 表示仓库根；越界、隐藏/敏感路径及链接会抛出 ValueError。
        返回路径可以尚不存在，以支持新建文件；是否存在、是否为目录由具体操作继续检查。
        """
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
            or name.startswith(("benchmarks/solutions/", "benchmarks/acceptance/"))
        ):
            raise ValueError("路径被拒绝：只允许仓库内公开的普通相对路径")
        current = self.repo
        # 逐层拒绝链接，再核对解析后的路径，避免看似位于仓库内的路径实际指向外部。
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
            # 切片赋值原地修改 os.walk 提供的目录列表，使它不再进入被排除的子目录。
            directories[:] = sorted(
                name
                for name in directories
                if not excluded(name)
                and not (Path(parent) / name).is_symlink()
                and not (Path(parent) / name).is_junction()
                and (Path(parent) / name).relative_to(self.repo).as_posix()
                not in ("benchmarks/solutions", "benchmarks/acceptance")
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
                    # yield 逐个交出结果；调用方达到显示上限后可以停下，无须先收集全部文件。
                    yield relative, path

    def _read(self, path: Path) -> bytes:
        """读取有大小上限的 UTF-8 普通文本，返回原始字节供摘要与精确写入校验。

        调用前应先通过 path 校验访问边界；本方法不登记 observed，读取授权由 read_file 分支登记。
        使用字节而非解码后的文本作版本比较，可保留换行等原始内容差异。
        """
        with path.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        if len(data) > MAX_FILE_BYTES:
            raise ValueError("文件超过256 KiB限制")
        data.decode("utf-8")
        if b"\x00" in data:
            raise ValueError("不支持二进制文件")
        return data

    def dispatch(self, name: str, arguments: dict, *, remaining: float = 600) -> dict:
        """接收模型工具请求，完成权限、参数校验，并返回统一的结果字典。

        remaining 是会话传下来的剩余秒数，执行命令和 Harness 时不能重新获得整轮时限。
        参数或文件操作失败转换为 ok=false 的反馈，让模型有机会重新读取或调整参数。
        ExecutionStopped 会继续向会话传播，避免把取消误当成可重试的普通工具错误。
        """
        # 工具名和 JSON 参数进入这里，返回可写入 tool_result 的结构化字典。
        # ok 表示工具分发成功；例如 Harness 检查是否通过，还要读取嵌套结果的 status。
        try:
            if name not in SCHEMAS or (name == "run_command" and not self.allow_commands):
                raise ValueError("未开放该工具；运行命令需要 --allow-commands")
            if name == "run_harness_check" and self.harness is None:
                raise ValueError("未开放 Harness 检查；启动时请添加 --allow-harness")
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
        """执行已校验请求，返回各工具特有的数据；统一 ok 字段由 dispatch 添加。

        读取返回分页文本和文件摘要；写入/替换要求摘要匹配，并在首次修改前保存基线。
        get_diff 汇总基线差异，run_harness_check 委托 VerificationRunner，其余命令分支交给 _command。
        拆开校验与执行，便于让所有工具共用一致的入口，同时保留不同工具的实际约束。
        """
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
            # 同时核对模型携带的摘要与本次读取记录：只猜对摘要而没有读取，也不能覆盖已有文件。
            # 这是乐观并发检查：发现版本变化就拒绝，优势是不长期锁文件，代价是需要重新读取。
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
                # 在第一次写入前保存原文；之后多次编辑仍相对同一基线计算差异。
                self.baselines[args.path] = old.decode("utf-8") if old is not None else None
                self.on_change()
            path.parent.mkdir(parents=True, exist_ok=True)
            # 修改前再检查一次；本地模式不提供跨编辑器事务锁。
            self.path(args.path)
            if (path.read_bytes() if path.exists() else None) != old:
                raise ValueError("写入前文件发生变化，请重新读取")
            if old is None:
                # x 模式要求目标不存在，避免新建时意外覆盖刚由其他程序创建的文件。
                with path.open("xb") as stream:
                    stream.write(new)
            else:
                descriptor, temporary = tempfile.mkstemp(prefix=".patchpilot-", dir=path.parent)
                try:
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(new)
                    shutil.copymode(path, temporary)
                    # 同目录临时文件替换可避免读到半份内容，但不能消除最后检查后的并发修改窗口。
                    os.replace(temporary, path)
                finally:
                    Path(temporary).unlink(missing_ok=True)
            self.observed.pop(args.path, None)
            self.on_change()
            return {"path": args.path, "sha256": digest(new), "bytes": len(new)}
        if name == "get_diff":
            return self.get_diff()
        if name == "run_harness_check":
            if self.harness is None:
                raise ValueError("未开放 Harness 检查；启动时请添加 --allow-harness")
            result = self.harness.run(args.profile or None, remaining=remaining)
            return {"harness": result}
        return self._command(args, remaining)

    def get_diff(self) -> dict:
        """返回本会话文件工具累计修改的 unified diff 与截断标记。

        差异相对首次修改前保存的内容计算，不依赖 Git；新建文件以 /dev/null 作为旧路径。
        输出受 MAX_OUTPUT 限制，完整修改范围不能仅凭截断后的预览推断。
        """
        # 只遍历文件工具记录的基线；命令或外部编辑器单独修改的其他文件不会自动出现在这里。
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
        """通过系统 shell 在真实仓库执行授权命令，返回退出码、输出尾部和超时信息。

        Windows 优先 PowerShell，仅当程序尚未成功启动时允许尝试兼容的 PowerShell 入口；
        命令已启动后的失败不会因此重放。非 Windows 使用 /bin/sh。
        时限取命令参数与会话剩余预算的较小值，进程树回收由 run_process 负责。
        无论结果如何都会清空读取摘要，因为命令可能已改变文件。
        """
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
        try:
            try:
                result = run_process(
                    command,
                    cwd=self.repo,
                    timeout=min(args.timeout_seconds, remaining),
                    max_output=MAX_OUTPUT,
                    control=self.control,
                )
            except ProcessStartError:
                fallback = shutil.which("powershell.exe") if os.name == "nt" else None
                if not fallback or command[0] == fallback:
                    raise
                command[0] = fallback
                result = run_process(
                    command,
                    cwd=self.repo,
                    timeout=min(args.timeout_seconds, remaining),
                    max_output=MAX_OUTPUT,
                    control=self.control,
                )
        finally:
            # 命令可能改动任意文件，因此无论成功失败，都使已有读取摘要失效。
            self.observed.clear()
        return {
            "command": args.command,
            "exit_code": result.exit_code,
            "output": result.output,
            "timed_out": result.timed_out,
            "truncated": result.truncated,
            "shell": "PowerShell" if os.name == "nt" else "sh",
        }

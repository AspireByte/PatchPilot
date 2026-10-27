"""在仓库副本上执行登记检查；同一服务也适配内部基准验收。"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

from patchpilot.control import ExecutionControl, ExecutionStopped
from patchpilot.process_runner import ProcessInterrupted, run_process
from patchpilot.sandbox import DockerRunner, SandboxError

DEFAULT_CONFIG_NAME = ".patchpilot/harness.toml"
MAX_FILES = 20000
MAX_SNAPSHOT_BYTES = 100 * 1024 * 1024
SKIP_PARTS = {
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
SHELL_PROGRAMS = {"sh", "bash", "zsh", "fish", "cmd", "powershell", "pwsh"}


def _cwd(value: str) -> str:
    if (
        not value
        or "\\" in value
        or ":" in value
        or "\x00" in value
        or PurePosixPath(value).is_absolute()
        or ".." in value.split("/")
    ):
        raise ValueError("Harness cwd 必须是仓库内相对目录")
    return value


def _argv(value: object) -> tuple[str, ...]:
    if (
        not isinstance(value, list)
        or not value
        or len(value) > 100
        or any(not isinstance(arg, str) or not arg or len(arg) > 4096 for arg in value)
        or any(re.search(r"[|;&<>`$\x00\r\n]", arg) for arg in value)
    ):
        raise ValueError("Harness argv 必须是安全参数数组，不能包含 shell 拼接字符")
    program = value[0].replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe")
    if program in SHELL_PROGRAMS or program.endswith((".bat", ".cmd")):
        raise ValueError("Harness 不允许 shell 或批处理入口，请登记实际可执行程序")
    if program.startswith("python") and any(arg in ("-c", "-mcode") for arg in value[1:]):
        raise ValueError("Harness 不允许内联脚本，请登记仓库内明确的检查脚本")
    return tuple(value)


@dataclass(frozen=True)
class HarnessProfile:
    name: str
    argv: tuple[str, ...]
    cwd: str = "."


@dataclass(frozen=True)
class HarnessConfig:
    version: int = 1
    runtime: str = "auto"
    image: str | None = None
    default_profile: str = "test"
    timeout_seconds: float = 120
    max_output_bytes: int = 16000
    allow_host_fallback: bool = True
    profiles: dict[str, HarnessProfile] = field(default_factory=dict)
    path: str | None = None

    @classmethod
    def load(cls, repo: Path, path: Path | None = None, *, runtime_override: str | None = None):
        selected = path if path is not None else repo / DEFAULT_CONFIG_NAME
        # 配置是启动时授权的控制面，不在工具执行时重新加载。
        for ancestor in (selected, *selected.parents):
            if ancestor.is_symlink() or ancestor.is_junction():
                raise ValueError("Harness 配置不允许链接或目录联接")
        if path is not None and not selected.is_file():
            raise ValueError("找不到 Harness 配置")
        try:
            raw = (
                tomllib.loads(selected.read_text(encoding="utf-8-sig")) if selected.exists() else {}
            )
        except (OSError, ValueError) as error:
            raise ValueError("Harness TOML 配置无法读取") from error
        allowed = {
            "version",
            "runtime",
            "image",
            "default_profile",
            "timeout_seconds",
            "max_output_bytes",
            "allow_host_fallback",
            "profiles",
        }
        if raw.keys() - allowed:
            raise ValueError("Harness 配置包含未知字段")
        if type(raw.get("version", 1)) is not int or raw.get("version", 1) != 1:
            raise ValueError("Harness version 必须为 1")
        runtime = runtime_override if runtime_override is not None else raw.get("runtime", "auto")
        if runtime not in ("auto", "docker", "host"):
            raise ValueError("Harness runtime 必须是 auto、docker 或 host")
        image = raw.get("image")
        if image is not None and (
            not isinstance(image, str)
            or not image
            or image.startswith("-")
            or re.search(r"\s|\x00", image)
        ):
            raise ValueError("Harness image 无效")
        timeout = raw.get("timeout_seconds", 120)
        if (
            type(timeout) not in (int, float)
            or not math.isfinite(timeout)
            or not 0 < timeout <= 3600
        ):
            raise ValueError("Harness timeout_seconds 必须在 0 到 3600 秒之间")
        maximum = raw.get("max_output_bytes", 16000)
        if type(maximum) is not int or not 1024 <= maximum <= 1024 * 1024:
            raise ValueError("Harness max_output_bytes 必须在 1024 到 1048576 之间")
        fallback = raw.get("allow_host_fallback", True)
        if type(fallback) is not bool:
            raise ValueError("Harness allow_host_fallback 必须为布尔值")
        profiles = {}
        values = raw.get("profiles", {})
        if not isinstance(values, dict):
            raise ValueError("Harness profiles 必须为表")
        for name, value in values.items():
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or not isinstance(value, dict):
                raise ValueError("Harness profile 名称或配置无效")
            if value.keys() - {"argv", "cwd"}:
                raise ValueError("Harness profile 包含未知字段")
            directory = value.get("cwd", ".")
            if not isinstance(directory, str):
                raise ValueError("Harness cwd 必须为字符串")
            profiles[name] = HarnessProfile(name, _argv(value.get("argv")), _cwd(directory))
        if not selected.exists():
            python_project = any(
                (repo / name).is_file()
                for name in (
                    "pyproject.toml",
                    "pytest.ini",
                    "setup.cfg",
                    "setup.py",
                )
            ) or any(repo.glob("*.py"))
            if python_project:
                profiles["test"] = HarnessProfile("test", ("python", "-m", "pytest", "-q"))
        elif not profiles:
            raise ValueError("Harness 配置必须登记至少一个 profile")
        default = raw.get("default_profile", next(iter(profiles), "test"))
        if not isinstance(default, str) or (profiles and default not in profiles):
            raise ValueError("Harness default_profile 不存在")
        return cls(
            1,
            runtime,
            image,
            default,
            float(timeout),
            maximum,
            fallback,
            profiles,
            str(selected.resolve()) if selected.exists() else None,
        )


@dataclass(frozen=True)
class HarnessResult:
    status: str
    profile: str
    runtime: str
    evidence_level: str = "none"
    isolated: bool = False
    exit_code: int | None = None
    timed_out: bool = False
    cancelled: bool = False
    truncated: bool = False
    output: str = ""
    snapshot_sha256: str = ""
    duration_seconds: float = 0
    artifact_path: str = ""
    detail: str = ""
    image_id: str | None = None
    cleanup_ok: bool = True
    argv: tuple[str, ...] = ()
    cwd: str = "."
    fallback_reason: str = ""


class HarnessService:
    def __init__(
        self,
        repo: Path,
        config: HarnessConfig | None = None,
        *,
        artifacts_root: Path | None = None,
        control: ExecutionControl | None = None,
        on_event: Callable[[str, dict], None] | None = None,
    ):
        if repo.is_symlink() or repo.is_junction() or not repo.is_dir():
            raise ValueError("Harness 仓库必须是存在的普通目录")
        self.repo = repo.resolve()
        self.config = config or HarnessConfig.load(self.repo)
        self.artifacts_root = (artifacts_root or self.repo / ".patchpilot-runs").resolve()
        self.control = control
        self.on_event = on_event or (lambda _kind, _values: None)

    @staticmethod
    def _excluded(path: Path):
        return any(
            (part.startswith(".") and part != ".github")
            or part in SKIP_PARTS
            or part in PRIVATE_NAMES
            or part.endswith((".pem", ".key", ".p12", ".pfx", ".egg-info", ".pyc"))
            for part in (p.lower() for p in path.parts)
        ) or path.as_posix().lower().startswith("benchmarks/solutions/")

    def _check(self, deadline):
        if self.control:
            self.control.check()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Harness 总时限已耗尽")
        return remaining

    def _files(self):
        for parent, directories, files in os.walk(self.repo, followlinks=False):
            for name in list(directories):
                path = Path(parent) / name
                if self._excluded(path.relative_to(self.repo)) or path.resolve().is_relative_to(
                    self.artifacts_root
                ):
                    directories.remove(name)
                elif path.is_symlink() or path.is_junction():
                    raise ValueError("Harness 快照拒绝链接或目录联接")
            for name in sorted(files):
                path = Path(parent) / name
                relative = path.relative_to(self.repo)
                if self._excluded(relative) or path.resolve().is_relative_to(self.artifacts_root):
                    continue
                if path.is_symlink() or path.is_junction() or not path.is_file():
                    raise ValueError("Harness 快照只允许普通文件")
                yield relative, path

    def _copy_snapshot(self, destination: Path, deadline: float):
        records = []
        size = 0
        destination.mkdir()
        for relative, path in self._files():
            self._check(deadline)
            if len(records) >= MAX_FILES:
                raise ValueError("Harness 快照超过 20000 个文件")
            mode = path.stat().st_mode & 0o777
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with path.open("rb") as source, target.open("xb") as output:
                while chunk := source.read(65536):
                    self._check(deadline)
                    size += len(chunk)
                    if size > MAX_SNAPSHOT_BYTES:
                        raise ValueError("Harness 快照超过 100 MiB")
                    digest.update(chunk)
                    output.write(chunk)
            shutil.copymode(path, target)
            records.append([relative.as_posix(), digest.hexdigest(), mode])
        records.sort()
        digest = hashlib.sha256(json.dumps(records, separators=(",", ":")).encode()).hexdigest()
        return digest, records

    @staticmethod
    def _host_environment(copied):
        # 避免把父进程的 API 密钥和指向原仓库的 Python 路径直接传给检查。
        allowed = {
            "path",
            "systemroot",
            "windir",
            "comspec",
            "pathext",
            "temp",
            "tmp",
            "home",
            "userprofile",
            "lang",
            "lc_all",
            "virtual_env",
        }
        env = {key: value for key, value in os.environ.items() if key.lower() in allowed}
        env.update(
            {
                "PYTHONIOENCODING": "utf-8",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": str(copied / "src") + os.pathsep + str(copied),
            }
        )
        return env

    def _docker_image(self, deadline):
        if not self.config.image:
            raise SandboxError("未配置 Docker 镜像")
        return DockerRunner(image=self.config.image).check(
            time_budget=min(10, self._check(deadline))
        )["image_id"]

    def _docker_run(self, copied, profile, image_id, deadline):
        name = "patchpilot-harness-" + uuid.uuid4().hex
        # 只挂载临时副本，并为非 root 用户提供副本内的写权限。
        if os.name != "nt":
            for directory, _, files in os.walk(copied):
                Path(directory).chmod(0o777)
                for filename in files:
                    path = Path(directory) / filename
                    path.chmod(0o666 | (path.stat().st_mode & 0o111))
        command = [
            "docker",
            "run",
            "--name",
            name,
            "--pull",
            "never",
            "--network",
            "none",
            "--label",
            "patchpilot.managed=true",
            "--user",
            "10001:10001",
            "--cpus",
            "1.0",
            "--memory",
            "512m",
            "--pids-limit",
            "128",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--init",
            "--read-only",
            "--log-driver",
            "none",
            "--tmpfs",
            "/tmp:rw,nosuid,size=64m,mode=1777",
            "--mount",
            f"type=bind,source={copied},target=/workspace",
            "--workdir",
            "/workspace" + ("/" + profile.cwd if profile.cwd != "." else ""),
            "--env",
            "PYTHONPATH=/workspace/src:/workspace",
            "--env",
            "PYTHONIOENCODING=utf-8",
            "--entrypoint",
            profile.argv[0],
            image_id,
            *profile.argv[1:],
        ]
        cleanup_ok = True
        try:
            process = run_process(
                command,
                cwd=copied,
                timeout=self._check(deadline),
                max_output=self.config.max_output_bytes,
                control=self.control,
            )
        finally:
            # Docker CLI 被取消不会自动终止容器；始终按唯一名称回收。
            try:
                cleanup = subprocess.run(
                    ["docker", "rm", "--force", "--volumes", name],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                cleanup_ok = cleanup.returncode == 0 or b"No such container" in cleanup.stderr
            except (OSError, subprocess.SubprocessError):
                cleanup_ok = False
            self._cleanup_ok = cleanup_ok
        return process

    def run(self, profile: str | None = None, *, remaining: float | None = None):
        name = profile or self.config.default_profile
        started = time.monotonic()
        duration = (
            min(self.config.timeout_seconds, remaining)
            if remaining is not None
            else self.config.timeout_seconds
        )
        deadline = started + duration
        artifact = self.artifacts_root / ("harness-" + uuid.uuid4().hex)
        artifact.mkdir(parents=True)
        selected = self.config.profiles.get(name)
        result = HarnessResult(
            "infra_error", name, self.config.runtime, artifact_path=str(artifact)
        )
        self._cleanup_ok = True
        self.on_event("harness_started", {"profile": name})
        values = asdict(result)
        try:
            self._check(deadline)
            if selected is None:
                raise ValueError("未找到 Harness profile；请配置 .patchpilot/harness.toml")
            values.update(argv=selected.argv, cwd=selected.cwd)
            with tempfile.TemporaryDirectory(prefix="snapshot-", dir=artifact) as temporary:
                copied = Path(temporary) / "repo"
                snapshot, records = self._copy_snapshot(copied, deadline)
                values["snapshot_sha256"] = snapshot
                (artifact / "manifest.json").write_text(
                    json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                cwd = copied / selected.cwd
                if not cwd.is_dir() or not cwd.resolve().is_relative_to(copied):
                    raise ValueError("Harness cwd 不存在或超出快照")
                runtime = self.config.runtime
                image_id = None
                if runtime != "host":
                    try:
                        image_id = self._docker_image(deadline)
                    except (OSError, SandboxError, subprocess.SubprocessError) as error:
                        self._check(deadline)
                        if runtime == "docker" or not self.config.allow_host_fallback:
                            raise ValueError("Docker 或配置镜像不可用，未执行检查") from error
                        values["fallback_reason"] = "Docker 或本地镜像不可用，按授权降级到宿主"
                        runtime = "host"
                    else:
                        runtime = "docker"
                values.update(
                    runtime=runtime,
                    image_id=image_id,
                    isolated=runtime == "docker",
                    evidence_level="sandbox" if runtime == "docker" else "local",
                )
                if runtime == "docker":
                    executed = self._docker_run(copied, selected, image_id, deadline)
                else:
                    command = list(selected.argv)
                    if command[0] in ("python", "python3", "python.exe"):
                        command[0] = sys.executable
                    executed = run_process(
                        command,
                        cwd=cwd,
                        timeout=self._check(deadline),
                        max_output=self.config.max_output_bytes,
                        control=self.control,
                        env=self._host_environment(copied),
                    )
                values.update(
                    exit_code=executed.exit_code,
                    output=executed.output,
                    truncated=executed.truncated,
                    timed_out=executed.timed_out,
                )
                values["status"] = (
                    "timeout"
                    if executed.timed_out
                    else "passed"
                    if executed.exit_code == 0
                    else "failed"
                )
                values["detail"] = "仅表示登记检查的退出结果；不等同于隐藏验收。"
        except KeyboardInterrupt:
            if self.control:
                self.control.stop("cancelled")
            values.update(status="cancelled", cancelled=True, detail="用户中断 Harness 检查")
        except ProcessInterrupted as error:
            if self.control and error.reason == "cancelled":
                self.control.stop("cancelled")
            values.update(
                status="cancelled",
                cancelled=True,
                detail=error.reason,
                output=error.result.output,
                truncated=error.result.truncated,
            )
        except ExecutionStopped as error:
            values.update(status="cancelled", cancelled=True, detail=error.reason)
        except TimeoutError:
            values.update(status="timeout", timed_out=True, detail="Harness 总时限已耗尽")
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            values.update(status="infra_error", detail=str(error)[:1000])
        values.update(
            cleanup_ok=self._cleanup_ok, duration_seconds=round(time.monotonic() - started, 3)
        )
        if not self._cleanup_ok:
            values.update(status="infra_error", detail="Docker 容器清理失败，请检查本次产物")
        result = HarnessResult(**values)
        (artifact / "output.log").write_text(result.output, encoding="utf-8")
        (artifact / "profile.json").write_text(
            json.dumps(asdict(self.config), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary = artifact / "result.json.tmp"
        temporary.write_text(
            json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temporary, artifact / "result.json")
        self.on_event("harness_finished", asdict(result))
        return asdict(result)

    @staticmethod
    def verify_benchmark(task, patch, runs_root, runner, *, time_budget=None):
        """内部基准适配入口；不在聊天工具中暴露维护者材料。"""
        from patchpilot.evaluation.catalog import acceptance_directory
        from patchpilot.patching import apply_patch, export_diff
        from patchpilot.snapshot import compute_snapshot_hash
        from patchpilot.task import to_agent_input
        from patchpilot.verification import VerificationResult
        from patchpilot.workspace import create_workspace

        started = time.monotonic()
        workspace = create_workspace(task, runs_root)
        report_path = workspace.artifacts / "verification.json"
        (workspace.artifacts / "candidate.patch").write_bytes(patch)
        (workspace.artifacts / "agent-input.json").write_text(
            to_agent_input(task).model_dump_json(indent=2), encoding="utf-8"
        )
        candidate_hash = acceptance_hash = None
        tests = None
        try:
            acceptance = acceptance_directory(task.spec.task_id)
            acceptance_hash = compute_snapshot_hash(acceptance)
            applied = apply_patch(workspace, patch, task.spec.snapshot_sha256)
            candidate_hash = applied.after_sha256
            (workspace.artifacts / "diff.patch").write_bytes(export_diff(task, workspace))
            remaining = (
                max(0, time_budget - (time.monotonic() - started))
                if time_budget is not None
                else None
            )
            tests = runner.run(workspace, acceptance=acceptance, time_budget=remaining)
            status = tests.status
            detail = tests.detail
            if compute_snapshot_hash(workspace.repo) != candidate_hash:
                status, detail = "infra_error", "验收期间候选工作区发生了改变"
            if compute_snapshot_hash(acceptance) != acceptance_hash:
                status, detail = "infra_error", "验收用例在执行期间发生了改变"
        except ValueError as error:
            status, detail = "rejected", str(error)
        result = VerificationResult(
            status=status,
            task_id=task.spec.task_id,
            patch_sha256=hashlib.sha256(patch).hexdigest(),
            base_sha256=task.spec.snapshot_sha256,
            candidate_sha256=candidate_hash,
            acceptance_sha256=acceptance_hash,
            workspace_path=str(workspace.root),
            report_path=str(report_path),
            tests=tests,
            detail=detail,
        )
        report_path.write_text(
            json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return result

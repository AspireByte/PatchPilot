"""一次项目检查的执行核心。

本模块只处理快照、执行后端和证据产物，不知道聊天上下文、Goal Loop 或固定任务。
``HarnessService`` 目前保留为配置与兼容门面；新的调用方可以直接使用
``VerificationRunner``，以后增加 MCP 或其他协议时也只需适配这个进程内接口。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from patchpilot.core.control import ExecutionControl, ExecutionStopped
from patchpilot.infrastructure.process.runner import ProcessInterrupted, ProcessResult, run_process


class VerificationBackendError(RuntimeError):
    """执行后端不可用或返回了不可信的基础设施结果。"""


@dataclass(frozen=True)
class Snapshot:
    sha256: str
    records: list[list[object]]
    path: Path


class Snapshotter:
    """把仓库复制到临时目录，并为实际复制内容生成稳定摘要。"""

    def __init__(
        self,
        repo: Path,
        artifacts_root: Path,
        *,
        max_files: int = 20_000,
        max_bytes: int = 100 * 1024 * 1024,
    ) -> None:
        if repo.is_symlink() or repo.is_junction() or not repo.is_dir():
            raise ValueError("验证仓库必须是存在的普通目录")
        self.repo = repo.resolve()
        self.artifacts_root = artifacts_root.resolve()
        self.max_files = max_files
        self.max_bytes = max_bytes

    @staticmethod
    def _excluded(path: Path) -> bool:
        skip = {
            "node_modules",
            "__pycache__",
            "venv",
            "env",
            "dist",
            "build",
            "target",
            "_acceptance",
            "benchmarks/acceptance",
        }
        private = {"credentials", "credentials.json", "id_rsa", "id_ed25519"}
        normalized = path.as_posix().lower()
        return any(
            (part.startswith(".") and part != ".github")
            or part in skip
            or part in private
            or part.endswith((".pem", ".key", ".p12", ".pfx", ".egg-info", ".pyc"))
            for part in (item.lower() for item in path.parts)
        ) or normalized.startswith(("benchmarks/solutions/", "benchmarks/acceptance/"))

    def _files(self):
        for parent, directories, files in os.walk(self.repo, followlinks=False):
            for name in list(directories):
                path = Path(parent) / name
                relative = path.relative_to(self.repo)
                if self._excluded(relative) or path.resolve().is_relative_to(self.artifacts_root):
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

    def copy(
        self, destination: Path, deadline: float, control: ExecutionControl | None
    ) -> Snapshot:
        records: list[list[object]] = []
        size = 0
        destination.mkdir(parents=True, exist_ok=True)

        def check() -> None:
            if control:
                control.check()
            if time.monotonic() >= deadline:
                raise TimeoutError("Harness 总时限已耗尽")

        for relative, source_path in self._files():
            check()
            if len(records) >= self.max_files:
                raise ValueError(f"Harness 快照超过 {self.max_files} 个文件")
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            mode = source_path.stat().st_mode & 0o777
            with source_path.open("rb") as source, target.open("xb") as output:
                while chunk := source.read(65_536):
                    check()
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise ValueError("Harness 快照超过大小上限")
                    digest.update(chunk)
                    output.write(chunk)
            shutil.copymode(source_path, target)
            records.append([relative.as_posix(), digest.hexdigest(), mode])
        records.sort()
        value = hashlib.sha256(
            json.dumps(records, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return Snapshot(value, records, destination)


class HostBackend:
    """宿主执行后端；提供生命周期控制，不提供权限隔离。"""

    @staticmethod
    def environment(snapshot: Path) -> dict[str, str]:
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
                "PYTHONPATH": str(snapshot / "src") + os.pathsep + str(snapshot),
            }
        )
        return env

    @staticmethod
    def run(
        argv: list[str],
        *,
        cwd: Path,
        snapshot: Path,
        timeout: float,
        max_output: int,
        control: ExecutionControl | None,
        process_fn: Callable[..., ProcessResult] = run_process,
    ) -> ProcessResult:
        command = list(argv)
        if command[0].lower() in {"python", "python3", "python.exe"}:
            command[0] = sys.executable
        return process_fn(
            command,
            cwd=cwd,
            timeout=timeout,
            max_output=max_output,
            control=control,
            env=HostBackend.environment(snapshot),
        )


class DockerBackend:
    """通用 Docker 后端，集中资源限制、无网络、非 root 和容器清理。"""

    def __init__(
        self,
        image: str | None,
        *,
        process_fn: Callable[..., ProcessResult] = run_process,
        cleanup_fn: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ) -> None:
        self.image = image
        self.process_fn = process_fn
        self.cleanup_fn = cleanup_fn

    def check(self, deadline: float, control: ExecutionControl | None) -> str:
        if not self.image:
            raise VerificationBackendError("未配置 Docker 镜像")
        if control:
            control.check()
        remaining = min(10, deadline - time.monotonic())
        if remaining <= 0:
            raise TimeoutError("Harness 总时限已耗尽")
        info = subprocess.run(
            ["docker", "info", "--format", "{{.OSType}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=remaining,
            check=False,
        )
        if info.returncode != 0 or info.stdout.strip() != "linux":
            raise VerificationBackendError("需要可用的 Docker Linux 容器引擎")
        inspect_timeout = min(10, deadline - time.monotonic())
        if inspect_timeout <= 0:
            raise TimeoutError("Harness 总时限已耗尽")
        inspect = subprocess.run(
            ["docker", "image", "inspect", self.image, "--format", "{{.Id}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=inspect_timeout,
            check=False,
        )
        image_id = inspect.stdout.strip()
        if inspect.returncode != 0 or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id):
            raise VerificationBackendError("本地 Docker 镜像不存在或无法确定不可变 ID")
        return image_id

    def run(
        self,
        snapshot: Path,
        profile,
        image_id: str,
        *,
        timeout: float,
        max_output: int,
        control: ExecutionControl | None,
    ) -> ProcessResult:
        name = "patchpilot-harness-" + uuid.uuid4().hex
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
            "--memory-swap",
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
            f"type=bind,source={snapshot},target=/workspace",
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
            return self.process_fn(
                command,
                cwd=snapshot,
                timeout=timeout,
                max_output=max_output,
                control=control,
            )
        finally:
            try:
                cleanup = self.cleanup_fn(
                    ["docker", "rm", "--force", "--volumes", name],
                    capture_output=True,
                    timeout=10,
                    check=False,
                )
                stderr = cleanup.stderr or b""
                missing = (
                    "No such container" in stderr
                    if isinstance(stderr, str)
                    else b"No such container" in stderr
                )
                cleanup_ok = cleanup.returncode == 0 or missing
            except (OSError, subprocess.SubprocessError):
                cleanup_ok = False
            self.cleanup_ok = cleanup_ok


class ArtifactWriter:
    """保存检查输入、输出和最终结果，避免工具结果依赖内存状态。"""

    @staticmethod
    def create(root: Path) -> Path:
        artifact = root / ("harness-" + uuid.uuid4().hex)
        artifact.mkdir(parents=True, exist_ok=False)
        return artifact

    @staticmethod
    def write(artifact: Path, config, snapshot: Snapshot | None, result: dict) -> None:
        if snapshot is not None:
            (artifact / "manifest.json").write_text(
                json.dumps(snapshot.records, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        (artifact / "profile.json").write_text(
            json.dumps(asdict(config), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (artifact / "output.log").write_text(result.get("output", ""), encoding="utf-8")
        temporary = artifact / "result.json.tmp"
        temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, artifact / "result.json")


class VerificationRunner:
    """执行一次已登记 profile，返回可审计的结构化结果。"""

    def __init__(
        self,
        repo: Path,
        config,
        *,
        artifacts_root: Path,
        control: ExecutionControl | None = None,
        on_event: Callable[[str, dict], None] | None = None,
        process_fn: Callable[..., ProcessResult] = run_process,
        docker_image_fn: Callable[[float], str] | None = None,
        snapshot_fn: Callable[[Path, float], Snapshot] | None = None,
        docker_run_fn: Callable[..., ProcessResult] | None = None,
        docker_cleanup_ok_fn: Callable[[], bool] | None = None,
        cleanup_fn: Callable[..., subprocess.CompletedProcess] = subprocess.run,
    ) -> None:
        if repo.is_symlink() or repo.is_junction() or not repo.is_dir():
            raise ValueError("验证仓库必须是存在的普通目录")
        self.repo = repo.resolve()
        self.config = config
        self.artifacts_root = artifacts_root.resolve()
        self.control = control
        self.on_event = on_event or (lambda _kind, _values: None)
        self.process_fn = process_fn
        self.snapshotter = Snapshotter(self.repo, self.artifacts_root)
        self.snapshot_fn = snapshot_fn
        self.docker = DockerBackend(
            config.image,
            process_fn=process_fn,
            cleanup_fn=cleanup_fn,
        )
        self.docker_image_fn = docker_image_fn
        self.docker_run_fn = docker_run_fn
        self.docker_cleanup_ok_fn = docker_cleanup_ok_fn

    def _check(self, deadline: float) -> float:
        if self.control:
            self.control.check()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Harness 总时限已耗尽")
        return remaining

    def _snapshot(self, destination: Path, deadline: float) -> Snapshot:
        if self.snapshot_fn:
            return self.snapshot_fn(destination, deadline)
        return self.snapshotter.copy(destination, deadline, self.control)

    def run(self, profile: str | None = None, *, remaining: float | None = None) -> dict:
        name = profile or self.config.default_profile
        started = time.monotonic()
        duration = (
            min(self.config.timeout_seconds, remaining)
            if remaining is not None
            else self.config.timeout_seconds
        )
        deadline = started + duration
        artifact = ArtifactWriter.create(self.artifacts_root)
        selected = self.config.profiles.get(name)
        effective_runtime = (
            getattr(self.config, "runtime_override", None)
            or getattr(selected, "runtime", None)
            or self.config.runtime
        )
        values = {
            "status": "infra_error",
            "profile": name,
            "runtime": effective_runtime,
            "evidence_level": "none",
            "isolated": False,
            "exit_code": None,
            "timed_out": False,
            "cancelled": False,
            "truncated": False,
            "output": "",
            "snapshot_sha256": "",
            "duration_seconds": 0,
            "artifact_path": str(artifact),
            "detail": "",
            "image_id": None,
            "cleanup_ok": True,
            "argv": (),
            "cwd": ".",
            "fallback_reason": "",
        }
        self.on_event("harness_started", {"profile": name})
        snapshot = None
        try:
            self._check(deadline)
            if selected is None:
                raise ValueError("未找到 Harness profile；请配置 .patchpilot/harness.toml")
            values.update(argv=selected.argv, cwd=selected.cwd)
            with tempfile.TemporaryDirectory(prefix="snapshot-", dir=artifact) as temporary:
                copied = Path(temporary) / "repo"
                snapshot = self._snapshot(copied, deadline)
                values["snapshot_sha256"] = snapshot.sha256
                cwd = copied / selected.cwd
                if not cwd.is_dir() or not cwd.resolve().is_relative_to(copied):
                    raise ValueError("Harness cwd 不存在或超出快照")
                runtime = (
                    getattr(self.config, "runtime_override", None)
                    or getattr(selected, "runtime", None)
                    or self.config.runtime
                )
                image_id = None
                if runtime != "host":
                    try:
                        image_id = (
                            self.docker_image_fn(self._check(deadline))
                            if self.docker_image_fn
                            else self.docker.check(deadline, self.control)
                        )
                    except (
                        OSError,
                        VerificationBackendError,
                        subprocess.SubprocessError,
                    ) as error:
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
                    if self.docker_run_fn:
                        executed = self.docker_run_fn(copied, selected, image_id, deadline)
                    else:
                        executed = self.docker.run(
                            copied,
                            selected,
                            image_id,
                            timeout=self._check(deadline),
                            max_output=self.config.max_output_bytes,
                            control=self.control,
                        )
                    values["cleanup_ok"] = (
                        self.docker_cleanup_ok_fn()
                        if self.docker_cleanup_ok_fn
                        else getattr(self.docker, "cleanup_ok", True)
                    )
                else:
                    executed = HostBackend.run(
                        list(selected.argv),
                        cwd=cwd,
                        snapshot=copied,
                        timeout=self._check(deadline),
                        max_output=self.config.max_output_bytes,
                        control=self.control,
                        process_fn=self.process_fn,
                    )
                values.update(
                    exit_code=executed.exit_code,
                    output=executed.output,
                    truncated=executed.truncated,
                    timed_out=executed.timed_out,
                    status=(
                        "timeout"
                        if executed.timed_out
                        else "passed"
                        if executed.exit_code == 0
                        else "failed"
                    ),
                    detail="仅表示登记检查的退出结果；不等同于隐藏验收。",
                )
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
        except (
            OSError,
            ValueError,
            VerificationBackendError,
            subprocess.SubprocessError,
        ) as error:
            values.update(status="infra_error", detail=str(error)[:1000])
        values["duration_seconds"] = round(time.monotonic() - started, 3)
        if not values["cleanup_ok"]:
            values.update(status="infra_error", detail="Docker 容器清理失败，请检查本次产物")
        ArtifactWriter.write(artifact, self.config, snapshot, values)
        self.on_event("harness_finished", values)
        return values

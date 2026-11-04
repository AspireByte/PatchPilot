"""Harness 配置与旧固定任务兼容门面；执行核心位于 verification.runner。"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from patchpilot.core.control import ExecutionControl
from patchpilot.infrastructure.process.runner import run_process
from patchpilot.infrastructure.verification.config import HarnessConfig
from patchpilot.infrastructure.verification.runner import (
    DockerBackend,
    Snapshot,
    Snapshotter,
    VerificationRunner,
)


@dataclass(frozen=True)
class HarnessResult:
    """一次检查的结构化证据，同时记录执行结果、输入版本和环境边界。

    status 表示通过、失败、超时、取消或基础设施异常；exit_code 是实际子进程的退出码。
    snapshot_sha256 绑定实际复制的内容，artifact_path 指向 manifest、配置、输出与结果。
    isolated/evidence_level 区分 Docker 隔离证据与宿主本地证据，cleanup_ok 说明容器清理情况。
    passed 只表示登记命令正常返回零，不能据此推断未运行的检查或隐藏验收也通过。
    """

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
    """兼容旧名称的配置门面，并保留固定任务的基准适配入口。

    run 复制当前仓库、选择环境、执行指定 profile，并把可核对的产物保存到会话目录。
    服务不修改用户仓库中的普通相对文件，也不调用模型生成或修复代码。
    host 仅通过副本隔开正常文件写入，项目脚本仍有当前用户权限；docker 才提供容器限制。
    """

    def __init__(
        self,
        repo: Path,
        config: HarnessConfig | None = None,
        *,
        artifacts_root: Path | None = None,
        control: ExecutionControl | None = None,
        on_event: Callable[[str, dict], None] | None = None,
    ):
        """绑定目标目录和已加载配置，准备产物位置；检查本身在 run 时才执行。

        config 未传时从仓库加载；artifacts_root 指定证据目录，默认在仓库的运行产物目录。
        control 接收会话取消信号，on_event 将检查开始和结束事件送回会话及终端。
        """
        if repo.is_symlink() or repo.is_junction() or not repo.is_dir():
            raise ValueError("Harness 仓库必须是存在的普通目录")
        self.repo = repo.resolve()
        self.config = config or HarnessConfig.load(self.repo)
        self.artifacts_root = (artifacts_root or self.repo / ".patchpilot-runs").resolve()
        self.control = control
        self.on_event = on_event or (lambda _kind, _values: None)

    def _check(self, deadline):
        if self.control:
            self.control.check()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Harness 总时限已耗尽")
        return remaining

    def _copy_snapshot(self, destination: Path, deadline: float):
        """兼容旧调用方的快照接口，实际实现由通用 Snapshotter 提供。"""
        snapshot = Snapshotter(self.repo, self.artifacts_root).copy(
            destination, deadline, self.control
        )
        return snapshot.sha256, snapshot.records

    def _docker_image(self, deadline):
        """兼容旧测试和基准调用方的镜像预检入口。"""
        return DockerBackend(self.config.image).check(deadline, self.control)

    def _docker_run(self, copied, profile, image_id, deadline):
        """兼容旧接口，实际 Docker 参数与清理由通用 DockerBackend 提供。"""
        backend = DockerBackend(
            self.config.image,
            process_fn=run_process,
            cleanup_fn=subprocess.run,
        )
        result = backend.run(
            copied,
            profile,
            image_id,
            timeout=self._check(deadline),
            max_output=self.config.max_output_bytes,
            control=self.control,
        )
        self._cleanup_ok = getattr(backend, "cleanup_ok", True)
        return result

    def run(self, profile: str | None = None, *, remaining: float | None = None):
        """执行一次通用检查；HarnessService 只保留配置与旧 API 门面。"""
        runner = VerificationRunner(
            self.repo,
            self.config,
            artifacts_root=self.artifacts_root,
            control=self.control,
            on_event=self.on_event,
            process_fn=run_process,
            docker_image_fn=self._docker_image,
            snapshot_fn=lambda destination, deadline: Snapshot(
                *self._copy_snapshot(destination, deadline), destination
            ),
            docker_run_fn=self._docker_run,
            docker_cleanup_ok_fn=lambda: getattr(self, "_cleanup_ok", True),
            cleanup_fn=subprocess.run,
        )
        result = runner.run(profile, remaining=remaining)
        self._cleanup_ok = result.get("cleanup_ok", True)
        return result

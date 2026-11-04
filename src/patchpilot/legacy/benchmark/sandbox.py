"""仅在预构建 Linux 镜像内执行固定 pytest 配置，不提供任意命令入口。"""

# DockerRunner.run 是旧固定任务的 pytest 后端；通用 Harness 仅复用其 check 做镜像预检，
# 再由 harness.py 自己组装登记 profile 的执行命令，两种 Docker 路径不能混为一条调用链。

import io
import json
import math
import re
import subprocess
import tarfile
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path

from patchpilot.core.control import ExecutionControl, ExecutionStopped
from patchpilot.legacy.benchmark.snapshot import compute_snapshot_hash
from patchpilot.legacy.benchmark.workspace import Workspace

DEFAULT_IMAGE = "patchpilot-tests:py312-v1"
MAX_REPORT_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class SandboxLimits:
    timeout_seconds: float = 30

    def __post_init__(self) -> None:
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("测试超时必须是有限的正数")


@dataclass(frozen=True)
class TestOutcome:
    status: str
    passed: int | None = None
    failed: int | None = None
    errors: int | None = None
    skipped: int | None = None


def classify_pytest(exit_code: int, report: bytes | None) -> TestOutcome:
    """联合退出码与实际 testcase 分类，缺少证据时不能声明通过。"""
    # 与通用 Harness 的零退出码判定不同，此处还要求有效 JUnit 用例证据；全跳过不算通过。
    fallback = {2: "test_error", 4: "test_error", 5: "no_tests"}.get(exit_code, "infra_error")
    if report is None:
        return TestOutcome(fallback)
    if (
        len(report) > MAX_REPORT_BYTES
        or b"<!DOCTYPE" in report.upper()
        or b"<!ENTITY" in report.upper()
    ):
        return TestOutcome("infra_error")
    try:
        root = ET.fromstring(report)
    except ET.ParseError:
        return TestOutcome("infra_error")
    if root.tag not in ("testsuite", "testsuites"):
        return TestOutcome("infra_error")
    passed = failed = errors = skipped = 0
    for case in root.iter("testcase"):
        if case.find("error") is not None:
            errors += 1
        elif case.find("failure") is not None:
            failed += 1
        elif case.find("skipped") is not None:
            skipped += 1
        else:
            passed += 1
    if exit_code in (2, 4) or errors:
        status = "test_error"
    elif exit_code == 5 and not (passed or failed):
        status = "no_tests"
    elif exit_code == 1 and failed:
        status = "failed"
    elif exit_code == 0 and not (failed or errors):
        status = "passed" if passed else "no_tests"
    else:
        status = "infra_error"
    return TestOutcome(status, passed, failed, errors, skipped)


def create_command(
    image_id: str,
    name: str,
    limits: SandboxLimits,
    acceptance: bool,
    targets: tuple[str, ...] | None = None,
    attempt_id: str | None = None,
) -> list[str]:
    """所有资源与权限参数均来自程序，不接受任务文件拼接命令。"""
    selected = targets or ("tests", "repro")
    for target in selected:
        if (
            target not in ("tests", "repro", "regression")
            and re.fullmatch(
                r"(?:tests|repro|regression)/(?:[A-Za-z0-9_]+/)*[A-Za-z0-9_]+\.py", target
            )
            is None
        ):
            raise ValueError("不支持的测试目标")
    targets = [*selected, *(["/acceptance"] if acceptance else [])]
    labels = ["--label", f"patchpilot.attempt={uuid.UUID(attempt_id)}"] if attempt_id else []
    return [
        "create",
        *labels,
        "--name",
        name,
        "--label",
        "patchpilot.managed=true",
        "--pull",
        "never",
        "--network",
        "none",
        "--user",
        "10001:10001",
        "--cpus",
        "1.0",
        "--memory",
        "256m",
        "--memory-swap",
        "256m",
        "--pids-limit",
        "64",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges:true",
        "--init",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m,mode=1777",
        "--ulimit",
        "fsize=16777216:16777216",
        "--ulimit",
        "nofile=256:256",
        "--log-driver",
        "local",
        "--log-opt",
        "max-size=1m",
        "--log-opt",
        "max-file=1",
        "--log-opt",
        "compress=false",
        "--workdir",
        "/workspace",
        "--entrypoint",
        "python",
        image_id,
        "-B",
        "-m",
        "pytest",
        "-c",
        "pytest.ini",
        "-o",
        "addopts=",
        *targets,
        "-q",
        "-p",
        "no:cacheprovider",
        "--junitxml=/results/report.xml",
    ]


@dataclass(frozen=True)
class TestResult:
    """固定 pytest 容器执行的证据汇总，包含分类、用例计数、环境与产物路径。

    exit_code 和用例计数可能为空，表示未得到相应执行证据；不能把空值当成零失败。
    cleanup_ok 表示本次容器是否成功回收，清理失败会使最终状态转为基础设施异常。
    """

    status: str
    exit_code: int | None
    passed: int | None
    failed: int | None
    errors: int | None
    skipped: int | None
    duration_seconds: float
    image_id: str | None
    container_id: str | None
    log_path: str
    report_path: str | None
    cleanup_ok: bool
    detail: str


class SandboxError(RuntimeError):
    """Docker 引擎、镜像或产物读取异常。"""


def _docker(arguments: list[str], *, timeout: float = 20, binary: bool = False):
    return subprocess.run(
        ["docker", *arguments],
        capture_output=True,
        timeout=timeout,
        **({} if binary else {"text": True, "encoding": "utf-8", "errors": "replace"}),
    )


def _checked(arguments: list[str], *, timeout: float = 20) -> str:
    result = _docker(arguments, timeout=timeout)
    if result.returncode != 0:
        raise SandboxError(f"Docker 操作失败：{result.stderr[-2000:].strip()}")
    return result.stdout.strip()


def _read_report(container: str, *, timeout: float = 20) -> bytes | None:
    result = _docker(["cp", f"{container}:/results/report.xml", "-"], binary=True, timeout=timeout)
    if result.returncode != 0:
        return None
    if len(result.stdout) > MAX_REPORT_BYTES + 65536:
        raise SandboxError("测试报告超过大小限制")
    try:
        with tarfile.open(fileobj=io.BytesIO(result.stdout), mode="r:*") as archive:
            members = archive.getmembers()
            if len(members) != 1 or not members[0].isfile() or members[0].size > MAX_REPORT_BYTES:
                raise SandboxError("测试报告必须是有界的普通文件")
            stream = archive.extractfile(members[0])
            if stream is None:
                raise SandboxError("无法读取测试报告")
            return stream.read(MAX_REPORT_BYTES + 1)
    except tarfile.TarError as error:
        raise SandboxError("Docker 返回的测试报告归档无效") from error


def _wait_with_control(container_id: str, seconds: float, control: ExecutionControl | None) -> int:
    if control is None:
        return int(_checked(["wait", container_id], timeout=seconds))
    deadline = time.monotonic() + seconds
    with subprocess.Popen(
        ["docker", "wait", container_id], stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ) as process:
        try:
            while True:
                control.check()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired("docker wait", seconds)
                try:
                    stdout, stderr = process.communicate(timeout=min(0.1, remaining))
                except subprocess.TimeoutExpired:
                    continue
                if process.returncode != 0:
                    raise SandboxError("无法等待测试容器结束")
                return int(stdout.strip())
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5)


def cleanup_attempt_containers(attempt_id: str) -> None:
    identifier = str(uuid.UUID(attempt_id))
    result = _checked(
        [
            "ps",
            "-aq",
            "--filter",
            "label=patchpilot.managed=true",
            "--filter",
            f"label=patchpilot.attempt={identifier}",
        ]
    )
    ids = result.split()
    if any(re.fullmatch(r"[0-9a-f]{12,64}", item) is None for item in ids):
        raise SandboxError("Docker返回的容器编号不合法")
    if ids:
        _checked(["rm", "--force", "--volumes", *ids])


class DockerRunner:
    """旧固定任务的容器 pytest 执行器，负责环境预检、代码复制、报告读取和容器清理。

    check 确认 Linux 引擎与本地镜像并锁定镜像 ID，run 再创建受资源和权限限制的容器。
    测试目标只能来自程序允许的公开路径；最终验收可由内部调用方额外提供 acceptance 目录。
    本类不调用模型、不安装仓库依赖，测试是否通过需结合退出码和实际 JUnit 用例判断。
    """

    def __init__(
        self,
        *,
        image: str = DEFAULT_IMAGE,
        limits: SandboxLimits | None = None,
        control: ExecutionControl | None = None,
        attempt_id: str | None = None,
    ) -> None:
        self.image = image
        self.limits = limits or SandboxLimits()
        self.control = control
        self.attempt_id = attempt_id

    def check(self, *, time_budget: float = 20) -> dict[str, str]:
        # 只检查本地 Linux 引擎和预构建镜像，并返回不可变镜像 ID；不下载或构建镜像。
        deadline = time.monotonic() + time_budget

        def remaining() -> float:
            value = deadline - time.monotonic()
            if value <= 0:
                raise subprocess.TimeoutExpired("docker check", time_budget)
            return min(10, value)

        if _checked(["info", "--format", "{{.OSType}}"], timeout=remaining()) != "linux":
            raise SandboxError("需要可用的 Docker Linux 容器引擎")
        result = _docker(
            ["image", "inspect", self.image, "--format", "{{.Id}}"], timeout=remaining()
        )
        if result.returncode != 0:
            raise SandboxError("本地测试镜像不存在，请先构建 sandbox/Dockerfile")
        image_id = result.stdout.strip()
        if not image_id.startswith("sha256:") or len(image_id) != 71:
            raise SandboxError("无法确定测试镜像的不可变 ID")
        return {"engine": "linux", "image": self.image, "image_id": image_id}

    def run(
        self,
        workspace: Workspace,
        *,
        acceptance: Path | None = None,
        targets: tuple[str, ...] | None = None,
        time_budget: float | None = None,
    ) -> TestResult:
        """将候选工作区复制进新容器运行测试，保存日志/JUnit/配置并返回 TestResult。

        workspace 指定候选与产物目录，targets 选择允许的测试路径，acceptance 是可选维护者用例。
        time_budget 限制整段执行阶段，测试等待还受 SandboxLimits 的单次时限约束。
        超时会停止容器；取消、引擎故障或报告问题分别分类，finally 负责清理本次容器。
        不挂载原工作区，容器内普通文件写入不会回写候选仓库。
        """
        started = time.monotonic()
        deadline = started + time_budget if time_budget is not None else None

        def available(maximum: float = 20) -> float:
            if self.control is not None:
                self.control.check()
            if deadline is None:
                return maximum
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired("sandbox deadline", time_budget)
            return min(maximum, remaining)

        artifacts = workspace.artifacts / f"test-{uuid.uuid4().hex}"
        artifacts.mkdir()
        log_path = artifacts / "container.log"
        log_path.write_text("", encoding="utf-8")
        name = f"patchpilot-{uuid.uuid4().hex}"
        container_id = image_id = report_path = None
        create_attempted = False
        cleanup_ok = True
        exit_code = None
        outcome = TestOutcome("infra_error")
        detail = ""
        try:
            compute_snapshot_hash(workspace.repo)
            if acceptance is not None:
                compute_snapshot_hash(acceptance)
            image_id = self.check(time_budget=available())["image_id"]
            create_attempted = True
            container_id = _checked(
                create_command(
                    image_id, name, self.limits, acceptance is not None, targets, self.attempt_id
                ),
                timeout=available(),
            )
            # docker cp 把候选代码复制进容器，不把原工作区可写挂载进去，测试写入留在容器中。
            _checked(
                ["cp", f"{workspace.repo}/.", f"{container_id}:/workspace"], timeout=available()
            )
            if acceptance is not None:
                _checked(
                    ["cp", f"{acceptance}/.", f"{container_id}:/acceptance"], timeout=available()
                )
            configuration = _checked(["inspect", container_id], timeout=available())
            (artifacts / "container.json").write_text(
                json.dumps(json.loads(configuration)[0], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            _checked(["start", container_id], timeout=available())
            try:
                wait_seconds = available(self.limits.timeout_seconds)
                exit_code = _wait_with_control(container_id, wait_seconds, self.control)
            except subprocess.TimeoutExpired:
                _checked(["kill", container_id])
                outcome = TestOutcome("timeout")
                detail = "测试超过时限，已终止整个容器"
            logs = _docker(
                ["logs", "--tail", "1000", container_id],
                timeout=available() if outcome.status != "timeout" else 5,
            )
            if logs.returncode != 0:
                raise SandboxError("无法读取容器日志")
            log_path.write_text(logs.stdout + logs.stderr, encoding="utf-8")
            if outcome.status != "timeout":
                report = _read_report(container_id, timeout=available())
                if report is not None:
                    report_path = str(artifacts / "report.xml")
                    Path(report_path).write_bytes(report)
                outcome = classify_pytest(exit_code, report)
                detail = "已联合退出码与 JUnit 测试记录分类"
        except ExecutionStopped as error:
            outcome = TestOutcome(error.reason)
            detail = "执行已停止，将强制清理本次容器"
        except subprocess.TimeoutExpired:
            outcome = TestOutcome("timeout")
            detail = "执行阶段总时间预算耗尽，停止后续动作并清理容器"
        except (OSError, ValueError, SandboxError, subprocess.SubprocessError) as error:
            outcome = TestOutcome("infra_error")
            detail = str(error)
        finally:
            # 即使 create 已尝试但没拿到容器 ID，也按本次唯一名称清理，避免超时留下运行实例。
            if create_attempted:
                try:
                    _checked(["rm", "--force", "--volumes", name])
                except (OSError, SandboxError, subprocess.SubprocessError) as error:
                    cleanup_ok = False
                    outcome = TestOutcome("infra_error")
                    detail += f"；容器清理失败，需要人工检查 {name}：{error}"
        result = TestResult(
            status=outcome.status,
            exit_code=exit_code,
            passed=outcome.passed,
            failed=outcome.failed,
            errors=outcome.errors,
            skipped=outcome.skipped,
            duration_seconds=round(time.monotonic() - started, 3),
            image_id=image_id,
            container_id=container_id,
            log_path=str(log_path),
            report_path=report_path,
            cleanup_ok=cleanup_ok,
            detail=detail,
        )
        (artifacts / "result.json").write_text(
            json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return result

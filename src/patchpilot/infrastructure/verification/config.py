"""Harness 配置与旧固定任务兼容门面；执行核心位于 verification_runner。"""

# 延迟处理本模块的类型注解，便于声明彼此引用的类型；注解本身不代替配置校验。
from __future__ import annotations

import math
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

DEFAULT_CONFIG_NAME = ".patchpilot/harness.toml"
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
    # profile 保存实际程序与参数数组，拒绝 shell 入口和内联脚本，缩小模型可选择的执行范围。
    # 已登记的项目脚本仍是可执行代码，需要信任；参数检查并不能让宿主执行变成沙箱。
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
    """一项已登记的检查：name 是选择名称，argv 是程序参数数组，cwd 是副本内工作目录。

    配置加载时校验这些值，模型调用时只选择 name，从而让命令内容由启动配置控制。
    """

    name: str
    argv: tuple[str, ...]
    cwd: str = "."
    runtime: str | None = None


@dataclass(frozen=True)
class HarnessConfig:
    """启动时解析的检查配置，汇总 profile、环境策略、时限和输出上限。

    runtime 决定 host/docker/auto，默认 host；profile.runtime 可以选择单项检查的执行环境。
    allow_host_fallback 仅控制 auto 预检失败时的宿主降级。
    image 必须对应预先准备的本地镜像；配置不承担依赖安装或镜像下载工作。
    配置加载后由服务持续复用，修改磁盘 TOML 不会让正在运行的会话自动获得新命令。
    """

    version: int = 1
    runtime: str = "host"
    image: str | None = None
    default_profile: str = "test"
    timeout_seconds: float = 120
    max_output_bytes: int = 16000
    allow_host_fallback: bool = True
    # default_factory 每次构造都创建新字典，避免多个配置对象共享同一份可变默认值。
    # frozen 只禁止字段重新赋值，不会把这个字典递归冻结；调用方仍需遵守不修改配置的约定。
    profiles: dict[str, HarnessProfile] = field(default_factory=dict)
    path: str | None = None
    runtime_override: str | None = None

    @classmethod
    def load(cls, repo: Path, path: Path | None = None, *, runtime_override: str | None = None):
        """读取 TOML 并构造配置；格式、未知字段或命令约束不满足时抛出 ValueError。

        path 为空时查找仓库默认配置；默认文件缺失且检测到 Python 项目时，提供 pytest 检查。
        显式指定的文件必须存在；runtime_override 只覆盖执行环境，不改变已登记命令。
        返回对象已包含可用 profile，后续工具调用只需选择，无须重新解析磁盘配置。
        """
        # @classmethod 接收类 cls，通过 cls(...) 返回配置；启动时解析一次，工具只选择已登记名称。
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
        runtime = runtime_override if runtime_override is not None else raw.get("runtime", "host")
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
            if value.keys() - {"argv", "cwd", "runtime"}:
                raise ValueError("Harness profile 包含未知字段")
            directory = value.get("cwd", ".")
            if not isinstance(directory, str):
                raise ValueError("Harness cwd 必须为字符串")
            profile_runtime = value.get("runtime")
            if profile_runtime is not None and profile_runtime not in ("auto", "docker", "host"):
                raise ValueError("Harness profile runtime 必须是 auto、docker 或 host")
            profiles[name] = HarnessProfile(
                name, _argv(value.get("argv")), _cwd(directory), profile_runtime
            )
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
                profiles["test"] = HarnessProfile(
                    "test", ("python", "-m", "pytest", "-q"), runtime="host"
                )
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
            runtime_override,
        )

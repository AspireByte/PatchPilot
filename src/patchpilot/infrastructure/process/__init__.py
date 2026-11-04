"""进程执行适配器。"""

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from patchpilot.infrastructure.process.runner import ProcessResult, run_process


class ProcessRunner(Protocol):
    """可注入的宿主进程执行端口，测试可替换为 Fake。"""

    def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout: float,
        max_output: int,
        control=None,
        env: dict[str, str] | None = None,
    ) -> ProcessResult: ...


__all__ = ["ProcessResult", "ProcessRunner", "run_process"]

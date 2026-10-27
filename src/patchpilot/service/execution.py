"""Worker与执行器之间的小型领域契约。"""

from collections.abc import Callable
from dataclasses import dataclass

from patchpilot.control import ExecutionControl
from patchpilot.workspace import Workspace


@dataclass(frozen=True)
class ExecutionHooks:
    control: ExecutionControl
    event: Callable[[dict], None]
    boundary: Callable[[Workspace, str], None]


@dataclass(frozen=True)
class ExecutionOutput:
    execution_status: str
    verdict: str
    reason: str
    usage: dict
    patch: bytes
    report: dict
    logs: tuple[bytes, ...] = ()

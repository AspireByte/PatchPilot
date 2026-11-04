"""工具注册和执行所需的最小协议。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from patchpilot.core.types import ToolCall


@dataclass(frozen=True)
class ToolContext:
    remaining: float
    control: Any = None


class ToolProvider(Protocol):
    def definitions(self) -> list[dict]: ...

    def dispatch(self, name: str, arguments: Mapping[str, Any], *, remaining: float) -> dict: ...


class Tool(Protocol):
    name: str

    def execute(self, call: ToolCall, context: ToolContext) -> dict: ...

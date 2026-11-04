"""Core Runtime 使用的最小端口。

这些协议只描述运行时需要的行为，不引入 Provider SDK、工具实现或持久化类型。
组合入口可以依靠结构化类型注入 Fake Model、Fake Tool 和事件接收器。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Protocol

from .types import ModelRequest, ModelResponse, ToolCall


class ModelPort(Protocol):
    kind: str

    def complete(self, request: ModelRequest) -> ModelResponse: ...


class ToolPort(Protocol):
    def definitions(self) -> list[dict]: ...

    def execute(self, call: ToolCall, *, remaining: float) -> dict: ...


EventPort = Callable[[object], None]
MessageSequence = Sequence[dict]


__all__ = ["EventPort", "MessageSequence", "ModelPort", "ToolPort"]

"""Runtime 内部使用的、与 Provider SDK 无关的数据类型。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ModelUsage:
    """一次模型调用的已知 token 用量。"""

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return sum(
            (
                self.input_tokens,
                self.output_tokens,
                self.cache_read_input_tokens,
                self.cache_creation_input_tokens,
            )
        )


@dataclass(frozen=True)
class ModelRequest:
    """发送给模型适配器的请求，不暴露第三方 SDK 类型。"""

    system: str
    messages: Sequence[Mapping[str, Any]]
    tools: Sequence[Mapping[str, Any]]
    timeout_seconds: float
    max_output_tokens: int


@dataclass(frozen=True)
class ModelResponse:
    """模型适配器返回的最小响应。content 保持 JSON 兼容。"""

    content: tuple[Mapping[str, Any], ...]
    stop_reason: str | None
    usage: ModelUsage | None = None

    @property
    def text(self) -> str:
        return "\n".join(
            str(block.get("text", ""))
            for block in self.content
            if block.get("type") == "text" and block.get("text")
        )

    @property
    def tool_calls(self) -> tuple[ToolCall, ...]:
        return tuple(
            ToolCall(
                identifier=str(block["id"]),
                name=str(block["name"]),
                input=dict(block.get("input") or {}),
            )
            for block in self.content
            if block.get("type") == "tool_use"
            and block.get("id") is not None
            and block.get("name") is not None
        )


@dataclass(frozen=True)
class ToolCall:
    """模型提出的一次工具调用。"""

    identifier: str
    name: str
    input: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    """工具执行结果，供 Loop 转换为模型历史消息。"""

    identifier: str
    ok: bool
    content: Mapping[str, Any]

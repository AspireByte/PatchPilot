"""固定任务链路保留的旧模型调用协议。"""

from typing import Protocol

from anthropic.types import Message, MessageParam, ToolParam


class AgentModel(Protocol):
    kind: str

    def complete(
        self,
        *,
        system: str,
        messages: list[MessageParam],
        tools: list[ToolParam],
        timeout_seconds: float,
        max_output_tokens: int,
    ) -> Message: ...

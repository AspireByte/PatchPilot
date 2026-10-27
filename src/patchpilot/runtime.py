"""聊天与内部回归共用的模型协议、预算和故障类型。"""

import math
from dataclasses import dataclass
from typing import Protocol

from anthropic.types import Message, MessageParam, ToolParam


@dataclass(frozen=True)
class AgentBudget:
    max_model_calls: int = 8
    max_tool_calls: int = 32
    timeout_seconds: float = 180
    max_total_tokens: int = 100000
    max_context_chars: int = 120000
    max_repeated_actions: int = 3
    max_output_tokens: int = 4096

    def __post_init__(self) -> None:
        for value in (
            self.max_model_calls,
            self.max_tool_calls,
            self.max_total_tokens,
            self.max_context_chars,
            self.max_repeated_actions,
            self.max_output_tokens,
        ):
            if type(value) is not int or value <= 0:
                raise ValueError("调用、用量与上下文预算必须为正整数")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("总时限必须为有限正数")


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


class ModelFailure(RuntimeError):
    """只携带安全的错误分类，不把SDK凭据、请求头或原始异常交给模型。"""

    REASONS = frozenset(
        {
            "provider_error",
            "authentication_error",
            "permission_denied",
            "model_not_found",
            "bad_request",
            "model_timeout",
            "rate_limited",
            "model_connection_error",
            "invalid_json",
        }
    )

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)

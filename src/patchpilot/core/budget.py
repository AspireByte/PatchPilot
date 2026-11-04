"""Agent Runtime 的预算配置。"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class AgentBudget:
    """限制一次运行可使用的模型、工具、时间、上下文和输出资源。"""

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
                raise ValueError("模型、工具、用量和上下文预算必须为正整数")
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("总时限必须为有限正数")

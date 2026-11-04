"""项目检查能力的基础设施端口。"""

from __future__ import annotations

from typing import Protocol


class VerificationPort(Protocol):
    """供 Session 和工具调用的项目检查接口。"""

    def run(self, profile: str | None = None, *, remaining: float | None = None) -> dict: ...


__all__ = ["VerificationPort"]

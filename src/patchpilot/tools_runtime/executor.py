"""工具调用的统一执行边界。"""

from __future__ import annotations

from collections.abc import Mapping

from patchpilot.core.control import ExecutionStopped
from patchpilot.core.types import ToolCall

from .protocol import ToolContext, ToolProvider
from .registry import ToolNotFound, ToolRegistry


class ToolExecutor:
    """把模型工具调用转发给已授权的 Provider。"""

    def __init__(self, provider: ToolProvider, *, control=None):
        self.registry = (
            provider if isinstance(provider, ToolRegistry) else ToolRegistry.from_provider(provider)
        )
        self.control = control

    def definitions(self) -> list[dict]:
        return self.registry.definitions()

    def execute(self, call: ToolCall, *, remaining: float) -> dict:
        if not isinstance(call, ToolCall) or not call.identifier or not call.name:
            return {"ok": False, "error": "工具调用缺少有效编号或名称"}
        if not isinstance(call.input, Mapping):
            return {"ok": False, "error": "工具参数必须是对象"}
        if self.control is not None:
            self.control.check()
        try:
            return self.registry.execute(
                call,
                context=ToolContext(remaining=remaining, control=self.control),
            )
        except ExecutionStopped:
            raise
        except (ToolNotFound, ValueError, TypeError, OSError) as error:
            return {"ok": False, "error": str(error)[:1000]}
        except Exception as error:
            # 工具异常不能让模型循环泄露宿主堆栈；取消信号已在上面单独保留并继续传播。
            return {"ok": False, "error": f"工具执行失败：{error}"[:1000]}

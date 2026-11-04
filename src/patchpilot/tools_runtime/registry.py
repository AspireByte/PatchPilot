"""工具定义与授权范围的注册表。

注册表只负责描述和查找工具。具体的文件、命令或 Harness 实现仍由调用方注入，
这样核心循环可以在测试中使用内存工具，而不需要加载宿主设施。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from patchpilot.core.types import ToolCall, ToolResult

from .protocol import ToolContext, ToolProvider


class ToolNotFound(LookupError):
    """调用方请求了当前注册表中没有或未授权的工具。"""


@dataclass(frozen=True)
class RegisteredTool:
    """对模型公开的一项工具及其执行器。"""

    name: str
    description: str
    input_schema: Mapping[str, Any]
    handler: Callable[[ToolCall, ToolContext], Mapping[str, Any] | ToolResult]

    def definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": dict(self.input_schema),
        }


class ToolRegistry:
    """按名称保存工具定义，并只暴露当前注册的工具。

    ``from_provider`` 是旧 ``LocalTools`` 等 provider 的兼容桥接；新工具可以直接
    用 ``register`` 注册，避免把所有工具塞进一个 dispatcher。
    """

    def __init__(
        self, tools: list[RegisteredTool] | None = None, *, provider: ToolProvider | None = None
    ):
        self._tools: dict[str, RegisteredTool] = {}
        self._provider = provider
        for tool in tools or []:
            self.register_tool(tool)

    @classmethod
    def from_provider(cls, provider: ToolProvider) -> ToolRegistry:
        return cls(provider=provider)

    def register_tool(self, tool: RegisteredTool) -> None:
        if not tool.name or tool.name in self._tools:
            raise ValueError("工具名称为空或已注册")
        self._tools[tool.name] = tool

    def register(
        self,
        name: str,
        description: str,
        input_schema: Mapping[str, Any],
        handler: Callable[[ToolCall, ToolContext], Mapping[str, Any] | ToolResult],
    ) -> None:
        self.register_tool(RegisteredTool(name, description, dict(input_schema), handler))

    def definitions(self) -> list[dict[str, Any]]:
        if self._provider is not None:
            return [dict(item) for item in self._provider.definitions()]
        return [tool.definition() for tool in self._tools.values()]

    def execute(self, call: ToolCall, *, context: ToolContext) -> dict[str, Any]:
        if self._provider is not None:
            result = self._provider.dispatch(
                call.name, dict(call.input), remaining=context.remaining
            )
            return dict(result)
        try:
            tool = self._tools[call.name]
        except KeyError as error:
            raise ToolNotFound(call.name) from error
        self._validate_input(tool.input_schema, call.input)
        result = tool.handler(call, context)
        if isinstance(result, ToolResult):
            return {"ok": result.ok, **dict(result.content)}
        return dict(result)

    @staticmethod
    def _validate_input(schema: Mapping[str, Any], arguments: Mapping[str, Any]) -> None:
        """执行注册时声明的最小 JSON schema 约束，避免仅依赖模型遵守描述。"""
        if schema.get("type") not in (None, "object"):
            return
        required = schema.get("required", ())
        missing = [name for name in required if name not in arguments]
        if missing:
            raise ValueError(f"缺少工具参数：{', '.join(map(str, missing))}")
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            return
        if schema.get("additionalProperties") is False:
            extra = set(arguments) - set(properties)
            if extra:
                raise ValueError(f"存在未声明工具参数：{', '.join(sorted(map(str, extra)))}")
        type_map = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "object": Mapping,
            "array": list,
        }
        for name, definition in properties.items():
            if name not in arguments or not isinstance(definition, Mapping):
                continue
            expected = type_map.get(definition.get("type"))
            if expected is not None and (
                not isinstance(arguments[name], expected)
                or (definition.get("type") == "integer" and isinstance(arguments[name], bool))
            ):
                raise ValueError(f"工具参数类型不正确：{name}")

"""上下文构建边界；当前实现保留完整历史并只做容量检查。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Protocol


class ContextLimitReached(RuntimeError):
    """序列化后的请求超过当前上下文预算。"""


class ContextSource(Protocol):
    """为请求提供附加消息的只读来源，供未来 Memory 等能力接入。"""

    def messages(self) -> Sequence[Mapping[str, Any]]: ...


class Compactor(Protocol):
    """可选的历史压缩策略；不会修改 Session 保存的原始消息。"""

    def compact(
        self, messages: Sequence[Mapping[str, Any]], *, max_chars: int
    ) -> Sequence[Mapping[str, Any]]: ...


class NoopCompactor:
    """默认策略：原样返回完整历史。"""

    def compact(
        self, messages: Sequence[Mapping[str, Any]], *, max_chars: int
    ) -> Sequence[Mapping[str, Any]]:
        return messages


class ContextBuilder:
    """构建模型请求上下文，为未来压缩保留稳定入口。"""

    def __init__(
        self,
        messages: list[dict],
        max_chars: int,
        *,
        compactor: Compactor | None = None,
        sources: Sequence[ContextSource] = (),
    ):
        self.messages = messages
        self.max_chars = max_chars
        self.compactor = compactor or NoopCompactor()
        self.sources = tuple(sources)

    def append(self, message: dict) -> None:
        self.messages.append(message)

    def fits(
        self,
        system: str,
        tools: Sequence[Mapping[str, Any]],
        *,
        messages: Sequence[Mapping[str, Any]] | None = None,
    ) -> bool:
        history = self._view(messages)
        size = len(system)
        size += len(json.dumps(history, ensure_ascii=False))
        size += len(json.dumps(tools, ensure_ascii=False))
        return size <= self.max_chars

    def build(
        self,
        system: str,
        tools: Sequence[Mapping[str, Any]],
        *,
        messages: Sequence[Mapping[str, Any]] | None = None,
    ) -> tuple[str, list[Mapping[str, Any]], list[Mapping[str, Any]]]:
        history = list(self._view(messages))
        tool_defs = list(tools)
        if not self.fits(system, tool_defs, messages=history):
            raise ContextLimitReached("上下文达到上限，请保存并使用 /new 开启新会话")
        return system, history, tool_defs

    def _view(self, messages: Sequence[Mapping[str, Any]] | None) -> Sequence[Mapping[str, Any]]:
        history = list(self.messages if messages is None else messages)
        for source in self.sources:
            history.extend(source.messages())
        return self.compactor.compact(history, max_chars=self.max_chars)

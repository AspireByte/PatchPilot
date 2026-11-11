"""上下文视图、只读容量检查与请求前压缩边界。"""

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

    def view(self, messages):
        return messages


class ContextBuilder:
    """构建模型请求上下文，统一扣除固定开销并按需压缩历史。"""

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
        history = self.view(messages=messages)
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
        raw = self.messages if messages is None else messages
        tool_defs = list(tools)
        extra = [message for source in self.sources for message in source.messages()]
        overhead = len(system) + len(json.dumps(tool_defs, ensure_ascii=False))
        if extra:
            overhead += len(json.dumps(extra, ensure_ascii=False))
        available = self.max_chars - overhead
        if available <= 0:
            raise ContextLimitReached("固定提示和工具定义已超过上下文预算，请调整预算或使用 /new")
        history = list(self.compactor.compact(raw, max_chars=available)) + extra
        if (
            len(system)
            + len(json.dumps(history, ensure_ascii=False))
            + len(json.dumps(tool_defs, ensure_ascii=False))
            > self.max_chars
        ):
            raise ContextLimitReached("上下文达到上限，最近消息无法压缩；可使用 /compact 或 /new")
        return system, history, tool_defs

    def view(self, *, messages: Sequence[Mapping[str, Any]] | None = None) -> list:
        """只生成已有摘要视图，不调用模型，也不触发压缩。"""
        history = list(self.messages if messages is None else messages)
        view = getattr(self.compactor, "view", None)
        history = list(view(history)) if view else history
        for source in self.sources:
            history.extend(source.messages())
        return history

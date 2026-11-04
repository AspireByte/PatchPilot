"""Runtime 生命周期事件。"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class RuntimeEvent:
    """第一阶段统一的生命周期事件；payload 保持有界且 JSON 兼容。"""

    kind: str
    payload: Mapping[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"event": self.kind, **dict(self.payload)}


class EventSink(Protocol):
    def __call__(self, event: RuntimeEvent) -> None: ...


def make_event_sink(listener: Callable[[dict[str, Any]], None]) -> EventSink:
    """把旧的 dict 回调适配为 RuntimeEvent 订阅。"""

    def publish(event: RuntimeEvent) -> None:
        listener(event.as_dict())

    return publish

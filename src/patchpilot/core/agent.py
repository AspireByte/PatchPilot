"""Runtime Agent 生命周期对象。"""

from __future__ import annotations

from collections.abc import Callable

from .events import RuntimeEvent


class Agent:
    """持有 Loop 依赖和事件订阅，不直接依赖 CLI 或磁盘存储。"""

    def __init__(self, loop_factory, *, on_event: Callable[[RuntimeEvent], None] | None = None):
        self.loop_factory = loop_factory
        self._listeners: list[Callable[[RuntimeEvent], None]] = []
        if on_event is not None:
            self._listeners.append(on_event)
        self._aborted = False
        self._active_loop = None

    def subscribe(self, listener: Callable[[RuntimeEvent], None]):
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def publish(self, event: RuntimeEvent) -> None:
        for listener in tuple(self._listeners):
            listener(event)

    def prompt(self, messages: list[dict], system: str):
        self._aborted = False
        loop = self.loop_factory(events=self.publish)
        self._active_loop = loop
        try:
            return loop.run(messages, system)
        finally:
            self._active_loop = None

    def continue_run(self, messages: list[dict], system: str):
        return self.prompt(messages, system)

    def abort(self) -> None:
        self._aborted = True
        loop = self._active_loop
        if loop is not None and getattr(loop, "control", None) is not None:
            loop.control.stop("cancelled")

    @property
    def aborted(self) -> bool:
        return self._aborted

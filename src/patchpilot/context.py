"""会话上下文边界；当前只做完整历史与容量检查，为未来压缩预留接口。"""

import json


class ContextManager:
    def __init__(self, messages: list, max_chars: int):
        self.messages = messages
        self.max_chars = max_chars

    def append(self, message: dict) -> None:
        """统一消息追加点，保留完整块及工具调用编号。"""
        self.messages.append(message)

    def fits(self, system: str, tools: list, *, messages: list | None = None) -> bool:
        history = self.messages if messages is None else messages
        size = len(system) + len(json.dumps(history, ensure_ascii=False))
        size += len(json.dumps(tools, ensure_ascii=False))
        return size <= self.max_chars

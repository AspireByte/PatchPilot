"""Session 对外接口的轻量协议。"""

from typing import Protocol


class Session(Protocol):
    def prompt(self, text: str): ...

    def close(self) -> None: ...

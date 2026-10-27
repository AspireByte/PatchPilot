"""会话状态原子替换与由操作系统释放的独占锁。"""

import json
import os
import tempfile
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class _Record(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class _Usage(_Record):
    calls: int = Field(ge=0)
    tokens: int = Field(ge=0)
    unknown_usage: int = Field(ge=0)


class _Stats(_Record):
    worker: _Usage
    evaluator: _Usage
    tool_calls: int = Field(ge=0)


class _Goal(_Record):
    condition: str
    status: Literal["none", "active", "paused", "achieved"]
    evaluations: int = Field(ge=0)
    reason: str


class _Message(_Record):
    role: Literal["user", "assistant"]
    content: str | list[dict]


class _State(_Record):
    version: Literal[1]
    repo: str
    messages: list[_Message]
    goal: _Goal
    stats: _Stats
    baselines: dict[str, str | None]
    model: str
    evaluator_model: str


class SessionStore:
    def __init__(self, root: Path, session_id: str | None = None):
        self.session_id = str(UUID(session_id)) if session_id else str(uuid4())
        self.directory = root.resolve() / self.session_id
        if session_id and not (self.directory / "state.json").is_file():
            raise ValueError("找不到指定会话；请检查会话编号和 --sessions-dir")
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = (self.directory / "session.lock").open("a+b")
        try:
            if os.name == "nt":
                import msvcrt

                self._lock.seek(0, os.SEEK_END)
                if not self._lock.tell():
                    self._lock.write(b"0")
                    self._lock.flush()
                self._lock.seek(0)
                msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self._lock.close()
            raise ValueError("该会话已被另一个进程打开") from error

    def load(self) -> dict:
        try:
            value = json.loads((self.directory / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError("会话状态无法读取") from error
        if not isinstance(value, dict) or value.get("version") != 1:
            raise ValueError("不支持的会话状态版本")
        try:
            return _State.model_validate(value).model_dump()
        except ValidationError as error:
            raise ValueError("会话状态字段损坏，拒绝恢复") from error

    def save(self, state: dict) -> None:
        descriptor, name = tempfile.mkstemp(prefix="state-", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(state, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.directory / "state.json")
        finally:
            Path(name).unlink(missing_ok=True)

    def event(self, event: dict) -> None:
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")

    def close(self) -> None:
        self._lock.close()

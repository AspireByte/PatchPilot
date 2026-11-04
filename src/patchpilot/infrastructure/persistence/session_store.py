"""会话状态原子替换与由操作系统释放的独占锁。"""

import json
import os
import tempfile
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from patchpilot.session_runtime.state import StateCodec


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


class _Harness(_Record):
    enabled: bool
    config: str | None
    runtime: Literal["auto", "docker", "host"] | None


class _State(_Record):
    # Literal 限定可接受的值，A | None 表示允许空值；这些注解由 Pydantic 在 load 时校验。
    # 普通 Python 注解不会自动做这些检查，这里的运行时校验来自 BaseModel。
    version: Literal[1]
    repo: str
    messages: list[_Message]
    goal: _Goal
    stats: _Stats
    baselines: dict[str, str | None]
    model: str
    evaluator_model: str
    harness: _Harness | None = None


class SessionStore:
    """管理单个会话的磁盘记录与独占访问，向 AgentSession 提供 load/save/event 接口。

    state.json 保存当前完整状态，events.jsonl 逐行追加执行事件，session.lock 承载系统文件锁。
    锁用于拒绝两个进程同时打开同一会话；它不锁仓库文件，也不自动回滚模型产生的副作用。
    状态先写临时文件再替换正式文件，降低中断时留下不完整 JSON 的风险。
    """

    def __init__(
        self,
        root: Path,
        session_id: str | None = None,
        *,
        codec: StateCodec | None = None,
    ):
        """定位会话目录并立即取得独占锁；未提供编号时生成新的 UUID。

        指定旧编号时要求 state.json 已存在，避免把错误编号悄悄当成新会话。
        编号格式错误、记录缺失或锁被占用时初始化失败；成功后应保证调用 close 释放资源。
        """
        self.session_id = str(UUID(session_id)) if session_id else str(uuid4())
        self.codec = codec or StateCodec()
        self.directory = root.resolve() / self.session_id
        if session_id and not (self.directory / "state.json").is_file():
            raise ValueError("找不到指定会话；请检查会话编号和 --sessions-dir")
        self.directory.mkdir(parents=True, exist_ok=True)
        # 锁由打开的文件句柄持有，进程退出后由系统释放；锁文件仍在不等于会话仍被占用。
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
        """读取并严格校验版本及字段，返回供会话恢复的普通字典。

        Pydantic 校验模型会检查状态类型与取值，再转换回字典；文件损坏时统一抛出 ValueError。
        本方法只加载数据，暂停目标和补齐未完成工具调用由 AgentSession 负责。
        """
        try:
            value = json.loads((self.directory / "state.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError("会话状态无法读取") from error
        try:
            value = self.codec.decode(value)
        except ValueError as error:
            raise ValueError("不支持的会话状态版本") from error
        try:
            return _State.model_validate(value).model_dump()
        except ValidationError as error:
            raise ValueError("会话状态字段损坏，拒绝恢复") from error

    def save(self, state: dict) -> None:
        """将调用方组装的可序列化状态完整替换为新 state.json，不在此处执行字段模型校验。

        同目录临时文件写入后依次 flush、fsync、replace，finally 清理残留临时文件。
        文件写入失败会向上传播；事件日志与仓库修改不参与这次替换事务。
        """
        # 在同目录写临时文件、flush/fsync 后再 replace，避免中途退出留下半份 state.json。
        # 这保护单次状态文件替换，不代表状态文件、事件日志和仓库修改处于同一个事务。
        descriptor, name = tempfile.mkstemp(prefix="state-", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(self.codec.encode(state), stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.directory / "state.json")
        finally:
            Path(name).unlink(missing_ok=True)

    def event(self, event: dict) -> None:
        """追加一个 JSON 事件并换行，便于逐条读取过程记录；不覆盖完整状态快照。

        与 save 不同，这里没有单独调用 fsync，不能把事件已写出等同于所有状态都已持久化。
        """
        with (self.directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")

    def close(self) -> None:
        self._lock.close()

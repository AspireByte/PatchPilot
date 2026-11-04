"""Session 状态编码和未来版本迁移接口。"""

from __future__ import annotations

from typing import Protocol


class StateMigrator(Protocol):
    def migrate(self, state: dict, from_version: int, to_version: int) -> dict: ...


class UnsupportedStateVersion(ValueError):
    """状态文件版本无法由当前运行时读取或迁移。"""


class StateCodec:
    """当前只接受现有 version 1，预留后续迁移入口。"""

    current_version = 1

    def __init__(self, migrator: StateMigrator | None = None):
        self.migrator = migrator

    def decode(self, state: dict) -> dict:
        if not isinstance(state, dict) or not isinstance(state.get("version"), int):
            raise UnsupportedStateVersion("会话状态缺少有效版本")
        version = state["version"]
        if version > self.current_version:
            raise UnsupportedStateVersion("会话状态版本高于当前运行时")
        result = dict(state)
        while version < self.current_version:
            if self.migrator is None:
                raise UnsupportedStateVersion("当前运行时没有会话状态迁移器")
            migrated = self.migrator.migrate(result, version, version + 1)
            if not isinstance(migrated, dict) or migrated.get("version") != version + 1:
                raise UnsupportedStateVersion("会话状态迁移器返回了无效版本")
            result = migrated
            version += 1
        return result

    def encode(self, state: dict) -> dict:
        result = dict(state)
        result["version"] = self.current_version
        return result

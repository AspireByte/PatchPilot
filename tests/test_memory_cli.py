"""真实 CLI 入口的记忆开关、资源管理、新会话及恢复行为。"""

import json
import sqlite3

from test_chat_session import ScriptedModel, response

from patchpilot.infrastructure.persistence.memory_store import SQLiteMemoryStore
from patchpilot.interfaces.cli.main import main


def install_model(monkeypatch, *, memory):
    instances = []

    class FakeModel(ScriptedModel):
        def __init__(self, *, model, **kwargs):
            turns = [response("第一轮")]
            if memory:
                turns.append(response('{"operations": []}'))
            turns.append(response("第二轮"))
            if memory:
                turns.append(response('{"operations": []}'))
            super().__init__(*turns)
            self.model = model
            self.closed = False
            instances.append(self)

        def close(self):
            self.closed = True

    monkeypatch.setattr("patchpilot.interfaces.cli.chat.AnthropicModel", FakeModel)
    return instances


def test_cli_new_reuses_database_and_resume_requires_flag(tmp_path, monkeypatch):
    instances = install_model(monkeypatch, memory=True)
    inputs = iter(
        [
            "/memory remember --global 使用中文解释",
            "/memory remember 使用上下文压缩",
            "第一次解释",
            "/new",
            "第二次解释",
            "/exit",
        ]
    )
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    root, path = tmp_path / "sessions", tmp_path / "memory.sqlite3"
    assert (
        main(
            [
                "chat",
                "--repo",
                str(tmp_path),
                "--sessions-dir",
                str(root),
                "--allow-api",
                "--memory",
                "--memory-db",
                str(path),
            ]
        )
        == 0
    )
    assert len(instances[0].requests) == 4
    for request in instances[0].requests[::2]:
        assert "使用中文解释" in request["messages"][-1]["content"]
        assert any(t["name"] == "memory_read" for t in request["tools"])
    assert all(m.closed for m in instances)
    states = list(root.glob("*/state.json"))
    assert len(states) == 2
    stored = SQLiteMemoryStore(path)
    try:
        assert len(stored.preferences()) == 1
    finally:
        stored.close()
    instances = install_model(monkeypatch, memory=False)
    inputs = iter(["恢复解释", "/exit"])
    assert (
        main(
            [
                "chat",
                "--resume",
                states[0].parent.name,
                "--sessions-dir",
                str(root),
                "--allow-api",
            ]
        )
        == 0
    )
    assert len(instances[0].requests) == 1
    assert not any(t["name"].startswith("memory_") for t in instances[0].requests[0]["tools"])
    state = json.loads(states[0].read_text(encoding="utf-8"))
    assert state["version"] == 1


def test_memory_db_flag_alone_does_not_open_database(tmp_path, monkeypatch):
    install_model(monkeypatch, memory=False)

    def forbidden(*args):
        raise AssertionError("未显式启用时不应打开数据库")

    monkeypatch.setattr("patchpilot.interfaces.cli.chat.SQLiteMemoryStore", forbidden)
    inputs = iter(["普通问题", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    assert (
        main(
            [
                "chat",
                "--repo",
                str(tmp_path),
                "--sessions-dir",
                str(tmp_path / "sessions"),
                "--allow-api",
                "--memory-db",
                str(tmp_path / "unused.sqlite3"),
            ]
        )
        == 0
    )
    assert not (tmp_path / "unused.sqlite3").exists()


def test_cli_unavailable_database_preserves_chat(tmp_path, monkeypatch, capsys):
    instances = install_model(monkeypatch, memory=False)

    def unavailable(*args):
        raise sqlite3.OperationalError("数据库不可用")

    monkeypatch.setattr("patchpilot.interfaces.cli.chat.SQLiteMemoryStore", unavailable)
    inputs = iter(["普通问题", "/memory", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    assert (
        main(
            [
                "chat",
                "--repo",
                str(tmp_path),
                "--sessions-dir",
                str(tmp_path / "sessions"),
                "--allow-api",
                "--memory",
                "--memory-db",
                str(tmp_path / "unavailable.sqlite3"),
            ]
        )
        == 0
    )
    assert len(instances[0].requests) == 1
    assert "数据库不可用" in capsys.readouterr().out

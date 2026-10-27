"""交互入口、配置加载和新建/恢复会话测试。"""

import json
import os

import pytest
from test_chat_session import ScriptedModel, response

from patchpilot.chat_cli import load_env_file
from patchpilot.cli import main


def test_chat_requires_api_permission(tmp_path, capsys):
    assert main(["chat", "--repo", str(tmp_path)]) == 2
    assert "--allow-api" in capsys.readouterr().out
    assert not (tmp_path / ".patchpilot-sessions").exists()


def test_chat_help_explains_host_commands(capsys):
    with pytest.raises(SystemExit) as result:
        main(["chat", "--help"])
    assert result.value.code == 0
    output = capsys.readouterr().out
    assert "--resume" in output and "不是Docker" in output


def test_environment_file_is_explicit_and_does_not_override(tmp_path, monkeypatch):
    monkeypatch.setenv("PATCHPILOT_TEST_EXISTING", "已有")
    monkeypatch.delenv("PATCHPILOT_TEST_NEW", raising=False)
    path = tmp_path / "settings.env"
    path.write_text(
        'PATCHPILOT_TEST_EXISTING=不覆盖\nexport PATCHPILOT_TEST_NEW="含 空格$原样" # 注释\n',
        encoding="utf-8",
    )
    load_env_file(path)
    try:
        assert os.environ["PATCHPILOT_TEST_EXISTING"] == "已有"
        assert os.environ["PATCHPILOT_TEST_NEW"] == "含 空格$原样"
    finally:
        os.environ.pop("PATCHPILOT_TEST_NEW", None)


def test_invalid_env_file_does_not_partially_update(tmp_path, monkeypatch):
    monkeypatch.delenv("PATCHPILOT_TEST_NEW", raising=False)
    path = tmp_path / "settings.env"
    path.write_text("PATCHPILOT_TEST_NEW=值\n错误格式\n", encoding="utf-8")
    with pytest.raises(ValueError, match="第2行"):
        load_env_file(path)
    assert "PATCHPILOT_TEST_NEW" not in os.environ


def test_cli_conversation_new_and_resume(tmp_path, monkeypatch, capsys):
    class FakeModel(ScriptedModel):
        def __init__(self, *, model, **kwargs):
            super().__init__(response("你好"), response("恢复后的回答"))
            self.model = model

        def close(self):
            pass

    monkeypatch.setattr("patchpilot.chat_cli.AnthropicModel", FakeModel)
    root = tmp_path / "sessions"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env").write_text("ANTHROPIC_API_KEY=不能自动加载", encoding="utf-8")
    before = os.environ.get("ANTHROPIC_API_KEY")
    inputs = iter(["你好", "/new", "新会话", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    assert main(["chat", "--repo", str(repo), "--sessions-dir", str(root), "--allow-api"]) == 0
    states = list(root.glob("*/state.json"))
    assert len(states) == 2
    assert os.environ.get("ANTHROPIC_API_KEY") == before
    state_path = states[0]
    saved = json.loads(state_path.read_text(encoding="utf-8"))
    assert saved["messages"]
    inputs = iter(["继续", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _: next(inputs))
    assert (
        main(
            ["chat", "--resume", state_path.parent.name, "--sessions-dir", str(root), "--allow-api"]
        )
        == 0
    )
    restored = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(restored["messages"]) > len(saved["messages"])
    assert "恢复后的回答" not in capsys.readouterr().err

"""主程序不再公开固定任务的验收和沙箱 CLI。"""

import tomllib
from pathlib import Path

import pytest

from patchpilot.interfaces.cli.main import main


@pytest.mark.parametrize("command", ["verify-patch", "sandbox-check"])
def test_old_execution_commands_are_rejected(command):
    with pytest.raises(SystemExit) as result:
        main([command])
    assert result.value.code == 2


def test_distribution_exposes_only_chat_cli():
    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["name"] == "myagent"
    assert data["project"]["scripts"] == {
        "myagent": "patchpilot.interfaces.cli.main:main",
        "patchpilot": "patchpilot.interfaces.cli.main:main",
    }

"""主程序不再公开固定任务的验收和沙箱 CLI。"""

import tomllib
from pathlib import Path

import pytest

from patchpilot.cli import main


@pytest.mark.parametrize("command", ["verify-patch", "sandbox-check"])
def test_old_execution_commands_are_rejected(command):
    with pytest.raises(SystemExit) as result:
        main([command])
    assert result.value.code == 2


def test_distribution_exposes_only_chat_cli():
    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["scripts"] == {"patchpilot": "patchpilot.cli:main"}

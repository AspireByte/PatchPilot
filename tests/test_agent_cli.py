"""旧固定任务用户入口已移除；预算参数由聊天入口接管。"""

import pytest

from patchpilot.cli import main


@pytest.mark.parametrize("flag", ["--max-model-calls", "--max-tool-calls", "--max-tokens"])
def test_chat_budgets_reject_nonpositive_values(flag, capsys) -> None:
    with pytest.raises(SystemExit) as result:
        main(["chat", flag, "0"])
    assert result.value.code == 2
    assert "正整数" in capsys.readouterr().err

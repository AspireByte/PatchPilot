from pathlib import Path

import pytest

from patchpilot.cli import main

CASE = Path(__file__).resolve().parents[1] / "benchmarks/tasks/pagination-001/task.json"


def test_agent_cli_requires_explicit_api_permission(tmp_path: Path, capsys) -> None:
    code = main(["agent-run", str(CASE), "--runs-dir", str(tmp_path)])
    assert code == 2
    captured = capsys.readouterr()
    assert "--allow-api" in captured.err
    assert not list(tmp_path.iterdir())


def test_agent_help_explains_the_network_and_cost_gate(capsys) -> None:
    with pytest.raises(SystemExit) as exit:
        main(["agent-run", "--help"])
    assert exit.value.code == 0
    text = capsys.readouterr().out
    assert "--allow-api" in text
    assert "费用" in text


@pytest.mark.parametrize("flag", ["--max-model-calls", "--max-tool-calls", "--max-tokens"])
def test_agent_budgets_reject_nonpositive_values(flag, capsys) -> None:
    with pytest.raises(SystemExit) as exit:
        main(["agent-run", str(CASE), flag, "0"])
    assert exit.value.code == 2
    assert "正整数" in capsys.readouterr().err

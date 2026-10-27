from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_ci_configuration_is_parseable_read_only_and_has_no_model_credentials():
    workflow = ROOT / ".github/workflows/ci.yml"
    assert workflow.is_file(), "尚未提供自动CI配置"
    data = yaml.load(workflow.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    assert data["permissions"] == {"contents": "read"}
    assert "pull_request_target" not in data["on"]
    assert {"checks", "integration"} <= data["jobs"].keys()
    for job in data["jobs"].values():
        assert int(job["timeout-minutes"]) <= 20
        text = str(job)
        assert "secrets." not in text and "--allow-api" not in text
        assert "ANTHROPIC_API_KEY" not in text and "ANTHROPIC_AUTH_TOKEN" not in text
    integration = data["jobs"]["integration"]
    commands = "\n".join(step.get("run", "") for step in integration["steps"])
    assert "--run-docker" in commands and "--run-postgres" in commands
    assert "postgres" in integration["services"]

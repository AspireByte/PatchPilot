import importlib
from pathlib import Path

import pytest


def api():
    assert importlib.util.find_spec("patchpilot.sandbox"), "尚未实现 Docker 测试执行器"
    return importlib.import_module("patchpilot.sandbox")


@pytest.mark.parametrize(
    ("exit_code", "xml", "status", "counts"),
    [
        (0, b'<testsuite><testcase name="ok"/></testsuite>', "passed", (1, 0, 0, 0)),
        (1, b"<testsuite><testcase><failure/></testcase></testsuite>", "failed", (0, 1, 0, 0)),
        (2, b"<testsuite><testcase><error/></testcase></testsuite>", "test_error", (0, 0, 1, 0)),
        (5, b"<testsuite/>", "no_tests", (0, 0, 0, 0)),
        (0, b"<testsuite><testcase><skipped/></testcase></testsuite>", "no_tests", (0, 0, 0, 1)),
        (0, b"not xml", "infra_error", None),
        (0, None, "infra_error", None),
        (5, None, "no_tests", None),
        (2, None, "test_error", None),
        (4, None, "test_error", None),
        (137, None, "infra_error", None),
        (1, b'<testsuite><testcase name="ok"/></testsuite>', "infra_error", (1, 0, 0, 0)),
        (0, b"<testsuite><testcase><failure/></testcase></testsuite>", "infra_error", (0, 1, 0, 0)),
    ],
)
def test_pytest_result_classification(exit_code, xml, status, counts) -> None:
    outcome = api().classify_pytest(exit_code, xml)
    assert outcome.status == status
    actual = (outcome.passed, outcome.failed, outcome.errors, outcome.skipped)
    assert actual == (counts if counts is not None else (None, None, None, None))


def test_xml_entities_are_not_accepted() -> None:
    xml = b'<!DOCTYPE foo [<!ENTITY x "fake">]><testsuite><testcase name="&x;"/></testsuite>'
    assert api().classify_pytest(0, xml).status == "infra_error"


def test_container_command_enforces_the_policy_without_host_mounts(tmp_path: Path) -> None:
    module = api()
    policy = module.SandboxLimits()
    arguments = module.create_command("sha256:" + "a" * 64, "patchpilot-test", policy, False)
    for flag, expected in {
        "--network": "none",
        "--user": "10001:10001",
        "--cpus": "1.0",
        "--memory": "256m",
        "--memory-swap": "256m",
        "--pids-limit": "64",
        "--cap-drop": "ALL",
        "--security-opt": "no-new-privileges:true",
        "--pull": "never",
    }.items():
        assert arguments[arguments.index(flag) + 1] == expected
    assert "--privileged" not in arguments
    assert "--volume" not in arguments and "--mount" not in arguments
    assert "-v" not in arguments
    assert arguments[-1] == "--junitxml=/results/report.xml"
    assert "/acceptance" not in arguments
    assert "/acceptance" in module.create_command("sha256:" + "a" * 64, "test", policy, True)


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_invalid_timeouts_are_rejected(value: float) -> None:
    with pytest.raises(ValueError):
        api().SandboxLimits(timeout_seconds=value)

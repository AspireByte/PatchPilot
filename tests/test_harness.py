"""通用 Harness 配置、快照、宿主降级和聊天工具契约。"""

import hashlib
import json
import subprocess
import threading
from pathlib import Path

import pytest

from patchpilot.control import ExecutionControl
from patchpilot.harness import HarnessConfig, HarnessService
from patchpilot.local_tools import LocalTools
from patchpilot.process_runner import ProcessResult


def write_config(repo: Path, body: str) -> None:
    path = repo / ".patchpilot"
    path.mkdir()
    (path / "harness.toml").write_text(body, encoding="utf-8")


def test_host_profile_runs_on_snapshot_and_keeps_repo_unchanged(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        'version=1\nruntime="host"\n[profiles.test]\nargv=["python","check.py"]\n',
    )
    (tmp_path / "check.py").write_text(
        "from pathlib import Path\nPath('created.txt').write_text('x')\nprint('ok')\n",
        encoding="utf-8",
    )
    (tmp_path / "source.py").write_text("value = 1\n", encoding="utf-8")
    service = HarnessService(tmp_path, HarnessConfig.load(tmp_path))

    result = service.run("test")

    assert result["status"] == "passed"
    assert result["runtime"] == "host"
    assert result["isolated"] is False
    assert result["evidence_level"] == "local"
    assert not (tmp_path / "created.txt").exists()
    report = Path(result["artifact_path"]) / "result.json"
    assert json.loads(report.read_text(encoding="utf-8"))["snapshot_sha256"]


def test_profile_rejects_shell_syntax_and_invalid_cwd(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        'version=1\n[profiles.bad]\nargv=["python", "-c", "a|b"]\n',
    )
    with pytest.raises(ValueError, match="安全参数数组"):
        HarnessConfig.load(tmp_path)


def test_missing_explicit_config_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="找不到 Harness 配置"):
        HarnessConfig.load(tmp_path, tmp_path / "missing.toml")


def test_docker_mode_reports_infrastructure_error_when_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    write_config(
        tmp_path,
        'version=1\nruntime="docker"\nimage="missing-image"\n[profiles.test]\nargv=["python","-V"]\n',
    )
    monkeypatch.setattr(
        HarnessService, "_docker_image", lambda *args: (_ for _ in ()).throw(OSError())
    )
    result = HarnessService(tmp_path, HarnessConfig.load(tmp_path)).run("test")
    assert result["status"] == "infra_error"
    assert result["runtime"] == "docker"
    assert result["isolated"] is False


def test_local_tool_exposes_harness_only_when_injected(tmp_path: Path) -> None:
    plain = LocalTools(tmp_path)
    assert "run_harness_check" not in {item["name"] for item in plain.definitions()}
    write_config(tmp_path, 'version=1\nruntime="host"\n[profiles.test]\nargv=["python","-V"]\n')
    service = HarnessService(tmp_path, HarnessConfig.load(tmp_path))
    tools = LocalTools(tmp_path, harness=service)
    assert "run_harness_check" in {item["name"] for item in tools.definitions()}
    result = tools.dispatch("run_harness_check", {"profile": "test"})
    assert result["ok"] is True
    assert result["harness"]["exit_code"] == 0


def test_timeout_returns_bounded_result(tmp_path: Path) -> None:
    write_config(
        tmp_path,
        'version=1\nruntime="host"\ntimeout_seconds=0.3\n[profiles.test]\nargv=["python","slow.py"]\n',
    )
    (tmp_path / "slow.py").write_text("import time\ntime.sleep(10)\n", encoding="utf-8")
    result = HarnessService(tmp_path, HarnessConfig.load(tmp_path)).run("test")
    assert result["status"] == "timeout"
    assert result["timed_out"] is True
    assert result["exit_code"] is None


@pytest.mark.parametrize("directory", ["../outside", "/outside", "C:/outside", "a/../../b"])
def test_profile_cwd_rejects_escape(tmp_path, directory):
    write_config(tmp_path, '[profiles.test]\nargv=["python", "-V"]\ncwd=' + json.dumps(directory))
    with pytest.raises(ValueError, match="cwd"):
        HarnessConfig.load(tmp_path)


@pytest.mark.parametrize(
    "argv",
    [
        ["sh", "-c", "echo hi"],
        ["cmd.exe", "/c", "echo hi"],
        ["check.cmd"],
        ["python", "-c", "print(1)"],
        ["echo", "a;b"],
        ["echo", "x>file"],
    ],
)
def test_config_rejects_implicit_shell_and_inline_script(tmp_path, argv):
    write_config(tmp_path, "[profiles.test]\nargv=" + json.dumps(argv))
    with pytest.raises(ValueError):
        HarnessConfig.load(tmp_path)


def test_default_probe_does_not_invent_profiles_for_non_python_project(tmp_path):
    assert not HarnessConfig.load(tmp_path).profiles
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    assert HarnessConfig.load(tmp_path).profiles["test"].argv == ("python", "-m", "pytest", "-q")


def test_snapshot_excludes_secrets_and_hash_binds_copied_bytes(tmp_path, monkeypatch):
    write_config(tmp_path, 'runtime="host"\n[profiles.test]\nargv=["python", "check.py"]')
    (tmp_path / "check.py").write_text("print('checked')", encoding="utf-8")
    for name in (
        ".env",
        ".env.production",
        ".aws/credentials",
        "node_modules/x",
        "_acceptance/x",
        "benchmarks/solutions/x",
        ".patchpilot-sessions/x",
        ".git/x",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("excluded", encoding="utf-8")
    original = (tmp_path / "check.py").read_bytes()
    service = HarnessService(tmp_path)
    copy_snapshot = service._copy_snapshot

    def change_after_copy(destination, deadline):
        value = copy_snapshot(destination, deadline)
        (tmp_path / "check.py").write_text("raise RuntimeError()", encoding="utf-8")
        return value

    monkeypatch.setattr(service, "_copy_snapshot", change_after_copy)
    result = service.run()
    assert result["status"] == "passed"
    manifest = json.loads((Path(result["artifact_path"]) / "manifest.json").read_text())
    assert [item[0] for item in manifest] == ["check.py"]
    assert manifest[0][1] == hashlib.sha256(original).hexdigest()
    assert (
        result["snapshot_sha256"]
        == hashlib.sha256(json.dumps(manifest, separators=(",", ":")).encode()).hexdigest()
    )


def test_configuration_is_frozen_until_restart(tmp_path):
    write_config(tmp_path, 'runtime="host"\n[profiles.test]\nargv=["python", "-V"]')
    service = HarnessService(tmp_path)
    (tmp_path / ".patchpilot/harness.toml").write_text("broken config", encoding="utf-8")
    assert service.run()["status"] == "passed"


def test_real_failure_cwd_output_limit_and_environment(tmp_path, monkeypatch):
    write_config(
        tmp_path,
        'runtime="host"\nmax_output_bytes=1024\n[profiles.test]\nargv=["python", "check.py"]\ncwd="checks"',
    )
    checks = tmp_path / "checks"
    checks.mkdir()
    (checks / "check.py").write_text(
        "import os, sys\nassert 'ANTHROPIC_API_KEY' not in os.environ\n"
        "assert os.path.basename(os.getcwd()) == 'checks'\nprint('x'*3000)\n"
        "print('tail-marker')\nsys.exit(7)",
        encoding="utf-8",
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-secret-not-inherited")
    result = HarnessService(tmp_path).run()
    assert result["status"] == "failed" and result["exit_code"] == 7
    assert result["truncated"] and len(result["output"].encode()) <= 1024
    assert "tail-marker" in result["output"]


def test_cancellation_returns_record_and_does_not_leave_process_running(tmp_path):
    write_config(tmp_path, 'runtime="host"\n[profiles.test]\nargv=["python", "slow.py"]')
    (tmp_path / "slow.py").write_text(
        "import time\nprint('before-cancellation', flush=True)\ntime.sleep(60)", encoding="utf-8"
    )
    control = ExecutionControl()
    timer = threading.Timer(0.3, lambda: control.stop("cancelled"))
    timer.start()
    try:
        result = HarnessService(tmp_path, control=control).run()
    finally:
        timer.join()
    assert result["status"] == "cancelled" and result["cancelled"]
    assert result["exit_code"] is None
    assert "before-cancellation" in result["output"]
    assert (Path(result["artifact_path"]) / "result.json").is_file()


@pytest.mark.parametrize("fallback", [True, False])
def test_auto_fallback_obeys_configuration(tmp_path, monkeypatch, fallback):
    write_config(
        tmp_path,
        'runtime="auto"\nallow_host_fallback='
        + str(fallback).lower()
        + '\n[profiles.test]\nargv=["python", "-V"]',
    )
    result = HarnessService(tmp_path).run()
    assert result["status"] == ("passed" if fallback else "infra_error")
    if fallback:
        assert result["runtime"] == "host" and result["fallback_reason"]


def test_docker_selection_uses_digest_and_cleanup_even_on_timeout(tmp_path, monkeypatch):
    write_config(
        tmp_path, 'runtime="auto"\nimage="configured"\n[profiles.test]\nargv=["python", "-V"]'
    )
    image_id = "sha256:" + "a" * 64
    monkeypatch.setattr(HarnessService, "_docker_image", lambda *_: image_id)
    commands = []

    def execute(command, **options):
        commands.append(command)
        assert options["cwd"].name == "repo"
        return ProcessResult(None, True, "timeout", False)

    def cleanup(command, **options):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr("patchpilot.harness.run_process", execute)
    monkeypatch.setattr("patchpilot.harness.subprocess.run", cleanup)
    result = HarnessService(tmp_path).run()
    assert result["runtime"] == "docker" and result["isolated"]
    assert result["image_id"] == image_id and result["status"] == "timeout"
    assert commands[0][-2:] == [image_id, "-V"]
    assert "--entrypoint" in commands[0] and "--pull" in commands[0]
    assert commands[-1][:3] == ["docker", "rm", "--force"]


def test_snapshot_link_escape_is_rejected(tmp_path):
    write_config(tmp_path, 'runtime="host"\n[profiles.test]\nargv=["python", "-V"]')
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("当前系统没有创建符号链接权限")
    try:
        assert HarnessService(tmp_path).run()["status"] == "infra_error"
    finally:
        link.unlink()


def test_exhausted_turn_budget_never_starts_process(tmp_path, monkeypatch):
    write_config(tmp_path, 'runtime="host"\n[profiles.test]\nargv=["python", "-V"]')

    def forbidden(*args, **kwargs):
        raise AssertionError("预算耗尽时不能执行")

    monkeypatch.setattr("patchpilot.harness.run_process", forbidden)
    result = HarnessService(tmp_path).run(remaining=0)
    assert result["status"] == "timeout" and result["exit_code"] is None


@pytest.mark.docker
def test_real_docker_executes_snapshot_with_overridden_entrypoint(tmp_path):
    write_config(
        tmp_path,
        'runtime="docker"\nimage="patchpilot-tests:py312-v1"\n'
        '[profiles.test]\nargv=["python", "check.py"]',
    )
    (tmp_path / "check.py").write_text(
        "import os\nfrom pathlib import Path\nassert os.getuid() == 10001\n"
        "assert not Path('.env').exists()\nPath('created.txt').write_text('snapshot')\n"
        "print('container-checked')\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text("PRIVATE=value", encoding="utf-8")
    result = HarnessService(tmp_path).run()
    assert result["status"] == "passed", result
    assert result["isolated"] and result["image_id"].startswith("sha256:")
    assert result["cleanup_ok"] and "container-checked" in result["output"]
    assert not (tmp_path / "created.txt").exists()

import importlib
import json
import subprocess
from pathlib import Path

import pytest

from patchpilot.task import load_task
from patchpilot.workspace import create_workspace

CASE = Path(__file__).resolve().parents[1] / "benchmarks/tasks/pagination-001/task.json"
pytestmark = pytest.mark.docker


def api():
    assert importlib.util.find_spec("patchpilot.sandbox"), "尚未实现 Docker 测试执行器"
    return importlib.import_module("patchpilot.sandbox")


def test_real_container_runs_the_baseline_and_cleans_up(tmp_path: Path) -> None:
    module = api()
    workspace = create_workspace(load_task(CASE), tmp_path)
    result = module.DockerRunner().run(workspace)
    assert result.status == "failed", result.detail
    assert (result.passed, result.failed, result.errors) == (6, 1, 0)
    assert result.exit_code == 1
    assert result.cleanup_ok
    assert result.image_id.startswith("sha256:")
    assert result.duration_seconds > 0
    assert Path(result.log_path).is_file()
    assert Path(result.report_path).is_file()
    inspect = json.loads((Path(result.log_path).parent / "container.json").read_text())
    assert inspect["HostConfig"]["NetworkMode"] == "none"
    assert inspect["HostConfig"]["Memory"] == 256 * 1024 * 1024
    assert inspect["HostConfig"]["NanoCpus"] == 1_000_000_000
    assert inspect["HostConfig"]["PidsLimit"] == 64
    assert inspect["HostConfig"]["Binds"] is None
    assert inspect["Config"]["User"] == "10001:10001"
    assert inspect["Mounts"] == []
    assert "ALL" in inspect["HostConfig"]["CapDrop"]
    assert "no-new-privileges:true" in inspect["HostConfig"]["SecurityOpt"]
    exists = subprocess.run(["docker", "inspect", result.container_id], capture_output=True)
    assert exists.returncode != 0


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("def test_ok():\n    assert True\n", "passed"),
        ("def test_fail():\n    assert False\n", "failed"),
        ("this is not valid python!", "test_error"),
        ("value = 1\n", "no_tests"),
        ("import pytest\n@pytest.mark.skip\ndef test_skip():\n    pass\n", "no_tests"),
    ],
)
def test_real_pytest_outcomes(tmp_path: Path, source: str, expected: str) -> None:
    module = api()
    workspace = create_workspace(load_task(CASE), tmp_path)
    for directory in ("tests", "repro"):
        for path in (workspace.repo / directory).glob("*.py"):
            path.unlink()
    (workspace.repo / "tests/test_outcome.py").write_text(source, encoding="utf-8")
    result = module.DockerRunner().run(workspace)
    assert result.status == expected, result.detail
    assert result.cleanup_ok


def test_timeout_kills_the_container_and_its_child_processes(tmp_path: Path) -> None:
    module = api()
    workspace = create_workspace(load_task(CASE), tmp_path)
    (workspace.repo / "repro/test_loop.py").write_text(
        "import subprocess\nimport sys\n"
        "def test_loop():\n"
        "    subprocess.Popen([sys.executable, '-c', 'while True: pass'])\n"
        "    while True:\n        pass\n",
        encoding="utf-8",
    )
    runner = module.DockerRunner(limits=module.SandboxLimits(timeout_seconds=3))
    result = runner.run(workspace)
    assert result.status == "timeout", result.detail
    assert result.cleanup_ok
    assert result.duration_seconds < 45
    exists = subprocess.run(["docker", "inspect", result.container_id], capture_output=True)
    assert exists.returncode != 0


def test_container_has_the_fixed_environment_and_no_host_secrets(
    tmp_path: Path, monkeypatch
) -> None:
    module = api()
    monkeypatch.setenv("PATCHPILOT_HOST_SECRET", "must-not-enter-container")
    workspace = create_workspace(load_task(CASE), tmp_path)
    for directory in ("tests", "repro"):
        for path in (workspace.repo / directory).glob("*.py"):
            path.unlink()
    (workspace.repo / "tests/test_policy.py").write_text(
        "import os\nimport sys\nfrom pathlib import Path\nimport pytest\n"
        "def test_policy():\n"
        "    assert sys.version_info[:3] == (3, 12, 8)\n"
        "    assert pytest.__version__ == '8.3.5'\n"
        "    assert os.getuid() == 10001\n"
        "    assert 'PATCHPILOT_HOST_SECRET' not in os.environ\n"
        "    assert not Path('/var/run/docker.sock').exists()\n"
        "    assert sorted(os.listdir('/sys/class/net')) == ['lo']\n"
        "    assert Path('/sys/fs/cgroup/memory.max').read_text().strip() == '268435456'\n"
        "    assert Path('/sys/fs/cgroup/pids.max').read_text().strip() == '64'\n",
        encoding="utf-8",
    )
    result = module.DockerRunner().run(workspace)
    assert result.status == "passed", result.detail
    assert result.passed == 1


def test_container_stderr_is_preserved_in_the_log(tmp_path: Path) -> None:
    module = api()
    workspace = create_workspace(load_task(CASE), tmp_path)
    (workspace.repo / "repro/test_crash.py").write_text(
        "import os\n"
        "def test_crash(capfd):\n"
        "    with capfd.disabled():\n"
        "        os.write(2, b'important-container-stderr\\n')\n"
        "        os._exit(3)\n",
        encoding="utf-8",
    )
    result = module.DockerRunner().run(workspace)
    assert result.status != "passed"
    assert "important-container-stderr" in Path(result.log_path).read_text(encoding="utf-8")


def test_missing_local_image_does_not_pull_or_fall_back(tmp_path: Path) -> None:
    workspace = create_workspace(load_task(CASE), tmp_path)
    result = api().DockerRunner(image="patchpilot-missing-test-image:never").run(workspace)
    assert result.status == "infra_error"
    assert result.container_id is None
    assert "镜像" in result.detail

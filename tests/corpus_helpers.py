"""基准样例测试共用的红/绿检查，始终只在临时副本运行代码。"""

import shutil
import subprocess
from pathlib import Path

from test_additional_samples import assert_green, run_pytest, snapshot_contents

from patchpilot.snapshot import compute_snapshot_hash
from patchpilot.task import load_task

ROOT = Path(__file__).resolve().parents[1]


def verify_sample(task_id: str, tmp_path: Path) -> dict[str, int]:
    root = ROOT / "benchmarks/tasks" / task_id
    assert (root / "task.json").is_file(), f"缺少任务：{task_id}"
    task = load_task(root / "task.json")
    before = snapshot_contents(task.snapshot_root)
    workspace = Path(shutil.copytree(task.snapshot_root, tmp_path / "repo"))
    acceptance = Path(
        shutil.copytree(
            ROOT / "src/patchpilot/_acceptance" / task_id.replace("-", "_"), tmp_path / "acceptance"
        )
    )
    normal = assert_green(workspace, tmp_path / "normal.xml", "tests")
    red, red_cases = run_pytest(workspace, tmp_path / "red.xml", "tests", "repro")
    assert red.returncode == 1, red.stdout + red.stderr
    failures = [case for case in red_cases if case.find("failure") is not None]
    assert failures
    assert all(case.attrib["name"].startswith("test_repro_") for case in failures)
    patch = (ROOT / "benchmarks/solutions" / task_id / "reference.patch").resolve()
    for args in (["apply", "--check", str(patch)], ["apply", str(patch)]):
        result = subprocess.run(
            ["git", "-C", str(workspace), *args], capture_output=True, timeout=15
        )
        assert result.returncode == 0, result.stderr
    green = assert_green(workspace, tmp_path / "green.xml", "tests", "repro", str(acceptance))
    assert snapshot_contents(task.snapshot_root) == before
    assert compute_snapshot_hash(task.snapshot_root) == task.spec.snapshot_sha256
    return {
        "normal": len(normal),
        "repro": len(red_cases) - len(normal),
        "acceptance": len(green) - len(red_cases),
    }

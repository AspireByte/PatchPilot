"""在临时副本中验证四个独立缺陷样例及维护者验收。"""

import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from patchpilot.snapshot import compute_snapshot_hash
from patchpilot.task import load_task

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = (
    ("boundary-001", "boundary_001", "boundary"),
    ("null-001", "null_001", "null"),
    ("validation-001", "validation_001", "validation"),
    ("state-001", "state_001", "state"),
)


def snapshot_contents(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def run_pytest(
    workspace: Path, report: Path, *targets: str
) -> tuple[subprocess.CompletedProcess[str], list[ET.Element]]:
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-c",
        "pytest.ini",
        *targets,
        "-q",
        "-p",
        "no:cacheprovider",
        f"--junitxml={report}",
    ]
    result = subprocess.run(
        command,
        cwd=workspace,
        env={
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTEST_ADDOPTS": "",
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    details = result.stdout + result.stderr
    print(f"工作目录：{workspace}\n命令：{subprocess.list2cmdline(command)}\n{details}")
    assert report.is_file(), details
    tree = ET.parse(report)
    cases = list(tree.iter("testcase"))
    assert cases, "必须实际执行测试，不能以未收集到测试冒充通过"
    assert not list(tree.iter("error")), details
    assert not list(tree.iter("skipped")), details
    return result, cases


def assert_green(workspace: Path, report: Path, *targets: str) -> list[ET.Element]:
    result, cases = run_pytest(workspace, report, *targets)
    assert result.returncode == 0, result.stdout + result.stderr
    assert all(not list(case) for case in cases), result.stdout + result.stderr
    return cases


@pytest.mark.parametrize(
    ("task_id", "acceptance_name", "topic"), SAMPLES, ids=[item[0] for item in SAMPLES]
)
def test_additional_sample_red_and_green(
    tmp_path: Path, task_id: str, acceptance_name: str, topic: str
) -> None:
    task_dir = ROOT / "benchmarks" / "tasks" / task_id
    source = task_dir / "repo"
    patch = ROOT / "benchmarks" / "solutions" / task_id / "reference.patch"
    acceptance = ROOT / "src" / "patchpilot" / "_acceptance" / acceptance_name
    assert (task_dir / "task.json").is_file(), f"缺少独立样例：{task_id}"
    task = load_task(task_dir / "task.json")
    assert task.spec.task_id == task_id
    assert task.snapshot_root == source.resolve()
    assert patch.is_file(), "缺少参考补丁"
    assert acceptance.is_dir(), "缺少维护者验收目录"
    assert {path.name for path in acceptance.iterdir()} == {f"test_acceptance_{topic}.py"}, (
        "维护者验收目录只能包含对应测试文件"
    )
    assert not any("acceptance" in path.name for path in source.rglob("*"))
    original = snapshot_contents(source)
    original_hash = compute_snapshot_hash(source)
    assert original_hash == task.spec.snapshot_sha256
    workspace = Path(shutil.copytree(source, tmp_path / "repo"))
    extra = Path(shutil.copytree(acceptance, tmp_path / "acceptance"))
    assert not extra.is_relative_to(workspace)

    try:
        normal_cases = assert_green(workspace, tmp_path / "normal-before.xml", "tests")
        red, red_cases = run_pytest(workspace, tmp_path / "red.xml", "tests", "repro")
        assert red.returncode == 1, red.stdout + red.stderr
        failures = [case for case in red_cases if case.find("failure") is not None]
        assert failures, "缺陷快照必须至少存在一个断言失败"
        assert len(red_cases) > len(normal_cases), "复现用例必须实际执行"
        for case in failures:
            failure = case.find("failure")
            assert failure is not None
            assert case.attrib["name"].startswith("test_repro_"), red.stdout
            assert (failure.text or "").rstrip().endswith(": AssertionError"), red.stdout
        assert snapshot_contents(workspace) == original, "红阶段不得修改快照"

        git = shutil.which("git")
        assert git is not None, "验证参考补丁需要安装 Git"
        patch.read_text(encoding="utf-8")
        for arguments in (["apply", "--check", str(patch)], ["apply", str(patch)]):
            command = [git, "-C", str(workspace), *arguments]
            applied = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8", timeout=10
            )
            print(f"命令：{subprocess.list2cmdline(command)}\n退出码：{applied.returncode}")
            assert applied.returncode == 0, applied.stdout + applied.stderr
        patched = snapshot_contents(workspace)
        assert patched.keys() == original.keys(), "补丁不得新增或删除文件"
        changed = [name for name in original if original[name] != patched[name]]
        assert changed, "参考补丁必须实际修改源码"
        assert all(name.startswith("src/") and name.endswith(".py") for name in changed), (
            "参考补丁只能修改已有源码"
        )

        normal_after = assert_green(workspace, tmp_path / "normal-after.xml", "tests")
        repro_after = assert_green(workspace, tmp_path / "repro-after.xml", "repro")
        extra_after = assert_green(workspace, tmp_path / "acceptance-after.xml", str(extra))
        assert len(normal_after) == len(normal_cases)
        assert len(normal_after) + len(repro_after) == len(red_cases)
        assert snapshot_contents(workspace) == patched, "绿阶段不得生成缓存或修改文件"
        print(
            f"{task_id}：正常 {len(normal_after)}，复现 {len(repro_after)}，"
            f"验收 {len(extra_after)}；红阶段断言失败 {len(failures)}"
        )
    finally:
        assert snapshot_contents(source) == original, "原始快照内容必须保持不变"
        assert compute_snapshot_hash(source) == original_hash, "原始快照哈希必须保持不变"

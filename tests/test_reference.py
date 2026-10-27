import os
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "benchmarks/tasks/pagination-001/repo"
PATCH = ROOT / "benchmarks/solutions/pagination-001/reference.patch"


def copy_snapshot(tmp_path: Path) -> Path:
    assert SOURCE.is_dir(), "缺少分页缺陷快照"
    return Path(shutil.copytree(SOURCE, tmp_path / "repo"))


def run_sample_tests(workspace: Path, report: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            "pytest.ini",
            "tests",
            "repro",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--junitxml={report}",
        ],
        cwd=workspace,
        env={
            **os.environ,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
        },
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )


def snapshot_contents(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_broken_snapshot_has_only_the_expected_reproduction_failure(tmp_path: Path) -> None:
    workspace = copy_snapshot(tmp_path)
    report = tmp_path / "baseline.xml"
    result = run_sample_tests(workspace, report)

    assert result.returncode == 1, result.stdout + result.stderr
    cases = list(ET.parse(report).iter("testcase"))
    assert len(cases) == 7
    assert not any(case.find("error") is not None for case in cases)
    assert not any(case.find("skipped") is not None for case in cases)
    assert [case.attrib["name"] for case in cases if case.find("failure") is not None] == [
        "test_pages_keep_records_with_equal_timestamps"
    ]


def test_reference_patch_fixes_the_sample_without_changing_the_original(tmp_path: Path) -> None:
    workspace = copy_snapshot(tmp_path)
    original = snapshot_contents(SOURCE)
    before = snapshot_contents(workspace)
    assert PATCH.is_file(), "缺少参考补丁"
    git = shutil.which("git")
    assert git is not None, "验证参考补丁需要安装 Git"

    for arguments in (["apply", "--check", str(PATCH)], ["apply", str(PATCH)]):
        result = subprocess.run(
            [git, "-C", str(workspace), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    after = snapshot_contents(workspace)
    assert before.keys() == after.keys()
    assert [name for name in before if before[name] != after[name]] == [
        "src/pagination_demo/paginator.py"
    ]
    report = tmp_path / "patched.xml"
    result = run_sample_tests(workspace, report)
    assert result.returncode == 0, result.stdout + result.stderr
    cases = list(ET.parse(report).iter("testcase"))
    assert len(cases) == 7
    assert all(not list(case) for case in cases)
    assert snapshot_contents(SOURCE) == original

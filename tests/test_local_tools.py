"""本地工具的文件边界、冲突保护与真实宿主命令测试。"""

import hashlib
import os
import threading
import time

import pytest

from patchpilot.control import ExecutionControl, ExecutionStopped
from patchpilot.local_tools import MAX_OUTPUT, LocalTools


def test_edit_conflict_protects_external_changes(tmp_path):
    path = tmp_path / "main.py"
    path.write_bytes(b"user = 1\r\n")
    tools = LocalTools(tmp_path)
    observed = tools.dispatch("read_file", {"path": "main.py"})
    path.write_bytes(b"user = 3\r\n")
    result = tools.dispatch(
        "edit_file",
        {
            "path": "main.py",
            "old_text": "1",
            "new_text": "2",
            "expected_hash": observed["sha256"],
        },
    )
    assert not result["ok"]
    assert path.read_bytes() == b"user = 3\r\n"
    # 猜中摘要也不能替代一次读取。
    result = tools.dispatch(
        "write_file",
        {
            "path": "main.py",
            "content": "覆盖",
            "expected_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
        },
    )
    assert not result["ok"]


def test_create_edit_and_diff_without_git(tmp_path):
    tools = LocalTools(tmp_path)
    assert tools.dispatch(
        "write_file",
        {
            "path": "nested/main.py",
            "content": "value = 1\n",
            "expected_hash": None,
        },
    )["ok"]
    read = tools.dispatch("read_file", {"path": "nested/main.py"})
    assert tools.dispatch(
        "edit_file",
        {
            "path": "nested/main.py",
            "old_text": "1",
            "new_text": "2",
            "expected_hash": read["sha256"],
        },
    )["ok"]
    diff = tools.get_diff()["diff"]
    assert "/dev/null" in diff and "+value = 2" in diff


@pytest.mark.parametrize(
    "path",
    [
        "../outside",
        "/absolute",
        "C:/Windows/file",
        "a\\b",
        ".env",
        ".env.local",
        ".git/config",
        "node_modules/code.js",
        "secret.pem",
        "a/../b",
        "a//b",
        "src/patchpilot/_acceptance/test.py",
        "benchmarks/solutions/answer.patch",
    ],
)
def test_forbidden_paths(tmp_path, path):
    tools = LocalTools(tmp_path)
    result = tools.dispatch("write_file", {"path": path, "content": "x", "expected_hash": None})
    assert not result["ok"]


def test_listing_search_and_read_bounds(tmp_path):
    (tmp_path / "visible.py").write_text("needle\n" * 300, encoding="utf-8")
    (tmp_path / ".env").write_text("needle=secret", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("needle", encoding="utf-8")
    tools = LocalTools(tmp_path)
    assert tools.dispatch("list_files", {})["files"] == ["visible.py"]
    result = tools.dispatch("search_text", {"query": "needle", "limit": 2})
    assert len(result["matches"]) == 2 and result["truncated"]
    assert tools.dispatch("read_file", {"path": "visible.py"})["truncated"]


def test_links_cannot_escape_repo(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    link = repo / "link"
    if os.name == "nt":
        import subprocess

        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)], check=True, capture_output=True
        )
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        tools = LocalTools(repo)
        assert not tools.dispatch(
            "write_file",
            {
                "path": "link/escape.py",
                "content": "bad",
                "expected_hash": None,
            },
        )["ok"]
        assert not (outside / "escape.py").exists()
        assert tools.dispatch("list_files", {})["files"] == []
    finally:
        link.rmdir() if os.name == "nt" else link.unlink()


def test_commands_require_explicit_permission(tmp_path):
    tools = LocalTools(tmp_path)
    assert "run_command" not in {item["name"] for item in tools.definitions()}
    assert not tools.dispatch("run_command", {"command": "echo 禁止"})["ok"]


def test_real_command_success_failure_output_and_cwd(tmp_path):
    tools = LocalTools(tmp_path, allow_commands=True)
    command = (
        "Write-Output '中文输出'; (Get-Location).Path"
        if os.name == "nt"
        else "printf '中文输出'; pwd"
    )
    result = tools.dispatch("run_command", {"command": command})
    assert result["ok"] and result["exit_code"] == 0
    assert "中文输出" in result["output"] and str(tmp_path) in result["output"]
    failed = tools.dispatch("run_command", {"command": "exit 7"})
    assert failed["exit_code"] == 7
    native = tools.dispatch(
        "run_command",
        {
            "command": "cmd /c exit 9" if os.name == "nt" else "sh -c 'exit 9'",
        },
    )
    assert native["exit_code"] == 9


def test_real_command_timeout_and_output_limit(tmp_path):
    tools = LocalTools(tmp_path, allow_commands=True)
    result = tools.dispatch(
        "run_command",
        {
            "command": "Start-Sleep -Seconds 10" if os.name == "nt" else "sleep 10",
            "timeout_seconds": 0.3,
        },
    )
    assert result["timed_out"] and result["exit_code"] != 0
    result = tools.dispatch(
        "run_command",
        {
            "command": "Write-Output ('x' * 30000)"
            if os.name == "nt"
            else "head -c 30000 /dev/zero | tr '\\0' x",
        },
    )
    assert result["truncated"] and len(result["output"]) <= MAX_OUTPUT


def test_command_cancellation_terminates_execution(tmp_path):
    control = ExecutionControl()
    tools = LocalTools(tmp_path, allow_commands=True, control=control)
    timer = threading.Timer(0.5, lambda: control.stop("cancelled"))
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(ExecutionStopped):
            tools.dispatch(
                "run_command",
                {
                    "command": "Start-Sleep -Seconds 10" if os.name == "nt" else "sleep 10",
                },
            )
        assert time.monotonic() - started < 8
    finally:
        timer.join()


@pytest.mark.skipif(os.name != "nt", reason="验证Windows Job Object的子进程回收")
def test_windows_command_timeout_kills_child_process(tmp_path):
    import ctypes
    import sys
    from ctypes import wintypes

    # 子进程先登记PID再阻塞，父命令超时时子进程必须同时终止。
    script = tmp_path / "child.py"
    script.write_text(
        "import os, pathlib, time\n"
        "pathlib.Path('child.pid').write_text(str(os.getpid()))\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    tools = LocalTools(tmp_path, allow_commands=True)
    result = tools.dispatch(
        "run_command",
        {
            "command": f"& '{sys.executable}' child.py",
            "timeout_seconds": 2.0,
        },
    )
    assert result["timed_out"]
    pid = int((tmp_path / "child.pid").read_text(encoding="utf-8"))
    # 不依赖可能被禁用的WMI/tasklist；直接检查进程等待句柄。
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenProcess.restype = wintypes.HANDLE
    api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    api.WaitForSingleObject.restype = wintypes.DWORD
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.OpenProcess(0x00100000, False, pid)
    if handle:
        try:
            assert api.WaitForSingleObject(handle, 0) == 0
        finally:
            api.CloseHandle(handle)
    else:
        assert ctypes.get_last_error() == 87  # 进程已经销毁，PID不存在。

"""有界进程执行：参数数组、输出尾部、时限、取消和进程树清理。"""

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from patchpilot.control import ExecutionControl, ExecutionStopped


@dataclass(frozen=True)
class ProcessResult:
    exit_code: int | None
    timed_out: bool
    output: str
    truncated: bool


class ProcessStartError(OSError):
    """进程尚未启动，允许调用方选择兼容的启动程序。"""


class ProcessInterrupted(ExecutionStopped):
    """中断后保留已收集的有界输出，供 Harness 保存执行证据。"""

    def __init__(self, reason: str, result: ProcessResult):
        super().__init__(reason)
        self.result = result


def run_process(
    argv: list[str],
    *,
    cwd: Path,
    timeout: float,
    max_output: int,
    control: ExecutionControl | None = None,
    env: dict[str, str] | None = None,
) -> ProcessResult:
    if control:
        control.check()
    output = bytearray()
    size = 0
    lock = threading.Lock()
    process = None
    reader = None
    job = None
    interruption = None
    timed_out = False

    def drain(stream):
        nonlocal size
        try:
            while data := stream.read(4096):
                with lock:
                    size += len(data)
                    output.extend(data)
                    del output[:-max_output]
        finally:
            stream.close()

    try:
        try:
            process = subprocess.Popen(
                argv,
                shell=False,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=os.name != "nt",
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            )
        except OSError as error:
            raise ProcessStartError(str(error)) from error
        if os.name == "nt":
            from patchpilot.process_job import WindowsJob

            job = WindowsJob()
            job.assign(process)
        reader = threading.Thread(target=drain, args=(process.stdout,), daemon=True)
        reader.start()
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if control:
                control.check()
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(0.025)
    except (KeyboardInterrupt, ExecutionStopped) as error:
        interruption = error
    finally:
        # 即使父进程已退出，也回收遗留子进程并关闭它们持有的输出管道。
        if job:
            job.close()
        elif process is not None and os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=10)
            if reader:
                reader.join(timeout=5)
            elif process.stdout:
                process.stdout.close()
    with lock:
        text = bytes(output).decode("utf-8", errors="replace")
    result = ProcessResult(
        None if timed_out or interruption else process.returncode,
        timed_out,
        text,
        size > max_output,
    )
    if interruption is not None:
        reason = interruption.reason if isinstance(interruption, ExecutionStopped) else "cancelled"
        raise ProcessInterrupted(reason, result) from interruption
    return result

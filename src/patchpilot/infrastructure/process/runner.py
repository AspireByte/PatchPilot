"""有界进程执行：参数数组、输出尾部、时限、取消和进程树清理。"""

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from patchpilot.core.control import ExecutionControl, ExecutionStopped


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
    """按参数数组启动子进程，等待退出或停止条件，并回收进程树。

    argv 指定程序与参数，cwd/env 指定工作目录与环境，timeout 为剩余执行秒数。
    max_output 按字节限制保留的输出尾部；stdout/stderr 合并读取，返回退出码与截断/超时标记。
    程序尚未启动时抛出 ProcessStartError，中断后抛出携带已收集结果的 ProcessInterrupted。
    进程生命周期控制不等于权限隔离；调用方仍需区分宿主执行与 Docker 容器执行。
    """
    # 共供宿主命令、宿主 Harness 和 Docker CLI 使用；返回退出码、有界输出及超时标记。
    # 这里控制进程生命周期，不提供文件系统或网络隔离；Docker 隔离由调用方的容器参数实现。
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
        # 内嵌函数共享 output；size 是外层局部变量，重新赋值前需用 nonlocal 声明。
        nonlocal size
        try:
            while data := stream.read(4096):
                with lock:
                    size += len(data)
                    output.extend(data)
                    # 持续排空管道以免子进程阻塞，但只保留末尾 max_output 字节，限制内存占用。
                    del output[:-max_output]
        finally:
            stream.close()

    try:
        try:
            process = subprocess.Popen(
                argv,
                # 参数数组不经 shell 解释；LocalTools 如需执行 shell，会显式把 shell 作为程序启动。
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
            from patchpilot.infrastructure.process.job import WindowsJob

            job = WindowsJob()
            job.assign(process)
        reader = threading.Thread(target=drain, args=(process.stdout,), daemon=True)
        # 读线程负责输出，主线程负责轮询时限和停止信号；两边通过锁协调输出缓冲区。
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

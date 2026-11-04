"""跨心跳线程与执行循环传播合作式停止，不以数据库状态代替进程中断。"""

from threading import Event, Lock


class ExecutionStopped(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class ExecutionControl:
    """可在线程间共享的合作式停止控制器，将取消或租约失效传播给执行点。

    stop 记录原因并设置 Event，stopped 只查询信号，check 检测到停止后抛出 ExecutionStopped。
    它不会自行杀进程；网络适配器、进程执行器和循环需要检查信号并各自完成清理。
    """

    # Event 传递跨线程停止信号，Lock 保护原因更新；停止是合作式的，需要执行点主动 check。
    # lease_lost 优先于 cancelled，避免普通取消原因掩盖旧 worker 已失去执行权这一事实。
    def __init__(self) -> None:
        self._event = Event()
        self._lock = Lock()
        self._reason = ""

    @property
    def stopped(self) -> bool:
        return self._event.is_set()

    def stop(self, reason: str) -> None:
        if reason not in ("cancelled", "lease_lost"):
            raise ValueError("不支持的执行停止原因")
        with self._lock:
            if not self._reason or reason == "lease_lost":
                self._reason = reason
            self._event.set()

    def check(self) -> None:
        if self._event.is_set():
            raise ExecutionStopped(self._reason)

"""跨心跳线程与执行循环传播合作式停止，不以数据库状态代替进程中断。"""

from threading import Event, Lock


class ExecutionStopped(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class ExecutionControl:
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

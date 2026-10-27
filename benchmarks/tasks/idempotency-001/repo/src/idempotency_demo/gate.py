"""单线程、非重入调用的内存请求去重器。"""

from collections.abc import Callable


class RequestGate:
    """每个实例独立记住成功完成的请求标识。"""

    def __init__(self) -> None:
        self._completed: set[str] = set()

    def run(self, request_id: str, callback: Callable[[], object]) -> bool:
        """首次成功返回 True，已完成的重复请求不执行且返回 False。"""
        if request_id in self._completed:
            return False
        self._completed.add(request_id)
        callback()
        return True

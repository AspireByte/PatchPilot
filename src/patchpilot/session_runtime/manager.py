"""Session 创建和恢复的轻量组合边界。"""

from __future__ import annotations


class SessionManager:
    """通过注入工厂创建 Session，暂不引入分支和后台任务。"""

    def __init__(self, factory):
        self.factory = factory

    def create(self, **kwargs):
        return self.factory(**kwargs)

    def resume(self, **kwargs):
        return self.factory(**kwargs)

"""Session 层的扩展边界。

状态编解码器会被持久化适配器导入，因此会话实现采用延迟导出，避免
``session_runtime`` 包初始化时反向加载 SessionStore 形成循环依赖。
"""

from .state import StateCodec, StateMigrator, UnsupportedStateVersion

__all__ = [
    "Session",
    "SessionManager",
    "AgentSession",
    "SessionResult",
    "StateCodec",
    "StateMigrator",
    "UnsupportedStateVersion",
]


def __getattr__(name):
    if name in {"AgentSession", "SessionResult"}:
        from .session import AgentSession, SessionResult

        return {"AgentSession": AgentSession, "SessionResult": SessionResult}[name]
    if name == "Session":
        from .api import Session

        return Session
    if name == "SessionManager":
        from .manager import SessionManager

        return SessionManager
    raise AttributeError(name)

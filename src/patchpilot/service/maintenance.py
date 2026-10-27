"""独立租约回收；只清理带本次Attempt标签的容器。"""

import logging

from patchpilot.sandbox import cleanup_attempt_containers

logger = logging.getLogger(__name__)


def reap_and_cleanup(repository, *, cleanup: bool = False) -> list[dict]:
    expired = repository.reap_expired()
    if cleanup:
        for attempt in expired:
            try:
                cleanup_attempt_containers(attempt["attempt_id"])
            except Exception:
                logger.warning(
                    "已标记执行中断，但容器清理失败；请检查Attempt %s", attempt["attempt_id"]
                )
    return expired

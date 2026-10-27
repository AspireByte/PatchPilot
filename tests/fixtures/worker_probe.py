"""只用于进程中断测试的阻塞执行器，不属于生产诊断或模型修复。"""

import sys
from pathlib import Path
from threading import Event

from patchpilot.service.config import ServiceSettings
from patchpilot.service.repository import Repository
from patchpilot.service.worker import Worker


class ProbeExecutor:
    kind = "test-process-probe"

    def execute(self, task, workspace, hooks, runner):
        hooks.event({"event": "tool_started", "call_id": "pending-read", "tool": "read_file"})
        Path(sys.argv[1]).write_text("ready", encoding="utf-8")
        while True:
            hooks.control.check()
            Event().wait(0.02)


if __name__ == "__main__":
    settings = ServiceSettings.from_env()
    repository = Repository(settings.database_url)
    try:
        Worker(repository, settings, mode="diagnostic", executor=ProbeExecutor()).run_once()
    finally:
        repository.close()

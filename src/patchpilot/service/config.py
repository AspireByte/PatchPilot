"""由宿主配置服务路径与凭据，不能从任务请求覆盖。"""

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ServiceSettings:
    database_url: str = field(repr=False)
    api_token: str = field(repr=False)
    data_root: Path = Path(".patchpilot-service")
    tasks_root: Path = Path("benchmarks/tasks")
    solutions_root: Path = Path("benchmarks/solutions")
    lease_seconds: float = 15
    heartbeat_seconds: float = 2
    reap_seconds: float = 2
    cleanup_expired: bool = False

    def __post_init__(self):
        if not self.database_url.startswith("postgresql+psycopg://"):
            raise ValueError("任务服务必须使用PostgreSQL psycopg连接")
        if len(self.api_token) < 16:
            raise ValueError("必须设置至少16字符的PATCHPILOT_API_TOKEN")
        if self.lease_seconds <= 0 or not 0 < self.heartbeat_seconds < self.lease_seconds / 2:
            raise ValueError("心跳间隔必须小于租约的一半")

    @classmethod
    def from_env(cls):
        return cls(
            database_url=os.environ.get(
                "PATCHPILOT_DATABASE_URL",
                "postgresql+psycopg://patchpilot:patchpilot-local-only@127.0.0.1:55432/patchpilot",
            ),
            api_token=os.environ.get("PATCHPILOT_API_TOKEN", ""),
            data_root=Path(os.environ.get("PATCHPILOT_DATA_ROOT", ".patchpilot-service")).resolve(),
            tasks_root=Path(os.environ.get("PATCHPILOT_TASKS_ROOT", "benchmarks/tasks")).resolve(),
            solutions_root=Path(
                os.environ.get("PATCHPILOT_SOLUTIONS_ROOT", "benchmarks/solutions")
            ).resolve(),
        )

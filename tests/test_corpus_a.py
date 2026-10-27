"""仅在临时副本验证八个独立缺陷任务的红绿闭环。"""

from pathlib import Path

import pytest
from corpus_helpers import verify_sample


@pytest.mark.parametrize(
    "task_id",
    [
        "money-001",
        "path-001",
        "retry-001",
        "merge-001",
        "ranking-001",
        "batching-001",
        "sql-001",
        "idempotency-001",
    ],
)
def test_corpus_a(task_id: str, tmp_path: Path) -> None:
    counts = verify_sample(task_id, tmp_path)
    assert counts == {"normal": 4, "repro": 2, "acceptance": 6}
    print(
        f"{task_id}：正常 {counts['normal']}，复现 {counts['repro']}，"
        f"维护者验收 {counts['acceptance']}"
    )

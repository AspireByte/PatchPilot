"""在临时副本中验证七个独立缺陷任务的红绿闭环。"""

from pathlib import Path

import pytest
from corpus_helpers import verify_sample

SAMPLES = (
    ("header-001", 3, 2, 5),
    ("datetime-001", 4, 2, 6),
    ("json-lines-001", 4, 2, 5),
    ("unicode-001", 3, 2, 5),
    ("percentile-001", 5, 2, 6),
    ("url-path-001", 4, 2, 5),
    ("csv-export-001", 4, 2, 6),
)


@pytest.mark.parametrize(
    ("task_id", "normal", "repro", "acceptance"),
    SAMPLES,
    ids=[sample[0] for sample in SAMPLES],
)
def test_corpus_b(tmp_path: Path, task_id: str, normal: int, repro: int, acceptance: int) -> None:
    counts = verify_sample(task_id, tmp_path)
    assert counts == {"normal": normal, "repro": repro, "acceptance": acceptance}
    print(f"{task_id}：正常 {normal}，复现 {repro}，维护者验收 {acceptance}")

"""提供按时间与编号排序的游标分页。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Row:
    id: int
    created_at: int


def paginate(
    rows: list[Row], *, cursor: tuple[int, int] | None = None, limit: int = 2
) -> list[Row]:
    """返回游标之后的一页记录，不修改传入列表。"""
    if limit <= 0:
        raise ValueError("每页数量必须大于零")

    ordered = sorted(rows, key=lambda row: (row.created_at, row.id))
    if cursor is not None:
        ordered = [row for row in ordered if row.created_at > cursor[0]]
    return ordered[:limit]

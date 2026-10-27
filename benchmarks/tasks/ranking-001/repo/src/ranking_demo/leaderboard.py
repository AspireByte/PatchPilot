"""生成按分数及标识排序的榜单。"""


def rank_records(records: list[dict]) -> list[dict]:
    """返回新列表，保留原记录对象和完全相同排序键的输入顺序。

    分数为整数，标识为字符串，其余字段不参与排序。
    """
    return sorted(
        records,
        key=lambda record: (record["score"], record["id"]),
        reverse=True,
    )

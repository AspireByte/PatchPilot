"""在独立 SQLite 内存库中筛选自建记录。"""

import sqlite3
from contextlib import closing


def visible_ids(records: list[tuple[int, str, int, int]], owner: str) -> list[int]:
    """记录字段依次为标识、所属者、启用标记、公开标记。"""
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.execute(
            "CREATE TABLE records (id INTEGER PRIMARY KEY, owner TEXT, active INTEGER, public INTEGER)"
        )
        connection.executemany("INSERT INTO records VALUES (?, ?, ?, ?)", records)
        rows = connection.execute(
            "SELECT id FROM records WHERE active = 1 AND owner = ? OR public = 1 ORDER BY id",
            (owner,),
        ).fetchall()
        return [row[0] for row in rows]

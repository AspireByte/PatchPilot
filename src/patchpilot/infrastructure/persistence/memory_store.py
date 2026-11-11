"""SQLite 长期记忆：作用范围、版本、全文索引与游标在事务内更新。"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections.abc import Sequence
from contextlib import contextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from uuid import uuid4

from patchpilot.core.memory import (
    GLOBAL_SCOPE,
    KINDS,
    MemoryCursor,
    MemoryRecord,
    MemorySource,
    MemoryUpdate,
)


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def tokenize(text: str) -> list[str]:
    """中文相邻二元词；英文同时索引完整标识符和驼峰/下划线分词。"""
    text = unicodedata.normalize("NFKC", text)
    terms = []
    for word in re.findall(r"[A-Za-z0-9_]+|[\u3400-\u9fff]+", text):
        if re.fullmatch(r"[\u3400-\u9fff]+", word):
            terms.extend(word[i : i + 2] for i in range(max(1, len(word) - 1)))
        else:
            terms.append(word.casefold())
            terms.extend(
                p.casefold() for p in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|\d+", word)
            )
    return list(dict.fromkeys(terms))


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class SQLiteMemoryStore:
    """调用方拥有连接；多个会话可共享实例，跨进程写入由 SQLite 串行化。"""

    def __init__(self, path: Path):
        self.location = str(path.expanduser().resolve())
        self._lock = RLock()
        self._closed = False
        Path(self.location).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(
            self.location, timeout=0.2, isolation_level=None, check_same_thread=False
        )
        self.db.row_factory = sqlite3.Row
        try:
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError("记忆数据库版本高于当前运行时")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY, scope TEXT NOT NULL, kind TEXT NOT NULL,
                    topic TEXT NOT NULL, summary TEXT NOT NULL, body TEXT NOT NULL,
                    version INTEGER NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    manual INTEGER NOT NULL, outcome TEXT NOT NULL, sources TEXT NOT NULL,
                    normalized TEXT NOT NULL, UNIQUE(scope, kind, topic)
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(id UNINDEXED, terms);
                CREATE TABLE IF NOT EXISTS revisions (
                    id TEXT NOT NULL, version INTEGER NOT NULL, record TEXT NOT NULL,
                    PRIMARY KEY(id, version)
                );
                CREATE TABLE IF NOT EXISTS cursors (
                    scope TEXT NOT NULL, session_id TEXT NOT NULL, position TEXT NOT NULL,
                    PRIMARY KEY(scope, session_id)
                );
                CREATE TABLE IF NOT EXISTS batches (
                    scope TEXT NOT NULL, session_id TEXT NOT NULL, id TEXT NOT NULL,
                    PRIMARY KEY(scope, session_id, id)
                );
                PRAGMA user_version=1;
                COMMIT;
            """)
        except BaseException:
            self.db.close()
            raise

    @contextmanager
    def _transaction(self):
        with self._lock:
            self.db.execute("BEGIN IMMEDIATE")
            try:
                yield
                self.db.execute("COMMIT")
            except BaseException:
                self.db.execute("ROLLBACK")
                raise

    @staticmethod
    def _record(row) -> MemoryRecord:
        if row is None:
            raise ValueError("找不到可访问的记忆")
        values = dict(row)
        values.pop("normalized", None)
        values["manual"] = bool(values["manual"])
        values["sources"] = tuple(MemorySource(**s) for s in json.loads(values["sources"]))
        return MemoryRecord(**values)

    def initialize_cursor(self, scope, session_id, initial):
        with self._transaction():
            self.db.execute(
                "INSERT OR IGNORE INTO cursors VALUES (?, ?, ?)",
                (scope, session_id, _json(asdict(initial))),
            )
            return self.cursor(scope, session_id)

    def cursor(self, scope, session_id):
        with self._lock:
            row = self.db.execute(
                "SELECT position FROM cursors WHERE scope=? AND session_id=?",
                (scope, session_id),
            ).fetchone()
            if row is None:
                raise ValueError("记忆提取水位尚未初始化")
            return MemoryCursor(**json.loads(row[0]))

    def read(self, scope, identifier):
        with self._lock:
            return self._record(
                self.db.execute(
                    "SELECT * FROM memories WHERE id=? AND scope IN (?, ?) AND status='active'",
                    (identifier, scope, GLOBAL_SCOPE),
                ).fetchone()
            )

    def preferences(self, *, limit=5):
        with self._lock:
            rows = self.db.execute(
                "SELECT * FROM memories WHERE scope=? AND kind='preference' AND status='active' "
                "ORDER BY manual DESC, updated_at DESC, id LIMIT ?",
                (GLOBAL_SCOPE, max(0, min(limit, 100))),
            ).fetchall()
            return [self._record(row) for row in rows]

    def search(self, scope, query, *, limit=5, include_global=True):
        terms = tokenize(query)[:64]
        if not terms or limit <= 0:
            return []
        scopes = (scope, GLOBAL_SCOPE if include_global else scope)
        with self._lock:
            if len(normalize(query).strip()) == 1:
                rows = self.db.execute(
                    "SELECT * FROM memories WHERE scope IN (?, ?) AND status='active' "
                    "AND instr(normalized, ?) > 0 ORDER BY updated_at DESC, id LIMIT ?",
                    (*scopes, normalize(query).strip(), min(limit, 100)),
                ).fetchall()
            else:
                match = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
                rows = self.db.execute(
                    "SELECT m.* FROM memory_fts JOIN memories m ON m.id=memory_fts.id "
                    "WHERE memory_fts MATCH ? AND m.scope IN (?, ?) AND m.status='active' "
                    "ORDER BY bm25(memory_fts), m.updated_at DESC, m.id LIMIT ?",
                    (match, *scopes, min(limit, 100)),
                ).fetchall()
            return [self._record(row) for row in rows]

    def _upsert(self, scope, update, *, manual=False):
        if (
            update.scope not in (scope, GLOBAL_SCOPE)
            or update.kind not in KINDS
            or (update.scope == GLOBAL_SCOPE and update.kind != "preference")
            or update.outcome not in ("success", "failure", "unknown")
            or not update.sources
        ):
            raise ValueError("记忆类型、来源或作用范围无效")
        topic = normalize(update.topic).strip()
        if not topic or len(topic) > 200:
            raise ValueError("记忆主题为空或过长")
        if not update.summary.strip() or not update.body.strip():
            raise ValueError("记忆正文与摘要不能为空")
        if len(update.summary) > 500 or len(update.body) > 8000:
            raise ValueError("记忆内容超过长度限制")
        old = self.db.execute(
            "SELECT * FROM memories WHERE scope=? AND kind=? AND topic=?",
            (update.scope, update.kind, topic),
        ).fetchone()
        if old and old["status"] == "deleted" and not manual:
            # 保留墓碑；迟到的提取批次不得自动恢复用户已忘记的主题。
            return None
        if not manual:
            if old:
                if old["id"] != update.id or old["version"] != update.expected_version:
                    raise ValueError("记忆版本冲突，未覆盖已有记录")
            elif update.id is not None or update.expected_version is not None:
                raise ValueError("更新目标不存在或主题不匹配")
        now = datetime.now(UTC).isoformat()
        record = MemoryRecord(
            id=old["id"] if old else str(uuid4()),
            scope=update.scope,
            kind=update.kind,
            topic=topic,
            summary=update.summary.strip(),
            body=update.body.strip(),
            version=old["version"] + 1 if old else 1,
            status="active",
            created_at=old["created_at"] if old else now,
            updated_at=now,
            manual=manual or bool(old and old["manual"]),
            outcome=update.outcome,
            sources=update.sources,
        )
        data = record.as_dict()
        data["sources"] = _json(data["sources"])
        data["normalized"] = normalize(f"{record.topic} {record.summary} {record.body}")
        columns = ", ".join(data)
        placeholders = ", ".join("?" for _ in data)
        self.db.execute(
            f"INSERT OR REPLACE INTO memories ({columns}) VALUES ({placeholders})",
            tuple(data.values()),
        )
        self.db.execute("DELETE FROM memory_fts WHERE id=?", (record.id,))
        self.db.execute(
            "INSERT INTO memory_fts VALUES (?, ?)",
            (record.id, " ".join(tokenize(f"{record.topic} {record.summary} {record.body}"))),
        )
        self.db.execute(
            "INSERT INTO revisions VALUES (?, ?, ?)",
            (record.id, record.version, _json(record.as_dict())),
        )
        return record

    def apply_batch(
        self,
        scope: str,
        session_id: str,
        batch_id: str,
        start: MemoryCursor,
        end: MemoryCursor,
        updates: Sequence[MemoryUpdate],
    ) -> bool:
        with self._transaction():
            if self.db.execute(
                "SELECT 1 FROM batches WHERE scope=? AND session_id=? AND id=?",
                (scope, session_id, batch_id),
            ).fetchone():
                return False
            if self.cursor(scope, session_id) != start:
                raise ValueError("记忆处理水位冲突")
            if (end.message, end.part, end.offset) <= (start.message, start.part, start.offset):
                raise ValueError("记忆处理水位没有前进")
            for update in updates:
                self._upsert(scope, update)
            self.db.execute(
                "UPDATE cursors SET position=? WHERE scope=? AND session_id=?",
                (_json(asdict(end)), scope, session_id),
            )
            self.db.execute("INSERT INTO batches VALUES (?, ?, ?)", (scope, session_id, batch_id))
            return True

    def remember(self, scope, update):
        with self._transaction():
            return self._upsert(scope, update, manual=True)

    def forget(self, scope, identifier):
        with self._transaction():
            old = self.read(scope, identifier)
            data = old.as_dict()
            data.update(
                status="deleted", version=old.version + 1, updated_at=datetime.now(UTC).isoformat()
            )
            self.db.execute(
                "UPDATE memories SET status='deleted', version=?, updated_at=? WHERE id=?",
                (data["version"], data["updated_at"], identifier),
            )
            self.db.execute("DELETE FROM memory_fts WHERE id=?", (identifier,))
            self.db.execute(
                "INSERT INTO revisions VALUES (?, ?, ?)", (identifier, data["version"], _json(data))
            )

    def count(self, scope):
        with self._lock:
            return self.db.execute(
                "SELECT count(*) FROM memories WHERE scope IN (?, ?) AND status='active'",
                (scope, GLOBAL_SCOPE),
            ).fetchone()[0]

    def close(self):
        with self._lock:
            if not self._closed:
                self.db.close()
                self._closed = True

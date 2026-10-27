"""仓储使用的六张 PostgreSQL 表；历史 Alembic 脚本独立维护。"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

run = Table(
    "run",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("task_id", String(128), nullable=False),
    Column("mode", String(16), nullable=False),
    Column("execution_status", String(24), nullable=False),
    Column("verdict", String(32), nullable=False, server_default="not_run"),
    Column("reason", String(1024), nullable=False, server_default=""),
    Column("budget", JSONB, nullable=False),
    Column("base_sha256", String(64), nullable=False),
    Column(
        "current_attempt_id",
        String(36),
        ForeignKey("attempt.id", name="fk_run_current_attempt", use_alter=True),
    ),
    Column(
        "latest_checkpoint_id",
        String(36),
        ForeignKey("checkpoint.id", name="fk_run_latest_checkpoint", use_alter=True),
    ),
    Column("cancel_requested", Boolean, nullable=False, server_default=text("false")),
    Column("event_seq", BigInteger, nullable=False, server_default="0"),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    ),
    Column("usage", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    CheckConstraint("mode IN ('agent', 'diagnostic')", name="ck_run_mode"),
    CheckConstraint(
        "execution_status IN ('queued','running','completed','failed','interrupted','cancelled')",
        name="ck_run_execution_status",
    ),
    CheckConstraint("event_seq >= 0", name="ck_run_event_seq"),
)
Index("ix_run_claim", run.c.mode, run.c.execution_status, run.c.created_at, run.c.id)

attempt = Table(
    "attempt",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("run_id", String(36), ForeignKey("run.id"), nullable=False),
    Column("worker_id", String(128), nullable=False),
    Column("token", String(128), nullable=False),
    Column("execution_status", String(24), nullable=False),
    Column("verdict", String(32), nullable=False, server_default="not_run"),
    Column("reason", String(1024), nullable=False, server_default=""),
    Column("usage", JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    Column(
        "checkpoint_id",
        String(36),
        ForeignKey("checkpoint.id", use_alter=True, name="fk_attempt_checkpoint"),
    ),
    Column("lease_until", DateTime(timezone=True)),
    Column(
        "started_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    ),
    Column("finished_at", DateTime(timezone=True)),
    CheckConstraint(
        "execution_status IN ('running','completed','failed','interrupted','cancelled')",
        name="ck_attempt_execution_status",
    ),
)
Index("ix_attempt_run", attempt.c.run_id, attempt.c.started_at, attempt.c.id)
Index("ix_attempt_lease", attempt.c.execution_status, attempt.c.lease_until)

step = Table(
    "step",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("run_id", String(36), ForeignKey("run.id"), nullable=False),
    Column("attempt_id", String(36), ForeignKey("attempt.id"), nullable=False),
    Column("key", String(256), nullable=False),
    Column("kind", String(64), nullable=False),
    Column("name", String(256), nullable=False),
    Column("status", String(24), nullable=False),
    Column("metadata", JSONB, nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    ),
    UniqueConstraint("attempt_id", "key", name="uq_step_attempt_key"),
    CheckConstraint(
        "status IN ('started','completed','failed','cancelled','unknown','skipped')",
        name="ck_step_status",
    ),
)
Index("ix_step_run", step.c.run_id, step.c.created_at, step.c.id)

event = Table(
    "event",
    metadata,
    Column("run_id", String(36), ForeignKey("run.id"), primary_key=True),
    Column("seq", BigInteger, primary_key=True),
    Column("attempt_id", String(36), ForeignKey("attempt.id")),
    Column("kind", String(64), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    ),
    CheckConstraint("seq > 0", name="ck_event_seq"),
)

artifact = Table(
    "artifact",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("run_id", String(36), ForeignKey("run.id"), nullable=False),
    Column("attempt_id", String(36), ForeignKey("attempt.id"), nullable=False),
    Column("kind", String(64), nullable=False),
    Column("relative_path", String(1024), nullable=False),
    Column("sha256", String(64), nullable=False),
    Column("size", BigInteger, nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    ),
    CheckConstraint("size >= 0", name="ck_artifact_size"),
)
Index("ix_artifact_run", artifact.c.run_id, artifact.c.created_at, artifact.c.id)

checkpoint = Table(
    "checkpoint",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("run_id", String(36), ForeignKey("run.id"), nullable=False),
    Column("attempt_id", String(36), ForeignKey("attempt.id"), nullable=False),
    Column("artifact_id", String(36), ForeignKey("artifact.id"), nullable=False),
    Column("base_sha256", String(64), nullable=False),
    Column("candidate_sha256", String(64), nullable=False),
    Column("metadata", JSONB, nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=text("clock_timestamp()"),
    ),
)
Index("ix_checkpoint_run", checkpoint.c.run_id, checkpoint.c.created_at, checkpoint.c.id)

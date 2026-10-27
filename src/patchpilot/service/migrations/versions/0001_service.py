"""阶段四持久化初始六表；历史结构不依赖实时 models。"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_service"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(128), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("execution_status", sa.String(24), nullable=False),
        sa.Column("verdict", sa.String(32), nullable=False, server_default="not_run"),
        sa.Column("reason", sa.String(1024), nullable=False, server_default=""),
        sa.Column("budget", postgresql.JSONB(), nullable=False),
        sa.Column("base_sha256", sa.String(64), nullable=False),
        sa.Column("current_attempt_id", sa.String(36)),
        sa.Column("latest_checkpoint_id", sa.String(36)),
        sa.Column(
            "cancel_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("event_seq", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column(
            "usage", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.CheckConstraint("mode IN ('agent', 'diagnostic')", name="ck_run_mode"),
        sa.CheckConstraint(
            "execution_status IN ('queued','running','completed','failed','interrupted','cancelled')",
            name="ck_run_execution_status",
        ),
        sa.CheckConstraint("event_seq >= 0", name="ck_run_event_seq"),
    )
    op.create_index("ix_run_claim", "run", ["mode", "execution_status", "created_at", "id"])
    op.create_table(
        "attempt",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("run.id"), nullable=False),
        sa.Column("worker_id", sa.String(128), nullable=False),
        sa.Column("token", sa.String(128), nullable=False),
        sa.Column("execution_status", sa.String(24), nullable=False),
        sa.Column("verdict", sa.String(32), nullable=False, server_default="not_run"),
        sa.Column("reason", sa.String(1024), nullable=False, server_default=""),
        sa.Column(
            "usage", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("checkpoint_id", sa.String(36)),
        sa.Column("lease_until", sa.DateTime(timezone=True)),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "execution_status IN ('running','completed','failed','interrupted','cancelled')",
            name="ck_attempt_execution_status",
        ),
    )
    op.create_index("ix_attempt_run", "attempt", ["run_id", "started_at", "id"])
    op.create_index("ix_attempt_lease", "attempt", ["execution_status", "lease_until"])
    op.create_table(
        "step",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("run.id"), nullable=False),
        sa.Column("attempt_id", sa.String(36), sa.ForeignKey("attempt.id"), nullable=False),
        sa.Column("key", sa.String(256), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.UniqueConstraint("attempt_id", "key", name="uq_step_attempt_key"),
        sa.CheckConstraint(
            "status IN ('started','completed','failed','cancelled','unknown','skipped')",
            name="ck_step_status",
        ),
    )
    op.create_index("ix_step_run", "step", ["run_id", "created_at", "id"])
    op.create_table(
        "event",
        sa.Column("run_id", sa.String(36), sa.ForeignKey("run.id"), primary_key=True),
        sa.Column("seq", sa.BigInteger(), primary_key=True),
        sa.Column("attempt_id", sa.String(36), sa.ForeignKey("attempt.id")),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint("seq > 0", name="ck_event_seq"),
    )
    op.create_table(
        "artifact",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("run.id"), nullable=False),
        sa.Column("attempt_id", sa.String(36), sa.ForeignKey("attempt.id"), nullable=False),
        sa.Column("kind", sa.String(64), nullable=False),
        sa.Column("relative_path", sa.String(1024), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
        sa.CheckConstraint("size >= 0", name="ck_artifact_size"),
    )
    op.create_index("ix_artifact_run", "artifact", ["run_id", "created_at", "id"])
    op.create_table(
        "checkpoint",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("run.id"), nullable=False),
        sa.Column("attempt_id", sa.String(36), sa.ForeignKey("attempt.id"), nullable=False),
        sa.Column("artifact_id", sa.String(36), sa.ForeignKey("artifact.id"), nullable=False),
        sa.Column("base_sha256", sa.String(64), nullable=False),
        sa.Column("candidate_sha256", sa.String(64), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("clock_timestamp()"),
        ),
    )
    op.create_index("ix_checkpoint_run", "checkpoint", ["run_id", "created_at", "id"])
    op.create_foreign_key(
        "fk_run_current_attempt", "run", "attempt", ["current_attempt_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_run_latest_checkpoint", "run", "checkpoint", ["latest_checkpoint_id"], ["id"]
    )
    op.create_foreign_key(
        "fk_attempt_checkpoint", "attempt", "checkpoint", ["checkpoint_id"], ["id"]
    )


def downgrade() -> None:
    op.drop_constraint("fk_attempt_checkpoint", "attempt", type_="foreignkey")
    op.drop_constraint("fk_run_latest_checkpoint", "run", type_="foreignkey")
    op.drop_constraint("fk_run_current_attempt", "run", type_="foreignkey")
    for table in ("checkpoint", "artifact", "event", "step", "attempt", "run"):
        op.drop_table(table)

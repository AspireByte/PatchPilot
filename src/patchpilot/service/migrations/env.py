"""只接收调用方提供的连接，避免读取凭据或输出数据库 URL。"""

from alembic import context

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("请通过 patchpilot.service.database.migrate 提供迁移连接")

context.configure(connection=connection, target_metadata=None, compare_type=True)
with context.begin_transaction():
    context.run_migrations()

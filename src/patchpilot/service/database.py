"""仅面向 PostgreSQL 的引擎与包内 Alembic 升级入口。"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url


def make_engine(database_url: str) -> Engine:
    url = make_url(database_url)
    if url.get_backend_name() != "postgresql":
        raise ValueError("持久化服务仅支持 PostgreSQL")
    return create_engine(url, pool_pre_ping=True, echo=False, hide_parameters=True)


def migrate(database_url: str) -> None:
    """复用事务连接运行包内迁移，不将连接串写入 Alembic 配置或日志。"""
    engine = make_engine(database_url)
    try:
        with engine.begin() as connection:
            # 同一数据库、同一 schema 的并发启动只允许一个迁移执行者。
            connection.execute(
                text(
                    "SELECT pg_advisory_xact_lock("
                    "hashtext(current_database()), "
                    "hashtext('patchpilot:migrate:' || current_schema()))"
                )
            )
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).with_name("migrations")))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    finally:
        engine.dispose()

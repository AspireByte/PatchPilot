"""真实 PostgreSQL 测试夹具；每测试创建独立随机 schema，结束后保留数据。"""

import os
import uuid
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://patchpilot:patchpilot-local-only@127.0.0.1:55432/patchpilot"
)


@pytest.fixture
def pg_repository(request) -> Iterator:
    """测试文件显式 ``from postgres_helpers import pg_repository`` 即可使用。"""
    if not request.config.getoption("--run-postgres", default=False):
        pytest.skip("需要显式传入 --run-postgres 执行真实 PostgreSQL 测试")

    from patchpilot.service.database import migrate
    from patchpilot.service.repository import Repository

    url = make_url(os.environ.get("PATCHPILOT_TEST_DATABASE_URL", DEFAULT_DATABASE_URL))
    schema = f"pp_test_{uuid.uuid4().hex}"
    request.node.user_properties.append(("postgres_schema", schema))
    admin = create_engine(url.difference_update_query(["options"]), echo=False)
    repository = None
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        isolated_url = url.update_query_dict({"options": f"-csearch_path={schema}"})
        database_url = isolated_url.render_as_string(hide_password=False)
        migrate(database_url)
        repository = Repository(database_url)
        yield repository
    finally:
        try:
            if repository is not None:
                repository.close()
        finally:
            # 随机 schema 故意保留；后续清理须核对具体目标并另行取得授权。
            admin.dispose()

import pytest


@pytest.fixture(autouse=True)
def isolate_temporary_git_discovery(tmp_path, monkeypatch):
    """基准副本不应继承父项目的 Git 根，否则 git apply 会静默跳过补丁。"""
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.resolve()))


def pytest_addoption(parser) -> None:
    parser.addoption("--run-docker", action="store_true", help="显式运行真实 Linux 容器测试")
    parser.addoption("--run-postgres", action="store_true", help="显式运行真实 PostgreSQL 集成测试")


def pytest_collection_modifyitems(config, items) -> None:
    for item in items:
        for name in ("docker", "postgres"):
            if name in item.keywords and not config.getoption(f"--run-{name}"):
                item.add_marker(
                    pytest.mark.skip(reason=f"需要显式传入 --run-{name} 执行真实集成测试")
                )

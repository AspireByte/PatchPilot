"""API/Worker独立进程入口；只监听本机，真实模型需再次显式授权。"""

import argparse
import json
import sys

import uvicorn
from sqlalchemy.exc import SQLAlchemyError

from patchpilot.service.api import create_app
from patchpilot.service.config import ServiceSettings
from patchpilot.service.database import migrate
from patchpilot.service.maintenance import reap_and_cleanup
from patchpilot.service.repository import Repository
from patchpilot.service.worker import Worker


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="patchpilot-service", description="本机受控任务API与独立Worker"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("migrate", help="升级本项目PostgreSQL结构，不删除数据")
    serve = commands.add_parser("serve", help="在127.0.0.1启动任务API")
    serve.add_argument("--port", type=int, default=8000)
    worker_parser = commands.add_parser("worker", help="启动独立Worker进程")
    worker_parser.add_argument("--once", action="store_true", help="只领取至多一个任务")
    mode = worker_parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--diagnostic",
        action="store_true",
        help="只执行显式诊断任务：参考补丁＋Docker，绝不算AI修复",
    )
    mode.add_argument(
        "--allow-api",
        action="store_true",
        help="允许本Worker处理排队的Agent任务、发送代码并产生费用",
    )
    commands.add_parser("reap", help="识别过期租约，不自动批量删除容器")
    arguments = parser.parse_args(argv)
    try:
        settings = ServiceSettings.from_env()
        if arguments.command == "migrate":
            migrate(settings.database_url)
            print("数据库结构升级完成。")
            return 0
        repository = Repository(settings.database_url)
        try:
            if arguments.command == "serve":
                if not 1 <= arguments.port <= 65535:
                    raise ValueError("端口必须在1到65535之间")
                uvicorn.run(
                    create_app(settings, repository=repository),
                    host="127.0.0.1",
                    port=arguments.port,
                )
            elif arguments.command == "reap":
                print(json.dumps(reap_and_cleanup(repository), ensure_ascii=False))
            else:
                worker = Worker(
                    repository,
                    settings,
                    mode="diagnostic" if arguments.diagnostic else "agent",
                    allow_api=arguments.allow_api,
                )
                if arguments.once:
                    print(json.dumps({"claimed": worker.run_once()}, ensure_ascii=False))
                else:
                    worker.run_forever()
        finally:
            repository.close()
    except (ValueError, OSError):
        print("服务配置或运行条件不符合要求；请检查本机数据库、令牌和授权参数。", file=sys.stderr)
        return 2
    except SQLAlchemyError:
        print("数据库暂不可用，请检查独立PostgreSQL服务与迁移。", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("服务已停止；未完成任务将由租约回收标记为中断。", file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

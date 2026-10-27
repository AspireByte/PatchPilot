"""PatchPilot 的唯一用户命令入口。"""

import argparse
import math

from patchpilot.chat_cli import add_chat_parser, run_chat


def _positive_timeout(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("超时必须为有限正数") from error
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("超时必须为有限正数")
    return seconds


def _positive_integer(value: str) -> int:
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("预算必须是正整数") from error
    if number <= 0:
        raise argparse.ArgumentTypeError("预算必须是正整数")
    return number


def _add_help(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("-h", "--help", action="help", help="显示帮助信息并退出")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="patchpilot",
        description="本地仓库持续对话与按需 Harness 检查。",
        add_help=False,
    )
    _add_help(parser)
    commands = parser.add_subparsers(dest="command", title="命令")
    add_chat_parser(commands, _add_help, _positive_integer, _positive_timeout)
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        parser.print_help()
        return 0
    if arguments.command == "chat":
        try:
            return run_chat(arguments)
        except (OSError, ValueError) as error:
            print(f"启动失败：{error}")
            return 2
    parser.error(f"未知命令：{arguments.command}")
    return 2

"""命令行入口。"""

import argparse
import json
import math
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from patchpilot.agent import AgentBudget, run_agent
from patchpilot.anthropic_model import DEFAULT_MODEL, AnthropicModel
from patchpilot.chat_cli import add_chat_parser, run_chat
from patchpilot.patching import MAX_PATCH_BYTES
from patchpilot.sandbox import DEFAULT_IMAGE, DockerRunner, SandboxError, SandboxLimits
from patchpilot.task import TaskValidationError, load_task, to_agent_input
from patchpilot.verification import verify_patch


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
        prog="patchpilot", description="加载、执行与验证受控 Python 代码修复任务。", add_help=False
    )
    _add_help(parser)
    commands = parser.add_subparsers(dest="command", title="命令")
    add_chat_parser(commands, _add_help, _positive_integer, _positive_timeout)
    validate = commands.add_parser("validate-task", help="校验任务描述及代码快照", add_help=False)
    _add_help(validate)
    validate.add_argument("manifest", type=Path, metavar="任务描述", help="task.json 的路径")
    validate.add_argument("--agent-input", action="store_true", help="仅输出面向 Agent 的公开 JSON")
    check = commands.add_parser(
        "sandbox-check", help="检查 Linux 引擎与本地测试镜像", add_help=False
    )
    _add_help(check)
    check.add_argument("--image", default=DEFAULT_IMAGE, help="已构建的本地测试镜像")
    verify = commands.add_parser("verify-patch", help="从原始快照独立验收补丁", add_help=False)
    _add_help(verify)
    verify.add_argument("manifest", type=Path, metavar="任务描述")
    verify.add_argument("patch", type=Path, metavar="补丁文件")
    verify.add_argument(
        "--runs-dir", type=Path, default=Path(".patchpilot-runs"), help="执行产物目录"
    )
    verify.add_argument("--image", default=DEFAULT_IMAGE, help="已构建的本地测试镜像")
    verify.add_argument("--timeout", type=_positive_timeout, default=30.0, help="测试超时秒数")
    agent = commands.add_parser(
        "agent-run", help="运行单模型修复循环（发送任务内容并产生费用）", add_help=False
    )
    _add_help(agent)
    agent.add_argument("manifest", type=Path, metavar="任务描述")
    agent.add_argument(
        "--allow-api", action="store_true", help="确认发送公开任务内容到配置的API并承担费用"
    )
    agent.add_argument("--model", default=DEFAULT_MODEL, help="单一模型标识，不自动切换")
    agent.add_argument("--image", default=DEFAULT_IMAGE, help="本地预构建镜像")
    agent.add_argument("--runs-dir", type=Path, default=Path(".patchpilot-runs"))
    agent.add_argument(
        "--timeout", type=_positive_timeout, default=180.0, help="总时间预算秒数（清理另有宽限）"
    )
    agent.add_argument("--max-model-calls", type=_positive_integer, default=8)
    agent.add_argument("--max-tool-calls", type=_positive_integer, default=32)
    agent.add_argument(
        "--max-tokens",
        type=_positive_integer,
        default=100000,
        help="已知累计token用量停止阈值，不是金额硬上限",
    )
    agent.add_argument("--max-output-tokens", type=_positive_integer, default=4096)
    arguments = parser.parse_args(argv)
    if arguments.command is None:
        parser.print_help()
        return 0
    try:
        if arguments.command == "chat":
            return run_chat(arguments)
        if arguments.command == "sandbox-check":
            print(
                json.dumps(
                    DockerRunner(image=arguments.image).check(), ensure_ascii=False, indent=2
                )
            )
            return 0
        task = load_task(arguments.manifest)
        if arguments.command == "validate-task":
            if arguments.agent_input:
                print(to_agent_input(task).model_dump_json(indent=2))
            else:
                print(f"任务校验通过：{task.spec.task_id}，快照 SHA-256 与描述一致。")
            return 0
        if arguments.command == "agent-run":
            if not arguments.allow_api:
                print(
                    "真实模型会发送公开任务内容并产生费用；请显式添加 --allow-api。",
                    file=sys.stderr,
                )
                return 2
            budget = AgentBudget(
                max_model_calls=arguments.max_model_calls,
                max_tool_calls=arguments.max_tool_calls,
                timeout_seconds=arguments.timeout,
                max_total_tokens=arguments.max_tokens,
                max_output_tokens=arguments.max_output_tokens,
            )
            runner = DockerRunner(image=arguments.image)
            runner.check()
            try:
                model = AnthropicModel(model=arguments.model)
            except (RuntimeError, ValueError):
                print(
                    "模型客户端初始化失败，请检查本机API认证设置，不要把密钥写入任务文件。",
                    file=sys.stderr,
                )
                return 2
            try:
                result = run_agent(
                    task,
                    arguments.runs_dir,
                    model,
                    runner,
                    budget,
                    on_event=lambda event: print(
                        json.dumps(event, ensure_ascii=False), file=sys.stderr, flush=True
                    ),
                )
            finally:
                model.close()
            print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
            return 0 if result.status == "passed" else 1
        try:
            with arguments.patch.open("rb") as stream:
                patch = stream.read(MAX_PATCH_BYTES + 1)
        except OSError as error:
            print(f"无法读取补丁：{error}", file=sys.stderr)
            return 2
        if len(patch) > MAX_PATCH_BYTES:
            print("补丁超过 1 MiB 限制", file=sys.stderr)
            return 2
        runner = DockerRunner(
            image=arguments.image, limits=SandboxLimits(timeout_seconds=arguments.timeout)
        )
        result = verify_patch(task, patch, arguments.runs_dir, runner)
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        if result.status == "passed":
            return 0
        return 2 if result.status == "infra_error" else 1
    except TaskValidationError as error:
        print(f"任务校验失败：{error}", file=sys.stderr)
    except (OSError, ValueError, SandboxError, subprocess.SubprocessError) as error:
        heading = "沙箱检查失败" if arguments.command == "sandbox-check" else "执行失败"
        print(f"{heading}：{error}", file=sys.stderr)
    return 2

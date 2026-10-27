"""交互终端入口与显式环境文件加载。"""

import os
import re
from contextlib import ExitStack
from pathlib import Path

from patchpilot.anthropic_model import DEFAULT_MODEL, AnthropicModel
from patchpilot.runtime import AgentBudget
from patchpilot.session import HELP, AgentSession
from patchpilot.session_store import SessionStore


def add_chat_parser(commands, add_help, positive_integer, positive_timeout) -> None:
    parser = commands.add_parser("chat", help="启动本地仓库持续对话", add_help=False)
    add_help(parser)
    parser.add_argument("--repo", type=Path, help="直接操作的本地仓库；新会话必填")
    parser.add_argument("--resume", help="恢复会话编号；不会自动继续目标或重放命令")
    parser.add_argument(
        "--sessions-dir",
        type=Path,
        default=Path(".patchpilot-sessions"),
        help="会话保存目录，默认相对启动目录",
    )
    parser.add_argument("--allow-api", action="store_true", help="允许发送代码到模型API并产生费用")
    parser.add_argument(
        "--allow-commands",
        action="store_true",
        help="允许本次启动自动执行宿主命令（不是Docker沙箱）",
    )
    parser.add_argument(
        "--allow-harness",
        action="store_true",
        help="允许模型按需运行已配置的受控项目检查",
    )
    parser.add_argument(
        "--harness-config",
        type=Path,
        help="Harness TOML配置，默认读取仓库/.patchpilot/harness.toml",
    )
    parser.add_argument(
        "--harness-runtime",
        choices=("auto", "docker", "host"),
        default=None,
        help="Harness执行环境，默认优先Docker并按配置降级宿主",
    )
    parser.add_argument("--model", help="工作模型名称；恢复时默认沿用记录")
    parser.add_argument("--evaluator-model", help="评估模型名称，默认与工作模型相同")
    parser.add_argument("--env-file", type=Path, help="显式加载环境文件，不覆盖已有环境变量")
    parser.add_argument("--max-model-calls", type=positive_integer, default=24)
    parser.add_argument("--max-tool-calls", type=positive_integer, default=64)
    parser.add_argument(
        "--max-tokens",
        type=positive_integer,
        default=100000,
        help="已知token停止阈值，不是金额硬上限",
    )
    parser.add_argument("--max-output-tokens", type=positive_integer, default=4096)
    parser.add_argument("--max-context-chars", type=positive_integer, default=120000)
    parser.add_argument("--timeout", type=positive_timeout, default=600.0)
    parser.add_argument("--max-continuations", type=positive_integer, default=8)


def load_env_file(path: Path) -> None:
    """读取单行 KEY=VALUE；支持引号和export，不展开变量或执行脚本。"""
    values = {}
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ValueError(f"环境文件第{number}行格式无效，应为 KEY=VALUE")
        if value.startswith(("'", '"')):
            quote = value[0]
            end = value.find(quote, 1)
            if end < 0 or (
                value[end + 1 :].strip() and not value[end + 1 :].strip().startswith("#")
            ):
                raise ValueError(f"环境文件第{number}行引号无效；仅支持单行值")
            value = value[1:end]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
        values[key] = value
    for key, value in values.items():
        os.environ.setdefault(key, value)


def show_event(event: dict) -> None:
    kind = event["event"]
    if kind == "assistant_text":
        print(event["text"], flush=True)
    elif kind == "harness_started":
        print(f"[Harness开始] profile={event['profile']}", flush=True)
    elif kind == "harness_finished":
        print(
            f"[Harness完成] {event['status']} / {event['runtime']} / 退出码 {event['exit_code']}",
            flush=True,
        )
    elif kind == "model_started":
        print("[目标评估中]" if event["kind"] == "evaluator" else "[模型处理中]", flush=True)
    elif kind == "tool_started":
        command = event.get("command")
        print(f"[工具] {event['tool']}" + (f"\n{command}" if command else ""), flush=True)
    elif kind == "tool_finished":
        result = event["result"]
        if "harness" in result:
            harness = result["harness"]
            print(
                f"[Harness] {harness['status']} / {harness['runtime']} / "
                f"退出码 {harness['exit_code']}",
                flush=True,
            )
            if harness.get("output"):
                print(harness["output"], flush=True)
            return
        if "exit_code" in result:
            print(
                f"[命令退出码] {result['exit_code']}" + ("（超时）" if result["timed_out"] else ""),
                flush=True,
            )
            if result["output"]:
                print(result["output"], flush=True)
            if result["truncated"]:
                print("[输出已截断，仅显示末尾]", flush=True)
        elif not result["ok"]:
            print(f"[工具失败] {result['error']}", flush=True)


def run_chat(arguments) -> int:
    if not arguments.allow_api:
        print("真实模型会发送代码并产生费用；请显式添加 --allow-api。")
        return 2
    if not arguments.resume and arguments.repo is None:
        raise ValueError("新会话需要 --repo")
    if arguments.env_file:
        load_env_file(arguments.env_file)
    budget = AgentBudget(
        max_model_calls=arguments.max_model_calls,
        max_tool_calls=arguments.max_tool_calls,
        timeout_seconds=arguments.timeout,
        max_total_tokens=arguments.max_tokens,
        max_context_chars=arguments.max_context_chars,
        max_output_tokens=arguments.max_output_tokens,
    )
    with ExitStack() as stack:
        store = SessionStore(arguments.sessions_dir, arguments.resume)
        stack.callback(store.close)
        saved = store.load() if arguments.resume else {}
        model_name = arguments.model or saved.get("model") or DEFAULT_MODEL
        evaluator_name = (
            arguments.evaluator_model
            or (saved.get("evaluator_model") if not arguments.model else None)
            or model_name
        )
        try:
            model = AnthropicModel(model=model_name)
            stack.callback(model.close)
            evaluator = AnthropicModel(model=evaluator_name, thinking=False)
            stack.callback(evaluator.close)
        except (RuntimeError, ValueError) as error:
            raise ValueError("模型初始化失败，请检查API认证与Base URL设置") from error
        session = AgentSession(
            arguments.repo,
            model,
            evaluator_model=evaluator,
            store=store,
            allow_commands=arguments.allow_commands,
            allow_harness=arguments.allow_harness,
            harness_config=arguments.harness_config,
            harness_runtime=arguments.harness_runtime,
            budget=budget,
            max_continuations=arguments.max_continuations,
            on_event=show_event,
        )
        try:
            while True:
                print(f"\n会话：{session.session_id}\n仓库：{session.tools.repo}")
                print(f"记录：{session.store.directory}\n{HELP}")
                if arguments.allow_commands:
                    print("已允许自动执行宿主命令；命令拥有当前用户的环境访问能力。")
                if arguments.allow_harness:
                    print("已允许模型按需运行Harness检查；结果会标记隔离或宿主证据。")
                while True:
                    try:
                        text = input("你 > ")
                    except (EOFError, KeyboardInterrupt):
                        print("\n会话已保存。")
                        return 0
                    result = session.submit(text)
                    if result.status == "exit":
                        print("会话已保存。")
                        return 0
                    if result.status == "new":
                        session.close()
                        session = AgentSession(
                            session.tools.repo,
                            model,
                            evaluator_model=evaluator,
                            sessions_root=arguments.sessions_dir,
                            allow_commands=arguments.allow_commands,
                            allow_harness=arguments.allow_harness,
                            harness_config=arguments.harness_config,
                            harness_runtime=arguments.harness_runtime,
                            budget=budget,
                            max_continuations=arguments.max_continuations,
                            on_event=show_event,
                        )
                        break
                    if result.status in ("status", "error", "cleared") and result.text:
                        print(result.text)
                    if result.reason:
                        print(f"[{result.status}] {result.reason}")
        finally:
            session.close()

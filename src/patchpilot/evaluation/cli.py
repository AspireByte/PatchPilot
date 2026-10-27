"""评测命令；真实模型运行与候选数据批准都必须由操作者显式开启。"""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from patchpilot.agent import AgentBudget
from patchpilot.anthropic_model import DEFAULT_MODEL
from patchpilot.evaluation.metrics import Pricing
from patchpilot.evaluation.runner import BatchConfig, run_batch, save_summary
from patchpilot.evaluation.validation import audit_dataset, validate_tasks, write_review


def _positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("必须为正整数")
    return number


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="patchpilot-eval", description="自建基准校验、单次基线与Agent对照评测"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    listing = commands.add_parser("list", help="查看候选任务资料与审批指纹，不调用模型")
    validate = commands.add_parser("validate", help="无模型费用的任务容器红绿校验")
    run = commands.add_parser("run", help="显式启动真实模型评测，发送任务内容并产生费用")
    report = commands.add_parser("report", help="从计划与逐项结果重新生成完整分母摘要")
    for command in (listing, validate, run):
        command.add_argument("--tasks-root", type=Path, default=Path("benchmarks/tasks"))
        command.add_argument("--solutions-root", type=Path, default=Path("benchmarks/solutions"))
    listing.add_argument("--review-path", type=Path, help="输出全部Issue与评分规则供人工审阅")
    for command in (validate, run):
        command.add_argument("--tasks", help="显式逗号分隔任务ID，不接受路径")
        command.add_argument("--output", type=Path, default=Path(".patchpilot-evals"))
    run.add_argument("--split", choices=("development", "holdout", "all"), default="development")
    run.add_argument("--strategy", choices=("baseline", "agent", "both"), default="both")
    run.add_argument("--repeat", type=_positive, default=1)
    run.add_argument("--max-trials", type=_positive, default=40)
    run.add_argument("--max-model-calls", type=_positive, default=8)
    run.add_argument("--max-tool-calls", type=_positive, default=32)
    run.add_argument("--max-output-tokens", type=_positive, default=4096)
    run.add_argument("--max-tokens", type=_positive, default=100000)
    run.add_argument("--timeout", type=_positive, default=180)
    run.add_argument("--model", default=DEFAULT_MODEL)
    run.add_argument("--allow-api", action="store_true", help="确认发送公开任务内容并承担模型费用")
    run.add_argument("--approve-dataset", help="人工审阅后显式批准list输出的dataset_sha256")
    run.add_argument(
        "--confirm-holdout", action="store_true", help="确认保留集不用来调参，且构建者已知并非盲测"
    )
    for command in (run, report):
        command.add_argument("--pricing", type=Path, help="显式服务定价JSON；缺失时费用未知")
    report.add_argument("batch_path", type=Path)
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "list":
            result = audit_dataset(arguments.tasks_root, arguments.solutions_root)
            if arguments.review_path:
                write_review(arguments.review_path, arguments.tasks_root)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if not result["material_errors"] else 1
        pricing = (
            Pricing(**json.loads(arguments.pricing.read_text(encoding="utf-8")))
            if getattr(arguments, "pricing", None)
            else None
        )
        if arguments.command == "report":
            print(
                json.dumps(
                    save_summary(arguments.batch_path, pricing), ensure_ascii=False, indent=2
                )
            )
            return 0
        tasks = arguments.tasks.split(",") if arguments.tasks else None
        if arguments.command == "validate":
            result = validate_tasks(
                arguments.tasks_root, arguments.solutions_root, arguments.output, task_ids=tasks
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["valid"] == result["total"] else 1
        if not arguments.allow_api:
            print(
                "真实评测需要--allow-api，并在审阅候选输入后提供--approve-dataset指纹。",
                file=sys.stderr,
            )
            return 2
        strategies = (
            ("baseline", "agent") if arguments.strategy == "both" else (arguments.strategy,)
        )
        budget = AgentBudget(
            max_model_calls=arguments.max_model_calls,
            max_tool_calls=arguments.max_tool_calls,
            timeout_seconds=arguments.timeout,
            max_total_tokens=arguments.max_tokens,
            max_output_tokens=arguments.max_output_tokens,
        )
        config = BatchConfig(
            task_ids=tuple(tasks) if tasks else None,
            split=arguments.split,
            strategies=strategies,
            repeats=arguments.repeat,
            model=arguments.model,
            budget=budget,
            max_trials=arguments.max_trials,
        )
        result = run_batch(
            arguments.tasks_root,
            arguments.solutions_root,
            arguments.output,
            config=config,
            allow_api=True,
            approval_hash=arguments.approve_dataset,
            confirm_holdout=arguments.confirm_holdout,
            pricing=pricing,
        )
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
        return 0
    except (ValueError, TypeError, OSError):
        print(
            "评测配置或资料无效：请核对任务选择、预算、定价和--approve-dataset审批指纹。",
            file=sys.stderr,
        )
        return 2
    except KeyboardInterrupt:
        print(
            "评测已中断；已完成记录与完整计划仍保留，请用report查看（不会删除未完成分母）。",
            file=sys.stderr,
        )
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

"""计划先行、逐项落盘、冻结版本的本地批量评测，不自动重试或排除失败。"""

import hashlib
import json
import os
import platform
import re
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from time import monotonic

from patchpilot.agent import SYSTEM_PROMPT, AgentBudget, ModelFailure, run_agent
from patchpilot.anthropic_model import DEFAULT_MODEL, AnthropicModel
from patchpilot.evaluation.baseline import BASELINE_SYSTEM, RETRIEVAL_POLICY, run_baseline
from patchpilot.evaluation.catalog import acceptance_directory, load_dataset
from patchpilot.evaluation.metrics import TOKEN_FIELDS, Pricing, markdown_summary, summarize
from patchpilot.sandbox import DEFAULT_IMAGE, DockerRunner
from patchpilot.snapshot import compute_snapshot_hash
from patchpilot.task import load_task
from patchpilot.tools import tool_definitions
from patchpilot.workspace import create_workspace


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    os.replace(temporary, path)


def code_fingerprint() -> str:
    root = Path(__file__).resolve().parents[1]
    records = [
        (path.relative_to(root).as_posix(), _sha(path.read_bytes()))
        for path in sorted(root.rglob("*.py"))
        if "__pycache__" not in path.parts
    ]
    return _sha(json.dumps(records, separators=(",", ":")).encode())


@dataclass(frozen=True)
class BatchConfig:
    task_ids: tuple[str, ...] | None = None
    split: str = "development"
    strategies: tuple[str, ...] = ("baseline", "agent")
    repeats: int = 1
    model: str = DEFAULT_MODEL
    budget: AgentBudget = field(default_factory=AgentBudget)
    max_trials: int = 40
    fixture: bool = False

    def __post_init__(self):
        if (
            not self.strategies
            or len(set(self.strategies)) != len(self.strategies)
            or set(self.strategies) - {"baseline", "agent"}
        ):
            raise ValueError("不支持的评测策略")
        if (
            type(self.repeats) is not int
            or not 1 <= self.repeats <= 10
            or not 1 <= self.max_trials <= 200
        ):
            raise ValueError("重复次数或总尝试上限不合法")


@dataclass(frozen=True)
class BatchResult:
    batch_path: str
    summary: dict


class AuditedModel:
    def __init__(self, model, requested: str, root: Path):
        self.base = model
        self.kind = model.kind
        self.requested = requested
        self.root = root
        self.calls = 0
        self.incomplete_usage_responses = 0
        self.missing_usage_fields: set[str] = set()
        self.usage = {name: 0 for name in TOKEN_FIELDS}
        self.served = []

    @property
    def calls_without_usage(self) -> int:
        return self.calls - len(self.served) + self.incomplete_usage_responses

    def complete(self, **kwargs):
        self.calls += 1
        response = self.base.complete(**kwargs)
        self.served.append(response.model)
        complete = True
        for name in TOKEN_FIELDS:
            value = getattr(response.usage, name, None)
            if value is None and name not in TOKEN_FIELDS[:2]:
                continue
            if type(value) is int and value >= 0:
                self.usage[name] += value
            else:
                complete = False
                self.missing_usage_fields.add(name)
        if not complete:
            self.incomplete_usage_responses += 1
        write_json(
            self.root / "model-responses" / f"{self.calls:04}.json",
            response.model_dump(mode="json"),
        )
        if response.model != self.requested:
            raise ModelFailure("served_model_mismatch")
        return response


def _failure(result) -> str | None:
    if result.status == "passed":
        return None
    reason = result.stop_reason
    if reason == "served_model_mismatch":
        return reason
    if reason in ModelFailure.REASONS:
        return "timeout" if reason == "model_timeout" else "provider_error"
    if reason in ("time_budget",):
        return "timeout"
    if reason in (
        "model_budget",
        "tool_budget",
        "token_budget",
        "context_budget",
        "repeated_action",
    ):
        return "budget_exhausted"
    if reason == "max_tokens":
        return "truncated"
    if reason == "refusal":
        return "refusal"
    if result.verification:
        status = result.verification.status
        return {
            "rejected": "invalid_patch",
            "failed": "repair_failed",
            "timeout": "timeout",
            "infra_error": "verification_error",
            "test_error": "repair_failed",
            "no_tests": "verification_error",
        }.get(status, "verification_error")
    return "internal_error"


def load_records(batch_path: Path) -> list[dict]:
    plan = json.loads((batch_path / "plan.json").read_text(encoding="utf-8"))
    result = []
    seen = set()
    for spec in plan["trials"]:
        identifier = str(uuid.UUID(spec["trial_id"]))
        if identifier in seen:
            raise ValueError("计划包含重复尝试编号")
        seen.add(identifier)
        pending = {
            **spec,
            "batch_id": plan["batch_id"],
            "config_sha256": plan["config_sha256"],
            "status": "pending",
            "failure_category": "incomplete",
            "model_calls": None,
            "calls_without_usage": 0,
            "usage_complete": False,
            "usage": {},
            "elapsed_seconds": None,
        }
        directory = spec.get("directory", f"trials/{identifier}")
        if directory != f"trials/{identifier}" and re.fullmatch(r"t/\d{4}", directory) is None:
            raise ValueError("计划目录不合法")
        path = batch_path / directory / "result.json"
        if not path.is_file():
            result.append(pending)
            continue
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(row, dict):
                raise ValueError("结果必须为对象")
            if any(
                row.get(key) != pending.get(key)
                for key in (
                    "trial_id",
                    "batch_id",
                    "config_sha256",
                    "task_id",
                    "strategy",
                    "repeat",
                )
            ):
                raise ValueError("结果与计划位置不匹配")
            if row.get("status") not in ("running", "passed", "failed"):
                raise ValueError("未知结果状态")
            usage = row.get("usage")
            if (
                not isinstance(usage, dict)
                or set(usage) != set(TOKEN_FIELDS)
                or any(type(value) is not int or value < 0 for value in usage.values())
            ):
                raise ValueError("损坏的用量记录")
            if (
                any(
                    type(row.get(key)) is not int or row[key] < 0
                    for key in ("model_calls", "calls_without_usage")
                )
                or type(row.get("usage_complete")) is not bool
            ):
                raise ValueError("损坏的调用计数")
            if row["calls_without_usage"] > row["model_calls"]:
                raise ValueError("无用量调用数不能大于总调用数")
            if row["status"] == "running":
                row.update(
                    status="pending",
                    failure_category="incomplete",
                    usage_complete=False,
                    model_calls=None,
                )
            elif row["status"] == "failed" and not row.get("failure_category"):
                raise ValueError("失败结果缺少分类")
            elif row["status"] == "passed" and row.get("failure_category") is not None:
                raise ValueError("通过结果不能带失败分类")
            result.append(row)
        except (OSError, ValueError, TypeError):
            result.append({**pending, "status": "failed", "failure_category": "record_invalid"})
    return result


def save_summary(batch_path: Path, pricing: Pricing | None = None) -> dict:
    plan = json.loads((batch_path / "plan.json").read_text(encoding="utf-8"))
    if pricing is None and plan.get("pricing"):
        pricing = Pricing(**plan["pricing"])
    summary = summarize(load_records(batch_path), pricing)
    summary.update(
        batch_id=plan["batch_id"],
        result_kind=plan["result_kind"],
        batch_state=plan.get("state", "unknown"),
    )
    write_json(batch_path / "summary.json", summary)
    prefix = (
        "本批次为离线替身验证，不是模型修复成绩。\n\n"
        if plan["result_kind"] != "real-model"
        else ""
    )
    (batch_path / "summary.md").write_text(prefix + markdown_summary(summary), encoding="utf-8")
    return summary


def run_batch(
    tasks_root: Path,
    solutions_root: Path,
    output_root: Path,
    *,
    config: BatchConfig | None = None,
    allow_api: bool = False,
    approval_hash: str | None = None,
    confirm_holdout: bool = False,
    pricing: Pricing | None = None,
    model_factory=None,
    runner_factory=None,
) -> BatchResult:
    config = config or BatchConfig()
    dataset = load_dataset()
    selected = dataset.select(
        split=config.split, task_ids=list(config.task_ids) if config.task_ids else None
    )
    count = len(selected) * len(config.strategies) * config.repeats
    if not count or count > config.max_trials:
        raise ValueError("计划尝试数量超限，请显式缩小选择或调整上限")
    fingerprint = dataset.fingerprint(tasks_root, solutions_root)
    if config.fixture:
        if model_factory is None:
            raise ValueError("离线替身批次必须显式注入测试模型")
    elif not allow_api or approval_hash != fingerprint:
        raise ValueError("真实评测需要--allow-api与匹配的--approve-dataset指纹")
    if (
        not config.fixture
        and any(item.split == "holdout" for item in selected)
        and not confirm_holdout
    ):
        raise ValueError("保留集需要显式确认，不能用于日常调参")
    runner_factory = runner_factory or (lambda image: DockerRunner(image=image))
    model_factory = model_factory or (lambda *args: AnthropicModel(model=config.model))
    environment_error = False
    image_id = None
    try:
        image_id = runner_factory(DEFAULT_IMAGE).check()["image_id"]
    except Exception:
        environment_error = True
    config_data = asdict(config)
    config_data.update(
        dataset_version=dataset.version,
        dataset_sha256=fingerprint,
        code_sha256=code_fingerprint(),
        image_id=image_id,
        python=platform.python_version(),
        baseline_retrieval=RETRIEVAL_POLICY,
        baseline_prompt_sha256=_sha(BASELINE_SYSTEM.encode()),
        agent_prompt_sha256=_sha(SYSTEM_PROMPT.encode()),
        tool_schema_sha256=_sha(json.dumps(tool_definitions(), sort_keys=True).encode()),
        dependency_versions={
            name: version(name) for name in ("patchpilot", "anthropic", "pydantic", "httpx2")
        },
    )
    config_hash = _sha(
        json.dumps(config_data, sort_keys=True, separators=(",", ":"), default=str).encode()
    )
    batch_id = str(uuid.uuid4())
    batch = output_root.resolve() / f"b-{batch_id.replace('-', '')[:12]}"
    batch.mkdir(parents=True)
    frozen = {}
    for item in selected:
        try:
            task = load_task(tasks_root / item.task_id / "task.json")
            frozen[item.task_id] = (
                task.spec.snapshot_sha256,
                compute_snapshot_hash(acceptance_directory(item.task_id)),
            )
        except (OSError, ValueError):
            frozen[item.task_id] = (None, None)
    trials = []
    for repetition in range(config.repeats):
        for index, item in enumerate(selected):
            order = (
                config.strategies
                if (index + repetition) % 2 == 0
                else tuple(reversed(config.strategies))
            )
            for strategy in order:
                trials.append(
                    {
                        "trial_id": str(uuid.uuid4()),
                        "directory": f"t/{len(trials):04d}",
                        "task_id": item.task_id,
                        "split": item.split,
                        "group": item.group,
                        "strategy": strategy,
                        "repeat": repetition,
                        "task_snapshot_sha256": frozen[item.task_id][0],
                        "acceptance_sha256": frozen[item.task_id][1],
                    }
                )
    plan = {
        "batch_id": batch_id,
        "state": "running",
        "created_at": datetime.now(UTC).isoformat(),
        "config_sha256": config_hash,
        "config": config_data,
        "result_kind": "offline-fixture" if config.fixture else "real-model",
        "pricing": asdict(pricing) if pricing else None,
        "trials": trials,
    }
    write_json(batch / "plan.json", plan)
    completed = False
    try:
        for spec in trials:
            root = batch / spec["directory"]
            root.mkdir(parents=True)
            record = {
                **spec,
                "batch_id": batch_id,
                "config_sha256": config_hash,
                "dataset_sha256": fingerprint,
                "code_sha256": config_data["code_sha256"],
                "image_id": image_id,
                "environment_id": "python-pytest-v1",
                "model_requested": config.model,
                "model_served": [],
                "status": "running",
                "failure_category": None,
                "stop_reason": None,
                "usage": {name: 0 for name in TOKEN_FIELDS},
                "model_calls": 0,
                "tool_calls": 0,
                "calls_without_usage": 0,
                "usage_complete": True,
                "artifacts": {},
            }
            write_json(root / "result.json", record)
            started = monotonic()
            model = audit = None
            try:
                if dataset.fingerprint(tasks_root, solutions_root) != fingerprint:
                    record.update(
                        status="failed",
                        failure_category="version_mismatch",
                        stop_reason="dataset_changed",
                    )
                    continue
                if environment_error:
                    record.update(
                        status="failed",
                        failure_category="environment_failed",
                        stop_reason="image_unavailable",
                    )
                    continue
                task = load_task(tasks_root / spec["task_id"] / "task.json")
                if (
                    task.spec.snapshot_sha256 != spec["task_snapshot_sha256"]
                    or compute_snapshot_hash(acceptance_directory(task.spec.task_id))
                    != spec["acceptance_sha256"]
                ):
                    record.update(
                        status="failed",
                        failure_category="version_mismatch",
                        stop_reason="task_or_acceptance_changed",
                    )
                    continue
                runner = runner_factory(image_id)
                workspace = create_workspace(task, root / "p")
                preflight = runner.run(workspace, time_budget=config.budget.timeout_seconds)
                record["preflight_status"] = preflight.status
                if preflight.status != "failed" or not preflight.failed or preflight.errors:
                    category = (
                        "timeout"
                        if preflight.status == "timeout"
                        else "not_reproduced"
                        if preflight.status in ("passed", "no_tests")
                        else "environment_failed"
                    )
                    record.update(
                        status="failed", failure_category=category, stop_reason=preflight.status
                    )
                    continue
                remaining = config.budget.timeout_seconds - (monotonic() - started)
                if remaining <= 0:
                    record.update(
                        status="failed", failure_category="timeout", stop_reason="preflight_timeout"
                    )
                    continue
                model = model_factory(spec["task_id"], spec["strategy"], spec["repeat"])
                if config.fixture and model.kind == "anthropic-sdk":
                    raise ValueError("替身模式不能使用真实SDK模型")
                if not config.fixture and model.kind != "anthropic-sdk":
                    raise ValueError("真实模式不能将测试替身标作模型成绩")
                audit = AuditedModel(model, config.model, root)
                solver = run_baseline if spec["strategy"] == "baseline" else run_agent
                result = solver(
                    task,
                    root / "w",
                    audit,
                    runner,
                    replace(config.budget, timeout_seconds=remaining),
                )
                category = _failure(result)
                if dataset.fingerprint(tasks_root, solutions_root) != fingerprint:
                    category = "version_mismatch"
                if (
                    result.verification
                    and result.verification.acceptance_sha256 != spec["acceptance_sha256"]
                ):
                    category = "version_mismatch"
                if (
                    result.verification
                    and result.verification.tests
                    and result.verification.tests.image_id != image_id
                ):
                    category = "version_mismatch"
                record.update(
                    status="passed" if category is None else "failed",
                    failure_category=category,
                    stop_reason=result.stop_reason,
                    tool_calls=result.tool_calls,
                    model_kind=model.kind,
                    patch_sha256=_sha(Path(result.patch_path).read_bytes()),
                )
                record["artifacts"] = {
                    name: Path(getattr(result, name)).relative_to(root).as_posix()
                    for name in ("patch_path", "report_path", "transcript_path", "events_path")
                }
            except (ValueError, OSError):
                record.update(
                    status="failed",
                    failure_category="task_invalid"
                    if audit is None and model is None
                    else "internal_error",
                    stop_reason="validation_or_io_error",
                )
            except Exception:
                record.update(
                    status="failed",
                    failure_category="internal_error",
                    stop_reason="unexpected_error",
                )
            finally:
                if audit is not None:
                    record.update(
                        usage=audit.usage,
                        model_calls=audit.calls,
                        calls_without_usage=audit.calls_without_usage,
                        usage_complete=audit.calls_without_usage == 0,
                        model_served=audit.served,
                        responses_received=len(audit.served),
                        usage_missing_fields=sorted(audit.missing_usage_fields),
                    )
                if model is not None and hasattr(model, "close"):
                    try:
                        model.close()
                    except Exception:
                        record.update(
                            status="failed",
                            failure_category="internal_error",
                            stop_reason="model_cleanup_error",
                        )
                record["elapsed_seconds"] = round(monotonic() - started, 6)
                write_json(root / "result.json", record)
        completed = True
    finally:
        plan["state"] = "completed" if completed else "interrupted"
        write_json(batch / "plan.json", plan)
        summary = save_summary(batch, pricing)
    return BatchResult(str(batch), summary)

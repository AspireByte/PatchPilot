"""显式、单决策者的 Agent 循环；模型只能请求受控工具。"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic

from anthropic.types import MessageParam

from patchpilot.control import ExecutionControl, ExecutionStopped
from patchpilot.patching import export_diff
from patchpilot.prompts import AGENT_CORE_PROMPT, BENCHMARK_POLICY_PROMPT, compose_prompt
from patchpilot.runtime import AgentBudget, AgentModel, ModelFailure
from patchpilot.sandbox import DockerRunner
from patchpilot.snapshot import compute_snapshot_hash
from patchpilot.task import LoadedTask, to_agent_input
from patchpilot.tools import ToolDispatcher, ToolResult, tool_definitions
from patchpilot.verification import VerificationResult, verify_patch
from patchpilot.workspace import Workspace, create_workspace

SYSTEM_PROMPT = compose_prompt(AGENT_CORE_PROMPT, BENCHMARK_POLICY_PROMPT)


@dataclass(frozen=True)
class AgentResult:
    status: str
    stop_reason: str
    model_kind: str
    model_calls: int
    tool_calls: int
    usage: dict[str, int]
    calls_without_usage: int
    workspace_path: str
    patch_path: str
    report_path: str
    transcript_path: str
    events_path: str
    verification: VerificationResult | None


def run_agent(
    task: LoadedTask,
    runs_root: Path,
    model: AgentModel,
    runner: DockerRunner,
    budget: AgentBudget | None = None,
    *,
    on_event: Callable[[dict], None] | None = None,
    control: ExecutionControl | None = None,
    existing_workspace: Workspace | None = None,
    on_boundary: Callable[[Workspace, str], None] | None = None,
) -> AgentResult:
    limits = budget or AgentBudget()
    started = monotonic()
    deadline = started + limits.timeout_seconds
    workspace = existing_workspace or create_workspace(task, runs_root)
    if workspace.base_sha256 != task.spec.snapshot_sha256:
        raise ValueError("恢复工作区的基线版本不匹配")
    dispatcher = ToolDispatcher(task, workspace, runner)
    transcript_path = workspace.artifacts / "transcript.json"
    events_path = workspace.artifacts / "events.jsonl"
    patch_path = workspace.artifacts / "candidate.patch"
    report_path = workspace.artifacts / "agent-result.json"
    definitions = tool_definitions()
    public = to_agent_input(task).model_dump(mode="json")
    initial = (
        dispatcher.dispatch("initial", "list_files", {})
        if control is None or not control.stopped
        else None
    )
    public["initial_files"] = (
        {**initial.data, "truncated": initial.truncated}
        if initial
        else {"files": [], "truncated": False}
    )
    messages: list[MessageParam] = [
        {"role": "user", "content": json.dumps(public, ensure_ascii=False)}
    ]
    model_calls = tool_calls = unknown_usage = 0
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    seen_ids: set[str] = set()
    repeated: dict[str, int] = {}
    stop = "model_budget"
    verification = None
    status = "unverified"

    def event(kind: str, **values) -> None:
        record = {"event": kind, **values}
        with events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        if on_event is not None:
            try:
                on_event(record)
            except ExecutionStopped:
                if kind != "run_finished":
                    raise

    def checkpoint() -> None:
        transcript_path.write_text(
            json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def remaining() -> float:
        return max(0, deadline - monotonic())

    event("run_started", model_kind=model.kind, budget=asdict(limits))
    checkpoint()
    try:
        if control is not None:
            control.check()
        if on_boundary is not None:
            on_boundary(workspace, "initial")
        while True:
            if control is not None:
                control.check()
            if remaining() <= 0:
                stop = "time_budget"
                break
            if model_calls >= limits.max_model_calls:
                stop = "model_budget"
                break
            serialized = json.dumps(messages, ensure_ascii=False)
            if (
                len(serialized) + len(SYSTEM_PROMPT) + len(json.dumps(definitions))
                > limits.max_context_chars
            ):
                stop = "context_budget"
                break
            if sum(usage.values()) >= limits.max_total_tokens:
                stop = "token_budget"
                break
            model_calls += 1
            event("model_started", number=model_calls)
            try:
                response = model.complete(
                    system=SYSTEM_PROMPT,
                    messages=messages,
                    tools=definitions,
                    timeout_seconds=remaining(),
                    max_output_tokens=min(
                        limits.max_output_tokens, limits.max_total_tokens - sum(usage.values())
                    ),
                )
            except ModelFailure as error:
                unknown_usage += 1
                stop = error.reason
                event("model_failed", reason=stop)
                break
            for field in usage:
                usage[field] += getattr(response.usage, field, 0) or 0
            content = [
                block.model_dump(mode="json", exclude_none=True) for block in response.content
            ]
            messages.append({"role": "assistant", "content": content})
            blocks = [block for block in response.content if block.type == "tool_use"]
            event(
                "model_finished",
                number=model_calls,
                reason=response.stop_reason,
                usage=response.usage.model_dump(mode="json"),
            )
            checkpoint()
            terminal = None
            if remaining() <= 0:
                terminal = "time_budget"
            elif sum(usage.values()) >= limits.max_total_tokens:
                terminal = "token_budget"
            elif response.stop_reason in ("max_tokens", "refusal"):
                terminal = response.stop_reason
            elif response.stop_reason not in ("tool_use", "end_turn") or (
                response.stop_reason == "end_turn" and blocks
            ):
                terminal = "protocol_error"
            ids = [block.id for block in blocks]
            if len(set(ids)) != len(ids) or any(identifier in seen_ids for identifier in ids):
                terminal = "protocol_error"
            seen_ids.update(ids)
            results = []
            for block in blocks:
                if terminal is None and tool_calls >= limits.max_tool_calls:
                    terminal = "tool_budget"
                if terminal is None and remaining() <= 0:
                    terminal = "time_budget"
                if terminal is not None:
                    result = ToolResult(
                        block.id,
                        block.name,
                        False,
                        {},
                        {
                            "code": "budget_exhausted"
                            if terminal.endswith("budget")
                            else "not_executed",
                            "message": "本轮已停止，工具未执行",
                        },
                    )
                else:
                    fingerprint = hashlib.sha256(
                        json.dumps(
                            [block.name, block.input, compute_snapshot_hash(workspace.repo)],
                            sort_keys=True,
                            ensure_ascii=False,
                        ).encode()
                    ).hexdigest()
                    if control is not None:
                        control.check()
                    tool_calls += 1
                    event("tool_started", call_id=block.id, tool=block.name)
                    result = dispatcher.dispatch(
                        block.id, block.name, block.input, timeout_seconds=remaining()
                    )
                    if control is not None:
                        control.check()
                    repeated[fingerprint] = repeated.get(fingerprint, 0) + 1
                    if repeated[fingerprint] >= limits.max_repeated_actions:
                        terminal = "repeated_action"
                    if remaining() <= 0:
                        terminal = "time_budget"
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "is_error": not result.ok,
                        "content": json.dumps(asdict(result), ensure_ascii=False),
                    }
                )
                event(
                    "tool_finished",
                    call_id=block.id,
                    tool=block.name,
                    ok=result.ok,
                    truncated=result.truncated,
                )
            if results:
                messages.append({"role": "user", "content": results})
                checkpoint()
                if terminal is None and on_boundary is not None:
                    on_boundary(workspace, "tool_batch")
            if terminal:
                stop = terminal
                break
            if response.stop_reason == "end_turn":
                if blocks:
                    stop = "protocol_error"
                    break
                stop = "model_finished"
                if remaining() <= 0:
                    stop = "time_budget"
                    break
                if control is not None:
                    control.check()
                patch = export_diff(task, workspace)
                patch_path.write_bytes(patch)
                event("verification_started")
                verification = verify_patch(task, patch, runs_root, runner, time_budget=remaining())
                status = verification.status
                event("verification_finished", status=status)
                break
            if not blocks:
                stop = "protocol_error"
                break
    except ExecutionStopped as error:
        stop = error.reason
        status = "unverified"
    except (ValueError, OSError):
        stop = "runtime_error"
        event("runtime_failed", reason=stop)
    finally:
        checkpoint()
        try:
            patch_path.write_bytes(export_diff(task, workspace))
        except (ValueError, OSError):
            status, stop = "unverified", "invalid_workspace"
            patch_path.write_bytes(b"")
        event("run_finished", status=status, reason=stop)
    result = AgentResult(
        status,
        stop,
        model.kind,
        model_calls,
        tool_calls,
        usage,
        unknown_usage,
        str(workspace.root),
        str(patch_path),
        str(report_path),
        str(transcript_path),
        str(events_path),
        verification,
    )
    report_path.write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result

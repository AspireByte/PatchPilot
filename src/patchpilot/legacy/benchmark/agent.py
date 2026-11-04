"""显式、单决策者的 Agent 循环；模型只能请求受控工具。"""

# 本模块保留固定任务内部回归流程；当前公开聊天入口使用 session.py 的 AgentSession。
# 两者共用模型协议，但本流程会在模型结束后自动导出补丁并进行独立基准验收。

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from time import monotonic

from anthropic.types import MessageParam

from patchpilot.core.budget import AgentBudget
from patchpilot.core.control import ExecutionControl, ExecutionStopped
from patchpilot.legacy.benchmark.model_adapter import AgentModel
from patchpilot.legacy.benchmark.patching import export_diff
from patchpilot.legacy.benchmark.sandbox import DockerRunner
from patchpilot.legacy.benchmark.snapshot import compute_snapshot_hash
from patchpilot.legacy.benchmark.task import LoadedTask, to_agent_input
from patchpilot.legacy.benchmark.tools import ToolDispatcher, ToolResult, tool_definitions
from patchpilot.legacy.benchmark.verification import VerificationResult, verify_patch
from patchpilot.legacy.benchmark.workspace import Workspace, create_workspace
from patchpilot.model.protocol import ModelFailure
from patchpilot.session_runtime.prompts import (
    AGENT_CORE_PROMPT,
    BENCHMARK_POLICY_PROMPT,
    compose_prompt,
)

SYSTEM_PROMPT = compose_prompt(AGENT_CORE_PROMPT, BENCHMARK_POLICY_PROMPT)


@dataclass(frozen=True)
class AgentResult:
    """旧固定任务执行结束后的汇总，区分执行停止原因与补丁验收结果。

    status 来自验收或保持 unverified，stop_reason 说明模型结束、预算或异常等停止原因。
    调用次数、usage 与 calls_without_usage 记录已知及未知用量，路径字段指向补丁和过程产物。
    verification 为空表示未得到验收对象，不能仅凭模型回答声称修复成功。
    """

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
    """在独立副本中执行一次有预算上限的固定任务修复，并交付补丁与证据。

    task 是已经校验的任务，runs_root 保存工作区和产物，model 决策，runner 执行容器测试。
    existing_workspace 可恢复候选代码；on_boundary 在初始状态及完整工具批次后通知服务保存检查点。
    循环保留工具调用配对、校验停止原因，并限制调用、上下文、用量、时间和重复动作。
    正常 end_turn 后导出累计补丁，调用 verify_patch 在固定基线的新副本上验收。
    中断或受控错误仍尽量保存过程与候选补丁；返回 AgentResult，不向原始快照写入修复。
    """
    # 输入已校验任务、模型和 Docker 执行器；输出运行状态与证据路径，不直接修改原始快照。
    # existing_workspace/on_boundary 供内部服务恢复及保存检查点使用，不是聊天会话的恢复接口。
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
    # 模型正常结束只说明完成了对话；必须经过下面的 verify_patch 才能获得测试结论。
    status = "unverified"

    def event(kind: str, **values) -> None:
        # 闭包可读取外层的产物路径和回调；**values 接收不同事件各自的命名字段。
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
                    # 指纹包含仓库摘要：同样的工具输入在代码变化后可能有意义，不应直接当成死循环。
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
                # 补丁由工作区与固定基线的差异生成；验收在另一份干净工作区重新应用该补丁。
                # 工具阶段只开放公开测试，维护者验收由 verify_patch 的内部通路添加。
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
        # 正常结束、预算耗尽或受控异常都保存历史和候选补丁，便于检查停止前已完成的工作。
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

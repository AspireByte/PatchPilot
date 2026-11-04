"""新 Runtime 内核的可注入模型、工具和事件边界测试。"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from patchpilot.core import AgentLoop, LoopStopped, ModelResponse, RuntimeEvent
from patchpilot.core.budget import AgentBudget
from patchpilot.core.context import ContextBuilder
from patchpilot.core.types import ModelUsage, ToolCall
from patchpilot.session_runtime import StateCodec, UnsupportedStateVersion
from patchpilot.tools_runtime import ToolExecutor, ToolRegistry


class FakeModel:
    kind = "fake"

    def __init__(self, *responses):
        self.responses = iter(responses)
        self.requests = []

    def complete(self, request):
        self.requests.append(request)
        return next(self.responses)


class FakeTools:
    def __init__(self):
        self.calls = []

    def definitions(self):
        return [{"name": "echo", "description": "echo", "input_schema": {}}]

    def dispatch(self, name, arguments, *, remaining):
        self.calls.append((name, dict(arguments), remaining))
        return {"ok": True, "echo": arguments.get("value")}


def response(*blocks, stop_reason="end_turn", tokens=2):
    return ModelResponse(
        tuple(blocks),
        stop_reason,
        ModelUsage(input_tokens=tokens, output_tokens=tokens),
    )


def test_loop_executes_tools_and_preserves_message_pairs():
    model = FakeModel(
        response(
            {"type": "text", "text": "开始"},
            {"type": "tool_use", "id": "call-1", "name": "echo", "input": {"value": "x"}},
            stop_reason="tool_use",
        ),
        response({"type": "text", "text": "完成"}),
    )
    tools = FakeTools()
    events: list[RuntimeEvent] = []
    messages = [{"role": "user", "content": "开始"}]
    result = AgentLoop(
        model,
        ToolExecutor(tools),
        AgentBudget(max_model_calls=3, max_tool_calls=2),
        context=ContextBuilder(messages, 100000),
        events=events.append,
    ).run(messages, "system")

    assert result.last_text == "完成"
    assert tools.calls and tools.calls[0][0] == "echo"
    assert messages[1]["role"] == "assistant"
    assert messages[2]["content"][0]["tool_use_id"] == "call-1"
    assert [event.kind for event in events].count("model_started") == 2
    assert any(event.kind == "tool_finished" for event in events)


def test_loop_rejects_duplicate_tool_ids_without_execution():
    model = FakeModel(
        response(
            {"type": "tool_use", "id": "same", "name": "echo", "input": {}},
            {"type": "tool_use", "id": "same", "name": "echo", "input": {}},
            stop_reason="tool_use",
        )
    )
    tools = FakeTools()
    messages = [{"role": "user", "content": "test"}]
    with pytest.raises(LoopStopped, match="重复"):
        AgentLoop(model, ToolExecutor(tools), AgentBudget()).run(messages, "system")
    assert not tools.calls


def test_loop_context_limit_stops_before_model_request():
    model = FakeModel(response({"type": "text", "text": "不应调用"}))
    messages = [{"role": "user", "content": "too large"}]
    with pytest.raises(LoopStopped, match="上下文"):
        AgentLoop(
            model,
            ToolExecutor(FakeTools()),
            AgentBudget(max_context_chars=1),
            context=ContextBuilder(messages, 1),
        ).run(messages, "system")
    assert not model.requests


def test_core_modules_do_not_import_provider_or_service_packages():
    root = Path(__file__).parents[1] / "src" / "patchpilot" / "core"
    forbidden = {
        "anthropic",
        "fastapi",
        "sqlalchemy",
        "patchpilot.service",
        "patchpilot.evaluation",
        "patchpilot.legacy",
    }
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert not imported.intersection(forbidden), (path, imported.intersection(forbidden))


def test_tool_registry_can_run_in_memory_tool_without_legacy_provider():
    registry = ToolRegistry()
    registry.register(
        "echo",
        "echo",
        {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        lambda call, context: {"ok": True, "value": call.input["value"]},
    )
    result = ToolExecutor(registry).execute(ToolCall("call-1", "echo", {"value": "x"}), remaining=3)
    assert result == {"ok": True, "value": "x"}
    assert registry.definitions()[0]["name"] == "echo"
    assert (
        ToolExecutor(registry).execute(ToolCall("call-2", "echo", {}), remaining=3)["ok"] is False
    )


def test_state_codec_reserves_version_migration_boundary():
    class Migrations:
        def migrate(self, state, from_version, to_version):
            return {**state, "version": to_version, "migrated": True}

    assert StateCodec().encode({"value": 1})["version"] == 1
    with pytest.raises(UnsupportedStateVersion):
        StateCodec().decode({"version": 0})
    assert StateCodec(Migrations()).decode({"version": 0})["migrated"] is True

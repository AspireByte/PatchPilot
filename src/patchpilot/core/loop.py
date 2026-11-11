"""可注入模型和工具的通用 Agent Loop。"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from time import monotonic
from typing import Any

from patchpilot.core.control import ExecutionControl

from .context import ContextBuilder, ContextLimitReached
from .events import EventSink, RuntimeEvent
from .types import ModelRequest, ModelResponse


class LoopStopped(RuntimeError):
    """模型循环因预算、协议或上下文边界停止。"""


@dataclass
class LoopResult:
    messages: list[dict]
    last_text: str
    model_calls: int
    tool_calls: int
    tokens: int
    stop_reason: str = "model_finished"


class AgentLoop:
    """只负责模型请求与工具结果往返，不负责 Session 或持久化。"""

    def __init__(
        self,
        model,
        tools,
        budget,
        *,
        control: ExecutionControl | None = None,
        context: ContextBuilder | None = None,
        events: EventSink | None = None,
        checkpoint=None,
        initial_model_calls: int = 0,
        initial_tool_calls: int = 0,
        initial_tokens: int = 0,
        kind: str = "worker",
        started: float | None = None,
        repeated: dict[str, int] | None = None,
        clock=None,
    ):
        self.model = model
        self.tools = tools
        self.budget = budget
        self.control = control
        if hasattr(self.model, "control"):
            self.model.control = control
        self.context = context
        self.events = events or (lambda _event: None)
        self.checkpoint = checkpoint or (lambda: None)
        self.model_calls = initial_model_calls
        self.tool_calls = initial_tool_calls
        self.tokens = initial_tokens
        self.kind = kind
        self._clock = clock or monotonic
        self.started = self._clock() if started is None else started
        self.repeated = repeated if repeated is not None else {}

    def _emit(self, event_kind: str, **payload: Any) -> None:
        self.events(RuntimeEvent(event_kind, payload))

    def _remaining(self) -> float:
        return max(0.0, self.budget.timeout_seconds - (self._clock() - self.started))

    def _check_budget(self) -> None:
        if self.control:
            self.control.check()
        if self._remaining() <= 0:
            raise LoopStopped("本轮时间预算已耗尽")
        if self.tokens >= self.budget.max_total_tokens:
            raise LoopStopped("本轮已知token预算已耗尽")

    @staticmethod
    def _previous_tool_ids(messages: list[dict]) -> set[str]:
        return {
            str(block["id"])
            for message in messages
            if message.get("role") == "assistant" and isinstance(message.get("content"), list)
            for block in message["content"]
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id")
        }

    def _call_model(self, system: str, messages: list[dict], tools: list[dict]) -> ModelResponse:
        self._check_budget()
        if self.model_calls >= self.budget.max_model_calls:
            raise LoopStopped("本轮模型调用预算已耗尽")
        if self.context is not None:
            try:
                system, messages, tools = self.context.build(system, tools, messages=messages)
            except ContextLimitReached as error:
                raise LoopStopped(str(error)) from error
        return self.complete_auxiliary(
            ModelRequest(
                system=system,
                messages=messages,
                tools=tools,
                timeout_seconds=self._remaining(),
                max_output_tokens=self.budget.max_output_tokens,
            ),
            kind=self.kind,
        )

    def complete_auxiliary(
        self, request: ModelRequest, *, kind: str = "compactor"
    ) -> ModelResponse:
        """摘要等内部请求与工作请求共享次数、token、取消和时间预算。"""
        self._check_budget()
        if self.model_calls >= self.budget.max_model_calls:
            raise LoopStopped("本轮模型调用预算已耗尽（包含上下文压缩）")
        self.model_calls += 1
        self._emit(
            "model_started",
            kind=kind,
            number=self.model_calls,
        )
        self.checkpoint()
        try:
            response = self.model.complete(
                ModelRequest(
                    system=request.system,
                    messages=request.messages,
                    tools=request.tools,
                    timeout_seconds=self._remaining(),
                    max_output_tokens=min(
                        request.max_output_tokens,
                        self.budget.max_total_tokens - self.tokens,
                    ),
                )
            )
        except Exception as error:
            reason = getattr(error, "reason", None) or str(error)
            self._emit("model_failed", kind=kind, number=self.model_calls, reason=reason)
            raise
        usage = response.usage
        if usage is not None:
            self.tokens += usage.total_tokens
        self._emit(
            "model_finished",
            kind=kind,
            number=self.model_calls,
            usage=(
                {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "cache_read_input_tokens": usage.cache_read_input_tokens,
                    "cache_creation_input_tokens": usage.cache_creation_input_tokens,
                }
                if usage is not None
                else None
            ),
        )
        self.checkpoint()
        return response

    def run(self, messages: list[dict], system: str) -> LoopResult:
        """运行一轮并发布统一的 turn 生命周期事件。"""
        self._emit("turn_started", kind=self.kind)
        try:
            return self._run(messages, system)
        except Exception as error:
            self._emit("paused", kind=self.kind, reason=str(error))
            raise
        finally:
            self._emit(
                "turn_finished",
                kind=self.kind,
                model_calls=self.model_calls,
                tool_calls=self.tool_calls,
                tokens=self.tokens,
            )

    def _run(self, messages: list[dict], system: str) -> LoopResult:
        """运行到模型结束或抛出可由 Session 转换的停止异常。"""

        last_text = ""
        while True:
            definitions = self.tools.definitions()
            response = self._call_model(system, messages, definitions)
            blocks = list(response.tool_calls)
            identifiers = [block.identifier for block in blocks]
            previous = self._previous_tool_ids(messages)
            if len(set(identifiers)) != len(identifiers) or any(
                identifier in previous for identifier in identifiers
            ):
                raise LoopStopped("工具调用编号重复，响应未进入可恢复历史")

            content = [dict(block) for block in response.content]
            messages.append({"role": "assistant", "content": content})
            self.checkpoint()
            last_text = response.text
            if last_text:
                self._emit("assistant_text", text=last_text)

            if response.stop_reason not in ("tool_use", "end_turn"):
                raise LoopStopped(f"模型停止：{response.stop_reason}")
            if (response.stop_reason == "end_turn" and blocks) or (
                response.stop_reason == "tool_use" and not blocks
            ):
                raise LoopStopped("模型工具调用协议错误")
            if not blocks:
                return LoopResult(
                    messages,
                    last_text,
                    self.model_calls,
                    self.tool_calls,
                    self.tokens,
                )

            results: list[dict] = []
            messages.append({"role": "user", "content": results})
            self.checkpoint()
            for block in blocks:
                self._check_budget()
                if self.tool_calls >= self.budget.max_tool_calls:
                    raise LoopStopped("工具调用预算已耗尽")
                self.tool_calls += 1
                self._emit(
                    "tool_started",
                    tool=block.name,
                    call_id=block.identifier,
                    number=self.tool_calls,
                    command=(
                        block.input.get("command") if isinstance(block.input, Mapping) else None
                    ),
                )
                try:
                    result = self.tools.execute(block, remaining=self._remaining())
                except Exception:
                    self.checkpoint()
                    raise
                result = result if isinstance(result, dict) else {"ok": False, "error": str(result)}
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.identifier,
                        "is_error": not bool(result.get("ok")),
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
                self.checkpoint()
                self._emit(
                    "tool_finished",
                    tool=block.name,
                    call_id=block.identifier,
                    number=self.tool_calls,
                    result=result,
                )
                fingerprint = json.dumps(
                    [block.name, dict(block.input), result], sort_keys=True, ensure_ascii=False
                )
                self.repeated[fingerprint] = self.repeated.get(fingerprint, 0) + 1
                if self.repeated[fingerprint] >= self.budget.max_repeated_actions:
                    raise LoopStopped("重复无效操作已达上限")

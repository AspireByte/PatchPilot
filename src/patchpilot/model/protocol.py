"""模型 Provider 的稳定端口和错误分类。"""

from __future__ import annotations

from typing import Any, ClassVar, Protocol

from patchpilot.core.types import ModelRequest, ModelResponse, ModelUsage


class ModelClient(Protocol):
    kind: str

    def complete(self, request: ModelRequest) -> ModelResponse: ...


class ModelClientAdapter:
    """把旧的关键字参数模型适配为 Runtime 的 Provider 无关端口。"""

    def __init__(self, delegate: Any):
        self.delegate = delegate

    @property
    def kind(self) -> str:
        return str(getattr(self.delegate, "kind", "model"))

    @property
    def control(self):
        return getattr(self.delegate, "control", None)

    @control.setter
    def control(self, value) -> None:
        if hasattr(self.delegate, "control"):
            self.delegate.control = value

    def complete(self, request: ModelRequest) -> ModelResponse:
        response = self.delegate.complete(
            system=request.system,
            messages=list(request.messages),
            tools=list(request.tools),
            timeout_seconds=request.timeout_seconds,
            max_output_tokens=request.max_output_tokens,
        )
        usage = getattr(response, "usage", None)

        def block_value(block):
            if isinstance(block, dict):
                return dict(block)
            dump = getattr(block, "model_dump", None)
            if dump is not None:
                return dump(mode="json", exclude_none=True)
            return {
                key: value
                for key in ("type", "id", "name", "input", "text")
                if (value := getattr(block, key, None)) is not None
            }

        return ModelResponse(
            content=tuple(block_value(block) for block in response.content),
            stop_reason=getattr(response, "stop_reason", None),
            usage=(
                ModelUsage(
                    input_tokens=getattr(usage, "input_tokens", 0) or 0,
                    output_tokens=getattr(usage, "output_tokens", 0) or 0,
                    cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                    cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0)
                    or 0,
                )
                if usage is not None
                else None
            ),
        )

    def close(self) -> None:
        close = getattr(self.delegate, "close", None)
        if close:
            close()


class ModelFailure(RuntimeError):
    """对外隐藏 SDK 细节的模型错误。"""

    REASONS: ClassVar[frozenset[str]] = frozenset(
        {
            "provider_error",
            "authentication_error",
            "permission_denied",
            "model_not_found",
            "bad_request",
            "model_timeout",
            "rate_limited",
            "model_connection_error",
            "invalid_json",
        }
    )

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)

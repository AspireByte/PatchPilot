import asyncio
import importlib
import time
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from anthropic.types import Message


def api():
    assert importlib.util.find_spec("patchpilot.anthropic_model"), "尚未接入官方SDK适配器"
    return importlib.import_module("patchpilot.anthropic_model")


def message(stop="tool_use"):
    return Message.model_validate(
        {
            "id": "msg-test",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": [
                {"type": "thinking", "thinking": "", "signature": "keep-this-signature"},
                {"type": "tool_use", "id": "tool-1", "name": "list_files", "input": {}},
            ],
            "stop_reason": stop,
            "usage": {"input_tokens": 12, "output_tokens": 8, "cache_read_input_tokens": 3},
        }
    )


class FakeStream:
    def __init__(self, response, events=()):
        self.response, self.events = response, events
        self.finished = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        yield from self.events
        self.finished = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def __aiter__(self):
        for event in self.events:
            yield event
        self.finished = True

    async def get_final_message(self):
        assert self.finished, "必须读完整条流后才取最终消息"
        return self.response


class FakeClient:
    base_url = "https://api.anthropic.com/"

    def __init__(self, response=None, error=None, events=()):
        self.messages = self
        self.response = response or message()
        self.error, self.events = error, events
        self.parameters = self.options = None

    async def close(self):
        pass

    def with_options(self, **kwargs):
        self.options = kwargs
        return self

    def stream(self, **kwargs):
        self.parameters = kwargs
        if self.error:
            raise self.error
        return FakeStream(self.response, self.events)


def invoke(client, timeout=3):
    model = api().AnthropicModel(client=client)
    try:
        return model.complete(
            system="规则",
            messages=[{"role": "user", "content": "公开任务"}],
            tools=[
                {"name": "list_files", "description": "列表", "input_schema": {"type": "object"}}
            ],
            timeout_seconds=timeout,
            max_output_tokens=4096,
        )
    finally:
        model.close()


def test_sdk_adapter_preserves_content_usage_and_disables_implicit_retries() -> None:
    client = FakeClient()
    response = invoke(client)
    assert response is client.response
    assert response.content[0].signature == "keep-this-signature"
    assert response.usage.cache_read_input_tokens == 3
    assert client.options["max_retries"] == 0
    assert client.options["timeout"] <= 3
    assert client.parameters["model"] == "claude-opus-5-5"
    assert client.parameters["thinking"]["type"] == "adaptive"
    assert client.parameters["tools"][0]["eager_input_streaming"]
    assert "fallbacks" not in client.parameters


def test_proxy_does_not_receive_an_unsupported_streaming_flag() -> None:
    client = FakeClient()
    client.base_url = "https://configured-proxy.invalid/"
    invoke(client)
    assert "eager_input_streaming" not in client.parameters["tools"][0]


def test_evaluator_can_disable_thinking_and_use_no_tools() -> None:
    client = FakeClient(response=message(stop="end_turn"), events=())
    client.response.content = []
    model = api().AnthropicModel(client=client, thinking=False)
    try:
        model.complete(
            system="仅评估",
            messages=[{"role": "user", "content": "证据"}],
            tools=[],
            timeout_seconds=3,
            max_output_tokens=512,
        )
        assert client.parameters["tools"] == []
        assert "thinking" not in client.parameters
        assert "output_config" not in client.parameters
    finally:
        model.close()


@pytest.mark.parametrize(
    "kind,reason",
    [
        ("timeout", "model_timeout"),
        ("rate", "rate_limited"),
        ("auth", "authentication_error"),
        ("permission", "permission_denied"),
        ("not_found", "model_not_found"),
        ("bad_request", "bad_request"),
        ("server", "provider_error"),
        ("connection", "model_connection_error"),
    ],
)
def test_provider_errors_are_safe_classifications(kind, reason) -> None:
    request = httpx2.Request("POST", "https://provider.invalid")
    response = httpx2.Response(429, request=request)
    if kind == "timeout":
        error = anthropic.APITimeoutError(request=request)
    elif kind == "connection":
        error = anthropic.APIConnectionError(request=request)
    else:
        cls = {
            "rate": anthropic.RateLimitError,
            "auth": anthropic.AuthenticationError,
            "permission": anthropic.PermissionDeniedError,
            "not_found": anthropic.NotFoundError,
            "bad_request": anthropic.BadRequestError,
            "server": anthropic.APIStatusError,
        }[kind]
        error = cls("SECRET_AUTH_HEADER", response=response, body={"token": "SECRET"})
    with pytest.raises(Exception) as raised:
        invoke(FakeClient(error=error))
    assert getattr(raised.value, "reason", None) == reason
    assert "SECRET" not in str(raised.value)


def test_invalid_stream_json_is_not_silently_accepted() -> None:
    events = [
        SimpleNamespace(
            type="content_block_delta",
            index=1,
            delta=SimpleNamespace(
                type="input_json_delta", partial_json='{"directory": "src"} trailing'
            ),
        )
    ]
    with pytest.raises(Exception) as raised:
        invoke(FakeClient(events=events))
    assert getattr(raised.value, "reason", None) == "invalid_json"


@pytest.mark.parametrize("stop", ["max_tokens", "refusal"])
def test_stopped_stream_is_returned_without_executing_or_retrying(stop) -> None:
    client = FakeClient(response=message(stop))
    assert invoke(client).stop_reason == stop


class SlowStream(FakeStream):
    closed = False

    def __exit__(self, *args):
        self.closed = True
        return False

    def __iter__(self):
        yield SimpleNamespace(type="message_start")
        time.sleep(0.25)
        yield SimpleNamespace(type="message_stop")
        self.finished = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True
        return False

    async def __aiter__(self):
        yield SimpleNamespace(type="message_start")
        await asyncio.sleep(0.25)
        yield SimpleNamespace(type="message_stop")
        self.finished = True


def test_absolute_deadline_cancels_a_stalled_stream_and_closes_it() -> None:
    api()
    stream = SlowStream(message())
    client = FakeClient()
    client.stream = lambda **kwargs: stream
    started = time.monotonic()
    with pytest.raises(Exception) as raised:
        invoke(client, timeout=0.05)
    assert getattr(raised.value, "reason", None) == "model_timeout"
    assert time.monotonic() - started < 0.18
    assert stream.closed


class ErrorStream(FakeStream):
    def __iter__(self):
        raise anthropic.APIError(
            "SECRET_AUTH_HEADER",
            request=httpx2.Request("POST", "https://invalid"),
            body={"secret": "SECRET"},
        )
        yield

    async def __aiter__(self):
        raise anthropic.APIError(
            "SECRET_AUTH_HEADER",
            request=httpx2.Request("POST", "https://invalid"),
            body={"secret": "SECRET"},
        )
        yield


def test_stream_internal_api_error_is_safely_classified() -> None:
    client = FakeClient()
    client.stream = lambda **kwargs: ErrorStream(message())
    with pytest.raises(Exception) as raised:
        invoke(client)
    assert getattr(raised.value, "reason", None) == "provider_error"
    assert "SECRET" not in str(raised.value)


def test_real_sdk_stream_parser_and_client_reuse_are_exercised_without_network() -> None:
    import json

    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        events = [
            (
                "message_start",
                {
                    "type": "message_start",
                    "message": {
                        "id": "msg-offline",
                        "type": "message",
                        "role": "assistant",
                        "model": "claude-opus-5-5",
                        "content": [],
                        "stop_reason": None,
                        "stop_sequence": None,
                        "usage": {"input_tokens": 12, "output_tokens": 0},
                    },
                },
            ),
            (
                "content_block_start",
                {
                    "type": "content_block_start",
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "id": "call-offline",
                        "name": "list_files",
                        "input": {},
                    },
                },
            ),
            (
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "input_json_delta", "partial_json": "{}"},
                },
            ),
            ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            (
                "message_delta",
                {
                    "type": "message_delta",
                    "delta": {"stop_reason": "tool_use", "stop_sequence": None},
                    "usage": {"output_tokens": 10},
                },
            ),
            ("message_stop", {"type": "message_stop"}),
        ]
        payload = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, content=payload.encode()
        )

    client = anthropic.AsyncAnthropic(
        api_key="offline-key",
        auth_token="offline-token",
        base_url="https://unit-test.invalid",
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    model = api().AnthropicModel(client=client)
    try:
        for _ in range(2):
            response = model.complete(
                system="公开规则",
                messages=[{"role": "user", "content": "公开任务"}],
                tools=[],
                timeout_seconds=2,
                max_output_tokens=100,
            )
            assert response.stop_reason == "tool_use"
            assert response.content[0].id == "call-offline"
            assert response.usage.output_tokens == 10
    finally:
        model.close()
    assert len(captured) == 2
    assert all(item["model"] == "claude-opus-5-5" for item in captured)


def test_stream_error_still_saves_final_agent_report(tmp_path: Path) -> None:
    from patchpilot.agent import run_agent
    from patchpilot.task import load_task

    client = FakeClient()
    client.stream = lambda **kwargs: ErrorStream(message())
    model = api().AnthropicModel(client=client)
    try:
        task = load_task(
            Path(__file__).resolve().parents[1] / "benchmarks/tasks/pagination-001/task.json"
        )
        result = run_agent(task, tmp_path, model, None)
    finally:
        if hasattr(model, "close"):
            model.close()
    assert result.stop_reason == "provider_error"
    assert result.calls_without_usage == 1
    text = Path(result.report_path).read_text(encoding="utf-8")
    assert "SECRET" not in text

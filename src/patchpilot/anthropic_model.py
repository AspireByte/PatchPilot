"""官方 Anthropic 异步SDK适配；同步外观、可取消的请求总时限、不执行工具。"""

import asyncio
import json

import anthropic
from anthropic.types import Message, MessageParam, ToolParam

from patchpilot.agent import ModelFailure
from patchpilot.control import ExecutionControl

DEFAULT_MODEL = "claude-opus-5-5"


class AnthropicModel:
    kind = "anthropic-sdk"

    def __init__(
        self,
        *,
        client: anthropic.AsyncAnthropic | None = None,
        model: str = DEFAULT_MODEL,
        control: ExecutionControl | None = None,
        thinking: bool = True,
    ):
        self.control = control
        self.client = client if client is not None else anthropic.AsyncAnthropic(max_retries=0)
        self.model = model
        self.thinking = thinking
        self._runner = asyncio.Runner()

    def complete(
        self,
        *,
        system: str,
        messages: list[MessageParam],
        tools: list[ToolParam],
        timeout_seconds: float,
        max_output_tokens: int,
    ) -> Message:
        return self._runner.run(
            self._controlled_complete(
                system=system,
                messages=messages,
                tools=tools,
                timeout_seconds=timeout_seconds,
                max_output_tokens=max_output_tokens,
            )
        )

    async def _controlled_complete(self, **kwargs) -> Message:
        if self.control is None:
            return await self._complete(**kwargs)
        self.control.check()

        async def watch() -> None:
            while True:
                self.control.check()
                await asyncio.sleep(0.1)

        request = asyncio.create_task(self._complete(**kwargs))
        watcher = asyncio.create_task(watch())
        try:
            done, _ = await asyncio.wait((request, watcher), return_when=asyncio.FIRST_COMPLETED)
            if watcher in done:
                await watcher
            self.control.check()
            return await request
        finally:
            for task in (request, watcher):
                if not task.done():
                    task.cancel()
            await asyncio.gather(request, watcher, return_exceptions=True)

    def close(self) -> None:
        try:
            self._runner.run(self.client.close())
        finally:
            self._runner.close()

    async def _complete(
        self,
        *,
        system: str,
        messages: list[MessageParam],
        tools: list[ToolParam],
        timeout_seconds: float,
        max_output_tokens: int,
    ) -> Message:
        eager = str(self.client.base_url).rstrip("/") == "https://api.anthropic.com"
        definitions = [
            {**tool, **({"eager_input_streaming": True} if eager else {})} for tool in tools
        ]
        try:
            async with asyncio.timeout(timeout_seconds):
                client = self.client.with_options(timeout=timeout_seconds, max_retries=0)
                fragments: dict[int, str] = {}
                async with client.messages.stream(
                    model=self.model,
                    max_tokens=max_output_tokens,
                    system=system,
                    messages=messages,
                    tools=definitions,
                    **(
                        {"thinking": {"type": "adaptive"}, "output_config": {"effort": "high"}}
                        if self.thinking
                        else {}
                    ),
                ) as stream:
                    async for event in stream:
                        if (
                            event.type == "content_block_delta"
                            and event.delta.type == "input_json_delta"
                        ):
                            fragments[event.index] = (
                                fragments.get(event.index, "") + event.delta.partial_json
                            )
                    response = await stream.get_final_message()
                if response.stop_reason not in ("refusal", "max_tokens"):
                    for index, raw in fragments.items():
                        parsed = json.loads(raw)
                        if not isinstance(parsed, dict) or index >= len(response.content):
                            raise ModelFailure("invalid_json")
                        block = response.content[index]
                        if block.type != "tool_use" or block.input != parsed:
                            raise ModelFailure("invalid_json")
                return response
        except (TimeoutError, anthropic.APITimeoutError) as error:
            raise ModelFailure("model_timeout") from error
        except anthropic.AuthenticationError as error:
            raise ModelFailure("authentication_error") from error
        except anthropic.PermissionDeniedError as error:
            raise ModelFailure("permission_denied") from error
        except anthropic.NotFoundError as error:
            raise ModelFailure("model_not_found") from error
        except anthropic.RateLimitError as error:
            raise ModelFailure("rate_limited") from error
        except anthropic.BadRequestError as error:
            raise ModelFailure("bad_request") from error
        except anthropic.APIStatusError as error:
            raise ModelFailure("provider_error") from error
        except anthropic.APIConnectionError as error:
            raise ModelFailure("model_connection_error") from error
        except anthropic.APIError as error:
            raise ModelFailure("provider_error") from error
        except ValueError as error:
            raise ModelFailure("invalid_json") from error

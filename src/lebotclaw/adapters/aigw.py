import os
from typing import Generator

from openai import OpenAI, APIError, APITimeoutError, APIConnectionError

from lebotclaw.adapters.base import ModelAdapter, ModelResponse, ModelAdapterError


class AigwAdapter(ModelAdapter):
    """智云 TokenHub 网关端点（OpenAI 兼容协议，一个 key 挂多模型）。

    用 AIGW_MODEL 环境变量切换模型，默认 deepseek-v4-pro。
    注意：该网关部分模型为思考模型，reasoning 消耗 max_tokens，默认给足 4096。
    """

    def __init__(self, api_key: str = None, model: str = None, **kwargs):
        super().__init__(
            "aigw",
            api_key or os.getenv("AIGW_API_KEY", ""),
            "https://aigw.telecomjs.com/v1",
            **kwargs,
        )
        self.model = model or os.getenv("AIGW_MODEL", "deepseek-v4-pro")
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    def generate(
        self,
        messages: list[dict],
        tools: list[dict] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> ModelResponse:
        try:
            kwargs = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            response = self.client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            content = choice.message.content or ""

            tool_calls = []
            if choice.message.tool_calls:
                for tc in choice.message.tool_calls:
                    tool_calls.append({
                        "id": tc.id,
                        "tool_name": tc.function.name,
                        "arguments": tc.function.arguments,
                    })

            usage = {}
            if response.usage:
                usage = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens,
                }

            return ModelResponse(
                content=content,
                tool_calls=tool_calls,
                usage=usage,
                model=response.model or self.model,
            )
        except APITimeoutError as e:
            raise ModelAdapterError(f"Aigw request timeout: {e}", provider="aigw")
        except APIConnectionError as e:
            raise ModelAdapterError(f"Aigw connection error: {e}", provider="aigw")
        except APIError as e:
            raise ModelAdapterError(
                f"Aigw API error: {e}",
                provider="aigw",
                status_code=e.status_code,
            )

    def stream(
        self,
        messages: list[dict],
        tools: list[dict] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> Generator[str, None, None]:
        try:
            kwargs = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": True,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            response = self.client.chat.completions.create(**kwargs)
            for chunk in response:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content
        except APITimeoutError as e:
            raise ModelAdapterError(f"Aigw stream timeout: {e}", provider="aigw")
        except APIConnectionError as e:
            raise ModelAdapterError(f"Aigw stream connection error: {e}", provider="aigw")
        except APIError as e:
            raise ModelAdapterError(
                f"Aigw stream API error: {e}",
                provider="aigw",
                status_code=e.status_code,
            )

    def stream_deltas(
        self,
        messages: list[dict],
        tools: list[dict] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> Generator[tuple, None, None]:
        """真流式：逐 token yield ('text', str)；模型要求工具时末尾 yield ('tool_calls', list)。

        流式解析 tool_calls（按 index 累积分片的 function.name / arguments）。
        tool_calls 元素格式同 generate()：{id, tool_name, arguments(str)}。
        """
        try:
            kwargs = {
                "model": self.model,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": True,
            }
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"
            response = self.client.chat.completions.create(**kwargs)
            tc_acc: dict = {}
            for chunk in response:
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    yield ("text", delta.content)
                tcs = getattr(delta, "tool_calls", None) if delta else None
                if tcs:
                    for tc in tcs:
                        idx = tc.index if tc.index is not None else 0
                        slot = tc_acc.setdefault(idx, {"id": "", "tool_name": "", "arguments": ""})
                        if tc.id:
                            slot["id"] = tc.id
                        if tc.function:
                            if tc.function.name:
                                slot["tool_name"] += tc.function.name
                            if tc.function.arguments:
                                slot["arguments"] += tc.function.arguments
            if tc_acc:
                yield ("tool_calls", [tc_acc[i] for i in sorted(tc_acc)])
        except APITimeoutError as e:
            raise ModelAdapterError(f"Aigw stream timeout: {e}", provider="aigw")
        except APIConnectionError as e:
            raise ModelAdapterError(f"Aigw stream connection error: {e}", provider="aigw")
        except APIError as e:
            raise ModelAdapterError(
                f"Aigw stream API error: {e}",
                provider="aigw",
                status_code=e.status_code,
            )

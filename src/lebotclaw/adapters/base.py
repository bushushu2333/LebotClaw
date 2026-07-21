from abc import ABC, abstractmethod
from typing import Generator


class ModelMessage:
    def __init__(self, role: str, content: str, tool_calls: list = None):
        self.role = role
        self.content = content
        self.tool_calls = tool_calls or []


class ModelResponse:
    def __init__(self, content: str, tool_calls: list = None, usage: dict = None, model: str = ""):
        self.content = content
        self.tool_calls = tool_calls or []
        self.usage = usage or {}
        self.model = model


class ModelAdapterError(Exception):
    def __init__(self, message: str, provider: str = "", status_code: int = None):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code


class ModelAdapter(ABC):
    def __init__(self, model_name: str, api_key: str = "", base_url: str = "", **kwargs):
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url
        self.config = kwargs

    @abstractmethod
    def generate(
        self,
        messages: list[dict],
        tools: list[dict] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> ModelResponse:
        ...

    @abstractmethod
    def stream(
        self,
        messages: list[dict],
        tools: list[dict] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> Generator[str, None, None]:
        ...

    def stream_deltas(
        self,
        messages: list[dict],
        tools: list[dict] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> Generator[tuple, None, None]:
        """流式事件流：yield ('text', str) 多次；模型要求工具时末尾 yield ('tool_calls', list)。

        默认兜底实现：generate() 一次性拿完整回复后按小段伪流式吐（非真流式）。
        子类应覆盖为真流式逐 token。tool_calls 元素格式同 generate()：{id, tool_name, arguments}。
        """
        resp = self.generate(messages=messages, tools=tools, temperature=temperature, max_tokens=max_tokens)
        if resp.content:
            buf, step = resp.content, 24
            for i in range(0, len(buf), step):
                yield ("text", buf[i:i + step])
        if resp.tool_calls:
            yield ("tool_calls", resp.tool_calls)

    def health_check(self) -> bool:
        try:
            resp = self.generate(
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=10,
            )
            return bool(resp.content)
        except Exception:
            return False

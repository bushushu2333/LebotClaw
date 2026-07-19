"""通道抽象：中性数据模型 + ChatChannel 接口 + ChannelRegistry。

设计参考 inno-agent 的 channels/channel.ts：
- 所有入站消息归一到 IncomingMessage；
- 所有出站动作为 reply（回消息）/ push（主动推）/ send_file；
- ChannelRegistry 记录每个渠道的默认推送目标，供 cron 主动推送回落。
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class IncomingMessage:
    """所有渠道入站消息的中性表示。"""
    channel: str               # "feishu" | "wechat" | "web" | ...
    chat_id: str = ""
    user_id: str = ""
    text: str = ""
    message_id: str = ""
    raw: dict = field(default_factory=dict)


@dataclass
class PushTarget:
    """出站推送目标。"""
    channel: str
    chat_id: str


@dataclass
class StreamEvent:
    """流式事件（增强项占位：真流式需 Agent.chat_stream_with_tools）。"""
    type: str = "text_delta"   # text_delta | done | error
    delta: str = ""
    full: str = ""


class FileSendNotSupportedError(Exception):
    pass


class ChatChannel(ABC):
    """通道接口。各渠道实现 reply/push；run/stop 可选（长连接类需要）。"""
    name: str = "base"

    @abstractmethod
    def reply(self, target: PushTarget, text: str) -> None:
        """回复一条入站消息。"""

    @abstractmethod
    def push(self, target: PushTarget, text: str) -> None:
        """主动推送（cron 触发）。默认实现 = reply。"""

    def send_file(self, target: PushTarget, path: str) -> None:
        raise FileSendNotSupportedError(f"{self.name} does not support sending files")

    def run(self) -> None:
        """长连接/轮询入口（在后台 daemon 线程中调用）。默认无。"""

    def stop(self) -> None:
        """停止长连接。"""


class ChannelRegistry:
    def __init__(self):
        self._channels: dict[str, ChatChannel] = {}
        self._default_targets: dict[str, PushTarget] = {}

    def register(self, channel: ChatChannel) -> None:
        self._channels[channel.name] = channel

    def get(self, name: str) -> Optional[ChatChannel]:
        return self._channels.get(name)

    def has(self, name: str) -> bool:
        return name in self._channels

    def set_default_target(self, name: str, target: PushTarget) -> None:
        self._default_targets[name] = target

    def default_target(self, name: str = None) -> Optional[PushTarget]:
        """取某渠道的默认推送目标；未指定 name 时返回任一可用目标。"""
        if name:
            return self._default_targets.get(name)
        for t in self._default_targets.values():
            return t
        return None

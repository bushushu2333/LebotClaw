"""统一通道层（参考 inno-agent）：所有渠道归一到 reply/push/send_file。

Web / 飞书 / 微信(占位) / cron 共用同一套抽象，差异吸收在各自适配器里。
"""
from lebotclaw.channels.base import (
    ChatChannel,
    ChannelRegistry,
    IncomingMessage,
    PushTarget,
    StreamEvent,
    FileSendNotSupportedError,
)

__all__ = [
    "ChatChannel",
    "ChannelRegistry",
    "IncomingMessage",
    "PushTarget",
    "StreamEvent",
    "FileSendNotSupportedError",
]

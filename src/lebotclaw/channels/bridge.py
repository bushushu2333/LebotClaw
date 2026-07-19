"""微信 bridge 通道占位。

本期不接 sidecar（规避非官方个人号协议的封号风险），仅定义契约供未来对接：

入站（sidecar → LebotClaw）：
    POST {web.bridge_url 或 /api/bridge/messages}
    body: {"chat_id": "...", "text": "...", "message_id": "..."}
    → 由 routes_api 接收并交给 Dispatcher

出站（LebotClaw → sidecar）：
    POST {sidecar_url}/push   body: {"chat_id": "...", "text": "..."}
    Header: Authorization: Bearer {bridge_token}

要启用时：实现 reply/push 对接 sidecar（wechaty / 企业微信），并配置
config.channels.wechat.enabled=true 与 sidecar 地址。
"""
from lebotclaw.channels.base import ChatChannel, PushTarget


class WechatBridgeChannel(ChatChannel):
    name = "wechat"

    def __init__(self, cfg: dict):
        self.cfg = cfg or {}
        self.sidecar_url = self.cfg.get("bridge_url", "")
        self.bridge_token = self.cfg.get("bridge_token", "")

    def reply(self, target: PushTarget, text: str) -> None:
        self._not_implemented()

    def push(self, target: PushTarget, text: str) -> None:
        self._not_implemented()

    def run(self) -> None:
        # 未接 sidecar：不启动
        pass

    def _not_implemented(self):
        raise NotImplementedError(
            "微信 bridge 通道本期未接 sidecar（规避非官方协议封号风险）。"
            "如需启用，对接 wechaty / 企业微信官方 API 并实现 reply/push。"
        )
